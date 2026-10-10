# MCP on demand — ACI selects which MCP servers a prompt needs (design)

Status: **Approved** (2026-10-10, user: "làm 3 việc luôn"). Decisions taken with the user in chat: fallback = A+C,
granularity = per MCP server, decision mechanism = a separate tool-server judge (option 1).
ADR: `docs/adr/015-mcp-on-demand.md` (Accepted).

## 1. Why (measured 2026-10-10, not assumed)

OpenCode on both machines enables 6 MCP servers. Their `tools/list` schemas, sent with the model's tool
definitions:

| server | tools | ~tokens |
|---|---|---|
| github | 26 | 4,200 |
| playwright | 33 | 3,700 |
| memory | 9 | 2,900 |
| sysmgr / sysmgr-mac | 18 | 1,800 |
| context7 | 2 | 1,300 |
| duckdb | ? | did not answer initialize — unmeasured |
| **total** | | **~10k (Mac) – ~14k (Arch)** |

Actual use over the whole OpenCode history (`server.tool` calls in `opencode.db`): Mac 5/557 sessions (0.9%) used
any MCP (sysmgr 5, github 1); Arch 15/294 (5.1%) (playwright 9, context7 4, sysmgr 2, github 1). memory and duckdb:
zero uses ever. So > 95% of sessions carry ~10–14k tokens of tool definitions they never use, on every model call of
the agent loop.

Unverified assumption that gates the whole project: removing entries from the V2 `context` hook's `tools` record
removes their schemas from the model request (§6, step 0).

## 2. Goal and non-goals

**Goal:** per prompt, ACI decides which MCP servers the task needs; the OpenCode plugin disables the others for that
prompt. Success = the gate in §7.

**Non-goals (YAGNI):** per-tool selection; ACI executing tools or proxying MCP (credentials stay on the client; no new
execution plane — ADR-013/014 boundaries); Goose/Antigravity/Claude clients; saving RAM (MCP processes keep running —
only context/tokens are saved); changing JEV prompt v7 (frozen for gate v4).

## 3. Architecture

```
OpenCode prompt ──prompt hook──► aci-router.ts ──POST /v1/routes──► ACI (home-sever)
                                                                          ├─ skill pipeline (JEV v7, unchanged)
                                                                          └─ ToolServerSelector (new, parallel)
          ◄── bundle (skills) + tool_servers {decision, selected, reason, selector_version} ──┘
plugin: decision=select → `context` hook removes unselected MCP tools before every model request + notice line;
        `aci_enable_mcp` re-enables a server mid-turn; decision=all / any error → change nothing (today's behavior)
```

### 3.1 Registry: MCP servers as capabilities

- Each MCP server = one capability of kind `tool` (existing `ToolSpec`; no new kind), id `mcp:<name>`
  (`mcp:github`, `mcp:playwright`, `mcp:context7`, `mcp:memory`, `mcp:duckdb`, `mcp:sysmgr`).
- `side_effects` set per server (github `remote_write`, playwright `external_effect`, …) — informational.
- The routing document is a short description **written by ACI maintainers** (trusted), e.g. "playwright: drive a
  real browser — run/debug E2E tests, screenshots, page interaction". The servers' own `tools/list` descriptions are
  third-party text and are NEVER shown to the judge (same prompt-injection boundary as SKILL.md bodies, ADR-008/009).
- Normal release lifecycle applies (production channel, human promotion via capctl); one registry, no parallel list.
- Client name mapping: the client's configured server name may differ (`sysmgr-mac` on Arch). The plugin maps
  `mcp:<name>` to local servers by an explicit alias table in plugin options; unknown → ignored.

### 3.2 ToolServerSelector (ACI, application/adapter layers)

- Protocol `ToolServerSelector` in `application/` (pure), adapter `OpenAICompatToolServerJudge` in
  `adapters/outbound/model_provider/` reusing the JEV judge plumbing (base URL, key, in-flight limiter, total deadline,
  never-raise boundary, SSE handling). Own prompt + own `TOOL_SELECTOR_PROMPT_VERSION`; JEV v7 untouched.
