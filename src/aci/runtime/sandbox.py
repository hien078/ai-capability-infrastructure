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
import re
import resource
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
#: Absolute paths scrubbed from CALLER-VISIBLE sandbox diagnostics: bwrap's
#: own stderr can name server paths (a failed bind source, the probe
#: tempdir), and the refusal text reaches the client verbatim (403).
_ABSOLUTE_PATH = re.compile(r"(?<![\w.-])/(?:[^\s\"']*)")


def _scrub_paths(text: str) -> str:
    """Replace every absolute path in ``text`` with ``<path>`` (ADV-1: the
    probe's bwrap-stderr hint is caller-visible — no server paths on it)."""
    return _ABSOLUTE_PATH.sub("<path>", text)


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
            # the operator's diagnostic — but it can NAME server paths (a
            # failed bind source, the probe tempdir), and this reason goes on
            # a caller-visible 403, so absolute paths are scrubbed (ADV-1).
            detail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            hint = detail[-1][:200] if detail else f"exit {proc.returncode}"
            reason = f"bwrap probe failed ({_scrub_paths(hint)})"
            return f"{reason} — unprivileged user namespaces blocked?"
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
        "the server operator must install the OS sandbox (bubblewrap on Linux, "
        "Seatbelt/sandbox-exec on macOS) or explicitly opt out with ACI_AGENT_SANDBOX=none"
    )


#: Trees whose file contents a Seatbelt-sandboxed process may not read (the
#: server's own $HOME is added at profile time). `/private/var/folders` is the
#: per-user temp/cache tree; `/private/var/root` is root's home.
HIDDEN_READ_ROOTS: tuple[str, ...] = (
    "/Users",
    "/Volumes",
    "/private/var/folders",
    "/private/var/root",
)


def _sbpl_quote(path: str) -> str:
    """A double-quoted SBPL string literal. Seatbelt has no escape syntax, so
    a `"` in a path cannot be represented — such paths are refused upstream."""
    return '"' + path.replace('"', "") + '"'


