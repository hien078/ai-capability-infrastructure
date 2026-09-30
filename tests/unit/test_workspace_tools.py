"""Standard workspace tools (harness.md §12, §16, §19) through a REAL ToolRuntime:
authority preflight + WorkspaceManager enforcement + evidence conventions."""

import hashlib
import os
import re
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import (
    ExecutionEnvelope,
    FilesystemScope,
    GrantEnvelope,
    NetworkScope,
    ProcessScope,
)
from aci.domain.runtime.evidence import (
    CandidateResult,
    EvidenceItem,
    EvidenceKind,
    ResultContract,
)
from aci.domain.runtime.state import BudgetLedger, RunState, RuntimeStateSnapshot, TaskState
from aci.domain.runtime.tools import SideEffectReport, ToolCall, ToolObservation, ToolSpec
from aci.runtime.tool_runtime import ToolRuntime
from aci.runtime.verification import VerificationManager
from aci.runtime.workspace import WorkspaceManager
from aci.runtime.workspace_tools import (
    VERIFICATION_CHECK_NAME,
    WorkspaceToolDispatcher,
    build_workspace_tool_runtime,
    provision_workspace,
    standard_tool_specs,
    verification_command_check,
)

PY = sys.executable
#: A narrower workspace-relative scope (the '.' whole-workspace grant has its own test).
SCOPE = "src"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot() -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="run-1", created_at=datetime(2026, 9, 30, tzinfo=UTC)),
        task=TaskState(task_id="task-1", objective="edit the workspace"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
    )


def _envelope(
    *,
    read: list[str] | None = None,
    write: list[str] | None = None,
    process: list[str] | None = None,
) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        run_id="run-1",
        workspace_id="ws-1",
        filesystem=FilesystemScope(
            read=[SCOPE] if read is None else read, write=[SCOPE] if write is None else write
        ),
        network=NetworkScope(),
        process=ProcessScope(allowed_prefixes=[PY] if process is None else process),
    )


class Harness:
    def __init__(
        self,
        root: Path,
        envelope: ExecutionEnvelope,
        *,
        allow_commands: bool = True,
        command_timeout_ms: int = 20_000,
        bound: ExecutionEnvelope | None = None,
    ) -> None:
        self.root = root
        self.envelope = envelope
        self.manager = WorkspaceManager()
        self.ws_id = self.manager.create_local(root, envelope=bound or envelope)
        self.runtime: ToolRuntime = build_workspace_tool_runtime(
            self.manager,
            self.ws_id,
            allow_commands=allow_commands,
            command_timeout_ms=command_timeout_ms,
        )
        self._seq = 0

    def call(self, tool_id: str, **arguments: object) -> ToolObservation:
        self._seq += 1
        call = ToolCall(call_id=f"call-{self._seq}", tool_id=tool_id, arguments=dict(arguments))
        return self.runtime.execute(call, snapshot=_snapshot(), envelope=self.envelope)


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    root = tmp_path / "ws"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "README.md").write_text("outside the scope\n")
    return Harness(root, _envelope())


# -- specs ------------------------------------------------------------------------


def test_standard_tool_specs_are_function_safe_closed_schemas() -> None:
    specs = {s.tool_id: s for s in standard_tool_specs(allow_commands=True)}
    assert set(specs) == {"read_file", "list_dir", "write_file", "edit_file", "run_command"}
    for spec in specs.values():
        assert re.fullmatch(r"[a-zA-Z0-9_-]+", spec.tool_id)
        assert spec.description
        assert spec.input_schema["type"] == "object"
        assert spec.input_schema["additionalProperties"] is False
        assert set(spec.input_schema["required"]) == set(spec.input_schema["properties"])
    assert specs["read_file"].side_effect_class == "READ_ONLY"
    assert specs["read_file"].authority_requirements.read_path_args == ["path"]
    assert specs["list_dir"].authority_requirements.read_path_args == ["path"]
    assert specs["write_file"].side_effect_class == "LOCAL_MUTATION"
    assert specs["write_file"].authority_requirements.write_path_args == ["path"]
    edit = specs["edit_file"].authority_requirements
    assert (edit.read_path_args, edit.write_path_args) == (["path"], ["path"])
    assert set(specs["edit_file"].input_schema["required"]) == {"path", "old_string", "new_string"}
    run = specs["run_command"]
    assert run.authority_requirements.command_args == ["command"]
    assert run.input_schema["properties"]["command"]["type"] == "array"
    assert run.input_schema["properties"]["command"]["minItems"] == 1
    assert run.output_policy.truncation == "head_tail"
    assert run.timeout_ms == 120_000


