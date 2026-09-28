# aci-coder

First-party delegated coding agent of the ACI platform.

## What it is

`aci-coder` is a `kind=agent` capability: an addressable agent that accepts
delegated tasks over A2A (`SendMessage`), executes them under an
`AgentProfile` (model class, tool grants, skill policy, budget — §56.1), and
returns the result as a task message. Execution semantics are
`delegated-autonomy`: the caller delegates a unit of work and receives a
terminal state, never a shared session.

## What it provides

- python implementation work
- debugging and root-cause analysis
- test writing and verification

## How it runs

The runtime (`ProfileDrivenAgentRuntime`) owns the lifecycle; the executor
(a model client against the deployment's OpenAI-compatible endpoint) does the
work. Required skills named by the profile's `SkillPolicy` are resolved
against active production releases — eligibility is never bypassed — and
their entry files are loaded from the immutable artifact store with content
re-verification. The agent's response text is returned verbatim as the task's
result message.

## Boundaries

- The platform never gains write authority from this profile (§76).
- Side effects are explicit via the profile's `ExecutionPolicy` (§32).
- Skill instructions never grant tool permissions; the executor's host owns
  execution.
