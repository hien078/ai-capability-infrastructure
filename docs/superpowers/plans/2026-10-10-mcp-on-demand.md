# MCP on demand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ACI decides per prompt which MCP servers an OpenCode task needs; the OpenCode V2 plugin removes the other
servers' tools before every model request (fail-open to "all").

**Architecture:** MCP servers are registry capabilities (`kind=tool`, id `mcp:<name>`, ACI-authored trusted text). A
`JudgeToolServerRouter` (routing layer) reuses the JEV judge adapter with its own system prompt, runs in parallel with
the skill pipeline inside `RouteCapabilitiesService`, and returns `tool_servers` on `POST /v1/routes`. The plugin stores
the decision per session in its `prompt` hook, filters `tools` in its `context` hook, and registers `aci_enable_mcp`.

**Tech Stack:** Python 3.11+ (FastAPI, Pydantic v2, httpx), pytest, ruff, mypy strict; TypeScript plugin for OpenCode
V2 (`@opencode/plugin`), helper tests with `node --test` (Node ≥ 22 strips TS types; Mac/Arch have Node 26).

**Spec:** `docs/superpowers/specs/2026-10-10-mcp-on-demand-design.md` · **ADR:** `docs/adr/015-mcp-on-demand.md`

## Global Constraints

- Default OFF everywhere: `ACI_TOOL_SELECTOR=off` (server) and plugin option `toolSelection: false`.
- Fail-open: any selector failure / no candidates / selector off ⇒ `decision="all"`; plugin error ⇒ `tools` untouched.
- JEV prompt v7 text and every §8.1 JEV parameter stay byte-identical (gate v4 freeze).
- The judge sees ONLY ACI-authored trusted documents of `mcp:*` capabilities, never MCP `tools/list` text.
- `mcp:*` capabilities never enter skill retrieval or the skill bundle (`allowed_kinds` stays `["skill"]`).
- ACI never executes, proxies or stores credentials for MCP tools.
- Layering (boundary tests): no FastAPI/SQLAlchemy/httpx in `domain/` or `application/`.
- Privacy: organic task texts never leave home-sever `data/` / gitignored dirs; never read route_runs created after the
  gate-v4 freeze (`docs/plans/jev-reranker.md` §9) for anything.
- Every task: `./.venv/bin/python -m pytest -q tests/unit tests/security`, `ruff check src tests migrations scripts`,
  `ruff format --check src tests migrations scripts`, `./.venv/bin/python -m mypy src` all clean before its commit.

## Review Focus

1. Client sends no `tool_selection` (old plugin, Goose, curl) → response has `tool_servers.decision == "all"` and the
   skill bundle is unchanged — test in Task 4.
2. Judge returns an id not installed on the client or not `mcp:*` → dropped; if nothing valid remains the decision is
   still `select` with `[]` (task needs none) — test in Task 3.
3. Tool keys from the spike differ from `<server>_*` (e.g. `<server>.<tool>`) → plugin prefix match must use the exact
   separator recorded in Task 0 — test in Task 6.
4. Session with several prompts: the latest prompt's decision replaces the previous one, but `aci_enable_mcp` grants
   persist for the session — test in Task 6.
5. Selector slower than the skill pipeline → route latency = max of both, and a selector timeout never delays the
   bundle beyond the judge deadline — test in Task 4.

---

### Task 0: Spike — does the V2 `context` hook really shrink the model request? (GO / NO-GO)

**Files (throwaway, NOT committed):**
- Create: `/tmp/mcp-spike/plugin.ts`, `/tmp/mcp-spike/README.md` (findings)

- [ ] **Step 1: Write a throwaway plugin**

