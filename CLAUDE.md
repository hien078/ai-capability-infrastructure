# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AI Capability Infrastructure (ACI) — a Capability Registry + Skill Intelligence + Multi-Adapter Delivery system for composable agent skills. Not a *generalized* agent platform, but it does have a real service-side agent execution plane (HarnessKernel, ADR-014) — capability WHAT, optional kernel HOW, client-side global DAG. Status: V1 + V2 complete, V3 alive (real executor + A2A), V4-1 Evaluation live, three real clients (OpenCode + Antigravity + Goose) — REAL-CLIENT ERA, operating.

- `AGENTS.md` — detailed repo state, gotchas, measured results, and build order. Read it before doing anything non-trivial; it is the operational source of truth alongside this file.
- `plan_v2_revised.md` — the architecture plan (single source of truth for design). Follow it; do not invent a competing design. `docs/adr/` holds 14 accepted ADRs (001–014) — read the relevant ADR before implementing its phase.
- `docs/architecture-current.md` — concise current-state overview (Vietnamese, 2026-10-01): what is actually implemented / enabled by default / built-but-frozen / merely proposed.
- `docs/plans/aci-improvement-2026-10.md` — the current actionable improvement plan (Vietnamese): staged reopening of targeted features, ownership, acceptance checks, rollback, measurement limits.

## Freeze decision (2026-09-29, amended 2026-09-30 — do not relitigate without new evidence)

The acquisition era (judges, refinery, crawler, canary) is **FROZEN**: built, test-green, documented — stop extending, do NOT remove. **Amendment 2026-09-30 (user decision, option A): the agent-runtime execution plane is UNFROZEN as HarnessKernel (ADR-014, `docs/plans/harness.md`) — `src/aci/domain/runtime/` + `src/aci/runtime/` + REST `/v1/agent-runs`.** The A2A wire surface itself stays exactly as built (no extension); crawl.md stays FROZEN. Unfreeze conditions are recorded in AGENTS.md.

## Commands

```bash
.venv/bin/python -m pytest -q            # all tests (integration/security live-DB ones skip without Postgres)
pytest -q tests/unit/test_foo.py::test_bar  # single test (pythonpath=src is set in pyproject.toml)
ruff check src tests migrations scripts && ruff format --check src tests migrations scripts   # both clean (line-length 100, rules E,F,I,UP,B)
uv pip install --python .venv/bin/python -r requirements-lock.txt   # deps are locked (hashed, universal 3.11-3.13); pyproject keeps >= ranges
uv pip compile pyproject.toml --extra dev --extra semantic --universal --python-version 3.11 --generate-hashes -o requirements-lock.txt   # regenerate after any dep change
.venv/bin/python -m mypy src             # strict, clean required
docker compose up -d db                  # pgvector/pgvector:pg16
.venv/bin/alembic upgrade head           # needs .venv on PATH; env.py reads ACI_DATABASE_URL first
alembic check                            # must stay clean (ORM ↔ migrations in sync)
.venv/bin/uvicorn aci.main:app --reload  # /health liveness, /ready readiness (SELECT 1)
```

CI (`.github/workflows/ci.yml`): `verify` job on a Python 3.11/3.12/3.13 matrix — install from `requirements-lock.txt` + `pip check` → ruff → format-check → mypy (strict, `python_version = "3.11"` floor) → `alembic upgrade head` → `alembic check` → `pytest -q` (pytest-timeout 300s/test) on a pgvector service; `security` job — `pip-audit` on the lock (fails on known vulns; ignore only via `--ignore-vuln <ID>` + justification), `bandit -c pyproject.toml -r src -ii` (justified skips in `[tool.bandit]`), gitleaks over full history.

## Architecture

Layered, dependency-inward, enforced by boundary tests (`tests/unit/test_architecture_boundaries.py`, `test_contracts.py`) — run them after any domain change:

