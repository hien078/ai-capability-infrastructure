"""WorkspaceManager security tests (harness.md §16, INV-04 enforcement)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import (
    ExecutionEnvelope,
    FilesystemScope,
    NetworkScope,
    ProcessScope,
)
from aci.runtime.workspace import LocalWorkspace, SandboxWorkspace, WorkspaceManager


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