```ts
// /tmp/mcp-spike/plugin.ts — loaded via a temp opencode config `plugin` entry; NOT the ACI plugin.
import { Plugin } from "@opencode/plugin"
const DROP = (process.env.SPIKE_DROP || "github,memory,context7,duckdb,sysmgr").split(",")
export default Plugin.define({
  id: "spike.mcp",
  async setup(ctx) {
    await ctx.tool.transform((editor) => {
      editor.add({
        id: "spike_echo",
        description: "Spike: echoes its input.",
        input: { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
        async execute(input: any) { return { output: `echo:${input.text}` } },
      } as any)
    })
    await ctx.session.hook("context", async (event) => {
      const keys = Object.keys(event.tools)
      console.error(`[spike] tool keys sample: ${keys.slice(0, 40).join(" ")}`)
      if (process.env.SPIKE_ON !== "1") return
      for (const k of keys) if (DROP.some((s) => k.startsWith(`${s}_`) || k.startsWith(`${s}.`))) delete event.tools[k]
    })
  },
})
```

- [ ] **Step 2: Run the same prompt twice in fresh sessions** (Mac, a scratch dir outside any repo; `export PWD`):

```bash
mkdir -p /tmp/mcp-spike/work && cd /tmp/mcp-spike/work && export PWD=/tmp/mcp-spike/work ACI_ROUTER_DISABLED=1
SPIKE_ON=0 opencode run --standalone --model=home-gateway/OneNexus/glm-5.3 "Reply with exactly OK." 2>off.err
SPIKE_ON=1 opencode run --standalone --model=home-gateway/OneNexus/glm-5.3 "Reply with exactly OK." 2>on.err
SPIKE_ON=1 opencode run --standalone --model=home-gateway/OneNexus/glm-5.3 "Call the spike_echo tool with text hi, then reply with its output." 2>tool.err
```

(How to load the throwaway plugin: point a temp `OPENCODE_CONFIG` / project `opencode.json` `plugin` array at
`/tmp/mcp-spike/plugin.ts` — check `opencode --help` / docs for the V2 flag; do not edit `~/.config/opencode`.)

- [ ] **Step 3: Compare input tokens** on home-sever (read-only):

```bash
ssh home-sever 'python3 - <<EOF
import sqlite3,os
c=sqlite3.connect("file:"+os.path.expanduser("~/.9router/db/data.sqlite")+"?mode=ro",uri=True)
cols=[r[1] for r in c.execute("pragma table_info(usageHistory)")]
print(cols)
for r in c.execute("select * from usageHistory order by rowid desc limit 6"): print({k:v for k,v in zip(cols,r) if "token" in k.lower() or "model" in k.lower() or "time" in k.lower()})
EOF'
```

- [ ] **Step 4: Decide.** GO iff (a) the ON run's prompt/input tokens are lower than OFF by ≥ 70% of the measured
  schema size of the dropped servers (~6–10k tokens), (b) `[spike] tool keys sample` shows the exact MCP key form,
  (c) `tool.err`/output shows `spike_echo` was called. Write `/tmp/mcp-spike/README.md` with numbers + key separator.
  **NO-GO ⇒ stop the whole job, write the result file (see Final Output) and exit.**

### Task 1: Domain contracts

**Files:**
- Modify: `src/aci/domain/routing/models.py` (add after `RouteResult`), `src/aci/domain/capability/models.py`
  (`RouteCapabilitiesCommand`), `src/aci/application/protocols.py` (new protocol)
- Test: `tests/unit/test_tool_server_contracts.py`

**Interfaces:**
- Produces:
  - `ToolServerDecision(BaseModel, frozen)`: `decision: Literal["select","all"]`, `selected: list[str] = []`,
    `reason: str = Field("", max_length=500)`, `selector_version: str = ""`, `judge_status: str | None = None`,
    `latency_ms: int | None = None`; classmethod `all_(reason: str, selector_version: str = "") -> ToolServerDecision`.
  - `RouteResult.tool_servers: ToolServerDecision | None = None`.
  - `RouteCapabilitiesCommand.installed_tool_servers: list[str] | None = None`.
  - `ToolServerRouter(Protocol)`: `def decide(self, task_text: str, eligible: list[EligibleCandidate], installed: list[str]) -> ToolServerDecision: ...`

- [ ] **Step 1: Write the failing test**