def test_run_command_only_when_commands_allowed() -> None:
    ids = {s.tool_id for s in standard_tool_specs(allow_commands=False)}
    assert ids == {"read_file", "list_dir", "write_file", "edit_file"}
    spec = standard_tool_specs(allow_commands=True, command_timeout_ms=5_000)[-1]
    assert (spec.tool_id, spec.timeout_ms) == ("run_command", 5_000)


# -- read / list -------------------------------------------------------------------


def test_read_file_numbers_lines_and_records_file_evidence(harness: Harness) -> None:
    obs = harness.call("read_file", path="./src//app.py")
    assert obs.status == "success", obs.summary
    assert obs.inline_output == "    1  def add(a, b):\n    2      return a - b"
    assert obs.side_effects == SideEffectReport()
    assert obs.evidence == [
        EvidenceItem(
            kind=EvidenceKind.FILE_STATE,
            ref="file://src/app.py",
            sha256=_sha((harness.root / "src" / "app.py").read_bytes()),
            summary="read",
        )
    ]


def test_read_file_evidence_hash_matches_on_disk_bytes_for_crlf(harness: Harness) -> None:
    (harness.root / "src" / "win.txt").write_bytes(b"one\r\ntwo\r\n")
    obs = harness.call("read_file", path="src/win.txt")
    assert obs.inline_output == "    1  one\n    2  two"
    assert obs.evidence[0].sha256 == harness.manager.file_hashes(harness.ws_id)["src/win.txt"]


def test_list_dir_sorts_and_marks_directories(harness: Harness) -> None:
    obs = harness.call("list_dir", path="src")
    assert obs.status == "success", obs.summary
    assert obs.inline_output == "app.py\npkg/"
    assert obs.evidence == [
        EvidenceItem(kind=EvidenceKind.FILE_STATE, ref="file://src", summary="listed")
    ]


def test_read_outside_read_scope_is_denied(harness: Harness) -> None:
    obs = harness.call("read_file", path="README.md")
    assert obs.status == "denied"
    assert obs.error_class == "AUTHORITY_DENIED"
    assert obs.evidence == []


def test_missing_and_binary_files_are_error_observations_without_server_paths(
    harness: Harness,
) -> None:
    missing = harness.call("read_file", path="src/nope.py")
    assert missing.status == "error"
    assert "src/nope.py" in missing.summary
    assert str(harness.root) not in missing.summary
    (harness.root / "src" / "blob.bin").write_bytes(b"\xff\xfe\x00")
    binary = harness.call("read_file", path="src/blob.bin")
    assert binary.status == "error"
    assert "not a UTF-8 text file" in binary.summary


# -- write / edit ------------------------------------------------------------------


def test_write_file_reports_confirmed_side_effect_and_hash(harness: Harness) -> None:
    content = "print('hi')\n"
    obs = harness.call("write_file", path="src/./new/mod.py", content=content)
    assert obs.status == "success", obs.summary
    assert (harness.root / "src" / "new" / "mod.py").read_text() == content
    assert obs.side_effects == SideEffectReport(
        state="confirmed", resources_changed=["file:src/new/mod.py"]
    )
    assert obs.evidence == [
        EvidenceItem(
            kind=EvidenceKind.FILE_STATE,
            ref="file://src/new/mod.py",
            sha256=_sha(content.encode()),
            summary="written",
        )
    ]


@pytest.mark.parametrize("path", ["README.md", "other/x.py", "srcx/x.py", "../escape.py"])
def test_write_outside_write_scope_is_denied_and_writes_nothing(
    harness: Harness, path: str
) -> None:
    before = sorted(p.relative_to(harness.root.parent) for p in harness.root.parent.rglob("*"))
    obs = harness.call("write_file", path=path, content="pwned")
    assert obs.status == "denied"
    assert obs.error_class == "AUTHORITY_DENIED"
    assert obs.side_effects == SideEffectReport()
    after = sorted(p.relative_to(harness.root.parent) for p in harness.root.parent.rglob("*"))
    assert after == before
    assert (harness.root / "README.md").read_text() == "outside the scope\n"


