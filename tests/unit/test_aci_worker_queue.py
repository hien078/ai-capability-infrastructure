"""Deterministic unit tests for scripts/aci_worker_queue.py.

All host interaction (systemd, /proc sampling) is mocked via FakeHostControls
and all time is virtual (FakeClock) — no test waits in real time and no test
touches a real systemd unit, process, or the network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
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


class FakeClock:
    """Virtual clock: advance() moves time; sleep() never waits."""

    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeUnit:
    def __init__(self, unit: str, cwd: str) -> None:
        self.unit = unit
        self.cwd = cwd
        self.active = True
        self.exit_code: int | None = None
        self.receipt_written = False
        self.sigint_count = 0
        self.stopped = False


class FakeHostControls:
    """Mock host: canned snapshots, fake unit lifecycle, recorded calls."""

    def __init__(
        self,
        *,
        snapshots: list[q.HostSnapshot] | None = None,
        systemd: bool = True,
        limits_ok: bool = True,
        ownership_ok: bool = True,
    ) -> None:
        self.snapshots = list(snapshots or [])
        self._snap_idx = 0
        self.systemd_ok = systemd
        self.limits_ok = limits_ok
        self.ownership_ok = ownership_ok
        #: per-unit ownership-validation overrides (unit name -> fail)
        self.ownership_fail_units: set[str] = set()
        self.units: dict[str, FakeUnit] = {}
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.sigints: list[str] = []
        self.cleaned: list[str] = []
        self.receipts: dict[str, int] = {}
        self.logs: dict[str, str] = {}
        self.descriptions: dict[str, str] = {}
        self.argvs: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.log_paths: list[Path] = []

    def snapshot(self) -> q.HostSnapshot:
        if self._snap_idx < len(self.snapshots):
            s = self.snapshots[self._snap_idx]
            self._snap_idx += 1
            return s
        return self.snapshots[-1] if self.snapshots else q.HostSnapshot(8.0, 10.0, 1.0)

    def systemd_available(self) -> bool:
        return self.systemd_ok

    def available(self) -> bool:
        # the scheduler's generic fail-closed gate (backend seam)
        return self.systemd_ok

    def poll_worker_limits(self, job: q.JobSpec, js: q.JobState) -> str | None:
        # systemd enforces limits in-kernel: nothing to poll (backend seam)
        return None

    def verify_unit_resource_limits(self, unit: str) -> tuple[bool, str]:
        if not self.limits_ok:
            return False, "MemoryHigh=0 != expected 1610612736"
        return True, "ok"

    def validate_ownership(
        self, state: q.QueueState, job: q.JobSpec, unit: str
    ) -> tuple[bool, str]:
        if unit in self.ownership_fail_units:
            return False, "Description mismatch (simulated per-unit)"
        if not self.ownership_ok:
            return False, "Description mismatch (simulated)"
        expected = state.unit_for(job)
        if unit != expected or state.unit_token not in unit:
            return False, f"unit {unit} is not this run's unit {expected}"
        u = self.units.get(unit)
        if u is None or u.cwd != job.cwd:
            return False, "WorkingDirectory mismatch"
        return True, "ok"

    def start_worker_unit(
        self,
        state: q.QueueState,
        job: q.JobSpec,
        argv: list[str],
        env: dict[str, str],
        log_path: Path,
        receipt_path: Path,
    ) -> str:
        unit = state.unit_for(job)
        self.started.append(unit)
        self.argvs.append(list(argv))
        self.envs.append(dict(env))
        self.log_paths.append(Path(log_path))
        self.units[unit] = FakeUnit(unit, job.cwd)
        self.descriptions[unit] = state.description_for(job)
        # simulate the wrapper writing the receipt when the "process" finishes
        self.receipts[str(receipt_path)] = 0
        # and a well-formed stream-json result event (tests override via set_log)
        self.logs.setdefault(str(log_path), '{"type":"result","subtype":"success"}\n')
        return unit

    def finish_unit(self, unit: str, exit_code: int = 0, log: str = "") -> None:
        u = self.units[unit]
        u.active = False
        u.exit_code = exit_code
        u.receipt_written = True
        if log:
            self._pending_log = log

    def set_log(self, log_path: Path, text: str) -> None:
        self.logs[str(log_path)] = text

    def set_receipt(self, receipt_path: Path, code: int | None) -> None:
        if code is None:
            self.receipts.pop(str(receipt_path), None)
        else:
            self.receipts[str(receipt_path)] = code

    def stop_worker_unit(self, unit: str) -> bool:
        self.stopped.append(unit)
        u = self.units.get(unit)
        if u:
            u.active = False
            u.stopped = True
        return True

    def send_sigint_to_unit(self, unit: str) -> bool:
        self.sigints.append(unit)
        u = self.units.get(unit)
        if u:
            u.sigint_count += 1
        return True

    def unit_active(self, unit: str) -> bool:
        u = self.units.get(unit)
        return bool(u and u.active)

    def cleanup_finished_unit(self, state: q.QueueState, job: q.JobSpec, unit: str) -> None:
        # mirrors the real contract: reset-failed is ownership-gated
        ok, _why = self.validate_ownership(state, job, unit)
        if ok:
            self.cleaned.append(unit)

    def read_exit_receipt(self, receipt_path: Path) -> int | None:
        return self.receipts.get(str(receipt_path))

    def parse_run_log(
        self, log_path: Path, require_result: bool = False
    ) -> tuple[str | None, bool]:
        # same parser as production, over the fake's in-memory logs
        return q.parse_run_log_text(self.logs.get(str(log_path), ""), require_result)


def snap(mem: float = 8.0, cpu: float = 10.0, load: float = 1.0) -> q.HostSnapshot:
    return q.HostSnapshot(mem, cpu, load)


def make_queue(
    tmp_path: Path,
    jobs: list[dict[str, Any]],
    host: FakeHostControls | None = None,
    clock: FakeClock | None = None,
    **kw: Any,
) -> tuple[q.WorkerQueue, q.QueueState, FakeHostControls, FakeClock]:
    specs = [q.JobSpec.from_manifest(j) for j in jobs]
    state_path = tmp_path / "state.json"
    state = q.QueueState(state_path)
    state.load()
    for s in specs:
        state.jobs[s.id] = q.JobState(status="queued")
        state.job_digests[s.id] = s.digest()
    host = host or FakeHostControls()
    clock = clock or FakeClock()
    queue = q.WorkerQueue(
        state, specs, tmp_path / "logs", host_ctl=host, sleep_fn=clock.sleep, clock=clock.time
    )
    return queue, state, host, clock


def job_raw(jid: str, tmp_path: Path, **kw: Any) -> dict[str, Any]:
    return {"id": jid, "cwd": str(tmp_path), **kw}


# --- admission / pressure ------------------------------------------------------------------------


def test_admission_first_worker_floor():
    d = q.evaluate_admission(0, snap(mem=3.0))
    assert not d.admitted
    assert "3.5" in d.reason


def test_admission_additional_worker_floor():
    d = q.evaluate_admission(1, snap(mem=4.0))
    assert not d.admitted
    assert "5" in d.reason


def test_admission_cpu_and_load_gates():
    assert not q.evaluate_admission(0, snap(cpu=80.0)).admitted
    assert not q.evaluate_admission(0, snap(load=11.0)).admitted
    assert q.evaluate_admission(0, snap()).admitted


def test_admission_max_concurrent():
    d = q.evaluate_admission(2, snap())
    assert not d.admitted
    assert "max concurrent" in d.reason


def test_pressure_requires_all_samples():
    assert not q.evaluate_pressure([snap(mem=2.0), snap(mem=2.0), snap(mem=8.0)])[0]
    pressured, reason = q.evaluate_pressure([snap(mem=2.0)] * 3)
    assert pressured and "MemAvailable" in reason


def test_pressure_cpu_and_load_axes():
    assert q.evaluate_pressure([snap(cpu=90.0)] * 3)[0]
    assert q.evaluate_pressure([snap(load=13.0)] * 3)[0]


def test_pressure_needs_three_samples():
    assert not q.evaluate_pressure([snap(mem=1.0)] * 2)[0]


# --- manifest validation -------------------------------------------------------------------------


def test_manifest_rejects_unknown_kind(tmp_path):
    with pytest.raises(q.QueueError, match="unknown kind"):
        q.JobSpec.from_manifest({"id": "a", "kind": "codex", "cwd": str(tmp_path)})


def test_manifest_rejects_missing_cwd(tmp_path):
    with pytest.raises(q.QueueError, match="cwd"):
        q.JobSpec.from_manifest({"id": "a", "cwd": str(tmp_path / "nope")})


def test_manifest_rejects_duplicate_ids(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"jobs": [job_raw("a", tmp_path), job_raw("a", tmp_path)]}))
    with pytest.raises(q.QueueError, match="duplicate"):
        q.load_manifest(p)


def test_manifest_rejects_cycle(tmp_path):
    queue, *_ = make_queue(
        tmp_path,
        [job_raw("a", tmp_path, depends_on=["b"]), job_raw("b", tmp_path, depends_on=["a"])],
    )
    with pytest.raises(q.QueueError, match="cycle"):
        queue._topological_order()


def test_state_bound_to_manifest_spec(tmp_path):
    state = q.QueueState(tmp_path / "state.json")
    state.load()
    jobs = [q.JobSpec.from_manifest(job_raw("a", tmp_path))]
    q.bind_state_to_manifest(state, jobs)
    # same manifest again: fine
    q.bind_state_to_manifest(state, jobs)
    # changed spec under the same id: fail closed
    changed = [q.JobSpec.from_manifest(job_raw("a", tmp_path, prompt="different"))]
    with pytest.raises(q.QueueError, match="spec changed"):
        q.bind_state_to_manifest(state, changed)


# --- scheduler: real parallelism -------------------------------------------------------


def test_two_independent_jobs_run_concurrently_dependent_waits(tmp_path):
    """The core scheduler test: TWO independent jobs active SIMULTANEOUSLY
    while a dependent job waits, then runs after its dependency is reviewed."""
    host = FakeHostControls()
    queue, state, host, clock = make_queue(
        tmp_path,
        [
            job_raw("a", tmp_path),
            job_raw("b", tmp_path),
            job_raw("c", tmp_path, depends_on=["a"]),
        ],
        host,
    )
    # drive the scheduler manually: first poll admits a and b (max 2)
    queue._try_admit()
    assert len(queue._active) == 2, "two independent jobs must be active simultaneously"
    assert "c" not in queue._active, "dependent must wait while a runs"
    # a finishes with exit 0 -> finished-needs-review (NOT verified)
    unit_a = state.jobs["a"].unit_name
    host.finish_unit(unit_a, exit_code=0)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit_a)
    assert state.jobs["a"].status == "finished-needs-review"
    # now c may be admitted (its dep is finished-needs-review = satisfied)
    queue._try_admit()
    assert "c" in queue._active
    # b still active alongside c (max 2 concurrent)
    assert len(queue._active) == 2


def test_max_two_workers_enforced(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw(f"j{i}", tmp_path) for i in range(4)])
    queue._try_admit()
    assert len(queue._active) == 2
    assert len(host.started) == 2
    # the other two stay queued
    assert state.jobs["j2"].status == "queued"
    assert state.jobs["j3"].status == "queued"


def test_scheduler_completes_all_jobs(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(
        tmp_path, [job_raw("a", tmp_path), job_raw("b", tmp_path)]
    )
    # units finish as soon as they start (the poll loop observes them inactive)
    orig_start = host.start_worker_unit

    def start_and_finish(*a: Any, **kw: Any) -> str:
        unit = orig_start(*a, **kw)
        host.finish_unit(unit)
        return unit

    host.start_worker_unit = start_and_finish  # type: ignore[method-assign]
    rc = queue.run()
    assert rc == 0
    assert all(js.status == "finished-needs-review" for js in state.jobs.values())


def test_scheduler_run_loop_terminates_when_done(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    # finish the unit as soon as it starts: the loop must exit, not hang
    orig_start = host.start_worker_unit

    def start_and_finish(*a: Any, **kw: Any) -> str:
        unit = orig_start(*a, **kw)
        host.finish_unit(unit)
        return unit

    host.start_worker_unit = start_and_finish  # type: ignore[method-assign]
    rc = queue.run()
    assert rc == 0
    assert state.jobs["a"].status == "finished-needs-review"


# --- dependency gating ---------------------------------------------------------------------------


def test_dependency_failed_blocks_and_defers_downstream(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(
        tmp_path,
        [job_raw("a", tmp_path), job_raw("b", tmp_path, depends_on=["a"])],
    )
    queue._try_admit()
    unit_a = state.jobs["a"].unit_name
    host.set_receipt(tmp_path / "logs" / "a.exit", 3)
    host.finish_unit(unit_a, exit_code=3)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit_a)
    assert state.jobs["a"].status == "failed"
    # b must NOT be admitted; the scheduler defers it (dep terminal non-satisfying)
    queue._try_admit()
    assert "b" not in queue._active
    assert state.jobs["b"].status == "deferred"
    assert "failed" in (state.jobs["b"].error or "")


def test_dependency_running_keeps_dependent_queued(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(
        tmp_path,
        [job_raw("a", tmp_path), job_raw("b", tmp_path, depends_on=["a"])],
    )
    queue._try_admit()
    assert "a" in queue._active
    # b stays queued while a runs (NOT deferred — a may still succeed)
    queue._try_admit()
    assert state.jobs["b"].status == "queued"


def test_dependency_unknown_id_rejected(tmp_path):
    queue, *_ = make_queue(tmp_path, [job_raw("b", tmp_path, depends_on=["ghost"])])
    ok, reason = queue._deps_satisfied(queue.jobs["b"])
    assert not ok and "unknown dependency" in reason


# --- exit code semantics -------------------------------------------------------------------------


def test_exit_zero_is_needs_review_not_verified(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue._try_admit()
    unit = state.jobs["a"].unit_name
    host.finish_unit(unit, exit_code=0)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit)
    js = state.jobs["a"]
    assert js.status == "finished-needs-review"
    assert js.exit_code == 0


def test_nonzero_exit_fails_and_never_unblocks_dependents(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(
        tmp_path,
        [job_raw("a", tmp_path), job_raw("b", tmp_path, depends_on=["a"])],
    )
    queue._try_admit()
    unit = state.jobs["a"].unit_name
    host.set_receipt(tmp_path / "logs" / "a.exit", 7)
    host.finish_unit(unit, exit_code=7)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit)
    assert state.jobs["a"].status == "failed"
    assert state.jobs["a"].exit_code == 7
    assert state.jobs["b"].status in ("queued", "deferred")
    ok, reason = queue._deps_satisfied(queue.jobs["b"])
    assert not ok and "failed" in reason


def test_missing_receipt_fails_closed(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue._try_admit()
    unit = state.jobs["a"].unit_name
    host.set_receipt(tmp_path / "logs" / "a.exit", None)  # no receipt at all
    host.finish_unit(unit)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit)
    js = state.jobs["a"]
    assert js.status == "failed"
    assert "receipt missing" in (js.error or "")


def test_is_error_result_fails(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue._try_admit()
    unit = state.jobs["a"].unit_name
    host.set_log(
        tmp_path / "logs" / "a.attempt-1.log",
        '{"type":"result","session_id":"s1","is_error":true}\n',
    )
    host.finish_unit(unit)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit)
    assert state.jobs["a"].status == "failed"
    assert state.jobs["a"].session_id == "s1"  # session still captured from the log


def test_session_id_captured_from_result_event(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue._try_admit()
    unit = state.jobs["a"].unit_name
    host.set_log(
        tmp_path / "logs" / "a.attempt-1.log",
        '{"type":"system","session_id":"sess-abc"}\n{"type":"result","session_id":"sess-abc"}\n',
    )
    host.finish_unit(unit)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit)
    assert state.jobs["a"].session_id == "sess-abc"


# --- durable sessions / resume -------------------------------------------------------------------


def test_claude_job_gets_persisted_session_and_resume_flag(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path, kind="claude")])
    queue._try_admit()
    js = state.jobs["a"]
    assert js.session_id and len(js.session_id) == 36  # uuid4 persisted BEFORE spawn
    assert js.unit_name  # unit name persisted
    argv = q.build_claude_argv(queue.jobs["a"], js.session_id)
    assert f"--session-id={js.session_id}" in argv


def test_resumed_claude_job_uses_resume_with_persisted_session(tmp_path):
    spec = q.JobSpec.from_manifest(job_raw("a", tmp_path, kind="claude"))
    argv = q.build_claude_argv(spec, "existing-session", resume=True)
    assert "--resume=existing-session" in argv
    assert not any(a.startswith("--session-id") for a in argv)


def test_interrupted_job_resumes_with_same_session(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path, kind="claude")])
    queue._try_admit()
    js = state.jobs["a"]
    session = js.session_id
    unit = js.unit_name
    # simulate a supervisor restart: reconcile adopts nothing (unit gone)
    host.finish_unit(unit)
    queue._interrupt_job(queue.jobs["a"], js, unit, reason="supervisor signal", graceful=False)
    assert js.status == "interrupted"
    assert js.session_id == session
    # explicit resume: re-queued, and the NEXT start reuses the persisted session
    js.status = "queued"
    queue._try_admit()
    assert state.jobs["a"].session_id == session
    assert state.jobs["a"].attempts == 2  # counters never reset


def test_reconcile_adopts_owned_active_unit(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue._try_admit()
    # unit stays ACTIVE across the simulated restart
    queue2, state2, host2, clock2 = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue2.state = state  # a restart reloads the same state from disk
    queue2.jobs = queue.jobs
    queue2.host_ctl = host  # same host: the unit is genuinely still active
    queue2._reconcile_previous_run()
    assert "a" in queue2._active, "still-active owned unit must be adopted"


def test_reconcile_interrupts_unowned_active_unit(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue._try_admit()
    js = state.jobs["a"]
    js.status = "running"
    # fresh supervisor sees a "running" job whose unit is GONE
    host.finish_unit(js.unit_name)
    queue2, *_ = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue2.state = state
    queue2.jobs = queue.jobs
    queue2._reconcile_previous_run()
    assert state.jobs["a"].status == "interrupted"
    assert "restarted" in (state.jobs["a"].error or "")


# --- pressure deferral ---------------------------------------------------------------------------


def test_sustained_pressure_interrupts_and_defers(tmp_path):
    # snapshots start healthy (job a is admitted), then collapse: three
    # consecutive low samples 5 virtual seconds apart = sustained pressure.
    host = FakeHostControls(snapshots=[snap(mem=8.0)] * 2 + [snap(mem=2.0)] * 30)
    queue, state, host, clock = make_queue(
        tmp_path,
        [job_raw("a", tmp_path), job_raw("b", tmp_path)],
        host,
    )
    rc = queue.run()
    assert rc == 75
    assert state.pressure_deferred
    # the running job got SIGINT (grace) then stop, marked interrupted honestly
    assert host.sigints, "running worker must receive SIGINT before stop"
    assert host.stopped
    assert state.jobs["a"].status == "interrupted"
    # queued work deferred, not silently dropped
    assert state.jobs["b"].status == "deferred"
    assert state.jobs["a"].session_id  # session preserved for resume


def test_pressure_samples_spaced_by_clock(tmp_path):
    host = FakeHostControls(snapshots=[snap(mem=2.0)] * 10)
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    # run one poll iteration manually; samples only accumulate with >= 5s gaps
    queue._last_sample = None
    queue._pressure_samples.append(host.snapshot())
    queue._last_sample = clock.now
    clock.advance(1.0)  # only 1 virtual second: no new sample yet
    pressured, _ = q.evaluate_pressure(queue._pressure_samples)
    assert not pressured


def test_admission_refusal_defers_not_fails(tmp_path):
    host = FakeHostControls(snapshots=[snap(mem=1.0)])
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)], host)
    rc = queue.run()
    # deferral-only run: exit 75 (same as pressure deferral), NOT a silent 0
    assert rc == q.EXIT_DEFERRED == 75
    js = state.jobs["a"]
    assert js.status == "deferred"
    assert "MemAvailable" in (js.error or "")


def test_pressure_history_bounded():
    samples = [snap(mem=2.0)] * 50
    assert q.evaluate_pressure(samples)[0]
    # bounded usage: only the tail is consulted
    assert q.evaluate_pressure(samples[-q.PRESSURE_HISTORY_MAX :])[0]


# --- ownership / signal safety -------------------------------------------------------------------


def test_interrupt_rejects_foreign_unit(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    job = queue.jobs["a"]
    js = state.jobs["a"]
    js.status = "running"
    foreign = "aci-worker-othertoken-a.service"
    queue._interrupt_job(job, js, foreign, reason="test", graceful=False)
    assert js.status == "failed"
    assert "ownership" in (js.error or "").lower() or "not this run" in (js.error or "")
    assert host.sigints == []  # NO signal reached the foreign unit
    assert host.stopped == []


def test_validate_ownership_real_function_rejects_mismatch(tmp_path):
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path))
    # patch unit_properties to a loaded unit with wrong cwd
    orig = q.unit_properties
    q.unit_properties = lambda unit, props: {  # type: ignore[assignment]
        "LoadState": "loaded",
        "Description": state.description_for(job),
        "WorkingDirectory": "/elsewhere",
        "ExecStart": f"exec {q.CLAUDE_BIN} -p",
    }
    try:
        ok, reason = q.validate_ownership(state, job, state.unit_for(job))
    finally:
        q.unit_properties = orig  # type: ignore[assignment]
    assert not ok and "WorkingDirectory" in reason


def test_validate_ownership_rejects_wrong_exe(tmp_path):
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path))
    orig = q.unit_properties
    q.unit_properties = lambda unit, props: {  # type: ignore[assignment]
        "LoadState": "loaded",
        "Description": state.description_for(job),
        "WorkingDirectory": job.cwd,
        "ExecStart": "exec /some/other/binary -p",
    }
    try:
        ok, reason = q.validate_ownership(state, job, state.unit_for(job))
    finally:
        q.unit_properties = orig  # type: ignore[assignment]
    assert not ok and "ExecStart" in reason


def test_validate_ownership_rejects_wrong_description(tmp_path):
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path))
    orig = q.unit_properties
    q.unit_properties = lambda unit, props: {  # type: ignore[assignment]
        "LoadState": "loaded",
        "Description": "some other description",
        "WorkingDirectory": job.cwd,
        "ExecStart": f"exec {q.CLAUDE_BIN} -p",
    }
    try:
        ok, reason = q.validate_ownership(state, job, state.unit_for(job))
    finally:
        q.unit_properties = orig  # type: ignore[assignment]
    assert not ok and "Description" in reason


def test_resource_verification_failure_stops_unit_and_fails_job(tmp_path):
    host = FakeHostControls(limits_ok=False)
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)], host)
    queue._try_admit()
    # _start_job itself verifies: job must be failed and the unit stopped
    assert state.jobs["a"].status == "failed"
    assert "resource-limit" in (state.jobs["a"].error or "")
    assert host.stopped, "unverified unit must be stopped (fail closed)"
    assert "a" not in queue._active


def test_no_process_name_matching_primitives():
    source = _SCRIPT.read_text(encoding="utf-8")
    for banned in ("pkill", "killall", "os.killpg", "psutil", "pgrep"):
        assert banned not in source, f"banned process-control primitive present: {banned}"


# --- fail closed ---------------------------------------------------------------------------------


def test_fail_closed_without_systemd(tmp_path):
    host = FakeHostControls(systemd=False)
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)], host)
    with pytest.raises(q.QueueError, match="FAIL CLOSED"):
        queue.run()


# --- unit identity -------------------------------------------------------------------------------


def test_unit_names_carry_run_token(tmp_path):
    queue, state, *_ = make_queue(tmp_path, [job_raw("a", tmp_path)])
    unit = state.unit_for(queue.jobs["a"])
    assert unit.startswith("aci-worker-")
    assert state.unit_token in unit
    assert unit.endswith("-a.service")
    assert q.UNIT_TOKEN_RE.match(state.unit_token)


def test_state_rejects_corrupted_unit_token(tmp_path):
    state_path = tmp_path / "state.json"
    state = q.QueueState(state_path)
    state.load()
    state.unit_token = "NOTHEX!!"
    state.save()
    state2 = q.QueueState(state_path)
    with pytest.raises(q.QueueError, match="corrupted unit token"):
        state2.load()


def test_state_rejects_invalid_job_status(tmp_path):
    state_path = tmp_path / "state.json"
    state = q.QueueState(state_path)
    state.load()
    state.jobs["a"] = q.JobState(status="hacked")
    state.save()
    state2 = q.QueueState(state_path)
    with pytest.raises(q.QueueError, match="invalid status"):
        state2.load()


# --- lock ----------------------------------------------------------------------------------------


def test_single_supervisor_lock(tmp_path):
    state_path = tmp_path / "state.json"
    state = q.QueueState(state_path)
    state.load()
    state.acquire_lock()
    try:
        state2 = q.QueueState(state_path)
        state2.load()
        with pytest.raises(q.QueueError, match="another queue supervisor"):
            state2.acquire_lock()
    finally:
        state.release_lock()
    state3 = q.QueueState(state_path)
    state3.load()
    state3.acquire_lock()  # after release a new supervisor may take it
    state3.release_lock()


# --- operator control ----------------------------------------------------------------------------


def test_defer_all_control_request_consumed_by_supervisor(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(
        tmp_path,
        [job_raw("a", tmp_path), job_raw("b", tmp_path), job_raw("c", tmp_path)],
        host,
    )
    queue._try_admit()  # a and b running (max 2), c queued
    assert set(queue._active) == {"a", "b"}
    # operator writes a control request while the supervisor holds the lock
    state.control_path.write_text(json.dumps({"action": "defer-all"}), encoding="utf-8")
    assert queue._consume_control_request() is True
    queue._interrupt_active(reason="operator defer-all request", defer_queued=True)
    # active workers honestly interrupted (SIGINT grace), queued work deferred
    assert state.jobs["a"].status == "interrupted"
    assert state.jobs["b"].status == "interrupted"
    assert state.jobs["c"].status == "deferred"
    assert host.sigints  # the running workers were gracefully interrupted


def test_defer_all_cli_writes_control_request_when_locked(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state = q.QueueState(state_path)
    state.load()
    state.save()  # a live supervisor always persists its state before running
    state.acquire_lock()  # simulate a live supervisor
    try:
        args = type("Args", (), {"state": str(state_path), "reason": None})()
        rc = q.cmd_defer_all(args)
        assert rc == 0
        assert state_path.with_suffix(".control").exists(), "request file must be written"
        raw = json.loads(state_path.with_suffix(".control").read_text())
        assert raw["action"] == "defer-all"
    finally:
        state.release_lock()


def test_defer_all_cli_direct_when_no_supervisor(tmp_path):
    state_path = tmp_path / "state.json"
    state = q.QueueState(state_path)
    state.load()
    state.jobs["a"] = q.JobState(status="queued")
    state.save()
    args = type("Args", (), {"state": str(state_path), "reason": None})()
    rc = q.cmd_defer_all(args)
    assert rc == 0
    state2 = q.QueueState(state_path)
    state2.load()
    assert state2.jobs["a"].status == "deferred"


def test_resume_cli_requeues_failed_job(tmp_path):
    state_path = tmp_path / "state.json"
    state = q.QueueState(state_path)
    state.load()
    state.jobs["a"] = q.JobState(status="failed", attempts=2, session_id="s1", error="boom")
    state.save()
    args = type("Args", (), {"state": str(state_path), "job": "a"})()
    rc = q.cmd_resume(args)
    assert rc == 0
    state2 = q.QueueState(state_path)
    state2.load()
    js = state2.jobs["a"]
    assert js.status == "queued"
    assert js.attempts == 2  # metadata preserved
    assert js.session_id == "s1"


def test_resume_cli_rejects_running_job(tmp_path):
    state_path = tmp_path / "state.json"
    state = q.QueueState(state_path)
    state.load()
    state.jobs["a"] = q.JobState(status="running")
    state.save()
    args = type("Args", (), {"state": str(state_path), "job": "a"})()
    rc = q.cmd_resume(args)
    assert rc == 1


# --- run loop integration (virtual time) ---------------------------------------------------------


def test_full_run_with_timeout_interrupts_gracefully(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path, timeout_s=10)])
    # unit never finishes on its own; the timeout path must fire
    rc = queue.run()
    assert rc == 0  # all work reached a terminal state; loop exits cleanly
    js = state.jobs["a"]
    assert js.status == "interrupted"
    assert "timeout" in (js.error or "")
    assert host.sigints, "timeout must SIGINT (grace) before stop"
    assert host.stopped


def test_shutdown_signal_interrupts_active_jobs(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue._try_admit()
    queue._shutdown = True  # simulate SIGINT to the supervisor
    rc = queue.run()
    assert rc == 130
    assert state.jobs["a"].status == "interrupted"
    assert host.sigints


# --- CLI argv builders ---------------------------------------------------------------------------


def test_claude_argv_flags(tmp_path):
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path, allowed_tools=["Read", "Edit"]))
    argv = q.build_claude_argv(job, "sess-1")
    assert "--model=OneNexus/glm-5.3" in argv
    assert "--session-id=sess-1" in argv
    assert "--permission-mode=acceptEdits" in argv
    assert "--permission-prompts=none" in argv
    assert "--strict-mcp-config" in argv
    assert '--mcp-config={"mcpServers":{}}' in argv
    assert "--allowedTools=Read" in argv and "--allowedTools=Edit" in argv


def test_claude_env_pins_threads_and_subagent_model(tmp_path):
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path))
    env = q.build_claude_env(job)
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "OneNexus/glm-5.3"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "OneNexus/glm-5.3"
    assert env["PWD"] == str(tmp_path)
    assert env["OMP_NUM_THREADS"] == "1"


def test_opencode_argv(tmp_path):
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path, kind="opencode"))
    argv = q.build_opencode_argv(job, "sess1")
    assert "--standalone" in argv
    assert "--model=local-gateway/OneNexus/glm-5.3" in argv
    assert "--session=sess1" in argv


def test_no_secrets_in_state_file(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue._try_admit()
    state.save()
    raw = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in raw
    assert "Authorization" not in raw


# --- cpu sampling correctness --------------------------------------------------------------------


def test_proc_stat_uses_first_eight_counters(monkeypatch):
    # guest fields (already inside user/nice) must not double-count
    fake = "cpu  100 10 20 500 5 0 0 30 40 50\n"
    import io

    monkeypatch.setattr("builtins.open", lambda *a, **kw: io.StringIO(fake))
    total, idle = q._proc_stat_jiffies()
    assert total == 100 + 10 + 20 + 500 + 5 + 0 + 0 + 30
    assert idle == 500 + 5


# --- independent review (Task C) regressions -----------------------------------------------------


class TimedHost(FakeHostControls):
    """Units finish on their own after `durations[job]` virtual seconds; records
    the peak number of simultaneously active units and per-job start times."""

    def __init__(self, clock: FakeClock, durations: dict[str, float], **kw: Any) -> None:
        super().__init__(**kw)
        self.clock = clock
        self.durations = durations
        self.started_at: dict[str, float] = {}
        self.finished_at: dict[str, float] = {}
        self.peak_active = 0

    def start_worker_unit(self, state, job, argv, env, log_path, receipt_path):  # type: ignore[no-untyped-def]
        unit = super().start_worker_unit(state, job, argv, env, log_path, receipt_path)
        self.started_at[job.id] = self.clock.now
        self.peak_active = max(self.peak_active, sum(u.active for u in self.units.values()))
        return unit

    def unit_active(self, unit: str) -> bool:
        u = self.units.get(unit)
        if u is None or not u.active:
            return False
        jid = unit.rsplit("-", 1)[-1].removesuffix(".service")
        if self.clock.now - self.started_at[jid] >= self.durations.get(jid, 1e9):
            u.active = False
            self.finished_at[jid] = self.clock.now
            return False
        return True


def test_run_loop_drives_two_concurrent_workers_while_dependent_waits(tmp_path):
    """Drive the REAL run() loop (not _try_admit): a and b overlap, c (depends on
    a) only starts after a finished, and never more than 2 units are active."""
    clock = FakeClock()
    host = TimedHost(clock, {"a": 10.0, "b": 30.0, "c": 5.0})
    queue, state, host, clock = make_queue(
        tmp_path,
        [job_raw("a", tmp_path), job_raw("b", tmp_path), job_raw("c", tmp_path, depends_on=["a"])],
        host,
        clock,
    )
    assert queue.run() == 0
    assert host.peak_active == 2
    assert host.started_at["a"] == host.started_at["b"]  # admitted in the same poll
    assert host.started_at["c"] >= host.finished_at["a"]
    assert host.started_at["c"] < host.finished_at["b"]  # c overlapped b
    assert all(js.status == "finished-needs-review" for js in state.jobs.values())


def test_admission_refusal_while_worker_active_waits_instead_of_deferring(tmp_path):
    # b is refused only by the 5 GiB "another worker" floor while a runs: it must
    # WAIT (stay queued) and start once a frees its slot, not be deferred away.
    host = FakeHostControls(snapshots=[snap(mem=8.0), snap(mem=4.5)])
    queue, state, host, clock = make_queue(
        tmp_path, [job_raw("a", tmp_path), job_raw("b", tmp_path)], host
    )
    queue._try_admit()
    assert set(queue._active) == {"a"}
    assert state.jobs["b"].status == "queued"
    unit_a = state.jobs["a"].unit_name
    host.finish_unit(unit_a)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit_a)
    queue._try_admit()  # 4.5 GiB >= the 3.5 GiB first-worker floor
    assert "b" in queue._active


def test_resumed_claude_job_spawns_with_exact_resume_flag(tmp_path):
    # The persisted session must be RESUMED (--resume), never re-created with
    # --session-id (claude rejects a reused --session-id; history would be lost).
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path, kind="claude")], host)
    queue._try_admit()
    js = state.jobs["a"]
    session = js.session_id
    assert f"--session-id={session}" in host.argvs[0]
    queue._interrupt_job(queue.jobs["a"], js, js.unit_name, reason="pressure", graceful=True)
    assert js.status == "interrupted"
    js.status = "queued"  # what an explicit `run`/`resume` does
    host.units.clear()  # the old unit is gone
    queue._try_admit()
    second = host.argvs[1]
    assert f"--resume={session}" in second
    assert not any(a.startswith("--session-id") for a in second)


def test_manifest_session_pointer_is_resumed_and_persisted(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(
        tmp_path, [job_raw("a", tmp_path, kind="claude", session_id="prior-sess")], host
    )
    queue._try_admit()
    assert "--resume=prior-sess" in host.argvs[0]
    assert state.jobs["a"].session_id == "prior-sess"


class FileHost(FakeHostControls):
    """Receipts/logs are REAL files (the production readers), the wrapper is
    simulated: the 'process' writes nothing unless the test writes it."""

    def start_worker_unit(self, state, job, argv, env, log_path, receipt_path):  # type: ignore[no-untyped-def]
        unit = state.unit_for(job)
        self.started.append(unit)
        self.argvs.append(list(argv))
        self.envs.append(dict(env))
        self.log_paths.append(Path(log_path))
        self.units[unit] = FakeUnit(unit, job.cwd)
        return unit

    def read_exit_receipt(self, receipt_path: Path) -> int | None:
        return q.read_exit_receipt(receipt_path)

    def parse_run_log(self, log_path: Path, require_result: bool = False):  # type: ignore[no-untyped-def]
        return q.parse_run_log(log_path, require_result)


def test_stale_exit_receipt_from_previous_attempt_is_never_reused(tmp_path):
    # attempt 1 left receipt "0"; attempt 2 is OOM-killed (OOMPolicy=kill kills
    # the wrapper too, so it writes NO receipt) -> must fail, not inherit "0".
    host = FileHost()
    queue, state, host, clock = make_queue(
        tmp_path, [job_raw("a", tmp_path), job_raw("b", tmp_path, depends_on=["a"])], host
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.exit").write_text("0\n")
    queue._try_admit()
    (logs / "a.attempt-1.log").write_text(
        '{"type":"result","subtype":"success","is_error":false}\n'
    )
    unit = state.jobs["a"].unit_name
    host.finish_unit(unit)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit)
    assert state.jobs["a"].status == "failed"
    assert "receipt missing" in (state.jobs["a"].error or "")
    assert not queue._deps_satisfied(queue.jobs["b"])[0]


def test_claude_exit_zero_without_result_event_fails(tmp_path):
    # stdout carried a plain-text error and no stream-json result: unverifiable.
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path, kind="claude")], host)
    queue._try_admit()
    host.set_log(tmp_path / "logs" / "a.attempt-1.log", "API Error: 401 unauthorized\n")
    unit = state.jobs["a"].unit_name
    host.finish_unit(unit)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit)
    assert state.jobs["a"].status == "failed"


def test_claude_error_result_subtype_fails(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path, kind="claude")], host)
    queue._try_admit()
    host.set_log(
        tmp_path / "logs" / "a.attempt-1.log", '{"type":"result","subtype":"error_max_turns"}\n'
    )
    unit = state.jobs["a"].unit_name
    host.finish_unit(unit)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit)
    assert state.jobs["a"].status == "failed"


def test_reconcile_finalizes_unit_that_completed_while_supervisor_was_dead(tmp_path):
    # unit gone + durable receipt present: finalize from the receipt instead of
    # marking `interrupted` (which the next `run` would re-execute = duplicate).
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)], host)
    queue._try_admit()
    host.finish_unit(state.jobs["a"].unit_name)
    queue2, *_ = make_queue(tmp_path, [job_raw("a", tmp_path)])
    queue2.state, queue2.jobs, queue2.host_ctl = state, queue.jobs, host
    queue2._reconcile_previous_run()
    assert state.jobs["a"].status == "finished-needs-review"
    assert state.jobs["a"].attempts == 1


def test_systemd_run_disables_env_expansion_and_never_interpolates_job_fields(
    tmp_path, monkeypatch
):
    captured: list[list[str]] = []

    def fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        captured.append(cmd)
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(q.subprocess, "run", fake_run)
    evil = 'x"; touch /tmp/pwn; echo "${HOME} $$ `id`'
    state = q.QueueState(tmp_path / "s.json")
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path, prompt=evil))
    q.start_worker_unit(
        state, job, q.build_claude_argv(job, "s1"), {}, tmp_path / "a.log", tmp_path / "a.exit"
    )
    cmd = captured[0]
    # systemd expands ${VAR}/$$ in service command lines by default (>= v254)
    assert "--expand-environment=no" in cmd
    sh = cmd.index("/bin/sh")
    assert cmd[sh : sh + 4] == ["/bin/sh", "-c", q.WRAPPER_SH, "worker"]
    assert cmd[-1] == evil  # passed verbatim as ONE positional arg ("$@"), never spliced


def test_manifest_cwd_is_normalized_absolute(tmp_path):
    spec = q.JobSpec.from_manifest({"id": "a", "cwd": str(tmp_path) + "/"})
    assert spec.cwd == str(tmp_path)


def test_manifest_rejects_non_ascii_job_id(tmp_path):
    with pytest.raises(q.QueueError, match="slug"):
        q.JobSpec.from_manifest({"id": "jöb", "cwd": str(tmp_path)})


def test_defer_all_refuses_missing_state(tmp_path):
    args = type("Args", (), {"state": str(tmp_path / "nope.json"), "reason": None})()
    assert q.cmd_defer_all(args) == 1
    assert not (tmp_path / "nope.json").exists()


def test_stale_control_request_does_not_defer_a_new_explicit_run(tmp_path, monkeypatch):
    # a defer-all request left for a supervisor that died before consuming it
    # must not silently defer the NEXT explicit `run`.
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"jobs": [job_raw("a", tmp_path)]}))
    state_path = tmp_path / "st.json"
    state_path.with_suffix(".control").write_text(json.dumps({"action": "defer-all"}))
    host = FakeHostControls()
    orig_start = host.start_worker_unit

    def start_and_finish(*a: Any, **kw: Any) -> str:
        unit = orig_start(*a, **kw)
        host.finish_unit(unit)
        return unit

    host.start_worker_unit = start_and_finish  # type: ignore[method-assign]
    monkeypatch.setattr(q, "HostControls", lambda: host)
    monkeypatch.setattr(q, "POLL_INTERVAL_S", 0.0)
    args = type(
        "Args",
        (),
        {
            "manifest": str(manifest),
            "state": str(state_path),
            "dry_run": False,
            "backend": "systemd",
        },
    )
    assert q.cmd_run(args()) == 0
    st = q.QueueState(state_path)
    st.load()
    assert st.jobs["a"].status == "finished-needs-review"


def test_pressure_samples_spaced_and_history_bounded_in_run_loop(tmp_path):
    clock = FakeClock()
    sample_times: list[float] = []

    class Host(TimedHost):
        def snapshot(self) -> q.HostSnapshot:
            sample_times.append(clock.now)
            # healthy for the first sample + admission, then RAM collapses
            return snap(mem=8.0) if clock.now <= 1000.0 else snap(mem=2.0)

    host = Host(clock, {})
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)], host, clock)
    assert queue.run() == 75
    low = [t for t in sample_times if t > 1000.0]
    assert len(low) == q.PRESSURE_SAMPLES  # exactly 3 sustained samples, no more
    assert all(b - a >= q.PRESSURE_INTERVAL_S for a, b in zip(low, low[1:], strict=False))
    assert len(queue._pressure_samples) <= q.PRESSURE_HISTORY_MAX


def test_pressure_history_bounded_over_long_healthy_run(tmp_path):
    clock = FakeClock()
    host = TimedHost(clock, {"a": 600.0})
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)], host, clock)
    assert queue.run() == 0
    assert len(queue._pressure_samples) == q.PRESSURE_HISTORY_MAX


def test_unit_active_query_failure_is_not_treated_as_exited(monkeypatch):
    # systemctl timeout / D-Bus failure (e.g. under host pressure) returns no
    # properties: that is UNKNOWN, not "exited" — finalizing would free the slot
    # of a still-running worker. A gone unit reports ActiveState=inactive.
    monkeypatch.setattr(q, "unit_properties", lambda unit, props: {})
    assert q.unit_active("aci-worker-0123456789abcdef-a.service") is True
    monkeypatch.setattr(q, "unit_properties", lambda unit, props: {"ActiveState": "inactive"})
    assert q.unit_active("aci-worker-0123456789abcdef-a.service") is False


# --- opencode path (verified live against opencode v2.0.21, 2026-10-01) -------------


def test_opencode_session_ids_carry_the_required_ses_prefix() -> None:
    sid = q.new_opencode_session_id()
    assert sid.startswith("ses_") and len(sid) == 4 + 24


def test_opencode_argv_is_headless_safe() -> None:
    job = q.JobSpec.from_manifest(
        {"id": "oc-job", "kind": "opencode", "prompt": "do it", "cwd": "/tmp"}
    )
    argv = q.build_opencode_argv(job, "ses_abc")
    assert "--auto" in argv  # never block on an interactive permission prompt
    assert "--session=ses_abc" in argv
    assert argv[-1] == "do it"


def test_opencode_error_event_is_never_success() -> None:
    log = (
        '{"type":"error","sessionID":"","error":{"message":"Expected a string '
        'starting with \\"ses\\""}}\n'
    )
    _session, is_error = q.parse_run_log_text(log)
    assert is_error


def test_opencode_session_id_is_read_from_events() -> None:
    log = '{"type":"text","sessionID":"ses_123","part":{}}\n'
    session, is_error = q.parse_run_log_text(log)
    assert session == "ses_123" and not is_error


def test_resource_overrides_are_validated_and_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MAX_CONCURRENT_WORKERS",
        "ADMIT_MEM_AVAILABLE_GIB",
        "ADMIT_SINGLE_MEM_AVAILABLE_GIB",
        "PRESSURE_MEM_AVAILABLE_GIB",
    ):
        monkeypatch.setattr(q, name, getattr(q, name))
    q.apply_resource_overrides(3, 3.0, 1.5)
    assert q.MAX_CONCURRENT_WORKERS == 3
    assert q.ADMIT_MEM_AVAILABLE_GIB == q.ADMIT_SINGLE_MEM_AVAILABLE_GIB == 3.0
    assert q.PRESSURE_MEM_AVAILABLE_GIB == 1.5
    for bad in ((9, None, None), (None, 1.0, None), (None, None, 0.5), (None, 3.0, 3.5)):
        with pytest.raises(q.QueueError):
            q.apply_resource_overrides(*bad)


# --- m2 hardening (2026-10-02 independent review, non-blocking findings) ----------------


class RecordingHost(FakeHostControls):
    """Records the VIRTUAL time of every SIGINT/stop call (clock-driven)."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self.clock = clock
        self.sigint_times: list[float] = []
        self.stop_times: list[float] = []

    def send_sigint_to_unit(self, unit: str) -> bool:
        self.sigint_times.append(self.clock.now)
        return super().send_sigint_to_unit(unit)

    def stop_worker_unit(self, unit: str) -> bool:
        self.stop_times.append(self.clock.now)
        return super().stop_worker_unit(unit)


