"""§61 security cases for the agent-run CHANGE read model (the shared
platform-surface contract, 2026-10-03; xl-harness FINDINGS 1-2).

``GET /v1/agent-runs/{run_id}`` (and the POST response) now project the
run's file changes — a NEW wire surface that echoes workspace file CONTENT
as diff text. Load-bearing claims, enforced as tests:

- No server path ever reaches the wire: change paths are workspace-relative
  POSIX, never absolute, never ``..``; the manifest's copy-source path (an
  absolute server path) appears nowhere.
- The diff text is scrubbed with the run's own guard (``SecretLeakGuard``
  patterns, §14): a secret the run wrote into a changed file is REDACTED
  (the guard's exact replacement), a private key block (never redactable
  per the guard) withholds the whole hunk — the file stays listed.
- The start manifest lives OUTSIDE the working copy (the model holds write
  grants over the whole workspace — a manifest inside it could be rewritten
  to hide the run's own changes) and is unchanged by a run that wrote files.
- A tampered manifest ``source`` never turns the hash-verified start-bytes
  read into an arbitrary-file read: the path is re-validated against the
  server's workspace/runs roots at READ time.
- Kernel infrastructure (the Seatbelt profile the kernel writes into the
  workspace) never appears as a change.

Deterministic: scripted model, tmp directories, no network, no live DB.
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest import agent_runs as rest_agent_runs
from aci.adapters.inbound.rest.errors import register_error_handlers
from aci.adapters.inbound.rest.wiring import Container, Settings
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.application.workspace_changes import (
    MANIFEST_DIRNAME,
    manifest_path,
    read_start_manifest,
)
from aci.domain.runtime.actions import FinalCandidate, ToolCall, ToolCallBatchAction
from aci.runtime.model_gateway import FakeModelGateway

WORKSPACE = "proj"
SECRET = "sk-abcdefghijklmnopqrst"
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----\n"


class Roots:
    """Server-side directories: ``workspaces/proj`` (the source) + ``runs``."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.workspace_root = tmp_path / "workspaces"
        self.runs_root = tmp_path / "runs"
        self.source = self.workspace_root / WORKSPACE
        (self.source / "src").mkdir(parents=True)
        (self.source / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (self.source / "README.md").write_text("# demo\n", encoding="utf-8")

    def run_dir(self, run_id: str) -> Path:
        path = self.runs_root / run_id
        assert path.is_dir(), f"no run dir for {run_id}"
        return path


class _GatewayFactory:
    def __init__(self, gateway: FakeModelGateway) -> None:
        self.gateway = gateway

    def build(self) -> object:
        return self.gateway


class _NullToolFactory:
    def build(self) -> object:
        from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime

        class _NoDispatch:
            def dispatch(self, tool: object, args: object, envelope: object) -> object:
                raise AssertionError("a workspace run must get the real workspace tools")

        return ToolRuntime(ToolRegistry(), dispatcher=cast("Any", _NoDispatch()))


class _NullCapabilityFactory:
    def build(self) -> object:
        class _None:
            def handle_request(self, request: object, snapshot: object) -> list[object]:
                return []

        return _None()


class _NullContextFactory:
    def build(self) -> object:
        from aci.runtime.context_engine import ContextBudget, ContextEngine

        return ContextEngine(ContextBudget(total_tokens=60_000))


def _service(script: list[Any], roots: Roots) -> AgentRunService:
    return AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _GatewayFactory(FakeModelGateway(script))),
        tool_executor_factory=cast(ModelGatewayFactory, _NullToolFactory()),
        capability_runtime_factory=cast(ModelGatewayFactory, _NullCapabilityFactory()),
        context_engine_factory=cast(ModelGatewayFactory, _NullContextFactory()),
        workspace_root=str(roots.workspace_root),
        runs_root=str(roots.runs_root),
        process_prefixes=[sys.executable],
    )


def _client(service: AgentRunService) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    container = cast(
        Container,
        type(
            "C",
            (),
            {
                "agent_run_service": service,
                "settings": Settings(agent_runs_token=""),
            },
        )(),
    )
    app.dependency_overrides[rest_agent_runs.get_container] = lambda: container
    app.include_router(rest_agent_runs.router)
    return TestClient(app)


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "objective": "change the project",
        "acceptance_criteria": ["src/greet.py exists"],
        "workspace": WORKSPACE,
    }
    body.update(overrides)
    return body


def _write(path: str, content: str, call_id: str = "c1") -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[
            ToolCall(
                call_id=call_id, tool_id="write_file", arguments={"path": path, "content": content}
            )
        ]
    )


