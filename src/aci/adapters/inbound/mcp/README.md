# ACI MCP adapter

MCP surface for the capability registry (plan §29; ADR-006; Phase 12). One
composition over the same services REST uses — no separate state, no
per-skill tools.

## Run

```bash
python -m aci.adapters.inbound.mcp   # stdio server; config from ACI_* env
```

Stdio is the universal transport: local clients spawn the server as a child
process. Configuration is the same source as the REST app
(`ACI_DATABASE_URL`, `ACI_OBJECT_STORE_ROOT`, …) — there are no MCP-specific
settings.

## Surface

| Kind | Name | Notes |
| --- | --- | --- |
| Extension | `io.modelcontextprotocol/skills` | `skills/list`, `skills/get` (SEP-2640, Final) |
| Resource template | `skill://{skill_id}/{file_path}` | file bytes via core `resources/read`; manifest-whitelisted, sha256 re-verified every read |
| Tool | `route_capabilities` | full §14 pipeline → pinned bundle (0–5 items) + traces |
| Tool | `search_capabilities` | discovery-only (§11.2); eligibility runs only in routing |
| Tool | `report_outcome` | multi-source verdicts against a routed bundle (§33) |

Skill entries keep canonical `SKILL.md` paths (`skill://<capability_id>/SKILL.md`);
frontmatter passes through raw; every manifest file carries `sha256:` digest +
size. Unknown skill/file → JSON-RPC `-32602`; integrity failures → `-32603`.
Domain errors surface as tool execution errors carrying the stable §45 code
(`BUNDLE_NOT_FOUND: …`) so the model can self-correct.

## Client config

```json
{
  "mcpServers": {
    "aci": {
      "command": "python",
      "args": ["-m", "aci.adapters.inbound.mcp"],
      "env": { "ACI_DATABASE_URL": "postgresql+psycopg://aci:aci@localhost:5432/aci" }
    }
  }
}
```

## Contract notes

- **Stateless (§29.4):** no handler keeps per-session state; durable state lives
  in the registry under `route_run_id`/`bundle_id`. A brand-new session sees
  the same registry rows.
- **Caching (§46):** `skills/list`/`skills/get` return `ttlMs: 0`,
  `cacheScope: "private"` — the listing is revocation-sensitive, so clients
  must re-fetch rather than serve it from shared caches.
- **No MCP types in core (ADR-004):** domain/application depend only on
  Protocols; all MCP wire shapes live in this adapter
  (`skills.py`/`tools.py`). Protocol revision changes are adapter-only edits.
- **Content over tools (§29.3):** skill bodies are never stuffed into tool
  results; clients lazily `resources/read` the files the manifest advertises.
