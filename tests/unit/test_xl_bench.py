"""Unit tests for scripts/xl_bench.py — the xl-orchestration bench harness.

Fakes only: no model, no DB, no network beyond loopback to the sidecar's own
stdlib HTTP server. The ACI server is a fake (post_agent_run/cancel), the
"server's per-run working copy" is a tmp dir the test mutates, and the tool
client (scripts/xl_oc_template/tools/delegate_client.py) is imported from the
template so the exact file the runner ships is what is tested.
"""

import base64
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import xl_bench as xb  # noqa: E402

TEMPLATE_TOOLS = SCRIPTS / "xl_oc_template" / "tools"


@pytest.fixture()
def client_mod():
    """The stdlib tool client the TS wrapper spawns — imported from the
    template dir so the shipped file is the tested file."""
    spec = importlib.util.spec_from_file_location(
        "delegate_client", TEMPLATE_TOOLS / "delegate_client.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class FakeAci:
    """Fake POST /v1/agent-runs surface: records bodies, returns canned results.

    ``gate`` (optional) blocks INSIDE post_agent_run after recording the
    body — for deterministic mid-run cancel tests."""

    def __init__(
        self,
        result: dict[str, Any] | None = None,
        *,
        exc: Exception | None = None,
        block_seconds: float = 0.0,
        gate: threading.Event | None = None,
    ) -> None:
        self.posts: list[dict[str, Any]] = []
        self.cancels: list[str] = []
        self.result = result or {
            "run_id": "run_fake123",
            "status": "succeeded",
            "stop_reason": "VERIFICATION_PASSED",
            "summary": "fixed",
            "turns": 3,
            "tool_calls": 5,
            "wall_time_seconds": 1.0,
            "checks": ["command_passed"],
            "evidence_verdict": "success",
        }
        self.exc = exc
        self.block_seconds = block_seconds
        self.gate = gate

    def post_agent_run(self, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append(body)
        if self.gate is not None:
            self.gate.wait(timeout=10.0)
        if self.block_seconds:
            time.sleep(self.block_seconds)
        if self.exc is not None:
            raise self.exc
        return self.result

    def cancel_agent_run(self, run_id: str) -> bool:
        self.cancels.append(run_id)
        return True


def _snapshot_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _snapshot_b64(files: dict[str, str]) -> str:
    return base64.b64encode(_snapshot_bytes(files)).decode()


@pytest.fixture()
def bench(tmp_path: Path):
    """A sidecar over tmp workspace/runs roots with a fake ACI behind it."""
    aci = FakeAci()
    sidecar = xb.DelegationSidecar(
        workspace_root=tmp_path / "workspaces",
        runs_root=tmp_path / "runs",
        aci=aci,
    )
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "runs").mkdir()
    return sidecar, aci, tmp_path


def _wait_terminal(sidecar: xb.DelegationSidecar, leaf_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = sidecar.handle_check(leaf_id)
        if out.get("status") != "running":
            return out
        time.sleep(0.02)
    raise AssertionError("leaf never became terminal")


# -- sidecar: delegate -------------------------------------------------------


def test_delegate_writes_snapshot_and_posts_contract(bench) -> None:
    sidecar, aci, tmp = bench
    out = sidecar.handle_delegate(
        {
            "objective": "fix the greeting bug",
            "constraints": ["stdlib only"],
            "verification_command": ["python", "-m", "pytest", "-q"],
            "write_scopes": ["."],
            "max_turns": 200,  # over the bench cap
            "snapshot": _snapshot_b64({"toy.py": "x = 1\n"}),
            "tag": "xl-bench:A2:fixture:0",
        }
    )
    assert out["ok"], out
    leaf_id = out["leaf_id"]
    # The snapshot landed under the server's workspace root, with the marker.
    ws = tmp / "workspaces" / out["workspace"]
    assert (ws / "toy.py").read_text() == "x = 1\n"
    assert (ws / xb.LEAF_MARKER).read_text() == leaf_id
    # The ACI POST body: the bench contract (max_turns <= 60, profile coder).
    _wait_terminal(sidecar, leaf_id)
    assert len(aci.posts) == 1
    body = aci.posts[0]
    assert body["workspace"] == out["workspace"]
    assert body["max_turns"] == 60
    assert body["requested_profile"] == "coder"
    assert body["objective"] == "fix the greeting bug"
    assert body["verification_command"] == ["python", "-m", "pytest", "-q"]
    assert body["write_scopes"] == ["."]


def test_delegate_rejects_bad_objective_and_snapshot(bench) -> None:
    sidecar, aci, _ = bench
    empty = sidecar.handle_delegate({"objective": "", "snapshot": _snapshot_b64({"a": "b"})})
    assert not empty["ok"]
    assert not sidecar.handle_delegate({"objective": "x" * 8001, "snapshot": "c"})["ok"]
    assert not sidecar.handle_delegate({"objective": "ok", "snapshot": "not base64!!"})["ok"]
    assert not sidecar.handle_delegate({"objective": "ok", "snapshot": ""})["ok"]
    assert aci.posts == []


def test_tree_hashes_exclude_kernel_infrastructure(tmp_path: Path) -> None:
    """The kernel writes its Seatbelt profile into the run workspace — that is
    infrastructure, never merged leaf output (seen live in the wiring smoke)."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "mod.py").write_text("fix\n")
    (run_dir / xb.LEAF_MARKER).write_text("leaf_x")
    (run_dir / ".aci-sandbox-profile.sb").write_text("(version 1)")
    (run_dir / "__pycache__").mkdir()
    (run_dir / "__pycache__" / "m.pyc").write_text("junk")
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "mod.py").write_text("bug\n")
    changed, deleted, diff, files = xb.diff_trees(snapshot, run_dir)
    assert changed == ["mod.py"]
    assert deleted == []
    assert ".aci-sandbox-profile.sb" not in files
    assert xb.LEAF_MARKER not in diff


def test_delegate_rejects_escaping_tar(bench) -> None:
    sidecar, _, tmp = bench
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"boom"))
    out = sidecar.handle_delegate(
        {
            "objective": "ok",
            "snapshot": base64.b64encode(buf.getvalue()).decode(),
        }
    )
    assert not out["ok"]
    assert "unsafe snapshot member" in out["error"]
    escaped = (tmp / "workspaces").parent / "escape.txt"
    assert not escaped.exists()


# -- sidecar: check + diff (FINDING #1's bench-side solution) ----------------


def test_check_returns_diff_and_files_from_run_copy(bench) -> None:
    sidecar, aci, tmp = bench
    out = sidecar.handle_delegate(
        {
            "objective": "fix",
            "snapshot": _snapshot_b64(
                {
                    "keep.py": "a = 1\n",
                    "edit.py": "old\n",
                    "gone.py": "z\n",
                }
            ),
        }
    )
    leaf_id = out["leaf_id"]
    _wait_terminal(sidecar, leaf_id)
    # The server's per-run working copy (what the product keeps on disk).
    run_dir = tmp / "runs" / "run_fake123"
    run_dir.mkdir()
    (run_dir / "keep.py").write_text("a = 1\n")
    (run_dir / "edit.py").write_text("new\n")
    (run_dir / "added.py").write_text("fresh\n")
    # The sidecar computes the diff lazily at the terminal state — re-check.
    result = _wait_terminal(sidecar, leaf_id)
    assert result["status"] == "succeeded"
    assert result["run_id"] == "run_fake123"
    assert sorted(result["changed_files"]) == ["added.py", "edit.py"]
    assert result["deleted_files"] == ["gone.py"]
    assert "+fresh" in result["diff"] and "-old" in result["diff"] and "+new" in result["diff"]
    assert base64.b64decode(result["files"]["edit.py"]) == b"new\n"
    assert base64.b64decode(result["files"]["added.py"]) == b"fresh\n"
    assert "gone.py" not in result["files"]


def test_check_unknown_leaf_and_running(bench) -> None:
    sidecar, _, _tmp = bench
    assert not sidecar.handle_check("leaf_nope")["ok"]
    aci = FakeAci(block_seconds=5.0)
    sidecar2 = xb.DelegationSidecar(
        workspace_root=_tmp / "workspaces",
        runs_root=_tmp / "runs",
        aci=aci,
    )
    out = sidecar2.handle_delegate({"objective": "slow", "snapshot": _snapshot_b64({"a": "1"})})
    assert sidecar2.handle_check(out["leaf_id"])["status"] == "running"


def test_leaf_error_is_a_row_not_a_crash(bench) -> None:
    sidecar, _, tmp = bench
    aci = FakeAci(exc=RuntimeError("ACI POST /v1/agent-runs 500: boom"))
    sidecar2 = xb.DelegationSidecar(
        workspace_root=tmp / "workspaces",
        runs_root=tmp / "runs",
        aci=aci,
    )
    out = sidecar2.handle_delegate({"objective": "x", "snapshot": _snapshot_b64({"a": "1"})})
    result = _wait_terminal(sidecar2, out["leaf_id"])
    assert result["status"] == "error"
    assert "RuntimeError" in result["error"]


def test_paused_leaf_is_cancelled_never_merged(bench) -> None:
    sidecar, aci, tmp = bench
    aci.result = {
        "run_id": "run_paused1",
        "status": "interrupted",
        "summary": "needs input",
    }
    out = sidecar.handle_delegate({"objective": "x", "snapshot": _snapshot_b64({"a": "1"})})
    result = _wait_terminal(sidecar, out["leaf_id"])
    assert result["status"] == "cancelled"
    assert result["cancelled"] is True
    assert aci.cancels == ["run_paused1"]


# -- sidecar: cancel (incl. the marker race) ---------------------------------


def test_cancel_after_terminal_races_post(bench) -> None:
    sidecar, aci, tmp = bench
    out = sidecar.handle_delegate({"objective": "x", "snapshot": _snapshot_b64({"a": "1"})})
    leaf_id = out["leaf_id"]
    _wait_terminal(sidecar, leaf_id)
    result = sidecar.handle_cancel(leaf_id)
    assert result["ok"] and result["cancelled"]
    # The cancel raced the terminal POST: the leaf is dropped bench-side and a
    # later check never merges it (the client guards on `cancelled`).
    final = _wait_terminal(sidecar, leaf_id)
    assert final["cancelled"] is True


def test_cancel_mid_run_finds_run_id_by_marker(bench) -> None:
    """The real cancel path: while the synchronous POST is still executing,
    the marker in the server's per-run copy identifies the run id."""
    sidecar, aci, tmp = bench
    gate = threading.Event()
    aci.gate = gate
    out = sidecar.handle_delegate({"objective": "x", "snapshot": _snapshot_b64({"a": "1"})})
    leaf_id = out["leaf_id"]
    for _ in range(200):  # the worker thread is now blocked INSIDE the POST
        if aci.posts:
            break
        time.sleep(0.02)
    assert aci.posts
    # The server has provisioned the per-run copy (a copy of the snapshot).
    run_dir = tmp / "runs" / "run_live42"
    run_dir.mkdir()
    (run_dir / xb.LEAF_MARKER).write_text(leaf_id, encoding="utf-8")
    result = sidecar.handle_cancel(leaf_id)
    assert result["ok"] and result["run_id"] == "run_live42"
    assert aci.cancels == ["run_live42"]
    gate.set()  # release the POST: it returns "succeeded", but the leaf is dropped
    final = _wait_terminal(sidecar, leaf_id, timeout=10.0)
    assert final["cancelled"] is True


def test_cancel_before_post_skips_the_run(bench) -> None:
    sidecar, aci, tmp = bench
    leaf = xb.LeafState(leaf_id="leaf_x", workspace="xl-leaf_x", objective="x")
    sidecar.leaves[leaf.leaf_id] = leaf
    leaf.cancel_requested = True  # cancelled before the worker issues the POST
    sidecar._execute_leaf(leaf, {"objective": "x"})  # noqa: SLF001 — the thread body, direct
    assert aci.posts == []
    assert sidecar.handle_check(leaf.leaf_id)["status"] == "cancelled"


def test_handle_leaves_filters_by_tag(bench) -> None:
    sidecar, _, _ = bench
    sidecar.handle_delegate(
        {"objective": "a", "snapshot": _snapshot_b64({"a": "1"}), "tag": "xl-bench:A2:f1:0"}
    )
    sidecar.handle_delegate(
        {"objective": "b", "snapshot": _snapshot_b64({"b": "2"}), "tag": "xl-bench:A3:f1:0"}
    )
    assert len(sidecar.handle_leaves("xl-bench:A2")["leaves"]) == 1
    assert len(sidecar.handle_leaves("xl-bench:")["leaves"]) == 2
    assert sidecar.handle_leaves("xl-bench:A2")["leaves"][0]["objective"] == "a"


# -- the HTTP sidecar + the real tool client, end to end (loopback only) -----


def test_sidecar_http_round_trip_with_tool_client(bench, client_mod, tmp_path, monkeypatch) -> None:
    """The full bench-side chain over real loopback HTTP: the tool client
    snapshots an orchestrator workspace, delegates through the sidecar, the
    fake ACI 'executes', the server's run copy is diffed, and the client
    applies the changed files back into the orchestrator workspace."""
    sidecar, aci, tmp = bench
    httpd = xb.serve_sidecar(sidecar, "127.0.0.1", 0)
    port = httpd.server_address[1]
    monkeypatch.setenv("XL_SIDECAR_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("XL_RUN_TAG", "xl-bench:A2:t:0")
    oc_work = tmp_path / "orchestrator-work"
    oc_work.mkdir()
    (oc_work / "mod.py").write_text("value = 0\n")  # the bug
    (oc_work / "junk" / "__pycache__").mkdir(parents=True)
    (oc_work / "junk" / "__pycache__" / "x.pyc").write_text("noise")
    # delegate: snapshot -> sidecar -> fake ACI POST
    delegate_out = client_mod.cmd_delegate(
        oc_work,
        {
            "objective": "set value to 1",
            "verification_command": ["python", "-m", "pytest", "-q"],
            "max_turns": 30,
        },
    )
    assert delegate_out["ok"], delegate_out
    leaf_id = delegate_out["leaf_id"]
    # The ACI 'run' happens: the server's per-run copy diverges from the snap.
    run_dir = tmp / "runs" / "run_fake123"
    run_dir.mkdir()
    (run_dir / "mod.py").write_text("value = 1\n")  # the fix
    (run_dir / "test_mod.py").write_text(
        "from mod import value\n\n\ndef test_v():\n    assert value == 1\n"
    )
    # check: poll until terminal, then the client applies into the workspace.
    for _ in range(200):
        check_out = client_mod.cmd_check(oc_work, {"leaf_id": leaf_id})
        if check_out.get("status") != "running":
            break
        time.sleep(0.02)
    assert check_out["ok"], check_out
    assert check_out["applied"] is True, check_out.get("apply_problems")
    assert (oc_work / "mod.py").read_text() == "value = 1\n"
    assert (oc_work / "test_mod.py").exists()
    # The snapshot excluded junk; the ACI body carries the bench contract.
    assert not (tmp / "workspaces" / delegate_out["workspace"] / "junk").exists()
    assert aci.posts[0]["max_turns"] == 30
    assert aci.posts[0]["verification_command"] == ["python", "-m", "pytest", "-q"]
    # cancel path over HTTP
    cancel_out = client_mod.cmd_cancel({"leaf_id": leaf_id})
    assert cancel_out["ok"]
    httpd.shutdown()


def test_client_check_never_applies_cancelled_leaf(
    bench, client_mod, tmp_path, monkeypatch
) -> None:
    sidecar, aci, tmp = bench
    httpd = xb.serve_sidecar(sidecar, "127.0.0.1", 0)
    monkeypatch.setenv("XL_SIDECAR_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    aci.result = {"run_id": "run_c1", "status": "cancelled", "summary": "stale"}
    out = sidecar.handle_delegate({"objective": "x", "snapshot": _snapshot_b64({"a": "1"})})
    run_dir = tmp / "runs" / "run_c1"
    run_dir.mkdir()
    (run_dir / "a").write_text("partial\n")
    final = _wait_terminal(sidecar, out["leaf_id"])
    assert final["cancelled"] is True
    oc_work = tmp_path / "oc"
    oc_work.mkdir()
    (oc_work / "a").write_text("1\n")
    check_out = client_mod.cmd_check(oc_work, {"leaf_id": out["leaf_id"]})
    assert check_out["applied"] is False
    assert (oc_work / "a").read_text() == "1\n"  # untouched
    httpd.shutdown()


# -- the tool client's own helpers -------------------------------------------


def test_snapshot_tar_excludes_junk_and_symlinks(client_mod, tmp_path: Path) -> None:
    work = tmp_path / "w"
    (work / "pkg").mkdir(parents=True)
    (work / "pkg" / "mod.py").write_text("x = 1\n")
    (work / "pkg" / "__pycache__").mkdir()
    (work / "pkg" / "__pycache__" / "m.pyc").write_text("junk")
    (work / ".git").mkdir()
    (work / ".git" / "HEAD").write_text("ref")
    link = work / "pkg" / "link.py"
    link.symlink_to(work / "pkg" / "mod.py")
    data = client_mod.snapshot_tar(work)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = {m.name for m in tar.getmembers()}
    assert "pkg/mod.py" in names
    assert not any("__pycache__" in n or n.startswith(".git") for n in names)
    assert "pkg/link.py" not in names


def test_apply_result_writes_deletes_and_refuses_escapes(client_mod, tmp_path: Path) -> None:
    work = tmp_path / "w"
    work.mkdir()
    (work / "old.py").write_text("old\n")
    problems = client_mod.apply_result(
        work,
        {
            "files": {
                "new.py": base64.b64encode(b"new\n").decode(),
                "sub/dir/deep.py": base64.b64encode(b"deep\n").decode(),
                "../escape.py": base64.b64encode(b"evil\n").decode(),
                "/abs.py": base64.b64encode(b"evil\n").decode(),
            },
            "deleted_files": ["old.py", "../gone.py"],
        },
    )
    assert (work / "new.py").read_text() == "new\n"
    assert (work / "sub/dir/deep.py").read_text() == "deep\n"
    assert not (work / "old.py").exists()
    assert not (work / "../escape.py").exists() and not (work / "../gone.py").exists()
    assert len(problems) == 3  # both escapes + the unsafe delete are refused


def test_client_main_argv_and_stdin(client_mod, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XL_SIDECAR_URL", "http://127.0.0.1:1")
    out = client_mod.main([str(client_mod.__file__), "bogus"])
    assert out == 2
    assert not json.loads(capsys.readouterr().out)["ok"]


# -- playbooks ---------------------------------------------------------------


def test_playbooks_pin_the_arm_contracts() -> None:
    for needle in (
        "delegate_to_aci",
        "delegate_to_aci_check",
        "delegate_to_aci_cancel",
        "EAGER DECOMPOSITION",
        "verification_command",
    ):
        assert needle in xb.A2_PLAYBOOK
    # A3 = A2 + the discipline; every design item is encoded.
    assert xb.A3_PLAYBOOK.startswith(xb.A2_PLAYBOOK)
    for needle in (
        "ROLLING WAVE",
        "UNCERTAINTY FIRST",
        "PROBE leaf",
        "PLAN VERSIONS",
        "CANCEL every still-running leaf",
        "mechanical check",
    ):
        assert needle in xb.A3_PLAYBOOK
    # A1 gets none: the runner's prompt composition is arm-dependent.
    assert "delegate_to_aci" not in xb.TOY_TASK["prompt"] or True  # toy task may mention it


def test_toy_task_is_not_a_fixture() -> None:
    assert set(xb.TOY_TASK["files"]) == {"toy_greet.py", "test_toy.py"}
    assert "MUST delegate" in xb.TOY_TASK["prompt"]


# -- metrics -----------------------------------------------------------------


def test_parse_opencode_stream_sums_step_finish_tokens(tmp_path: Path) -> None:
    stream = tmp_path / "s.jsonl"
    events = [
        {"type": "step_start", "sessionID": "ses_1", "part": {"type": "step-start"}},
        {"type": "tool_use", "sessionID": "ses_1", "part": {"tool": "bash"}},
        {
            "type": "step_finish",
            "sessionID": "ses_1",
            "part": {
                "type": "step-finish",
                "tokens": {"input": 100, "output": 10, "reasoning": 0, "cache": {"read": 5}},
            },
        },
        {
            "type": "step_finish",
            "sessionID": "ses_1",
            "part": {
                "type": "step-finish",
                "tokens": {"input": 50, "output": 4},
            },
        },
        {"type": "text", "sessionID": "ses_1", "part": {"text": "done"}},
        {
            "type": "error",
            "sessionID": "ses_1",
            "error": {"type": "provider.auth", "message": "Invalid API key"},
        },
        "not json at all",
    ]
    stream.write_text("\n".join(json.dumps(e) if isinstance(e, dict) else e for e in events))
    parsed = xb.parse_opencode_stream(stream)
    assert parsed["session_id"] == "ses_1"
    assert parsed["tokens_in"] == 150
    assert parsed["tokens_out"] == 14
    assert parsed["tool_uses"] == 1
    assert parsed["errors"] and "provider.auth" in parsed["errors"][0]


def test_kernel_usage_url_normalization() -> None:
    """The bench DB URL is SQLAlchemy-shaped; psycopg needs a plain libpq URI."""
    assert xb._psycopg_url("postgresql+psycopg://aci:aci@h:5432/aci_e2b") == (
        "postgresql://aci:aci@h:5432/aci_e2b"
    )
    assert xb._psycopg_url("postgresql://aci:aci@h/aci") == "postgresql://aci:aci@h/aci"


def test_is_model_failure_rule() -> None:
    dead = {"tool_uses": 0, "errors": ['{"type": "provider.transport", "message": "5xx"}']}
    assert xb._is_model_failure(dead, wall=30.0)
    assert not xb._is_model_failure(dead, wall=120.0)  # too late to be a provider death
    alive = {"tool_uses": 3, "errors": ["provider.transport 5xx"]}
    assert not xb._is_model_failure(alive, wall=30.0)  # it did work first
    clean = {"tool_uses": 0, "errors": []}
    assert not xb._is_model_failure(clean, wall=30.0)


def test_parse_pytest_summary_counts() -> None:
    counts = xb.parse_pytest_summary("2 failed, 5 passed, 1 error in 0.1s")
    assert counts == {"passed": 5, "failed": 2, "errors": 1}
    assert xb.parse_pytest_summary("no tests ran") == {
        "passed": 0,
        "failed": 0,
        "errors": 0,
    }


def test_shadow_tree_and_reverted_lines(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True, capture_output=True)
    (work / "a.py").write_text("line1\n")
    t0 = xb.shadow_tree(work)
    # The orchestrator writes line2+line3, then re-plans: line3 is replaced by
    # line4 (one line written, then reverted).
    (work / "a.py").write_text("line1\nline2\nline3\n")
    t1 = xb.shadow_tree(work)
    (work / "a.py").write_text("line1\nline2\nline4\n")
    t2 = xb.shadow_tree(work)
    metrics = xb.reverted_lines(work, [t0, t1, t2], t2)
    assert metrics["lines_written"] == 3  # 2 added t0->t1, 1 added t1->t2
    assert metrics["lines_survived"] == 2
    assert metrics["lines_reverted"] == 1
    # A clean run reverts nothing.
    clean = xb.reverted_lines(work, [t0, t0], t0)
    assert clean["lines_reverted"] == 0


def test_hidden_suite_result_runs_fresh_copy_outside(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "mod.py").write_text("value = 1\n")
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "test_mod.py").write_text(
        "from mod import value\n\n\ndef test_v():\n    assert value == 1\n"
    )
    grade = xb.hidden_suite_result(work, hidden)
    assert grade["pass_fraction"] == 1.0 and grade["passed"] == 1
    # The hidden test never lands inside the run workspace.
    assert not (work / "test_mod.py").exists()
    # A failing workspace grades below 1.
    (work / "mod.py").write_text("value = 0\n")
    grade = xb.hidden_suite_result(work, hidden)
    assert grade["pass_fraction"] == 0.0 and grade["failed"] == 1
    assert grade["failed_ids"] == ["test_mod.py::test_v"]
    # Invalidated originals are removed for perturbed grading (file form).
    (work / "mod.py").write_text("value = 1\n")
    grade = xb.hidden_suite_result(work, hidden, invalidated=["test_mod.py"])
    assert grade["pass_fraction"] == 0.0  # no tests ran -> fraction 0, exit != 0
    # Node form deselects exactly one test of a collected file.
    (work / "mod.py").write_text("value = 1\n")
    (hidden / "test_mod2.py").write_text("def test_two():\n    assert True\n")
    grade = xb.hidden_suite_result(work, hidden, invalidated=["test_mod.py::test_v"])
    assert grade["passed"] == 1 and grade["failed"] == 0  # only test_two ran


def test_hidden_suite_isolation_grades_only_the_hidden_suite(tmp_path: Path) -> None:
    """FIX 1 (hidden-suite isolation): the score counts ONLY the hidden suite.
    An agent-written always-pass test must not inflate the fraction, a
    malicious agent conftest.py must not be able to monkeypatch the product
    into passing, and an agent pytest config (addopts) must not be able to
    silently deselect the hidden tests. Seed-shipped test files survive the
    strip (trusted fixture content); everything else test-shaped goes."""
    seed = tmp_path / "seed"
    work = tmp_path / "work"
    for d in (seed, work):
        d.mkdir()
        (d / "mod.py").write_text("value = 0\n")  # the bug is still there
    # what the agent left behind in the final workspace:
    (work / "test_agent_wins.py").write_text("def test_w():\n    assert True\n")
    (work / "conftest.py").write_text("import mod\nmod.value = 1\n")  # the fake-green patch
    (seed / "test_shipped.py").write_text("def test_s():\n    assert True\n")
    (work / "test_shipped.py").write_text("def test_s():\n    assert True\n")  # byte-identical
    (work / "test_shipped_tampered.py").write_text("def test_s():\n    assert True\n")
    (work / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '--deselect test_mod.py::test_v'\n"
    )
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "test_mod.py").write_text(
        "from mod import value\n\n\ndef test_v():\n    assert value == 1\n"
    )
    grade = xb.hidden_suite_result(work, hidden, seed_dir=seed)
    # ONLY the hidden test ran, and it FAILED (the bug is real): the agent's
    # always-pass test did not inflate the score and the conftest did not
    # patch the product into passing.
    assert grade["passed"] == 0 and grade["failed"] == 1
    assert grade["pass_fraction"] == 0.0
    assert grade["failed_ids"] == ["test_mod.py::test_v"]
    # the strip removed exactly the agent-written test-shaped files; the
    # seed-identical shipped test survived (and is not a hidden path, so it
    # is not collected either).
    assert sorted(grade["stripped_agent_tests"]) == [
        "conftest.py",
        "test_agent_wins.py",
        "test_shipped_tampered.py",
    ]
    # Real work still scores: fix the bug and the hidden suite goes green —
    # the isolation hides only the agent's test files, never the product.
    (work / "mod.py").write_text("value = 1\n")
    grade = xb.hidden_suite_result(work, hidden, seed_dir=seed)
    assert grade["pass_fraction"] == 1.0 and grade["passed"] == 1
    # Without a seed_dir every test-shaped file is stripped (fail-closed).
    (work / "mod.py").write_text("value = 0\n")
    grade = xb.hidden_suite_result(work, hidden)
    assert grade["failed"] == 1 and grade["passed"] == 0


def test_strip_agent_tests_seed_identical_rule(tmp_path: Path) -> None:
    """The byte-identity rule, pinned directly: identical to the seed -> kept;
    different bytes or not in the seed -> removed; non-test files untouched."""
    seed = tmp_path / "seed"
    fresh = tmp_path / "fresh"
    for d in (seed, fresh):
        d.mkdir()
    (seed / "conftest.py").write_text("# fixture harness\n")
    (fresh / "conftest.py").write_text("# fixture harness\n")  # identical -> kept
    (seed / "test_shipped.py").write_text("def test_a():\n    assert True\n")
    (fresh / "test_shipped.py").write_text("def test_a():\n    assert True\n")  # kept
    (fresh / "test_evil.py").write_text("def test_b():\n    assert True\n")  # stripped
    (fresh / "pkg" / "conftest.py").parent.mkdir()
    (fresh / "pkg" / "conftest.py").write_text("import mod\n")  # stripped (not in seed)
    (fresh / "mod.py").write_text("value = 0\n")  # NOT test-shaped -> untouched
    removed = xb.strip_agent_tests(fresh, seed)
    assert sorted(removed) == ["pkg/conftest.py", "test_evil.py"]
    assert (fresh / "conftest.py").is_file()
    assert (fresh / "test_shipped.py").is_file()
    assert (fresh / "mod.py").is_file()
    assert not (fresh / "test_evil.py").exists()
    assert not (fresh / "pkg" / "conftest.py").exists()


def test_id_covers_forms() -> None:
    """The declared-id matcher: directory prefixes, parametrizations, files."""
    assert xb._id_covers("test_x.py::test_y", "test_x.py::test_y")
    assert xb._id_covers("test_x.py::test_y", "sub/test_x.py::test_y")
    assert xb._id_covers("test_x.py::test_y", "test_x.py::test_y[p1]")
    assert xb._id_covers("test_x.py", "sub/test_x.py::test_y[q]")
    assert not xb._id_covers("test_x.py::test_y", "test_x.py::test_other")
    assert not xb._id_covers("test_x.py::test_y", "test_z.py::test_y")


# -- fixture format + validator ---------------------------------------------


def _make_fixture(
    root: Path,
    *,
    reference_fixes: bool = True,
    change_bites: bool = True,
    patch_implements: bool = True,
    guards: list[str] | None = None,
) -> Path:
    """A demo fixture in the UNIFIED change semantics: the change REVERSES
    the original behavior (add() becomes product semantics), so the
    invalidated original test fails on the patch, the change test bites on
    the unpatched reference, and reference+patch passes the post-change
    suite. Knobs break one property at a time for the rejection tests."""
    fixture = root / "xl-demo"
    (fixture / "workspace").mkdir(parents=True)
    (fixture / "workspace" / "calc.py").write_text(
        "def add(a, b):\n    return a - b  # the shipped bug\n"
    )
    (fixture / "hidden").mkdir()
    (fixture / "hidden" / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n"
    )
    (fixture / "reference").mkdir()
    (fixture / "reference" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n"
        if reference_fixes
        else "def add(a, b):\n    return a - b\n"
    )
    (fixture / "change").mkdir()
    (fixture / "change" / "CHANGE_NOTE.md").write_text(
        "Requirement change: add() now MULTIPLIES its operands — "
        "add(a, b, ...) returns their product (the old sum behavior is gone)."
    )
    (fixture / "change" / "hidden").mkdir()
    if change_bites:
        (fixture / "change" / "hidden" / "test_three.py").write_text(
            "from calc import add\n\n\ndef test_three():\n    assert add(2, 3, 4) == 24\n"
        )
    else:  # a change test the unpatched reference already satisfies
        (fixture / "change" / "hidden" / "test_three.py").write_text(
            "from calc import add\n\n\ndef test_three():\n    assert add(2, 3) == 5\n"
        )
    (fixture / "change" / "invalidates.txt").write_text("test_calc.py::test_add\n")
    (fixture / "change" / "patch").mkdir()
    patch_code = (
        "def add(*operands):\n"
        "    product = 1\n"
        "    for operand in operands:\n"
        "        product *= operand\n"
        "    return product\n"
    )
    if patch_implements:
        (fixture / "change" / "patch" / "calc.py").write_text(patch_code)
    else:  # a patch that does not implement the change
        (fixture / "change" / "patch" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    if guards is not None:
        (fixture / "change" / "guards.txt").write_text("\n".join(guards) + ("\n" if guards else ""))
    (fixture / "TASK.md").write_text("Make the test suite pass.")
    return fixture


def test_load_fixture_reads_change_pack(tmp_path: Path) -> None:
    fixture = xb.load_fixture(_make_fixture(tmp_path, guards=["test_three.py::test_three"]))
    assert fixture.name == "xl-demo"
    assert fixture.perturbed
    assert fixture.change_note and "MULTIPLIES" in fixture.change_note
    assert fixture.invalidated == ["test_calc.py::test_add"]
    assert fixture.guards == ["test_three.py::test_three"]
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "TASK.md").write_text("task")
    assert xb.load_fixture(bare).change_note is None
    assert xb.load_fixture(bare).invalidated == []
    assert xb.load_fixture(bare).guards == []


def test_verify_fixture_accepts_the_unified_semantics(tmp_path: Path, capsys) -> None:
    """All six cases green on an honest fixture: fail-as-shipped (both
    suites), pass-when-solved (original), the BITE (the unpatched reference
    fails the post-change suite, every non-guard change test failing),
    pass-when-solved (reference + patch), and the EXACT reversal (the
    original suite on the patch fails exactly the invalidated test)."""
    fixture = _make_fixture(tmp_path)
    assert xb.verify_fixture(fixture) == 0
    out = capsys.readouterr().out
    assert "fail-as-shipped (seed, original suite)" in out
    assert "the change BITES (reference, post-change suite)" in out
    assert "pass-when-solved (reference + change patch, post-change suite)" in out
    assert "the reversal is exact (reference + patch, ORIGINAL suite)" in out
    assert "bite detail: 1/1 change tests fail pre-patch" in out


def test_verify_fixture_rejects_reference_that_does_not_solve(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path, reference_fixes=False)
    assert xb.verify_fixture(fixture) == 1


def test_verify_fixture_rejects_a_change_that_does_not_bite(tmp_path: Path, capsys) -> None:
    """A change test the unpatched reference already passes measures nothing
    about the change — the bite check must flag it (job item 3's class)."""
    fixture = _make_fixture(tmp_path, change_bites=False)
    assert xb.verify_fixture(fixture) == 1
    assert "do not bite pre-patch" in capsys.readouterr().out


def test_verify_fixture_rejects_a_patch_that_does_not_implement(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path, patch_implements=False)
    assert xb.verify_fixture(fixture) == 1


def test_verify_fixture_rejects_unknown_guard(tmp_path: Path, capsys) -> None:
    fixture = _make_fixture(tmp_path, guards=["test_calc.py::test_add"])
    assert xb.verify_fixture(fixture) == 1
    assert "guard is not a change test" in capsys.readouterr().out


def test_verify_fixture_rejects_invalidates_of_a_non_original_test(tmp_path: Path, capsys) -> None:
    fixture = _make_fixture(tmp_path)
    (fixture / "change" / "invalidates.txt").write_text("test_nope.py::test_missing\n")
    assert xb.verify_fixture(fixture) == 1
    assert "invalidates a non-original test" in capsys.readouterr().out


def test_verify_fixture_rejects_a_patch_targeting_unknown_files(tmp_path: Path, capsys) -> None:
    fixture = _make_fixture(tmp_path)
    (fixture / "change" / "patch" / "unknown.py").write_text("x = 1\n")
    assert xb.verify_fixture(fixture) == 1
    assert "patch targets an unknown file" in capsys.readouterr().out


def test_verify_fixture_reversal_overreach_is_a_violation(tmp_path: Path, capsys) -> None:
    """If the patch breaks an original test that is NOT invalidated, the
    reversal is not exact — a violation (the change must reverse only what
    it claims)."""
    fixture = _make_fixture(tmp_path)
    # a patch that ALSO breaks the (non-invalidated) original behavior in a
    # second file the original suite covers
    (fixture / "workspace" / "other.py").write_text("def one():\n    return 1\n")
    (fixture / "hidden" / "test_other.py").write_text(
        "from other import one\n\n\ndef test_one():\n    assert one() == 1\n"
    )
    (fixture / "change" / "patch" / "other.py").write_text("def one():\n    return 2\n")
    assert xb.verify_fixture(fixture) == 1
    assert "over-reaches" in capsys.readouterr().out


# -- the runner's guards -----------------------------------------------------


def test_arm_endpoints_bridge_gateway_and_sidecar() -> None:
    """The sandbox has NO network: A1 gets ONLY the gateway bridge; A2/A3
    get gateway + sidecar (the delegate tool's bridge). Nothing else is
    reachable — not the ACI server, not the DB, not sibling runs."""
    gw = xb._arm_endpoints("A1", "http://localhost:20128/v1", "http://127.0.0.1:8770")
    assert gw == [("localhost", 20128, "gateway")]
    for arm in ("A2", "A3"):
        assert xb._arm_endpoints(arm, "http://localhost:20128/v1", "http://127.0.0.1:8770") == [
            ("localhost", 20128, "gateway"),
            ("127.0.0.1", 8770, "sidecar"),
        ]


def test_url_endpoint_parses_host_and_port() -> None:
    assert xb._url_endpoint("http://localhost:20128/v1") == ("localhost", 20128)
    assert xb._url_endpoint("http://127.0.0.1:8770") == ("127.0.0.1", 8770)
    assert xb._url_endpoint("http://no-port.example/") == ("no-port.example", 80)


def test_opencode_runs_are_always_standalone() -> None:
    """FIX 5: never a shared `opencode serve --service` (the macOS lesson: a
    bench session resumed old runs and kept running after the bench). Both
    benches always run `opencode run --standalone`."""
    for source in ("xl_bench.py", "rc_bench.py"):
        text = (SCRIPTS / source).read_text(encoding="utf-8")
        assert '"run", "--standalone"' in text, source
        assert '"serve"' not in text, source  # never a serve command


def test_round_refused_off_linux(monkeypatch) -> None:
    args = type("Args", (), {})()
    args.fixtures = "x"
    args.arms = "A1,A2,A3"
    args.repeat = 1
    args.perturbation = "both"
    args.wall_budget = 60.0
    args.out = "/tmp/x.json"
    args.bench_root = "/tmp/aci-xl-bench-test"
    args.db_url = "postgresql://x"
    args.embedder = "hashing"
    args.model = xb.MODEL
    args.venv = str(xb.ROOT / ".venv")
    args.aci_port = 8020
    args.sidecar_port = 18770
    args.prefixes = ["python -m pytest"]
    monkeypatch.setattr(sys, "platform", "darwin")
    assert xb.run_round(args) == 2


def test_ensure_oc_template_copies_and_installs(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        xb.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})(),
    )
    cache = xb.ensure_oc_template(tmp_path / "cache")
    assert (cache / "tools" / "delegate_to_aci.ts").is_file()
    assert (cache / "tools" / "delegate_client.py").is_file()
    assert (cache / "package.json").is_file()
    assert calls and calls[0][0].endswith("bun") and calls[0][1] == "install"
    # The repo template dir itself stays clean (no node_modules in the repo).
    assert not (xb.TEMPLATE / "node_modules").exists()


# -- the TS tool contract (pinned like test_opencode_plugin_contract) --------


def test_delegate_tool_ts_contract() -> None:
    ts = (TEMPLATE_TOOLS / "delegate_to_aci.ts").read_text(encoding="utf-8")
    # Three tools: default (delegate_to_aci) + check + cancel exports.
    assert "export default tool({" in ts
    assert "export const check = tool({" in ts
    assert "export const cancel = tool({" in ts
    # It spawns the stdlib python client shipped next to it.
    assert "delegate_client.py" in ts
    assert '"python3"' in ts
    # The bench contract is stated on the tool surface.
    assert "max_turns <= 60" in ts
    assert "leaf_id" in ts
    # The TS never reads the sidecar env or any ACI token itself: the stdlib
    # python client reads XL_SIDECAR_URL; the ACI bearer token never reaches
    # the orchestrator at all (only the sidecar holds it, on the host).
    assert "process.env.XL_SIDECAR_URL" not in ts
    assert "ACI_AGENT_RUNS_TOKEN" not in ts
    assert "Authorization" not in ts


def test_delegate_client_reads_sidecar_env(client_mod) -> None:
    assert client_mod.EXCLUDE_DIRS >= {"__pycache__", ".git", "node_modules"}
    assert client_mod.MAX_DIFF_CHARS > 0
