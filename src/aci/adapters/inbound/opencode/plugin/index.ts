/**
 * ACI routing plugin for OpenCode V2 (plan §28, §28.5; ADR-005; Phase 11).
 *
 * Thin by contract: the plugin contains NO retrieval, rerank, or composition
 * logic. It extracts a minimal TaskContext (§25.4 — hints only, never repo
 * dumps or secrets), calls POST /v1/routes on the platform, and injects the
 * 0–5 selected skill IDs into the prompt. OpenCode then resolves and lazily
 * loads those skills from the HTTP catalog (Phase 10) through its normal
 * path — this plugin never touches skill bodies, filesystems, or permissions.
 *
 * Failure policy (§28.4, ADR-005): fail-open by default — a routing outage
 * logs and admits the prompt without platform-selected skills. Set
 * `failClosed: true` only for compliance flows where admission must stop.
 *
 * Outcome instrumentation (§33): the route/bundle ids are stashed in the
 * admission metadata under `aci` so the client can report real evidence via
 * POST /v1/outcomes when the work finishes.
 *
 * Auth: when the platform sets ACI_API_TOKEN, export the same value as
 * ACI_API_TOKEN in OpenCode's environment; the plugin then sends
 * `Authorization: Bearer <token>` (and no header when it is unset).
 *
 * Install: see README.md. Verified against @opencode/plugin@2 (§77):
 * `ctx.session.hook("prompt", …)` receives a mutable draft whose
 * `prompt.skills` entries are `{ id }` (Skill.ID) and follow normal
 * resolution; `metadata` is a free-form record.
 */
import { Plugin } from "@opencode/plugin"
import type { Skill } from "@opencode/schema/skill"

export interface RouterOptions {
  /** ACI REST base URL. */
  baseUrl?: string
  /** Caller identity for the request envelope (§11.1). */
  principalId?: string
  organizationId?: string
  workspaceId?: string
  /** Minimal TaskContext hints (§25.4). Keep these hints — no repo dumps. */
  language?: string
  frameworks?: string[]
  phase?: string
  /** Bundle budgets (§19). Unset `maxContextTokens` = the server default
   * (ACI owns the one default; it budgets on real SKILL.md sizes). */
  maxItems?: number
  maxContextTokens?: number
  /** Routing must never hang prompt admission. */
  timeoutMs?: number
  /** Fail-open default (ADR-005); fail-closed only for compliance flows. */
  failClosed?: boolean
}

interface RouteResponse {
  route_run_id: string
  bundle: {
    bundle_id: string
    items: Array<{ capability_id: string; version: string; digest: string }>
  }
}

const DEFAULTS = {
  baseUrl: "http://127.0.0.1:8000",
  principalId: "opencode",
  maxItems: 5,
  timeoutMs: 2000,
  failClosed: false,
}

/** Bearer header from ACI_API_TOKEN; empty when unset (unauthenticated mode). */
function authHeaders(): Record<string, string> {
  const token = process.env.ACI_API_TOKEN
  return token ? { authorization: `Bearer ${token}` } : {}
}

/** Options after defaults are applied. */
type ResolvedOptions = RouterOptions & typeof DEFAULTS

async function routeCapabilities(
  options: ResolvedOptions,
  taskText: string,
): Promise<RouteResponse> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), options.timeoutMs)
  try {
    const response = await fetch(new URL("/v1/routes", options.baseUrl), {
      method: "POST",
      headers: { "content-type": "application/json", ...authHeaders() },
      signal: controller.signal,
      body: JSON.stringify({
        task: { text: taskText },
        context: {
          language: options.language,
          frameworks: options.frameworks ?? [],
          phase: options.phase,
        },
        constraints: {
          max_items: options.maxItems,
          max_context_tokens: options.maxContextTokens,
          allowed_kinds: ["skill"],
        },
        principal_id: options.principalId,
        organization_id: options.organizationId,
        workspace_id: options.workspaceId,
        client_type: "opencode",
      }),
    })
    if (!response.ok) {
      // Stable §45 error bodies: {"error": {"code": ..., "message": ...}}.
      const body = (await response.json().catch(() => ({}))) as {
        error?: { code?: string; message?: string }
      }
      throw new Error(
        `route failed (${response.status}): ${body.error?.code ?? "unknown"}`,
      )
    }
    return (await response.json()) as RouteResponse
  } finally {
    clearTimeout(timer)
  }
}

export default Plugin.define({
  id: "aci.router",
  async setup(ctx) {
    const options = { ...DEFAULTS, ...(ctx.options as RouterOptions) }

    await ctx.session.hook("prompt", async (event) => {
      let route: RouteResponse
      try {
        route = await routeCapabilities(options, event.prompt.text)
      } catch (error) {
        if (options.failClosed) {
          // Fail-closed: rethrow so OpenCode does not admit the prompt.
          throw error
        }
        // Fail-open (default): continue admission without routed skills.
        console.error(
          `[aci.router] routing unavailable, continuing without skills: ${error}`,
        )
        return
      }

      // Inject only the selected skills (progressive disclosure, §28.3).
      // They resolve through the normal catalog path; the plugin never
      // loads skill bodies itself.
      event.prompt.skills ??= []
      for (const item of route.bundle.items) {
        // Capability ids are valid OpenCode skill ids by construction
        // (kebab-case, §28.2 catalog mapping); assert through the brand.
        event.prompt.skills.push({ id: item.capability_id as Skill.ID })
      }

      // Stash ids for outcome instrumentation (§33): report real evidence
      // via POST /v1/outcomes with these ids when the work finishes.
      event.metadata = {
        ...event.metadata,
        aci: {
          routeRunId: route.route_run_id,
          bundleId: route.bundle.bundle_id,
        },
      }
    })
  },
})