- Input: task text (bounded like today) + the eligible `mcp:*` capabilities' trusted descriptions.
- Output: `{"selected": ["mcp:..."], "reason": "..."}`; instructions = "select none unless the task clearly needs
  the server; if you are unsure whether a server is needed, select it" (uncertainty ⇒ enabled, no separate
  confidence field).
- Decision rule: judge ok → `decision="select"` with the validated subset (unknown ids dropped); anything else
  (timeout, error, invalid output, limiter busy, no installed+eligible servers) → `decision="all"`.
- Runs in parallel with the skill pipeline (thread pool in the route use case); route latency = max, not sum.
- Settings: `ACI_TOOL_SELECTOR` = `off` (default) | `judge`; model/effort/timeout reuse `ACI_JEV_*` unless
  overridden by `ACI_TOOL_SELECTOR_*`. Default `off` ⇒ response always `decision="all"` (no behavior change).

### 3.3 API

`POST /v1/routes` response gains an optional field (backward compatible — old clients ignore it):

```json
"tool_servers": {"decision": "select" | "all", "selected": ["mcp:playwright"],
                 "reason": "<bounded>", "selector_version": "1"}
```

Request: a new optional `tool_selection: {"servers": ["mcp:github", ...]}` lists the servers the client has installed
(the selector only chooses among those ∩ eligible registry entries); absent ⇒ no selection, `decision="all"`.
`constraints.allowed_kinds` stays `["skill"]`: `mcp:*` capabilities NEVER enter skill retrieval or the bundle — the
selector reads the eligible `mcp:*` entries through its own path (eligibility still applies: status/channel/trust).

### 3.4 OpenCode plugin (`src/aci/adapters/inbound/opencode/plugin/index.ts`, deployed as `aci-router.ts`)

The deployed client is **OpenCode V2 (2.0.22, `@opencode/plugin`)**. Verified in its plugin type definitions
(`node_modules/@opencode/plugin/dist/promise/{session,tool}.d.ts`), not yet at runtime:

- `ctx.session.hook("prompt", …)` — already used for skills; it now also stores the `tool_servers` decision per
  `sessionID` in plugin memory (latest prompt wins).
- `ctx.session.hook("context", …)` — runs before **every model request** of the agent loop with a mutable
  `tools: Record<name, {description, input}>`. When the session's decision is `select`, the hook deletes the entries
  of every configured MCP server that is neither selected nor enabled on request (key form, e.g. `github_search_code`
  vs `github.search_code`, is recorded in step 0).
- `ctx.tool.transform(editor => editor.add(...))` — the plugin registers one small tool, `aci_enable_mcp`
  (`{server: string}`): calling it adds the server to the session's enabled set, so its tools reappear from the
  **next model request in the same turn** (fallback A without waiting for the next prompt). Unknown servers → error
  result listing the valid names.
- The prompt hook appends one notice line: "MCP off for this prompt: … — call `aci_enable_mcp` if you need one".
- Override: `+mcp:<name>` in the prompt text enables that server for the session.
- `decision="all"`, no stored decision, ACI error/timeout, any plugin exception → `context` hook leaves `tools`
  untouched.
- Plugin option `toolSelection: false` = kill switch (default false until the §7 gate passes).

## 4. Data flow per prompt

1. User/orchestrator prompt → plugin `prompt` hook.
2. Plugin POSTs `/v1/routes` (timeout 10 s, unchanged).
3. ACI: [eligibility → retrieval → JEV → compose (skills only)] ‖ [eligible `mcp:*` ∩ client-installed →
   ToolServerSelector] → persist route_run (stages gain `tool_selection`) → respond.
4. Plugin applies skills (as today), stores the decision for the session, adds the notice.
5. Before every model request the `context` hook removes the disabled MCP tools; `aci_enable_mcp` calls widen the
   session's set for the following requests.

