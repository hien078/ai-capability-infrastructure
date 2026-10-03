"""Shared bench sandbox isolation — ONE helper for both benches (2026-10-03).

Used by ``scripts/xl_bench.py`` (the XL orchestration bench) and
``scripts/rc_bench.py`` (the real-client bench). Bench-side only — no product
code, no product defaults.

ISOLATION (the rc_bench pattern, ADR-014 amendment 29 — reused, NOT weakened):
bubblewrap with tmpfs over ALL of ``/home`` (not just ``$HOME``), the btrfs
snapshot trees, ``/tmp`` and ``/var/tmp``; the docker socket masked; the run
root bound back; read-only binds for the client binaries; its own PID
namespace + procfs; ``--die-with-parent``.

NETWORK ISOLATION (the rc-bench v2 lesson, 2026-10-03: a no-skill run
port-scanned localhost, found the ACI server, searched its API and
downloaded the skill): ``--unshare-net`` — the sandbox has NO network at all.
Every endpoint an arm legitimately needs is re-exposed INSIDE the sandbox at
``127.0.0.1:<port>`` through a unix-socket bridge under ``<run root>/.net/``:

    OUTSIDE (host, started before the client):
        socat UNIX-LISTEN:<root>/.net/<name>.sock,fork TCP:<host>:<port>
    INSIDE (sandbox, backgrounded before the client):
        socat TCP-LISTEN:<port>,bind=127.0.0.1,fork UNIX-CONNECT:<root>/.net/<name>.sock

The unix socket lives under the run root (the one path bound into the
sandbox), so it is the ONLY network path in; nothing else on the host — not
the ACI servers, not the DB, not sibling runs — is reachable. The inside
listeners are backgrounded in the sandbox's ``sh`` wrapper and die with the
sandbox (own PID namespace + ``--die-with-parent``). socat is at
``/usr/bin/socat`` on the Linux host.

PROCESS-TREE CLEANUP (the macOS 2026-10-03 lesson: orphaned model-written
brute-force scripts kept 8 cores busy for HOURS after their OpenCode session
ended — they ignored SIGTERM — and ``opencode serve --service`` resumed old
bench sessions): the client always runs in its OWN session (setsid); on
timeout the WHOLE PROCESS GROUP is SIGKILLed (SIGKILL cannot be trapped or
ignored). Bench sessions never live in a shared ``opencode serve --service``:
every run is ``opencode run --standalone`` (both benches pin that).
"""

from __future__ import annotations

import contextlib
import os
import shlex
import signal
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any

#: One allowed network endpoint: (host, port, bridge name). The bridge name
#: picks the unix-socket filename under ``<run root>/.net/``.
Endpoint = tuple[str, int, str]

#: Where the bridge sockets/logs live (inside the run root, which is bound
#: into the sandbox — the only network path in).
NET_DIR = ".net"


def _sock(root: Path, name: str) -> Path:
    return root / NET_DIR / f"{name}.sock"