# 1. per-attempt logs


def test_per_attempt_logs_are_kept_and_latest_is_copied(tmp_path):
    """Every attempt writes its OWN <job>.attempt-N.log — the wrapper's `>`
    redirection can never destroy an earlier attempt's evidence — and
    <job>.log is kept as a copy of the latest attempt."""
    host = FileHost()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path, kind="claude")], host)
    logs = tmp_path / "logs"
    queue._try_admit()
    # the wrapper was pointed at the PER-ATTEMPT log, not at <job>.log
    aciq_log = host.log_paths[0]
    assert aciq_log == logs / "a.attempt-1.log"
    aciq_log.write_text("attempt-1 evidence\n")
    host.finish_unit(state.jobs["a"].unit_name)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], state.jobs["a"].unit_name)
    assert (logs / "a.attempt-1.log").read_text() == "attempt-1 evidence\n"
    assert (logs / "a.log").read_text() == "attempt-1 evidence\n"  # latest copy
    # resume: attempt 2 must NOT touch attempt 1's file
    state.jobs["a"].status = "queued"
    queue._try_admit()
    aciq_log2 = host.log_paths[1]
    assert aciq_log2 == logs / "a.attempt-2.log"
    aciq_log2.write_text("attempt-2 evidence\n")
    host.finish_unit(state.jobs["a"].unit_name)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], state.jobs["a"].unit_name)
    assert (logs / "a.attempt-1.log").read_text() == "attempt-1 evidence\n"  # NOT lost
    assert (logs / "a.log").read_text() == "attempt-2 evidence\n"  # latest = attempt 2