```python
from aci.domain.capability.models import RouteCapabilitiesCommand
from aci.domain.routing.models import RouteResult, ToolServerDecision


def test_all_decision_factory():
    d = ToolServerDecision.all_("selector off", selector_version="1")
    assert d.decision == "all" and d.selected == [] and d.reason == "selector off"


def test_route_result_tool_servers_optional():
    assert "tool_servers" in RouteResult.model_fields
    assert RouteResult.model_fields["tool_servers"].default is None


def test_command_installed_servers_default_none():
    assert RouteCapabilitiesCommand.model_fields["installed_tool_servers"].default is None


def test_decision_is_frozen():
    d = ToolServerDecision(decision="select", selected=["mcp:github"])
    try:
        d.decision = "all"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ToolServerDecision must be frozen")
```

- [ ] **Step 2:** `./.venv/bin/python -m pytest -q tests/unit/test_tool_server_contracts.py` → FAIL (ImportError).
- [ ] **Step 3: Implement** the models/field/protocol exactly as in Interfaces (protocol docstring: "fail-open: any
  failure returns `ToolServerDecision.all_`; never raises").
- [ ] **Step 4:** rerun → PASS; run `tests/unit/test_architecture_boundaries.py tests/unit/test_contracts.py` → PASS.
- [ ] **Step 5: Commit** `feat(domain): ToolServerDecision + ToolServerRouter protocol (ADR-015)`.

### Task 2: Judge adapter accepts a system template (JEV default byte-identical)

**Files:**
- Modify: `src/aci/adapters/outbound/model_provider/judge.py` (constructor kwarg; `_judge` uses it)
- Create: `src/aci/adapters/outbound/model_provider/tool_server_prompt.py`
- Test: `tests/unit/test_tool_server_prompt.py`

**Interfaces:**
- Produces: `OpenAICompatSkillJudge(..., system_template: str | None = None)` (None ⇒ the existing v7
  `_SYSTEM_TEMPLATE`); `TOOL_SERVER_SYSTEM_TEMPLATE: str`, `TOOL_SELECTOR_PROMPT_VERSION = "1"`.

- [ ] **Step 1: Write the failing tests**

```python
import hashlib

import httpx

from aci.adapters.outbound.model_provider import judge as J
from aci.adapters.outbound.model_provider.tool_server_prompt import (
    TOOL_SELECTOR_PROMPT_VERSION,
    TOOL_SERVER_SYSTEM_TEMPLATE,
)
from aci.domain.routing.models import JudgeCandidate

V7_SHA256 = hashlib.sha256(J._SYSTEM_TEMPLATE.encode()).hexdigest()  # pinned at import of the frozen module


def _capture(template: str | None) -> str:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["system"] = json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"selected": [], "reason": "x"}'}}]})

    judge = J.OpenAICompatSkillJudge(
        base_url="http://gw/v1", api_key="k", model="m",
        client=httpx.Client(transport=httpx.MockTransport(handler)), system_template=template,
    )
    judge.judge("task", [JudgeCandidate(capability_id="a", document_text="d")], 2)
    return seen["system"]


def test_default_template_is_the_frozen_v7_prompt():
    assert _capture(None) == J._SYSTEM_TEMPLATE.replace("{max_select}", "2")
    assert J.JEV_PROMPT_VERSION == "7"


def test_custom_template_is_sent():
    assert _capture(TOOL_SERVER_SYSTEM_TEMPLATE).startswith("You decide which MCP servers")


def test_tool_server_template_rules():
    t = TOOL_SERVER_SYSTEM_TEMPLATE
    assert "unsure" in t and "select it" in t          # uncertainty ⇒ enabled
    assert "select none" in t.lower()                     # default = none
    assert TOOL_SELECTOR_PROMPT_VERSION == "1"
```

Also record the current v7 hash in the test file as a literal: run
`python -c "import hashlib,aci.adapters.outbound.model_provider.judge as J;print(hashlib.sha256(J._SYSTEM_TEMPLATE.encode()).hexdigest())"`
BEFORE editing judge.py and add `assert V7_SHA256 == "<that hex>"` in `test_default_template_is_the_frozen_v7_prompt`.

- [ ] **Step 2:** run → FAIL (module missing / unexpected kwarg).
- [ ] **Step 3: Implement.** In `__init__` add `system_template: str | None = None` → `self._system_template =
  system_template if system_template is not None else _SYSTEM_TEMPLATE`; in `_judge` replace
  `_SYSTEM_TEMPLATE.replace(...)` with `self._system_template.replace(...)`. Create `tool_server_prompt.py`:

```python
"""Tool-server selector prompt (ADR-015). Separate from the frozen JEV v7 skill prompt."""

TOOL_SELECTOR_PROMPT_VERSION = "1"

TOOL_SERVER_SYSTEM_TEMPLATE = (
    "You decide which MCP servers (tool bundles) a coding agent needs for ONE task.\n"
    "Each candidate is an MCP server with a short trusted description.\n"
    "Select none unless the task clearly needs that server's capability "
    "(e.g. driving a real browser, calling the GitHub API, fetching library docs).\n"
    "Editing code, running shell commands, reading or writing files and running tests need NO MCP server.\n"
    "If you are unsure whether a server is needed, select it.\n"
    'Return ONLY JSON: {"selected": ["<id>", ...], "reason": "<one sentence>"}.\n'
    "Select at most {max_select} ids, only from the candidate list."
)
```

- [ ] **Step 4:** run the new tests + `tests/unit/test_jev_judge_adapter.py tests/unit/test_jev_reranker.py` → PASS.
- [ ] **Step 5: Commit** `feat(judge): injectable system template; tool-server prompt v1 (JEV v7 byte-identical)`.

### Task 3: `JudgeToolServerRouter`

**Files:**
- Create: `src/aci/routing/tool_servers.py`
- Test: `tests/unit/test_tool_server_router.py`, `tests/security/test_tool_server_boundaries.py`

**Interfaces:**
- Consumes: `SkillJudge` (judge(task_text, candidates, max_select) -> JudgeVerdict), `build_trusted_document(version,
  model_id=..., now=...)` from `aci.routing.retrieval`, `CapabilityRepository.get_version(capability_id, version)`.
- Produces: `class JudgeToolServerRouter` with `__init__(self, judge: SkillJudge, capabilities: CapabilityRepository,
  *, selector_version: str = TOOL_SELECTOR_PROMPT_VERSION)` and `decide(task_text, eligible, installed) ->
  ToolServerDecision`; module constant `MCP_PREFIX = "mcp:"`; `class OffToolServerRouter` whose `decide` always returns
  `ToolServerDecision.all_("tool selector off")`.

Behavior of `decide`:
1. `pool = [c for c in eligible if c.kind == "tool" and c.capability_id.startswith("mcp:") and c.capability_id in installed]`.
2. `pool` empty → `all_("no installed+eligible MCP servers")`.
3. Candidates = `JudgeCandidate(capability_id, document_text=build_trusted_document(version...).text)` for each pool
   entry whose version exists (missing → skip).
4. `verdict = judge.judge(task_text, candidates, max_select=len(candidates))`.
5. `verdict.status != "ok"` → `all_(f"judge {verdict.status}")` with `judge_status`, `latency_ms`.
6. ok → `selected = [i for i in dict.fromkeys(verdict.selected) if i in {c.capability_id for c in candidates}]`;
   return `ToolServerDecision(decision="select", selected=selected, reason=verdict.reason[:500], judge_status="ok", ...)`.
7. Any exception → `all_("selector error: <TypeName>")` (never raises).

- [ ] **Step 1: Write the failing tests** (fake judge + fake repo; build `EligibleCandidate` with kind "tool"/"skill"):

```python
from datetime import UTC, datetime

from aci.domain.routing.models import JudgeVerdict
from aci.routing.tool_servers import JudgeToolServerRouter, OffToolServerRouter


class FakeJudge:
    def __init__(self, verdict): self.verdict, self.calls = verdict, []
    def judge(self, task_text, candidates, max_select):
        self.calls.append([c.capability_id for c in candidates]); return self.verdict


def test_selects_validated_subset(eligible, repo):
    judge = FakeJudge(JudgeVerdict(status="ok", selected=["mcp:playwright", "mcp:evil", "web-skill"], reason="e2e"))
    d = JudgeToolServerRouter(judge, repo).decide("run e2e", eligible, ["mcp:playwright", "mcp:github"])
    assert d.decision == "select" and d.selected == ["mcp:playwright"]
    assert judge.calls == [["mcp:github", "mcp:playwright"]] or sorted(judge.calls[0]) == ["mcp:github", "mcp:playwright"]


def test_skill_kind_never_offered(eligible, repo):
    judge = FakeJudge(JudgeVerdict(status="ok", selected=[]))
    JudgeToolServerRouter(judge, repo).decide("t", eligible, ["mcp:github", "web-skill"])
    assert all(i.startswith("mcp:") for i in judge.calls[0])


def test_empty_selection_is_select_none(eligible, repo):
    d = JudgeToolServerRouter(FakeJudge(JudgeVerdict(status="ok", selected=[])), repo).decide("fix bug", eligible, ["mcp:github"])
    assert d.decision == "select" and d.selected == []


def test_judge_failure_is_all(eligible, repo):
    for status in ("timeout", "error", "invalid_output"):
        d = JudgeToolServerRouter(FakeJudge(JudgeVerdict(status=status)), repo).decide("t", eligible, ["mcp:github"])
        assert d.decision == "all" and d.judge_status == status


def test_nothing_installed_is_all(eligible, repo):
    assert JudgeToolServerRouter(FakeJudge(JudgeVerdict(status="ok")), repo).decide("t", eligible, []).decision == "all"


def test_exception_is_all(eligible, repo):
    class Boom:
        def judge(self, *a): raise RuntimeError("x")
    assert JudgeToolServerRouter(Boom(), repo).decide("t", eligible, ["mcp:github"]).decision == "all"


def test_off_router_is_all(eligible):
    assert OffToolServerRouter().decide("t", eligible, ["mcp:github"]).decision == "all"
```

(If `JudgeVerdict` requires `model_id`, pass `model_id="fake"` in every fake verdict.) (Write the `eligible` and `repo` fixtures in the same file: two `mcp:*` tool candidates + one skill candidate; repo
returns a `CapabilityVersion` whose summary is ACI text. Copy the construction pattern from an existing test that
builds `EligibleCandidate` / `CapabilityVersion`, e.g. grep `EligibleCandidate(` in `tests/unit`.)

Security test (`tests/security/test_tool_server_boundaries.py`): build a repo version whose trusted summary is
"SAFE-TEXT" and assert the FakeJudge's received `document_text` contains "SAFE-TEXT" and that the router never reads
anything but `capabilities.get_version` (use a repo double that raises on any other attribute access).

- [ ] **Step 2:** run → FAIL. **Step 3:** implement per Behavior. **Step 4:** run → PASS.
- [ ] **Step 5: Commit** `feat(routing): JudgeToolServerRouter — fail-open MCP server selection (ADR-015)`.

### Task 4: Route service, REST, wiring, telemetry

**Files:**
- Modify: `src/aci/application/route_capabilities.py`, `src/aci/adapters/inbound/rest/schemas.py`,
  `src/aci/adapters/inbound/rest/routes.py`, `src/aci/adapters/inbound/rest/wiring.py`, `src/aci/config.py`
- Test: `tests/unit/test_route_tool_servers.py`, extend `tests/security/` with bundle-isolation test

**Interfaces:**
- Consumes: `ToolServerRouter`, `ToolServerDecision`, `RouteCapabilitiesCommand.installed_tool_servers`.
- Produces: `RouteCapabilitiesService(..., tool_servers: ToolServerRouter | None = None)` (keyword, last);
  REST request `tool_selection: ToolSelectionIn | None` with `servers: list[str]` (max 32 items, each ≤ 64 chars,
  pattern `^mcp:[a-z0-9][a-z0-9-]{0,62}$`); `Settings.tool_selector: str = "off"` (`off|judge`) and optional
  `tool_selector_model/effort/timeout_seconds` defaulting to the `jev_*` values.

Service behavior:
- If `command.installed_tool_servers is None` or `self._tool_servers is None` → `tool_servers =
  ToolServerDecision.all_("client did not request tool selection")` without any extra work.
- Else: load `candidates` once (already done), run `eligibility.filter(candidates, context, rules,
  allowed_kinds=["tool"])` separately for the tool pool, and submit `tool_servers.decide(task_text, kept,
  installed)` to a module-level `ThreadPoolExecutor(max_workers=4)` BEFORE retrieval; after composition call
  `future.result(timeout=judge_timeout + 2)`; on any exception → `all_("selector error")`.
- `stages["tool_selection"] = decision.model_dump(mode="json")`; `RouteResult(..., tool_servers=decision)`.
- Skill path unchanged: skill eligibility still uses `command.allowed_kinds`.

- [ ] **Step 1: Failing tests** — (a) no `tool_selection` in request → `tool_servers.decision == "all"` and bundle
  identical to a run without the feature; (b) with a fake router returning select [mcp:github] → response carries it
  and `stages.tool_selection.selected == ["mcp:github"]`; (c) a fake router sleeping 0.3 s while the skill path takes
  0.3 s → total latency < 0.5 s (parallel); (d) security: register an `mcp:github` tool capability as production
  eligible, route with `allowed_kinds=["skill"]` → no `mcp:` id in `bundle.items`; (e) invalid `servers` entry
  (`"github"` without prefix) → 422; (f) `ACI_TOOL_SELECTOR=judge` without JEV base URL/key → wiring raises ValueError
  (fail closed, same message style as `_build_reranker`). Use `create_app(container=...)` + FastAPI TestClient
  following existing route tests (grep `def test_.*route` in `tests/unit`).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run full `tests/unit tests/security` → PASS.
- [ ] **Step 5: Commit** `feat(routes): tool_servers decision on /v1/routes, parallel, default off (ADR-015)`.

### Task 5: Registry entries for the 6 MCP servers

**Files:**
- Create: `config/mcp_servers.yaml` (ACI-authored descriptions), `scripts/register_mcp_servers.py`
- Test: `tests/unit/test_register_mcp_servers.py`

Descriptions (exact text, maintainers may refine later through a new version):

```yaml
servers:
  - id: mcp:playwright
    side_effects: external_effect
    summary: "Drive a real web browser: open pages, click, fill forms, run or debug end-to-end UI tests, take screenshots, read page text and console logs."
  - id: mcp:github
    side_effects: remote_write
    summary: "Call the GitHub API: read or search repositories, files, issues and pull requests on github.com; create branches, issues or pull requests."
  - id: mcp:context7
    side_effects: read-only
    summary: "Fetch up-to-date documentation and code examples for a named third-party library or framework."
  - id: mcp:memory
    side_effects: local_write
    summary: "Persistent knowledge-graph memory across sessions: store and recall entities, relations and observations."
  - id: mcp:duckdb
    side_effects: local_write
    summary: "Run SQL queries against a local DuckDB analytics database file."
  - id: mcp:sysmgr
    side_effects: local_write
    summary: "Manage the user's machines and projects: system status, processes, run commands on registered machines, project plans and task lists."
```

- [ ] **Step 1:** find the existing registration path for a non-skill capability (`src/aci/adapters/inbound/rest/capabilities.py`
  and its application service). If it cannot create a `kind=tool` version with a trusted summary, STOP this task and
  record it as a blocker in the result file (do not invent a parallel registry).
- [ ] **Step 2: Failing test:** the script's `load_servers(path)` parses the YAML into `(id, ToolSpec(side_effects), summary)`
  tuples, rejects ids without `mcp:` and duplicate ids; `register(..., dry_run=True)` performs no writes.
- [ ] **Step 3:** implement the script: default `--dry-run`; `--apply` registers versions `1.0.0` into the DB given by
  `--database-url` (required, no default) as NON-production (quarantined/staging per the existing lifecycle).
  Production promotion is the user's `capctl` step — the script never promotes.
- [ ] **Step 4:** tests PASS. **Step 5: Commit** `feat(registry): MCP server capabilities config + registration script (non-production)`.

### Task 6: OpenCode plugin (prompt + context hooks, `aci_enable_mcp`, kill switch)

**Files:**
- Create: `src/aci/adapters/inbound/opencode/plugin/tool-selection.ts` (pure helpers, no `@opencode/*` imports),
  `src/aci/adapters/inbound/opencode/plugin/tool-selection.test.ts`
- Modify: `src/aci/adapters/inbound/opencode/plugin/index.ts`, `src/aci/adapters/inbound/opencode/plugin/README.md`

**Interfaces (tool-selection.ts):**

```ts
export type Decision = { decision: "select" | "all"; selected: string[] }
export interface SessionState { decision?: Decision; enabled: Set<string> }   // enabled = "mcp:<name>"
export function overridesFromText(text: string): string[]                     // "+mcp:github" → ["mcp:github"]
export function serverOfTool(key: string, aliases: Record<string, string>, sep: string): string | undefined
  // "github_search_code" with sep "_" and aliases {github:"mcp:github"} → "mcp:github"; unknown prefix → undefined
export function toolsToRemove(keys: string[], state: SessionState, aliases: Record<string, string>, sep: string): string[]
  // [] when no decision or decision "all"; else keys whose server is known AND not selected AND not enabled
export function noticeLine(state: SessionState, aliases: Record<string, string>): string | undefined
```

Plugin options added to `RouterOptions`: `toolSelection?: boolean` (default false), `mcpAliases?: Record<string,string>`
(default `{github:"mcp:github", playwright:"mcp:playwright", context7:"mcp:context7", memory:"mcp:memory",
duckdb:"mcp:duckdb", sysmgr:"mcp:sysmgr", "sysmgr-mac":"mcp:sysmgr"}`), `toolKeySeparator?: string` (default = the
separator recorded in Task 0).

- [ ] **Step 1: Failing tests** (`node --test src/aci/adapters/inbound/opencode/plugin/tool-selection.test.ts`):

```ts
import { test } from "node:test"
import assert from "node:assert/strict"
import { overridesFromText, serverOfTool, toolsToRemove, noticeLine } from "./tool-selection.ts"

const A = { github: "mcp:github", playwright: "mcp:playwright", "sysmgr-mac": "mcp:sysmgr" }

test("override parsing", () => {
  assert.deepEqual(overridesFromText("pls +mcp:github and +mcp:playwright"), ["mcp:github", "mcp:playwright"])
  assert.deepEqual(overridesFromText("no overrides"), [])
})
test("server of tool", () => {
  assert.equal(serverOfTool("github_search_code", A, "_"), "mcp:github")
  assert.equal(serverOfTool("sysmgr-mac_run_command", A, "_"), "mcp:sysmgr")
  assert.equal(serverOfTool("read", A, "_"), undefined)
})
test("no decision or all removes nothing", () => {
  assert.deepEqual(toolsToRemove(["github_x"], { enabled: new Set() }, A, "_"), [])
  assert.deepEqual(toolsToRemove(["github_x"], { decision: { decision: "all", selected: [] }, enabled: new Set() }, A, "_"), [])
})
test("select removes unselected, keeps selected, enabled and builtins", () => {
  const s = { decision: { decision: "select" as const, selected: ["mcp:playwright"] }, enabled: new Set(["mcp:sysmgr"]) }
  assert.deepEqual(
    toolsToRemove(["github_a", "playwright_b", "sysmgr-mac_c", "read", "aci_enable_mcp"], s, A, "_"),
    ["github_a"],
  )
})
test("notice lists disabled servers", () => {
  const s = { decision: { decision: "select" as const, selected: [] }, enabled: new Set<string>() }
  assert.match(noticeLine(s, A) ?? "", /mcp:github/)
  assert.equal(noticeLine({ enabled: new Set() }, A), undefined)
})
```

- [ ] **Step 2:** run → FAIL. **Step 3:** implement helpers, then wire `index.ts`:
  - module `const sessions = new Map<string, SessionState>()`;
  - `prompt` hook (existing): when `options.toolSelection`, send `tool_selection: { servers: [...new Set(Object.values(options.mcpAliases))] }`
    in the route request; store `{decision: route.tool_servers ?? {decision:"all",selected:[]}, enabled: (prev?.enabled ?? new Set()) ∪ overridesFromText(text)}`
    under `event.sessionID`; append `noticeLine` to `event.prompt.text` only when defined; on routing error store `decision: all`.
  - `ctx.session.hook("context", …)`: when `options.toolSelection`, `for (const k of toolsToRemove(Object.keys(event.tools), state, aliases, sep)) delete event.tools[k]`; wrap in try/catch → on error do nothing.
  - `ctx.tool.transform(editor => editor.add({ id: "aci_enable_mcp", description: "Enable an MCP server that ACI turned off for this session. Input: server name, e.g. github or mcp:github.", input: {type:"object",properties:{server:{type:"string"}},required:["server"]}, execute }))`
    where `execute` normalizes to `mcp:<name>`, rejects unknown names with the valid list, adds to `state.enabled`, returns `enabled mcp:<name>`.
    Register it only when `options.toolSelection` is true.
- [ ] **Step 4:** helper tests PASS; `./.venv/bin/python -m pytest -q tests/unit/test_opencode_plugin_env_contract.py` PASS.
- [ ] **Step 5: Commit** `feat(plugin): OpenCode V2 MCP on demand — context-hook filtering, aci_enable_mcp, default off`.

### Task 7: Offline evaluation script (tuning data only)

**Files:**
- Create: `scripts/tool_selector_eval.py`
- Test: `tests/unit/test_tool_selector_eval.py`

- [ ] **Step 1:** define the ground-truth join BEFORE running anything and write it in the script docstring:
  a session needs `mcp:x` iff any stored tool call in that OpenCode session has name `<x-alias><sep>…` or `<x-alias>.…`;
  the session's task text = its FIRST user message text; join to nothing else (no ACI DB needed).
  Input: a JSONL produced by a read-only exporter subcommand (`export --opencode-db PATH --out FILE`) run on each
  machine; rows `{session_id, machine, task_text, used: ["mcp:x", ...], created_at}`; output file mode 600, path
  under gitignored `data/mcp-eval/`.
- [ ] **Step 2: Failing tests** for the exporter on a tiny sqlite fixture (two sessions, one with a `github.search_code`
  call) and for `score(rows, decisions)` → `wrongly_disabled_rate`, `mean_tokens_saved` (using the per-server token
  sizes from the spec §1 table: github 4200, playwright 3700, memory 2900, sysmgr 1800, context7 1300, duckdb 0).
- [ ] **Step 3:** implement `export` + `run` (calls `JudgeToolServerRouter` with the real judge config via env, same
  env vars as `scripts/jev_eval.py`) + `score`. **Step 4:** tests PASS.
- [ ] **Step 5: Commit** `feat(eval): tool_selector_eval — export/run/score for MCP selection (tuning data only)`.

### Task 8: Tuning measurement (no gate, no production change)

- [ ] **Step 1:** export the Mac sessions created before this plan's commit (the worker has no Arch access; the lead
  exports Arch with the same subcommand).
- [ ] **Step 2:** run the selector on the Mac tuning rows on home-sever (copy dir `~/aci-mcp-eval-mac/`, read-only DB,
  one run), score it.
- [ ] **Step 3:** write the numbers into the result file. Do NOT tune the prompt more than 2 iterations; record every
  iteration.

## Final Output (Mac worker)

1. `git format-patch <base> --stdout > ~/Data/Projects/ACI/data/aci-improvement/mac/mcp-on-demand.patch`
2. `~/Data/Projects/ACI/data/aci-improvement/mac/mcp-on-demand-result.md`: Task 0 GO/NO-GO numbers FIRST (OFF vs ON
   input tokens, key separator, plugin tool callable), blockers, per-task status, test counts, Task 8 tuning numbers.
   No task texts.