def _run(service: AgentRunService) -> dict[str, Any]:
    """A completed workspace run + its GET body (both carry `changes`)."""
    data = _client(service).post("/v1/agent-runs", json=_body()).json()
    assert data["status"] == "succeeded", data
    got = _client(service).get(f"/v1/agent-runs/{data['run_id']}").json()
    assert got["status"] == "succeeded", got
    return got


def _assert_no_server_paths(roots: Roots, text: str) -> None:
    for path in (roots.runs_root, roots.workspace_root, roots.tmp):
        assert str(path) not in text
        assert str(path.resolve()) not in text


# -- 1: no server path on the wire, paths stay workspace-relative ------------


def test_changes_never_carry_server_paths(tmp_path: Path) -> None:
    """The diff echoes workspace file content — it must never echo a server
    path: not the runs root, not the workspace root, not the manifest's
    copy-source path (an absolute server path)."""
    roots = Roots(tmp_path)
    service = _service(
        [
            _write("src/greet.py", "def greet():\n    return 'hi'\n"),
            FinalCandidate(summary="done", changes=["src/greet.py"]),
        ],
        roots,
    )
    got = _run(service)
    assert got["changes"] is not None
    assert "src/greet.py" in got["changes"]["diff"]
    _assert_no_server_paths(roots, json.dumps(got))


def test_change_paths_are_workspace_relative_posix(tmp_path: Path) -> None:
    """Every ``files[].path`` is a workspace-relative POSIX path: never
    absolute, never ``..``, never a backslash — the walk yields relative
    paths by construction; this pins it on the wire."""
    roots = Roots(tmp_path)
    service = _service(
        [
            _write("src/deep/mod.py", "x = 1\n"),
            _write("top.txt", "y = 2\n", call_id="c2"),
            FinalCandidate(summary="done", changes=["src/deep/mod.py", "top.txt"]),
        ],
        roots,
    )
    got = _run(service)
    assert got["changes"] is not None
    paths = [f["path"] for f in got["changes"]["files"]]
    assert paths == ["src/deep/mod.py", "top.txt"]
    for path in paths:
        assert not path.startswith("/")
        assert ".." not in path.split("/")
        assert "\\" not in path
        assert "\x00" not in path


# -- 2: the diff text is scrubbed with the run's own guard (§14) --------------


def test_a_secret_the_run_wrote_is_redacted_in_the_diff(tmp_path: Path) -> None:
    """The run's write_file observation carries only the path + size (the
    guard never sees the content), but the DIFF would echo it wholesale —
    the new surface gets the guard's own redaction: the exact
    ``[REDACTED:<label>]`` replacement, on POST and on GET."""
    roots = Roots(tmp_path)
    service = _service(
        [
            _write("token.txt", f"api_key = {SECRET}\n"),
            FinalCandidate(summary="done", changes=["token.txt"]),
        ],
        roots,
    )
    got = _run(service)
    assert got["changes"] is not None
    assert SECRET not in json.dumps(got)
    assert "[REDACTED:api token (sk-)]" in got["changes"]["diff"]
    # The file itself is still listed — only the text is scrubbed.
    assert [f["path"] for f in got["changes"]["files"]] == ["token.txt"]
    assert (
        got["changes"]["files"][0]["sha256_after"]
        == hashlib.sha256(f"api_key = {SECRET}\n".encode()).hexdigest()
    )


def test_a_private_key_file_is_withheld_entirely(tmp_path: Path) -> None:
    """A private key block is never redactable per the guard (it BLOCKS);
    the diff withholds the whole hunk — the file stays listed with hashes."""
    roots = Roots(tmp_path)
    service = _service(
        [_write("key.pem", PRIVATE_KEY), FinalCandidate(summary="done", changes=["key.pem"])],
        roots,
    )
    got = _run(service)
    assert got["changes"] is not None
    assert "MIIB" not in json.dumps(got)
    assert "PRIVATE KEY" not in got["changes"]["diff"]
    assert "[diff withheld: secret pattern (private key block)]" in got["changes"]["diff"]
    assert [f["path"] for f in got["changes"]["files"]] == ["key.pem"]


# -- 3: the manifest is outside the model's write grants ---------------------


