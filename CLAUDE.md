# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AI Capability Infrastructure (ACI) — a Capability Registry + Skill Intelligence + Multi-Adapter Delivery system for composable agent skills. **Not** an agent platform. Architecture V2; V1 is complete through the benchmark arena.

- `AGENTS.md` — detailed repo state, gotchas, measured results, and build order. Read it before doing anything non-trivial; it is the operational source of truth alongside this file.
- `plan_v2_revised.md` — the architecture plan (single source of truth for design). Follow it; do not invent a competing design. `docs/adr/` holds 12 accepted ADRs — read the relevant ADR before implementing its phase.

## Commands

```bash
.venv/bin/python -m pytest -q            # all tests (integration/security live-DB ones skip without Postgres)
pytest -q tests/unit/test_foo.py::test_bar  # single test (pythonpath=src is set in pyproject.toml)
ruff check src tests && ruff format --check src tests   # both must be clean (line-length 100, rules E,F,I,UP,B)
.venv/bin/python -m mypy src             # strict, clean required
docker compose up -d db                  # pgvector/pgvector:pg16
.venv/bin/alembic upgrade head           # needs .venv on PATH; env.py reads ACI_DATABASE_URL first
alembic check                            # must stay clean (ORM ↔ migrations in sync)
.venv/bin/uvicorn aci.main:app --reload  # /health liveness, /ready readiness (SELECT 1)
```

CI (`.github/workflows/ci.yml`): ruff → format-check → `alembic upgrade head` → `pytest -q` on a pgvector service.

## Architecture

Layered, dependency-inward, enforced by boundary tests (`tests/unit/test_architecture_boundaries.py`, `test_contracts.py`) — run them after any domain change:

- `src/aci/domain/` — pure Pydantic contracts (`capability`, `skills`, `provenance`, `policy`, `taxonomy`, `routing`). No FastAPI/SQLAlchemy/OpenCode/MCP imports anywhere in domain/application.
- `src/aci/application/` — use cases over Protocols (`CapabilityRepository`, `EligibilityPolicy`, `CandidateRetriever`, `CapabilityReranker`, `DependencyResolver`, `BundleComposer`, …).
- `src/aci/routing/` — the §14 pipeline: eligibility → retrieval (pgvector) → rerank → dependency resolve → compose. Each stage emits trace data.
- `src/aci/adapters/inbound/{rest,mcp,opencode}/` — all protocol/client logic. REST (`/v1/*`) and OpenCode catalog (`/opencode/skills/…`) are mounted in `aci.main`; `create_app(container=None)` lets tests inject their own `Container`.
- `src/aci/adapters/outbound/{postgres,pgvector,model_provider,object_store}/` — SQLAlchemy repos, embedders (`HashingEmbedder` default, `FastEmbedEmbedder` behind `ACI_EMBEDDER=fastembed`), content-addressed blob store (`ACI_OBJECT_STORE_ROOT`, default `data/objects`, gitignored).
- `src/aci/providers/skills/` — skill ingestion (parse SKILL.md → hash → blobs → version/artifact → release pointer `raw` → provenance). `src/aci/providers/licensing/` + `src/aci/providers/security/` — SPDX detection and pattern scanner used as ADR-012 promotion gates.
- `src/aci/control_plane/promotion/` — promote/rollback/revoke = release-pointer moves only.
- `src/aci/evaluation/` — benchmark harness, `SMOKE_CASES` + `DEV_CASES` (the dev set is the standing instrument for any router change).

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
- `tests/security/` codifies the ten minimum security cases (content boundaries, exposure boundaries, telemetry hygiene) — extend it when adding wire surfaces.
- Postgres repos flush parent rows before raw-FK children (the UOW does not order raw-FK inserts).

## Gotchas

- System python lacks mypy — use `.venv/bin/python -m mypy` or `uvx mypy src`.
- When spawning `opencode run` programmatically, always export `PWD` — the CLI resolves its project from `$PWD`, not `getcwd()` (see `scripts/proof_loop.py` and the plugin README).
- Do not grow the skill corpus for count; grow it targeted to domain gaps (§55). Do not tune router weights on tiny samples (§34) — use `DEV_CASES` paired A/B instead.
