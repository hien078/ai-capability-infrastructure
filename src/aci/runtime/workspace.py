"""WorkspaceManager (harness.md §16): authority decides, the workspace enforces (INV-04)."""

import hashlib
import json
import os
import signal
import subprocess
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import ExecutionEnvelope
from aci.runtime.protocols import ProcessResult
from aci.runtime.sandbox import NoSandbox, ProcessSandbox

#: Tool caches and VCS metadata: never workspace state, never a side effect.
NOISE_DIRS: frozenset[str] = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}
)


def command_within_prefixes(command: Sequence[str], prefixes: Sequence[str]) -> bool:
    """Token-aware, fail-closed process scope (§13.4): argv must start with the
    whitespace-split tokens of an allowed prefix. `pytest` never admits
    `pytest-evil`, an empty prefix admits nothing, and no prefixes deny all.
    A multi-token prefix never admits a program whose NAME is the joined
    prefix string (m5 review, ADV-5: `["python -m pytest", …]` is not
    `python -m pytest` — matching is token-wise only)."""
    if not command:
        return False
    for prefix in prefixes:
        tokens = prefix.split()
        if not tokens:
            continue
        if list(command[: len(tokens)]) == tokens:
            return True
    return False


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
        # Bytes-exact (no newline translation): an edit round-trips CRLF files
        # and the content hash matches the on-disk hash from `_scan`.
        return self._validate(path).read_bytes().decode("utf-8")

    def write_file(self, path: str, content: str) -> None:
        target = self._validate(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")

    def list_dir(self, path: str) -> list[str]:
        """Sorted entry names; directories carry a trailing `/`."""
        return sorted(p.name + ("/" if p.is_dir() else "") for p in self._validate(path).iterdir())

    def _scan(self) -> dict[str, str]:
        """Walk the tree collecting {posix relpath: sha256} of regular files.
        Symlink-escaping entries, special files (a FIFO would block the read)
        and NOISE_DIRS are skipped."""
        mapping: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(self._root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in NOISE_DIRS]
            for name in filenames:
                abs_path = Path(dirpath) / name
                real = Path(os.path.realpath(abs_path))
                if real != self._root_real and self._root_real not in real.parents:
                    continue
                if not real.is_file():
                    continue
                try:
                    digest = hashlib.sha256(real.read_bytes()).hexdigest()
                except OSError:
                    continue
                mapping[abs_path.relative_to(self._root).as_posix()] = digest
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
    """§16.4 — local execution; paths stay under root, processes die on timeout.

    Every process goes through `sandbox` (§16.5): the agent-run path passes a
    BwrapSandbox (fail closed when unusable); the bare primitive defaults to
    NoSandbox — the caller that exposes a workspace to a model decides."""

    def __init__(self, root: str | Path, sandbox: ProcessSandbox | None = None) -> None:
        super().__init__(root)
        self._sandbox: ProcessSandbox = sandbox if sandbox is not None else NoSandbox()
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def sandbox(self) -> ProcessSandbox:
        return self._sandbox

    def execute(self, command: list[str], timeout_ms: int) -> ProcessResult:
        if not command:
            raise DomainError(ErrorCode.WORKSPACE_PATH_INVALID, "execute requires a command")
        # Raises PERMISSION_DENIED when the sandbox cannot isolate the command
        # (fail closed) — nothing has been started at that point.
        prepared = self._sandbox.prepare(command, self._root_real)
        start = time.monotonic()
        # The process group (start_new_session) is the kill unit: under bwrap
        # its leader is the outer bwrap, whose death takes the sandbox's PID
        # namespace down with it (--die-with-parent + --unshare-pid).
        proc = subprocess.Popen(
            prepared.argv,
            cwd=prepared.cwd,
            env=prepared.env,
            stdin=subprocess.DEVNULL,
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

    def create_local(
        self,
        root: str | Path,
        envelope: ExecutionEnvelope | None = None,
        *,
        sandbox: ProcessSandbox | None = None,
    ) -> str:
        workspace_id = f"ws-{uuid.uuid4().hex[:12]}"
        self._workspaces[workspace_id] = LocalWorkspace(root, sandbox=sandbox)
        self._envelopes[workspace_id] = envelope
        return workspace_id

    def root(self, workspace_id: str) -> Path:
        return self._get(workspace_id).root

    def _get(self, workspace_id: str) -> _RootedWorkspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise DomainError(ErrorCode.WORKSPACE_NOT_FOUND, f"unknown workspace: {workspace_id!r}")
        return workspace

    def _live_envelope(self, workspace_id: str) -> ExecutionEnvelope | None:
        envelope = self._envelopes[workspace_id]
        if envelope is not None and envelope.expires_at is not None:
            if envelope.expires_at <= datetime.now(UTC):
                raise DomainError(
                    ErrorCode.AUTHORITY_EXPIRED, f"envelope expired for {workspace_id}"
                )
        return envelope

    def _check_envelope(self, workspace_id: str, path: str, mode: str) -> None:
        """Re-check a workspace-relative path against envelope scopes before any fs touch.

        Relative scope prefixes resolve against the workspace root (`.` is the
        whole root); absolute prefixes are taken as-is."""
        envelope = self._live_envelope(workspace_id)
        if envelope is None:
            return
        root = self._workspaces[workspace_id].root
        abs_path = Path(os.path.realpath(root / path))
        scopes = envelope.filesystem.read if mode == "read" else envelope.filesystem.write
        for prefix in scopes:
            prefix_real = Path(os.path.realpath(prefix if os.path.isabs(prefix) else root / prefix))
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
        """A bound envelope is fail-closed: no allowed prefixes means no processes."""
        workspace = self._get(workspace_id)
        envelope = self._live_envelope(workspace_id)
        if envelope is not None and not command_within_prefixes(
            command, envelope.process.allowed_prefixes
        ):
            raise DomainError(
                ErrorCode.PERMISSION_DENIED,
                f"command {command[:3]!r} is outside envelope process scopes for {workspace_id}",
            )
        return workspace.execute(command, timeout_ms)

    def terminate(self, workspace_id: str) -> None:
        self._get(workspace_id).terminate()

    def snapshot(self, workspace_id: str) -> str:
        return self._get(workspace_id).snapshot()

    def diff(self, workspace_id: str, snapshot_id: str) -> str:
        return self._get(workspace_id).diff(snapshot_id)

    def file_hashes(self, workspace_id: str) -> dict[str, str]:
        """{posix relpath: sha256} of every workspace file — the side-effect baseline."""
        return self._get(workspace_id)._scan()


__all__ = [
    "NOISE_DIRS",
    "LocalWorkspace",
    "SandboxWorkspace",
    "WorkspaceManager",
    "command_within_prefixes",
]