def test_workspace_manager_reenforces_its_bound_envelope(tmp_path: Path) -> None:
    """Defense in depth: ToolRuntime authorizes against the call envelope, the
    WorkspaceManager still refuses what ITS bound envelope does not cover."""
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    wide = _envelope(write=["src", "other"])
    narrow = _envelope(write=["src"])
    h = Harness(root, wide, bound=narrow)
    obs = h.call("write_file", path="other/x.py", content="nope")
    assert obs.status == "error"
    assert "outside envelope write scopes" in obs.summary
    assert not (root / "other").exists()


def test_edit_file_replaces_the_unique_occurrence(harness: Harness) -> None:
    obs = harness.call(
        "edit_file", path="src/app.py", old_string="return a - b", new_string="return a + b"
    )
    assert obs.status == "success", obs.summary
    on_disk = (harness.root / "src" / "app.py").read_bytes()
    assert on_disk == b"def add(a, b):\n    return a + b\n"
    assert "line 2" in obs.inline_output
    assert obs.side_effects == SideEffectReport(
        state="confirmed", resources_changed=["file:src/app.py"]
    )
    assert obs.evidence == [
        EvidenceItem(
            kind=EvidenceKind.FILE_STATE,
            ref="file://src/app.py",
            sha256=_sha(on_disk),
            summary="written",
        )
    ]


@pytest.mark.parametrize(
    ("old_string", "expected"),
    [("return a * b", "was not found"), ("a", "occurs"), ("", "must not be empty")],
)
def test_edit_file_requires_exactly_one_match(
    harness: Harness, old_string: str, expected: str
) -> None:
    original = (harness.root / "src" / "app.py").read_bytes()
    obs = harness.call("edit_file", path="src/app.py", old_string=old_string, new_string="X")
    assert obs.status == "error"
    assert expected in obs.summary
    assert obs.evidence == []
    assert obs.side_effects == SideEffectReport()
    assert (harness.root / "src" / "app.py").read_bytes() == original


def test_edit_file_two_matches_names_the_count(harness: Harness) -> None:
    (harness.root / "src" / "dup.py").write_text("x = 1\nx = 1\n")
    obs = harness.call("edit_file", path="src/dup.py", old_string="x = 1", new_string="x = 2")
    assert obs.status == "error"
    assert "occurs 2 times" in obs.summary
    assert (harness.root / "src" / "dup.py").read_text() == "x = 1\nx = 1\n"


def test_edit_file_preserves_crlf_line_endings(harness: Harness) -> None:
    (harness.root / "src" / "win.txt").write_bytes(b"one\r\ntwo\r\n")
    obs = harness.call("edit_file", path="src/win.txt", old_string="two", new_string="TWO")
    assert obs.status == "success", obs.summary
    assert (harness.root / "src" / "win.txt").read_bytes() == b"one\r\nTWO\r\n"


# -- run_command -------------------------------------------------------------------


def test_run_command_reports_output_and_hash_diff_side_effects(harness: Harness) -> None:
    (harness.root / "src" / "old.py").write_text("old\n")
    script = (
        "import os, pathlib\n"
        "pathlib.Path('src/gen.py').write_text('generated')\n"
        "pathlib.Path('src/app.py').write_text('changed')\n"
        "os.remove('src/old.py')\n"
        "os.makedirs('src/__pycache__', exist_ok=True)\n"
        "pathlib.Path('src/__pycache__/x.pyc').write_text('noise')\n"
        "print('did it')"
    )
    argv = [PY, "-c", script]
    obs = harness.call("run_command", command=argv)
    assert obs.status == "success", obs.summary
    assert obs.inline_output == "exit_code: 0\n--- stdout ---\ndid it\n\n--- stderr ---\n"
    assert obs.side_effects == SideEffectReport(
        state="confirmed",
        resources_changed=["file:src/app.py", "file:src/gen.py", "file:src/old.py"],
    )
    assert obs.evidence == [
        EvidenceItem(
            kind=EvidenceKind.COMMAND_OUTPUT, ref="cmd://1", summary=f"exit=0 {shlex.join(argv)}"
        )
    ]
    quiet = harness.call("run_command", command=[PY, "-c", "import sys; sys.exit(3)"])
    assert quiet.status == "success"
    assert quiet.inline_output.startswith("exit_code: 3\n")
    assert quiet.side_effects == SideEffectReport(state="none", resources_changed=[])
    assert quiet.evidence[0].ref == "cmd://2"
    assert quiet.evidence[0].summary.startswith("exit=3 ")


