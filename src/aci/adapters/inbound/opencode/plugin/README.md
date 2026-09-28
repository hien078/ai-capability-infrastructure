# ACI OpenCode Routing Plugin (Phase 11, plan §28; ADR-005)

A thin OpenCode V2 plugin that routes prompts through the AI Capability
Infrastructure and injects the selected skill IDs into the prompt. The
platform keeps the intelligence (eligibility → retrieval → rerank →
dependency resolution → composition, §14); OpenCode keeps the host behavior
(resolution, context, tools, permissions, execution loop).

The plugin contains **no** retrieval/rerank/composition logic and never
touches skill bodies, the filesystem, or permissions (ADR-005).

## Install

> Verified against OpenCode v2.0.18 (2026-09-28, §77). The config `plugin`
> array accepts **npm package names only** — an object entry
> `{"package": ..., "options": ...}` is silently skipped as malformed.

1. Copy this file into the project (or `~/.config/opencode/plugins/` for
   global) as a **real file, not a symlink** — bun resolves imports from
   the file's realpath, so a symlink back into this repo makes
   `@opencode/plugin` unresolvable:

   ```bash
   mkdir -p .opencode/plugins
   cp src/aci/adapters/inbound/opencode/plugin/index.ts \
      .opencode/plugins/aci-router.ts
   ```

   Files in the plugins directory load automatically at startup.

2. Declare the plugin's dependencies so bun can resolve them
   (`.opencode/package.json`):

   ```json
   {
     "dependencies": {
       "@opencode/plugin": "2.0.18",
       "@opencode/schema": "2.0.18"
     }
   }
   ```

   OpenCode runs `bun install` at startup; pin the versions to your
   OpenCode version. If the server was already running, restart it — a
   warm process caches failed module resolution.

3. Point OpenCode's native skill catalog at the platform (Phase 10) so the
   injected IDs resolve and lazy-load from the registry — never from a
   manually maintained local copy:

   ```jsonc
   {
     "skills": ["http://127.0.0.1:8000/opencode/skills/"]
   }
   ```

4. Start the platform: `uvicorn aci.main:app` (serves `/v1/routes` and the
   catalog).

The plugin runs on its built-in defaults (`baseUrl`
`http://127.0.0.1:8000`, `principalId` `opencode`, fail-open). To pass
options, publish it as an npm package and use the config tuple form
`["package-name", {"baseUrl": ..., "language": ...}]`, or edit the
defaults in the installed copy.

## Options

| Option             | Default                | Meaning                                                       |
| ------------------ | ---------------------- | ------------------------------------------------------------- |
| `baseUrl`          | `http://127.0.0.1:8000`| ACI REST base URL                                             |
| `principalId`      | `opencode`             | caller identity for the request envelope (§11.1)             |
| `organizationId`   | –                      | optional scope                                                |
| `workspaceId`      | –                      | optional scope                                                |
| `language`         | –                      | minimal TaskContext hint (§25.4)                              |
| `frameworks`       | `[]`                   | minimal TaskContext hints — never repo dumps or secrets       |
| `phase`            | –                      | e.g. `debugging`                                              |
| `maxItems`         | `5`                    | bundle budget (§19)                                           |
| `maxContextTokens` | `6000`                 | bundle budget (§19)                                           |
| `timeoutMs`        | `2000`                 | routing must never hang prompt admission                      |
| `failClosed`       | `false`                | fail-open default (ADR-005); `true` only for compliance flows|

## Behavior

- On every admitted prompt the plugin POSTs `/v1/routes` with the prompt text
  plus the minimal configured context, then pushes the 0–5 selected skill IDs
  into `prompt.skills`. OpenCode resolves them through the catalog above; only
  selected skills ever enter context (progressive disclosure, §28.3).
- **Fail-open (default):** routing errors/timeouts are logged and the prompt
  is admitted without platform-selected skills. **Fail-closed:** the hook
  rethrows and OpenCode does not admit the prompt (§28.4).
- The route run + bundle ids are stashed in the admission metadata under
  `aci` (`routeRunId`, `bundleId`). Report real evidence for the run via
  `POST /v1/outcomes` with those ids (§33) — multi-source verdicts, never a
  lone success flag (ADR-010).
- Local permissions stay authoritative: the plugin only adds skill IDs; it
  grants nothing (§28).

## Contract tests

`tests/integration/test_opencode_plugin_contract.py` pins the exact HTTP
contract this plugin depends on (request shape, response fields, stable §45
error bodies) against the live app, so adapter drift is caught in CI.
