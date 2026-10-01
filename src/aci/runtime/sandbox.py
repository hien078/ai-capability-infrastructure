"""ProcessSandbox (harness.md §16.5; user decision 2026-10-01): every process
the kernel starts in a run workspace — the model's `run_command` AND the
client's `verification_command` — runs inside an OS sandbox, not as the bare
server user.

`BwrapSandbox` (bubblewrap, unprivileged user namespaces) is the default:

* read-only system (`/usr` + the standard `/bin` `/lib*` links, from `/etc`
  only the dynamic-linker cache and the timezone file);
* read-only binds of the server interpreter's prefixes (venv + base install),
  so `python -m pytest` from the server environment works — never `$HOME`,
  the server's cwd (the repo, `data/`), the runs root, or `/`;
* the run workspace bind-mounted read-write at the NEUTRAL path
  ``/workspace`` (server paths never reach the process or its output);
* fresh `/proc`, minimal `/dev`, tmpfs `/tmp`; every namespace unshared
  (no network, no host PIDs/IPC/hostname), all capabilities dropped,
  `--die-with-parent` + `--new-session`, a cleared environment;
* resource limits via util-linux `prlimit` INSIDE the sandbox (CPU seconds,
  address space, file size, process count, open files). `prlimit` rather
  than a `preexec_fn` setrlimit: preexec is unsafe in a threaded server, and
  the process count is then charged to the sandbox's own user namespace —
  not to every process the server user already owns.

FAIL CLOSED: a sandbox that is unusable (bwrap/prlimit missing, user
namespaces blocked, the probe command failing) REFUSES every command with a
caller-visible `PERMISSION_DENIED`; it never silently degrades to unsandboxed
execution. `NoSandbox` is the explicit opt-out (`ACI_AGENT_SANDBOX=none`).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from aci.domain.capability.errors import DomainError, ErrorCode

#: Where the run workspace appears inside the sandbox.
WORKSPACE_MOUNT = "/workspace"
#: Top-level system entries reproduced inside the sandbox (as symlinks when
#: the host has merged-/usr links, as read-only binds when they are real dirs).
_SYSTEM_LINKS: tuple[str, ...] = ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
#: The only /etc files a sandboxed process sees (no passwd, no hosts, no ssl:
#: there is no network and no other user to look up).
_ETC_FILES: tuple[str, ...] = ("/etc/ld.so.cache", "/etc/localtime")
_PROBE_TIMEOUT_SECONDS = 15.0


def minimal_process_env(home: str | Path) -> dict[str, str]:
    """§16.4 — a minimal allowlisted environment: the server's env (API keys,
    DB URLs) never reaches workspace processes. Relative PATH entries are
    dropped so a model-written file in the cwd can never shadow a command."""
    entries = [os.path.dirname(sys.executable)]
    entries += os.environ.get("PATH", os.defpath).split(os.pathsep)
    path = os.pathsep.join(dict.fromkeys(e for e in entries if os.path.isabs(e)))
    env = {
        "PATH": path,
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    tz = os.environ.get("TZ")
    if tz:
        env["TZ"] = tz
    return env


@dataclass(frozen=True)
class ResourceLimits:
    """Per-process rlimits applied inside the sandbox (all must be positive)."""

    cpu_seconds: int = 600
    address_space_bytes: int = 4 * 1024**3
    file_size_bytes: int = 1024**3
    max_processes: int = 256
    open_files: int = 1024

    def __post_init__(self) -> None:
        for name in (
            "cpu_seconds",
            "address_space_bytes",
            "file_size_bytes",
            "max_processes",
            "open_files",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"sandbox limit {name} must be positive")

    def prlimit_args(self) -> list[str]:
        return [
            f"--cpu={self.cpu_seconds}",
            f"--as={self.address_space_bytes}",
            f"--fsize={self.file_size_bytes}",
            f"--nproc={self.max_processes}",
            f"--nofile={self.open_files}",
        ]


@dataclass(frozen=True)
class SandboxedCommand:
    """What LocalWorkspace hands to Popen: the (possibly wrapped) argv, the
    environment, the host cwd, and the path the process sees as its workspace."""

    argv: list[str]
    env: dict[str, str]
    cwd: Path
    workspace_alias: str


@runtime_checkable
class ProcessSandbox(Protocol):
    """Wraps one workspace command. `prepare` raises DomainError
    (PERMISSION_DENIED) when the sandbox cannot isolate it — fail closed."""

    name: str

    def unavailable_reason(self) -> str | None:
        """None when usable; otherwise a caller-safe reason (no server paths)."""
        ...

    def prepare(self, command: Sequence[str], workspace: Path) -> SandboxedCommand: ...


class NoSandbox:
    """Explicit opt-out: the command runs directly as the server user, with
    the minimal env and the workspace as cwd (the pre-sandbox §16.4 behavior)."""

    name = "none"

    def unavailable_reason(self) -> str | None:
        return None

    def prepare(self, command: Sequence[str], workspace: Path) -> SandboxedCommand:
        return SandboxedCommand(
            argv=list(command),
            env=minimal_process_env(workspace),
            cwd=workspace,
            workspace_alias=str(workspace),
        )


def interpreter_prefixes() -> list[str]:
    """The running interpreter's install trees (venv + base install, both the
    configured and the symlink-resolved spellings) — what `python -m pytest`
    from the server environment needs to see."""
    candidates = [
        sys.prefix,
        sys.exec_prefix,
        sys.base_prefix,
        sys.base_exec_prefix,
        os.path.dirname(os.path.dirname(os.path.realpath(sys.executable))),
    ]
    cfg = Path(sys.prefix) / "pyvenv.cfg"
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home" and value.strip():
                candidates.append(os.path.dirname(value.strip().rstrip("/")))
    return list(dict.fromkeys(c for c in candidates if c))


def _is_within(path: str, ancestor: str) -> bool:
    return path == ancestor or path.startswith(ancestor.rstrip("/") + "/")


def _system_covered(path: str) -> bool:
    """Already visible read-only through the system mounts."""
    real = os.path.realpath(path)
    return _is_within(real, "/usr") or any(_is_within(path, p) for p in _SYSTEM_LINKS)


def _forbidden_bind(path: str, protected: Sequence[str]) -> bool:
    """A read-only bind must never expose `/`, a protected directory, or any
    ancestor of one (binding the parent of $HOME exposes $HOME)."""
    real = os.path.realpath(path)
    if real == "/":
        return True
    return any(_is_within(os.path.realpath(p), real) for p in protected if p)


_PROBE_CACHE: dict[tuple[str, ...], str | None] = {}
_PROBE_LOCK = threading.Lock()


@dataclass
class BwrapSandbox:
    """bubblewrap profile (see module docstring). The argv builder is pure;
    usability is PROBED once (a real sandboxed interpreter run) and cached."""

    limits: ResourceLimits = field(default_factory=ResourceLimits)
    bwrap_path: str | None = None
    prlimit_path: str | None = None
    #: Read-only binds for the interpreter; None = the running interpreter's.
    ro_prefixes: Sequence[str] | None = None
    #: Extra read-only binds (deployment toolchains), same safety rules.
    extra_ro_binds: Sequence[str] = ()
    name: str = "bwrap"

    def __post_init__(self) -> None:
        self._bwrap = self.bwrap_path or shutil.which("bwrap") or "/usr/bin/bwrap"
        self._prlimit = self.prlimit_path or shutil.which("prlimit") or "/usr/bin/prlimit"
        prefixes = list(self.ro_prefixes) if self.ro_prefixes is not None else None
        self._prefixes = prefixes if prefixes is not None else interpreter_prefixes()
        self._local_reason: str | None = None
        self._probed = False

    # -- profile ------------------------------------------------------------

    def _protected(self, workspace: Path | None) -> list[str]:
        protected = [os.path.expanduser("~"), os.getcwd()]
        if workspace is not None:
            protected.append(str(workspace))
        return protected

    def ro_binds(self, workspace: Path | None = None) -> list[str]:
        """Host paths bound read-only (beyond the system dirs), deduplicated.
        Missing, system-covered and FORBIDDEN paths are dropped."""
        protected = self._protected(workspace)
        binds: list[str] = []
        for raw in [*self._prefixes, *self.extra_ro_binds]:
            if not raw or not os.path.isabs(raw) or not os.path.isdir(raw):
                continue
            path = os.path.normpath(raw)
            if _system_covered(path) or _forbidden_bind(path, protected):
                continue
            binds.append(path)
            real = os.path.realpath(path)
            if real != path and not _forbidden_bind(real, protected):
                binds.append(real)
        return list(dict.fromkeys(binds))

    def sandbox_env(self, workspace: Path | None = None) -> dict[str, str]:
        """The minimal env with HOME at the neutral mount and PATH reduced to
        entries that exist inside the sandbox (system dirs + bound prefixes)."""
        env = minimal_process_env(WORKSPACE_MOUNT)
        binds = self.ro_binds(workspace)
        visible = [
            entry
            for entry in env["PATH"].split(os.pathsep)
            if _system_covered(entry) or any(_is_within(entry, b) for b in binds)
        ]
        env["PATH"] = os.pathsep.join(visible or ["/usr/bin"])
        return env

    def build_argv(self, command: Sequence[str], workspace: Path) -> list[str]:
        """The full bwrap argv for `command` with `workspace` mounted read-write
        at WORKSPACE_MOUNT. Pure (no process is started)."""
        argv: list[str] = [self._bwrap, "--ro-bind", "/usr", "/usr"]
        for link in _SYSTEM_LINKS:
            if os.path.islink(link):
                argv += ["--symlink", os.readlink(link), link]
            elif os.path.isdir(link):
                argv += ["--ro-bind", link, link]
        for etc in _ETC_FILES:
            argv += ["--ro-bind-try", etc, etc]
        for path in self.ro_binds(workspace):
            argv += ["--ro-bind", path, path]
        argv += [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            # A FRESH private tmpfs mounted at the sandbox's /tmp — not the
            # host /tmp (bandit B108 is about using the shared host dir).
            "/tmp",  # nosec B108
            "--bind",
            os.path.realpath(workspace),
            WORKSPACE_MOUNT,
            "--chdir",
            WORKSPACE_MOUNT,
            "--unshare-all",
            "--hostname",
            "sandbox",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--clearenv",
        ]
        for key, value in sorted(self.sandbox_env(workspace).items()):
            argv += ["--setenv", key, value]
        argv += ["--", self._prlimit, *self.limits.prlimit_args(), "--", *command]
        return argv

    # -- usability ----------------------------------------------------------

    def _probe(self) -> str | None:
        if not os.access(self._bwrap, os.X_OK):
            return "bubblewrap (bwrap) is not installed"
        if not os.access(self._prlimit, os.X_OK):
            return "prlimit (util-linux) is not installed"
        with tempfile.TemporaryDirectory(prefix="aci-sandbox-probe-") as tmp:
            argv = self.build_argv([sys.executable, "-c", "pass"], Path(tmp))
            try:
                proc = subprocess.run(
                    argv,
                    cwd=tmp,
                    env=self.sandbox_env(Path(tmp)),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"bwrap probe could not run ({type(exc).__name__})"
        if proc.returncode != 0:
            # bwrap's own stderr ("setting up uid map: Permission denied") is
            # the operator's diagnostic; it carries no workspace path.
            detail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            hint = detail[-1][:200] if detail else f"exit {proc.returncode}"
            return f"bwrap probe failed ({hint}) — unprivileged user namespaces blocked?"
        return None

    def unavailable_reason(self) -> str | None:
        """Probe once per profile per process (cached; thread-safe)."""
        if self._probed:
            return self._local_reason
        key = (self._bwrap, self._prlimit, *self.ro_binds())
        with _PROBE_LOCK:
            if key not in _PROBE_CACHE:
                _PROBE_CACHE[key] = self._probe()
            self._local_reason = _PROBE_CACHE[key]
            self._probed = True
        return self._local_reason

    def prepare(self, command: Sequence[str], workspace: Path) -> SandboxedCommand:
        reason = self.unavailable_reason()
        if reason is not None:
            raise DomainError(ErrorCode.PERMISSION_DENIED, sandbox_refusal(reason))
        return SandboxedCommand(
            argv=self.build_argv(command, workspace),
            env=self.sandbox_env(workspace),
            cwd=workspace,
            workspace_alias=WORKSPACE_MOUNT,
        )


def sandbox_refusal(reason: str) -> str:
    """The caller-visible refusal text (fail closed)."""
    return (
        f"process execution refused: the workspace sandbox is unavailable ({reason}); "
        "the server operator must install bubblewrap and allow unprivileged user "
        "namespaces, or explicitly opt out with ACI_AGENT_SANDBOX=none"
    )


def build_process_sandbox(
    kind: str,
    *,
    limits: ResourceLimits | None = None,
    extra_ro_binds: Sequence[str] = (),
) -> ProcessSandbox:
    """Settings → sandbox. Unknown kinds raise (no silent fallback)."""
    if kind == "bwrap":
        return BwrapSandbox(limits=limits or ResourceLimits(), extra_ro_binds=extra_ro_binds)
    if kind == "none":
        return NoSandbox()
    raise ValueError(f"unknown agent sandbox {kind!r} (expected 'bwrap' or 'none')")


__all__ = [
    "WORKSPACE_MOUNT",
    "BwrapSandbox",
    "NoSandbox",
    "ProcessSandbox",
    "ResourceLimits",
    "SandboxedCommand",
    "build_process_sandbox",
    "interpreter_prefixes",
    "minimal_process_env",
    "sandbox_refusal",
]
