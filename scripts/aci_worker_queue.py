#!/usr/bin/env python3
"""ACI worker queue — resource-controlled launcher for delegated GLM-5.3 agent jobs.

A small standard-library-only queue for invoking Claude Code / OpenCode
(GLM-5.3) jobs from a JSON manifest with a dependency graph, persistent
state/logs/session-ids, admission control against live host resources, and
per-worker resource containment. TWO backends behind one seam
(select_backend/make_host_controls):

- systemd (Linux, default there): each worker runs in its OWN transient
  systemd user service with kernel-enforced cgroup limits.
- darwin (macOS, default there): no systemd/cgroups exist — each worker
  runs in its OWN process group/session (start_new_session) under
  `nice -n 10` through the SAME /bin/sh exit-receipt wrapper; the
  per-worker memory cap is enforced by POLLING the process-group RSS
  (`ps -o rss= -g <pgid>`) and SIGINT -> grace -> SIGKILL of the whole
  GROUP (macOS does not enforce RLIMIT_AS). Ownership before ANY signal:
  the recorded pgid + leader pid + leader start time + command line must
  all match (never signal by name). The honest guarantee differences are
  listed in docs/operations/aci-worker-queue.md (macOS section).

Design constraints (see docs/operations/aci-worker-queue.md and
data/aci-improvement/BOOTSTRAP.md):

- Every worker runs in its OWN systemd user service (transient unit) with
  MemoryHigh=1536M, MemoryMax=2G, MemorySwapMax=256M, CPUQuota=200%,
  TasksMax=128, OOMPolicy=kill, Nice=10. The exact applied values are
  re-read from the unit and verified numerically before the job is allowed to
  run; any mismatch fails closed (the unit is stopped, the job fails).
- Admission: at most 2 concurrent workers; a new worker requires
  MemAvailable >= 5 GiB, CPU busy < 75%, load1 < 10; the first worker
  requires MemAvailable >= 3.5 GiB.
- Sustained pressure (3 samples, 5 s apart): MemAvailable < 3 GiB or
  CPU busy > 85% or load1 > 12 → stop admitting, interrupt running workers
  (SIGINT to ALL owned units first, ONE shared 30 s grace window, then stop
  stragglers), defer queued work to the next explicit invocation.
- Exit codes: 0 = every job reached a terminal state; 75 = work was DEFERRED
  (sustained pressure, or an admission/dependency deferral-only run where
  nothing ran); 130 = supervisor interrupted by signal; 2 = queue error.
- Logs: every attempt writes its OWN <job>.attempt-N.log (earlier evidence
  is never truncated); <job>.log is kept as a copy of the latest attempt.
- Ownership: every stop/signal/reset-failed call is preceded by ownership
  validation — the unit name must carry this queue run's persisted unit
  token, the unit Description must carry the same token + job id (the
  spec-digest label `spec_digest=`, or the legacy `exe=` label written by
  older versions), WorkingDirectory must equal the job cwd, and ExecStart
  must contain the expected CLI binary. Process-name matching utilities are
  never used.
- The worker CLI runs inside a /bin/sh wrapper in the same cgroup that
  redirects output to a log and writes a durable exit receipt; the agent
  exit code alone NEVER implies accepted/verified completion (exit 0 is
  recorded as finished-needs-review, everything else fails).
- One supervisor per state file (flock); state is bound to the manifest
  job specs (changing a manifest job under an existing state fails closed).
  State persists via write-to-temp + fsync + atomic rename (a crash never
  leaves a truncated state file).
- Job timeout default 30 min; CLI failure/timeout → bounded failed/interrupted
  state, no unlimited retries. Explicit `run` re-invocation resumes
  interrupted/deferred jobs (claude --resume with the persisted session id).

Usage:
  python scripts/aci_worker_queue.py run MANIFEST.json [--dry-run] [--backend systemd|darwin]
  python scripts/aci_worker_queue.py status [--state FILE] [--backend systemd|darwin]
  python scripts/aci_worker_queue.py resume JOB [--state FILE]
  python scripts/aci_worker_queue.py defer-all [--state FILE] [--reason TEXT]
  python scripts/aci_worker_queue.py host-snapshot [--backend systemd|darwin]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux only
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("aci_worker_queue")

# --- policy constants (BOOTSTRAP.md "Resource policy") ------------------------

MAX_CONCURRENT_WORKERS = 2
ADMIT_MEM_AVAILABLE_GIB = 5.0  # to start ANOTHER worker while some run
ADMIT_SINGLE_MEM_AVAILABLE_GIB = 3.5  # to admit a worker at all
ADMIT_CPU_BUSY_PCT = 75.0
ADMIT_LOAD1 = 10.0
PRESSURE_SAMPLES = 3
PRESSURE_INTERVAL_S = 5.0
PRESSURE_MEM_AVAILABLE_GIB = 3.0
PRESSURE_CPU_BUSY_PCT = 85.0
PRESSURE_LOAD1 = 12.0
PRESSURE_HISTORY_MAX = PRESSURE_SAMPLES * 2

# Exact expected cgroup values (verified against the live unit, not assumed).
UNIT_MEMORY_HIGH_BYTES = 1536 * 1024 * 1024
UNIT_MEMORY_MAX_BYTES = 2 * 1024 * 1024 * 1024
UNIT_MEMORY_SWAP_MAX_BYTES = 256 * 1024 * 1024
UNIT_CPU_QUOTA_PCT = 200  # systemd CPUQuota=200% -> CPUQuotaPerSecUSec=2s
# systemd reports CPUQuotaPerSecUSec in its time format ("2s"); the expected
# string is what `systemctl show` prints for a 200% quota.
UNIT_CPU_QUOTA_PER_SEC_USEC_STR = "2s"
UNIT_TASKS_MAX = 128
UNIT_OOM_POLICY = "kill"
UNIT_NICE = 10

DEFAULT_JOB_TIMEOUT_S = 30 * 60
SIGINT_CHECKPOINT_GRACE_S = 30.0
POLL_INTERVAL_S = 2.0

#: Exit code for "work was deferred, nothing ran to completion" — returned
#: consistently for sustained-pressure deferral AND admission/dependency
#: deferral-only runs (0 = every job reached a terminal state, 2 = QueueError,
#: 130 = supervisor interrupted by signal).
EXIT_DEFERRED = 75
EXIT_SIGNALLED = 130

UNIT_PREFIX = "aci-worker"
UNIT_TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # ASCII: becomes part of a unit name
#: Unit Description label for the manifest spec digest. Older versions wrote
#: the digest under the misleading name `exe=`; that label is still ACCEPTED
#: (see QueueState.description_matches) so units started by a supervisor that
#: is still running stay recognizable across an upgrade.
UNIT_DESC_SPEC_LABEL = "spec_digest"
UNIT_DESC_SPEC_LABEL_LEGACY = "exe"
STATE_SCHEMA_VERSION = 2
JOB_STATUSES = frozenset(
    {"queued", "running", "finished-needs-review", "verified", "failed", "deferred", "interrupted"}
)
# Only these statuses satisfy a dependency: exit 0 is NOT verification.
DEP_SATISFIED_STATUSES = frozenset({"finished-needs-review", "verified"})

CLAUDE_BIN = os.environ.get("ACIQ_CLAUDE_BIN") or (
    "/home/hien/.antigravity-ide/extensions/anthropic.claude-code-2.1.286-linux-x64"
    "/resources/native-binary/claude"
)


def _default_opencode_bin() -> str:
    # Linux: the original deployment path. macOS: the official installer path
    # (verified present on the Mac worker host). ACIQ_OPENCODE_BIN overrides.
    if platform.system() == "Darwin":
        return os.path.expanduser("~/.opencode/bin/opencode")
    return "/home/hien/.opencode/bin/opencode"


OPENCODE_BIN = os.environ.get("ACIQ_OPENCODE_BIN") or _default_opencode_bin()
MODEL_ID = "OneNexus/glm-5.3"
OPENCODE_MODEL_ID = "local-gateway/OneNexus/glm-5.3"
#: RuntimeMaxSec = job timeout + this margin (SIGINT grace + finalization).
RUNTIME_MAX_MARGIN_S = 120

# --- darwin (macOS) backend policy ------------------------------------------------
# Same per-worker resource POLICY as the systemd unit values, but enforced
# differently (no cgroups on macOS — see DarwinHostControls for the honest list
# of what is kernel-enforced vs supervisor-polled).

#: `nice -n 10` prefix: the worker runs at a LOWER CPU priority than the
#: supervisor (nice is an INCREMENT on the parent's nice, capped at 20).
DARWIN_WORKER_NICE = 10
#: Per-worker memory cap = the Linux MemoryMax policy (2 GiB). NOT kernel
#: enforced (macOS ignores RLIMIT_AS): enforced by polling the process-group
#: RSS every scheduler poll and SIGINT -> grace -> SIGKILL the group.
DARWIN_MEM_CAP_BYTES = UNIT_MEMORY_MAX_BYTES
#: Worker handle: the process-group id, `pgid-<pid>` (start_new_session makes
#: the spawned child the group leader, so pgid == leader pid).
DARWIN_HANDLE_RE = re.compile(r"^pgid-(\d+)$")

# The wrapper runs INSIDE the unit cgroup (so the CLI and all its descendants
# are contained) and leaves a durable exit receipt the supervisor reads after
# the unit goes inactive — transient units disappear before their exit code
# could be queried from systemd.
WRAPPER_SH = '"$@" > "$ACIQ_LOG" 2>&1; rc=$?; echo "$rc" > "$ACIQ_RECEIPT"'
# Kept as a named constant so tests/reviews can pin the wrapper contract.


class QueueError(RuntimeError):
    """Caller-visible queue failure (never a silent fallback)."""


# --- host resource sampling (pure reads) --------------------------------------


def read_mem_available_kib() -> float:
    # MemAvailable (not MemFree): the honest "can we admit more work" number.
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return float(line.split()[1])
    raise QueueError("MemAvailable missing from /proc/meminfo")


def mem_available_gib() -> float:
    return read_mem_available_kib() / (1024 * 1024)


def _proc_stat_jiffies() -> tuple[float, float]:
    with open("/proc/stat", encoding="utf-8") as fh:
        parts = fh.readline().split()[1:]
    # First 8 counters only (user..steal): guest/guest_nice are already
    # included inside user/nice and would double-count busy time.
    vals = [float(p) for p in parts[:8]]
    total = sum(vals)
    idle = vals[3] + vals[4]  # idle + iowait
    return total, idle


def cpu_busy_pct(sample_interval_s: float = 0.2) -> float:
    """CPU busy percent from /proc/stat deltas over a short sampling window."""
    t0 = _proc_stat_jiffies()
    time.sleep(sample_interval_s)
    t1 = _proc_stat_jiffies()
    total_d = t1[0] - t0[0]
    idle_d = t1[1] - t0[1]
    if total_d <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - idle_d / total_d)))


def load1() -> float:
    with open("/proc/loadavg", encoding="utf-8") as fh:
        return float(fh.read().split()[0])


@dataclass
class HostSnapshot:
    mem_available_gib: float
    cpu_busy_pct: float
    load1: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "mem_available_gib": round(self.mem_available_gib, 3),
            "cpu_busy_pct": round(self.cpu_busy_pct, 1),
            "load1": round(self.load1, 2),
        }


def host_snapshot() -> HostSnapshot:
    return HostSnapshot(mem_available_gib(), cpu_busy_pct(), load1())


# --- darwin (macOS) host sampling (pure reads) -----------------------------------


def _run_checked(cmd: list[str]) -> str:
    """Run a read-only host command; ANY failure is a caller-visible QueueError
    (fail closed — an unmeasurable host is never silently treated as healthy)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise QueueError(f"{cmd[0]} failed: {exc}") from exc
    if r.returncode != 0:
        raise QueueError(f"{cmd[0]} failed: {r.stderr.strip() or f'exit {r.returncode}'}")
    return r.stdout


