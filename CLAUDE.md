# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AI Capability Infrastructure (ACI) — a Capability Registry + Skill Intelligence + Multi-Adapter Delivery system for composable agent skills. **Not** an agent platform. Status: V1 + V2 complete, V3 alive (real executor + A2A), V4-1 Evaluation live, two real clients (OpenCode + Antigravity) — REAL-CLIENT ERA, operating.

- `AGENTS.md` — detailed repo state, gotchas, measured results, and build order. Read it before doing anything non-trivial; it is the operational source of truth alongside this file.
- `plan_v2_revised.md` — the architecture plan (single source of truth for design). Follow it; do not invent a competing design. `docs/adr/` holds 13 accepted ADRs — read the relevant ADR before implementing its phase.

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
- Agent-run processes (`run_command`, `verification_command`, H-bench post-hoc) run in a bubblewrap sandbox (ADR-014 amendment 13): they need `bwrap` + unprivileged user namespaces or they are REFUSED; set `ACI_AGENT_SANDBOX=none` only on a disposable host.
- Do not grow the skill corpus for count; grow it targeted to domain gaps (§55). Do not tune router weights on tiny samples (§34) — use `DEV_CASES` paired A/B instead.
