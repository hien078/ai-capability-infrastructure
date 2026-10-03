/**
 * xl-bench delegation tools (bench-side, job xl-harness — NOT product code).
 *
 * OpenCode custom tools that let the ORCHESTRATOR delegate code-writing
 * leaves to the ACI agent service (POST /v1/agent-runs) through the xl-bench
 * sidecar. The heavy lifting lives in `delegate_client.py` (stdlib-only
 * Python) so it runs identically inside the run sandbox; this file is the
 * thin OpenCode binding: spawn the client, pass a JSON payload on stdin,
 * return its JSON on stdout.
 *
 * Tool names (OpenCode: the filename is the default tool's name, named
 * exports become `<filename>_<export>`):
 *   delegate_to_aci        — snapshot the workspace, start an ACI leaf run.
 *   delegate_to_aci_check — poll a leaf; MERGE its changed files when done.
 *   delegate_to_aci_cancel— cancel a stale leaf (partial changes NOT merged).
 *
 * The sidecar URL comes from XL_SIDECAR_URL (set by the bench runner); the
 * ACI server's bearer token deliberately NEVER reaches the orchestrator —
 * only the sidecar holds it, on the host.
 */
import { tool } from "@opencode-ai/plugin"
import path from "path"

/** Absolute path of the stdlib python client shipped next to this file. */
function clientPath(context: { directory: string }): string {
  const here =
    typeof import.meta.path === "string" ? import.meta.path : undefined
  if (here) return path.join(path.dirname(here), "delegate_client.py")
  return path.join(context.directory, ".opencode", "tools", "delegate_client.py")
}

/** Run the python client once; return its JSON stdout as the tool result. */
async function runClient(
  sub: string,
  payload: Record<string, unknown>,
  context: { directory: string },
): Promise<string> {
  const proc = Bun.spawn({
    cmd: ["python3", clientPath(context), sub],
    cwd: context.directory,
    stdin: "pipe",
    stdout: "pipe",
    stderr: "pipe",
  })
  proc.stdin.write(JSON.stringify({ ...payload, workspace: context.directory }))
  proc.stdin.end()
  const [out, err, code] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ])
  if (code !== 0) {
    const tail = err.trim().slice(-1500)
    return JSON.stringify({
      ok: false,
      error: `delegate client exited ${code}${tail ? `: ${tail}` : ""}`,
    })
  }
  const text = out.trim()
  return text || JSON.stringify({ ok: false, error: "delegate client printed nothing" })
}

export default tool({
  description:
    "Delegate one self-contained code-writing LEAF subtask to the ACI agent " +
    "service. Snapshots this workspace, starts an ACI agent run on the " +
    "snapshot (max_turns <= 60), and returns a leaf_id immediately — the " +
    "leaf runs asynchronously; poll it with delegate_to_aci_check. The " +
    "objective must be a complete leaf contract: what to build/change, the " +
    "files involved, and the mechanical check that proves it " +
    "(verification_command, e.g. ['python','-m','pytest','-q']). The leaf " +
    "sees ONLY the snapshot taken now — it cannot see this conversation.",
  args: {
    objective: tool.schema
      .string()
      .describe("The leaf contract: goal, files, behavior, and its mechanical check."),
    verification_command: tool.schema
      .array(tool.schema.string())
      .optional()
      .describe("argv proving the leaf, e.g. ['python','-m','pytest','-q']."),
    constraints: tool.schema.array(tool.schema.string()).optional(),
    acceptance_criteria: tool.schema.array(tool.schema.string()).optional(),
    write_scopes: tool.schema
      .array(tool.schema.string())
      .optional()
      .describe("Workspace-relative write scopes (default: whole workspace)."),
    max_turns: tool.schema.number().int().min(1).max(60).optional(),
  },
  async execute(args, context) {
    return await runClient("delegate", args, context)
  },
})

export const check = tool({
  description:
    "Poll a delegated ACI leaf. While it runs, returns its status. When it " +
    "is terminal, MERGES the leaf's changed files into this workspace " +
    "(full contents, deterministic apply) and returns the run result: " +
    "status, stop_reason, verification evidence, changed files, and a " +
    "unified diff. A CANCELLED leaf is reported but NOT merged. Re-checking " +
    "a finished leaf is idempotent.",
  args: {
    leaf_id: tool.schema.string().describe("The leaf id returned by delegate_to_aci."),
  },
  async execute(args, context) {
    return await runClient("check", args, context)
  },
})

export const cancel = tool({
  description:
    "Cancel a delegated ACI leaf (stale plan, wrong direction). Its partial " +
    "changes are NOT merged into this workspace. Cancelled leaves still " +
    "report their diff on delegate_to_aci_check for inspection.",
  args: {
    leaf_id: tool.schema.string().describe("The leaf id returned by delegate_to_aci."),
  },
  async execute(args, context) {
    return await runClient("cancel", args, context)
  },
})
