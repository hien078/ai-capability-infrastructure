# AI Capability Infrastructure

> Architecture V2 (`plan_v2_revised.md`). Pre-implementation; V1 builds a Capability Registry + Skill Intelligence + Multi-Adapter Delivery system — not an agent platform.

## Quickstart

```bash
cp .env.example .env          # if present; never commit secrets
docker compose up -d db       # PostgreSQL + pgvector
alembic upgrade head
uv run pytest -q              # or: pytest -q
uv run ruff check src tests
uv run mypy src
uv run uvicorn aci.main:app --reload
```

- `GET /health` — liveness, no DB check.
- `GET /ready` — readiness, checks DB connectivity.
- Runtime API (as it lands): `POST /v1/routes`, `POST /v1/capabilities/search`, `POST /v1/outcomes`, OpenCode catalog at `GET /opencode/skills/index.json`.

## Layout

`src/aci/` — `domain/` (no protocol/DB imports) → `application/` → `routing/` → `providers/skills/` → `adapters/inbound/{rest,mcp,opencode}/` → `control_plane/` → `evaluation/` → `observability/`. See `AGENTS.md` for invariants and build order.

## Rules that matter

- Published `CapabilityVersion` rows are immutable; promotion/rollback only moves `CapabilityRelease` pointers.
- Router may return 0 items; eligibility runs before retrieval; reranker never rescues ineligible content.
- Embed/rerank trusted summaries only, never raw `SKILL.md` bodies.
- Skill instructions never grant host permissions; OpenCode owns filesystem/shell/git/permissions.
- One authoritative registry; kind-specific verbs only (no `execute_capability`).