def test_interrupted_attempt_log_is_promoted_to_latest(tmp_path):
    """An interrupted attempt's log also becomes <job>.log (the latest)."""
    host = FileHost()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)], host)
    logs = tmp_path / "logs"
    queue._try_admit()
    (logs / "a.attempt-1.log").write_text("partial evidence\n")
    queue._interrupt_job(
        queue.jobs["a"],
        state.jobs["a"],
        state.jobs["a"].unit_name,
        reason="pressure",
        graceful=False,
    )
    assert state.jobs["a"].status == "interrupted"
    assert (logs / "a.log").read_text() == "partial evidence\n"


# 2. pressure interruption: signal ALL owned units first, ONE shared grace window


def test_pressure_interrupt_signals_all_units_first_then_one_shared_window(tmp_path):
    """_interrupt_active must SIGINT ALL owned units FIRST, wait ONE shared
    30 s grace window, then stop the stragglers — never SIGINT one unit, wait
    out its full grace, stop it, and only then signal the next (60 s total)."""
    clock = FakeClock()
    host = RecordingHost(clock)
    queue, state, host, clock = make_queue(
        tmp_path, [job_raw("a", tmp_path), job_raw("b", tmp_path)], host, clock
    )
    queue._try_admit()
    assert set(queue._active) == {"a", "b"}
    t0 = clock.now
    queue._interrupt_active(reason="host pressure", defer_queued=True)
    # ALL units were signaled BEFORE any unit was stopped
    assert len(host.sigints) == 2
    assert max(host.sigint_times) < min(host.stop_times)
    # ONE shared 30 s window, not one per unit (old code: 60 s)
    assert clock.now - t0 <= q.SIGINT_CHECKPOINT_GRACE_S + 1.0
    assert all(js.status == "interrupted" for js in state.jobs.values())
    assert all(js.session_id for js in state.jobs.values())  # resumable