def parse_vm_stat(text: str) -> tuple[int, float]:
    """(page_size, available_bytes) from `vm_stat` output.

    available ≈ free + inactive + speculative pages — the honest "can we
    admit more work" number, the Darwin analog of Linux MemAvailable
    (purgeable pages are already counted inside free/inactive)."""
    m = re.search(r"page size of (\d+) bytes", text)
    if m is None:
        raise QueueError("vm_stat output has no page size — cannot sample memory")
    page_size = int(m.group(1))
    pages = 0
    for name in ("Pages free:", "Pages inactive:", "Pages speculative:"):
        pm = re.search(re.escape(name) + r"\s+(\d+)\.", text)
        if pm is None:
            raise QueueError(f"vm_stat output missing {name!r} — cannot sample memory")
        pages += int(pm.group(1))
    return page_size, float(pages * page_size)


def parse_loadavg_sysctl(text: str) -> float:
    """load1 from `sysctl -n vm.loadavg` output ('{ 3.75 3.01 3.83 }')."""
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if m is None:
        raise QueueError(f"vm.loadavg output unparsable: {text.strip()!r}")
    return float(m.group(1))


def parse_ps_cpu_pct(text: str, ncpu: int) -> float:
    """CPU busy % from `ps -A -o %cpu=` output, normalized by core count.

    ps %cpu is a per-process DECAYING AVERAGE, not an instantaneous sample
    like /proc/stat deltas: a burst shows up smeared over seconds. It is
    the honest approximation available without top(1)'s multi-second
    sampling loop; documented as such in the operations doc."""
    total = 0.0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            total += float(line)
        except ValueError:
            continue  # ps header noise / truncated line: never a sampling error
    return max(0.0, min(100.0, total / max(1, ncpu)))


def parse_ps_rss_sum_kib(text: str) -> int:
    """Total resident set size (KiB) of a process group from
    `ps -o rss= -g <pgid>` output (one rss value per group member)."""
    total = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            total += int(line)
        except ValueError:
            continue
    return total


def darwin_mem_available_gib() -> float:
    _page_size, available = parse_vm_stat(_run_checked(["vm_stat"]))
    return available / (1024 * 1024 * 1024)


def darwin_cpu_busy_pct() -> float:
    return parse_ps_cpu_pct(_run_checked(["ps", "-A", "-o", "%cpu="]), os.cpu_count() or 1)


def darwin_load1() -> float:
    return parse_loadavg_sysctl(_run_checked(["sysctl", "-n", "vm.loadavg"]))


def darwin_host_snapshot() -> HostSnapshot:
    return HostSnapshot(darwin_mem_available_gib(), darwin_cpu_busy_pct(), darwin_load1())


# --- admission / pressure ------------------------------------------------------


@dataclass
class AdmissionDecision:
    admitted: bool
    reason: str
    snapshot: HostSnapshot


def evaluate_admission(running: int, snap: HostSnapshot) -> AdmissionDecision:
    if running >= MAX_CONCURRENT_WORKERS:
        return AdmissionDecision(False, f"max concurrent workers ({running}) reached", snap)
    floor = ADMIT_SINGLE_MEM_AVAILABLE_GIB if running == 0 else ADMIT_MEM_AVAILABLE_GIB
    if snap.mem_available_gib < floor:
        return AdmissionDecision(
            False, f"MemAvailable {snap.mem_available_gib:.2f} GiB < {floor} GiB floor", snap
        )
    if snap.cpu_busy_pct >= ADMIT_CPU_BUSY_PCT:
        return AdmissionDecision(
            False, f"CPU busy {snap.cpu_busy_pct:.1f}% >= {ADMIT_CPU_BUSY_PCT}%", snap
        )
    if snap.load1 >= ADMIT_LOAD1:
        return AdmissionDecision(False, f"load1 {snap.load1:.2f} >= {ADMIT_LOAD1}", snap)
    return AdmissionDecision(True, "ok", snap)


def evaluate_pressure(samples: list[HostSnapshot]) -> tuple[bool, str]:
    """True when the last PRESSURE_SAMPLES samples ALL show pressure (sustained)."""
    if len(samples) < PRESSURE_SAMPLES:
        return False, ""
    recent = samples[-PRESSURE_SAMPLES:]
    if all(s.mem_available_gib < PRESSURE_MEM_AVAILABLE_GIB for s in recent):
        return True, f"sustained MemAvailable < {PRESSURE_MEM_AVAILABLE_GIB} GiB"
    if all(s.cpu_busy_pct > PRESSURE_CPU_BUSY_PCT for s in recent):
        return True, f"sustained CPU busy > {PRESSURE_CPU_BUSY_PCT}%"
    if all(s.load1 > PRESSURE_LOAD1 for s in recent):
        return True, f"sustained load1 > {PRESSURE_LOAD1}"
    return False, ""


# --- job spec / state -----------------------------------------------------------


def _spec_digest(raw: dict[str, Any]) -> str:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass
class JobSpec:
    id: str
    kind: str  # "claude" | "opencode"
    title: str = ""
    prompt: str = ""
    cwd: str = ""
    depends_on: list[str] = field(default_factory=list)
    timeout_s: float = DEFAULT_JOB_TIMEOUT_S
    allowed_tools: list[str] = field(default_factory=list)
    session_id: str | None = None  # resume pointer (excluded from the digest)

    @classmethod
    def from_manifest(cls, raw: dict[str, Any]) -> JobSpec:
        jid = raw.get("id")
        if not isinstance(jid, str) or not JOB_ID_RE.match(jid):
            raise QueueError(f"job id must be a non-empty slug: {jid!r}")
        kind = raw.get("kind", "claude")
        if kind not in ("claude", "opencode"):
            raise QueueError(f"job {jid}: unknown kind {kind!r} (claude|opencode)")
        # systemd normalizes WorkingDirectory; ownership compares it verbatim.
        cwd = os.path.abspath(raw.get("cwd") or os.getcwd())
        if not os.path.isdir(cwd):
            raise QueueError(f"job {jid}: cwd does not exist: {cwd}")
        depends = raw.get("depends_on", [])
        if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
            raise QueueError(f"job {jid}: depends_on must be a list of job ids")
        timeout = raw.get("timeout_s", DEFAULT_JOB_TIMEOUT_S)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise QueueError(f"job {jid}: timeout_s must be positive")
        return cls(
            id=jid,
            kind=kind,
            title=str(raw.get("title", jid)),
            prompt=str(raw.get("prompt", "")),
            cwd=cwd,
            depends_on=list(depends),
            timeout_s=float(timeout),
            allowed_tools=list(raw.get("allowed_tools", [])),
            session_id=raw.get("session_id"),
        )

    def digest(self) -> str:
        return _spec_digest(
            {
                "id": self.id,
                "kind": self.kind,
                "title": self.title,
                "prompt": self.prompt,
                "cwd": self.cwd,
                "depends_on": sorted(self.depends_on),
                "timeout_s": self.timeout_s,
                "allowed_tools": sorted(self.allowed_tools),
            }
        )

    def exe_path(self) -> str:
        return CLAUDE_BIN if self.kind == "claude" else OPENCODE_BIN