def test_run_command_timeout_is_reported_not_raised(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    h = Harness(root, _envelope(), command_timeout_ms=300)
    argv = [PY, "-c", "import time; time.sleep(30)"]
    obs = h.call("run_command", command=argv)
    assert obs.status == "success"
    assert obs.inline_output.startswith("exit_code: ")
    assert "(timed out)" in obs.inline_output.splitlines()[0]
    assert obs.evidence[0].summary == f"exit=timeout {shlex.join(argv)}"


def test_run_command_outside_process_scope_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    h = Harness(root, _envelope(process=[f"{PY} -m json.tool"]))
    obs = h.call("run_command", command=[PY, "-c", "open('ran', 'w')"])
    assert obs.status == "denied"
    assert obs.error_class == "AUTHORITY_DENIED"
    assert not (root / "ran").exists()
    no_process = Harness(root, _envelope(process=[]))
    denied = no_process.call("run_command", command=[PY, "-c", "open('ran', 'w')"])
    assert denied.status == "denied"
    assert not (root / "ran").exists()


def test_run_command_absent_when_commands_disallowed(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    h = Harness(root, _envelope(), allow_commands=False)
    assert "run_command" not in {t.tool_id for t in h.runtime.available_tools()}
    obs = h.call("run_command", command=[PY, "-c", "open('ran', 'w')"])
    assert obs.status == "error"
    assert obs.error_class == "TOOL_NOT_FOUND"
    assert not (root / "ran").exists()


def test_dispatcher_rejects_unknown_tool(tmp_path: Path) -> None:
    manager = WorkspaceManager()
    ws_id = manager.create_local(tmp_path / "ws")
    dispatcher = WorkspaceToolDispatcher(manager, ws_id)
    rogue = ToolSpec(tool_id="delete_everything", version="1.0.0")
    with pytest.raises(DomainError) as exc:
        dispatcher.dispatch(rogue, {}, _envelope())
    assert exc.value.code == ErrorCode.TOOL_NOT_FOUND


def test_dot_grant_covers_the_whole_workspace_through_tool_runtime(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("x = 1\n")
    h = Harness(root, _envelope(read=["."], write=["."]))
    assert h.call("read_file", path="src/app.py").status == "success"


# -- provisioning ------------------------------------------------------------------


def test_provision_copies_source_without_caches_or_escaping_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "src" / "__pycache__").mkdir(parents=True)
    (source / "src" / "app.py").write_text("app\n")
    (source / "src" / "__pycache__" / "app.pyc").write_text("cache")
    for noise in (".git", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache"):
        (source / noise).mkdir()
        (source / noise / "f").write_text("noise")
    secret = tmp_path / "server_secret.env"
    secret.write_text("API_KEY=x")
    (tmp_path / "server_dir").mkdir()
    os.symlink("app.py", source / "src" / "inside_link.py")
    os.symlink(secret, source / "abs_escape.env")
    os.symlink("../server_secret.env", source / "rel_escape.env")
    os.symlink(source / "src" / "app.py", source / "abs_into_source.py")
    os.symlink(tmp_path / "server_dir", source / "dir_escape")

    run = provision_workspace(source, tmp_path / "runs", "run-1")

    assert run == tmp_path / "runs" / "run-1"
    assert (run / "src" / "app.py").read_text() == "app\n"
    assert (run / "src" / "inside_link.py").is_symlink()
    assert (run / "src" / "inside_link.py").read_text() == "app\n"
    for gone in (
        "src/__pycache__",
        ".git",
        ".venv",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "abs_escape.env",
        "rel_escape.env",
        "abs_into_source.py",
        "dir_escape",
    ):
        assert not os.path.lexists(run / gone), gone
    assert secret.read_text() == "API_KEY=x"
    assert (source / "abs_escape.env").is_symlink()


def test_provision_refuses_existing_destination_missing_source_and_unsafe_run_id(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("a")
    runs = tmp_path / "runs"
    provision_workspace(source, runs, "run-1")
    (runs / "run-1" / "a.txt").write_text("run state")
    with pytest.raises(DomainError) as exists:
        provision_workspace(source, runs, "run-1")
    assert exists.value.code == ErrorCode.WORKSPACE_PATH_INVALID
    assert (runs / "run-1" / "a.txt").read_text() == "run state"
    with pytest.raises(DomainError) as missing:
        provision_workspace(tmp_path / "nope", runs, "run-2")
    assert missing.value.code == ErrorCode.WORKSPACE_NOT_FOUND
    with pytest.raises(DomainError) as not_dir:
        provision_workspace(source / "a.txt", runs, "run-3")
    assert not_dir.value.code == ErrorCode.WORKSPACE_NOT_FOUND
    for bad in ("../escape", "a/b", "..", ".", ""):
        with pytest.raises(DomainError) as unsafe:
            provision_workspace(source, runs, bad)
        assert unsafe.value.code == ErrorCode.WORKSPACE_PATH_INVALID
    assert not (tmp_path / "escape").exists()


def test_provision_skips_a_runs_root_nested_in_the_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("a")
    runs = source / "data" / "runs"
    first = provision_workspace(source, runs, "run-1")
    second = provision_workspace(source, runs, "run-2")
    assert (first / "a.txt").exists() and (second / "a.txt").exists()
    assert not (second / "data" / "runs").exists()


# -- verification command check ----------------------------------------------------


def _verify_env(tmp_path: Path) -> tuple[WorkspaceManager, str, Path]:
    root = tmp_path / "ws"
    root.mkdir()
    manager = WorkspaceManager()
    return manager, manager.create_local(root), root


def test_verification_check_passes_on_exit_zero(tmp_path: Path) -> None:
    manager, ws_id, _ = _verify_env(tmp_path)
    argv = [PY, "-c", "print('3 passed')"]
    check = verification_command_check(manager, ws_id, argv, allowed_prefixes=[PY])
    assert check.name == VERIFICATION_CHECK_NAME
    assert check.mandatory is True
    result = check.fn(_snapshot(), CandidateResult(summary="done"))
    assert result.passed
    assert result.mandatory
    assert "3 passed" in result.detail
    assert result.evidence == [
        EvidenceItem(
            kind=EvidenceKind.TEST_RESULT,
            ref=f"verify://{ws_id}",
            summary=f"exit=0 {shlex.join(argv)}",
        )
    ]


def test_verification_check_fails_on_nonzero_exit_with_bounded_tail(tmp_path: Path) -> None:
    manager, ws_id, _ = _verify_env(tmp_path)
    argv = [PY, "-c", "import sys; print('x' * 5000); print('1 failed', file=sys.stderr); exit(1)"]
    check = verification_command_check(manager, ws_id, argv, allowed_prefixes=[PY])
    result = check.fn(_snapshot(), CandidateResult(summary="done"))
    assert not result.passed
    assert len(result.detail) <= 1500
    assert result.detail.rstrip().endswith("1 failed")
    assert result.evidence[0].summary.startswith("exit=1 ")
    verdict = VerificationManager([check]).verify(
        CandidateResult(summary="done"),
        snapshot=_snapshot(),
        contract=ResultContract(contract_id="c", required_fields=["summary"]),
    )
    assert verdict.verdict == "FAIL"


def test_verification_check_fails_on_timeout(tmp_path: Path) -> None:
    manager, ws_id, _ = _verify_env(tmp_path)
    argv = [PY, "-c", "import time; time.sleep(30)"]
    check = verification_command_check(manager, ws_id, argv, allowed_prefixes=[PY], timeout_ms=300)
    result = check.fn(_snapshot(), CandidateResult(summary="done"))
    assert not result.passed
    assert result.detail.startswith("timed out")
    assert result.evidence[0].summary == f"exit=timeout {shlex.join(argv)}"


def test_verification_check_refuses_argv_outside_prefixes_without_running(
    tmp_path: Path,
) -> None:
    manager, ws_id, root = _verify_env(tmp_path)
    argv = [PY, "-c", "open('ran', 'w')"]
    check = verification_command_check(manager, ws_id, argv, allowed_prefixes=[f"{PY} -m pytest"])
    result = check.fn(_snapshot(), CandidateResult(summary="done"))
    assert not result.passed
    assert result.mandatory
    assert "refused" in result.detail
    assert result.evidence == []
    assert not (root / "ran").exists()
    empty = verification_command_check(manager, ws_id, argv, allowed_prefixes=[])
    assert not empty.fn(_snapshot(), CandidateResult(summary="done")).passed
    assert not (root / "ran").exists()