def test_the_start_manifest_is_outside_the_runs_write_grants(tmp_path: Path) -> None:
    """The model holds write grants over the whole working copy: a manifest
    living inside it could be rewritten to hide the run's own changes. It
    lives in a SIBLING dir under the runs root and a run that wrote files
    leaves it exactly as it was written at start."""
    roots = Roots(tmp_path)
    service = _service(
        [
            _write("src/greet.py", "def greet():\n    return 'hi'\n"),
            FinalCandidate(summary="done", changes=["src/greet.py"]),
        ],
        roots,
    )
    data = _client(service).post("/v1/agent-runs", json=_body()).json()
    assert data["status"] == "succeeded", data
    run_id = data["run_id"]
    target = manifest_path(roots.runs_root, run_id)
    assert target.is_file()
    assert not target.is_relative_to(roots.run_dir(run_id))
    assert target.parent.name == MANIFEST_DIRNAME
    # The baseline still describes the PRE-run state: the file the run
    # created is not in it (a tampered baseline would hide the change), and
    # the untouched file keeps its start hash.
    manifest = read_start_manifest(roots.runs_root, run_id)
    assert manifest is not None
    assert set(manifest.files) == {"src/app.py", "README.md"}
    assert manifest.files["src/app.py"] == hashlib.sha256(b"print('hello')\n").hexdigest()
    # And the run's own copy of the manifest does not exist inside the workspace.
    assert list(roots.run_dir(run_id).rglob(f"{MANIFEST_DIRNAME}*")) == []


def test_kernel_infrastructure_never_appears_as_a_change(tmp_path: Path) -> None:
    """The kernel writes its Seatbelt profile into the run workspace
    (xl-harness FINDING 5) — infrastructure, never task output: it must not
    show up in the change list (planted here deterministically; on Linux the
    bwrap sandbox writes no profile file at all)."""
    roots = Roots(tmp_path)
    service = _service(
        [
            _write("src/greet.py", "def greet():\n    return 'hi'\n"),
            FinalCandidate(summary="done", changes=["src/greet.py"]),
        ],
        roots,
    )
    data = _client(service).post("/v1/agent-runs", json=_body()).json()
    assert data["status"] == "succeeded", data
    (roots.run_dir(data["run_id"]) / ".aci-sandbox-profile.sb").write_text(
        "(version 1)", encoding="utf-8"
    )
    got = _client(service).get(f"/v1/agent-runs/{data['run_id']}").json()
    assert got["changes"] is not None
    assert [f["path"] for f in got["changes"]["files"]] == ["src/greet.py"]


# -- 4: a tampered manifest never widens the read -----------------------------


def test_tampered_manifest_source_never_reads_outside(tmp_path: Path) -> None:
    """The manifest's ``source`` is re-validated at READ time (under the
    workspace root or the runs root): a manifest pointing elsewhere —
    tampered, or a config change — degrades the hunk, never reads the file."""
    roots = Roots(tmp_path)
    service = _service(
        [
            _write("src/app.py", "print('rewritten by the run')\n"),
            FinalCandidate(summary="done", changes=["src/app.py"]),
        ],
        roots,
    )
    data = _client(service).post("/v1/agent-runs", json=_body()).json()
    assert data["status"] == "succeeded", data
    run_id = data["run_id"]
    # A plausible start state OUTSIDE the server's roots: same rel path, and
    # content whose hash matches the manifest (what a broken validation would
    # happily diff from).
    outside = tmp_path / "elsewhere"
    (outside / "src").mkdir(parents=True)
    (outside / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    target = manifest_path(roots.runs_root, run_id)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["source"] = str(outside.resolve())
    target.write_text(json.dumps(payload), encoding="utf-8")

    got = _client(service).get(f"/v1/agent-runs/{run_id}").json()
    assert got["changes"] is not None
    by_path = {f["path"]: f for f in got["changes"]["files"]}
    assert by_path["src/app.py"]["status"] == "modified"  # the LIST is unaffected
    assert (
        "[diff unavailable: the source workspace changed since run start]"
        in (got["changes"]["diff"])
    )
    # The outside file's content never made it into the diff.
    assert "print('hello')" not in got["changes"]["diff"]
    _assert_no_server_paths(roots, json.dumps(got))


def test_a_vanished_working_copy_is_null_changes_not_an_error(tmp_path: Path) -> None:
    """GET of a run whose copy was cleaned up stays a 200 with the result —
    ``changes`` honestly null, no path in the body."""
    roots = Roots(tmp_path)
    service = _service(
        [
            _write("src/greet.py", "def greet():\n    return 'hi'\n"),
            FinalCandidate(summary="done", changes=["src/greet.py"]),
        ],
        roots,
    )
    data = _client(service).post("/v1/agent-runs", json=_body()).json()
    assert data["status"] == "succeeded", data
    shutil.rmtree(roots.run_dir(data["run_id"]))
    response = _client(service).get(f"/v1/agent-runs/{data['run_id']}")
    assert response.status_code == 200
    got = response.json()
    assert got["status"] == "succeeded"
    assert got["changes"] is None
    _assert_no_server_paths(roots, json.dumps(got))
