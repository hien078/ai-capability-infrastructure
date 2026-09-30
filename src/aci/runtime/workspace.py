"""WorkspaceManager (harness.md §16): authority decides, the workspace enforces (INV-04)."""

import hashlib
import json
import os
import signal
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import ExecutionEnvelope
from aci.runtime.protocols import ProcessResult


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Hard-kill the whole process group (§16: runaway processes get no grace period)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class _RootedWorkspace:
    """Shared root-dir filesystem core: every path is validated BEFORE any fs touch."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._root_real = Path(os.path.realpath(self._root))
        self._snapshots: dict[str, dict[str, str]] = {}

    @property
    def root(self) -> Path:
        return self._root

    def _validate(self, path: str) -> Path:
        """Resolve `path` under the root or raise — traversal, absolute, and symlink escape."""
        if not path or os.path.isabs(path) or "\x00" in path:
            raise DomainError(
                ErrorCode.WORKSPACE_PATH_INVALID, f"workspace path is empty or absolute: {path!r}"
            )
        norm = os.path.normpath(path)
        if norm == ".." or norm.startswith(".." + os.sep):
            raise DomainError(
                ErrorCode.WORKSPACE_PATH_INVALID, f"workspace path escapes root via '..': {path!r}"
            )
        candidate = self._root / norm
        real = Path(os.path.realpath(candidate))
        if real != self._root_real and self._root_real not in real.parents:
            raise DomainError(
                ErrorCode.WORKSPACE_PATH_INVALID,
                f"workspace path resolves outside root (symlink escape?): {path!r}",
            )
        return candidate

    def read_file(self, path: str) -> str:
        return self._validate(path).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        target = self._validate(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def list_dir(self, path: str) -> list[str]:
        return sorted(p.name for p in self._validate(path).iterdir())

    def _scan(self) -> dict[str, str]:
        """Walk the tree collecting {relpath: sha256}; symlink-escaping entries are skipped."""
        mapping: dict[str, str] = {}
        for dirpath, _dirnames, filenames in os.walk(self._root, followlinks=False):
            for name in filenames:
                abs_path = Path(dirpath) / name
                real = Path(os.path.realpath(abs_path))
                if real != self._root_real and self._root_real not in real.parents:
                    continue
                mapping[str(abs_path.relative_to(self._root))] = hashlib.sha256(
                    abs_path.read_bytes()
                ).hexdigest()
        return mapping

    def snapshot(self) -> str:
        mapping = self._scan()
        payload = json.dumps(mapping, sort_keys=True).encode("utf-8")
        snapshot_id = hashlib.sha256(payload).hexdigest()
        self._snapshots[snapshot_id] = mapping
        return snapshot_id

    def diff(self, snapshot_id: str) -> str:
        stored = self._snapshots.get(snapshot_id)
        if stored is None:
            raise DomainError(
                ErrorCode.WORKSPACE_SNAPSHOT_NOT_FOUND, f"unknown snapshot: {snapshot_id!r}"
            )
        current = self._scan()
        lines = [f"--- snapshot {snapshot_id}", f"+++ workspace {self._root}"]
        for path in sorted(set(stored) | set(current)):
            if path not in stored:
                lines.append(f"+ {path}")
            elif path not in current:
                lines.append(f"- {path}")
            elif stored[path] != current[path]:
                lines.append(f"~ {path}")
        return "\n".join(lines)

    def execute(self, command: list[str], timeout_ms: int) -> ProcessResult:
        raise NotImplementedError("workspace backend does not implement execute()")

    def terminate(self) -> None:
        raise NotImplementedError("workspace backend does not implement terminate()")


class LocalWorkspace(_RootedWorkspace):
    """§16.4 — trusted local execution; paths stay under root, processes die on timeout."""

    def __init__(self, root: str | Path) -> None:
        super().__init__(root)
        self._process: subprocess.Popen[bytes] | None = None

    def execute(self, command: list[str], timeout_ms: int) -> ProcessResult:
        if not command:
            raise DomainError(ErrorCode.WORKSPACE_PATH_INVALID, "execute requires a command")
        start = time.monotonic()
        proc = subprocess.Popen(
            command,
            cwd=self._root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self._process = proc
        try:
            stdout, stderr = proc.communicate(timeout=timeout_ms / 1000)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(proc)
            stdout, stderr = proc.communicate()
        finally:
            self._process = None
        return ProcessResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            duration_ms=int((time.monotonic() - start) * 1000),
            timed_out=timed_out,
        )

    def terminate(self) -> None:
        """Hard-terminate the currently running process group (§16 runaway processes)."""
        if self._process is not None:
            _kill_process_group(self._process)


class SandboxWorkspace(_RootedWorkspace):
    """§16.5 — untrusted code goes here; container backend is P2, so execute is a stub."""

    def execute(self, command: list[str], timeout_ms: int) -> ProcessResult:
        raise NotImplementedError(
            "SandboxWorkspace.execute: container backend is P2 (harness.md §16.5) — not built yet"
        )

    def terminate(self) -> None:
        return None


class RemoteWorkspace:
    """§16.6 — container/K8s/cloud execution. The interface is identical to
    LocalWorkspace (§16.3: the rest of the kernel must not care where work
    happens); the transport (agent process over SSH/gRPC) is P2 deployment
    work, so execute is an honest stub until a real backend exists."""

    def __init__(self, workspace_id: str, endpoint: str) -> None:
        self.workspace_id = workspace_id
        self.endpoint = endpoint

    def read_file(self, path: str) -> str:
        raise NotImplementedError("RemoteWorkspace transport is P2 (harness.md §16.6)")

    def write_file(self, path: str, content: str) -> None:
        raise NotImplementedError("RemoteWorkspace transport is P2 (harness.md §16.6)")

    def list_dir(self, path: str) -> list[str]:
        raise NotImplementedError("RemoteWorkspace transport is P2 (harness.md §16.6)")

    def execute(self, command: list[str], timeout_ms: int) -> ProcessResult:
        raise NotImplementedError("RemoteWorkspace transport is P2 (harness.md §16.6)")

    def terminate(self) -> None:
        return None

    def snapshot(self) -> str:
        raise NotImplementedError("RemoteWorkspace transport is P2 (harness.md §16.6)")

    def diff(self, snapshot_id: str) -> str:
        raise NotImplementedError("RemoteWorkspace transport is P2 (harness.md §16.6)")


class WorkspaceManager:
    """§16 — creates workspaces, tracks them by id, and re-checks every path against both the
    workspace root and the ExecutionEnvelope filesystem scopes (INV-04: authority decided
    upstream, enforcement happens here)."""

    def __init__(self) -> None:
        self._workspaces: dict[str, _RootedWorkspace] = {}
        self._envelopes: dict[str, ExecutionEnvelope | None] = {}

    def create_local(self, root: str | Path, envelope: ExecutionEnvelope | None = None) -> str:
        workspace_id = f"ws-{uuid.uuid4().hex[:12]}"
        self._workspaces[workspace_id] = LocalWorkspace(root)
        self._envelopes[workspace_id] = envelope
        return workspace_id

    def _get(self, workspace_id: str) -> _RootedWorkspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise DomainError(ErrorCode.WORKSPACE_NOT_FOUND, f"unknown workspace: {workspace_id!r}")
        return workspace

    def _check_envelope(self, workspace_id: str, path: str, mode: str) -> None:
        """Re-check a workspace-relative path against envelope scopes before any fs touch."""
        envelope = self._envelopes[workspace_id]
        if envelope is None:
            return
        if envelope.expires_at is not None and envelope.expires_at <= datetime.now(UTC):
            raise DomainError(ErrorCode.AUTHORITY_EXPIRED, f"envelope expired for {workspace_id}")
        workspace = self._workspaces[workspace_id]
        assert workspace is not None
        abs_path = Path(os.path.realpath(workspace.root / path))
        scopes = envelope.filesystem.read if mode == "read" else envelope.filesystem.write
        for prefix in scopes:
            prefix_real = Path(os.path.realpath(prefix))
            if abs_path == prefix_real or prefix_real in abs_path.parents:
                return
        raise DomainError(
            ErrorCode.PERMISSION_DENIED,
            f"path {path!r} is outside envelope {mode} scopes for {workspace_id}",
        )

    def read_file(self, workspace_id: str, path: str) -> str:
        self._get(workspace_id)
        self._check_envelope(workspace_id, path, "read")
        return self._get(workspace_id).read_file(path)

    def write_file(self, workspace_id: str, path: str, content: str) -> None:
        self._get(workspace_id)
        self._check_envelope(workspace_id, path, "write")
        self._get(workspace_id).write_file(path, content)

    def list_dir(self, workspace_id: str, path: str) -> list[str]:
        self._get(workspace_id)
        self._check_envelope(workspace_id, path, "read")
        return self._get(workspace_id).list_dir(path)

    def execute(self, workspace_id: str, command: list[str], timeout_ms: int) -> ProcessResult:
        workspace = self._get(workspace_id)
        envelope = self._envelopes[workspace_id]
        if envelope is not None and envelope.process.allowed_prefixes:
            allowed = any(
                command and command[0].startswith(prefix)
                for prefix in envelope.process.allowed_prefixes
            )
            if not allowed:
                raise DomainError(
                    ErrorCode.PERMISSION_DENIED,
                    f"command {command[:1]!r} is outside envelope process scopes "
                    f"for {workspace_id}",
                )
        return workspace.execute(command, timeout_ms)

    def terminate(self, workspace_id: str) -> None:
        self._get(workspace_id).terminate()

    def snapshot(self, workspace_id: str) -> str:
        return self._get(workspace_id).snapshot()

    def diff(self, workspace_id: str, snapshot_id: str) -> str:
        return self._get(workspace_id).diff(snapshot_id)


__all__ = ["LocalWorkspace", "SandboxWorkspace", "WorkspaceManager"]