@dataclass
class JobState:
    status: str
    unit_name: str | None = None
    session_id: str | None = None
    exit_code: int | None = None
    started_at: float | None = None
    ended_at: float | None = None
    error: str | None = None
    attempts: int = 0
    # Darwin backend identity (ignored by the systemd backend): the worker runs
    # as a process GROUP; these four fields are the ownership record — pgid +
    # leader pid + leader start time + leader command line must ALL match
    # before any signal. Additive to schema 2: old state files default None.
    pgid: int | None = None
    leader_pid: int | None = None
    leader_start: str | None = None
    leader_cmd: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class QueueState:
    """Persistent queue state (JSON file, atomic writes, flock-guarded)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.unit_token: str = secrets.token_hex(8)
        self.jobs: dict[str, JobState] = {}
        self.job_digests: dict[str, str] = {}
        self.pressure_deferred: bool = False
        self.pressure_reason: str | None = None
        self._fh: Any = None

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise QueueError(f"state file {self.path} corrupt: {exc}") from exc
        if raw.get("schema") != STATE_SCHEMA_VERSION:
            raise QueueError(f"state file {self.path} schema {raw.get('schema')!r} unsupported")
        token = raw.get("unit_token")
        if not isinstance(token, str) or not UNIT_TOKEN_RE.match(token):
            raise QueueError(f"state file {self.path} has a corrupted unit token — refusing")
        self.unit_token = token
        self.pressure_deferred = bool(raw.get("pressure_deferred"))
        self.pressure_reason = raw.get("pressure_reason")
        digests = raw.get("job_digests", {})
        if not isinstance(digests, dict):
            raise QueueError(f"state file {self.path} job_digests corrupt")
        self.job_digests = {k: str(v) for k, v in digests.items()}
        jobs = raw.get("jobs", {})
        if not isinstance(jobs, dict):
            raise QueueError(f"state file {self.path} jobs corrupt")
        for jid, jraw in jobs.items():
            if not isinstance(jraw, dict) or jraw.get("status") not in JOB_STATUSES:
                raise QueueError(f"state file {self.path} job {jid} has invalid status — refusing")
            self.jobs[jid] = JobState(**jraw)

    def save(self) -> None:
        payload = {
            "schema": STATE_SCHEMA_VERSION,
            "unit_token": self.unit_token,
            "pressure_deferred": self.pressure_deferred,
            "pressure_reason": self.pressure_reason,
            "job_digests": self.job_digests,
            "jobs": {jid: js.to_dict() for jid, js in self.jobs.items()},
        }
        # Durable write: temp file + fsync + atomic rename + directory fsync —
        # a crash mid-write can never leave a truncated/corrupt state file
        # (load() would refuse it and the run token would be lost).
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        try:
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
        except OSError:
            return  # e.g. parent gone: the rename above is still atomic
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    # -- single-supervisor lock (acquire BEFORE load) ---------------------------

    def acquire_lock(self) -> None:
        if fcntl is None:
            raise QueueError("flock unavailable — refusing to run unsupervised (fail closed)")
        lock_path = self.path.with_suffix(".lock")
        self._fh = open(lock_path, "w", encoding="utf-8")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fh.close()
            self._fh = None
            raise QueueError(
                f"another queue supervisor holds {lock_path} — refusing concurrent supervision"
            ) from exc

    def release_lock(self) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None

    @property
    def control_path(self) -> Path:
        return self.path.with_suffix(".control")

    # -- helpers ------------------------------------------------------------------

    def running_count(self) -> int:
        return sum(1 for js in self.jobs.values() if js.status == "running")

    def unit_for(self, job: JobSpec) -> str:
        # Unit names carry the run token: ownership validation requires the
        # persisted token in the name AND matching unit properties.
        return f"{UNIT_PREFIX}-{self.unit_token}-{job.id}.service"

    def description_for(self, job: JobSpec) -> str:
        # Unforgeable-ish identity carried in the unit Description and
        # re-verified before any stop/signal call. The spec-digest label is
        # `spec_digest=` (what it actually is); older versions wrote the same
        # value under the misleading name `exe=` — see description_matches.
        return (
            f"aci worker queue token={self.unit_token} job={job.id} "
            f"{UNIT_DESC_SPEC_LABEL}={job.digest()}"
        )

    def description_matches(self, job: JobSpec, description: str) -> bool:
        """A unit Description identifies this run's job when it matches the
        CURRENT spec-digest label OR the legacy `exe=` label written by an
        older version — units of a RUNNING supervisor (started before an
        upgrade) must stay recognizable/interruptible, not become orphans."""
        if description == self.description_for(job):
            return True
        legacy = (
            f"aci worker queue token={self.unit_token} job={job.id} "
            f"{UNIT_DESC_SPEC_LABEL_LEGACY}={job.digest()}"
        )
        return description == legacy


# --- systemd --------------------------------------------------------------------


def _systemctl(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout
    )


def systemd_available() -> bool:
    if shutil.which("systemctl") is None or shutil.which("systemd-run") is None:
        return False
    if (
        "DBUS_SESSION_BUS_ADDRESS" not in os.environ
        and not Path(f"/run/user/{os.getuid()}").exists()
    ):
        return False
    try:
        r = _systemctl(["is-system-running"])
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0


def unit_properties(unit: str, props: list[str]) -> dict[str, str]:
    """Read unit properties via systemctl show (no pager, no extra deps)."""
    args = ["show", unit]
    for p in props:
        args.extend(["-p", p])
    try:
        r = _systemctl(args)
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if r.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def verify_unit_resource_limits(unit: str) -> tuple[bool, str]:
    """Verify the EXACT resource properties before claiming protection."""
    props = unit_properties(
        unit,
        [
            "LoadState",
            "MemoryHigh",
            "MemoryMax",
            "MemorySwapMax",
            "CPUQuotaPerSecUSec",
            "TasksMax",
            "OOMPolicy",
            "Nice",
        ],
    )
    if not props or props.get("LoadState") != "loaded":
        return False, f"unit {unit} not loaded"
    expected = {
        "MemoryHigh": str(UNIT_MEMORY_HIGH_BYTES),
        "MemoryMax": str(UNIT_MEMORY_MAX_BYTES),
        "MemorySwapMax": str(UNIT_MEMORY_SWAP_MAX_BYTES),
        "CPUQuotaPerSecUSec": UNIT_CPU_QUOTA_PER_SEC_USEC_STR,
        "TasksMax": str(UNIT_TASKS_MAX),
        "OOMPolicy": UNIT_OOM_POLICY,
        "Nice": str(UNIT_NICE),
    }
    for prop, want in expected.items():
        got = props.get(prop, "")
        if got != want:
            return False, f"{prop}={got!r} != expected {want!r}"
    return True, "ok"


def validate_ownership(state: QueueState, job: JobSpec, unit: str) -> tuple[bool, str]:
    """A unit may only be touched when name, Description token, cwd AND exe match."""
    expected = state.unit_for(job)
    if unit != expected:
        return False, f"unit {unit} is not this run's unit {expected}"
    if not UNIT_TOKEN_RE.match(state.unit_token):
        return False, "state unit token corrupted"
    props = unit_properties(unit, ["LoadState", "Description", "WorkingDirectory", "ExecStart"])
    if props.get("LoadState") != "loaded":
        return False, f"unit {unit} not loaded"
    if not state.description_matches(job, props.get("Description", "")):
        return False, f"unit {unit} Description does not match this run's job"
    wd = props.get("WorkingDirectory", "")
    if job.cwd and wd != job.cwd:
        return False, f"unit {unit} WorkingDirectory {wd!r} != job cwd {job.cwd!r}"
    exe = props.get("ExecStart", "")
    if job.exe_path() not in exe:
        return False, f"unit {unit} ExecStart does not contain {job.exe_path()}"
    return True, "ok"


def start_worker_unit(
    state: QueueState,
    job: JobSpec,
    argv: list[str],
    env: dict[str, str],
    log_path: Path,
    receipt_path: Path,
) -> str:
    """Start the job inside its own transient systemd user service with limits."""
    unit = state.unit_for(job)
    cmd = [
        "systemd-run",
        "--user",
        f"--unit={unit}",
        f"--description={state.description_for(job)}",
        # systemd (>= 254) expands ${VAR}/$VAR/$$ in service command lines by
        # default: job argv (prompt, title, tools) must reach the CLI verbatim.
        "--expand-environment=no",
        f"--property=MemoryHigh={UNIT_MEMORY_HIGH_BYTES}",
        f"--property=MemoryMax={UNIT_MEMORY_MAX_BYTES}",
        f"--property=MemorySwapMax={UNIT_MEMORY_SWAP_MAX_BYTES}",
        f"--property=CPUQuota={UNIT_CPU_QUOTA_PCT}%",
        f"--property=TasksMax={UNIT_TASKS_MAX}",
        f"--property=OOMPolicy={UNIT_OOM_POLICY}",
        f"--property=Nice={UNIT_NICE}",
        f"--property=WorkingDirectory={job.cwd}",
        # systemd ends the worker even if the supervisor itself dies.
        f"--property=RuntimeMaxSec={job.timeout_s + RUNTIME_MAX_MARGIN_S}",
    ]
    full_env = dict(env)
    full_env["ACIQ_LOG"] = str(log_path)
    full_env["ACIQ_RECEIPT"] = str(receipt_path)
    for k, v in full_env.items():
        cmd.append(f"--setenv={k}={v}")
    cmd.extend(["/bin/sh", "-c", WRAPPER_SH, "worker", *argv])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise QueueError(f"systemd-run failed for {unit}: {r.stderr.strip()}")
    return unit


def stop_worker_unit(unit: str) -> bool:
    try:
        return _systemctl(["stop", unit]).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def send_sigint_to_unit(unit: str) -> bool:
    try:
        return _systemctl(["kill", "--signal=SIGINT", unit]).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def unit_active(unit: str) -> bool:
    props = unit_properties(unit, ["ActiveState"])
    if not props:
        # Query failed (timeout/D-Bus): UNKNOWN is not "exited" — keep the slot
        # occupied; the job timeout + ownership-validated interrupt still bound it.
        # (A unit that is really gone reports ActiveState=inactive.)
        return True
    return props.get("ActiveState") in ("active", "activating", "deactivating")


def cleanup_finished_unit(state: QueueState, job: JobSpec, unit: str) -> None:
    """Best-effort reset of a finished transient unit — ONLY after ownership
    validation. `reset-failed` clears systemd's failed-unit bookkeeping; doing
    it to a unit we do not own would clear SOMEONE ELSE's failure state."""
    ok, why = validate_ownership(state, job, unit)
    if not ok:
        if "not loaded" in why:
            # A cleanly-exited transient unit is already gone: nothing to reset.
            return
        log.error("refusing reset-failed of %s: %s", unit, why)
        return
    try:
        _systemctl(["reset-failed", unit], timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        pass


def read_exit_receipt(receipt_path: Path) -> int | None:
    try:
        return int(receipt_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def parse_run_log(log_path: Path, require_result: bool = False) -> tuple[str | None, bool]:
    """Extract (session_id, is_error) from a claude stream-json / opencode log."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, require_result
    return parse_run_log_text(text, require_result)


def parse_run_log_text(text: str, require_result: bool = False) -> tuple[str | None, bool]:
    """(session_id, is_error). With require_result (claude stream-json), a log
    with NO result event — e.g. a plain-text CLI/gateway error on stdout — or a
    non-success result subtype is an error: completion must be verifiable."""
    session: str | None = None
    is_error = False
    saw_result = False
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        sid = obj.get("session_id")
        if not isinstance(sid, str):
            sid = obj.get("sessionID")  # opencode --format=json events
        if isinstance(sid, str) and sid and session is None:
            session = sid
        if obj.get("type") == "error":
            # opencode emits {"type":"error",...} events (e.g. an invalid
            # session or gateway failure) — never a success.
            is_error = True
        if obj.get("type") == "result":
            saw_result = True
            if isinstance(sid, str):
                session = sid
            subtype = obj.get("subtype", "success")
            is_error = bool(obj.get("is_error", False)) or subtype != "success"
    if require_result and not saw_result:
        is_error = True
    return session, is_error


# --- darwin (macOS) backend --------------------------------------------------------


def darwin_worker_env(job_env: dict[str, str]) -> dict[str, str]:
    """A defined minimal worker environment (the Darwin analog of the systemd
    user manager's environment): the session basics a CLI needs, then the
    job's explicit vars on top (PWD included — the CLIs resolve their
    project from $PWD, not getcwd())."""
    base: dict[str, str] = {}
    for key in ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "TZ"):
        val = os.environ.get(key)
        if val:
            base[key] = val
    return {**base, **job_env}


class DarwinHostControls:
    """macOS worker backend — the SAME scheduler contract as HostControls.

    There is no systemd and no cgroup on macOS, so the containment
    guarantees are rebuilt from process primitives, honestly:

    - each worker runs in its OWN process group/session (start_new_session)
      under `nice -n 10`, through the SAME /bin/sh exit-receipt wrapper;
    - the per-worker memory cap is enforced by POLLING the process-group
      RSS (`ps -o rss= -g <pgid>`) and SIGINT -> grace -> SIGKILL of the
      whole GROUP — the kernel does not enforce RLIMIT_AS on macOS;
    - ownership before ANY signal: the recorded pgid, leader pid, leader
      start time AND command line must all match (never signal by name);
    - a wall-clock timeout interrupts the group exactly like the Linux
      timeout path (the scheduler owns the clock).

    Honest differences vs the systemd backend (no kernel memory cap, no
    cgroup accounting of escaped double-forked children, ...) are listed in
    docs/operations/aci-worker-queue.md (macOS section).
    """

    def __init__(self) -> None:
        # handle -> Popen of the DIRECT child (the sh wrapper). poll() both
        # reaps the zombie and answers liveness. Adopted (post-restart)
        # workers have no Popen here and are checked via ps.
        self._popens: dict[str, Any] = {}
        # handle -> the nice value `nice -n 10` must have produced (verified
        # numerically after spawn, like the systemd unit properties).
        self._expected_nice: dict[str, int] = {}

    # -- overridable process primitives (tests stub these) ----------------------

    def _ps(self, args: list[str]) -> str:
        """ps(1) query. ps exits 1 with EMPTY output for a missing pid/group —
        that is 'gone', not an error; callers parse the text."""
        try:
            r = subprocess.run(["ps", *args], capture_output=True, text=True, timeout=10)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise QueueError(f"ps {args} failed: {exc}") from exc
        return r.stdout

    def _spawn(
        self, cmd: list[str], env: dict[str, str], cwd: str, log_path: Path
    ) -> subprocess.Popen[bytes]:
        """Start the worker: own session (pgid == pid), nice -n 10. The sh
        wrapper's OWN stderr (e.g. an exec failure) is appended to the
        per-attempt log — evidence, never lost."""
        log_fh = open(log_path, "ab")
        try:
            return subprocess.Popen(
                cmd,
                env=env,
                cwd=cwd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=log_fh,
            )
        finally:
            log_fh.close()

    def _kill(self, pgid: int, sig: int) -> bool:
        """Signal a whole process GROUP by its NUMERIC id (never by name).
        A group that already exited is success (the interrupt goal is met).
        A non-positive pgid is refused — kill(-0) would signal THIS process's
        own group (only reachable via a corrupted state file)."""
        if pgid <= 0:
            log.error("refusing to signal non-positive process group %r", pgid)
            return False
        try:
            os.kill(-pgid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return True

    def _getpriority(self, pid: int) -> int:
        return os.getpriority(os.PRIO_PROCESS, pid)

    @staticmethod
    def _pgid_of(unit: str) -> int | None:
        """The numeric process-group id from a worker handle — None for a
        malformed handle or a non-positive pgid (kill(-0) would signal THIS
        process's own group; only reachable via a corrupted state file)."""
        m = DARWIN_HANDLE_RE.match(unit)
        if m is None:
            return None
        pgid = int(m.group(1))
        return pgid if pgid > 0 else None

    # -- backend contract ---------------------------------------------------------

    def available(self) -> bool:
        """Fail closed unless every primitive the backend needs exists."""
        if platform.system() != "Darwin":
            log.warning("darwin backend selected on non-macOS (%s)", platform.system())
            return False
        missing = [t for t in ("ps", "sysctl", "vm_stat", "nice") if shutil.which(t) is None]
        missing += [p for p in ("/bin/sh", "/usr/bin/nice") if not Path(p).exists()]
        if missing:
            log.warning("darwin backend tools missing: %s", ", ".join(missing))
            return False
        return True

    def snapshot(self) -> HostSnapshot:
        return darwin_host_snapshot()

    def start_worker_unit(
        self,
        state: QueueState,
        job: JobSpec,
        argv: list[str],
        env: dict[str, str],
        log_path: Path,
        receipt_path: Path,
    ) -> str:
        """Start the job in its OWN process group/session under nice -n 10,
        through the same /bin/sh exit-receipt wrapper, and record the
        ownership identity (pgid/leader pid/start time/command line) into
        the PERSISTENT job state BEFORE anything can signal it."""
        js = state.jobs.get(job.id)
        if js is None:
            raise QueueError(f"job {job.id} has no state — cannot record worker identity")
        full_env = darwin_worker_env(env)
        full_env["ACIQ_LOG"] = str(log_path)
        full_env["ACIQ_RECEIPT"] = str(receipt_path)
        cmd = [
            "/usr/bin/nice",
            "-n",
            str(DARWIN_WORKER_NICE),
            "/bin/sh",
            "-c",
            WRAPPER_SH,
            "worker",
            *argv,
        ]
        supervisor_nice = self._getpriority(0)
        popen = self._spawn(cmd, full_env, job.cwd, log_path)
        handle = f"pgid-{popen.pid}"
        self._popens[handle] = popen
        # `nice -n 10` INCREMENTS the parent's nice (capped at 20): the exact
        # expected value is recorded and verified numerically after spawn.
        self._expected_nice[handle] = min(20, supervisor_nice + DARWIN_WORKER_NICE)
        # start_new_session makes the child the group leader: pgid == pid.
        js.pgid = popen.pid
        js.leader_pid = popen.pid
        try:
            js.leader_start = self._ps(["-ww", "-o", "lstart=", "-p", str(popen.pid)]).strip()
            js.leader_cmd = self._ps(["-ww", "-o", "command=", "-p", str(popen.pid)]).strip()
        except QueueError:
            # a worker we cannot IDENTIFY must never keep running unowned
            self._kill(popen.pid, signal.SIGKILL)
            raise
        return handle

    def verify_unit_resource_limits(self, unit: str) -> tuple[bool, str]:
        """Verify the containment properties that CAN be verified on Darwin
        before the job is allowed to run (fail closed on mismatch, like the
        systemd path): the worker leads its OWN process group and the nice
        increment applied. The memory cap is NOT verifiable here — macOS has
        no kernel enforcement; it is enforced by poll_worker_limits."""
        pid = self._pgid_of(unit)
        if pid is None:
            return False, f"bad worker handle {unit!r}"
        popen = self._popens.get(unit)
        if popen is not None and popen.poll() is not None:
            return False, f"worker leader {pid} exited during start verification"
        pgid_out = self._ps(["-o", "pgid=", "-p", str(pid)]).strip()
        if not pgid_out:
            return False, f"worker leader {pid} not found — group verification failed"
        if int(pgid_out) != pid:
            return False, f"worker {pid} is not a process-group leader (pgid={pgid_out})"
        expected_nice = self._expected_nice.get(unit)
        if expected_nice is None:
            return False, f"worker {pid} has no recorded nice expectation"
        nice = self._getpriority(pid)
        if nice < expected_nice:
            return False, f"worker nice {nice} < expected {expected_nice} — nice not applied"
        return True, "ok"

    def validate_ownership(self, state: QueueState, job: JobSpec, unit: str) -> tuple[bool, str]:
        """A worker may only be touched when the RECORDED identity matches the
        LIVE process: the handle carries the recorded pgid, the leader pid is
        alive AND in the recorded process group, the leader start time matches
        (pid-reuse protection), and the leader command line matches the
        recorded one AND contains the expected CLI binary. Never by name."""
        js = state.jobs.get(job.id)
        if js is None:
            return False, f"job {job.id} has no state"
        if unit != js.unit_name:
            return False, f"worker handle {unit!r} is not job {job.id}'s recorded {js.unit_name!r}"
        pgid, pid = js.pgid, js.leader_pid
        if pgid is None or pid is None or pgid != pid or pgid <= 0:
            return False, f"job {job.id} has no complete recorded worker identity"
        if self._pgid_of(unit) != pgid:
            return False, f"worker handle {unit!r} does not carry the recorded pgid {pgid}"
        # 1. the leader is alive and still in the recorded group
        pgid_out = self._ps(["-o", "pgid=", "-p", str(pid)]).strip()
        if not pgid_out:
            return False, f"worker leader {pid} is gone"
        if int(pgid_out) != pgid:
            return False, f"pid {pid} is not in the recorded process group {pgid} (pgid={pgid_out})"
        # 2. the leader start time matches — a reused pid is NOT our worker
        lstart = self._ps(["-ww", "-o", "lstart=", "-p", str(pid)]).strip()
        if js.leader_start and lstart != js.leader_start:
            return False, f"worker leader {pid} start time changed (pid reused?): {lstart!r}"
        # 3. the command line matches the recorded one and carries the CLI
        command = self._ps(["-ww", "-o", "command=", "-p", str(pid)]).strip()
        if js.leader_cmd and command != js.leader_cmd:
            return False, f"worker leader {pid} command line changed: {command[:120]!r}"
        if job.exe_path() not in command:
            return False, f"worker leader {pid} command line does not contain {job.exe_path()}"
        return True, "ok"

    def send_sigint_to_unit(self, unit: str) -> bool:
        """SIGINT the whole process GROUP by its recorded numeric pgid
        (the scheduler validates ownership BEFORE calling this)."""
        pgid = self._pgid_of(unit)
        if pgid is None:
            log.error("refusing SIGINT to malformed handle %r", unit)
            return False
        return self._kill(pgid, signal.SIGINT)

    def stop_worker_unit(self, unit: str) -> bool:
        """SIGKILL the whole process GROUP by its recorded numeric pgid
        (the Linux analog: systemctl stop's final kill of the cgroup)."""
        pgid = self._pgid_of(unit)
        if pgid is None:
            log.error("refusing SIGKILL to malformed handle %r", unit)
            return False
        return self._kill(pgid, signal.SIGKILL)

    def unit_active(self, unit: str) -> bool:
        popen = self._popens.get(unit)
        if popen is not None:
            # direct child: poll() reaps the zombie AND answers liveness
            return popen.poll() is None
        pid = self._pgid_of(unit)
        if pid is None:
            return False
        # adopted worker (started by a previous supervisor): leader-alive via
        # ps. A reused pid is indistinguishable HERE — validate_ownership
        # (which checks the recorded start time) guards every signal path.
        return bool(self._ps(["-o", "pid=", "-p", str(pid)]).strip())

    def poll_worker_limits(self, job: JobSpec, js: JobState) -> str | None:
        """Darwin memory cap: the kernel does not enforce RLIMIT_AS, so the
        per-worker cap is enforced HERE — poll the process-group RSS and
        report a violation (the scheduler then SIGINTs the group, waits the
        grace window, and SIGKILLs the stragglers)."""
        pgid = self._pgid_of(js.unit_name or "")
        if pgid is None or js.pgid is None or pgid != js.pgid:
            return None
        rss_kib = parse_ps_rss_sum_kib(self._ps(["-o", "rss=", "-g", str(js.pgid)]))
        if rss_kib <= 0:
            return None  # group gone: unit_active finalizes the job
        rss_bytes = rss_kib * 1024
        if rss_bytes > DARWIN_MEM_CAP_BYTES:
            return (
                f"memory cap exceeded: process-group RSS {rss_bytes // (1024 * 1024)} MiB"
                f" > cap {DARWIN_MEM_CAP_BYTES // (1024 * 1024)} MiB"
            )
        return None

    def cleanup_finished_unit(self, state: QueueState, job: JobSpec, unit: str) -> None:
        """No systemd bookkeeping exists on macOS — nothing to clean up. The
        method exists so the scheduler stays backend-agnostic."""
        return None

    def read_exit_receipt(self, receipt_path: Path) -> int | None:
        return read_exit_receipt(receipt_path)

    def parse_run_log(
        self, log_path: Path, require_result: bool = False
    ) -> tuple[str | None, bool]:
        return parse_run_log(log_path, require_result)


# --- backend selection ---------------------------------------------------------------

BACKENDS = ("systemd", "darwin")


def select_backend(flag: str | None = None) -> str:
    """Backend selection: an explicit flag wins, else the platform default
    (darwin on macOS, systemd on Linux)."""
    if flag is not None:
        if flag not in BACKENDS:
            raise QueueError(f"unknown backend {flag!r} (one of {'|'.join(BACKENDS)})")
        return flag
    return "darwin" if platform.system() == "Darwin" else "systemd"


def make_host_controls(backend: str) -> HostControls | DarwinHostControls:
    if backend == "darwin":
        return DarwinHostControls()
    return HostControls()


# --- CLI invocation builders ------------------------------------------------------


def build_claude_argv(job: JobSpec, session_id: str, *, resume: bool = False) -> list[str]:
    argv = [
        CLAUDE_BIN,
        "-p",
        f"--model={MODEL_ID}",
        "--output-format=stream-json",
        "--verbose",
        "--permission-mode=acceptEdits",
        "--permission-prompts=none",
        "--strict-mcp-config",
        '--mcp-config={"mcpServers":{}}',
        "--setting-sources=user,project",
    ]
    # The caller resolves the session from PERSISTED state (JobState), not the
    # manifest spec: re-entry must use the exact persisted id with --resume.
    if resume:
        argv.append(f"--resume={session_id}")
    else:
        argv.append(f"--session-id={session_id}")
    for t in job.allowed_tools:
        argv.append(f"--allowedTools={t}")
    if job.prompt:
        argv.append(job.prompt)
    return argv


def build_claude_env(job: JobSpec) -> dict[str, str]:
    env = {
        "CLAUDE_CODE_SUBAGENT_MODEL": MODEL_ID,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": MODEL_ID,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": MODEL_ID,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": MODEL_ID,
        # PWD must be exported explicitly: the CLIs resolve their project from
        # $PWD, not getcwd() (AGENTS.md gotcha). Thread pins keep BLAS/OMP
        # single-threaded inside the 200% CPU quota.
        "PWD": job.cwd,
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    return env


def new_opencode_session_id() -> str:
    return f"ses_{secrets.token_hex(12)}"


def build_opencode_argv(job: JobSpec, session_id: str) -> list[str]:
    # --auto: a headless run must never block on an interactive permission
    # prompt (explicitly denied permissions stay denied). --session creates the
    # session on first use and continues it on re-entry (verified live).
    argv = [
        OPENCODE_BIN,
        "run",
        "--standalone",
        "--auto",
        f"--model={OPENCODE_MODEL_ID}",
        "--format=json",
        f"--session={session_id}",
        f"--title={job.title or job.id}",
    ]
    if job.prompt:
        argv.append(job.prompt)
    return argv


def build_opencode_env(job: JobSpec) -> dict[str, str]:
    return {"PWD": job.cwd, "OMP_NUM_THREADS": "1"}


# --- host controls (mockable in tests) ----------------------------------------------


class HostControls:
    """Thin wrapper over the systemd/proc helpers so tests can mock the host."""

    def systemd_available(self) -> bool:
        return systemd_available()

    def available(self) -> bool:
        """Backend availability — the scheduler's fail-closed gate (the
        generic seam both backends implement)."""
        return self.systemd_available()

    def snapshot(self) -> HostSnapshot:
        return host_snapshot()

    def poll_worker_limits(self, job: JobSpec, js: JobState) -> str | None:
        """systemd enforces the per-worker limits IN THE KERNEL
        (MemoryMax/CPUQuota/TasksMax, verified numerically at start) — there
        is nothing to poll while the worker runs. The Darwin backend uses
        this hook to enforce its polled memory cap."""
        return None

    def verify_unit_resource_limits(self, unit: str) -> tuple[bool, str]:
        return verify_unit_resource_limits(unit)

    def validate_ownership(self, state: QueueState, job: JobSpec, unit: str) -> tuple[bool, str]:
        return validate_ownership(state, job, unit)

    def start_worker_unit(
        self,
        state: QueueState,
        job: JobSpec,
        argv: list[str],
        env: dict[str, str],
        log_path: Path,
        receipt_path: Path,
    ) -> str:
        return start_worker_unit(state, job, argv, env, log_path, receipt_path)

    def stop_worker_unit(self, unit: str) -> bool:
        return stop_worker_unit(unit)

    def send_sigint_to_unit(self, unit: str) -> bool:
        return send_sigint_to_unit(unit)

    def unit_active(self, unit: str) -> bool:
        return unit_active(unit)

    def cleanup_finished_unit(self, state: QueueState, job: JobSpec, unit: str) -> None:
        return cleanup_finished_unit(state, job, unit)

    def read_exit_receipt(self, receipt_path: Path) -> int | None:
        return read_exit_receipt(receipt_path)

    def parse_run_log(
        self, log_path: Path, require_result: bool = False
    ) -> tuple[str | None, bool]:
        return parse_run_log(log_path, require_result)


# --- scheduler ------------------------------------------------------------------------


class WorkerQueue:
    """Single polling scheduler: admission, dependency gating, unit polling.

    All waits go through the injected sleep_fn/clock so tests run in virtual
    time; production uses time.sleep/time.time.
    """

    def __init__(
        self,
        state: QueueState,
        jobs: list[JobSpec],
        logs_dir: Path,
        *,
        dry_run: bool = False,
        host_ctl: HostControls | DarwinHostControls | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.state = state
        self.jobs = {j.id: j for j in jobs}
        self.logs_dir = logs_dir
        self.dry_run = dry_run
        self.host_ctl = host_ctl or HostControls()
        self.sleep = sleep_fn
        self.clock = clock
        self._shutdown = False
        self._pressure_samples: list[HostSnapshot] = []
        self._last_sample: float | None = None
        self._active: dict[str, tuple[JobSpec, JobState]] = {}
        # NOTE: signal handlers are NOT installed here — only cmd_run installs
        # them for a real run (dry-run/tests must not touch process signals).

    def install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM checkpoint handlers (real runs only).

        Returns nothing but records the previous handlers so
        restore_signal_handlers() can put them back (cmd_run does)."""
        self._prev_handlers: dict[int, Any] = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._prev_handlers[sig] = signal.signal(sig, self._handle_signal)
            except (ValueError, OSError):  # pragma: no cover - non-main-thread
                pass

    def restore_signal_handlers(self) -> None:
        for sig, handler in getattr(self, "_prev_handlers", {}).items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):  # pragma: no cover - non-main-thread
                pass
        self._prev_handlers = {}

    def _handle_signal(self, signum: Any, frame: Any) -> None:
        log.warning("received signal %s — interrupting owned units and exiting", signum)
        self._shutdown = True

    # -- dependency gating --------------------------------------------------------

    def _deps_satisfied(self, job: JobSpec) -> tuple[bool, str]:
        for dep in job.depends_on:
            if dep not in self.jobs:
                return False, f"unknown dependency {dep}"
            dep_state = self.state.jobs.get(dep)
            if dep_state is None or dep_state.status not in DEP_SATISFIED_STATUSES:
                st = dep_state.status if dep_state else "never-run"
                return False, f"dependency {dep} status={st}"
        return True, "ok"

    def _deps_blocked_terminal(self, job: JobSpec) -> str | None:
        """A reason string when a dependency ended in a NON-satisfying terminal
        state (failed/deferred/interrupted) — the dependent must not wait forever."""
        for dep in job.depends_on:
            dep_state = self.state.jobs.get(dep)
            if dep_state is not None and dep_state.status in ("failed", "deferred", "interrupted"):
                return f"dependency {dep} status={dep_state.status}"
        return None

    # -- main loop ------------------------------------------------------------------

    def run(self) -> int:
        if not self.dry_run and not self.host_ctl.available():
            raise QueueError("worker backend unavailable — FAIL CLOSED, refusing to run unbounded")
        self._reconcile_previous_run()
        rc = 0
        while True:
            if self._consume_control_request():
                self._interrupt_active(reason="operator defer-all request", defer_queued=True)
                rc = EXIT_DEFERRED
                break
            if self._shutdown:
                self._interrupt_active(reason="supervisor signal", defer_queued=False)
                rc = EXIT_SIGNALLED
                break
            # finalize exited units / apply timeouts / enforce per-worker limits
            for _jid, (job, js) in list(self._active.items()):
                unit = js.unit_name or ""
                if not self.host_ctl.unit_active(unit):
                    self._finalize_job(job, js, unit)
                    continue
                # backend limit enforcement while the worker runs (the Darwin
                # memory cap; a no-op on systemd, whose limits are in-kernel)
                violation = self.host_ctl.poll_worker_limits(job, js)
                if violation:
                    log.warning("job %s resource violation: %s", job.id, violation)
                    self._interrupt_job(job, js, unit, reason=violation, graceful=True)
                    continue
                if js.started_at is not None and (self.clock() - js.started_at >= job.timeout_s):
                    log.warning("job %s exceeded timeout (%ss)", job.id, job.timeout_s)
                    self._interrupt_job(
                        job, js, unit, reason=f"timeout after {job.timeout_s}s", graceful=True
                    )
            # pressure sampling (spaced PRESSURE_INTERVAL_S of virtual time)
            now = self.clock()
            if self._last_sample is None or now - self._last_sample >= PRESSURE_INTERVAL_S:
                self._last_sample = now
                self._pressure_samples.append(self.host_ctl.snapshot())
                overflow = len(self._pressure_samples) - PRESSURE_HISTORY_MAX
                if overflow > 0:
                    del self._pressure_samples[:overflow]
                pressured, reason = evaluate_pressure(self._pressure_samples)
                if pressured:
                    log.warning("sustained pressure: %s — deferring remaining work", reason)
                    self._interrupt_active(reason=f"host pressure: {reason}", defer_queued=True)
                    self.state.pressure_deferred = True
                    self.state.pressure_reason = reason
                    rc = EXIT_DEFERRED
                    break
            # admission (max 2 active, deps gated)
            self._try_admit()
            if not self._active and not self._has_pending():
                break
            self.sleep(POLL_INTERVAL_S)
        self.state.save()
        if rc == 0 and self._all_deferred():
            # Nothing ran to any terminal state: every job of this run ended
            # DEFERRED (admission/dependency) — same honest signal as a
            # pressure deferral, not a silent "all good" 0.
            log.info("all jobs deferred by admission — exiting %d", EXIT_DEFERRED)
            rc = EXIT_DEFERRED
        return rc

    def _all_deferred(self) -> bool:
        """True when EVERY manifest job of this run ended deferred — nothing
        reached finished/failed/interrupted/verified."""
        if not self.jobs:
            return False
        return all(
            (self.state.jobs[jid].status == "deferred") if jid in self.state.jobs else False
            for jid in self.jobs
        )

    def _has_pending(self) -> bool:
        return any(js.status == "queued" for jid, js in self.state.jobs.items() if jid in self.jobs)

    def _try_admit(self) -> None:
        for job in self._topological_order():
            js = self.state.jobs.get(job.id)
            if js is None or js.status != "queued":
                continue
            if len(self._active) >= MAX_CONCURRENT_WORKERS:
                break
            blocked = self._deps_blocked_terminal(job)
            if blocked:
                js.status = "deferred"
                js.error = blocked
                self.state.save()
                continue
            ok, dep_reason = self._deps_satisfied(job)
            if not ok:
                continue  # dependency still running: leave queued, recheck later
            decision = evaluate_admission(len(self._active), self.host_ctl.snapshot())
            if not decision.admitted:
                if self._active:
                    # Refused only while our own workers run: WAIT for a slot
                    # (recheck next poll); sustained pressure is handled above.
                    break
                js.status = "deferred"
                js.error = decision.reason
                self.state.save()
                continue
            self._start_job(job, js)

    def _topological_order(self) -> list[JobSpec]:
        ordered: list[JobSpec] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(jid: str) -> None:
            if jid in visited:
                return
            if jid in visiting:
                raise QueueError(f"dependency cycle involving {jid}")
            visiting.add(jid)
            for dep in self.jobs[jid].depends_on:
                if dep not in self.jobs:
                    raise QueueError(f"job {jid} depends on unknown job {dep}")
                visit(dep)
            visiting.discard(jid)
            visited.add(jid)
            ordered.append(self.jobs[jid])

        for jid in self.jobs:
            visit(jid)
        return ordered

    # -- start / finalize / interrupt ------------------------------------------------

    def _attempt_log_path(self, job: JobSpec, js: JobState) -> Path:
        """Per-attempt log: <job>.attempt-N.log (N = the current attempt).
        Every attempt gets its OWN file — earlier evidence is never truncated."""
        return self.logs_dir / f"{job.id}.attempt-{js.attempts}.log"

    def _promote_latest_log(self, job: JobSpec, js: JobState) -> None:
        """Keep <job>.log as the LATEST attempt's log (a COPY — the per-attempt
        file stays untouched as the durable record). Best-effort."""
        if js.attempts < 1:
            return
        attempt_log = self._attempt_log_path(job, js)
        latest = self.logs_dir / f"{job.id}.log"
        try:
            if attempt_log.exists():
                shutil.copyfile(attempt_log, latest)
        except OSError:
            pass  # best-effort convenience copy; the attempt log is the record

    def _start_job(self, job: JobSpec, js: JobState) -> None:
        receipt_path = self.logs_dir / f"{job.id}.exit"
        # A previous attempt's receipt must never be read as THIS attempt's exit
        # code (e.g. an OOM kill takes the wrapper down before it writes one).
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        receipt_path.unlink(missing_ok=True)
        # The attempt counter is bumped BEFORE the per-attempt log path is
        # derived from it: this attempt writes <job>.attempt-N.log and never
        # truncates an earlier attempt's evidence.
        js.attempts += 1
        log_path = self._attempt_log_path(job, js)
        # Resume the persisted session when a previous attempt actually spawned
        # (unit_name is only set after a successful systemd-run), or the
        # manifest's explicit resume pointer; otherwise create a fresh session.
        resume = False
        if js.session_id and js.unit_name:
            session_id, resume = js.session_id, True
        elif job.session_id:
            session_id, resume = job.session_id, True
        elif js.session_id:
            session_id = js.session_id  # generated but never spawned
        elif job.kind == "claude":
            session_id = str(uuid.uuid4())
        else:
            # OpenCode validates session ids: they must start with "ses"
            # (verified live, v2.0.21); --session creates it when absent.
            session_id = new_opencode_session_id()
        # Persist identity BEFORE spawn: unit name, session, running status.
        js.status = "running"
        js.session_id = session_id
        js.started_at = self.clock()
        js.error = None
        js.exit_code = None
        js.ended_at = None
        if job.kind == "claude":
            argv = build_claude_argv(job, session_id, resume=resume)
            env = build_claude_env(job)
        else:
            argv = build_opencode_argv(job, session_id)
            env = build_opencode_env(job)
        try:
            unit = self.host_ctl.start_worker_unit(
                self.state, job, argv, env, log_path, receipt_path
            )
        except QueueError as exc:
            js.status = "failed"
            js.error = f"start failed: {exc}"
            js.ended_at = self.clock()
            self.state.save()
            return
        js.unit_name = unit
        self.state.save()
        ok, reason = self.host_ctl.verify_unit_resource_limits(unit)
        if not ok:
            # Fail closed: never let a worker run without verified limits.
            log.error("resource limits NOT verified for %s: %s — stopping", unit, reason)
            self._validated_stop(job, js, unit)
            js.status = "failed"
            js.error = f"resource-limit verification failed: {reason}"
            js.ended_at = self.clock()
            self.state.save()
            return
        self._active[job.id] = (job, js)
        log.info("job %s started as %s (session %s)", job.id, unit, session_id)

    def _finalize_job(self, job: JobSpec, js: JobState, unit: str) -> None:
        self._active.pop(job.id, None)
        receipt_path = self.logs_dir / f"{job.id}.exit"
        # The log of THIS attempt (per-attempt logs are never overwritten).
        log_path = self._attempt_log_path(job, js)
        code = self.host_ctl.read_exit_receipt(receipt_path)
        # claude runs with stream-json: a missing/non-success result event is an
        # error even on exit 0 (stdout errors are never treated as success).
        session, is_error = self.host_ctl.parse_run_log(
            log_path, require_result=(job.kind == "claude")
        )
        if session:
            js.session_id = session
        js.exit_code = code
        js.ended_at = self.clock()
        if code is None:
            js.status = "failed"
            js.error = "exit receipt missing — worker died without recording an exit code"
        elif code != 0:
            js.status = "failed"
            js.error = f"worker exited with code {code}"
        elif is_error:
            js.status = "failed"
            js.error = "worker result event missing, is_error, or non-success subtype"
        else:
            # Exit code alone NEVER implies accepted/verified completion.
            js.status = "finished-needs-review"
        self._promote_latest_log(job, js)
        self.host_ctl.cleanup_finished_unit(self.state, job, unit)
        self.state.save()
        log.info("job %s finalized: %s (exit=%s)", job.id, js.status, code)

    def _interrupt_job(
        self, job: JobSpec, js: JobState, unit: str, *, reason: str, graceful: bool
    ) -> None:
        """Timeout/pressure path: SIGINT ONLY to the validated owned unit,
        allow the checkpoint grace window, then stop only that unit."""
        self._active.pop(job.id, None)
        ok, why = self.host_ctl.validate_ownership(self.state, job, unit)
        if not ok:
            log.error("refusing to signal %s: %s", unit, why)
            js.status = "failed"
            js.error = f"ownership validation failed: {why}"
            js.ended_at = self.clock()
            self.state.save()
            return
        if graceful:
            self.host_ctl.send_sigint_to_unit(unit)
            deadline = self.clock() + SIGINT_CHECKPOINT_GRACE_S
            while self.clock() < deadline and self.host_ctl.unit_active(unit):
                self.sleep(1.0)
        self.host_ctl.stop_worker_unit(unit)
        js.status = "interrupted"
        js.error = reason
        js.ended_at = self.clock()
        # session id + working files preserved: interrupted jobs resume (--resume)
        self._promote_latest_log(job, js)
        self.state.save()
        log.info("job %s interrupted (%s) — session %s preserved", job.id, reason, js.session_id)

    def _has_receipt(self, job: JobSpec) -> bool:
        return self.host_ctl.read_exit_receipt(self.logs_dir / f"{job.id}.exit") is not None

    def _validated_stop(self, job: JobSpec, js: JobState, unit: str) -> None:
        """Stop a unit ONLY after ownership validation (cleanup paths included)."""
        ok, why = self.host_ctl.validate_ownership(self.state, job, unit)
        if ok:
            self.host_ctl.stop_worker_unit(unit)
        else:
            log.error("refusing cleanup stop of %s: %s", unit, why)

    def _interrupt_active(self, *, reason: str, defer_queued: bool) -> None:
        """Pressure/defer-all/signal path — THREE phases, not one-at-a-time:

        1. validate ownership of every active unit, then SIGINT ALL owned
           units FIRST (a worker mid-checkpoint keeps running while its
           sibling is still being signaled one-at-a-time otherwise);
        2. ONE shared SIGINT_CHECKPOINT_GRACE_S window for all of them;
        3. stop the stragglers (units still active after the window).
        """
        signaled: list[tuple[JobSpec, JobState, str]] = []
        for jid, (job, js) in list(self._active.items()):
            unit = js.unit_name or ""
            if unit and self.host_ctl.unit_active(unit):
                # Ownership validated before EVERY signal (as before).
                ok, why = self.host_ctl.validate_ownership(self.state, job, unit)
                if not ok:
                    log.error("refusing to signal %s: %s", unit, why)
                    js.status = "failed"
                    js.error = f"ownership validation failed: {why}"
                    js.ended_at = self.clock()
                    self._active.pop(jid, None)
                    continue
                self.host_ctl.send_sigint_to_unit(unit)
                signaled.append((job, js, unit))
            elif unit and self._has_receipt(job):
                self._finalize_job(job, js, unit)  # exited between polls: record it
            else:
                # no live unit of ours: never claim "interrupted" for a job that
                # is not actually running — mark it deferred (resumable).
                js.status = "deferred"
                js.error = reason
                js.ended_at = self.clock()
                self._promote_latest_log(job, js)
            self._active.pop(jid, None)
        # Phase 2: one SHARED grace window for all signaled units.
        deadline = self.clock() + SIGINT_CHECKPOINT_GRACE_S
        while self.clock() < deadline and any(
            self.host_ctl.unit_active(unit) for _job, _js, unit in signaled
        ):
            self.sleep(1.0)
        # Phase 3: stop the stragglers, mark every signaled job interrupted.
        for job, js, unit in signaled:
            if self.host_ctl.unit_active(unit):
                self.host_ctl.stop_worker_unit(unit)
            js.status = "interrupted"
            js.error = reason
            js.ended_at = self.clock()
            # session id + working files preserved: interrupted jobs resume
            self._promote_latest_log(job, js)
            log.info(
                "job %s interrupted (%s) — session %s preserved", job.id, reason, js.session_id
            )
        self._active.clear()
        if defer_queued:
            for jid, js in self.state.jobs.items():
                if jid in self.jobs and js.status == "queued":
                    js.status = "deferred"
                    js.error = reason
        self.state.save()

    # -- restart reconciliation ----------------------------------------------------

    def _reconcile_previous_run(self) -> None:
        """Adopt or honestly re-mark jobs left 'running' by a previous supervisor."""
        for jid, js in self.state.jobs.items():
            if js.status != "running" or jid not in self.jobs:
                continue
            job = self.jobs[jid]
            unit = js.unit_name or ""
            if unit and self.host_ctl.unit_active(unit):
                ok, why = self.host_ctl.validate_ownership(self.state, job, unit)
                if ok:
                    self._active[jid] = (job, js)
                    log.info("adopted still-active unit %s for job %s", unit, jid)
                    continue
                log.warning("active unit %s failed ownership check: %s", unit, why)
            elif unit and self._has_receipt(job):
                # finished while no supervisor was alive: finalize from the
                # durable receipt instead of re-running it on the next `run`.
                self._finalize_job(job, js, unit)
                continue
            js.status = "interrupted"
            js.error = "supervisor restarted while worker was active"
            js.ended_at = self.clock()
            self._promote_latest_log(job, js)
            self.state.save()

    # -- operator control ------------------------------------------------------------

    def _consume_control_request(self) -> bool:
        """A defer-all request written while the supervisor holds the lock."""
        path = self.state.control_path
        if not path.exists():
            return False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return False
        path.unlink(missing_ok=True)
        return isinstance(raw, dict) and raw.get("action") == "defer-all"


# --- manifest loading -------------------------------------------------------------------


def load_manifest(path: Path) -> list[JobSpec]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("jobs", [])
    if not isinstance(raw, list):
        raise QueueError("manifest must be a list of jobs or {'jobs': [...]}")
    jobs = [JobSpec.from_manifest(j) for j in raw]
    seen: set[str] = set()
    for j in jobs:
        if j.id in seen:
            raise QueueError(f"duplicate job id {j.id}")
        seen.add(j.id)
    return jobs


def bind_state_to_manifest(state: QueueState, jobs: list[JobSpec]) -> None:
    """Changing a manifest job under an existing state must fail closed."""
    for job in jobs:
        digest = job.digest()
        existing = state.job_digests.get(job.id)
        if existing is not None and existing != digest:
            raise QueueError(
                f"job {job.id} spec changed since the state was written "
                f"({existing} != {digest}) — use a new job id"
            )
        state.job_digests[job.id] = digest


# --- CLI -----------------------------------------------------------------------------------


def _default_state_path(manifest: Path | None) -> Path:
    if manifest is not None:
        stem = manifest.with_suffix("").name
        return manifest.parent / f".{stem}.queue-state.json"
    return Path("data/aci-improvement/queue-state.json")


def apply_resource_overrides(
    max_workers: int | None, admit_gib: float | None, pressure_gib: float | None
) -> None:
    """Operator overrides for one supervisor process (validated, fail closed).
    The per-worker cgroup limits are NOT overridable — they are the hard cap."""
    global MAX_CONCURRENT_WORKERS, ADMIT_MEM_AVAILABLE_GIB, ADMIT_SINGLE_MEM_AVAILABLE_GIB
    global PRESSURE_MEM_AVAILABLE_GIB
    if max_workers is not None:
        if not 1 <= max_workers <= 4:
            raise QueueError("--max-workers must be 1..4")
        MAX_CONCURRENT_WORKERS = max_workers
    if admit_gib is not None:
        if admit_gib < 2.0:
            raise QueueError("--admit-gib must be >= 2.0 (one worker may use up to 2 GiB)")
        ADMIT_MEM_AVAILABLE_GIB = ADMIT_SINGLE_MEM_AVAILABLE_GIB = admit_gib
    if pressure_gib is not None:
        if pressure_gib < 1.0:
            raise QueueError("--pressure-gib must be >= 1.0")
        PRESSURE_MEM_AVAILABLE_GIB = pressure_gib
    if PRESSURE_MEM_AVAILABLE_GIB >= ADMIT_MEM_AVAILABLE_GIB:
        raise QueueError("pressure floor must stay below the admission floor")


def cmd_run(args: argparse.Namespace) -> int:
    apply_resource_overrides(
        getattr(args, "max_workers", None),
        getattr(args, "admit_gib", None),
        getattr(args, "pressure_gib", None),
    )
    backend = select_backend(getattr(args, "backend", None))
    host_ctl = make_host_controls(backend)
    manifest_path = Path(args.manifest).resolve()
    jobs = load_manifest(manifest_path)
    state_path = Path(args.state) if args.state else _default_state_path(manifest_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    logs_dir = state_path.parent / "queue-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    state = QueueState(state_path)
    state.acquire_lock()  # BEFORE load: no stale-state race
    try:
        state.load()
        bind_state_to_manifest(state, jobs)
        if args.dry_run:
            snap = host_ctl.snapshot()
            print(f"[dry-run] backend: {backend}")
            print(f"[dry-run] manifest: {len(jobs)} job(s); state: {state_path}")
            print(f"[dry-run] host: {snap.to_dict()}")
            for j in jobs:
                dep_ok, dep_reason = WorkerQueue(
                    state, jobs, logs_dir, dry_run=True
                )._deps_satisfied(j)
                decision = evaluate_admission(0, snap)
                if backend == "darwin":
                    worker_id = "pgid-<pid> (assigned at spawn)"
                else:
                    worker_id = state.unit_for(j)
                print(
                    f"[dry-run] job {j.id} kind={j.kind} cwd={j.cwd} "
                    f"deps={'ok' if dep_ok else dep_reason} "
                    f"admission={'ok' if decision.admitted else decision.reason} "
                    f"worker={worker_id}"
                )
            return 0
        # We hold the lock, so any pending control request targeted a supervisor
        # that exited before consuming it: it must not defer this explicit run.
        if state.control_path.exists():
            log.warning("discarding stale control request %s", state.control_path)
            state.control_path.unlink(missing_ok=True)
        # explicit invocation = resume: interrupted/deferred (pressure) jobs re-queue
        state.pressure_deferred = False
        state.pressure_reason = None
        for js in state.jobs.values():
            if js.status in ("interrupted", "deferred"):
                js.status = "queued"
                js.error = None
        for j in jobs:
            state.jobs.setdefault(j.id, JobState(status="queued"))
        state.save()
        queue = WorkerQueue(state, jobs, logs_dir, host_ctl=host_ctl)
        # Signal handlers are installed HERE (real run only — never in
        # WorkerQueue.__init__, so dry-run/tests never touch process signals)
        # and restored afterwards.
        queue.install_signal_handlers()
        try:
            return queue.run()
        finally:
            queue.restore_signal_handlers()
            state.save()
    finally:
        state.release_lock()


def cmd_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state) if args.state else _default_state_path(None)
    if not state_path.exists():
        print(f"no state at {state_path}")
        return 1
    state = QueueState(state_path)
    state.load()
    snap = make_host_controls(select_backend(getattr(args, "backend", None))).snapshot()
    print(f"state: {state_path}")
    print(f"unit token: {state.unit_token}")
    print(f"host: {snap.to_dict()}")
    if state.pressure_deferred:
        print(f"pressure-deferred: {state.pressure_reason}")
    for jid in sorted(state.jobs):
        js = state.jobs[jid]
        print(
            f"  {jid}: {js.status}"
            f"{' exit=' + str(js.exit_code) if js.exit_code is not None else ''}"
            f"{' session=' + str(js.session_id) if js.session_id else ''}"
            f"{' unit=' + str(js.unit_name) if js.unit_name else ''}"
            f"{' attempts=' + str(js.attempts)}"
            f"{' err=' + str(js.error) if js.error else ''}"
        )
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    state_path = Path(args.state) if args.state else _default_state_path(None)
    if not state_path.exists():
        print(f"no state at {state_path}")
        return 1
    state = QueueState(state_path)
    state.acquire_lock()
    try:
        state.load()
        js = state.jobs.get(args.job)
        if js is None:
            print(f"no job {args.job!r} in {state_path}")
            return 1
        if js.status not in ("failed", "interrupted", "deferred"):
            print(f"job {args.job} status {js.status} is not resumable")
            return 1
        js.status = "queued"
        js.error = None
        state.save()
        print(
            f"job {args.job} re-queued (attempts={js.attempts}, session={js.session_id or 'none'})"
        )
        return 0
    finally:
        state.release_lock()


def cmd_defer_all(args: argparse.Namespace) -> int:
    state_path = Path(args.state) if args.state else _default_state_path(None)
    if not state_path.exists():
        print(f"no state at {state_path} — nothing deferred (pass the run's --state)")
        return 1
    state = QueueState(state_path)
    try:
        # If no supervisor is running we can take the lock and act directly.
        state.acquire_lock()
    except QueueError:
        # Supervisor alive: leave a control request it consumes in its poll loop.
        state.control_path.parent.mkdir(parents=True, exist_ok=True)
        state.control_path.write_text(
            json.dumps({"action": "defer-all", "reason": args.reason or "operator request"}),
            encoding="utf-8",
        )
        print(f"supervisor active — defer-all request written to {state.control_path}")
        return 0
    try:
        state.load()
        changed = 0
        for _jid, js in state.jobs.items():
            if js.status in ("queued", "interrupted"):
                js.status = "deferred"
                js.error = args.reason or "deferred by operator"
                changed += 1
        state.save()
        print(f"deferred {changed} job(s) (no active supervisor; running jobs untouched)")
        return 0
    finally:
        state.release_lock()


def cmd_host_snapshot(args: argparse.Namespace) -> int:
    snap = make_host_controls(select_backend(getattr(args, "backend", None))).snapshot()
    print(json.dumps(snap.to_dict(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run jobs from a manifest (resumes deferred/interrupted)")
    p_run.add_argument("manifest")
    p_run.add_argument("--state", help="state file path (default: alongside manifest)")
    p_run.add_argument("--dry-run", action="store_true", help="admission + plan only, no launch")
    p_run.add_argument(
        "--backend",
        choices=list(BACKENDS),
        default=None,
        help="worker backend (default: auto — darwin on macOS, systemd on Linux)",
    )
    p_run.add_argument(
        "--max-workers", type=int, default=None, help=f"override (default {MAX_CONCURRENT_WORKERS})"
    )
    p_run.add_argument(
        "--admit-gib",
        type=float,
        default=None,
        help=f"MemAvailable floor for any admission (default {ADMIT_SINGLE_MEM_AVAILABLE_GIB}/"
        f"{ADMIT_MEM_AVAILABLE_GIB} for first/additional workers)",
    )
    p_run.add_argument(
        "--pressure-gib",
        type=float,
        default=None,
        help=f"sustained-pressure MemAvailable floor (default {PRESSURE_MEM_AVAILABLE_GIB})",
    )
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="show persisted queue status")
    p_status.add_argument("--state", help="state file path")
    p_status.add_argument(
        "--backend",
        choices=list(BACKENDS),
        default=None,
        help="host-sampling backend (default: auto — darwin on macOS, systemd on Linux)",
    )
    p_status.set_defaults(func=cmd_status)

    p_resume = sub.add_parser(
        "resume", help="explicitly re-queue a failed/interrupted/deferred job"
    )
    p_resume.add_argument("job")
    p_resume.add_argument("--state", help="state file path")
    p_resume.set_defaults(func=cmd_resume)

    p_defer = sub.add_parser("defer-all", help="defer queued/interrupted jobs (control request)")
    p_defer.add_argument("--state", help="state file path")
    p_defer.add_argument("--reason")
    p_defer.set_defaults(func=cmd_defer_all)

    p_snap = sub.add_parser("host-snapshot", help="print host resource snapshot")
    p_snap.add_argument(
        "--backend",
        choices=list(BACKENDS),
        default=None,
        help="host-sampling backend (default: auto — darwin on macOS, systemd on Linux)",
    )
    p_snap.set_defaults(func=cmd_host_snapshot)

    args = parser.parse_args(argv)
    try:
        # argparse.Namespace attributes are Any; bind the handler to a typed
        # callable so the return is an int, not Any (mypy --strict).
        handler: Callable[[argparse.Namespace], int] = args.func
        return handler(args)
    except QueueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