def _user_process_count() -> int | None:
    """How many processes the current user owns (None when unknown)."""
    try:
        out = subprocess.run(
            ["/bin/ps", "-U", str(os.getuid()), "-o", "pid="],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return len(out.stdout.split())


def _nproc_limit(limits: ResourceLimits) -> int:
    """RLIMIT_NPROC for one Seatbelt command. macOS counts EVERY process of
    the user (no PID namespace), so `ulimit -u max_processes` on a host
    where the user already runs hundreds of processes makes every fork
    fail. Bound the command to `max_processes` MORE than the user has now,
    capped at the hard limit (sh refuses to raise past it)."""
    current = _user_process_count()
    value = limits.max_processes + (current or 0)
    hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
    if hard != resource.RLIM_INFINITY:
        value = min(value, hard)
    return value


@dataclass
class SeatbeltSandbox:
    """macOS Seatbelt (sandbox-exec) profile — the bwrap counterpart for hosts
    without Linux user namespaces. Same contract, same fail-closed rule.

    What the profile grants (everything else is denied — `(deny default)`):

    * READ access to the system (Seatbelt cannot make the system read-only
      the way `--ro-bind /usr` does; instead NOTHING outside the workspace
      is writable) — but file CONTENTS under the user/secret trees
      (`/Users`, `/Volumes`, the per-user temp/cache tree, root's home and
      the server's $HOME — `HIDDEN_READ_ROOTS`) are NOT readable, except the
      workspace itself and the interpreter install trees re-allowed after
      the deny (SBPL: the last matching rule wins). That keeps the model's
      commands away from API keys, ssh keys, other repos and the operator's
      files, as bwrap's "no $HOME/repo/data" does. Metadata (stat) stays
      allowed so path resolution works; directory LISTINGS are file data
      and are denied;
    * file writes ONLY under the run workspace (subpath allowlist) and the
      fresh per-command TMPDIR (Seatbelt resolves /tmp to /private/tmp, so
      the literal must be the resolved real path);
    * NO network — `(deny network*)` blocks sockets for the whole process
      tree (verified: curl inside the sandbox cannot connect);
    * process execution only for the command's own binaries — the model's
      command cannot spawn arbitrary host tools outside the allowlisted
      interpreter prefixes and /usr/bin:/bin:/usr/sbin:/sbin;
    * a CLEARED environment (env -i + the minimal allowlist, same rules as
      bwrap: no server secrets, no host paths, HOME at the workspace).

    Resource limits: Seatbelt has no prlimit(1); the command runs under
    `/bin/sh -c 'ulimit ...; exec ...'` so setrlimit applies to the final
    process (CPU, FSIZE, NPROC, NOFILE — RLIMIT_AS cannot be set on macOS;
    memory is bounded by the workspace timeout, not an AS cap).

    No PID/mount namespace exists on macOS: the process sees host PIDs and
    system paths (read-only in effect). That is the accepted delta vs bwrap;
    the write, network and user-data read boundaries are what fail closed.
    """

    limits: ResourceLimits = field(default_factory=ResourceLimits)
    sandbox_exec_path: str | None = None
    #: The interpreter trees: re-allowed for READ under the hidden roots
    #  (a venv under $HOME) and added to PATH for execution.
    ro_prefixes: Sequence[str] | None = None
    extra_ro_binds: Sequence[str] = ()
    #: Extra trees whose file contents are hidden (beyond HIDDEN_READ_ROOTS).
    extra_hidden_paths: Sequence[str] = ()
    name: str = "seatbelt"

    def __post_init__(self) -> None:
        self._sandbox_exec = (
            self.sandbox_exec_path or shutil.which("sandbox-exec") or "/usr/bin/sandbox-exec"
        )
        prefixes = list(self.ro_prefixes) if self.ro_prefixes is not None else None
        self._prefixes = prefixes if prefixes is not None else interpreter_prefixes()
        self._local_reason: str | None = None
        self._probed = False

    # -- profile ------------------------------------------------------------

    def _exec_prefixes(self) -> list[str]:
        """Interpreter prefixes allowed to execute (normpath'd, existing)."""
        out: list[str] = []
        for raw in [*self._prefixes, *self.extra_ro_binds]:
            if not raw or not os.path.isabs(raw) or not os.path.isdir(raw):
                continue
            path = os.path.normpath(raw)
            if _system_covered(path):
                continue  # already inside /usr or the standard links
            out.append(path)
        return list(dict.fromkeys(out))

    def _hidden_paths(self) -> list[str]:
        """Trees whose file CONTENTS the sandboxed process may not read:
        the fixed user/secret roots + the server's $HOME (both spellings)."""
        out: list[str] = []
        for raw in [*HIDDEN_READ_ROOTS, str(Path.home()), *self.extra_hidden_paths]:
            if raw and os.path.isabs(raw):
                out += [os.path.normpath(raw), os.path.realpath(raw)]
        return list(dict.fromkeys(out))

    def _read_paths(self, workspace: Path) -> list[str]:
        """Re-allowed for READ after the hidden-root deny: the workspace and
        the interpreter install trees (configured + symlink-resolved — Seatbelt
        matches the resolved path; a venv may symlink into another tree).
        A prefix that is a hidden root or an ANCESTOR of one (e.g. `$HOME`,
        `/Users`, `/`) is dropped — re-allowing it would re-open the tree."""
        hidden = self._hidden_paths()
        out = [os.path.realpath(workspace)]
        for raw in [*self._prefixes, *self.extra_ro_binds]:
            if not raw or not os.path.isabs(raw) or not os.path.isdir(raw):
                continue
            for path in (os.path.normpath(raw), os.path.realpath(raw)):
                if not any(_is_within(h, path) for h in hidden):
                    out.append(path)
        return list(dict.fromkeys(out))

    def _write_paths(self, workspace: Path) -> list[str]:
        """The ONLY writable subpaths: the workspace (real path) + a fresh
        per-command TMPDIR under it. A workspace path containing `"` cannot
        be expressed in SBPL — refused at prepare, never silently wider."""
        real = os.path.realpath(workspace)
        return [real, str(Path(real) / "tmp")]

    def profile(self, workspace: Path) -> str:
        """The SBPL profile for one command in `workspace`. Pure. The exact
        operation set was verified on macOS 26 (sandbox-exec rejects unknown
        filter names — e.g. `file-read-metadata*`/`process-signal` are NOT
        valid here; `mach-lookup` takes no wildcard)."""
        writes = " ".join(
            f"(allow file-write* (subpath {_sbpl_quote(p)}))" for p in self._write_paths(workspace)
        )
        hidden = " ".join(f"(subpath {_sbpl_quote(p)})" for p in self._hidden_paths())
        reread = " ".join(f"(subpath {_sbpl_quote(p)})" for p in self._read_paths(workspace))
        return (
            "(version 1)\n"
            "(deny default)\n"
            # Read the system (read-only in effect: writes are denied below
            # unless under the workspace) ...
            "(allow file-read*)\n"
            # ... but NOT file contents under the user/secret trees ...
            f"(deny file-read-data {hidden})\n"
            # ... except the workspace and the interpreter trees (the last
            # matching rule wins, so this re-allow must come AFTER the deny).
            f"(allow file-read-data {reread})\n"
            # The command's own process tree: exec + fork (signals and wait
            # are covered by same-process semantics; no explicit filter).
            "(allow process-exec)\n"
            "(allow process-fork)\n"
            # No network sockets for any process in the sandbox.
            "(deny network*)\n"
            # Mach lookups the runtime needs (bootstrap, dyld) — plain
            # `mach-lookup` allows any service name; the wildcard form
            # `mach-lookup*` is a syntax error in this SBPL version.
            "(allow mach-lookup)\n"
            # Character devices every tool expects (logging to /dev/null,
            # seeding from /dev/urandom): writable, but they are devices —
            # no filesystem path is exposed by allowing them.
            '(allow file-write* (literal "/dev/null"))\n'
            '(allow file-write* (literal "/dev/urandom"))\n'
            '(allow file-write* (literal "/dev/random"))\n'
            '(allow file-write* (literal "/dev/zero"))\n'
            f"{writes}\n"
        )

    def _ulimit_sh(self, command: Sequence[str], nproc: int | None = None) -> list[str]:
        """`sh -c` wrapper applying the rlimits then exec'ing the command.
        RLIMIT_AS (`ulimit -v`) cannot be set on macOS (jetsam owns memory
        policy; sh refuses with EINVAL) — the address-space limit is
        enforced by the workspace timeout + file-size/nproc/nofile instead.
        `nproc` is the RLIMIT_NPROC value; see `_nproc_limit` (macOS counts
        ALL of the user's processes, so a bare max_processes would make every
        fork fail on a busy host)."""
        lim = self.limits
        script = (
            f"ulimit -t {lim.cpu_seconds} "
            f"-f {lim.file_size_bytes // 1024} "
            f"-u {nproc if nproc is not None else lim.max_processes} "
            f"-n {lim.open_files}; "
        )
        quoted = " ".join("'" + c.replace("'", "'\\''") + "'" for c in command)
        return ["/bin/sh", "-c", script + "exec " + quoted]

    def sandbox_env(self, workspace: Path | None = None) -> dict[str, str]:
        """The minimal env (same allowlist as bwrap). PATH = the interpreter
        prefixes FIRST, then the system dirs — the bwrap order: `python3`
        must be the server's interpreter (with pytest), never macOS's
        /usr/bin/python3 Xcode stub. With a workspace, HOME is the real
        workspace path and TMPDIR its tmp/ dir (the only writable places —
        there is no /workspace mount on macOS)."""
        real = os.path.realpath(workspace) if workspace is not None else None
        env = minimal_process_env(real or WORKSPACE_MOUNT)
        visible: list[str] = []
        for p in self._exec_prefixes():
            visible += [str(Path(p) / "bin"), str(Path(p) / "sbin")]
        visible += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        env["PATH"] = os.pathsep.join(dict.fromkeys(e for e in visible if os.path.isdir(e)))
        if real is not None:
            env["TMPDIR"] = str(Path(real) / "tmp")
            # No mount namespace: stop git from discovering a repository
            # ABOVE the workspace (it would see the host's repo as its own).
            env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(real)
        return env

    def build_argv(
        self, command: Sequence[str], workspace: Path, *, nproc: int | None = None
    ) -> list[str]:
        """The full sandbox-exec argv. Pure (no process is started). The
        profile file lives under the workspace (the one writable place) at
        a fixed dot-name; `prepare` writes it right before Popen."""
        env = self.sandbox_env(workspace)
        # env -i: the profile inherits NOTHING from the server process.
        return [
            "/usr/bin/env",
            "-i",
            *[f"{k}={v}" for k, v in sorted(env.items())],
            self._sandbox_exec,
            "-f",
            str(Path(os.path.realpath(workspace)) / ".aci-sandbox-profile.sb"),
            *self._ulimit_sh(command, nproc),
        ]

    def _write_profile(self, workspace: Path) -> Path:
        """Write the SBPL profile into the workspace; returns its path."""
        target = Path(os.path.realpath(workspace)) / ".aci-sandbox-profile.sb"
        target.write_text(self.profile(workspace), encoding="utf-8")
        return target

    # -- usability ----------------------------------------------------------

    def _probe(self) -> str | None:
        if not os.access(self._sandbox_exec, os.X_OK):
            return "sandbox-exec (Seatbelt) is not installed"
        if sys.platform != "darwin":
            return "Seatbelt is macOS-only (this host is not Darwin)"
        if '"' in Path.home().name or '"' in os.getcwd():
            return "a protected path contains a quote (cannot be expressed in SBPL)"
        with tempfile.TemporaryDirectory(prefix="aci-sandbox-probe-") as tmp:
            ws = Path(tmp)
            try:
                profile = self._write_profile(ws)
                argv = self.build_argv([sys.executable, "-c", "pass"], ws)
                proc = subprocess.run(
                    argv,
                    cwd=tmp,
                    env=self.sandbox_env(ws),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"seatbelt probe could not run ({type(exc).__name__})"
            finally:
                profile.unlink(missing_ok=True)
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            hint = detail[-1][:200] if detail else f"exit {proc.returncode}"
            return f"seatbelt probe failed ({hint})"
        return None

    def unavailable_reason(self) -> str | None:
        """Probe once per profile per process (cached; thread-safe)."""
        if self._probed:
            return self._local_reason
        key = (self._sandbox_exec, *self._exec_prefixes())
        with _PROBE_LOCK:
            if key not in _PROBE_CACHE:
                _PROBE_CACHE[key] = self._probe()
            self._local_reason = _PROBE_CACHE[key]
            self._probed = True
        return self._local_reason

    def prepare(self, command: Sequence[str], workspace: Path) -> SandboxedCommand:
        reason = self.unavailable_reason()
        if reason is None and '"' in os.path.realpath(workspace):
            reason = "workspace path contains a quote (cannot be expressed in SBPL)"
        if reason is not None:
            raise DomainError(ErrorCode.PERMISSION_DENIED, sandbox_refusal(reason))
        self._write_profile(workspace)
        (Path(os.path.realpath(workspace)) / "tmp").mkdir(exist_ok=True)
        return SandboxedCommand(
            argv=self.build_argv(command, workspace, nproc=_nproc_limit(self.limits)),
            env=self.sandbox_env(workspace),
            cwd=workspace,
            workspace_alias=os.path.realpath(workspace),
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
    if kind == "seatbelt":
        return SeatbeltSandbox(limits=limits or ResourceLimits(), extra_ro_binds=extra_ro_binds)
    if kind == "none":
        return NoSandbox()
    raise ValueError(f"unknown agent sandbox {kind!r} (expected 'bwrap', 'seatbelt' or 'none')")


#: The sandbox kind that isolates commands on THIS platform: bubblewrap on
#: Linux, Seatbelt on macOS. The settings default routes through this so a
#: deployment on either platform gets the OS sandbox, never a silent none.
PLATFORM_SANDBOX_KIND: str = "seatbelt" if sys.platform == "darwin" else "bwrap"


def build_platform_default_sandbox(
    *,
    limits: ResourceLimits | None = None,
    extra_ro_binds: Sequence[str] = (),
) -> ProcessSandbox:
    """The safe default for code that cannot take a settings-derived kind
    (AgentRunService's constructor default, H-bench): the OS sandbox for
    this platform, fail closed when unusable."""
    return build_process_sandbox(
        PLATFORM_SANDBOX_KIND, limits=limits, extra_ro_binds=extra_ro_binds
    )


__all__ = [
    "WORKSPACE_MOUNT",
    "BwrapSandbox",
    "NoSandbox",
    "ProcessSandbox",
    "ResourceLimits",
    "SandboxedCommand",
    "HIDDEN_READ_ROOTS",
    "SeatbeltSandbox",
    "PLATFORM_SANDBOX_KIND",
    "build_platform_default_sandbox",
    "build_process_sandbox",
    "interpreter_prefixes",
    "minimal_process_env",
    "sandbox_refusal",
]