- `src/aci/domain/` — pure Pydantic contracts (`capability`, `skills`, `provenance`, `policy`, `taxonomy`, `routing`). No FastAPI/SQLAlchemy/OpenCode/MCP imports anywhere in domain/application.
- `src/aci/application/` — use cases over Protocols (`CapabilityRepository`, `EligibilityPolicy`, `CandidateRetriever`, `CapabilityReranker`, `DependencyResolver`, `BundleComposer`, …).
- `src/aci/routing/` — the §14 pipeline: eligibility → retrieval (pgvector) → rerank → dependency resolve → compose. Each stage emits trace data.
- `src/aci/adapters/inbound/{rest,mcp,opencode,a2a}/` — all protocol/client logic. REST (`/v1/*`), OpenCode catalog (`/opencode/skills/…`), and the A2A gateway (`/.well-known/agent-card.json`, `/a2a` JSON-RPC) are mounted in `aci.main`; `create_app(container=None)` lets tests inject their own `Container`.
- `src/aci/adapters/outbound/{postgres,pgvector,model_provider,object_store}/` — SQLAlchemy repos, embedders (`HashingEmbedder` default, `FastEmbedEmbedder` behind `ACI_EMBEDDER=fastembed`), content-addressed blob store (`ACI_OBJECT_STORE_ROOT`, default `data/objects`, gitignored).
- `src/aci/providers/skills/` — skill ingestion (parse SKILL.md → hash → blobs → version/artifact → source record `ingestion_status='quarantined'` → provenance; ingestion state lives on `source_records.ingestion_status`, not on release channels; re-ingest identical = idempotent, changed content same version = `CAPABILITY_ALREADY_EXISTS`). `src/aci/providers/licensing/` + `src/aci/providers/security/` — SPDX detection and pattern scanner used as ADR-012 promotion gates.
- `src/aci/control_plane/promotion/` — promote/rollback/revoke = release-pointer moves only.
- `src/aci/evaluation/` — benchmark harness, `SMOKE_CASES` + `DEV_CASES` (the dev set is the standing instrument for any router change) + `HARNESS_CASES` (H001–H020, the kernel benchmark pack per `docs/plans/harness.md` Appendix D).
- `src/aci/domain/runtime/` + `src/aci/runtime/` — the HarnessKernel (ADR-014, `docs/plans/harness.md`): service-side agent runtime. Domain contracts (RunStatus/StopReason, GrantEnvelope, ToolSpec, EvidenceBundle/Pack, RuntimeSpec, ModelAction union, SubtaskContract, RunResult) + 13 engine managers (StateManager is the ONLY mutable state authority, INV-01; completion is verifier-gated, INV-08; child authority ⊆ parent, INV-02; delegation OFF by default, §0.3). SYNC, no FastAPI/SQLAlchemy in `src/aci/runtime/` — unit-testable with fakes (`FakeModelGateway`, `ScriptedModel`).
- `src/aci/domain/acquisition/` + `src/aci/application/{extract_candidates,refine,evaluate_compatibility,propose_promotion}.py` + `src/aci/providers/evaluation/` — the FROZEN acquisition era (candidate lifecycle, judge ensemble, refinery). ADR-013: automation may move candidates to STAGING/canary only; production promotion stays human-only (`scripts/capctl.py` is the human gate).

### Routing pipeline rules (ADR-008/009)

- Eligibility (status/channel/kind/trust/license/scope/compatibility) runs **before** retrieval; the reranker can never add or rescue ineligible candidates.
- Embed and rerank **trusted normalized summaries only** — raw third-party `SKILL.md` bodies are structurally unreachable (prompt-injection boundary).
- Empty bundle (0 items) is a valid success; never force a low-confidence skill.
- Telemetry: persist `route_runs` before `bundles` (`bundles.route_run_id` is the FK; `route_runs.bundle_id` is a plain pointer — never reverse).

### Domain invariants (review-blocking)

