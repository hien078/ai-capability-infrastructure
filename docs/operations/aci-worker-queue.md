# ACI worker queue (`scripts/aci_worker_queue.py`)

A small standard-library-only queue for invoking Claude Code / OpenCode
(GLM-5.3) jobs from a JSON manifest, with a dependency graph, admission
control against live host resources, per-worker resource containment,
persistent state/logs/session-ids, and explicit deferral/resume.

TWO backends behind one seam (`select_backend`/`make_host_controls`,
overridable with `--backend`):

- **systemd** (Linux, the default there): each worker runs in its OWN
  transient systemd user service with kernel-enforced cgroup limits.
- **darwin** (macOS, the default there): no systemd/cgroups exist — each
  worker runs in its OWN process group/session under `nice -n 10` through
  the SAME `/bin/sh` exit-receipt wrapper, with a supervisor-polled memory
  cap. Which guarantees genuinely differ is listed under
  [macOS (Darwin) backend](#macos-darwin-backend) — nothing is silently
  assumed to be equivalent.

Tests: `tests/unit/test_aci_worker_queue.py` (systemd backend — fake clock +
fake host) and `tests/unit/test_aci_worker_queue_darwin.py` (darwin backend —
fake process table, no real processes; ONE opt-in real smoke test behind
`ACIQ_REAL_SMOKE=1`). No real systemd, sleeps, or network in the default
suite.

## Commands

```bash
python scripts/aci_worker_queue.py run MANIFEST.json [--dry-run] [--state FILE] \
    [--backend systemd|darwin]
python scripts/aci_worker_queue.py status [--state FILE] [--backend systemd|darwin]
python scripts/aci_worker_queue.py resume JOB [--state FILE]
python scripts/aci_worker_queue.py defer-all [--state FILE] [--reason TEXT]
python scripts/aci_worker_queue.py host-snapshot [--backend systemd|darwin]
```

- `--backend` overrides the platform default (darwin on macOS, systemd on
  Linux). Forcing a backend on the wrong platform fails closed
  (`available()` refuses), it never silently degrades.
- `run` is the ONLY command that launches work, and it is meant for the
  human coordinator. An explicit `run` invocation RESUMES: interrupted and
  deferred jobs are re-queued (claude jobs resume with `--resume <persisted
  session id>`; attempts/session metadata are preserved, never reset).
  Signal handlers (SIGINT/SIGTERM checkpoint) are installed ONLY by a real
  `run` — never by `--dry-run`, `status`, or library use.
- `--dry-run` prints the admission plan and worker identity without
  launching anything and marks nothing complete.
- `status` is read-only.
- `resume JOB` explicitly re-queues one failed/interrupted/deferred job.
- `defer-all` defers queued/interrupted jobs. If a supervisor is currently
  holding the state lock, it writes a control request file the supervisor
  consumes in its poll loop (it then SIGINTs ALL of its OWN validated workers
  at once, waits ONE shared 30 s grace window, stops the stragglers, and
  defers the rest); if no supervisor is running it acts directly. It never
  touches running workers it does not own.
- `host-snapshot` prints `MemAvailable` (GiB), CPU busy %, load1 — from
  `/proc` on the systemd backend, from `sysctl`/`vm_stat`/`ps` on darwin.

## Exit codes

- `0` — every job reached a terminal state (finished-needs-review / verified /
  failed / interrupted).
- `75` — work was DEFERRED, nothing ran to completion: sustained host
  pressure, OR a deferral-only run where every job ended deferred by
  admission/dependency refusal. Both mean "re-invoke explicitly later".
- `130` — the supervisor itself was interrupted by SIGINT/SIGTERM (owned
  units were gracefully interrupted first).
- `2` — queue error (bad manifest, corrupt state, systemd unavailable, …).
- `1` — `status`/`resume`/`defer-all` could not find or act on the state.

## Manifest

```json
{"jobs": [
  {
    "id": "slug-with-dashes",
    "kind": "claude" | "opencode",
    "title": "...",
    "prompt": "...",
    "cwd": "/absolute/path",
    "depends_on": ["other-job-id"],
    "timeout_s": 1800,
    "allowed_tools": ["Read", "Edit", "Bash"],
    "session_id": null
  }
]}
```

- `id` must be a slug; `cwd` must exist; `timeout_s` > 0 (default 1800).
- A manifest job's spec is DIGESTED into the state file; changing a job's
  spec under an existing state fails closed ("spec changed — use a new job
  id") so an edited manifest can never silently repurpose old state.

## Resource policy (enforced in code, verified against the live worker)

- **Admission** (both backends): max 2 concurrent workers. First worker
  needs MemAvailable >= 3.5 GiB; an additional worker needs >= 5 GiB, CPU
  busy < 75%, load1 < 10. A job refused while one of OUR workers is still
  running stays QUEUED and is rechecked on the next poll; it is DEFERRED
  (resumable, never failed) only when nothing of ours is running.
- **Sustained pressure** (both backends, identical semantics): 3 samples,
  5 s apart: MemAvailable < 3 GiB or CPU busy > 85% or load1 > 12 → ALL
  running workers are SIGINTed FIRST (each after its own ownership
  validation), then ONE shared 30 s checkpoint grace window elapses for all
  of them, then the stragglers are stopped; queued work is marked deferred,
  and the supervisor exits 75. A single worker's timeout uses the same
  per-worker SIGINT + 30 s grace. Deferred work is never auto-restarted —
  only an explicit `run`/`resume`.
- **Per-worker containment, systemd backend**: each worker runs in its OWN
  transient systemd user service with `MemoryHigh=1536M`, `MemoryMax=2G`,
  `MemorySwapMax=256M`, `CPUQuota=200%`, `TasksMax=128`, `OOMPolicy=kill`,
  `Nice=10`. The exact applied values are re-read from the unit and verified
  NUMERICALLY before the job is allowed to run; any mismatch fails closed
  (unit stopped, job failed). If systemd/cgroups are unavailable the queue
  refuses to run at all — never unbounded. `RuntimeMaxSec = timeout_s + 120`
  lets systemd end a worker even if the supervisor itself dies;
  `--expand-environment=no` keeps systemd from rewriting `$VAR` inside
  prompts.
- **Per-worker containment, darwin backend**: see
  [macOS (Darwin) backend](#macos-darwin-backend) — the same policy VALUES
  (2 GiB memory cap, nice 10, wall-clock timeout), enforced by the
  supervisor where macOS has no kernel mechanism.
- **CPU sampling**: `/proc/stat` deltas over the FIRST 8 counters only
  (guest/guest_nice are already inside user/nice — including them would
  double-count busy time) on the systemd backend; `ps -A -o %cpu=` summed
  and normalized by core count on darwin (a decaying average — see below).

## Ownership and signal safety (systemd backend; darwin analog above)

- Unit names carry a persisted per-state run token:
  `aci-worker-<token>-<job>.service`.
- Before ANY stop/signal/reset-failed call the queue validates: unit name
  matches the expected name, the unit Description matches the recorded
  `token/job/spec_digest` identity, `WorkingDirectory` equals the job cwd,
  and `ExecStart` contains the expected CLI binary. Mismatch → refuse to
  act. The Description label is `spec_digest=<manifest spec digest>`; units
  written by an older version of the queue carry the same digest under the
  legacy label `exe=` and are still accepted, so workers of a supervisor
  that is still running stay recognizable/interruptible across an upgrade.
- `reset-failed` (cleanup of a finished unit's systemd bookkeeping) is
  ownership-gated the same way: a unit that is gone is skipped, a mismatch
  is refused, only a validated OWNED unit is reset.
- Process-name matching utilities (`pkill`/`killall`/`pgrep`/`psutil`) are
  never used and their presence is test-banned.
- Existing OpenCode services, user sessions, swap, and sysctls are never
  touched.

## macOS (Darwin) backend

Selected automatically on macOS (`platform.system() == "Darwin"`), overridable
with `--backend systemd|darwin`. The scheduler, admission thresholds, pressure
deferral, exit codes (0/75/130/2), per-attempt logs, exit receipts, session
resume, dependency gating, flock, and atomic state persistence are ALL the
same code as Linux — only the containment mechanism differs.

### What the darwin backend does

- **Host snapshot**: total memory from `sysctl -n hw.memsize` (context);
  available memory ≈ free + inactive + speculative pages from `vm_stat`
  (the honest analog of Linux `MemAvailable`); CPU busy from
  `ps -A -o %cpu=` summed and normalized by core count; load1 from
  `sysctl -n vm.loadavg`. Any sampling failure fails closed (the run is
  refused), never silently treated as healthy.
- **Worker start**: each worker runs in its OWN process group/session
  (`start_new_session`, so pgid == leader pid) under `nice -n 10` (an
  INCREMENT on the supervisor's nice, capped at 20), through the SAME
  `/bin/sh` exit-receipt wrapper (`"$@" > "$ACIQ_LOG" 2>&1; rc=$?; echo
  "$rc" > "$ACIQ_RECEIPT"`). The worker env is a defined minimal base
  (PATH/HOME/USER/LOGNAME/TMPDIR/LANG/TZ) + the job's vars (PWD exported —
  the CLI resolves its project from `$PWD`, not `getcwd()`).
- **Start verification** (the analog of re-reading the systemd unit): the
  worker leads its OWN process group (`ps -o pgid= -p <pid>` == pid) and the
  nice increment applied (`getpriority` >= supervisor nice + 10, capped at
  20). Any mismatch fails closed: the group is killed, the job fails.
- **Memory cap (2 GiB, the MemoryMax policy value)**: macOS does NOT enforce
  `RLIMIT_AS`, so the cap is enforced by the supervisor POLLING the
  process-group RSS (`ps -o rss= -g <pgid>`, summed over group members) every
  scheduler poll (~2 s). Over cap → SIGINT the group → ONE 30 s grace window
  → SIGKILL the group → the job is honestly `interrupted` (resumable) with
  the measured RSS in the error. On Linux the same breach is a kernel OOM
  kill → missing receipt → `failed`; on darwin we know exactly why.
- **Wall-clock timeout**: identical to Linux (the supervisor polls
  `started_at + timeout_s`), SIGINT → grace → SIGKILL the group.
- **Ownership before ANY signal**: the recorded pgid + leader pid + leader
  start time (`ps -ww -o lstart=`) + leader command line
  (`ps -ww -o command=`) must ALL match the live process, and the command
  line must contain the expected CLI binary. Signals go to the process
  GROUP by its NUMERIC id (`os.kill(-pgid, sig)`) — never by name; the
  `pkill`/`killall`/`pgrep` ban is unchanged. A reused pid fails the
  start-time check and is never signaled.
- **Restart reconciliation**: a job left `running` by a dead supervisor is
  ADOPTED when the recorded identity still matches the live group (the
  fresh supervisor has no in-memory Popen — it validates from the
  PERSISTED state fields); anything else is honestly `interrupted`
  (resumable with its session), or finalized from the durable receipt when
  the worker completed while no supervisor was alive.
- **OpenCode on the Mac**: the SAME argv as Linux
  (`run --standalone --auto --model local-gateway/OneNexus/glm-5.3
  --format=json --session ses_<24 hex>`), `PWD` exported. The default
  binary is `~/.opencode/bin/opencode` (the official installer path);
  `ACIQ_OPENCODE_BIN`/`ACIQ_CLAUDE_BIN` override the binaries (the Linux
  claude path does not exist on macOS — a claude job there fails closed at
  spawn unless `ACIQ_CLAUDE_BIN` is set).

### Which guarantees DIFFER from Linux (honest list)

1. **No kernel-enforced memory cap — polled RSS.** The 2 GiB cap is checked
   every ~2 s against the group's summed RSS. A worker can OVERSHOOT the cap
   between polls (a fast allocation spike is bounded only by the poll
   interval, not by the kernel), and the 30 s SIGINT grace window lets a
   well-behaved worker finish a checkpoint — but an ill-behaved one keeps
   allocating during the window. Linux `MemoryMax` kills in-kernel, at the
   exact byte. The Mac itself can therefore memory-pressure before the cap
   fires; the sustained-pressure deferral (3 samples) is the second line.
2. **No cgroup accounting of escaped double-forked children.** A worker
   subprocess that double-forks and changes its process group LEAVES the
   accounted group: its RSS disappears from the cap poll and it survives a
   group kill. On Linux the cgroup counts every descendant however they
   fork. (Children that merely fork stay in the group and ARE counted —
   `ps -o rss= -g` sums all members.)
3. **No CPU quota.** `nice -n 10` only deprioritizes the worker relative to
   the user's foreground work; a worker CAN still burn all cores if nothing
   else wants them. Linux `CPUQuota=200%` is a hard cap.
4. **No TasksMax.** There is no process-count cap for the group on macOS.
5. **No RuntimeMaxSec equivalent.** The wall-clock timeout is enforced by
   the SUPERVISOR's poll loop. If the supervisor itself dies, a darwin
   worker runs unbounded (Linux systemd ends it at `timeout + 120` s
   regardless). The next `run` reconciles honestly (adopt or interrupt).
6. **`unit_active` = the group leader's liveness.** The leader is the sh
   wrapper, which exits when the CLI exits — so "leader alive" tracks the
   job. If the leader dies and its exact pid is REUSED by another process
   between polls, `ps -p` shows the new process as "alive" until the job
   timeout fires; the ownership validation (start time + command line)
   then refuses to signal it — the job fails closed, never a wrong signal.
7. **CPU busy % is a decaying average.** `ps %cpu` is a per-process
   lifetime-decayed average, not an instantaneous `/proc/stat` delta: a
   burst shows up smeared over seconds, a long-idle process under-reports.
   The admission/pressure thresholds are the same, but the signal is
   coarser.
8. **Available memory is an approximation.** free + inactive + speculative
   pages from `vm_stat` is the closest honest analog of `MemAvailable`, but
   it is a page-class heuristic, not the kernel's own admission estimate.
9. **Memory-cap breach → `interrupted` (resumable), not `failed`.** On
   Linux an OOM kill leaves no receipt → `failed` (we cannot even tell it
   was OOM). On darwin WE did the killing, so the job records the measured
   RSS and stays resumable — a human decides whether to retry it.

### Verified live (2026-10-02, Intel x86_64 macOS)

- `start_new_session` → pgid == leader pid; `ps -o pgid=/lstart=/command=`
  and `ps -o rss= -g <pgid>` all work as used; `os.kill(-pgid, sig)` reaches
  every group member; `nice -n 10` increments the parent's nice (this host's
  supervisor runs at nice 15, so workers land at the 20 cap — verified
  numerically by the start verification).
- The opt-in real smoke test
  (`ACIQ_REAL_SMOKE=1 pytest tests/unit/test_aci_worker_queue_darwin.py::test_real_darwin_smoke_sleep_through_the_backend`)
  ran `/bin/sleep 2` through the FULL queue (receipt 0 →
  finished-needs-review, identity recorded) and `/bin/sleep 30` killed by
  the 3 s wall-clock timeout (SIGINT → grace → SIGKILL the group →
  `interrupted`, process group verified empty afterwards): PASSED, 7.9 s.
- `host-snapshot` on this Mac: `{"mem_available_gib": 19.0, "cpu_busy_pct":
  68.7, "load1": 6.46}`; a live `--dry-run` refused admission honestly at
  `load1 13.10 >= 10.0` while the machine was busy.

## Exit codes and completion honesty

- The worker CLI runs inside a `/bin/sh` wrapper (IN the unit cgroup on
  Linux, IN the worker's process group on macOS) that redirects output to a
  log file and writes a durable exit receipt (`<job>.exit`). Transient
  units vanish after exit, so the receipt — not systemd — is the exit-code
  source of truth on both backends.
- **Logs are per attempt**: attempt N writes `<job>.attempt-N.log` (earlier
  attempts are never truncated — evidence survives re-runs); `<job>.log`
  is kept as a copy of the latest attempt for `tail` convenience.
- **Exit code 0 is recorded as `finished-needs-review`, never `verified`.**
  Agent exit codes alone never imply accepted/verified task completion.
- Nonzero exit, missing receipt, or `is_error` in the claude result event →
  `failed`. A failed dependency DEFERS its dependents (they never unblock).
- Claude session ids are generated (uuid4) and persisted BEFORE spawn; the
  claude `--session-id`/`--resume` flags use the persisted id, and the
  session id from the stream-json result event is reconciled into state.

## Restart safety

- One supervisor per state file (flock; the lock is taken BEFORE loading
  state).
- State persists via write-to-temp + `fsync` + atomic rename (+ a directory
  fsync): a crash mid-write can never leave a truncated state file — the
  run token and job states survive a power cut.
- On start, jobs left `running` by a dead supervisor are reconciled: a
  still-active unit that passes ownership validation is ADOPTED; anything
  else is honestly marked `interrupted` (resumable with its session).
- SIGINT/SIGTERM to the supervisor (handlers installed only by a real
  `run`) interrupts owned units gracefully and exits 130.

## CLI invocation details

- Claude: `-p --model OneNexus/glm-5.3 --output-format stream-json --verbose
  --permission-mode acceptEdits --permission-prompts none --strict-mcp-config
  --mcp-config {"mcpServers":{}} --setting-sources user,project` plus
  explicit `--allowedTools` from the manifest. No permission-bypass flag.
- `CLAUDE_CODE_SUBAGENT_MODEL` and the sonnet/opus/haiku default-model env
  aliases are all pinned to `OneNexus/glm-5.3`; `OMP/OPENBLAS/MKL_NUM_THREADS=1`.
- OpenCode: `run --standalone --auto --model local-gateway/OneNexus/glm-5.3
  --format=json --session ses_<24 hex> --title <title>` with `PWD` exported
  (the CLI resolves its project from `$PWD`, not `getcwd()`). Verified live
  2026-10-01 through the full queue lifecycle: session ids MUST start with
  `ses` (else the run fails with an error event), `--session` creates the
  session on first use and continues it on re-entry, `--auto` prevents a
  headless run from blocking on a permission prompt; `{"type":"error"}`
  events mark the job failed.
- Resume: a re-queued job continues its PERSISTED session (claude
  `--resume=<id>`, opencode `--session=<id>`); a stale exit receipt from a
  previous attempt is deleted before every spawn.
- No secrets are written to state files or logs; gateway credentials stay in
  the user's existing settings and are inherited, never printed.

## Known limitations (honest)

- OpenCode is the primary path since 2026-10-01 (live-verified, see above);
  completion for opencode jobs = exit receipt 0 and no error event (it has no
  stream-json `result` event like claude).
- The queue launches ONE claude/opencode process per job; it does not manage
  the subagents that process may spawn (they share the worker's cgroup —
  which is the point: `TasksMax=128` bounds the whole tree — on Linux; on
  macOS they share the worker's process GROUP, which the memory-cap poll
  sums but nothing bounds — see darwin difference #2 and #4).
- `resume`/`defer-all`/`status` never start work; only `run` does.
- No auto-retry: a failed job stays failed until a human runs `resume`.
- The wrapper writes the exit receipt to the logs dir; if the logs dir is
  on a different filesystem that fails to write, the job fails closed
  (missing receipt), which is the safe direction.
