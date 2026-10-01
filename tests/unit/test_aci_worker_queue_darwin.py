"""Darwin (macOS) backend tests for scripts/aci_worker_queue.py.

No real processes are spawned: the backend's process primitives
(_ps/_spawn/_kill/_getpriority) are stubbed with an in-memory process table,
so the REAL start/verify/ownership/signal/RSS-poll logic runs against fake
processes and virtual time. One opt-in smoke test (ACIQ_REAL_SMOKE=1) runs
/bin/sleep through the REAL backend on a real macOS host.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "aci_worker_queue.py"

spec = importlib.util.spec_from_file_location("aci_worker_queue", _SCRIPT)
assert spec is not None and spec.loader is not None
q = importlib.util.module_from_spec(spec)
sys.modules["aci_worker_queue"] = q  # dataclasses resolves annotations via sys.modules
spec.loader.exec_module(q)


# --- fake process table (the backend's primitives, stubbed) ----------------------


class FakeProc:
    """One in-memory process: identity (pid/pgid/lstart/cmd/nice) + liveness."""

    def __init__(
        self,
        pid: int,
        pgid: int,
        cmd: str,
        nice: int,
        lstart: str = "Fri Oct  2 00:00:00 2026",
        rss_kib: int = 1024,
    ) -> None:
        self.pid = pid
        self.pgid = pgid
        self.cmd = cmd
        self.nice = nice
        self.lstart = lstart
        self.rss_kib = rss_kib
        self.alive = True
        self.exit_code: int | None = None
        self.received: list[int] = []
        self.ignore_sigint = False  # a straggler: survives SIGINT, dies on SIGKILL


class FakePopen:
    """Popen stand-in: poll() answers liveness from the fake table."""

    def __init__(self, proc: FakeProc) -> None:
        self.proc = proc
        self.pid = proc.pid

    def poll(self) -> int | None:
        if self.proc.alive:
            return None
        return self.proc.exit_code if self.proc.exit_code is not None else 0


class StubDarwinHost(q.DarwinHostControls):
    """The REAL DarwinHostControls logic over a fake process table.

    Only the four process primitives are stubbed; start/verify/ownership/
    signal/RSS-poll/unit_active/cleanup are the production methods."""

    def __init__(
        self,
        *,
        snapshots: list[q.HostSnapshot] | None = None,
        supervisor_nice: int = 0,
    ) -> None:
        super().__init__()
        self.procs: dict[int, FakeProc] = {}
        self.next_pid = 4100
        self.supervisor_nice = supervisor_nice
        self.snapshots = list(snapshots or [])
        self._snap_idx = 0
        self.spawned_cmds: list[list[str]] = []
        self.spawned_envs: list[dict[str, str]] = []
        self.spawned_cwds: list[str] = []
        self.envs_by_pid: dict[int, dict[str, str]] = {}
        self.kills: list[tuple[int, int]] = []  # (pgid, sig)
        self.receipts: dict[str, int] = {}
        self.logs: dict[str, str] = {}
        self.cleaned: list[str] = []

    # -- stubbed primitives ------------------------------------------------------

    def _ps(self, args: list[str]) -> str:
        col = next((a for a in args if a.endswith("=")), "")
        if "-g" in args:
            pgid = int(args[args.index("-g") + 1])
            members = [p for p in self.procs.values() if p.alive and p.pgid == pgid]
            if col == "rss=":
                return "".join(f"{p.rss_kib}\n" for p in members)
            if col == "pid=":
                return "".join(f"{p.pid}\n" for p in members)
            return ""
        if "-p" in args:
            pid = int(args[args.index("-p") + 1])
            proc = self.procs.get(pid)
            if proc is None or not proc.alive:
                return ""  # real ps: exit 1, EMPTY output for a missing pid
            return {
                "pgid=": f"{proc.pgid}",
                "lstart=": proc.lstart,
                "command=": proc.cmd,
                "pid=": f"{proc.pid}",
                "rss=": f"{proc.rss_kib}",
            }.get(col, "")
        return ""

    def _spawn(self, cmd: list[str], env: dict[str, str], cwd: str, log_path: Path) -> FakePopen:
        pid = self.next_pid
        self.next_pid += 1
        # `nice -n 10` INCREMENTS the parent's nice (capped at 20) — mirrored.
        proc = FakeProc(
            pid=pid,
            pgid=pid,  # start_new_session: the child leads its own group
            cmd=" ".join(cmd),
            nice=min(20, self.supervisor_nice + q.DARWIN_WORKER_NICE),
        )
        self.procs[pid] = proc
        self.spawned_cmds.append(list(cmd))
        self.spawned_envs.append(dict(env))
        self.spawned_cwds.append(cwd)
        self.envs_by_pid[pid] = dict(env)
        return FakePopen(proc)

    def _kill(self, pgid: int, sig: int) -> bool:
        self.kills.append((pgid, sig))
        for proc in self.procs.values():
            if proc.alive and proc.pgid == pgid:
                if sig == signal.SIGINT and proc.ignore_sigint:
                    proc.received.append(sig)  # a straggler: SIGINT is not enough
                    continue
                proc.alive = False
                proc.exit_code = -sig
                proc.received.append(sig)
        return True

    def _getpriority(self, pid: int) -> int:
        if pid == 0:
            return self.supervisor_nice
        proc = self.procs.get(pid)
        return proc.nice if proc is not None else 0

    # -- test-facing surface (NOT part of the backend contract) ------------------

    def available(self) -> bool:
        # the production available() checks the real host; a stub is always
        # "present" (the fail-closed gate itself is tested separately)
        return True

    def snapshot(self) -> q.HostSnapshot:
        if self._snap_idx < len(self.snapshots):
            s = self.snapshots[self._snap_idx]
            self._snap_idx += 1
            return s
        return self.snapshots[-1] if self.snapshots else q.HostSnapshot(8.0, 10.0, 1.0)

    def read_exit_receipt(self, receipt_path: Path) -> int | None:
        return self.receipts.get(str(receipt_path))

    def parse_run_log(
        self, log_path: Path, require_result: bool = False
    ) -> tuple[str | None, bool]:
        return q.parse_run_log_text(self.logs.get(str(log_path), ""), require_result)

    def cleanup_finished_unit(self, state: q.QueueState, job: q.JobSpec, unit: str) -> None:
        ok, _why = self.validate_ownership(state, job, unit)
        if ok:
            self.cleaned.append(unit)

    def finish(self, handle: str, exit_code: int = 0, log: str = "") -> None:
        """Simulate the wrapper finishing: leader exits, receipt + log written."""
        pid = int(handle.removeprefix("pgid-"))
        proc = self.procs[pid]
        proc.alive = False
        proc.exit_code = exit_code
        env = self.envs_by_pid[pid]
        self.receipts[env["ACIQ_RECEIPT"]] = exit_code
        # a well-formed stream-json result event unless the test overrides it
        self.logs.setdefault(env["ACIQ_LOG"], log or '{"type":"result","subtype":"success"}\n')

    def proc_for(self, handle: str) -> FakeProc:
        return self.procs[int(handle.removeprefix("pgid-"))]

    def add_foreign(self, pid: int, pgid: int, cmd: str) -> FakeProc:
        """A process we do NOT own (e.g. a reused pid after our leader died)."""
        proc = FakeProc(pid=pid, pgid=pgid, cmd=cmd, nice=0, lstart="Mon Jan  1 00:00:00 2018")
        self.procs[pid] = proc
        return proc


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def snap(mem: float = 8.0, cpu: float = 10.0, load: float = 1.0) -> q.HostSnapshot:
    return q.HostSnapshot(mem, cpu, load)


def job_raw(jid: str, tmp_path: Path, **kw: Any) -> dict[str, Any]:
    return {"id": jid, "cwd": str(tmp_path), **kw}


def make_darwin_queue(
    tmp_path: Path,
    jobs: list[dict[str, Any]],
    host: StubDarwinHost | None = None,
    clock: FakeClock | None = None,
) -> tuple[q.WorkerQueue, q.QueueState, StubDarwinHost, FakeClock]:
    specs = [q.JobSpec.from_manifest(j) for j in jobs]
    state_path = tmp_path / "state.json"
    state = q.QueueState(state_path)
    state.load()
    for s in specs:
        state.jobs[s.id] = q.JobState(status="queued")
        state.job_digests[s.id] = s.digest()
    host = host or StubDarwinHost()
    clock = clock or FakeClock()
    queue = q.WorkerQueue(
        state, specs, tmp_path / "logs", host_ctl=host, sleep_fn=clock.sleep, clock=clock.time
    )
    return queue, state, host, clock


# --- backend selection ----------------------------------------------------------------


def test_select_backend_auto_by_platform(monkeypatch):
    monkeypatch.setattr(q.platform, "system", lambda: "Darwin")
    assert q.select_backend(None) == "darwin"
    monkeypatch.setattr(q.platform, "system", lambda: "Linux")
    assert q.select_backend(None) == "systemd"
    # an explicit flag always wins
    monkeypatch.setattr(q.platform, "system", lambda: "Darwin")
    assert q.select_backend("systemd") == "systemd"
    monkeypatch.setattr(q.platform, "system", lambda: "Linux")
    assert q.select_backend("darwin") == "darwin"


def test_select_backend_rejects_unknown_flag():
    with pytest.raises(q.QueueError, match="unknown backend"):
        q.select_backend("launchd")


def test_make_host_controls_returns_the_selected_backend():
    assert isinstance(q.make_host_controls("systemd"), q.HostControls)
    assert isinstance(q.make_host_controls("darwin"), q.DarwinHostControls)


# --- darwin host-sampling parsers ------------------------------------------------------


VM_STAT_SAMPLE = """Mach Virtual Memory Statistics: (page size of 4096 bytes)
Pages free:                               2673599.
Pages active:                             2466774.
Pages inactive:                           1683899.
Pages speculative:                         779657.
Pages throttled:                                 0.
Pages wired down:                          784054.
Pages purgeable:                          195420.
"""


def test_parse_vm_stat_sums_free_inactive_speculative():
    page_size, available = q.parse_vm_stat(VM_STAT_SAMPLE)
    assert page_size == 4096
    assert available == (2673599 + 1683899 + 779657) * 4096


def test_parse_vm_stat_fails_closed_on_missing_fields():
    with pytest.raises(q.QueueError, match="vm_stat"):
        q.parse_vm_stat("Mach Virtual Memory Statistics: (page size of 4096 bytes)\n")
    with pytest.raises(q.QueueError, match="vm_stat"):
        q.parse_vm_stat("no page size header at all\nPages free: 1.\n")


def test_parse_loadavg_sysctl():
    assert q.parse_loadavg_sysctl("{ 3.75 3.01 3.83 }") == 3.75
    assert q.parse_loadavg_sysctl("{ 0.50 0.60 0.70 }") == 0.5
    with pytest.raises(q.QueueError, match="unparsable"):
        q.parse_loadavg_sysctl("{ }")


def test_parse_ps_cpu_pct_normalizes_by_cores_and_caps():
    text = "  0.1\n 45.5\n 200.0\n 12.3\n"
    # sum = 257.9 over 8 cores -> 32.2%
    assert q.parse_ps_cpu_pct(text, 8) == pytest.approx(32.2375, abs=1e-3)
    # a fully busy 8-core host sums to ~800% -> capped at 100
    assert q.parse_ps_cpu_pct("800.0\n", 8) == 100.0
    # junk lines are skipped, never a sampling error
    assert q.parse_ps_cpu_pct("  1.0\n%CPU\n  3.0\n", 1) == pytest.approx(4.0)


def test_parse_ps_rss_sum_kib():
    assert q.parse_ps_rss_sum_kib("  1020\n   588\n") == 1608
    assert q.parse_ps_rss_sum_kib("") == 0  # group gone
    assert q.parse_ps_rss_sum_kib("  10\nRSS\n   5\n") == 15


def test_darwin_host_snapshot_wires_the_parsers(monkeypatch):
    def fake_run_checked(cmd: list[str]) -> str:
        if cmd[0] == "vm_stat":
            return VM_STAT_SAMPLE
        if cmd[0] == "ps":
            return "  0.1\n 45.5\n 200.0\n 12.3\n"
        if cmd[0] == "sysctl":
            return "{ 3.75 3.01 3.83 }"
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(q, "_run_checked", fake_run_checked)
    monkeypatch.setattr(q.os, "cpu_count", lambda: 8)
    s = q.darwin_host_snapshot()
    assert s.mem_available_gib == pytest.approx(
        (2673599 + 1683899 + 779657) * 4096 / (1024**3), abs=1e-3
    )
    assert s.cpu_busy_pct == pytest.approx(32.2375, abs=1e-3)
    assert s.load1 == 3.75


def test_darwin_sampler_fails_closed_when_a_tool_is_missing(monkeypatch):
    def boom(cmd: list[str]) -> str:
        raise q.QueueError("vm_stat failed: no such file")

    monkeypatch.setattr(q, "_run_checked", boom)
    with pytest.raises(q.QueueError, match="vm_stat failed"):
        q.darwin_mem_available_gib()


# --- worker environment ----------------------------------------------------------------


def test_darwin_worker_env_minimal_base_with_job_vars_on_top(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/Users/tester")
    monkeypatch.setenv("SECRET_TOKEN", "s3cret")
    env = q.darwin_worker_env({"PWD": "/job/cwd", "OMP_NUM_THREADS": "1"})
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/Users/tester"
    assert env["PWD"] == "/job/cwd"  # the job's explicit vars win
    assert env["OMP_NUM_THREADS"] == "1"
    assert "SECRET_TOKEN" not in env  # only the defined base is inherited


# --- start / verify --------------------------------------------------------------------


def test_start_records_identity_and_spawns_own_group_under_nice(tmp_path):
    host = StubDarwinHost()
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path, kind="opencode"))
    state.jobs["a"] = q.JobState(status="queued")
    handle = host.start_worker_unit(
        state,
        job,
        q.build_opencode_argv(job, "ses_" + "0" * 24),
        q.build_opencode_env(job),
        tmp_path / "a.log",
        tmp_path / "a.exit",
    )
    js = state.jobs["a"]
    pid = int(handle.removeprefix("pgid-"))
    proc = host.procs[pid]
    assert handle == f"pgid-{pid}"
    # the ownership record: pgid + leader pid + start time + command line
    assert js.pgid == pid and js.leader_pid == pid
    assert js.leader_start == proc.lstart
    assert js.leader_cmd == proc.cmd
    # own process group (start_new_session): pgid == leader pid
    assert proc.pgid == proc.pid
    # nice -n 10 applied (an increment on the supervisor's nice 0)
    assert proc.nice == 10
    # the SAME /bin/sh exit-receipt wrapper, nice-prefixed
    cmd = host.spawned_cmds[0]
    assert cmd[:6] == ["/usr/bin/nice", "-n", "10", "/bin/sh", "-c", q.WRAPPER_SH]
    assert cmd[6] == "worker"
    assert cmd[-1] == job.prompt if job.prompt else True
    # the worker env: defined base + job vars + the wrapper's file pointers
    env = host.spawned_envs[0]
    assert env["ACIQ_LOG"] == str(tmp_path / "a.log")
    assert env["ACIQ_RECEIPT"] == str(tmp_path / "a.exit")
    assert env["PWD"] == str(tmp_path)  # PWD exported (the CLI resolves from $PWD)
    assert host.spawned_cwds[0] == str(tmp_path)


def test_start_nice_increments_from_the_supervisors_nice_and_caps_at_20(tmp_path):
    # a supervisor already niced to 15 (as on the Mac worker host) -> the
    # worker lands at the 20 cap, never HIGHER priority than expected
    host = StubDarwinHost(supervisor_nice=15)
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path))
    state.jobs["a"] = q.JobState(status="queued")
    handle = host.start_worker_unit(
        state, job, ["/bin/sleep", "5"], {}, tmp_path / "l", tmp_path / "e"
    )
    proc = host.proc_for(handle)
    assert proc.nice == 20  # min(20, 15 + 10)
    ok, why = host.verify_unit_resource_limits(handle)
    assert ok, why


def test_verify_fails_closed_on_each_containment_property(tmp_path):
    host = StubDarwinHost()
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path))
    state.jobs["a"] = q.JobState(status="queued")
    handle = host.start_worker_unit(
        state, job, ["/bin/sleep", "5"], {}, tmp_path / "l", tmp_path / "e"
    )
    assert host.verify_unit_resource_limits(handle) == (True, "ok")
    # not a group leader (start_new_session silently missing)
    host.proc_for(handle).pgid = 99999
    ok, why = host.verify_unit_resource_limits(handle)
    assert not ok and "not a process-group leader" in why
    host.proc_for(handle).pgid = int(handle.removeprefix("pgid-"))
    # nice not applied (the worker outranks its expectation)
    host.proc_for(handle).nice = 0
    ok, why = host.verify_unit_resource_limits(handle)
    assert not ok and "nice not applied" in why
    host.proc_for(handle).nice = 10
    # the leader exited during verification
    host.proc_for(handle).alive = False
    ok, why = host.verify_unit_resource_limits(handle)
    assert not ok and "exited during start verification" in why
    # a malformed handle can never be verified
    assert host.verify_unit_resource_limits("not-a-handle")[0] is False


def test_start_fails_closed_without_job_state(tmp_path):
    host = StubDarwinHost()
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("ghost", tmp_path))
    with pytest.raises(q.QueueError, match="no state"):
        host.start_worker_unit(state, job, [], {}, tmp_path / "l", tmp_path / "e")


# --- ownership (the four recorded identity points) -------------------------------------


def start_one(tmp_path: Path, host: StubDarwinHost) -> tuple[q.QueueState, q.JobSpec, str]:
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path, kind="opencode"))
    state.jobs["a"] = q.JobState(status="queued")
    handle = host.start_worker_unit(
        state,
        job,
        q.build_opencode_argv(job, "ses_" + "0" * 24),
        q.build_opencode_env(job),
        tmp_path / "a.log",
        tmp_path / "a.exit",
    )
    # the scheduler persists the handle as the job's unit_name after spawn
    state.jobs["a"].unit_name = handle
    return state, job, handle


def test_validate_ownership_accepts_the_recorded_worker(tmp_path):
    host = StubDarwinHost()
    state, job, handle = start_one(tmp_path, host)
    ok, why = host.validate_ownership(state, job, handle)
    assert ok, why


def test_validate_ownership_rejects_each_mismatch(tmp_path):
    host = StubDarwinHost()
    state, job, handle = start_one(tmp_path, host)
    js = state.jobs["a"]
    # a handle that is not the job's recorded handle
    ok, why = host.validate_ownership(state, job, "pgid-1")
    assert not ok and "not job a's recorded" in why
    # incomplete recorded identity
    js.pgid = None
    ok, why = host.validate_ownership(state, job, handle)
    assert not ok and "no complete recorded worker identity" in why
    js.pgid = js.leader_pid
    # the leader is gone
    host.proc_for(handle).alive = False
    ok, why = host.validate_ownership(state, job, handle)
    assert not ok and "is gone" in why
    host.proc_for(handle).alive = True
    # the leader left the recorded process group
    host.proc_for(handle).pgid = 4242
    ok, why = host.validate_ownership(state, job, handle)
    assert not ok and "not in the recorded process group" in why
    host.proc_for(handle).pgid = js.pgid
    # the leader start time changed (pid reuse): a reused pid is NOT ours
    host.proc_for(handle).lstart = "Mon Jan  1 00:00:00 2018"
    ok, why = host.validate_ownership(state, job, handle)
    assert not ok and "start time changed" in why
    host.proc_for(handle).lstart = js.leader_start or ""
    # the command line changed
    host.proc_for(handle).cmd = "/bin/something-else entirely"
    ok, why = host.validate_ownership(state, job, handle)
    assert not ok and "command line changed" in why
    # the command line matches the record but carries the WRONG binary
    js.leader_cmd = "/some/other/binary --not-our-cli"
    host.proc_for(handle).cmd = js.leader_cmd or ""
    ok, why = host.validate_ownership(state, job, handle)
    assert not ok and "does not contain" in why


def test_validate_ownership_from_persisted_state_after_restart(tmp_path):
    """ADOPTED workers (previous supervisor) are validated from the PERSISTED
    identity alone — a fresh backend with no in-memory spawn record."""
    host = StubDarwinHost()
    state, job, handle = start_one(tmp_path, host)
    state.save()
    # a brand-new supervisor process: same state file, empty backend memory
    state2 = q.QueueState(tmp_path / "s.json")
    state2.load()
    host2 = StubDarwinHost()
    host2.procs = host.procs  # the worker is genuinely still running on the host
    ok, why = host2.validate_ownership(state2, job, handle)
    assert ok, why
    # and its liveness is answerable via ps (the adopted-worker path)
    assert host2.unit_active(handle) is True


def test_unit_active_adopted_worker_via_ps_and_direct_child_via_poll(tmp_path):
    host = StubDarwinHost()
    state, job, handle = start_one(tmp_path, host)
    assert host.unit_active(handle) is True  # direct child: poll()
    host.proc_for(handle).alive = False
    assert host.unit_active(handle) is False  # reaped/dead
    # adopted (no Popen in this backend): leader-alive via ps
    host2 = StubDarwinHost()
    host2.procs = host.procs
    assert host2.unit_active(handle) is False
    host.proc_for(handle).alive = True
    assert host2.unit_active(handle) is True
    assert host2.unit_active("garbage") is False


# --- signals ----------------------------------------------------------------------------


def test_signals_reach_the_whole_group_by_numeric_pgid(tmp_path):
    host = StubDarwinHost()
    state, job, handle = start_one(tmp_path, host)
    pgid = int(handle.removeprefix("pgid-"))
    # a second process in the worker's group (e.g. a CLI subprocess) that
    # IGNORES SIGINT — the straggler the SIGKILL phase exists for
    child = FakeProc(pid=host.next_pid, pgid=pgid, cmd="/bin/sleep 30", nice=10)
    child.ignore_sigint = True
    host.procs[child.pid] = child
    assert host.send_sigint_to_unit(handle) is True
    assert host.stop_worker_unit(handle) is True
    # BOTH group members were signaled — by numeric pgid, never by name
    assert (pgid, signal.SIGINT) in host.kills
    assert (pgid, signal.SIGKILL) in host.kills
    assert signal.SIGINT in host.proc_for(handle).received
    assert signal.SIGINT in child.received
    assert signal.SIGKILL in child.received  # the straggler died to the group kill
    assert not child.alive


def test_kill_of_an_empty_group_is_success_not_an_error(tmp_path):
    host = StubDarwinHost()
    # the group already exited: the interrupt goal is met (ProcessLookupError)
    assert host._kill(999999, signal.SIGINT) is True


def test_malformed_handle_is_never_signaled(tmp_path):
    host = StubDarwinHost()
    assert host.send_sigint_to_unit("kill-everything") is False
    assert host.stop_worker_unit("") is False
    assert host.kills == []


def test_non_positive_pgid_is_refused_even_from_a_corrupted_state(tmp_path):
    """kill(-0) would signal THIS supervisor's own process group — a corrupted
    state file (pgid: 0) must be refused, never acted on."""
    host = StubDarwinHost()
    state, job, handle = start_one(tmp_path, host)
    js = state.jobs["a"]
    js.pgid = 0
    js.leader_pid = 0
    js.unit_name = "pgid-0"
    ok, why = host.validate_ownership(state, job, "pgid-0")
    assert not ok and "no complete recorded worker identity" in why
    assert host.send_sigint_to_unit("pgid-0") is False
    assert host.stop_worker_unit("pgid-0") is False
    assert host.kills == []
    assert host.poll_worker_limits(job, js) is None


def test_start_kills_the_worker_if_identity_cannot_be_read(tmp_path):
    """A worker we cannot IDENTIFY must never keep running unowned: if the
    post-spawn ps reads fail, the group is killed before the error surfaces."""

    class ExplodingPs(StubDarwinHost):
        def __init__(self) -> None:
            super().__init__()
            self.boom = False

        def _ps(self, args: list[str]) -> str:
            if self.boom:
                raise q.QueueError("ps failed: simulated")
            return super()._ps(args)

    host = ExplodingPs()
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path))
    state.jobs["a"] = q.JobState(status="queued")
    host.boom = True  # the identity reads explode right after the spawn
    with pytest.raises(q.QueueError, match="ps failed"):
        host.start_worker_unit(state, job, ["/bin/sleep", "5"], {}, tmp_path / "l", tmp_path / "e")
    # the spawned group was killed, not leaked
    pgid = host.next_pid - 1
    assert (pgid, signal.SIGKILL) in host.kills
    assert not host.procs[pgid].alive


# --- per-worker memory cap (polled RSS) --------------------------------------------------


def test_poll_worker_limits_enforces_the_memory_cap(tmp_path):
    host = StubDarwinHost()
    state, job, handle = start_one(tmp_path, host)
    js = state.jobs["a"]
    assert host.poll_worker_limits(job, js) is None  # 1 MiB default: fine
    host.proc_for(handle).rss_kib = 3 * 1024 * 1024  # 3 GiB > the 2 GiB cap
    violation = host.poll_worker_limits(job, js)
    assert violation is not None and "memory cap exceeded" in violation
    assert str(q.DARWIN_MEM_CAP_BYTES // (1024 * 1024)) in violation  # the cap is stated
    # a group that vanished mid-poll is NOT a violation (finalize handles it)
    host.proc_for(handle).alive = False
    assert host.poll_worker_limits(job, js) is None


def test_poll_worker_limits_ignores_incomplete_identity(tmp_path):
    host = StubDarwinHost()
    state, job, handle = start_one(tmp_path, host)
    js = state.jobs["a"]
    js.pgid = None
    assert host.poll_worker_limits(job, js) is None


# --- scheduler integration (the full queue on the darwin backend) -----------------------


def test_darwin_queue_runs_job_to_finished_needs_review(tmp_path):
    host = StubDarwinHost()
    queue, state, host, clock = make_darwin_queue(
        tmp_path, [job_raw("a", tmp_path, kind="opencode")], host
    )
    # admit (the worker stays ALIVE through start verification), then let it
    # finish on its own: the poll loop must observe the exit and finalize
    # from the durable receipt.
    queue._try_admit()
    handle = state.jobs["a"].unit_name or ""
    assert handle.startswith("pgid-")
    host.finish(handle, exit_code=0)
    assert queue.run() == 0
    js = state.jobs["a"]
    assert js.status == "finished-needs-review"  # exit 0 is NEVER verified
    assert js.exit_code == 0
    assert js.unit_name and js.unit_name.startswith("pgid-")
    assert js.pgid is not None and js.leader_pid == js.pgid
    assert js.leader_start and js.leader_cmd


def test_darwin_queue_fail_closed_when_backend_unavailable(tmp_path):
    host = StubDarwinHost()
    queue, state, host, clock = make_darwin_queue(tmp_path, [job_raw("a", tmp_path)], host)
    host.available = lambda: False  # type: ignore[method-assign]
    with pytest.raises(q.QueueError, match="FAIL CLOSED"):
        queue.run()


def test_darwin_queue_memory_cap_interrupts_the_worker(tmp_path):
    """The polled memory cap: over cap -> SIGINT the group -> grace -> SIGKILL
    the stragglers -> the job is honestly interrupted (resumable)."""
    clock = FakeClock()
    host = StubDarwinHost()
    queue, state, host, clock = make_darwin_queue(
        tmp_path, [job_raw("a", tmp_path, kind="opencode")], host, clock
    )
    orig_start = host.start_worker_unit

    def start_and_bloat(*a: Any, **kw: Any) -> str:
        handle = orig_start(*a, **kw)
        host.proc_for(handle).rss_kib = 3 * 1024 * 1024  # 3 GiB > 2 GiB cap
        return handle

    host.start_worker_unit = start_and_bloat  # type: ignore[method-assign]
    rc = queue.run()
    assert rc == 0  # every job reached a terminal state
    js = state.jobs["a"]
    assert js.status == "interrupted"
    assert "memory cap exceeded" in (js.error or "")
    assert js.session_id  # resumable
    # the group was SIGINTed first, then SIGKILLed (graceful interrupt)
    pgid = int(js.unit_name.removeprefix("pgid-")) if js.unit_name else -1
    assert (pgid, signal.SIGINT) in host.kills
    assert (pgid, signal.SIGKILL) in host.kills
    assert host.kills.index((pgid, signal.SIGINT)) < host.kills.index((pgid, signal.SIGKILL))


def test_darwin_queue_timeout_interrupts_gracefully(tmp_path):
    clock = FakeClock()
    host = StubDarwinHost()
    queue, state, host, clock = make_darwin_queue(
        tmp_path, [job_raw("a", tmp_path, kind="opencode", timeout_s=10)], host, clock
    )
    rc = queue.run()  # the fake worker never exits on its own
    assert rc == 0
    js = state.jobs["a"]
    assert js.status == "interrupted"
    assert "timeout" in (js.error or "")
    pgid = int(js.unit_name.removeprefix("pgid-")) if js.unit_name else -1
    assert (pgid, signal.SIGINT) in host.kills
    assert (pgid, signal.SIGKILL) in host.kills


def test_darwin_queue_pressure_defers_and_exits_75(tmp_path):
    """Pressure deferral semantics are IDENTICAL to Linux: sustained low
    memory -> SIGINT own workers first, ONE shared grace window, stop the
    stragglers, defer the queued work, exit 75."""
    clock = FakeClock()
    host = StubDarwinHost(snapshots=[snap(mem=8.0)] * 2 + [snap(mem=2.0)] * 30)
    queue, state, host, clock = make_darwin_queue(
        tmp_path, [job_raw("a", tmp_path), job_raw("b", tmp_path)], host, clock
    )
    # the running worker IGNORES SIGINT (a straggler): the full three-phase
    # path must run — SIGINT, ONE shared grace window, then SIGKILL.
    orig_start = host.start_worker_unit

    def start_as_straggler(*a: Any, **kw: Any) -> str:
        handle = orig_start(*a, **kw)
        host.proc_for(handle).ignore_sigint = True
        return handle

    host.start_worker_unit = start_as_straggler  # type: ignore[method-assign]
    rc = queue.run()
    assert rc == q.EXIT_DEFERRED == 75
    assert state.pressure_deferred
    assert state.jobs["a"].status == "interrupted"  # the running worker
    assert state.jobs["b"].status == "deferred"  # the queued work
    assert state.jobs["a"].session_id  # session preserved for resume
    pgid = int(state.jobs["a"].unit_name.removeprefix("pgid-"))  # type: ignore[union-attr]
    assert (pgid, signal.SIGINT) in host.kills  # SIGINT first...
    assert (pgid, signal.SIGKILL) in host.kills  # ...then the straggler stop
    assert host.kills.index((pgid, signal.SIGINT)) < host.kills.index((pgid, signal.SIGKILL))


def test_darwin_queue_admission_refusal_defers_not_fails(tmp_path):
    host = StubDarwinHost(snapshots=[snap(mem=1.0)])
    queue, state, host, clock = make_darwin_queue(tmp_path, [job_raw("a", tmp_path)], host)
    assert queue.run() == q.EXIT_DEFERRED == 75
    js = state.jobs["a"]
    assert js.status == "deferred"
    assert "MemAvailable" in (js.error or "")
    assert host.kills == []  # nothing was ever spawned or signaled


def test_darwin_queue_never_signals_without_ownership(tmp_path):
    """A tampered identity (command line changed under us) must NEVER be
    signaled: the timeout path fails the job honestly instead."""
    clock = FakeClock()
    host = StubDarwinHost()
    queue, state, host, clock = make_darwin_queue(
        tmp_path, [job_raw("a", tmp_path, kind="opencode", timeout_s=10)], host, clock
    )
    queue._try_admit()
    js = state.jobs["a"]
    # the live process is no longer what we recorded (e.g. exec replaced it)
    host.proc_for(js.unit_name or "").cmd = "/usr/sbin/something-else --tampered"
    queue._interrupt_job(queue.jobs["a"], js, js.unit_name or "", reason="timeout", graceful=True)
    assert js.status == "failed"
    assert "ownership" in (js.error or "").lower()
    assert host.kills == []  # NO signal left this supervisor


def test_darwin_queue_two_workers_and_dependency_gating(tmp_path):
    clock = FakeClock()
    host = StubDarwinHost()
    queue, state, host, clock = make_darwin_queue(
        tmp_path,
        [job_raw("a", tmp_path), job_raw("b", tmp_path), job_raw("c", tmp_path, depends_on=["a"])],
        host,
        clock,
    )
    queue._try_admit()
    assert set(queue._active) == {"a", "b"}  # max 2 concurrent, as on Linux
    assert state.jobs["c"].status == "queued"  # the dependent waits
    host.finish(state.jobs["a"].unit_name or "")
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], state.jobs["a"].unit_name or "")
    queue._try_admit()
    assert set(queue._active) == {"b", "c"}  # c admitted once a finished


def test_darwin_state_roundtrips_the_worker_identity(tmp_path):
    host = StubDarwinHost()
    state, job, handle = start_one(tmp_path, host)
    state.save()
    state2 = q.QueueState(tmp_path / "s.json")
    state2.load()
    js = state2.jobs["a"]
    assert (js.pgid, js.leader_pid, js.leader_start, js.leader_cmd) == (
        state.jobs["a"].pgid,
        state.jobs["a"].leader_pid,
        state.jobs["a"].leader_start,
        state.jobs["a"].leader_cmd,
    )


def test_darwin_state_loads_old_schema_files_without_the_new_fields(tmp_path):
    """The identity fields are ADDITIVE to schema 2: an m2-era state file
    (no pgid/leader_*) still loads — the fields default to None."""
    state_path = tmp_path / "old.json"
    state_path.write_text(
        json.dumps(
            {
                "schema": 2,
                "unit_token": "0123456789abcdef",
                "pressure_deferred": False,
                "pressure_reason": None,
                "job_digests": {"a": "deadbeefdeadbeef"},
                "jobs": {"a": {"status": "queued", "attempts": 1}},
            }
        )
    )
    state = q.QueueState(state_path)
    state.load()
    js = state.jobs["a"]
    assert js.status == "queued"
    assert js.pgid is None and js.leader_pid is None
    assert js.leader_start is None and js.leader_cmd is None


# --- restart reconciliation ---------------------------------------------------------------


def test_darwin_reconcile_adopts_a_still_running_owned_group(tmp_path):
    host = StubDarwinHost()
    queue, state, host, clock = make_darwin_queue(tmp_path, [job_raw("a", tmp_path)], host)
    queue._try_admit()
    # a RESTART: fresh supervisor, same persisted state, the worker still runs
    state.save()
    state2 = q.QueueState(tmp_path / "state.json")
    state2.load()
    host2 = StubDarwinHost()
    host2.procs = host.procs  # the process is genuinely alive on the host
    queue2, *_ = make_darwin_queue(tmp_path, [job_raw("a", tmp_path)], host2)
    queue2.state = state2
    queue2._reconcile_previous_run()
    assert "a" in queue2._active, "a still-running OWNED group must be adopted"
    assert state2.jobs["a"].status == "running"


def test_darwin_reconcile_interrupts_when_the_leader_is_gone(tmp_path):
    host = StubDarwinHost()
    queue, state, host, clock = make_darwin_queue(tmp_path, [job_raw("a", tmp_path)], host)
    queue._try_admit()
    host.proc_for(state.jobs["a"].unit_name or "").alive = False  # died, no receipt
    state.save()
    state2 = q.QueueState(tmp_path / "state.json")
    state2.load()
    host2 = StubDarwinHost()
    host2.procs = host.procs
    queue2, *_ = make_darwin_queue(tmp_path, [job_raw("a", tmp_path)], host2)
    queue2.state = state2
    queue2._reconcile_previous_run()
    assert state2.jobs["a"].status == "interrupted"
    assert "restarted" in (state2.jobs["a"].error or "")


def test_darwin_reconcile_finalizes_from_the_durable_receipt(tmp_path):
    host = StubDarwinHost()
    queue, state, host, clock = make_darwin_queue(tmp_path, [job_raw("a", tmp_path)], host)
    queue._try_admit()
    # the worker finished while no supervisor was alive: receipt present
    host.finish(state.jobs["a"].unit_name or "", exit_code=0)
    state.save()
    state2 = q.QueueState(tmp_path / "state.json")
    state2.load()
    host2 = StubDarwinHost()
    host2.procs = host.procs
    host2.receipts = host.receipts
    host2.logs = host.logs
    queue2, *_ = make_darwin_queue(tmp_path, [job_raw("a", tmp_path)], host2)
    queue2.state = state2
    queue2._reconcile_previous_run()
    assert state2.jobs["a"].status == "finished-needs-review"
    assert state2.jobs["a"].exit_code == 0


def test_darwin_reconcile_refuses_a_reused_pid(tmp_path):
    """The leader died and its pid was REUSED by a foreign process: the
    group must NOT be adopted or signaled — the recorded start time and
    command line do not match, so the job is honestly interrupted."""
    host = StubDarwinHost()
    queue, state, host, clock = make_darwin_queue(tmp_path, [job_raw("a", tmp_path)], host)
    queue._try_admit()
    js = state.jobs["a"]
    pid = int((js.unit_name or "").removeprefix("pgid-"))
    host.proc_for(js.unit_name or "").alive = False  # our leader died...
    foreign = host.add_foreign(pid, pgid=pid, cmd="/usr/sbin/foreignd --not-ours")  # ...pid reused
    state.save()
    state2 = q.QueueState(tmp_path / "state.json")
    state2.load()
    host2 = StubDarwinHost()
    host2.procs = host.procs
    queue2, *_ = make_darwin_queue(tmp_path, [job_raw("a", tmp_path)], host2)
    queue2.state = state2
    queue2._reconcile_previous_run()
    assert state2.jobs["a"].status == "interrupted"  # NOT adopted
    assert "a" not in queue2._active
    assert host2.kills == []  # the foreign process was never touched
    assert foreign.alive  # ...and is still running, untouched


# --- the opencode argv on the mac (same argv, PWD exported) -------------------------------


def test_darwin_spawn_uses_the_same_opencode_argv_and_exports_pwd(tmp_path):
    host = StubDarwinHost()
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("oc", tmp_path, kind="opencode", prompt="do the work"))
    state.jobs["oc"] = q.JobState(status="queued")
    sid = q.new_opencode_session_id()
    host.start_worker_unit(
        state,
        job,
        q.build_opencode_argv(job, sid),
        q.build_opencode_env(job),
        tmp_path / "oc.log",
        tmp_path / "oc.exit",
    )
    cmd = host.spawned_cmds[0]
    # the SAME argv as Linux, verbatim behind the nice/sh/wrapper prefix
    tail = cmd[7:]
    assert tail == q.build_opencode_argv(job, sid)
    assert tail[0] == q.OPENCODE_BIN
    assert "run" in tail and "--standalone" in tail and "--auto" in tail
    assert f"--model={q.OPENCODE_MODEL_ID}" in tail
    assert "--format=json" in tail
    assert f"--session={sid}" in tail
    assert sid.startswith("ses_") and len(sid) == 4 + 24
    # PWD exported (the CLI resolves its project from $PWD, not getcwd())
    assert host.spawned_envs[0]["PWD"] == str(tmp_path)


# --- the process-name-matching ban still holds --------------------------------------------


def test_no_process_name_matching_primitives():
    source = _SCRIPT.read_text(encoding="utf-8")
    for banned in ("pkill", "killall", "os.killpg", "psutil", "pgrep"):
        assert banned not in source, f"banned process-control primitive present: {banned}"


# --- ONE opt-in real smoke test (ACIQ_REAL_SMOKE=1) ----------------------------------------


@pytest.mark.skipif(
    os.environ.get("ACIQ_REAL_SMOKE") != "1",
    reason="opt-in real smoke test — set ACIQ_REAL_SMOKE=1 on macOS",
)
def test_real_darwin_smoke_sleep_through_the_backend(tmp_path, monkeypatch):
    """Real processes through the REAL DarwinHostControls on a real macOS host:

    1. /bin/sleep 2 through the FULL queue (cmd_run): the wrapper writes a
       durable exit receipt 0 -> finished-needs-review (never verified);
    2. /bin/sleep 30 killed by the wall-clock timeout: SIGINT the group ->
       grace -> SIGKILL the stragglers -> interrupted, and the process group
       is REALLY gone afterwards.
    """
    assert q.select_backend(None) == "darwin"  # this test only makes sense on a Mac
    monkeypatch.setattr(q, "OPENCODE_BIN", "/bin/sleep")
    monkeypatch.setattr(q, "build_opencode_argv", lambda job, sid: ["/bin/sleep", "2"])
    # Admission is forced open: the gate is HOST-LOAD-DEPENDENT (this Mac runs
    # other workers; a busy host honestly defers) and unit-tested elsewhere —
    # this smoke tests the darwin BACKEND mechanics with real processes.
    monkeypatch.setattr(
        q, "evaluate_admission", lambda running, snap: q.AdmissionDecision(True, "smoke", snap)
    )

    # phase 1: a clean full-queue run with a real process
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"jobs": [job_raw("smoke-true", tmp_path, kind="opencode")]}))
    args = type(
        "Args",
        (),
        {
            "manifest": str(manifest),
            "state": str(tmp_path / "st.json"),
            "dry_run": False,
            "backend": "darwin",
            "max_workers": None,
            "admit_gib": None,
            "pressure_gib": None,
        },
    )()
    rc = q.cmd_run(args)
    assert rc == 0
    state = q.QueueState(tmp_path / "st.json")
    state.load()
    js = state.jobs["smoke-true"]
    assert js.status == "finished-needs-review"
    assert js.exit_code == 0
    assert js.pgid is not None and js.leader_pid == js.pgid
    assert "/bin/sleep" in (js.leader_cmd or "")
    assert (tmp_path / "queue-logs" / "smoke-true.exit").read_text().strip() == "0"
    assert (tmp_path / "queue-logs" / "smoke-true.attempt-1.log").exists()

    # phase 2: the wall-clock timeout kills a real long-running group
    monkeypatch.setattr(q, "build_opencode_argv", lambda job, sid: ["/bin/sleep", "30"])
    state2_path = tmp_path / "st2.json"
    state2 = q.QueueState(state2_path)
    state2.load()
    job = q.JobSpec.from_manifest(
        job_raw("smoke-sleep", tmp_path, kind="opencode", timeout_s=3, prompt="sleep")
    )
    state2.jobs["smoke-sleep"] = q.JobState(status="queued")
    state2.job_digests["smoke-sleep"] = job.digest()
    host = q.DarwinHostControls()
    queue = q.WorkerQueue(state2, [job], tmp_path / "logs2", host_ctl=host)
    rc = queue.run()
    assert rc == 0  # every job reached a terminal state
    js2 = state2.jobs["smoke-sleep"]
    assert js2.status == "interrupted"
    assert "timeout" in (js2.error or "")
    assert js2.pgid is not None
    # the process group is REALLY gone (no leaked sleep, no zombie leader)
    r = subprocess.run(
        ["ps", "-o", "pid=", "-g", str(js2.pgid)], capture_output=True, text=True, timeout=10
    )
    assert r.stdout.strip() == "", f"process group {js2.pgid} still has members: {r.stdout!r}"
    # the wrapper was SIGINT-killed before it could write a receipt
    assert not (tmp_path / "logs2" / "smoke-sleep.exit").exists()