## 5. Error handling (principle C: any doubt ⇒ today's behavior)

| situation | result |
|---|---|
| selector timeout / error / invalid output / busy | `decision="all"` |
| judge unsure | selects the server (enabled) |
| ACI unreachable, plugin error | no change |
| selected id not in registry | dropped (subset-only) |
| selected server not installed on client | ignored |
| `+mcp:x` in prompt | x always on |
| selector disabled (`ACI_TOOL_SELECTOR=off`) | `"all"` |

Telemetry (`route_runs.stages.tool_selection`): decision, selected ids, judge status, latency, version.
No new task-text storage. Security tests: the judge never sees MCP-provided descriptions; response never contains
credentials/config; selector cannot add servers outside the eligible registry set.

## 6. Build order

0. **Spike (go/no-go):** a throwaway V2 plugin whose `context` hook deletes the tools of 5 MCP servers; send the same
   prompt in two fresh `opencode run` sessions (plugin on / off) and compare input tokens recorded by the Ubuntu
   9router (`usageHistory`). Go only if input tokens drop by ≈ the measured schema size AND a plugin-added tool is
   callable; record the exact MCP tool key form. No-go ⇒ stop and report.
1. Registry entries `mcp:*` with trusted descriptions (curated by the lead, promoted by the user via capctl).
2. `ToolServerSelector` protocol + judge adapter + route wiring (parallel) + API field + telemetry + unit/security tests.
3. Plugin `prompt` + `context` hooks, `aci_enable_mcp` tool, override, notice, kill switch; plugin tests.
4. Offline eval script `scripts/tool_selector_eval.py` over §7 data.
5. Gate run, then (user decision) enable.

Implementation by the Mac OpenCode worker in a worktree (TDD), reviewed by the lead — same process as JEV.

## 7. Measurement and gate (pre-registered here, before any selector output exists)

- **Ground truth from real use:** a session "needs mcp:x" iff it actually called a tool of x. Source: `opencode.db`
  on Mac + Arch, joined to ACI `route_runs` task text by time + principal (exact join rule fixed in the eval script
  before running). Tuning set = all sessions up to the freeze (≈ 20 positive sessions, hundreds negative).
- **Held-out:** sessions created after the freeze commit; evaluated once when it holds **≥ 15 positive sessions**
  (any MCP) and ≥ 50 negative sessions.
- **Pass = ALL of (held-out):**
  1. wrongly-disabled rate (positive sessions whose needed server would be off) **≤ 10%**;
  2. mean tool-definition tokens saved per prompt **≥ 5,000** (from the measured per-server sizes);
  3. route p95 latency increase **≤ 1 s** vs the same day's JEV-only routes;
  4. **100%** of selector failures returned `decision="all"`;
  5. `tests/security` green.
- Online after enabling: count `+mcp:` overrides and "tool not available" mentions per week; > 5% of prompts needing an
  override = rollback (`ACI_TOOL_SELECTOR=off`).

Risk: positives are rare (~1–5% of sessions) → the wrongly-disabled rate is noisy (1 case ≈ 5–7%); hence the
"unsure ⇒ select" instruction and the "prefer enabling" default.

## 8. Rollback

Server: `ACI_TOOL_SELECTOR=off` + restart (always returns `"all"`). Client: plugin option `toolSelection: false`, or
restore the plugin backup. Neither touches the JEV skill path.

## 9. ADR-015 draft (to be accepted by the user before step 1)

**Title:** MCP servers as routed capabilities, enabled per prompt by the client.
**Decision:** register MCP servers as `tool` capabilities with ACI-authored descriptions; a separate selector returns a
per-prompt server set; the client enforces it locally. ACI never executes or proxies tools and never holds MCP
credentials. Default off; fail-open to "all".
**Consequences:** saves context only (not RAM); depends on OpenCode V2's `context` hook `tools` record (verified in
step 0);
adds one judge call per prompt (parallel); one registry preserved; production enablement stays a user decision behind
the §7 gate.