def test_pressure_interrupt_skips_unowned_unit_but_signals_owned(tmp_path):
    """Ownership is validated before EVERY signal: a unit that fails
    validation is never signaled (failed honestly) while its sibling still
    gets the full graceful interrupt."""
    clock = FakeClock()
    host = RecordingHost(clock)
    queue, state, host, clock = make_queue(
        tmp_path, [job_raw("a", tmp_path), job_raw("b", tmp_path)], host, clock
    )
    queue._try_admit()
    unit_a = state.jobs["a"].unit_name
    host.ownership_fail_units.add(unit_a)  # e.g. Description no longer matches
    queue._interrupt_active(reason="host pressure", defer_queued=True)
    assert host.sigints == [state.jobs["b"].unit_name]
    assert state.jobs["a"].status == "failed"
    assert "ownership" in (state.jobs["a"].error or "")
    assert state.jobs["b"].status == "interrupted"


# 3. deferral-only runs exit 75 (same as pressure deferral)


def test_dependency_deferral_only_run_returns_75(tmp_path):
    """An admission-refused job defers its dependent too: EVERY job deferred,
    nothing ran — exit 75, not a silent all-good 0."""
    host = FakeHostControls(snapshots=[snap(mem=1.0)])
    queue, state, host, clock = make_queue(
        tmp_path,
        [job_raw("a", tmp_path), job_raw("b", tmp_path, depends_on=["a"])],
        host,
    )
    assert queue.run() == q.EXIT_DEFERRED == 75
    assert state.jobs["a"].status == "deferred"
    assert state.jobs["b"].status == "deferred"


