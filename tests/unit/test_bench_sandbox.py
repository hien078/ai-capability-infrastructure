"""Unit tests for scripts/bench_sandbox.py — the ONE shared bench isolation
helper (job xl-fix, 2026-10-03). Fakes only: no bwrap, no socat, no network —
the command construction is pure, and the process-group kill is exercised with
plain python processes (the macOS lesson it encodes: a model-written script
that ignores SIGTERM outlived its bench session for hours).

The red state these tests were written against: rc_bench._bwrap had NO
--unshare-net (a run port-scanned localhost and reached the ACI server), no
unix-socket bridges, and subprocess.run(timeout=...) killed only the direct
child — orphaned grandchildren kept running.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bench_sandbox as bs  # noqa: E402


def _executable(path: Path, body: str) -> str:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _fake_bwrap(tmp_path: Path) -> str:
    """A bwrap stand-in: drops the isolation flags (everything up to and
    including the ``--`` separator) and execs the payload — enough to test
    run_sandboxed's composition on a host without bwrap."""
    return _executable(
        tmp_path / "fake-bwrap",
        '#!/bin/sh\nwhile [ "$1" != "--" ] && [ $# -gt 0 ]; do shift; done\nshift\nexec "$@"\n',
    )


def _fake_socat(tmp_path: Path) -> str:
    """A socat stand-in: the OUTSIDE bridge (UNIX-LISTEN) stays alive until
    terminated; the INSIDE listener (TCP-LISTEN) has nothing to do here."""
    return _executable(
        tmp_path / "fake-socat",
        '#!/bin/sh\ncase "$1" in\n  UNIX-LISTEN:*) sleep 600 ;;\n  *) exit 0 ;;\nesac\n',
    )


# -- command construction (pure — no bwrap needed) ---------------------------


class TestSandboxCommand:
    def test_the_rc_bench_isolation_is_kept_not_weakened(self, tmp_path: Path) -> None:
        cmd, _ = bs.sandbox_command(tmp_path / "run", [], ["opencode", "run"])
        joined = " ".join(cmd)
        # The amendment-29 isolation, unchanged from rc_bench._bwrap.
        for flag in ("--tmpfs /home", "--tmpfs /.snapshots", "--tmpfs /tmp", "--tmpfs /var/tmp"):
            assert flag in joined, flag
        assert "--ro-bind /dev/null /run/docker.sock" in joined
        assert "--unshare-pid" in joined
        assert "--proc /proc" in joined
        assert "--die-with-parent" in joined

    def test_network_is_fully_unshared(self, tmp_path: Path) -> None:
        """FIX: --unshare-net — the sandbox has NO network; every allowed
        endpoint comes back through a unix-socket bridge instead."""
        cmd, _ = bs.sandbox_command(tmp_path / "run", [], ["true"])
        assert "--unshare-net" in cmd

    def test_run_root_bound_and_extra_ro(self, tmp_path: Path) -> None:
        root = tmp_path / "run"
        cmd, _ = bs.sandbox_command(root, [tmp_path / "bin"], ["true"])
        i = cmd.index("--bind")
        assert cmd[i + 1] == str(root) and cmd[i + 2] == str(root)
        j = cmd.index("--ro-bind", i)
        assert cmd[j + 1] == str(tmp_path / "bin")

    def test_chdir_is_optional(self, tmp_path: Path) -> None:
        cmd, _ = bs.sandbox_command(tmp_path, [], ["true"])
        assert "--chdir" not in cmd
        cmd, _ = bs.sandbox_command(tmp_path, [], ["true"], workdir=tmp_path / "work")
        assert cmd[cmd.index("--chdir") + 1] == str(tmp_path / "work")

    def test_bridges_inside_and_outside(self, tmp_path: Path) -> None:
        """Each endpoint becomes an OUTSIDE unix-listener -> host TCP bridge
        plus an INSIDE 127.0.0.1 listener -> unix-connect bridge, and the
        inside ones are backgrounded before the client in an sh wrapper."""
        root = tmp_path / "run"
        endpoints = [
            ("127.0.0.1", 20128, "gateway"),
            ("127.0.0.1", 8770, "sidecar"),
        ]
        cmd, outside = bs.sandbox_command(root, [], ["opencode", "run"], endpoints=endpoints)
        sock_gw = bs._sock(root, "gateway")
        sock_sc = bs._sock(root, "sidecar")
        assert outside == [
            ["socat", f"UNIX-LISTEN:{sock_gw},fork", "TCP:127.0.0.1:20128"],
            ["socat", f"UNIX-LISTEN:{sock_sc},fork", "TCP:127.0.0.1:8770"],
        ]
        # the sandbox command is bwrap ... -- sh -c '<inside bridges>; exec "$@"
        sep = cmd.index("--")
        sh = cmd[sep + 1 :]
        assert sh[0] == "/bin/sh" and sh[1] == "-c"
        script = sh[2]
        assert f"socat TCP-LISTEN:20128,bind=127.0.0.1,fork UNIX-CONNECT:{sock_gw} &" in script
        assert f"socat TCP-LISTEN:8770,bind=127.0.0.1,fork UNIX-CONNECT:{sock_sc} &" in script
        assert script.endswith('exec "$@"\n')
        # the client is passed through as "$@" (no re-quoting, no shell parsing)
        assert sh[3:] == ["bench-sandbox", "opencode", "run"]

    def test_no_endpoints_means_no_bridges_no_network(self, tmp_path: Path) -> None:
        cmd, outside = bs.sandbox_command(tmp_path, [], ["true"])
        assert outside == []
        script = cmd[cmd.index("--") + 3]
        assert "socat" not in script
        assert script == 'exec "$@"\n'

    def test_client_elements_are_never_reparsed_as_bwrap_options(self, tmp_path: Path) -> None:
        """A hostile client argument must not become a bwrap flag: the client
        command lives AFTER the -- separator, inside sh's argv."""
        cmd, _ = bs.sandbox_command(tmp_path, [], ["--unshare-net", "--bind", "/etc", "/etc"])
        sep = cmd.index("--")
        assert "--unshare-net" in cmd[sep + 1 :]

    def test_custom_bwrap_and_socat_paths(self, tmp_path: Path) -> None:
        cmd, outside = bs.sandbox_command(
            tmp_path,
            [],
            ["true"],
            endpoints=[("127.0.0.1", 1, "x")],
            bwrap="/usr/local/bin/bwrap",
            socat="/usr/bin/socat",
        )
        assert cmd[0] == "/usr/local/bin/bwrap"
        assert outside[0][0] == "/usr/bin/socat"
        assert "TCP-LISTEN:1," in cmd[cmd.index("--") + 3]


