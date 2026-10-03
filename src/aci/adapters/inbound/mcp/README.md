# ACI MCP adapter

MCP surface for the capability registry (plan §29; ADR-006; Phase 12). One
composition over the same services REST uses — no separate state, no
per-skill tools.

## Run

Two transports, one composition (§29.4 stateless — every request is served
fresh; durable state lives in the registry under `route_run_id`/`bundle_id`):

```bash
python -m aci.adapters.inbound.mcp                                  # stdio (default)
python -m aci.adapters.inbound.mcp --transport streamable-http      # HTTP on 127.0.0.1:8000
python -m aci.adapters.inbound.mcp --transport streamable-http --host 0.0.0.0 --port 9000
```

- **stdio** is the universal transport: local clients spawn the server as a
  child process.
- **streamable-http** serves the official SDK Streamable HTTP transport
  (stateless, JSON responses) at `http://<host>:<port>/mcp` for clients that
  cannot spawn processes. Binding a localhost host keeps the SDK's
  DNS-rebinding protection on; the token gate below applies either way.
- The same endpoint is **mounted in the FastAPI app** at `/mcp` — one process
  with REST/catalog/A2A (`uvicorn aci.main:app`), same `Container`, same
  stateless contract. The in-app mount does not validate `Host` (the bind
  host is uvicorn's decision, not the app's) — parity with every other ACI
  HTTP route; the perimeter is the token gate plus the deployment's network
  position.

Configuration is the same source as the REST app (`ACI_DATABASE_URL`,
`ACI_OBJECT_STORE_ROOT`, …). `ACI_API_TOKEN` gates the HTTP transport
exactly like the REST routes: set ⇒ every `/mcp` request needs
`Authorization: Bearer <token>` (constant-time compare, 401 otherwise);
unset ⇒ unauthenticated localhost mode (same rule as REST today). stdio has
no gate — it is a local child process.

## Surface

| Kind | Name | Notes |
| --- | --- | --- |
| Extension | `io.modelcontextprotocol/skills` | `skills/list`, `skills/get` (SEP-2640, Final) |
| Resource template | `skill://{skill_id}/{file_path}` | file bytes via core `resources/read`; manifest-whitelisted, sha256 re-verified every read |
| Tool | `route_capabilities` | full §14 pipeline → pinned bundle (0–5 items) + traces |
| Tool | `search_capabilities` | discovery-only (§11.2); eligibility runs only in routing |
| Tool | `report_outcome` | multi-source verdicts against a routed bundle (§33) |
| Tool | `run_agent_task` | **opt-in** (`ACI_MCP_AGENT_RUNS=1`): start a HarnessKernel run, return the terminal result + evidence |
| Tool | `get_agent_run` | **opt-in**: the shared run read model (same as `GET /v1/agent-runs/{id}`) |
| Tool | `cancel_agent_run` | **opt-in**: request cancellation; a terminal run answers `false` |

The agent-run tools call the SAME `AgentRunService` the REST routes use
(`rest/agent_runs.start_run` — one translation, one validation path), and
they are **OFF by default**: they are a new execution surface (a run executes
workspace code on the server host, sandboxed per `ACI_AGENT_SANDBOX`). Opting
in while `ACI_API_TOKEN` is unset logs a loud warning — the tools would be
reachable unauthenticated over `/mcp`. `ACI_AGENT_RUNS_TOKEN` guards only the
REST routes; the MCP transport is guarded by `ACI_API_TOKEN`.

Skill entries keep canonical `SKILL.md` paths (`skill://<capability_id>/SKILL.md`);
frontmatter passes through raw; every manifest file carries `sha256:` digest +
size. Unknown skill/file → JSON-RPC `-32602`; integrity failures → `-32603`.
Domain errors surface as tool execution errors carrying the stable §45 code
(`BUNDLE_NOT_FOUND: …`) so the model can self-correct.

## Client config

stdio (local client spawns the server):

```json
{
  "mcpServers": {
    "aci": {
      "command": "python",
      "args": ["-m", "aci.adapters.inbound.mcp"],
      "env": {
        "ACI_DATABASE_URL": "postgresql+psycopg://aci:aci@localhost:5432/aci",
        "ACI_OBJECT_STORE_ROOT": "/absolute/path/to/data/objects"
      }
    }
  }
}
```

> The stdio child inherits the client's cwd — use an ABSOLUTE
> `ACI_OBJECT_STORE_ROOT` (a relative root breaks blob reads when the
> client's cwd moves).

Streamable HTTP (remote clients, or one ACI process serving everything):

```json
{
  "mcpServers": {
    "aci": {
      "type": "http",
      "url": "http://aci.internal:8000/mcp",
      "headers": { "Authorization": "Bearer <ACI_API_TOKEN>" }
    }
  }
}
```

### Goose (stdio extension)

`~/.config/goose/config.yaml` — goose drives the tools + `skill://` resources
itself (its loop decides when to call `route_capabilities`):

```yaml
extensions:
  aci:
    type: stdio
    command: /path/to/.venv/bin/python
    args: ["-m", "aci.adapters.inbound.mcp"]
    envs:
      ACI_DATABASE_URL: postgresql+psycopg://aci:aci@localhost:5432/aci
      ACI_EMBEDDER: fastembed
      ACI_OBJECT_STORE_ROOT: /absolute/path/to/data/objects
```

### Claude Code (streamable HTTP)

```bash
claude mcp add --transport http aci http://aci.internal:8000/mcp \
  --header "Authorization: Bearer <ACI_API_TOKEN>"
```

(or `--transport stdio` with the command form above for a local setup)

## Contract notes

- **Stateless (§29.4):** no handler keeps per-session state; durable state lives
  in the registry under `route_run_id`/`bundle_id`. A brand-new session sees
  the same registry rows. Over HTTP this also means no server-side session
  tracking: each POST is a fresh transport (`json_response` mode — no GET SSE
  stream; a GET without `Accept: text/event-stream` answers 406).
- **Caching (§46):** `skills/list`/`skills/get` return `ttlMs: 0`,
  `cacheScope: "private"` — the listing is revocation-sensitive, so clients
  must re-fetch rather than serve it from shared caches.
- **No MCP types in core (ADR-004):** domain/application depend only on
  Protocols; all MCP wire shapes live in this adapter
  (`skills.py`/`tools.py`/`http.py`). Protocol revision changes are adapter-only edits.
- **Content over tools (§29.3):** skill bodies are never stuffed into tool
  results; clients lazily `resources/read` the files the manifest advertises.
- **Auth:** the HTTP transport reuses `rest/auth.py` — `ACI_API_TOKEN` set ⇒
  `Authorization: Bearer` required (401 otherwise, constant-time compare);
  unset ⇒ unauthenticated localhost mode. Pinned in
  `tests/security/test_mcp_http_gate.py`.