def test_run_with_any_completed_job_still_exits_0(tmp_path):
    """75 is for DEFERRAL-ONLY runs: when at least one job reached a real
    terminal state, the run exits 0 even if another job was deferred."""
    host = FakeHostControls(snapshots=[snap(mem=8.0), snap(mem=4.0), snap(mem=3.0)])
    queue, state, host, clock = make_queue(
        tmp_path, [job_raw("a", tmp_path), job_raw("b", tmp_path)], host
    )
    orig_start = host.start_worker_unit

    def start_and_finish(*a: Any, **kw: Any) -> str:
        unit = orig_start(*a, **kw)
        host.finish_unit(unit)
        return unit

    host.start_worker_unit = start_and_finish  # type: ignore[method-assign]
    assert queue.run() == 0
    assert state.jobs["a"].status == "finished-needs-review"
    assert state.jobs["b"].status == "deferred"


# 4. signal handlers only in cmd_run for a real run


def test_worker_queue_constructor_installs_no_signal_handlers(tmp_path, monkeypatch):
    """Signal handlers belong to cmd_run's REAL run only — the constructor
    (also used by --dry-run and by tests) must never touch process signals."""
    installed: list[int] = []
    monkeypatch.setattr(q.signal, "signal", lambda sig, handler: installed.append(sig))
    queue, *_ = make_queue(tmp_path, [job_raw("a", tmp_path)])
    assert installed == [], "constructor must not install signal handlers"
    queue.install_signal_handlers()
    assert installed == [signal.SIGINT, signal.SIGTERM]
    queue.restore_signal_handlers()
    assert len(installed) == 4  # both handlers restored afterwards


