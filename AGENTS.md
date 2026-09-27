# AGENTS.md

> Single source of truth: `plan_v2_revised.md` (Architecture V2, 2026-09-27). Foundation only (Phase 0–1): domain contracts, `/health` + `/ready`, 13 unit tests. No CI, no pre-commit config, no DB migrations yet, no registry/persistence/routing/adapters code. Follow the plan; do not invent a competing design.

## Commands (verified)

- `.venv/bin/python -m pytest -q` (or plain `pytest -q`) — 50 tests: 29 unit + 21 integration (live-DB ones skip without Postgres). Works with no install (`pyproject.toml` sets `pythonpath = ["src"]`); full deps live in `.venv/`.
- `ruff check src tests` + `ruff format --check src tests` — both clean required (line-length 100, rules `E,F,I,UP,B`).
- `.venv/bin/python -m mypy src` — strict, clean. System python lacks mypy; use `.venv` or `uvx mypy src`.
- DB: `docker compose up -d db` (`pgvector/pgvector:pg16`, service `db`), then `alembic upgrade head` (needs `.venv` on PATH: `.venv/bin/alembic`). `migrations/env.py` reads `ACI_DATABASE_URL` first, falls back to `alembic.ini`.
- App: `.venv/bin/uvicorn aci.main:app --reload` (`/health` = liveness, `/ready` = sync `SELECT 1` against live DB). Verified both return `{"status": ...}`.
- CI (`.github/workflows/ci.yml`): ruff → format-check → `alembic upgrade head` → `pytest -q` on `pgvector:pg16` service.

## Repo state and gotchas

- Real code is `src/aci/domain/capability/{models,errors}.py`, `src/aci/domain/skills/models.py`, `src/aci/{config,main}.py`, `src/aci/observability/{logging,tracing}.py`, `src/aci/application/protocols.py` (repo/object-store/source-record Protocols, no SQLAlchemy imports), `src/aci/adapters/outbound/postgres/{base,orm,repositories,source_records}.py`, `src/aci/adapters/outbound/object_store/fs.py` (content-addressed, sha256 keys), `src/aci/providers/skills/{parser,package,canonicalization,ingestion}.py`. The `routing/`, `control_plane/`, `evaluation/` dirs from the plan (§42) do not exist yet — create them in build order, not upfront.
- Locked decisions live in `docs/adr/001–004*.md` (Accepted: capability abstraction, one registry, plane separation, adapter boundary). ADR-005–012 are still plan-only bullet points in `docs/adr/README.md`.
- Boundary tests in `tests/unit/test_architecture_boundaries.py` + `test_contracts.py` enforce the invariants below — run them after any domain change; extend them when adding layers. `tests/integration/` (`pytest.mark.integration`) needs live DB + `alembic upgrade head`, otherwise skips. Integration tests must use unique ids (`uid()` helper) — the live DB is shared and persists across runs.
- Migrations: `0001` pgvector extension, `0002` registry tables + immutability trigger, `0003` `source_records` provenance. `alembic check` must stay clean (ORM ↔ migrations in sync). Ingestion flow: parse SKILL.md → hash files → store blobs → create version/artifact → release pointer channel `raw` (quarantine) → provenance record; re-ingest identical content is idempotent, changed content under the same version raises `CAPABILITY_ALREADY_EXISTS`.

## Build order (do not skip)

Follow `plan_v2_revised.md` §52 / §75 in order: contracts ✅ → foundation ✅ → registry ✅ → skill packages ✅ → provenance/license/security gates → eligibility → retrieval → reranker → composer → REST → OpenCode catalog → OpenCode routing plugin → MCP → outcomes → benchmark. Prove the V1 loop (§80) before V2/V3 expansion.

## Target stack and layout

- Stack (§50): Python, FastAPI, Pydantic, PostgreSQL + pgvector, SQLAlchemy, Alembic, pytest, Ruff, mypy/pyright, Docker Compose, pre-commit. Do NOT add Kafka, K8s, Neo4j, Redis/Celery, graph DB, model gateway, or microservices without measured need.
- Target layout (§42): `src/aci/{domain,application,providers,routing,adapters/{inbound,outbound},control_plane,evaluation,observability,config}/`, `sdk/{python,typescript}/`, `tests/{unit,integration,protocol,security,benchmark,end_to_end}/`, `migrations/`. Do not scaffold all future folders (services/agents/A2A) on day one.

## Domain invariants (will fail review if broken)

