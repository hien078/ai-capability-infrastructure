# ACI worker queue (`scripts/aci_worker_queue.py`)

A small standard-library-only Linux queue for invoking Claude Code / OpenCode
(GLM-5.3) jobs from a JSON manifest, with a dependency graph, admission
control against live host resources, per-worker systemd user-service resource
containment, persistent state/logs/session-ids, and explicit deferral/resume.

Tests: `tests/unit/test_aci_worker_queue.py` (deterministic — fake clock +
fake host; no real systemd, sleeps, or network).

## Commands

```bash
python scripts/aci_worker_queue.py run MANIFEST.json [--dry-run] [--state FILE]
python scripts/aci_worker_queue.py status [--state FILE]
python scripts/aci_worker_queue.py resume JOB [--state FILE]
python scripts/aci_worker_queue.py defer-all [--state FILE] [--reason TEXT]
python scripts/aci_worker_queue.py host-snapshot
```

- `run` is the ONLY command that launches work, and it is meant for the
  human coordinator. An explicit `run` invocation RESUMES: interrupted and
  deferred jobs are re-queued (claude jobs resume with `--resume <persisted
  session id>`; attempts/session metadata are preserved, never reset).
  Signal handlers (SIGINT/SIGTERM checkpoint) are installed ONLY by a real
  `run` — never by `--dry-run`, `status`, or library use.
- `--dry-run` prints the admission plan and unit names without launching
  anything and marks nothing complete.
- `status` is read-only.
- `resume JOB` explicitly re-queues one failed/interrupted/deferred job.
- `defer-all` defers queued/interrupted jobs. If a supervisor is currently
  holding the state lock, it writes a control request file the supervisor
  consumes in its poll loop (it then SIGINTs ALL of its OWN validated units
  at once, waits ONE shared 30 s grace window, stops the stragglers, and
  defers the rest); if no supervisor is running it acts directly. It never
  touches running units it does not own.
- `host-snapshot` prints `MemAvailable` (GiB), CPU busy %, load1.

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

## Resource policy (enforced in code, verified against the live unit)

- **Admission**: max 2 concurrent workers. First worker needs
  MemAvailable >= 3.5 GiB; an additional worker needs >= 5 GiB, CPU busy
  < 75%, load1 < 10. A job refused while one of OUR workers is still running
  stays QUEUED and is rechecked on the next poll; it is DEFERRED (resumable,
  never failed) only when nothing of ours is running.
- **Sustained pressure** (3 samples, 5 s apart): MemAvailable < 3 GiB or CPU
  busy > 85% or load1 > 12 → ALL running workers are SIGINTed FIRST (each
  after its own ownership validation), then ONE shared 30 s checkpoint grace
  window elapses for all of them, then the stragglers are stopped; queued
  work is marked deferred, and the supervisor exits 75. A single worker's
  timeout uses the same per-unit SIGINT + 30 s grace. Deferred work is never
  auto-restarted — only an explicit `run`/`resume`.
- **Per-worker cgroup**: each worker runs in its OWN transient systemd user
  service with `MemoryHigh=1536M`, `MemoryMax=2G`, `MemorySwapMax=256M`,
  `CPUQuota=200%`, `TasksMax=128`, `OOMPolicy=kill`, `Nice=10`. The exact
  applied values are re-read from the unit and verified NUMERICALLY before
  the job is allowed to run; any mismatch fails closed (unit stopped, job
  failed). If systemd/cgroups are unavailable the queue refuses to run at
  all — never unbounded. `RuntimeMaxSec = timeout_s + 120` lets systemd end
  a worker even if the supervisor itself dies; `--expand-environment=no`
  keeps systemd from rewriting `$VAR` inside prompts.
- **CPU sampling**: `/proc/stat` deltas over the FIRST 8 counters only
  (guest/guest_nice are already inside user/nice — including them would
  double-count busy time).

## Ownership and signal safety

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

## Exit codes and completion honesty

- The worker CLI runs inside a `/bin/sh` wrapper IN the unit cgroup that
  redirects output to a log file and writes a durable exit receipt
  (`<job>.exit`). Transient units vanish after exit, so the receipt — not
  systemd — is the exit-code source of truth.
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
  which is the point: `TasksMax=128` bounds the whole tree).
- `resume`/`defer-all`/`status` never start work; only `run` does.
- No auto-retry: a failed job stays failed until a human runs `resume`.
- The wrapper writes the exit receipt to the logs dir; if the logs dir is
  on a different filesystem that fails to write, the job fails closed
  (missing receipt), which is the safe direction.