- One authoritative Capability Registry; no parallel Skill/Agent registries.
- `CapabilityVersion` is immutable and frozen; only `CapabilityRelease` pointers move. Identity is `capability_id + version`, never display name.
- `CapabilitySpec` is a discriminated union (`SkillSpec|ToolSpec|…`, discriminator `kind`); kind-specific verbs only — a test greps the domain for `execute_capability`.
- Faceted taxonomy, not a single tree; unknown facet name/value → `ValidationError`.
- Unknown license blocks production redistribution by default (§24); promotion (ADR-012) requires provenance chain + redistributable license + security scan passed.

## Testing conventions

- Integration tests (`pytest.mark.integration`) need live DB + `alembic upgrade head`, else skip. Use the `uid()` helper for unique ids; **never assert global emptiness** — the live DB is shared and accumulates rows.
- `tests/security/` codifies the minimum security cases (content boundaries, exposure boundaries, telemetry hygiene, acquisition boundaries per ADR-013) — extend it when adding wire surfaces.
- Postgres repos flush parent rows before raw-FK children (the UOW does not order raw-FK inserts).

## Gotchas

- **Two databases in the one container**: `aci` = dev/test (integration tests accumulate `uid()` rows — never treat its row counts as corpus size); `aci_bench` = operational (real corpus + telemetry). Ops scripts default to `aci_bench`, the app/tests default to `aci`. When checking "does X exist in production", query `aci_bench`. Never point a pytest run at `aci_bench` (fixture pollution once got ROUTED).
- System python lacks mypy — use `.venv/bin/python -m mypy` or `uvx mypy src`.
- When spawning `opencode run` programmatically, always export `PWD` — the CLI resolves its project from `$PWD`, not `getcwd()` (see `scripts/proof_loop.py` and the plugin README). Also: the opencode service caches the skills catalog — restart it after an ACI server restart, and rerun re-materialized task dirs in a fresh workdir.
- The §80 measurement loop (`scripts/proof_loop.py --level smoke|hard|multi|long|horizon`) is the standing A/E instrument; its final verdict (n=33): no single-bug acceptance delta at this model tier — value lives in process quality, not binary acceptance. Do not re-run it to "check" without a new axis (weaker model / harder corpus).
- The semantic embedder is selected by `ACI_EMBEDDER=fastembed` on the **server process** — verify via `route_runs.stages.retrieval.model_id` in telemetry, not by assumption (the lexical baseline silently confounded 4 §80 rounds).
- Agent-run processes (`run_command`, `verification_command`, H-bench post-hoc) run in the OS sandbox (ADR-014 amendment 13): bubblewrap on Linux (needs `bwrap` + unprivileged user namespaces), Seatbelt (`sandbox-exec`) on macOS — unusable = commands REFUSED; set `ACI_AGENT_SANDBOX=none` only on a disposable host. Seatbelt has NO mount namespace: workspaces must live outside any repo and shared work roots must be hidden (`run_hbench.work_root()` does both); every Mac H-bench round before `68bb135` is invalid — anchor-check a new host against the Linux archive before measuring (ADR-014 amendment 22).
- Do not grow the skill corpus for count; grow it targeted to domain gaps (§55). Do not tune router weights on tiny samples (§34) — use `DEV_CASES` paired A/B instead.
- PUBLIC-knowledge registry skills on the kernel path showed NO fix-rate value in any H-bench round (ADR-014 amendment 16/17). NON-public knowledge is different: E2 (K 0/20 → preloaded R 17/20) and E2B (registry + §14 router preload 19/20, hit rank 1 20/20; the same standard as a file in the repo only 9/20) — ADR-014 amendments 19/24. `ACI_AGENT_CAPABILITY_PRELOAD` still stays OFF and `request_capability` offered: flipping the default is an open USER decision that needs a pre-registered round on a real use case. E2C replicated on 7 new fixtures (F 8/24 vs Bp 17/24) and E2D replicated the direction on a second model family (dsv4: R 23/42 vs K 0/14); E2D also found the 12-turn budget binding (R@20 32/42 vs R@12 22/42) and skill FORMAT irrelevant (amendment 28). A registry-plane exception now marks a Bp/Bq row `invalid_reason=REGISTRY_UNAVAILABLE` — exclude it like MODEL_FAILURE; on the shared gateway prefer `--parallel 1` (parallel 3 lost ~60% of rows to MODEL_FAILURE). On the REAL OpenCode client (rc-bench, amendment 30) the ACI plugin did NOT beat OpenCode's native skills holding the same 45 skills (12/12 vs 12/12; native was cheaper) — rc-bench v2 (amendment 34) then tied on a DENSE 381-skill corpus too (OC-A 78/79 vs native 77/80; native picked the exact variant first in 77/80, router rank-1 only 26/40) — skill SELECTION is not OpenCode's bottleneck. Bench sandboxes must also isolate the NETWORK (`scripts/bench_sandbox.py`, `--unshare-net` + per-arm unix-socket bridges): unsandboxed-network no-skill runs port-scanned localhost and downloaded skills from the ACI server. Real-client benches must isolate each run (`scripts/rc_bench.py`: bwrap over all of /home + /.snapshots + /tmp, own PID ns) — btrfs snapshots under /home/.snapshots expose the repo. The kernel's own value over a naive loop is NOT shown (E1 n=136/arm; E1b found no fixture pool with headroom — amendments 18/23): kernel stays frozen for expansion.
- Composer v2 budgets bundles by the REAL `SKILL.md` size (`application/payload_sizes.py`), `DEFAULT_MAX_CONTEXT_TOKENS = 8000` (ADR-008 amended); the reranker is v3 (min-max calibrated). Any further ranking/budget change needs a paired A/B on `DEV_CASES`/`KERNEL_QUERY_CASES` (§34).
- **Operational host (2026-10-05): the 24/7 Ubuntu box `home-sever` (Tailscale 100.67.201.25, `ssh home-sever`) runs the ONE operational ACI** — repo clone `~/aci`, its own pgvector container (127.0.0.1:5432 only, `aci_bench` lives HERE), systemd --user `aci-server.service` (uvicorn bound to the tailnet IP :8000, Restart=always covers the boot-time IP race; `ACI_AGENT_RUNS_TOKEN` in `~/.config/aci/server.env`, mode 600) and `aci-backup.timer` (daily `scripts/backup_dbs.sh`, keep 14, same-disk — copy offsite). Unit files: `deploy/systemd/`. Deploy = `ssh home-sever 'cd ~/aci && git pull --ff-only && systemctl --user restart aci-server'` (after a lock change also `uv pip install -r requirements-lock.txt` + `uv pip install --no-deps -e .` — scripts need the editable install; the unit itself uses PYTHONPATH). All clients (OpenCode plugin + catalog, Goose/Antigravity MCP, Arch + Mac) point at `http://100.67.201.25:8000`. Ops scripts that mutate the corpus (`capctl`, `curate_public_skills`) run ON home-sever in `~/aci`. The old Mac server (`com.hien.aci-server`) is booted out + disabled; Arch's `aci` DB stays the dev/test DB, Arch's `aci_bench` is a STALE pre-cutover copy (do not use it). Ubuntu AppArmor blocks unprivileged userns → the bwrap agent sandbox is unusable there (agent-run commands would be REFUSED; no client uses agent-runs). `discernment-nudge` + `academy-guide` were REVOKED from production on 2026-10-05 (catch-all descriptions landed in 42/112 organic bundles).
- Arch DB boot race (2026-10-05, pre-cutover topology): a Tailscale-IP port in Arch's gitignored `docker-compose.override.yml` fails at boot and docker never retries (`docker start` does not republish ports — recreate). `scripts/ensure_db.sh` + `deploy/systemd/aci-db-ensure.{service,timer}` (installed on Arch) repair it.
- Corpus cleanup C1+C2 was applied to `aci_bench` and then ROLLED BACK by the user the same day (2026-10-01) — 0 relations again, 37 production releases unchanged; current routing = pre-cleanup. C3/C4 (claude-api split) deliberately deferred.