# -- process-tree cleanup (no bwrap needed — plain processes) -----------------


def _wait_gone(pid: int, timeout: float = 10.0) -> None:
    """Poll until kill(pid, 0) says the process is fully gone (a zombie would
    still answer kill(0); a reaped process does not)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"process {pid} survived the timeout kill")


class TestRunWithSession:
    def test_normal_exit(self) -> None:
        rc, timed_out = bs.run_with_session([sys.executable, "-c", "print('ok')"], timeout=60)
        assert timed_out is False
        assert rc == 0

    def test_child_is_its_own_session_leader(self, tmp_path: Path) -> None:
        """The client is a session leader (setsid): its whole tree shares one
        process group that can be killed at once."""
        out = tmp_path / "sid"
        with out.open("w") as fh:
            rc, timed_out = bs.run_with_session(
                [
                    sys.executable,
                    "-c",
                    "import os, sys; print(os.getpid(), os.getsid(0)); sys.stdout.flush()",
                ],
                stdout=fh,
                timeout=60,
            )
        assert not timed_out and rc == 0
        pid, sid = (int(part) for part in out.read_text().split())
        assert pid == sid  # a session leader, not a member of the bench's group

    def test_timeout_kills_the_whole_group_even_when_sigterm_is_ignored(
        self, tmp_path: Path
    ) -> None:
        """THE pin (macOS 2026-10-03): a model-written child that TRAPS SIGTERM
        must still be gone after the bench timeout — the whole process group
        is SIGKILLed, and SIGKILL cannot be trapped."""
        child_pid_file = tmp_path / "child.pid"
        child = tmp_path / "child.py"
        child.write_text(
            "import os, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
            "time.sleep(600)\n"
        )
        parent = tmp_path / "parent.py"
        parent.write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
            "time.sleep(600)\n"
        )
        rc, timed_out = bs.run_with_session(
            [sys.executable, str(parent), str(child), str(child_pid_file)], timeout=3.0
        )
        assert timed_out is True
        assert rc != 0
        _wait_gone(int(child_pid_file.read_text()))

    def test_timeout_kills_grandchildren_too(self, tmp_path: Path) -> None:
        """The whole TREE dies, not just the direct child (the orphan lesson)."""
        marker = tmp_path / "grandchild.pid"
        script = tmp_path / "tree.py"
        script.write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', "
            "\"import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); "
            'time.sleep(600)", sys.argv[1]])\n'
            "time.sleep(600)\n"
        )
        _rc, timed_out = bs.run_with_session(
            [sys.executable, str(script), str(marker)], timeout=2.0
        )
        assert timed_out
        _wait_gone(int(marker.read_text()))


# -- run_sandboxed composition (fake bwrap + fake socat) ----------------------


class TestRunSandboxed:
    def test_composes_bridges_bwrap_and_teardown(self, tmp_path: Path) -> None:
        """The outside bridges start before the client, are terminated after,
        and their logs land under <root>/.net/."""
        root = tmp_path / "run"
        root.mkdir()
        rc, timed_out = bs.run_sandboxed(
            root,
            [],
            [sys.executable, "-c", "print('inside')"],
            endpoints=[("127.0.0.1", 20128, "gateway")],
            bwrap=_fake_bwrap(tmp_path),
            socat=_fake_socat(tmp_path),
            timeout=30,
        )
        assert timed_out is False and rc == 0
        assert (root / bs.NET_DIR).is_dir()
        assert (root / bs.NET_DIR / "gateway.socat.log").is_file()

    def test_timeout_kills_the_sandboxed_client_group(self, tmp_path: Path) -> None:
        root = tmp_path / "run"
        root.mkdir()
        rc, timed_out = bs.run_sandboxed(
            root,
            [],
            [sys.executable, "-c", "import time; time.sleep(600)"],
            bwrap=_fake_bwrap(tmp_path),
            socat="true",
            timeout=2.0,
        )
        assert timed_out is True
        assert rc != 0

    def test_bridge_procs_are_terminated_not_orphaned(self, tmp_path: Path) -> None:
        root = tmp_path / "run"
        root.mkdir()
        fake_socat = _fake_socat(tmp_path)
        bs.run_sandboxed(
            root,
            [],
            ["true"],
            endpoints=[("127.0.0.1", 1, "gw")],
            bwrap=_fake_bwrap(tmp_path),
            socat=fake_socat,
            timeout=30,
        )
        # the OUTSIDE bridge (the sleep 600 one) must be gone after the run
        deadline = time.time() + 5.0
        while time.time() < deadline:
            probe = subprocess.run(["pgrep", "-f", fake_socat], capture_output=True, text=True)
            if not probe.stdout.strip():
                break
            time.sleep(0.1)
        else:
            pytest.fail("the outside bridge socat was left running after the run")
