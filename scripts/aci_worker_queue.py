#!/usr/bin/env python3
"""ACI worker queue — resource-controlled launcher for delegated GLM-5.3 agent jobs.

A small standard-library-only Linux queue for invoking Claude Code / OpenCode
(GLM-5.3) jobs from a JSON manifest with a dependency graph, persistent
state/logs/session-ids, admission control against live host resources, and
per-worker systemd user-service resource containment.

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
  (SIGINT + grace), defer queued work to the next explicit invocation.
- Ownership: every stop/signal call is preceded by ownership validation —
  the unit name must carry this queue run's persisted unit token, the unit
  Description must carry the same token + job id, WorkingDirectory must
  equal the job cwd, and ExecStart must contain the expected CLI binary.
  Process-name matching utilities are never used.
- The worker CLI runs inside a /bin/sh wrapper in the same cgroup that
  redirects output to a log and writes a durable exit receipt; the agent
  exit code alone NEVER implies accepted/verified completion (exit 0 is
  recorded as finished-needs-review, everything else fails).
- One supervisor per state file (flock); state is bound to the manifest
  job specs (changing a manifest job under an existing state fails closed).
- Job timeout default 30 min; CLI failure/timeout → bounded failed/interrupted
  state, no unlimited retries. Explicit `run` re-invocation resumes
  interrupted/deferred jobs (claude --resume with the persisted session id).

Usage:
  python scripts/aci_worker_queue.py run MANIFEST.json [--dry-run]
  python scripts/aci_worker_queue.py status [--state FILE]
  python scripts/aci_worker_queue.py resume JOB [--state FILE]
  python scripts/aci_worker_queue.py defer-all [--state FILE] [--reason TEXT]
  python scripts/aci_worker_queue.py host-snapshot
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
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

UNIT_PREFIX = "aci-worker"
UNIT_TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # ASCII: becomes part of a unit name
STATE_SCHEMA_VERSION = 2
JOB_STATUSES = frozenset(
    {"queued", "running", "finished-needs-review", "verified", "failed", "deferred", "interrupted"}
)
# Only these statuses satisfy a dependency: exit 0 is NOT verification.
DEP_SATISFIED_STATUSES = frozenset({"finished-needs-review", "verified"})

CLAUDE_BIN = (
    "/home/hien/.antigravity-ide/extensions/anthropic.claude-code-2.1.286-linux-x64"
    "/resources/native-binary/claude"
)
OPENCODE_BIN = "/home/hien/.opencode/bin/opencode"
MODEL_ID = "OneNexus/glm-5.3"
OPENCODE_MODEL_ID = "local-gateway/OneNexus/glm-5.3"
#: RuntimeMaxSec = job timeout + this margin (SIGINT grace + finalization).
RUNTIME_MAX_MARGIN_S = 120

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
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

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
        # re-verified before any stop/signal call.
        return f"aci worker queue token={self.unit_token} job={job.id} exe={job.digest()}"


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
    if props.get("Description") != state.description_for(job):
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


def cleanup_finished_unit(unit: str) -> None:
    """Best-effort reset of a finished transient unit (it may already be gone)."""
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

    def snapshot(self) -> HostSnapshot:
        return host_snapshot()

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

    def cleanup_finished_unit(self, unit: str) -> None:
        cleanup_finished_unit(unit)

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
        host_ctl: HostControls | None = None,
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
        self._install_signals()

    def _install_signals(self) -> None:
        # SIGINT/SIGTERM: checkpoint (interrupt owned units gracefully) and exit.
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._handle_signal)

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
        if not self.dry_run and not self.host_ctl.systemd_available():
            raise QueueError(
                "systemd user session/cgroups unavailable — FAIL CLOSED, refusing to run unbounded"
            )
        self._reconcile_previous_run()
        rc = 0
        while True:
            if self._consume_control_request():
                self._interrupt_active(reason="operator defer-all request", defer_queued=True)
                rc = 75
                break
            if self._shutdown:
                self._interrupt_active(reason="supervisor signal", defer_queued=False)
                rc = 130
                break
            # finalize exited units / apply timeouts
            for _jid, (job, js) in list(self._active.items()):
                unit = js.unit_name or ""
                if not self.host_ctl.unit_active(unit):
                    self._finalize_job(job, js, unit)
                elif js.started_at is not None and (self.clock() - js.started_at >= job.timeout_s):
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
                    rc = 75
                    break
            # admission (max 2 active, deps gated)
            self._try_admit()
            if not self._active and not self._has_pending():
                break
            self.sleep(POLL_INTERVAL_S)
        self.state.save()
        return rc

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

    def _start_job(self, job: JobSpec, js: JobState) -> None:
        log_path = self.logs_dir / f"{job.id}.log"
        receipt_path = self.logs_dir / f"{job.id}.exit"
        # A previous attempt's receipt must never be read as THIS attempt's exit
        # code (e.g. an OOM kill takes the wrapper down before it writes one).
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        receipt_path.unlink(missing_ok=True)
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
        js.attempts += 1
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
        log_path = self.logs_dir / f"{job.id}.log"
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
        self.host_ctl.cleanup_finished_unit(unit)
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
        for jid, (job, js) in list(self._active.items()):
            unit = js.unit_name or ""
            if unit and self.host_ctl.unit_active(unit):
                self._interrupt_job(job, js, unit, reason=reason, graceful=True)
            elif unit and self._has_receipt(job):
                self._finalize_job(job, js, unit)  # exited between polls: record it
            else:
                # no live unit of ours: never claim "interrupted" for a job that
                # is not actually running — mark it deferred (resumable).
                js.status = "deferred"
                js.error = reason
                js.ended_at = self.clock()
            self._active.pop(jid, None)
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


def cmd_run(args: argparse.Namespace) -> int:
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
            snap = host_snapshot()
            print(f"[dry-run] manifest: {len(jobs)} job(s); state: {state_path}")
            print(f"[dry-run] host: {snap.to_dict()}")
            for j in jobs:
                dep_ok, dep_reason = WorkerQueue(
                    state, jobs, logs_dir, dry_run=True
                )._deps_satisfied(j)
                decision = evaluate_admission(0, snap)
                print(
                    f"[dry-run] job {j.id} kind={j.kind} cwd={j.cwd} "
                    f"deps={'ok' if dep_ok else dep_reason} "
                    f"admission={'ok' if decision.admitted else decision.reason} "
                    f"unit={state.unit_for(j)}"
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
        queue = WorkerQueue(state, jobs, logs_dir)
        try:
            return queue.run()
        finally:
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
    snap = host_snapshot()
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


def cmd_host_snapshot(_: argparse.Namespace) -> int:
    print(json.dumps(host_snapshot().to_dict(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run jobs from a manifest (resumes deferred/interrupted)")
    p_run.add_argument("manifest")
    p_run.add_argument("--state", help="state file path (default: alongside manifest)")
    p_run.add_argument("--dry-run", action="store_true", help="admission + plan only, no launch")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="show persisted queue status")
    p_status.add_argument("--state", help="state file path")
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
    p_snap.set_defaults(func=cmd_host_snapshot)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except QueueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
