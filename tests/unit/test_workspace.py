"""WorkspaceManager security tests (harness.md §16, INV-04 enforcement)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import (
    ExecutionEnvelope,
    FilesystemScope,
    NetworkScope,
    ProcessScope,
)
from aci.runtime.workspace import (
    LocalWorkspace,
    SandboxWorkspace,
    WorkspaceManager,
    command_within_prefixes,
)


def _envelope(root: Path, read: list[str], write: list[str]) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        run_id="run-1",
        workspace_id="ws-irrelevant",
        filesystem=FilesystemScope(read=read, write=write),
        network=NetworkScope(),
        process=ProcessScope(),
    )


def test_rejects_parent_traversal(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "root")
    with pytest.raises(DomainError) as exc:
        ws.read_file("../secret.txt")
    assert exc.value.code == ErrorCode.WORKSPACE_PATH_INVALID


def test_rejects_absolute_path(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "root")
    with pytest.raises(DomainError) as exc:
        ws.read_file(str(tmp_path / "outside.txt"))
    assert exc.value.code == ErrorCode.WORKSPACE_PATH_INVALID


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    os.symlink(outside, root / "leak.txt")
    ws = LocalWorkspace(root)
    with pytest.raises(DomainError) as exc:
        ws.read_file("leak.txt")
    assert exc.value.code == ErrorCode.WORKSPACE_PATH_INVALID


def test_write_read_roundtrip(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "root")
    ws.write_file("sub/dir/file.txt", "hello")
    assert ws.read_file("sub/dir/file.txt") == "hello"
    assert ws.list_dir("sub/dir") == ["file.txt"]


def test_execute_timeout_kills_process(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "root")
    start = time.monotonic()
    result = ws.execute([sys.executable, "-c", "import time; time.sleep(30)"], timeout_ms=500)
    elapsed = time.monotonic() - start
    assert result.timed_out
    assert elapsed < 10


def test_execute_captures_stdout_and_exit_code(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "root")
    result = ws.execute(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
        ],
        timeout_ms=10_000,
    )
    assert not result.timed_out
    assert result.exit_code == 3
    assert result.stdout == "out\n"
    assert "err" in result.stderr


def test_snapshot_diff_detects_change(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "root")
    ws.write_file("a.txt", "one")
    snap = ws.snapshot()
    ws.write_file("b.txt", "two")
    ws.write_file("a.txt", "changed")
    diff = ws.diff(snap)
    assert "~ a.txt" in diff
    assert "+ b.txt" in diff


def test_diff_rejects_unknown_snapshot(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "root")
    with pytest.raises(DomainError) as exc:
        ws.diff("nope")
    assert exc.value.code == ErrorCode.WORKSPACE_SNAPSHOT_NOT_FOUND


def test_envelope_scope_enforced(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    scoped = root / "scoped"
    scoped.mkdir()
    mgr = WorkspaceManager()
    ws_id = mgr.create_local(root, envelope=_envelope(root, read=[str(root)], write=[str(scoped)]))
    # Inside envelope scope: allowed.
    mgr.write_file(ws_id, "scoped/file.txt", "ok")
    assert mgr.read_file(ws_id, "scoped/file.txt") == "ok"
    # Inside workspace root but OUTSIDE envelope write scope: denied.
    with pytest.raises(DomainError) as exc:
        mgr.write_file(ws_id, "elsewhere/file.txt", "nope")
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


def test_envelope_read_scope_enforced(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "public.txt").write_text("pub")
    (root / "private.txt").write_text("priv")
    mgr = WorkspaceManager()
    ws_id = mgr.create_local(
        root, envelope=_envelope(root, read=[str(root / "public.txt")], write=[])
    )
    assert mgr.read_file(ws_id, "public.txt") == "pub"
    with pytest.raises(DomainError) as exc:
        mgr.read_file(ws_id, "private.txt")
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


def test_envelope_process_scope_enforced(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    mgr = WorkspaceManager()
    ws_id = mgr.create_local(
        root,
        envelope=ExecutionEnvelope(
            run_id="run-1",
            workspace_id="ws-x",
            filesystem=FilesystemScope(read=[str(root)], write=[str(root)]),
            network=NetworkScope(),
            process=ProcessScope(allowed_prefixes=[sys.executable]),
        ),
    )
    result = mgr.execute(ws_id, [sys.executable, "-c", "print('ok')"], timeout_ms=10_000)
    assert result.exit_code == 0
    with pytest.raises(DomainError) as exc:
        mgr.execute(ws_id, ["/bin/sh", "-c", "echo nope"], timeout_ms=10_000)
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


def test_manager_unknown_workspace(tmp_path: Path) -> None:
    mgr = WorkspaceManager()
    with pytest.raises(DomainError) as exc:
        mgr.read_file("ws-missing", "a.txt")
    assert exc.value.code == ErrorCode.WORKSPACE_NOT_FOUND


def test_manager_terminate_kills_running_process(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    mgr = WorkspaceManager()
    ws_id = mgr.create_local(root)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    mgr._workspaces[ws_id]._process = proc  # type: ignore[union-index] # noqa: SLF001
    mgr.terminate(ws_id)
    proc.wait(timeout=10)
    assert proc.returncode is not None and proc.returncode != 0


def test_sandbox_execute_is_stub(tmp_path: Path) -> None:
    ws = SandboxWorkspace(tmp_path / "root")
    ws.write_file("a.txt", "sandbox fs works")
    assert ws.read_file("a.txt") == "sandbox fs works"
    with pytest.raises(NotImplementedError):
        ws.execute(["echo", "hi"], timeout_ms=100)
    ws.terminate()  # no-op, proves the interface


def test_manager_snapshot_diff_passthrough(tmp_path: Path) -> None:
    mgr = WorkspaceManager()
    ws_id = mgr.create_local(tmp_path / "root")
    mgr.write_file(ws_id, "x.txt", "v1")
    snap = mgr.snapshot(ws_id)
    mgr.write_file(ws_id, "x.txt", "v2")
    assert "~ x.txt" in mgr.diff(ws_id, snap)


# -- §16.4 process environment: the server env never reaches workspace processes --

_ALLOWED_ENV = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "TZ",
}


def _child_env(ws: LocalWorkspace) -> dict[str, str]:
    result = ws.execute(
        [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
        timeout_ms=10_000,
    )
    assert result.exit_code == 0, result.stderr
    env: dict[str, str] = json.loads(result.stdout)
    return env


def test_execute_does_not_inherit_server_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACI_AGENT_MODEL_API_KEY", "sk-sentinel-must-not-leak")
    monkeypatch.setenv("TZ", "UTC")
    ws = LocalWorkspace(tmp_path / "root")
    env = _child_env(ws)
    assert "ACI_AGENT_MODEL_API_KEY" not in env
    assert "sk-sentinel-must-not-leak" not in json.dumps(env)
    assert set(env) <= _ALLOWED_ENV
    assert env["HOME"] == os.path.realpath(tmp_path / "root")
    assert env["LANG"] == env["LC_ALL"] == "C.UTF-8"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["TZ"] == "UTC"


def test_execute_path_prefers_the_server_interpreter_and_drops_relative_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", "rel/bin", "/usr/bin"]))
    ws = LocalWorkspace(tmp_path / "root")
    entries = _child_env(ws)["PATH"].split(os.pathsep)
    assert entries[0] == os.path.dirname(sys.executable)
    assert all(os.path.isabs(e) for e in entries)
    assert "/usr/bin" in entries
    name = os.path.basename(sys.executable)
    result = ws.execute([name, "-c", "import sys; print(sys.executable)"], timeout_ms=10_000)
    assert result.stdout.strip() == sys.executable


def test_execute_stdin_is_closed(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "root")
    result = ws.execute(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout_ms=10_000
    )
    assert result.stdout.strip() == "''"


# -- relative envelope scopes resolve against the workspace root ---------------


def test_dot_scope_is_the_whole_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    mgr = WorkspaceManager()
    ws_id = mgr.create_local(root, envelope=_envelope(root, read=["."], write=["."]))
    mgr.write_file(ws_id, "a.txt", "top")
    mgr.write_file(ws_id, "deep/nested/b.txt", "deep")
    assert mgr.read_file(ws_id, "deep/nested/b.txt") == "deep"
    assert mgr.list_dir(ws_id, ".") == ["a.txt", "deep/"]


def test_relative_scope_resolves_against_root_not_server_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    cwd = tmp_path / "server_cwd"
    (cwd / "src").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    mgr = WorkspaceManager()
    ws_id = mgr.create_local(root, envelope=_envelope(root, read=["src"], write=["src"]))
    mgr.write_file(ws_id, "src/pkg/mod.py", "x = 1\n")
    assert mgr.read_file(ws_id, "src/pkg/mod.py") == "x = 1\n"
    for denied in ("other/mod.py", "srcx/mod.py", "mod.py"):
        with pytest.raises(DomainError) as exc:
            mgr.write_file(ws_id, denied, "nope")
        assert exc.value.code == ErrorCode.PERMISSION_DENIED
    assert not (cwd / "src" / "pkg").exists()


# -- token-aware, fail-closed process scopes -----------------------------------


@pytest.mark.parametrize(
    ("argv", "prefixes", "allowed"),
    [
        (["python", "-m", "pytest", "-q"], ["python -m pytest"], True),
        (["python", "-m", "pytest"], ["python -m pytest"], True),
        (["python", "-c", "import os"], ["python -m pytest"], False),
        (["python"], ["python -m pytest"], False),
        (["pytest", "-q"], ["pytest"], True),
        (["pytest-evil"], ["pytest"], False),
        (["pytestx", "-q"], ["pytest"], False),
        (["pytest"], [], False),
        (["pytest"], [""], False),
        (["pytest"], ["   "], False),
        ([], ["pytest"], False),
        (["ruff", "check"], ["pytest", "ruff check"], True),
    ],
)
def test_command_within_prefixes_is_token_aware(
    argv: list[str], prefixes: list[str], allowed: bool
) -> None:
    assert command_within_prefixes(argv, prefixes) is allowed


def test_command_within_prefixes_accepts_absolute_interpreter_prefix() -> None:
    assert command_within_prefixes([sys.executable, "-c", "pass"], [sys.executable])
    assert not command_within_prefixes([sys.executable + "x", "-c", "pass"], [sys.executable])


def _process_envelope(root: Path, prefixes: list[str]) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        run_id="run-1",
        workspace_id="ws-x",
        filesystem=FilesystemScope(read=["."], write=["."]),
        network=NetworkScope(),
        process=ProcessScope(allowed_prefixes=prefixes),
    )


def test_bound_envelope_without_prefixes_denies_every_command(tmp_path: Path) -> None:
    root = tmp_path / "root"
    mgr = WorkspaceManager()
    ws_id = mgr.create_local(root, envelope=_process_envelope(root, []))
    with pytest.raises(DomainError) as exc:
        mgr.execute(ws_id, [sys.executable, "-c", "open('ran', 'w')"], timeout_ms=10_000)
    assert exc.value.code == ErrorCode.PERMISSION_DENIED
    assert not (root / "ran").exists()


def test_bound_envelope_prefix_is_token_aware(tmp_path: Path) -> None:
    root = tmp_path / "root"
    mgr = WorkspaceManager()
    prefix = f"{sys.executable} -m json.tool"
    ws_id = mgr.create_local(root, envelope=_process_envelope(root, [prefix]))
    (root / "d.json").write_text('{"a": 1}')
    ok = mgr.execute(ws_id, [sys.executable, "-m", "json.tool", "d.json"], timeout_ms=10_000)
    assert ok.exit_code == 0
    with pytest.raises(DomainError) as exc:
        mgr.execute(ws_id, [sys.executable, "-c", "open('ran', 'w')"], timeout_ms=10_000)
    assert exc.value.code == ErrorCode.PERMISSION_DENIED
    assert not (root / "ran").exists()


def test_expired_envelope_denies_execute(tmp_path: Path) -> None:
    root = tmp_path / "root"
    mgr = WorkspaceManager()
    expired = _process_envelope(root, [sys.executable]).model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    ws_id = mgr.create_local(root, envelope=expired)
    with pytest.raises(DomainError) as exc:
        mgr.execute(ws_id, [sys.executable, "-c", "pass"], timeout_ms=10_000)
    assert exc.value.code == ErrorCode.AUTHORITY_EXPIRED


# -- file_hashes: the side-effect baseline --------------------------------------


def test_file_hashes_skips_noise_dirs_and_uses_posix_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    mgr = WorkspaceManager()
    ws_id = mgr.create_local(root)
    mgr.write_file(ws_id, "src/pkg/mod.py", "x = 1\n")
    for noise in (
        "__pycache__/m.pyc",
        "src/__pycache__/m.pyc",
        ".pytest_cache/v",
        ".mypy_cache/x",
        ".ruff_cache/x",
        ".git/HEAD",
    ):
        mgr.write_file(ws_id, noise, "noise")
    hashes = mgr.file_hashes(ws_id)
    assert set(hashes) == {"src/pkg/mod.py"}
    assert hashes["src/pkg/mod.py"] == hashlib.sha256(b"x = 1\n").hexdigest()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
def test_file_hashes_skips_special_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    mgr = WorkspaceManager()
    ws_id = mgr.create_local(root)
    mgr.write_file(ws_id, "a.txt", "a")
    os.mkfifo(root / "pipe")
    assert set(mgr.file_hashes(ws_id)) == {"a.txt"}


def test_read_file_is_bytes_exact(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "root")
    (tmp_path / "root" / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    assert ws.read_file("crlf.txt") == "one\r\ntwo\r\n"
    ws.write_file("crlf.txt", "one\r\nTWO\r\n")
    assert (tmp_path / "root" / "crlf.txt").read_bytes() == b"one\r\nTWO\r\n"