- One authoritative `Capability Registry`; no parallel Skill/Agent registries (§10, ADR-002 file `002-one-canonical-registry.md`).
- Separate immutable `CapabilityVersion` (frozen Pydantic model) from mutable `CapabilityRelease` (channel/state pointer), `CapabilityBinding` (protocol exposure), and derived `CapabilityMetrics`. Promotion/rollback changes release pointers only, never mutates published versions (§6).
- `CapabilitySpec` is a discriminated union (`SkillSpec|ToolSpec|ResourceSpec|WorkflowSpec|ServiceSpec|AgentSpec`, discriminator `kind`); no giant flat object with nullable fields.
- Kind-specific verbs only: skill→resolve/load/apply, resource→read, tool→invoke, workflow→instantiate/execute, service→call, agent→delegate. No universal `execute_capability()` (§8, §32) — a test greps the domain for it.
- Domain/application layers must not import OpenCode, MCP, A2A, FastAPI, or SQLAlchemy types; depend on Protocols (`CapabilityRepository`, `EligibilityPolicy`, `CandidateRetriever`, `CapabilityReranker`, `DependencyResolver`, `BundleComposer`, …). All client/protocol logic lives in `adapters/inbound/{rest,mcp,opencode}/` (§28.5, §44, ADR-004 file).
- Faceted taxonomy (domain/task_type/technology/concern/…), not a single tree. `evaluator/memory/integration/model` are roles, not top-level kinds (§7, §9).
- DB release state is authoritative; `corpus/raw` style folders are convenience only. Identity is stable `capability_id + version`, never display name alone (§21, §60.10).

## Routing pipeline rules

- Order: eligibility/policy filter → facet/metadata filter → pgvector retrieval (10–30 candidates) → rerank → dependency resolve (`REQUIRES`/`CONFLICTS_WITH`/`CHECKS` only in V1) → compose 0–5 items → version+digest pin + validation. Each stage emits trace data (§14–§19).
- Empty bundle (0 items) is a valid success; never force a low-confidence skill (§2.7, ADR-008).
- Eligibility (revoked, channel, license, trust, client/protocol compatibility, tenant scope) runs before retrieval; reranker must never rescue ineligible content (ADR-009).
- Embed and rerank only trusted normalized summaries (name/description/provides/facets/derived summary), never raw third-party `SKILL.md` bodies — prompt-injection boundary (§16.1, §17.1).
- Cache only immutable content (descriptors, release maps, embeddings, catalog, artifacts) keyed by version/digest; revocation bypasses cache; never cache authz indefinitely (§46).

## Adapter rules

- OpenCode (preferred, §28): thin plugin `prompt → minimal TaskContext → POST /v1/routes → inject 0–5 skill IDs → native lazy load from `GET /opencode/skills/index.json` + `GET /opencode/skills/{id}/{file}` → local execution → `POST /v1/outcomes`. Prefer `autoinvoke:false`; default fail-open, fail-closed only for compliance flows. OpenCode owns filesystem/shell/git/permissions; skill instructions never grant tool permissions.
- MCP (§29): expose skills via MCP Skills extension (`skills/list|get`, `resources/read`), plus small stable tools `route_capabilities` / `report_outcome` (optional `search_capabilities`). No per-skill tools. Stateless requests; persist state via `request_id/route_run_id/bundle_id` only.
- No A2A runtime in V1; no A2A for in-process calls. SDKs wrap REST, never sit beside protocols as peers (§31, ADR-007).
- Verify protocol specs at implementation time (§77): current OpenCode V2 catalog/hook semantics, MCP revision + Skills/Tasks extension support. Protocol changes = adapter-only updates.
- Minimal `TaskContext` (language/frameworks/phase/file hints, short summary). Never send `.env`/tokens/keys/secrets or raw repo dumps for routing (§25.4, §26).

## Data, outcomes, benchmarks

- Postgres tables (§41): `capabilities, capability_versions, capability_releases, capability_bindings, capability_facets/facet_nodes, capability_relations, capability_artifacts/artifact_files, provenance_events/source_records/license_assessments/security_assessments, embedding_documents/vectors, route_runs/route_candidates/bundles/bundle_items, outcome_events/verdicts, benchmark_cases/runs/results, policy_snapshots`. Enforce `UNIQUE(capability_id,version)`, FK bundle items to exact versions, content-addressed object storage with per-file SHA-256 + package digest (§39–§41).
- Outcomes: multi-source evidence (`test_harness/static_analysis/external_evaluator/human_review/agent_self_report/…` with confidence), never a single `{"success":true}`; `unknown` stays `unknown` (§33, ADR-010).
- Benchmarks judge end-to-end task outcomes (variants A–E: alone / manual skills / retrieval / +rerank / +composer) on 10-task smoke → 30–50 dev set, not router scores alone; label metric associations as non-causal without controls (§34–§35, §70).
- Security tests (§61) must cover: malicious SKILL.md, instruction override, denied shell, hash mismatch, revoked-but-cached skill, cross-workspace access, unknown-license promotion, unsafe scripts, adversarial reranker metadata, secret leakage in telemetry. Unknown license blocks production redistribution by default (§24).
- V1 scope (§49): registry + skill provider + provenance/licensing + eligibility + pgvector retrieval + baseline reranker + composer + REST + OpenCode catalog/plugin + MCP adapter + outcome ingestion + telemetry + benchmark harness. Explicitly out: A2A/agent runtime, remote execution, memory/model gateway, crawler, auto-promotion, self-modifying skills.