def test_cmd_run_installs_handlers_only_for_real_runs(tmp_path, monkeypatch):
    calls: list[tuple[Any, Any]] = []

    def fake_signal(sig: Any, handler: Any) -> Any:
        calls.append((sig, handler))
        return signal.default_int_handler

    monkeypatch.setattr(q.signal, "signal", fake_signal)
    monkeypatch.setattr(q, "host_snapshot", lambda: q.HostSnapshot(8.0, 10.0, 1.0))
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"jobs": [job_raw("a", tmp_path)]}))
    # dry-run: NO signal handlers (backend pinned: hermetic on macOS too)
    args = type(
        "Args",
        (),
        {
            "manifest": str(manifest),
            "state": str(tmp_path / "st.json"),
            "dry_run": True,
            "backend": "systemd",
        },
    )()
    assert q.cmd_run(args) == 0
    assert calls == [], "--dry-run must not install signal handlers"
    # real run: handlers installed (and restored afterwards)
    host = FakeHostControls()
    orig_start = host.start_worker_unit

    def start_and_finish(*a: Any, **kw: Any) -> str:
        unit = orig_start(*a, **kw)
        host.finish_unit(unit)
        return unit

    host.start_worker_unit = start_and_finish  # type: ignore[method-assign]
    monkeypatch.setattr(q, "HostControls", lambda: host)
    monkeypatch.setattr(q, "POLL_INTERVAL_S", 0.0)
    args2 = type(
        "Args",
        (),
        {
            "manifest": str(manifest),
            "state": str(tmp_path / "st2.json"),
            "dry_run": False,
            "backend": "systemd",
        },
    )()
    assert q.cmd_run(args2) == 0
    sigs = [sig for sig, _h in calls]
    assert signal.SIGINT in sigs and signal.SIGTERM in sigs
    assert len(calls) == 4  # 2 installs + 2 restores


