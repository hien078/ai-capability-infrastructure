# AGENTS.md

> Pre-implementation repo. Single source of truth: `plan_v2_revised.md` (Architecture V2, 2026-09-27). No code, build, test, or CI exists yet. Follow the plan; do not invent a competing design.

## Build order (do not skip)

Follow `plan_v2_revised.md` §52 / §75 in order: contracts → foundation → registry → skill packages → provenance/license/security gates → eligibility → retrieval → reranker → composer → REST → OpenCode catalog → OpenCode routing plugin → MCP → outcomes → benchmark. Prove the V1 loop (§80) before V2/V3 expansion.

Start with: ADRs (§68), Pydantic schemas (`Capability`, `CapabilityVersion`, `CapabilityRelease`, `CapabilityBinding`, `TaskContext`, `CapabilityBundle`, `OutcomeEvidence`), DB schema + Alembic migrations from day one.

## Target stack and layout

- Stack (§50): Python, FastAPI, Pydantic, PostgreSQL + pgvector, SQLAlchemy, Alembic, pytest, Ruff, mypy/pyright, Docker Compose, pre-commit. Do NOT add Kafka, K8s, Neo4j, Redis/Celery, graph DB, model gateway, or microservices without measured need.
- Target layout (§42): `src/aci/{domain,application,providers,routing,adapters/{inbound,outbound},control_plane,evaluation,observability,config}/`, `sdk/{python,typescript}/`, `tests/{unit,integration,protocol,security,benchmark,end_to_end}/`, `migrations/`. Do not scaffold all future folders (services/agents/A2A) on day one.

## Domain invariants (will fail review if broken)

- One authoritative `Capability Registry`; no parallel Skill/Agent registries (§10, ADR-001).
- Separate immutable `CapabilityVersion` from mutable `CapabilityRelease` (channel/state pointer), `CapabilityBinding` (protocol exposure), and derived `CapabilityMetrics`. Promotion/rollback changes release pointers only, never mutates published versions (§6, ADR-003).
- `CapabilitySpec` is a discriminated union (`SkillSpec|ToolSpec|ResourceSpec|WorkflowSpec|ServiceSpec|AgentSpec`); no giant flat object with nullable fields (ADR-002).
- Kind-specific verbs only: skill→resolve/load/apply, resource→read, tool→invoke, workflow→instantiate/execute, service→call, agent→delegate. No universal `execute_capability()` (§8, §32).
- Domain/application layers must not import OpenCode, MCP, A2A, FastAPI, or SQLAlchemy types; depend on Protocols (`CapabilityRepository`, `EligibilityPolicy`, `CandidateRetriever`, `CapabilityReranker`, `DependencyResolver`, `BundleComposer`, …). All client/protocol logic lives in `adapters/inbound/{rest,mcp,opencode}/` (§28.5, §44, ADR-004).
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