def sandbox_command(
    root: Path,
    extra_ro: list[Path],
    client: list[str],
    *,
    endpoints: Sequence[Endpoint] = (),
    workdir: Path | None = None,
    bwrap: str = "bwrap",
    socat: str = "socat",
) -> tuple[list[str], list[list[str]]]:
    """Build the network-isolated bwrap command + the OUTSIDE bridge commands.

    Returns ``(bwrap_argv, outside_argv_list)`` — PURE construction, no
    process is started (unit-tested without bwrap). The caller starts each
    outside argv (``subprocess.Popen``) BEFORE the bwrap process and
    terminates them after; :func:`run_sandboxed` does exactly that.

    The bwrap argv ends with ``/bin/sh -c '<inside socats> &\\nexec "$@"'
    bench-sandbox <client...>``: the inside listeners are backgrounded in the
    sandbox's own PID namespace (they die with it) and ``exec`` replaces the
    shell with the client so signals/exit codes pass through unchanged.
    """
    root = Path(root)
    inside: list[list[str]] = []
    outside: list[list[str]] = []
    for host, port, name in endpoints:
        sock = _sock(root, name)
        outside.append([socat, f"UNIX-LISTEN:{sock},fork", f"TCP:{host}:{port}"])
        inside.append([socat, f"TCP-LISTEN:{port},bind=127.0.0.1,fork", f"UNIX-CONNECT:{sock}"])
    script = "".join(f"{shlex.join(argv)} &\n" for argv in inside) + 'exec "$@"\n'
    sh_argv = ["/bin/sh", "-c", script, "bench-sandbox", *client]
    cmd = [
        bwrap,
        "--dev-bind", "/", "/",
        # ALL of /home (not just $HOME) and the btrfs snapshot trees: the
        # first round showed `find /` reaching the repo through
        # /home/.snapshots/<n>/snapshot/... .
        "--tmpfs", "/home",
        "--tmpfs", "/.snapshots",
        "--tmpfs", "/tmp",
        "--tmpfs", "/var/tmp",
        "--ro-bind", "/dev/null", "/run/docker.sock",
        # Own PID namespace + procfs: no other process (sibling runs, the
        # ACI servers) is visible, so /proc/<pid>/{cwd,root,environ} cannot
        # bypass the tmpfs masks.
        "--unshare-pid",
        # NO network at all — every allowed endpoint comes back in through a
        # unix-socket bridge (see the module docstring).
        "--unshare-net",
        "--proc", "/proc",
        "--bind", str(root), str(root),
        "--die-with-parent",
    ]  # fmt: skip
    for path in extra_ro:
        cmd += ["--ro-bind", str(path), str(path)]
    if workdir is not None:
        cmd += ["--chdir", str(workdir)]
    return cmd + ["--", *sh_argv], outside


def _kill_group(proc: subprocess.Popen[Any]) -> None:
    """SIGKILL the whole process group of ``proc`` (its own session). SIGKILL
    cannot be trapped or ignored — the macOS lesson was exactly a model script
    that trapped SIGTERM and outlived its bench session."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


def run_with_session(
    cmd: Sequence[str],
    *,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    stdout: IO[Any] | int | None = None,
    stderr: IO[Any] | int | None = None,
) -> tuple[int | None, bool]:
    """Run ``cmd`` in its OWN session (setsid) so the whole process group can
    be killed at once. Returns ``(returncode, timed_out)``. On timeout the
    ENTIRE group gets SIGKILL — never a lone-child kill that orphans
    grandchildren (a model-written script that ignores SIGTERM is still
    gone: SIGKILL cannot be trapped)."""
    proc = subprocess.Popen(
        [str(part) for part in cmd],
        env=env,
        cwd=None if cwd is None else str(cwd),
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    try:
        return proc.wait(timeout=timeout), False
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return proc.wait(), True


def run_sandboxed(
    root: Path,
    extra_ro: list[Path],
    client: list[str],
    *,
    endpoints: Sequence[Endpoint] = (),
    workdir: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    stdout: IO[Any] | int | None = None,
    stderr: IO[Any] | int | None = None,
    bwrap: str = "bwrap",
    socat: str = "socat",
) -> tuple[int | None, bool]:
    """The full isolation, composed: start the OUTSIDE bridge socats (one
    Popen per endpoint, logging under ``<root>/.net/<name>.socat.log``), run
    the client in the network-isolated sandbox IN ITS OWN SESSION, SIGKILL
    the whole group on timeout, tear the bridges down. Returns
    ``(returncode, timed_out)``."""
    root = Path(root)
    cmd, outside = sandbox_command(
        root,
        extra_ro,
        client,
        endpoints=endpoints,
        workdir=workdir,
        bwrap=bwrap,
        socat=socat,
    )
    net = root / NET_DIR
    if outside:
        net.mkdir(parents=True, exist_ok=True)
    procs: list[subprocess.Popen[Any]] = []
    logs: list[Any] = []
    for (_host, _port, name), argv in zip(endpoints, outside, strict=True):
        log = (net / f"{name}.socat.log").open("w")
        logs.append(log)
        procs.append(
            subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        )
    try:
        return run_with_session(cmd, timeout=timeout, env=env, stdout=stdout, stderr=stderr)
    finally:
        for proc in procs:
            proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
            if proc.poll() is None:
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)
        for log in logs:
            log.close()