# 5. reset-failed is ownership-gated


def test_finalize_never_resets_failed_of_unowned_unit(tmp_path):
    """`reset-failed` clears systemd's failed-unit bookkeeping — doing it to a
    unit that fails ownership validation would clear SOMEONE ELSE's failure
    state. The job itself still finalizes honestly."""
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)], host)
    queue._try_admit()
    unit = state.jobs["a"].unit_name
    host.finish_unit(unit)
    host.ownership_fail_units.add(unit)  # ownership now fails (Description tampered)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit)
    assert host.cleaned == [], "unowned unit must never be reset-failed"
    assert state.jobs["a"].status == "finished-needs-review"


def test_finalize_resets_owned_failed_unit(tmp_path):
    host = FakeHostControls()
    queue, state, host, clock = make_queue(tmp_path, [job_raw("a", tmp_path)], host)
    queue._try_admit()
    unit = state.jobs["a"].unit_name
    host.set_receipt(tmp_path / "logs" / "a.exit", 3)
    host.finish_unit(unit, exit_code=3)
    queue._finalize_job(queue.jobs["a"], state.jobs["a"], unit)
    assert host.cleaned == [unit]  # OWNED failed unit: reset-failed runs
    assert state.jobs["a"].status == "failed"


def test_cleanup_finished_unit_real_function_is_ownership_gated(tmp_path, monkeypatch):
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path))
    unit = state.unit_for(job)
    reset_calls: list[list[str]] = []

    def fake_systemctl(args: list[str], timeout: float = 20.0) -> Any:
        reset_calls.append(list(args))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(q, "_systemctl", fake_systemctl)
    # loaded unit with a MISMATCHED Description: refuse to reset
    monkeypatch.setattr(
        q,
        "unit_properties",
        lambda unit, props: {  # type: ignore[assignment]
            "LoadState": "loaded",
            "Description": "some other description",
            "WorkingDirectory": job.cwd,
            "ExecStart": f"exec {q.CLAUDE_BIN} -p",
        },
    )
    q.cleanup_finished_unit(state, job, unit)
    assert reset_calls == []
    # not loaded (cleanly-exited transient unit is gone): skip silently
    monkeypatch.setattr(q, "unit_properties", lambda unit, props: {})  # type: ignore[assignment]
    q.cleanup_finished_unit(state, job, unit)
    assert reset_calls == []
    # loaded + matching: reset-failed runs
    monkeypatch.setattr(
        q,
        "unit_properties",
        lambda unit, props: {  # type: ignore[assignment]
            "LoadState": "loaded",
            "Description": state.description_for(job),
            "WorkingDirectory": job.cwd,
            "ExecStart": f"exec {q.CLAUDE_BIN} -p",
        },
    )
    q.cleanup_finished_unit(state, job, unit)
    assert reset_calls == [["reset-failed", unit]]


# 6. honest Description label (spec_digest=, legacy exe= still accepted)


def test_unit_description_label_is_spec_digest_not_exe(tmp_path):
    """The Description label tells the truth: it carries the manifest SPEC
    DIGEST (older versions misleadingly named it `exe=`)."""
    queue, state, *_ = make_queue(tmp_path, [job_raw("a", tmp_path)])
    desc = state.description_for(queue.jobs["a"])
    assert "spec_digest=" in desc
    assert "exe=" not in desc
    assert queue.jobs["a"].digest() in desc


def test_validate_ownership_accepts_legacy_exe_label(tmp_path):
    """Units started by an OLDER version (Description `exe=<digest>`) stay
    recognizable/interruptible across an upgrade — validation accepts both
    labels, so a running supervisor's workers never become orphans."""
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path))
    legacy = f"aci worker queue token={state.unit_token} job={job.id} exe={job.digest()}"
    orig = q.unit_properties
    q.unit_properties = lambda unit, props: {  # type: ignore[assignment]
        "LoadState": "loaded",
        "Description": legacy,
        "WorkingDirectory": job.cwd,
        "ExecStart": f"exec {q.CLAUDE_BIN} -p",
    }
    try:
        ok, reason = q.validate_ownership(state, job, state.unit_for(job))
    finally:
        q.unit_properties = orig  # type: ignore[assignment]
    assert ok, reason


def test_validate_ownership_rejects_wrong_digest(tmp_path):
    state = q.QueueState(tmp_path / "s.json")
    state.load()
    job = q.JobSpec.from_manifest(job_raw("a", tmp_path))
    orig = q.unit_properties
    q.unit_properties = lambda unit, props: {  # type: ignore[assignment]
        "LoadState": "loaded",
        "Description": (
            f"aci worker queue token={state.unit_token} job={job.id} spec_digest=deadbeef"
        ),
        "WorkingDirectory": job.cwd,
        "ExecStart": f"exec {q.CLAUDE_BIN} -p",
    }
    try:
        ok, reason = q.validate_ownership(state, job, state.unit_for(job))
    finally:
        q.unit_properties = orig  # type: ignore[assignment]
    assert not ok and "Description" in reason


# 7. durable state persistence (fsync + atomic rename)


def test_state_save_fsyncs_temp_file_before_atomic_rename(tmp_path, monkeypatch):
    """State persists durably: write-to-temp + fsync + atomic rename + a
    directory fsync — a crash mid-write can never leave a truncated state
    file (load() would refuse it and the run token would be lost)."""
    state_path = tmp_path / "state.json"
    state = q.QueueState(state_path)
    state.load()
    fsynced: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        fsynced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(q.os, "fsync", recording_fsync)
    state.save()
    assert len(fsynced) == 2, "save() must fsync the temp file AND the directory"
    assert not (tmp_path / "state.tmp").exists()  # consumed by the atomic rename
    state2 = q.QueueState(state_path)
    state2.load()
    assert state2.unit_token == state.unit_token  # round-trips intact
