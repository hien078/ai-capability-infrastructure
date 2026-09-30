# harness.md — ACI V2 Unified Super Harness Plan

**Version:** 2.0-unified  
**Date:** 2026-09-29  
**Status:** Master architecture + implementation plan  
**Scope:** ACI Capability Intelligence + service-side AgentRuntime Harness + specialized runtime profiles  
**Purpose:** Canonical merged plan replacing the separate `PLANHARNESS.md` and `planharness1.md` working drafts.

---

## 0. How to Read This Document

This document merges two complementary plans:

- the **runtime-internals plan**, which is strongest on lifecycle, state, ToolRuntime, authority, guardrails, delegation, checkpoints, concurrency, recovery, RuntimeServices, security, durability, and production hardening;
- the **ACI-integration/profile plan**, which is strongest on client-orchestrator boundaries, dual `route_only` / `execute` surfaces, four loop families, nine AgentProfiles, evidence-oriented acceptance, ACI feedback loops, benchmark design, and open-source mechanism mining.

This is not a concatenation. Overlapping concepts have been normalized into one canonical vocabulary and one ownership model.

### 0.1 Canonical architectural rule

> **ACI Capability Intelligence decides WHAT reusable capability is appropriate.  
> AgentRuntime / HarnessKernel decides HOW one delegated objective is executed.  
> The client-side global orchestrator owns the global goal and global Task DAG.**

### 0.2 Canonical naming decisions

Use these names throughout implementation:

```text
HarnessKernel
├── RunController
├── StateManager
├── ContextEngine
├── PlanningStrategy
├── CapabilityRuntime
├── ToolRuntime
├── AuthorityManager
├── GuardrailManager
├── DelegationManager
├── WorkspaceManager
├── CheckpointCoordinator
├── RecoveryManager
└── VerificationManager

RuntimeServices
├── ModelGateway
├── StateStore
├── CheckpointStore
├── ArtifactStore
├── SandboxBackend
├── CredentialBroker
├── Clock
├── IdGenerator
└── EventBus / Telemetry
```

Normalization decisions:

```text
ContextManager       → ContextEngine
Planner              → PlanningStrategy
CapabilityManager    → CapabilityRuntime
CheckpointManager    → CheckpointCoordinator

EvidenceBundle
    = full internal verification/evidence object

EvidencePack
    = compact client-facing projection of EvidenceBundle

ResultContract
    = specification of what a valid result must contain

RunResult
    = actual terminal/paused runtime result
```

### 0.3 Resolved design tensions

#### Delegation

Infrastructure exists in V2, but:

```text
service-side recursive delegation = OFF by default
```

for the primary nine AgentProfiles.

Reason:

```text
Client global orchestrator already owns global decomposition.
```

Bounded local delegation can be enabled later when benchmark evidence shows a clear benefit.

Recommended initial policy:

```yaml
delegation:
  enabled: false
  max_depth: 1
  max_children: 3
```

#### One source of truth

`StateManager` is the only mutable runtime-state authority.

Other components:

```text
read snapshots
emit commands/events
return typed results
```

They do not maintain competing mutable copies of current runtime state.

#### Stop reasons

Use a stable top-level enum plus optional detailed code:

```yaml
stop_reason: VERIFICATION_FAILED
detail_code: REGRESSION_TEST_FAILED
```

This avoids an exploding flat enum while preserving observability.

#### Verification

The runtime MUST NOT transition to successful completion solely because the model says the task is complete.

Priority:

```text
deterministic evidence
> environment evidence
> independent evaluator
> model self-assessment
```

#### Tool execution

All side effects go through explicit `ToolRuntime`.

No direct shell/file/network side effects from `RunController`.

#### Policy and enforcement

```text
AuthorityManager
    decides what is permitted

WorkspaceManager / ToolRuntime / SandboxBackend
    enforce the decision
```

### 0.4 Dual product surfaces

ACI supports two distinct client surfaces:

```text
A. Capability-as-a-Service

POST /v1/routes
    ↓
Capability Intelligence
    ↓
1–5 capabilities
    ↓
Client


B. Agent-as-a-Service

POST /v1/agent-runs
    ↓
Capability Intelligence
    ↓
CapabilityBundle
    ↓
HarnessKernel
    ↓
specialized AgentProfile
    ↓
verified ResultContract
    ↓
Client
```

### 0.5 Global ownership map

```text
CLIENT GLOBAL ORCHESTRATOR
owns:
    user conversation
    global goal
    global Task DAG
    cross-subtask decisions
    delegation strategy
    global replanning
    accept / reject
    final synthesis

ACI CAPABILITY PLANE
owns:
    eligibility
    retrieval
    reranking
    JEV / elite judging
    compatibility
    dependency resolution
    capability composition
    capability policy

ACI SERVICE AGENT
owns:
    one delegated objective
    local task state
    local context
    local planning
    tools
    workspace
    capability usage
    local repair
    self-verification

OFFLINE CONTROL PLANE
owns:
    crawl
    normalize
    deduplicate
    LLM analyze / rewrite
    classify
    benchmark
    license
    security
    promotion
    versioning
```

---

# PART I — SYSTEM PLACEMENT AND ACI BOUNDARIES

## 1. Unified ACI Map

```text
══════════════════════════════════════════════════════════════════════
                         CLIENT PLANE
══════════════════════════════════════════════════════════════════════

USER
 ↓
GLOBAL ORCHESTRATOR
Codex / Claude / another host
 ↓
Task assessment
 ↓
global planning / Task DAG
 ↓
SubtaskContract
 │
 │ MCP / REST
 ▼

══════════════════════════════════════════════════════════════════════
                    ACI CAPABILITY PLANE
══════════════════════════════════════════════════════════════════════

Request Runner
 ↓
RoutingContext Builder
 ↓
Intent Understanding
 ↓
Eligibility
 ↓
Hybrid Retrieval
 ↓
Rerank
 ↓
Progressive Loading
 ↓
LLM Elite Judge / JEV
 ↓
Compatibility Graph
 ↓
Dependency Resolution
 ↓
Capability Composer
 ↓
Policy Gate
 ↓
CapabilityBundle
 │
 ├───────────────────── route_only=true ───────────────────► Client
 │
 └───────────────────── execute=true
                              │
                              ▼

══════════════════════════════════════════════════════════════════════
                     ACI AGENT RUNTIME PLANE
══════════════════════════════════════════════════════════════════════

HarnessKernel
 ↓
AgentProfile
 ↓
Context + Local Plan
 ↓
Tools + CapabilityRuntime
 ↓
Execution Loop
 ↓
Verification
 ↓
Recovery if necessary
 ↓
EvidenceBundle
 ↓
EvidencePack
 ↓
RunResult / ResultContract projection
 │
 ▼

══════════════════════════════════════════════════════════════════════
                      CLIENT ACCEPTANCE
══════════════════════════════════════════════════════════════════════

GLOBAL ORCHESTRATOR
 ↓
ACCEPT / REJECT / REVISE / REPLAN
 ↓
update Global Task DAG

══════════════════════════════════════════════════════════════════════
                    OFFLINE CONTROL PLANE
══════════════════════════════════════════════════════════════════════

Discover
 ↓
Crawl
 ↓
Normalize
 ↓
Deduplicate
 ↓
LLM Analyze
 ↓
Rewrite
 ↓
Classify
 ↓
Benchmark
 ↓
Security
 ↓
License
 ↓
Promote
 ↓
Version
 ↓
Capability Warehouse

══════════════════════════════════════════════════════════════════════
                  OBSERVABILITY / LEARNING
══════════════════════════════════════════════════════════════════════

routing traces
agent traces
capability usage
tool evidence
verification evidence
client outcomes
harness benchmarks
 ↓
gap / health / deprecation / harness proposals
 ↓
Offline Control Plane
```


## 2. Fundamental Boundaries

### 2.1 Global orchestrator vs service agent

#### Client global orchestrator owns

```text
User goal
Global conversation
Global Task DAG
Cross-subtask decisions
Global constraints
Delegation
Acceptance / rejection
Global replanning
Final synthesis
```

#### ACI service agent owns

```text
One delegated objective
Local context
Local workspace
Local plan
Local retries
Tool usage
Capability usage
Self-verification
Execution evidence
```

Critical invariant:

> A service agent MAY propose changes to the global plan, but MUST NOT mutate the global Task DAG directly.

Example:

```yaml
status: blocked

blocker:
  type: missing_prerequisite
  description: database migration is required first

suggested_followup:
  objective: create schema migration for refresh-token table
```

The client orchestrator decides whether that follow-up becomes a global task.

---

### 2.2 Capability Plane vs Runtime Plane

#### Capability Plane

Answers:

> Which capabilities are appropriate for this task?

Pipeline:

```text
RoutingContext
    ↓
Intent Understanding
    ↓
Eligibility
    ↓
Hybrid Retrieval
    ↓
Rerank
    ↓
Progressive Loading
    ↓
LLM Elite Judge / JEV
    ↓
Compatibility Graph
    ↓
Composer
    ↓
Policy Gate
    ↓
CapabilityBundle
```

#### Runtime Plane

Answers:

> Given this subtask and this CapabilityBundle, how do I finish the delegated objective?

Pipeline:

```text
SubtaskContract
+
CapabilityBundle
+
AgentProfile
    ↓
HarnessKernel
    ↓
Execution Loop
    ↓
Verifier
    ↓
EvidencePack
    ↓
ResultContract
```

---

### 2.3 Request Runner vs Agent RunController

These MUST remain separate.

#### Request Runner

Purpose:

```text
HTTP/MCP request lifecycle
validation
route request
return response
```

Expected duration:

```text
milliseconds
```

#### Agent RunController

Purpose:

```text
multi-turn runtime lifecycle
model calls
tool calls
retries
checkpoint
verification
termination
```

Expected duration:

```text
seconds → minutes
```

Names should remain distinct in code:

```text
routing/request_runner.py
runtime/harness/run_controller.py
```

---

### 2.4 Routing Context vs Runtime Context

#### RoutingContext

Minimal semantic information necessary to choose capabilities:

```yaml
task_summary:
intent:
domains:
constraints:
client:
requested_agent_profile:
risk_level:
project_summary:
```

#### RuntimeContext

The local agent working set:

```yaml
subtask:
relevant_files:
repo_map:
local_plan:
observations:
tool_results:
loaded_capabilities:
decision_notes:
workspace_refs:
verification_state:
```

Never reuse one giant context object for both.

---


## 3. Target Architecture

```text
════════════════════════════════════════════════════════════════════════
                         CLIENT ENVIRONMENT
════════════════════════════════════════════════════════════════════════

USER
 │
 ▼
GLOBAL ORCHESTRATOR
Codex / Claude / another host
 │
 ├─ maintain global goal
 ├─ decompose
 ├─ create SubtaskContract
 ├─ delegate
 ├─ inspect ResultContract
 ├─ accept/reject
 └─ update global Task DAG
 │
 │ MCP / REST
 ▼

════════════════════════════════════════════════════════════════════════
                              ACI
════════════════════════════════════════════════════════════════════════

                     Inbound Adapter
                           │
                           ▼
                     Request Runner
                           │
                    ┌──────┴──────┐
                    │             │
               route_only       execute
                    │             │
                    ▼             ▼
            Capability Plane   Capability Plane
                    │             │
                    ▼             ▼
             CapabilityBundle CapabilityBundle
                    │             │
                    ▼             ▼
                  Client     Agent Runtime Plane
                                  │
                                  ▼
                           HarnessKernel
                                  │
                                  ▼
                           AgentProfile
                                  │
                                  ▼
                         Local Execution Loop
                                  │
                                  ▼
                            Verification
                                  │
                                  ▼
                            EvidencePack
                                  │
                                  ▼
                           ResultContract
                                  │
                                  ▼
                                Client
```

---


# PART II — CANONICAL HARNESS RUNTIME BLUEPRINT


## 1. Goals

### 1.1 Primary goals

The v2 harness MUST:

- execute long-running agent tasks with bounded cost and bounded authority;
- expose an explicit, inspectable run lifecycle;
- support deterministic cancellation and stop reasons;
- keep one authoritative runtime state;
- manage context as a scarce resource;
- dynamically request and load capabilities from ACI;
- validate every tool invocation before side effects;
- separate authorization policy from actual workspace enforcement;
- support local, sandboxed, and remote workspaces;
- support child-agent delegation without privilege or budget escalation;
- checkpoint and resume interruptible runs;
- classify failures and recover according to policy;
- verify completion using evidence rather than trusting model self-declaration;
- provide complete telemetry for debugging, evaluation, cost, and ACI feedback;
- allow every major subsystem to be replaced without rewriting the rest of the runtime.

### 1.2 Secondary goals

The v2 harness SHOULD:

- run in-process for lightweight local use;
- run behind a server boundary for production or remote execution;
- support multiple model providers through one gateway;
- support MCP tools without making MCP a core domain abstraction;
- support capability hot-loading and unloading;
- support deterministic tests without real LLM calls;
- support streaming;
- support human approval and resumable interrupts;
- support limited parallel tool execution where declared safe;
- support future multi-agent orchestration without redesigning authority/state/budgets.

---


## 2. Explicit Non-Goals for v2

Do **not** turn v2 into all of the following at once:

- a general-purpose distributed workflow engine;
- an autonomous self-modifying agent platform;
- an unrestricted multi-agent swarm;
- a global memory system;
- a replacement for the ACI registry/retrieval/reranking stack;
- a mandatory graph runtime for every request;
- a universal Docker-only execution environment;
- an agent that automatically grants itself new permissions;
- a system where LLM outputs are treated as policy decisions;
- a system where "model says done" equals task completion;
- a system that loads every skill/tool into every model context.

These are either out of scope or deferred to v3.

---


## 4. Core Architectural Invariants

These are not suggestions. Code review should reject implementations that violate them without an explicit architecture decision record.

### INV-01 — One mutable source of truth

`StateManager` is the only owner of mutable runtime state.

Other components:

- receive immutable/read-only snapshots or scoped views;
- emit events/commands;
- return typed results;
- do not keep their own competing "current task state".

```text
BAD:
Planner.current_step = 4
StateManager.task.current_step = 3
Checkpoint.task.current_step = 2

GOOD:
StateManager.task.current_step = 4
Planner receives TaskStateSnapshot(step=4)
```

### INV-02 — Child authority cannot exceed parent authority

\[
Authority(child) \subseteq Authority(parent)
\]

No capability, model, child agent, tool, or MCP server may self-escalate.

### INV-03 — Child budget is carved out of parent budget

\[
Budget(child) \le ReservedBudget(parent)
\]

Spawning child agents must not multiply effective budget.

### INV-04 — Policy decides; enforcement enforces

- `AuthorityManager` decides what may happen.
- `WorkspaceManager`, `ToolRuntime`, `SandboxBackend`, and credential scopes enforce that decision.

### INV-05 — Capability intelligence and capability runtime are separate

- ACI selects capabilities.
- `CapabilityRuntime` loads/binds/activates them.

### INV-06 — No side effect before validation and authorization

Any side-effecting operation must pass:

1. schema validation;
2. pre-tool guardrail;
3. authority evaluation;
4. approval if required;
5. execution envelope construction.

### INV-07 — Model output is untrusted input

Every model-produced:

- tool call;
- plan;
- capability request;
- completion claim;
- structured result

must be validated before use.

### INV-08 — Completion is verifier-gated

The model may propose `TASK_COMPLETE`; only `VerificationManager` may transition the run to verified success when verification is required.

### INV-09 — Context is budgeted

No subsystem may append arbitrary unbounded text to model context.

### INV-10 — Tool output is bounded

All observations have size/token limits and a truncation/offload strategy.

### INV-11 — Stop reasons are first-class data

Every run ends or pauses with a machine-readable stop reason.

### INV-12 — Cancellation is cooperative by default, enforceable at workspace boundary

The loop cooperatively checks cancellation. Sandboxed/remote execution must also support hard termination for runaway processes.

### INV-13 — Checkpoint only safe, serializable state

Do not checkpoint opaque live sockets, process handles, provider clients, or non-serializable closures.

### INV-14 — Credentials are references, not prompt content

Secrets must be brokered at execution time and never inserted into model context unless explicitly required and policy permits it.

### INV-15 — Every external effect is traceable

Tool execution, approval, capability load, delegation, checkpoint, model call, recovery action, and verification outcome must emit telemetry.

---


## 5. Top-Level Architecture

```text
AgentRuntime
│
├── RuntimeSpec
│   ├── AgentProfile
│   ├── ModelPolicy
│   ├── LoopPolicy
│   ├── PlanningPolicy
│   ├── AuthorityPolicy
│   ├── InitialGrantEnvelope
│   ├── VerifierProfile
│   └── ResultContract
│
├── HarnessKernel
│   │
│   ├── RunController
│   ├── StateManager
│   ├── ContextEngine
│   ├── PlanningStrategy
│   ├── CapabilityRuntime
│   ├── ToolRuntime
│   ├── AuthorityManager
│   ├── GuardrailManager
│   ├── DelegationManager
│   ├── WorkspaceManager
│   ├── CheckpointCoordinator
│   ├── RecoveryManager
│   └── VerificationManager
│
├── RuntimeServices
│   ├── ModelGateway
│   ├── StateStore
│   ├── CheckpointStore
│   ├── ArtifactStore
│   ├── SandboxBackend
│   ├── CredentialBroker
│   ├── Clock
│   ├── IdGenerator
│   └── EventBus / Telemetry
│
└── Contracts
    ├── CapabilityBundle
    ├── CapabilityHandle
    ├── GrantEnvelope
    ├── ExecutionEnvelope
    ├── ToolCall
    ├── ToolObservation
    ├── EvidenceBundle
    ├── FailureEnvelope
    ├── RunResult
    └── ResultContract
```

`HarnessKernel` is a **composition root + coordinator boundary**, not a god class.

A recommended implementation shape:

```python
class HarnessKernel:
    def __init__(
        self,
        run_controller: RunController,
        state_manager: StateManager,
        context_engine: ContextEngine,
        planner: PlanningStrategy,
        capability_runtime: CapabilityRuntime,
        tool_runtime: ToolRuntime,
        authority: AuthorityManager,
        guardrails: GuardrailManager,
        delegation: DelegationManager,
        workspace: WorkspaceManager,
        checkpoints: CheckpointCoordinator,
        recovery: RecoveryManager,
        verifier: VerificationManager,
        services: RuntimeServices,
    ): ...
```

Business logic belongs in the components, not in a 10,000-line kernel.

---


## 6. RuntimeSpec

`RuntimeSpec` is the immutable configuration for a run or agent profile.

```yaml
runtime_spec:
  agent_profile: coding-default
  model_policy: balanced
  loop_policy: bounded-autonomous
  planning_policy: adaptive
  authority_policy: coding-safe
  verifier_profile: coding-standard
  result_contract: patch-and-summary-v1
```

### 6.1 AgentProfile

Defines behavior, not execution rights.

```yaml
agent_profile:
  id: coding-default
  role: software_engineer
  instructions_ref: profiles/coding-default.md
  default_capability_kinds:
    - skill
    - tool
    - resource
  delegation_allowed: true
  preferred_output_style: concise
```

Must NOT contain secrets or hidden permission escalation.

### 6.2 ModelPolicy

Defines model selection constraints.

```yaml
model_policy:
  default_class: reasoning
  allowed_providers:
    - openai
    - anthropic
    - google
  max_cost_usd: 2.50
  fallback_order:
    - reasoning_primary
    - reasoning_secondary
  tool_model_class: fast
  verifier_model_class: reasoning
```

### 6.3 LoopPolicy

```yaml
loop_policy:
  max_turns: 40
  max_total_tokens: 180000
  max_output_tokens: 30000
  max_tool_calls: 100
  max_wall_time_seconds: 1800
  max_recoveries: 8
  cancellation: cooperative
  verify_before_success: true
```

### 6.4 PlanningPolicy

```yaml
planning_policy:
  mode: adaptive   # none | todo | structured | adaptive
  trigger:
    estimated_steps_gte: 4
    cross_module_change: true
  max_plan_items: 20
  refresh_on:
    - verification_failure
    - capability_change
    - major_new_evidence
```

### 6.5 AuthorityPolicy

Defines which classes of operations are auto-allowed, denied, or require approval.

### 6.6 InitialGrantEnvelope

Concrete run-specific grants, never broader than the authority policy.

### 6.7 VerifierProfile

Defines which verifiers run and which are mandatory.

### 6.8 ResultContract

Defines the typed shape and acceptance criteria of final output.

---


## 7. RunController

`RunController` is the harness orchestrator. It owns lifecycle sequencing, not all business logic.

### 7.1 Responsibilities

- initialize a run;
- validate `RuntimeSpec`;
- initialize authoritative state;
- start/stop turn cycles;
- check budgets;
- observe cancellation;
- call ContextEngine;
- invoke ModelGateway;
- dispatch model actions;
- request verification;
- call RecoveryManager when needed;
- finalize `RunResult`;
- emit lifecycle events.

### 7.2 Non-responsibilities

It should not:

- perform vector retrieval;
- directly execute shell commands;
- parse provider-specific model payloads;
- store long-term memory;
- decide capability quality;
- implement sandboxing itself.

### 7.3 Run state machine

```text
CREATED
   │
   ▼
INITIALIZING
   │
   ├── invalid spec ──────────────► FAILED
   │
   ▼
READY
   │
   ▼
RUNNING
   │
   ├── capability needed ─────────► WAITING_CAPABILITY
   │                                  │
   │                                  └────► RUNNING
   │
   ├── approval needed ───────────► INTERRUPTED_APPROVAL
   │                                  │
   │                                  └────► RUNNING
   │
   ├── delegated child ───────────► WAITING_CHILD
   │                                  │
   │                                  └────► RUNNING
   │
   ├── checkpoint interrupt ──────► INTERRUPTED
   │                                  │
   │                                  └────► RUNNING
   │
   ├── candidate completion ──────► VERIFYING
   │                                  │
   │                                  ├ pass ─► SUCCEEDED
   │                                  └ fail ─► RECOVERING
   │
   ├── recoverable failure ───────► RECOVERING ─► RUNNING
   │
   ├── budget exhausted ──────────► PARTIAL / FAILED
   │
   ├── cancellation ──────────────► CANCELLED
   │
   └── fatal failure ─────────────► FAILED
```

### 7.4 Turn lifecycle

```text
TURN_START
   │
   ├─ budget preflight
   ├─ cancellation check
   ├─ state snapshot
   ├─ context assembly
   ├─ pre-model hooks
   ▼
MODEL_CALL
   │
   ├─ stream events
   ├─ collect usage
   ▼
MODEL_RESULT
   │
   ├─ validate structure
   ├─ classify action
   │
   ├─ final candidate ──► VERIFY
   ├─ tool calls ───────► TOOL PIPELINE
   ├─ delegation ───────► DELEGATION PIPELINE
   ├─ capability req ───► ACI PIPELINE
   └─ malformed ────────► RECOVERY
   │
   ▼
STATE_COMMIT
   │
   ▼
TURN_END
```

### 7.5 StopReason enum

Recommended v2 set:

```text
SUCCESS
PARTIAL_SUCCESS
CANCELLED
INTERRUPTED
AWAITING_APPROVAL
LIMIT_TURNS
LIMIT_TOTAL_TOKENS
LIMIT_OUTPUT_TOKENS
LIMIT_TOOL_CALLS
LIMIT_WALL_TIME
LIMIT_COST
VERIFICATION_FAILED
GUARDRAIL_BLOCKED
AUTHORITY_DENIED
CAPABILITY_UNAVAILABLE
WORKSPACE_FAILURE
MODEL_FAILURE
TOOL_FAILURE
FATAL_ERROR
```

Distinguish **pause** from **termination**:

- `AWAITING_APPROVAL`, `INTERRUPTED` are resumable.
- `CANCELLED` is terminal unless a new run is created from a checkpoint.
- budget limits may be resumable depending on policy.

### 7.6 Budget checks

Check at minimum:

- before model call;
- after model usage is known;
- before each tool execution;
- before capability lookup;
- before child spawn;
- after child completion;
- before recovery action.

---


## 8. StateManager

### 8.1 Principle

Use one authoritative state model with immutable snapshots.

Recommended model:

```text
RuntimeState
├── RunState
├── SessionState
├── TaskState
├── ContextState
├── CapabilityState
├── AuthorityState
├── WorkspaceState
├── DelegationState
├── BudgetState
├── VerificationState
└── RecoveryState
```

This can still be physically stored as normalized records, but logically there is one state authority.

### 8.2 RunState

```python
RunState:
    run_id
    parent_run_id?
    status
    stop_reason?
    created_at
    started_at?
    completed_at?
    current_turn
    version
```

### 8.3 SessionState

Session history is not the same as a run.

```python
SessionState:
    session_id
    conversation_refs
    user_preferences_ref?
    working_memory_refs
    last_run_id?
```

Do not automatically load all session history into context.

### 8.4 TaskState

```python
TaskState:
    task_id
    objective
    constraints
    acceptance_criteria
    current_phase
    plan?
    progress
    unresolved_questions
    completion_claim?
```

### 8.5 State updates

Prefer compare-and-swap/versioned updates:

```python
snapshot = state_manager.snapshot(run_id)
state_manager.commit(
    run_id=run_id,
    expected_version=snapshot.version,
    events=[...],
)
```

This prevents hidden concurrent mutation.

### 8.6 Optional event sourcing

Full event sourcing is not required for v2, but the state API SHOULD allow later reconstruction from events.

Recommended:

- current snapshot for fast runtime;
- append-only audit events for observability/replay;
- periodic checkpoints.

### 8.7 Serialization rule

Every checkpointable field must have:

- stable schema;
- version;
- migration strategy;
- deterministic serialization.

---


## 9. ContextEngine

Context management is a first-class optimization and correctness subsystem.

### 9.1 Responsibilities

- choose relevant state for each model call;
- prioritize context;
- enforce token budget;
- compress old history;
- offload large artifacts;
- cap tool observations;
- project parent context to children;
- inject active capability instructions;
- preserve essential invariants and constraints.

### 9.2 Context tiers

```text
Tier 0 — Always present
  system/runtime invariants
  task objective
  critical constraints
  authority summary
  result contract

Tier 1 — Active working set
  current plan
  recent turns
  active files/symbols
  active capabilities

Tier 2 — Retrieved on demand
  older observations
  repo map
  prior tool results
  references

Tier 3 — Offloaded
  raw logs
  large files
  long command outputs
  generated artifacts
```

### 9.3 Context budget allocation

Example, not hardcoded:

```yaml
context_budget:
  total_tokens: 60000
  fixed:
    invariants: 3000
    task: 2000
    active_capabilities: 8000
  variable:
    working_history: 18000
    repo_context: 16000
    tool_observations: 8000
    reserve: 5000
```

### 9.4 Selection algorithm

Suggested order:

1. pin non-droppable invariants;
2. pin task objective/acceptance criteria;
3. include active plan/progress;
4. include active capability instructions;
5. include recent unresolved tool/model turns;
6. score candidate context items;
7. include within remaining budget;
8. summarize or offload overflow;
9. preserve references to offloaded content.

### 9.5 Context item schema

```python
ContextItem:
    id
    kind
    content_ref
    priority
    estimated_tokens
    created_at
    last_used_at
    relevance
    pin_policy
    source
```

### 9.6 Offloading

Large observations should become artifacts:

```text
tool returns 120 KB
     ↓
OutputLimiter
     ├─ inline summary: 1.5 KB
     ├─ artifact_ref: artifact://...
     └─ metadata: lines / bytes / hash
```

### 9.7 Compaction

Compaction MUST preserve:

- decisions;
- constraints;
- file edits already made;
- failing tests;
- outstanding TODOs;
- approvals and authority changes;
- capability activations;
- evidence references.

Compaction MUST NOT merely summarize conversational tone.

### 9.8 Tool output limits

Each tool declares:

```yaml
output_policy:
  max_inline_chars: 12000
  max_inline_tokens: 3000
  truncation: head_tail
  spill_to_artifact: true
  summary: optional
```

No unbounded stdout or search results.

---


## 10. PlanningStrategy

Planner is an interchangeable strategy, not a mandatory global component.

### 10.1 Modes

```text
NonePlanning
TodoPlanning
StructuredPlanning
AdaptivePlanning
```

### 10.2 Adaptive planning

Use planning when task complexity justifies it:

- multiple independent steps;
- cross-file/cross-service edits;
- long-running research;
- explicit user milestones;
- verification requires multiple checks;
- delegation is likely.

Skip or minimize planning for trivial requests.

### 10.3 Plan item

```python
PlanItem:
    id
    objective
    status  # pending/running/blocked/done/failed
    dependencies
    evidence_refs
    assigned_run_id?
```

### 10.4 Rules

- planner proposes; state manager commits;
- plan changes are events;
- do not regenerate the whole plan every turn;
- replan only after materially new information;
- plan item "done" should ideally reference evidence.

---


## 11. CapabilityRuntime and ACI Integration

This is the critical boundary between ACI and the harness.

### 11.1 Responsibilities

`CapabilityRuntime` owns:

- requesting capability candidates from ACI;
- resolving selected capability payloads;
- local caching;
- loading skill instructions;
- binding tools;
- connecting MCP servers;
- activating/deactivating capability scope;
- reporting outcome feedback to ACI.

It does **not** own:

- global retrieval;
- global reranking;
- capability quality benchmarking;
- corpus curation;
- provenance truth;
- global dependency graph computation.

### 11.2 Subcomponents

```text
CapabilityRuntime
├── ACIClient
│   ├── search()
│   ├── resolve()
│   ├── explain()
│   └── feedback()
├── CapabilityLoader
├── SkillLoader
├── ToolBinder
├── MCPConnector
├── CapabilityCache
└── ActivationManager
```

### 11.3 ACI search request

```json
{
  "request_id": "req_...",
  "task": {
    "objective": "Optimize PostgreSQL queries",
    "constraints": ["no schema-breaking changes"]
  },
  "environment": {
    "client": "opencode",
    "languages": ["python", "sql"],
    "frameworks": ["fastapi"],
    "workspace_features": ["shell", "read", "write"]
  },
  "current_capabilities": ["repo-analysis@2.1"],
  "authority_summary": {
    "network": false,
    "filesystem_write_scope": ["/repo"]
  },
  "failure_context": null
}
```

Do not send unnecessary full conversation history.

### 11.4 CapabilityBundle

```json
{
  "bundle_id": "cb_...",
  "candidates": [
    {
      "id": "postgres-query-analysis",
      "version": "3.2.0",
      "reason": "...",
      "dependencies": [],
      "conflicts": [],
      "required_authority": {
        "filesystem_read": true,
        "network": false
      },
      "estimated_context_tokens": 1800,
      "payload_ref": "cap://postgres-query-analysis/3.2.0"
    }
  ],
  "expires_at": "...",
  "selection_trace_ref": "..."
}
```

### 11.5 Resolve flow

```text
CapabilityRuntime.search()
       ↓
ACI returns metadata candidates
       ↓
runtime/agent selects allowed candidate
       ↓
CapabilityRuntime.resolve(id, version)
       ↓
validate manifest
       ↓
authority compatibility check
       ↓
load instructions / bind tools / connect MCP
       ↓
ActivationManager.activate()
```

### 11.6 Runtime trust rule

A capability manifest may *declare* required permissions but cannot grant them.

```text
capability.required_permissions
           ↓
AuthorityManager.evaluate()
           ↓
ALLOW / APPROVAL / DENY
```

### 11.7 Capability version pinning

Once activated in a run, default to a pinned immutable version.

Do not silently upgrade a capability mid-run.

### 11.8 Capability failure feedback

When verification shows the capability was insufficient:

```json
{
  "capability_id": "postgres-query-analysis",
  "version": "3.2.0",
  "task_signature": "...",
  "outcome": "failed_verification",
  "failure_class": "CAPABILITY_INSUFFICIENT",
  "evidence_refs": ["..."],
  "cost": {...},
  "latency_ms": 4210
}
```

ACI may use this offline; the runtime must not let feedback mutate the current production capability version.

---


## 12. ToolRuntime

ToolRuntime is missing from many homemade agent architectures and should be explicit.

### 12.1 Tool execution pipeline

```text
MODEL TOOL CALL
      │
      ▼
1. ToolRegistry lookup
      │
      ▼
2. Argument schema validation
      │
      ▼
3. Pre-tool guardrails
      │
      ▼
4. Authority preflight
      │
      ├── DENY ─────────────► blocked observation
      ├── APPROVAL ─────────► interrupt / request approval
      └── ALLOW
      │
      ▼
5. Build ExecutionEnvelope
      │
      ▼
6. Workspace/Sandbox preparation
      │
      ▼
7. Execute
      │
      ▼
8. Capture result + side-effect metadata
      │
      ▼
9. OutputLimiter
      │
      ▼
10. Post-tool guardrails
      │
      ▼
11. Evidence extraction
      │
      ▼
12. State commit + telemetry
      │
      ▼
TOOL OBSERVATION
```

### 12.2 Components

```text
ToolRuntime
├── ToolRegistry
├── Dispatcher
├── ArgumentValidator
├── ExecutionAdapter
├── OutputLimiter
├── ErrorNormalizer
└── ToolResultSerializer
```

### 12.3 Tool contract

```python
ToolSpec:
    id
    version
    description
    input_schema
    output_schema?
    side_effect_class
    authority_requirements
    workspace_requirements
    timeout_policy
    cancellation_support
    idempotency_class
    output_policy
```

### 12.4 Side-effect classes

```text
PURE
READ_ONLY
LOCAL_MUTATION
EXTERNAL_MUTATION
DESTRUCTIVE
PRIVILEGED
```

Policy may differ by class.

### 12.5 Parallel execution

Parallel tool calls are allowed only if:

- tools declare concurrency-safe;
- no overlapping mutable resources;
- authority permits;
- result ordering is not semantically required.

Otherwise execute sequentially.

### 12.6 Idempotency classes

```text
IDEMPOTENT
IDEMPOTENT_WITH_KEY
NON_IDEMPOTENT
UNKNOWN
```

RecoveryManager must never blindly retry `NON_IDEMPOTENT` calls.

---


## 13. AuthorityManager

Authority is a core safety and correctness system, not an optional UI permission prompt.

### 13.1 Subcomponents

```text
AuthorityManager
├── PolicyEvaluator
├── GrantLedger
├── ApprovalCoordinator
└── ExecutionEnvelopeBuilder
```

### 13.2 Distinct concepts

#### Policy

What is permitted in principle.

#### Grant

What this run is currently allowed to do.

#### Approval

An external decision that may extend a grant within policy.

#### ExecutionEnvelope

The exact restrictions enforced for one operation.

### 13.3 Decision enum

```text
ALLOW
ALLOW_WITH_RESTRICTIONS
REQUIRE_APPROVAL
DENY
```

### 13.4 GrantEnvelope

```json
{
  "filesystem": {
    "read": ["/repo"],
    "write": ["/repo/src", "/repo/tests"]
  },
  "network": {
    "enabled": false,
    "allowed_hosts": []
  },
  "process": {
    "allowed_prefixes": ["pytest", "git status", "ruff"]
  },
  "secrets": {
    "allowed_refs": []
  },
  "expires_at": null
}
```

### 13.5 Least privilege

When an operation needs extra authority, request the narrowest possible delta.

Prefer:

```text
write /repo/package-lock.json
```

over:

```text
write /
```

Prefer:

```text
network host=pypi.org
```

over:

```text
network unrestricted
```

### 13.6 Approval lifecycle

```text
operation requires authority
      ↓
PolicyEvaluator
      ↓
REQUIRE_APPROVAL
      ↓
ApprovalRequest
      ↓
run → INTERRUPTED_APPROVAL
      ↓
CheckpointCoordinator persists
      ↓
user/reviewer decision
      ↓
GrantLedger updates scoped grant
      ↓
resume
      ↓
re-evaluate operation
```

Never execute merely because a prior stale approval object exists; re-check scope and expiry.

### 13.7 Approval cache

If reusable approvals are supported:

- scope narrowly;
- include command/tool identity;
- include workspace scope;
- include expiry;
- never cache broad destructive permissions by default.

### 13.8 Child grants

```python
child_grants = intersect(
    parent_grants,
    requested_child_grants,
    policy.maximum_child_grants,
)
```

---


## 14. GuardrailManager

Guardrails validate semantic or structural behavior; they are separate from authority.

### 14.1 Types

```text
InputGuardrails
PreModelGuardrails (optional)
PreToolGuardrails
PostToolGuardrails
OutputGuardrails
```

### 14.2 Blocking vs parallel input guardrails

For checks that prevent dangerous side effects or expensive execution, use blocking mode.

For low-risk classification where latency matters, parallel mode may be used only if starting execution early is acceptable.

### 14.3 Guardrail result

```python
GuardrailResult:
    status: PASS | WARN | BLOCK | TRANSFORM
    reason
    transformed_value?
    evidence
```

### 14.4 Examples

Input:
- malformed task;
- forbidden data handling request;
- invalid result contract.

Pre-tool:
- suspicious shell command;
- malformed SQL;
- disallowed destination.

Post-tool:
- secret leakage in stdout;
- invalid structured result;
- excessive untrusted HTML.

Output:
- result schema violation;
- missing required citations/evidence;
- unsupported completion claim.

---


## 15. DelegationManager

Delegation is controlled task decomposition, not unrestricted recursive spawning.

### 15.1 Responsibilities

- validate need for delegation;
- derive child task;
- project minimal child context;
- carve out child budget;
- derive child grants;
- set recursion/depth limits;
- create child RuntimeSpec;
- collect child result;
- verify result contract;
- merge only approved outputs into parent state.

### 15.2 Child creation

```text
Parent Task
   │
   ├─ child objective
   ├─ relevant files/context
   ├─ selected capabilities
   ├─ projected authority
   ├─ reserved budget
   └─ result contract
   │
   ▼
Child Run
```

### 15.3 Required invariants

```text
child.depth = parent.depth + 1
child.depth <= max_depth

child.authority ⊆ parent.authority

child.budget <= reserved_parent_budget

child.context != full_parent_context
```

### 15.4 Result aggregation

A child result is treated like an external structured observation:

- validate schema;
- verify evidence when required;
- do not blindly accept "done";
- store child trace reference;
- merge only result fields permitted by child contract.

### 15.5 Suggested v2 limits

Make configurable, but start conservatively:

- maximum delegation depth: small;
- maximum concurrent children: bounded;
- maximum total child runs per parent: bounded;
- no child spawning if remaining budget is below reserve.

---


## 16. WorkspaceManager

### 16.1 Principle

**Authority decides. Workspace enforces.**

### 16.2 Workspace types

```text
LocalWorkspace
SandboxWorkspace
RemoteWorkspace
```

### 16.3 Interface

```python
class Workspace:
    async def read_file(...)
    async def write_file(...)
    async def list_dir(...)
    async def execute(...)
    async def terminate_process(...)
    async def snapshot(...)
    async def diff(...)
```

### 16.4 Local workspace

Use for trusted local workflows when user explicitly accepts local effects.

### 16.5 Sandbox workspace

Use for:

- untrusted code;
- dependency installation;
- risky shell execution;
- autonomous refactors;
- public repository evaluation;
- multi-tenant workloads.

### 16.6 Remote workspace

Used for container/Kubernetes/cloud execution.

The rest of HarnessKernel should not care whether the workspace is local or remote.

### 16.7 Workspace identity

Every state-changing observation should include:

```text
workspace_id
snapshot_id?
cwd
resource_scope
```

### 16.8 Snapshot/diff support

For coding agents, optional but highly valuable:

```text
before snapshot
     ↓
agent changes
     ↓
verification
     ↓
diff / evidence
```

---


## 17. CheckpointCoordinator

### 17.1 Responsibilities

- decide when checkpoint is safe;
- serialize runtime state;
- save checkpoint through CheckpointStore;
- include pending interrupt/approval requests;
- restore into a compatible runtime schema;
- run migrations if needed.

### 17.2 Checkpoint triggers

Recommended:

- before human approval interrupt;
- after major plan phase;
- before risky operation;
- after expensive capability acquisition;
- periodic turn interval for long runs;
- before delegation fan-out;
- before controlled shutdown.

### 17.3 Checkpoint content

```text
run metadata
state version
task state
plan
active capabilities + pinned versions
grant ledger
budget ledger
workspace reference/snapshot
pending approvals
delegation tree
verification state
recovery counters
artifact refs
```

Do NOT store:

- live provider SDK objects;
- raw sockets;
- open subprocess handles;
- plaintext secrets.

### 17.4 Resume validation

On resume:

1. validate checkpoint schema version;
2. validate capability versions still resolvable;
3. validate workspace still exists or restore snapshot;
4. validate grants not expired;
5. revalidate pending approvals;
6. refresh model/provider clients;
7. continue from explicit resume point.

---


## 18. RecoveryManager

Recovery must be based on failure classification.

### 18.1 Failure taxonomy

```text
TRANSIENT_MODEL
RATE_LIMITED
MODEL_MALFORMED_OUTPUT

TRANSIENT_TOOL
TOOL_INVALID_ARGUMENT
TOOL_TIMEOUT
TOOL_EXECUTION_FAILED
TOOL_SIDE_EFFECT_UNCERTAIN

AUTHORITY_BLOCKED
APPROVAL_REJECTED

WORKSPACE_UNAVAILABLE
SANDBOX_DENIED

CAPABILITY_NOT_FOUND
CAPABILITY_LOAD_FAILED
CAPABILITY_INSUFFICIENT
CAPABILITY_CONFLICT

CONTEXT_OVERFLOW
CONTEXT_CORRUPTION

VERIFICATION_FAILED
RESULT_CONTRACT_FAILED

BUDGET_EXHAUSTED
CANCELLED
FATAL
```

### 18.2 Recovery actions

```text
RETRY_SAME
RETRY_BACKOFF
REPAIR_INPUT
REPAIR_OUTPUT
REPLAN
COMPACT_CONTEXT
REQUEST_APPROVAL
REQUEST_ALTERNATE_CAPABILITY
SWITCH_MODEL
RESTORE_CHECKPOINT
RETURN_PARTIAL
ESCALATE
FAIL
```

### 18.3 Recovery policy matrix

| Failure | Default action |
|---|---|
| rate limit | backoff / provider fallback |
| malformed model structure | repair once, then fallback/retry |
| invalid tool args | return schema error to model or repair |
| transient read-only tool failure | bounded retry |
| non-idempotent tool uncertain | **do not retry automatically** |
| authority denied | return blocked result; no circumvention |
| approval rejected | replan within existing authority |
| context overflow | compact/offload/retry |
| capability insufficient | ask ACI for alternate using failure context |
| verification failed | repair/replan with evidence |
| budget exhausted | partial/fail according to policy |
| fatal invariant violation | immediate fail |

### 18.4 Recovery budget

Recovery itself consumes budget.

```yaml
recovery:
  max_attempts_total: 8
  max_same_failure_retries: 2
  max_replans: 2
  max_capability_replacements: 2
```

No infinite self-healing loops.

---


## 19. VerificationManager

Verification is the completion gate.

### 19.1 Responsibilities

- run deterministic checks first;
- run domain verifiers;
- optionally run LLM evaluator;
- collect evidence;
- produce machine-readable verdict;
- decide whether candidate completion satisfies ResultContract.

### 19.2 Verification order

```text
cheap deterministic checks
      ↓
domain-specific checks
      ↓
tests/lint/schema/build
      ↓
artifact/diff checks
      ↓
LLM evaluator only if needed
      ↓
verdict
```

Use the cheapest reliable verifier first.

### 19.3 VerificationResult

```python
VerificationResult:
    verdict: PASS | FAIL | INCONCLUSIVE
    checks: list[CheckResult]
    evidence_bundle
    repair_hints
```

### 19.4 Coding verifier examples

- changed files exist;
- expected diff produced;
- code parses;
- tests pass;
- lint/typecheck if required;
- no unintended files changed;
- security scan for relevant changes;
- acceptance criteria mapped to evidence.

### 19.5 EvidenceBundle

```json
{
  "items": [
    {
      "kind": "test_result",
      "ref": "artifact://pytest/...",
      "hash": "...",
      "summary": "143 passed, 0 failed"
    },
    {
      "kind": "diff",
      "ref": "artifact://git-diff/..."
    }
  ]
}
```

### 19.6 Completion rule

```text
model proposes completion
     ↓
ResultContract validation
     ↓
VerificationProfile checks
     ↓
PASS?
  ├─ yes → SUCCEEDED
  └─ no  → RECOVERING or FAILED
```

---


## 20. RuntimeServices

RuntimeServices are infrastructure dependencies. Keep them outside domain logic.

### 20.1 ModelGateway

```text
ModelGateway
├── ModelRegistry
├── ModelRouter
├── ProviderAdapter
├── RetryPolicy
├── RateLimiter
├── UsageTracker
├── FallbackPolicy
└── StreamingAdapter
```

#### ModelRequest

```python
ModelRequest:
    role/profile
    messages
    tools
    output_schema?
    reasoning_policy
    token_limit
    temperature?
    provider_constraints
    cancellation_token
```

#### ModelGateway rules

- provider SDK objects do not leak into core;
- usage is normalized;
- errors are normalized;
- fallback is policy-controlled;
- never retry ambiguous side-effecting tool phases by replaying model output blindly.

### 20.2 StateStore

Persists state snapshots/session data.

### 20.3 CheckpointStore

Persists resumable checkpoint packages.

### 20.4 ArtifactStore

Stores large outputs, logs, diffs, files, evidence.

### 20.5 SandboxBackend

Concrete container/VM/remote execution provider.

### 20.6 CredentialBroker

```text
CredentialRef
   ↓
policy check
   ↓
short-lived scoped credential
   ↓
tool execution
```

Never serialize raw credential into checkpoint or prompt.

### 20.7 EventBus

All components emit typed events.

---


## 21. Event Model and Telemetry

### 21.1 Event envelope

```json
{
  "event_id": "...",
  "event_type": "tool.execution.finished",
  "timestamp": "...",
  "run_id": "...",
  "parent_run_id": null,
  "turn_id": "...",
  "correlation_id": "...",
  "payload": {...}
}
```

### 21.2 Core event taxonomy

```text
run.created
run.started
run.paused
run.resumed
run.cancelled
run.completed
run.failed

turn.started
turn.completed

model.request.started
model.request.completed
model.request.failed

context.assembled
context.compacted
context.offloaded

capability.search.started
capability.search.completed
capability.loaded
capability.unloaded
capability.feedback

tool.requested
tool.authority.evaluated
tool.approval.requested
tool.execution.started
tool.execution.completed
tool.execution.failed

workspace.created
workspace.snapshot
workspace.terminated

delegation.started
delegation.completed
delegation.failed

checkpoint.saved
checkpoint.restored

recovery.started
recovery.action
recovery.completed

verification.started
verification.check
verification.completed
```

### 21.3 Metrics

#### Reliability
- run success rate;
- verified success rate;
- partial success rate;
- fatal failure rate;
- recovery success rate.

#### Cost
- tokens/run;
- model cost/run;
- cost/verified-success;
- capability lookup cost;
- child-agent cost.

#### Latency
- total runtime;
- model latency;
- tool latency;
- ACI lookup latency;
- verification latency;
- approval wait time separately from compute time.

#### Context
- context tokens/call;
- compaction frequency;
- offloaded bytes;
- dropped context count.

#### Authority/security
- approvals requested;
- approvals denied;
- denied tool calls;
- sandbox escalations;
- child grant reductions.

#### Capability quality feedback
- capability activation count;
- verified success by capability;
- replacement frequency;
- failure class by capability;
- cost contribution.

---


## 22. Budget System

Budget must be hierarchical and first-class.

### 22.1 Budget dimensions

```text
turns
model input tokens
model output tokens
total tokens
model cost
tool calls
tool wall time
run wall time
capability lookups
capability loads
delegations
recovery attempts
artifact bytes
```

### 22.2 BudgetLedger

```python
BudgetLedger:
    limits
    consumed
    reserved
    remaining
```

### 22.3 Reservation

Before spawning a child or expensive verifier:

```python
reservation = budget.reserve({
    "tokens": 20000,
    "cost_usd": 0.30,
})
```

On child completion:

- consume actual;
- release unused reserve.

### 22.4 Graceful exhaustion

Budget exhaustion should generally produce:

- valid state;
- explicit stop reason;
- partial result if policy permits;
- continuation metadata if resumable.

---


## 23. Cancellation and Interrupts

They are different.

### 23.1 Cancellation

Terminal stop request.

Use cases:

- user presses Stop;
- client disconnect;
- timeout;
- administrator termination.

Check at:

- top of turn;
- model streaming;
- before tools;
- between tools;
- ACI calls;
- child waits;
- verification stages.

### 23.2 Interrupt

Pause with intent to resume.

Use cases:

- approval needed;
- human input needed;
- external dependency;
- scheduled resume.

Interrupt requires checkpointable state.

### 23.3 Tool cancellation

Tools should declare cancellation support.

For non-cooperative local calls:
- allow completion if safe and short;
- for sandboxed process, support kill/terminate;
- record whether side effects may have partially occurred.

---


## 24. ResultContract

ResultContract defines what success means structurally.

Example coding contract:

```yaml
result_contract:
  id: coding-change-v1
  required:
    - summary
    - changed_files
    - verification
  fields:
    summary: string
    changed_files:
      type: array
      items: string
    verification:
      type: evidence_bundle
  acceptance:
    verification_verdict: PASS
```

Research contract may require:

- answer;
- evidence/citations;
- uncertainty;
- artifact refs.

The verifier checks the contract before `SUCCESS`.

---


## 25. End-to-End Flows

### 25.1 Normal tool-assisted run

```text
request
  ↓
RunController.initialize
  ↓
StateManager.create
  ↓
ContextEngine.build
  ↓
ModelGateway.invoke
  ↓
tool call
  ↓
ToolRuntime.validate
  ↓
GuardrailManager.pre_tool
  ↓
AuthorityManager.evaluate
  ↓
WorkspaceManager.execute
  ↓
OutputLimiter
  ↓
GuardrailManager.post_tool
  ↓
StateManager.commit observation
  ↓
next turn
  ↓
model proposes done
  ↓
VerificationManager
  ↓
PASS
  ↓
RunResult(success)
```

### 25.2 Capability acquisition mid-run

```text
model/planner identifies missing expertise
  ↓
CapabilityRuntime.search
  ↓
ACI search/rerank/judge
  ↓
CapabilityBundle
  ↓
Authority compatibility check
  ↓
resolve pinned capability
  ↓
CapabilityLoader
  ↓
ContextEngine activates instructions
  ↓
ToolBinder registers tools
  ↓
next model turn
```

### 25.3 Approval-required tool

```text
tool call
  ↓
schema valid
  ↓
pre-tool guardrail PASS
  ↓
Authority = REQUIRE_APPROVAL
  ↓
Checkpoint save
  ↓
run INTERRUPTED_APPROVAL
  ↓
user approval
  ↓
GrantLedger update
  ↓
resume
  ↓
re-evaluate exact operation
  ↓
execute
```

### 25.4 Verification failure and repair

```text
model: done
  ↓
verification: FAIL
  ↓
FailureEnvelope(VERIFICATION_FAILED)
  ↓
RecoveryManager
  ↓
repair_hints + evidence
  ↓
Planner update
  ↓
model/tool loop
  ↓
verify again
```

### 25.5 Capability replacement

```text
verification repeatedly fails
  ↓
RecoveryManager classifies CAPABILITY_INSUFFICIENT
  ↓
CapabilityRuntime.search(
  failure_context = evidence
)
  ↓
ACI returns alternative
  ↓
deactivate old capability if safe
  ↓
activate replacement
  ↓
replan
```

### 25.6 Delegation

```text
parent identifies subtask
  ↓
DelegationManager
  ↓
project context
  ↓
derive child grants
  ↓
reserve child budget
  ↓
spawn child Runtime
  ↓
child result
  ↓
validate result contract
  ↓
merge evidence/summary
  ↓
release unused budget
```

---


## 26. Security Model

### 26.1 Trust boundaries

Treat as untrusted by default:

- user input;
- model output;
- capability instructions;
- MCP servers;
- tool results;
- repository content;
- external web content;
- generated code.

Trusted components:

- runtime policy;
- authority engine;
- verified capability metadata source;
- sandbox enforcement implementation;
- credential broker.

### 26.2 Prompt injection boundary

Repository files and web pages may contain instructions.

Mark context provenance and never allow retrieved content to redefine:

- authority rules;
- system invariants;
- result contract;
- secret policy;
- capability trust.

### 26.3 Capability supply-chain rules

Before production activation, ACI/control plane should track:

- source;
- immutable version/hash;
- license;
- author/provenance;
- review status;
- security status;
- benchmark status.

At runtime, verify payload hash/version.

### 26.4 MCP trust

MCP is transport/integration, not trust.

For each MCP server:
- identity;
- trust level;
- allowed tools;
- authority bounds;
- network scope;
- secret scope;
- timeout;
- cancellation support.

### 26.5 Secret handling

- use references, not literal secrets;
- redact telemetry;
- no secrets in capability feedback;
- no secrets in checkpoint;
- short-lived credentials where possible;
- per-tool credential scope.

---


## 27. Concurrency, Idempotency, and Race Conditions

### 27.1 State commits

Use versioned state writes.

### 27.2 Tool concurrency

Only safe if resource sets do not conflict.

ToolSpec may declare:

```yaml
resource_access:
  reads:
    - repo:src/**
  writes:
    - repo:tests/**
```

Scheduler can reject conflicting parallel calls.

### 27.3 Duplicate requests

Support idempotency key at run creation.

```text
same idempotency key + same request hash
→ return existing run/result
```

### 27.4 Approval race

Approval must bind to:

- run ID;
- operation hash;
- permission delta;
- workspace;
- expiry.

A changed operation invalidates old approval.

---


## 28. Error Contract

Normalize every failure into `FailureEnvelope`.

```json
{
  "failure_id": "...",
  "class": "TOOL_TIMEOUT",
  "severity": "recoverable",
  "component": "tool_runtime",
  "message": "...",
  "retryable": true,
  "side_effect_state": "none|possible|confirmed",
  "evidence_refs": [],
  "cause_ref": "...",
  "timestamp": "..."
}
```

Never make RecoveryManager parse arbitrary exception strings as its primary API.

---


## 29. File / Package Structure

Recommended Python-oriented structure; adapt names if TypeScript/Rust is selected.

```text
agent-runtime/
│
├── contracts/
│   ├── runtime_spec.py
│   ├── state.py
│   ├── events.py
│   ├── stop_reason.py
│   ├── failures.py
│   ├── capabilities.py
│   ├── authority.py
│   ├── tools.py
│   ├── evidence.py
│   └── result.py
│
├── kernel/
│   ├── kernel.py
│   ├── run_controller.py
│   ├── state_manager.py
│   ├── context_engine.py
│   ├── planning.py
│   ├── capability_runtime.py
│   ├── tool_runtime.py
│   ├── authority.py
│   ├── guardrails.py
│   ├── delegation.py
│   ├── workspace.py
│   ├── checkpoints.py
│   ├── recovery.py
│   └── verification.py
│
├── services/
│   ├── model_gateway/
│   │   ├── gateway.py
│   │   ├── registry.py
│   │   ├── router.py
│   │   ├── usage.py
│   │   └── providers/
│   ├── storage/
│   │   ├── state_store.py
│   │   ├── checkpoint_store.py
│   │   └── artifact_store.py
│   ├── sandbox/
│   ├── credentials/
│   └── event_bus/
│
├── capabilities/
│   ├── aci_client.py
│   ├── loader.py
│   ├── cache.py
│   ├── skill_loader.py
│   ├── tool_binder.py
│   └── mcp_connector.py
│
├── tools/
│   ├── registry.py
│   ├── validators.py
│   ├── dispatcher.py
│   ├── output_limits.py
│   └── adapters/
│
├── workspaces/
│   ├── base.py
│   ├── local.py
│   ├── sandbox.py
│   └── remote.py
│
├── policies/
│   ├── authority/
│   ├── loop/
│   ├── recovery/
│   └── verification/
│
├── telemetry/
│   ├── events.py
│   ├── traces.py
│   ├── metrics.py
│   └── sinks/
│
├── server/
│   ├── api.py
│   ├── websocket.py
│   └── auth.py
│
├── adapters/
│   ├── opencode/
│   ├── codex/
│   ├── claude_code/
│   └── sdk/
│
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── security/
│   ├── replay/
│   └── e2e/
│
└── examples/
```

---


## 30. Interface Sketches

These are design targets, not final syntax.

### 30.1 Runtime

```python
result = await runtime.run(
    task=TaskRequest(...),
    spec=RuntimeSpec(...),
    cancel_token=cancel_token,
)
```

### 30.2 Resume

```python
result = await runtime.resume(
    checkpoint_id="chk_...",
    interrupt_responses={
        "approval_123": ApprovalDecision.APPROVE
    },
)
```

### 30.3 Capability search

```python
bundle = await capability_runtime.search(
    CapabilityNeed(
        task=state.task.objective,
        environment=context.environment_summary(),
        current_capabilities=state.capabilities.active,
        failure_context=None,
    )
)
```

### 30.4 Tool dispatch

```python
observation = await tool_runtime.execute(
    tool_call,
    run_context=run_context,
)
```

### 30.5 Verification

```python
verification = await verifier.verify(
    candidate_result,
    state=state_snapshot,
    contract=runtime_spec.result_contract,
)
```

---


## 31. Configuration Strategy

Use immutable layered config:

```text
built-in defaults
   ↓
organization policy
   ↓
agent profile
   ↓
project config
   ↓
per-run overrides
```

Lower layer cannot bypass hard organization policy.

Recommended rule:

```text
effective_config = constrained_merge(...)
```

not plain dictionary overwrite.

---


# PART III — LOOP FAMILIES AND NINE SPECIALIZED AGENT PROFILES


## 19A. Profile Principle

The nine archetypes are NOT nine independent frameworks.

```text
Agent
=
HarnessKernel
+ RuntimeSpec
+ AgentProfile
+ LoopPolicy
+ ContextPolicy
+ GrantEnvelope
+ CapabilityBundle
+ VerifierProfile
+ RecoveryPolicy
+ TerminationPolicy
```

The four loop families share the same kernel while specializing reasoning/execution behavior.


## 19. Four Loop Families

Do not create nine fundamentally separate runtimes.

---

### 19.1 EngineeringLoop

Used by:

```text
Coder
Debugger
Tester
DevOps
partly Security
```

Generic skeleton:

```text
understand
 ↓
inspect environment
 ↓
local plan
 ↓
act
 ↓
observe
 ↓
verify
 ↓
repair / continue
```

---

### 19.2 ResearchLoop

Used by:

```text
Researcher
parts of Security
```

Skeleton:

```text
scope question
 ↓
research plan
 ↓
search / retrieve
 ↓
collect evidence
 ↓
cross-check
 ↓
resolve contradictions
 ↓
synthesize
 ↓
citation verification
```

---

### 19.3 AnalysisLoop

Used by:

```text
Data Analyst
parts of Architect
```

Skeleton:

```text
define question
 ↓
inspect structured state/data
 ↓
analysis plan
 ↓
execute computation/query
 ↓
inspect intermediate results
 ↓
validate
 ↓
interpret
 ↓
artifact
```

---

### 19.4 EvaluationLoop

Used by:

```text
Reviewer
Architect
Security review
high-risk final checking
```

Skeleton:

```text
scope
 ↓
collect deterministic evidence
 ↓
inspect artifact/change
 ↓
identify findings
 ↓
verify findings
 ↓
risk/confidence filter
 ↓
structured verdict
```

---


## 20. Nine AgentProfiles

---


## 20.1 CoderProfile

### Objective

Implement or modify software while satisfying explicit acceptance criteria.

### Loop family

```text
EngineeringLoop
```

### Primary tools

```text
repo_read
repo_search
repo_map
file_edit
apply_patch
shell
test_runner
lint
typecheck
git_diff
```

### Context strategy

```text
repo map
→ relevant symbols/files
→ local detailed context
```

Never load entire repository by default.

### Verifier

```text
tests
lint
typecheck
acceptance-specific checks
diff sanity
```

### Termination

Success only if:

```text
acceptance criteria satisfied
AND deterministic verification passes
OR explicit inconclusive condition returned
```

---


## 20.2 DebuggerProfile

### Objective

Find root cause, prove it, repair it, and demonstrate regression removal.

### Loop family

```text
EngineeringLoop / HypothesisDriven variant
```

### Required phases

```text
1. reproduce
2. localize
3. collect evidence
4. form hypothesis
5. test hypothesis
6. identify root cause
7. repair
8. reproduce again
9. regression verify
```

### Special state

```yaml
hypotheses:
  - statement:
    evidence_for:
    evidence_against:
    status:
```

### Termination

Require:

```text
root cause identified
AND symptom no longer reproduces
AND regression checks pass
```

unless reproduction is impossible and explicitly documented.

---


## 20.3 TesterProfile

### Objective

Validate behavior against acceptance criteria.

### Loop family

```text
EngineeringLoop / AssertionDriven variant
```

### Pattern

```text
acceptance criteria
 ↓
test plan
 ↓
fixture/setup
 ↓
execute
 ↓
observe
 ↓
assert
 ↓
evidence
```

### Tools

```text
test runner
browser
screenshot
API client
database read
logs
```

### Termination

Evidence-based assertions, not model opinion.

---


## 20.4 DevOpsSREProfile

### Objective

Diagnose infrastructure/runtime incidents and safely propose or perform remediation.

### Loop family

```text
EngineeringLoop / IncidentDriven variant
```

### Pattern

```text
incident
 ↓
deterministic signals
 ↓
bounded telemetry retrieval
 ↓
hypothesis
 ↓
targeted inspection
 ↓
root cause
 ↓
remediation
 ↓
health verification
 ↓
rollback if needed
```

### Safety

Mutation should be approval-gated at higher risk levels.

---


## 20.5 ResearcherProfile

### Objective

Produce evidence-grounded research with source traceability.

### Loop family

```text
ResearchLoop
```

### Required state

```yaml
research_plan:
queries:
evidence_records:
contradictions:
source_quality:
open_questions:
```

### Verification

```text
citation exists
citation supports claim
cross-source check
contradictions represented
```

---


## 20.6 DataAnalystProfile

### Objective

Perform reproducible data analysis.

### Loop family

```text
AnalysisLoop
```

### State

```text
data source
schema
transformations
queries
notebooks/scripts
intermediate tables
charts
statistical assumptions
```

### Verification

```text
query/code execution
schema validation
statistical sanity
reproducibility
lineage
```

---


## 20.7 SecurityAnalystProfile

### Objective

Defensive or authorized security analysis with strict scope enforcement.

### Loop family

```text
EvaluationLoop
+
restricted EngineeringLoop when explicit execution is authorized
```

### Hard requirements

```text
authorized scope
sandbox
restricted network
evidence-first findings
deterministic scanners when possible
independent verification
```

LLM MUST NOT be the source of truth for security findings.

---


## 20.8 ArchitectProfile

### Objective

Produce architecture decisions and migration plans.

### Loop family

```text
EvaluationLoop / Design variant
```

### Pattern

```text
requirements
 ↓
constraints
 ↓
current architecture
 ↓
candidate designs
 ↓
tradeoff analysis
 ↓
decision
 ↓
ADR
 ↓
migration DAG
```

### Authority

Recommended default:

```text
READ ONLY
```

Architect produces artifacts; Coder executes them.

---


## 20.9 ReviewerProfile

### Objective

Assess changes/results and produce evidence-backed findings.

### Loop family

```text
EvaluationLoop
```

### Context

```text
diff-first
acceptance criteria
relevant neighboring code
deterministic analyzer results
```

### Verification

Findings should be:

```text
evidence-backed
deduplicated
risk-ranked
confidence-filtered
```

For high-risk tasks optionally use:

```text
Reviewer
 ↓
second reviewer / judge
 ↓
accepted findings
```

Do not ensemble every task by default.

---


## 21. AgentProfile Schema

Suggested:

```yaml
id:
version:
description:

loop_family:

model_policy:
  default_model:
  fallback_models:
  reasoning_effort:

context_policy:
  strategy:
  max_context_tokens:
  compaction:
  artifact_offloading:

planning_policy:
  enabled:
  max_replans:

authority:
  permissions:
  approval_policy:
  workspace_policy:

tools:
  allowed:
  denied:

capabilities:
  max_loaded:
  refresh_allowed:
  max_refreshes:

verification:
  verifier_profile:
  required_checks:

recovery:
  retry_limit:
  repair_limit:
  no_progress_threshold:

termination:
  success_conditions:
  failure_conditions:

budgets:
  max_turns:
  max_tokens:
  max_cost:
  max_time:
```

Profiles MUST be versioned.

---


## 37. Verification Profiles

Examples.

---

### 37.1 code_verifier

```text
targeted test
full relevant suite
lint
typecheck
diff sanity
acceptance checks
```

---

### 37.2 debug_verifier

```text
original reproduction
post-fix reproduction
regression suite
root-cause evidence
```

---

### 37.3 research_verifier

```text
citation resolution
claim-source alignment
source quality
contradiction coverage
```

---

### 37.4 data_verifier

```text
query execution
schema validation
statistical checks
lineage
reproducibility
```

---

### 37.5 review_verifier

```text
finding evidence
duplicate suppression
static-analysis corroboration
confidence threshold
```

---


## 38. Risk Levels

Define runtime risk.

```text
R0 — read-only analysis
R1 — local reversible mutation
R2 — repository mutation
R3 — external system mutation
R4 — production/security-sensitive mutation
```

Risk influences:

```text
workspace isolation
approval policy
verification depth
checkpoint frequency
allowed profiles
model policy
```

---


## 39. Policy Gate Before Execution

Existing capability Policy Gate remains.

Add Runtime Admission Policy:

```text
Can this profile execute this subtask
with these permissions
in this workspace
with this risk?
```

Output:

```text
ALLOW
DENY
REQUIRE_APPROVAL
REQUIRE_STRONGER_SANDBOX
```

---


# PART IV — CLIENT SURFACES, PERSISTENCE, REVISION, AND ACI CLOSED LOOP


## 29A. Client Interaction Principle

The client orchestrator should consume compact typed state:

```text
SubtaskContract
RunStatus
ResultContract
EvidencePack
Artifact references
```

It should not need the service agent's full internal transcript.

Revision is delta-based:

```text
original objective
+ previous result summary
+ failed criteria
+ client feedback
+ new evidence
```

rather than replaying the entire global conversation.


## 29. Runtime API

Suggested service endpoints.

---

### 29.1 Execute subtask

```http
POST /v1/agent-runs
```

Request:

```json
{
  "task": {},
  "requested_profile": "coder",
  "execution_mode": "execute",
  "capability_policy": {},
  "budget": {}
}
```

Response:

```json
{
  "run_id": "...",
  "status": "created"
}
```

---

### 29.2 Get run

```http
GET /v1/agent-runs/{run_id}
```

---

### 29.3 Cancel

```http
POST /v1/agent-runs/{run_id}/cancel
```

---

### 29.4 Resume

```http
POST /v1/agent-runs/{run_id}/resume
```

---

### 29.5 Submit approval

```http
POST /v1/agent-runs/{run_id}/approvals/{approval_id}
```

---

### 29.6 Request revision

Client rejection should create a revision attempt:

```http
POST /v1/agent-runs/{run_id}/revise
```

Input:

```yaml
failed_criteria:
feedback:
new_evidence:
budget_extension:
```

The revision MUST reference the previous run rather than starting with no history.

---


## 30. MCP Surface

Keep MCP high-level.

Recommended:

```text
execute_subtask
get_task_status
get_task_result
revise_subtask
cancel_subtask
get_evidence
route_capabilities
search_capabilities
report_outcome
```

Do NOT expose:

```text
skill_0001
skill_0002
...
skill_10000
```

to the client orchestrator.

---


## 31. Persistence Model

Suggested new tables.

---

### 31.1 agent_runs

```text
id
task_id
profile_id
profile_version
harness_version
status
stop_reason
model_provider
model_id
started_at
ended_at
created_at
```

---

### 31.2 agent_run_state

```text
run_id
turn
state_json
budget_json
local_plan_json
context_summary
updated_at
```

---

### 31.3 agent_events

```text
id
run_id
type
payload_json
timestamp
trace_id
parent_event_id
```

---

### 31.4 agent_checkpoints

```text
id
run_id
turn
snapshot_json
workspace_snapshot_ref
created_at
```

---

### 31.5 agent_artifacts

```text
id
run_id
kind
object_ref
sha256
size
metadata_json
created_at
```

---

### 31.6 agent_verifications

```text
id
run_id
verifier_profile
status
checks_json
evidence_json
created_at
```

---

### 31.7 agent_capability_usage

```text
run_id
capability_id
version
digest
loaded_at
unloaded_at
usage_role
outcome
```

---


## 32. Object Store Layout

Example:

```text
data/objects/
└── agent-runs/
    └── {run_id}/
        ├── workspaces/
        ├── artifacts/
        ├── logs/
        ├── tool-results/
        ├── verification/
        ├── checkpoints/
        └── final/
```

Everything content-addressable where practical.

---


## 34. Core Domain Contracts

Use frozen/immutable models whenever possible.

---

### 34.1 SubtaskContract

```yaml
task_id:
parent_task_id:

objective:

global_context:
  project_summary:
  relevant_decisions:

constraints:
  - ...

scope:
  allowed:
  forbidden:

acceptance_criteria:
  - id:
    description:
    verifier_hint:

inputs:
  artifacts:
  references:

requested_profile:

risk:
  level:

budget:
```

---

### 34.2 CapabilityBundle

Reuse existing ACI bundle concepts.

Add runtime compatibility metadata:

```yaml
bundle_id:

capabilities:
  - id:
    version:
    digest:
    role:
    compatibility:

runtime_requirements:
  tools:
  environment:
```

---

### 34.3 CandidateResult

Internal before verification.

```yaml
summary:
changes:
artifacts:
claims:
assumptions:
remaining_work:
```

---

### 34.4 ResultContract

Final external contract as defined earlier.

---


## 35. Context Budget Strategy

Initial heuristic:

```text
0–55%:
normal operation

55–70%:
clear obsolete tool results
offload large observations

70–82%:
structured compaction
retrieve less aggressively

82–90%:
aggressive compaction
preserve only critical active state

>90%:
do not continue blindly
checkpoint
compact or fail/escalate
```

These thresholds MUST be benchmarked by model/provider.

---


## 36. Capability Loading Budget

Initial policy:

```text
initial full capabilities: 1–5
maximum simultaneously loaded: 8
capability refreshes: ≤2
```

Load only capability sections relevant to the runtime where possible.

Example:

```text
Coder
→ workflow + constraints + examples

Reviewer
→ review criteria + anti-patterns

not necessarily the exact same full body
```

Future optional optimization:

```text
capability views
```

---


## 40. Outcome Feedback to Capability Plane

After agent completion:

```text
ResultContract
+
VerificationReport
+
Capability usage
        ↓
OutcomeEvent
        ↓
existing ReportOutcomeService
```

Outcome attribution SHOULD distinguish:

```text
capability failure
agent-loop failure
tool failure
model failure
environment failure
bad task specification
```

Otherwise capability scores will be polluted by unrelated failures.

---


## 41. Feedback to Offline Control Plane

Examples.

#### Missing capability

```text
runtime requested capability
router confidence low
JEV no good match
        ↓
GapEvent
        ↓
offline acquisition proposal
```

#### Bad capability

```text
capability repeatedly selected
but causes verification failure
        ↓
HealthProposal
        ↓
benchmark/review
        ↓
update / deprecate
```

#### Runtime problem

```text
same capability works in baseline
but runtime repeatedly fails
        ↓
HarnessIssue
```

Do not mislabel every failure as skill quality.

---


## 56. Failure Scenarios the Design Must Handle

---

### Scenario A — Small task becomes complex

```text
client delegates small task
 ↓
agent discovers broad dependency
 ↓
scope violation detected
 ↓
return BLOCKED + suggested follow-up
```

Do not silently expand global scope.

---

### Scenario B — Capability insufficient

```text
agent fails because missing domain knowledge
 ↓
request_more()
 ↓
Capability Plane reroutes
 ↓
new capability
 ↓
continue
```

Bound the number of refreshes.

---

### Scenario C — Tool permission unavailable

```text
tool requested
 ↓
AuthorityManager → ASK
 ↓
no human attached
 ↓
BLOCKED_BY_PERMISSION
 ↓
client receives approval requirement
```

Never hang indefinitely.

---

### Scenario D — Model enters loop

```text
same actions
same observations
no progress
 ↓
NO_PROGRESS
 ↓
alternate/replan/escalate
```

---

### Scenario E — Process crashes

```text
checkpoint exists
 ↓
new worker
 ↓
restore state
 ↓
resume without duplicate mutation
```

---

### Scenario F — Agent claims success but tests fail

```text
candidate result
 ↓
VerificationManager
 ↓
mandatory check fail
 ↓
status cannot become completed
```

---

### Scenario G — Client rejects result

```text
ResultContract
 ↓
client REJECT
 ↓
revision feedback
 ↓
revise_subtask(run_id, feedback)
 ↓
new run attempt linked to previous attempt
 ↓
agent receives delta feedback + relevant evidence
```

Do not resend the entire global conversation.

---


## 57. Revision Model

Revision should be a first-class concept.

```yaml
revision:
  parent_run_id:
  attempt_number:
  failed_criteria:
  client_feedback:
  new_constraints:
  evidence_refs:
```

Agent should receive:

```text
original objective
critical context
previous result summary
failed criteria
new evidence
```

not the entire old transcript.

---


## 58. Evidence-Based Acceptance

Client orchestrator SHOULD accept based on:

```text
ResultContract
+
EvidencePack
+
critical artifact references
```

It SHOULD NOT need the agent's full internal history.

This preserves orchestrator context.

---


## 59. ACI Planes After Harness Integration

Final conceptual model:

```text
1. CLIENT PLANE
   Global orchestrator

2. CAPABILITY INTELLIGENCE PLANE
   understand → retrieve → JEV → compose

3. AGENT RUNTIME PLANE
   HarnessKernel → profile → execute → verify

4. CONTROL PLANE
   acquire → refine → benchmark → promote

5. DATA / OBSERVABILITY PLANE
   state → checkpoint → artifacts → telemetry → outcomes
```

---


## 60. Full Closed Loop

```text
Source ecosystem
      │
      ▼
OFFLINE CONTROL PLANE
crawl
normalize
dedupe
LLM analyze
rewrite
classify
benchmark
security
license
promote
version
      │
      ▼
CAPABILITY WAREHOUSE
      │
      ▼
CAPABILITY PLANE
intent
retrieve
rerank
JEV
compatibility
compose
policy
      │
      ▼
CapabilityBundle
      │
      ▼
AGENT RUNTIME
HarnessKernel
Profile
Loop
Tools
Capabilities
Verify
      │
      ▼
ResultContract
      │
      ▼
CLIENT ORCHESTRATOR
accept / reject
      │
      ▼
Outcome
      │
      ├──────────────► routing telemetry
      ├──────────────► harness benchmark
      ├──────────────► capability evidence
      └──────────────► offline gap/deprecation proposals
```

---


# PART V — ADVANCED RUNTIME HARDENING


## 47. Model ↔ Runtime Action Protocol

The model should not communicate arbitrary control flow through natural-language conventions such as:

```text
"I think I should use a tool now..."
```

Instead, the runtime should normalize provider outputs into a small action union.

### 47.1 ModelAction

```text
ModelAction =
    FinalCandidate
  | ToolCallBatch
  | CapabilityRequest
  | DelegationRequest
  | PlanUpdateRequest
  | ClarificationRequest
  | NoOp / Continue
```

Provider-specific details stay in ModelGateway.

#### ToolCallBatch

```json
{
  "type": "tool_calls",
  "calls": [
    {
      "call_id": "call_123",
      "tool_id": "filesystem.read",
      "arguments": {
        "path": "src/app.py"
      }
    }
  ]
}
```

#### CapabilityRequest

```json
{
  "type": "capability_request",
  "need": {
    "objective": "Diagnose PostgreSQL query plan",
    "reason": "Current active capabilities do not cover query optimization",
    "desired_kinds": ["skill", "tool"],
    "constraints": []
  }
}
```

#### DelegationRequest

```json
{
  "type": "delegation_request",
  "subtask": {
    "objective": "Inspect test failures and identify root cause",
    "required_output": "structured_findings"
  },
  "requested_budget": {
    "tokens": 12000,
    "tool_calls": 12
  }
}
```

#### FinalCandidate

```json
{
  "type": "final_candidate",
  "result": {...},
  "completion_claim": {
    "criteria_addressed": ["AC-1", "AC-2"]
  }
}
```

The runtime still verifies the claim.

### 47.2 Why normalize actions

This provides:

- provider independence;
- deterministic validation;
- explicit authority boundaries;
- easier replay;
- simpler testing;
- stable telemetry;
- no parsing of hidden chain-of-thought.

The runtime only needs observable action outputs, never private reasoning.

---


## 48. Resource Locking and Concurrency Control

As the harness grows, concurrency bugs can become more dangerous than model errors.

### 48.1 Resource model

Represent mutable resources explicitly.

```text
ResourceKey examples:

file:/repo/src/app.py
tree:/repo/src/**
git:index
workspace:process-table
service:postgres-dev
external:github/repo/pr/123
```

### 48.2 Access modes

```text
READ
WRITE
EXCLUSIVE
```

### 48.3 Lock rules

- multiple readers may coexist;
- writer conflicts with readers/writers;
- destructive operations require exclusive scope;
- parent and child agent operations use the same lock authority when sharing a workspace;
- locks have leases/timeouts to prevent deadlock after process failure.

### 48.4 Tool declaration

```yaml
resource_access:
  static:
    read:
      - tree:/repo/src/**
  dynamic:
    derive_from_arguments: true
```

A filesystem tool can derive the precise file resource from arguments.

### 48.5 Child agent isolation

Preferred order:

1. separate workspace/snapshot if practical;
2. otherwise explicit resource partition;
3. otherwise serialize children.

Do not allow uncontrolled concurrent edits to the same repository.

### 48.6 Merge stage

For isolated child workspaces:

```text
Child A patch ─┐
               ├─► MergeCoordinator ─► conflict check ─► parent workspace
Child B patch ─┘
```

Merge is an explicit side effect and must pass authority/verification.

---


## 49. Capability Activation Lifecycle

Capabilities should have a runtime lifecycle rather than simply "append prompt text".

### 49.1 States

```text
DISCOVERED
RESOLVED
VALIDATED
AUTHORIZED
LOADED
ACTIVE
SUSPENDED
UNLOADED
FAILED
```

### 49.2 Activation flow

```text
ACI candidate
  ↓
resolve immutable manifest
  ↓
validate signature/hash/schema
  ↓
check runtime compatibility
  ↓
check authority requirements
  ↓
load instructions/resources
  ↓
bind tools/MCP
  ↓
ContextEngine registers capability context
  ↓
ACTIVE
```

### 49.3 CapabilityHandle

```python
CapabilityHandle:
    capability_id
    version
    manifest_hash
    activation_id
    status
    loaded_tools
    context_refs
    mcp_sessions
    authority_requirements
    activated_at
```

### 49.4 Unload rules

Before unloading:

- ensure no tool call is in flight;
- preserve evidence/artifacts already produced;
- close capability-owned MCP sessions if not shared;
- remove capability instructions from future context;
- keep immutable activation record for trace.

### 49.5 Conflict handling

If capability A conflicts with B:

```text
ACI bundle declares conflict
        ↓
CapabilityRuntime sees B active
        ↓
choose according to bundle/composer policy
        ↓
suspend/unload B OR reject A
```

Do not silently run conflicting capability instructions simultaneously.

---


## 50. Context Selection Scoring

v2 does not require a learned context router. Use a transparent heuristic first.

For candidate context item \(i\):

\[
Score(i)
=
w_r R_i
+
w_p P_i
+
w_f F_i
+
w_d D_i
-
w_c C_i
\]

Where:

- \(R_i\): semantic/task relevance;
- \(P_i\): explicit priority;
- \(F_i\): freshness/recency;
- \(D_i\): dependency importance;
- \(C_i\): token cost penalty.

Pinned items bypass ranking.

A practical policy:

```text
1. pinned invariants
2. pinned task/acceptance criteria
3. active capability instructions
4. unresolved errors and latest evidence
5. current-plan dependencies
6. relevant recent conversation
7. retrieved historical context
```

### 50.1 Diversity

Do not spend all budget on 10 nearly identical search results.

Apply source/type diversity:

- code;
- test;
- documentation;
- tool result;
- decision;
- capability instruction.

### 50.2 Context integrity

Every summary should retain references to original artifacts.

```json
{
  "summary": "Three failing tests point to auth token expiry.",
  "source_refs": [
    "artifact://pytest/run-17",
    "file://tests/test_auth.py"
  ]
}
```

---


## 51. Expanded Failure / Recovery Matrix

| Failure class | Automatic retry? | Replan? | ACI alternate? | Human escalation? | Notes |
|---|---:|---:|---:|---:|---|
| TRANSIENT_MODEL | bounded | no | no | rarely | exponential backoff |
| RATE_LIMITED | bounded/fallback | no | no | no | respect provider retry hints |
| MODEL_MALFORMED_OUTPUT | repair + retry | maybe | no | no | schema-focused repair |
| TOOL_INVALID_ARGUMENT | usually no raw retry | maybe | no | no | return validation feedback |
| TOOL_TIMEOUT read-only | bounded | maybe | no | maybe | tool-specific |
| TOOL_TIMEOUT mutation | not blindly | yes | no | maybe | side-effect uncertainty |
| TOOL_EXECUTION_FAILED | bounded if safe | yes | maybe | maybe | classify root cause |
| AUTHORITY_BLOCKED | no | yes | no | optional | replan within grants |
| APPROVAL_REJECTED | no | yes | no | no | never circumvent |
| WORKSPACE_UNAVAILABLE | bounded reconnect | maybe | no | maybe | checkpoint if possible |
| SANDBOX_DENIED | no automatic escape | maybe | no | approval maybe | remain least privilege |
| CAPABILITY_NOT_FOUND | no | yes | yes | maybe | broaden request once |
| CAPABILITY_LOAD_FAILED | bounded | yes | yes | maybe | validate manifest |
| CAPABILITY_INSUFFICIENT | no | yes | yes | maybe | include evidence in ACI request |
| CAPABILITY_CONFLICT | no | yes | yes | no | composer/runtime conflict resolution |
| CONTEXT_OVERFLOW | compact | no | no | no | preserve pinned items |
| VERIFICATION_FAILED | no simple retry | yes | maybe | maybe | evidence-driven repair |
| RESULT_CONTRACT_FAILED | repair | maybe | no | no | structure before semantics |
| BUDGET_EXHAUSTED | no | no | no | optional | partial/resume |
| CANCELLED | no | no | no | no | terminal |
| FATAL | no | no | no | maybe | preserve diagnostic state |

---


## 52. Deployment Topology

Support at least three deployment modes without changing domain code.

### 52.1 Embedded local mode

```text
CLI / OpenCode adapter
       │
       └── AgentRuntime SDK
              ├─ LocalWorkspace
              ├─ local state
              └─ ACI remote/local
```

Best for:

- development;
- personal local coding;
- lowest latency.

### 52.2 Local runtime server

```text
OpenCode / UI
     │ HTTP/WebSocket
     ▼
Agent Runtime Server
     │
     ├─ local/sandbox workspace
     └─ ACI service
```

Benefits:

- process isolation;
- multiple clients;
- runtime upgrades independent of clients.

### 52.3 Remote production runtime

```text
Clients
   │
API Gateway
   │
Runtime Control Service
   │
   ├── State/Checkpoint DB
   ├── Event/Telemetry
   ├── ACI
   └── Workspace Provisioner
            │
            ├── ephemeral container
            ├── Kubernetes pod
            └── remote dev environment
```

### 52.4 Server API surface

Keep the core small:

```text
POST /runs
GET  /runs/{id}
GET  /runs/{id}/events
POST /runs/{id}/cancel
POST /runs/{id}/interrupt-responses
POST /runs/{id}/resume
GET  /runs/{id}/artifacts
```

Streaming:

- WebSocket, SSE, or equivalent.
- Events are typed EventEnvelope objects.

---


## 53. Backpressure and Overload Protection

A production harness must fail predictably under load.

### 53.1 Limits

Apply:

- max concurrent runs/user;
- max concurrent model calls;
- max concurrent sandbox allocations;
- max child runs;
- max MCP calls/server;
- bounded event queues;
- artifact upload limits.

### 53.2 Backpressure behavior

Prefer:

```text
queue / reject explicitly
```

over:

```text
accept everything → memory exhaustion
```

Use machine-readable responses:

```text
RUNTIME_BUSY
PROVIDER_RATE_LIMITED
SANDBOX_CAPACITY_EXHAUSTED
```

### 53.3 Priority

Optional v2:

```text
interactive
normal
background
```

Do not build a complex enterprise scheduler unless actual workloads require it.

---


## 54. Schema and Version Compatibility

Every persistent or cross-service contract needs versioning.

### 54.1 Versioned contracts

At minimum:

- RuntimeSpec;
- RuntimeState;
- Checkpoint;
- CapabilityBundle;
- ToolSpec;
- EventEnvelope;
- RunResult;
- EvidenceBundle.

### 54.2 Compatibility rules

Prefer additive changes.

```text
v2 reader can ignore unknown optional fields
```

Breaking changes require:

- new schema version;
- migration;
- compatibility tests.

### 54.3 Checkpoint migrations

```python
migrate_checkpoint_v1_to_v2(...)
migrate_checkpoint_v2_to_v3(...)
```

Never silently deserialize old schema into changed semantics.

### 54.4 Capability contract negotiation

ACIClient and ACI server should expose supported protocol versions.

```text
client protocol: 2
server protocols: [1,2]
→ use 2
```

---


## 55. Determinism and Replay

Agent output is probabilistic, but runtime behavior around it can still be reproducible.

Record:

- model name/version when available;
- model request hash;
- model normalized action response;
- tool request/result;
- capability bundle;
- policy decision;
- approval;
- state version;
- verifier outcome.

Replay mode can replace:

```text
live model → recorded ModelAction
live tool  → recorded ToolObservation
live ACI   → recorded CapabilityBundle
```

This allows reproducing lifecycle/state bugs without paying for external calls.

Do not claim bit-identical model reproducibility.

---


## 56. Runtime SLO / Quality Targets

Exact numbers should be benchmark-driven, but define categories from day one.

### 56.1 Reliability objectives

Examples of measurable targets:

- checkpoint restore correctness;
- no unauthorized operation in security suite;
- deterministic state transition validity;
- zero silent tool execution failure;
- trace completeness for all external effects.

### 56.2 Performance objectives

Track:

- p50/p95 harness overhead excluding model/tool external latency;
- context assembly latency;
- policy evaluation latency;
- checkpoint write latency;
- event queue lag.

The harness itself should not become the dominant latency source.

### 56.3 Cost objectives

Track baseline overhead from:

- verification model;
- planning;
- capability lookup;
- context summarization;
- recovery.

Any reasoning stage must justify measurable task-success improvement.

---


## 57. Fault Injection / Chaos Checklist

Regularly simulate:

#### Model
- provider unavailable;
- malformed response;
- stream disconnect;
- timeout;
- rate limit.

#### ACI
- slow search;
- stale capability version;
- resolve 404;
- incompatible bundle;
- malformed manifest.

#### Tool
- timeout;
- partial side effect;
- huge output;
- non-zero exit;
- invalid encoding.

#### Workspace
- container dies;
- disk full;
- process hangs;
- file changes outside expected scope.

#### Storage
- checkpoint write fails;
- artifact store temporarily unavailable;
- stale state version conflict.

#### Approval
- user rejects;
- response arrives after expiry;
- duplicate response;
- changed operation after approval.

#### Child agent
- child budget exhausted;
- child tries escalation;
- child returns malformed result;
- parent cancelled while child runs.

The expected stop/recovery outcome should be asserted for each case.

---


## 58. Authority Evaluation Pseudocode

```python
async def evaluate_tool_call(call, ctx):
    tool = registry.require(call.tool_id)

    validated_args = tool.input_schema.validate(call.arguments)

    pre_guard = await guardrails.check_pre_tool(
        tool=tool,
        args=validated_args,
        context=ctx,
    )
    if pre_guard.blocked:
        return Blocked(pre_guard.reason)

    requirement = tool.derive_authority(validated_args)

    decision = authority.evaluate(
        requirement=requirement,
        grants=ctx.grants,
        policy=ctx.authority_policy,
    )

    if decision.kind == "DENY":
        return Denied(decision.reason)

    if decision.kind == "REQUIRE_APPROVAL":
        return PendingApproval(
            approval_request=authority.make_approval_request(...)
        )

    envelope = authority.build_execution_envelope(
        decision=decision,
        workspace=ctx.workspace,
    )

    return Authorized(
        tool=tool,
        args=validated_args,
        execution_envelope=envelope,
    )
```

Important: execution happens **after** this function, never inside policy evaluation.

---


## 59. Run Loop Pseudocode

```python
async def run(task, spec, cancel_token):
    state = await states.create(task, spec)
    await events.emit(RunStarted(...))

    while True:
        cancel_token.raise_if_cancelled()

        budget.check_run(state)

        snapshot = await states.snapshot(state.run_id)

        model_context = await context_engine.build(
            state=snapshot,
            spec=spec,
        )

        model_result = await model_gateway.invoke(
            context=model_context,
            tools=tool_runtime.visible_specs(snapshot),
            policy=spec.model_policy,
            cancel_token=cancel_token,
        )

        action = normalize_model_action(model_result)

        if isinstance(action, ToolCallBatch):
            observations = []
            for call in action.calls:
                cancel_token.raise_if_cancelled()
                observation = await tool_runtime.execute(
                    call,
                    run_context=snapshot,
                )
                observations.append(observation)

            await states.commit_tool_observations(
                expected_version=snapshot.version,
                observations=observations,
            )
            continue

        if isinstance(action, CapabilityRequest):
            outcome = await capability_runtime.handle_request(
                action,
                snapshot,
            )
            await states.commit_capability_outcome(...)
            continue

        if isinstance(action, DelegationRequest):
            child_result = await delegation.run_child(...)
            await states.commit_child_result(...)
            continue

        if isinstance(action, FinalCandidate):
            verification = await verifier.verify(...)
            if verification.passed:
                return await finalize_success(...)
            failure = FailureEnvelope.verification_failed(verification)
            recovery_action = await recovery.decide(failure, snapshot)
            if recovery_action.is_terminal:
                return await finalize_failure(...)
            await apply_recovery(recovery_action)
            continue
```

Real implementation needs interrupts, checkpoints, streaming, errors, and version conflicts, but the control shape should remain understandable.

---


## 60. Tool Execution Pseudocode

```python
async def execute(call, run_context):
    tool = registry.require(call.tool_id)

    args = validator.validate(tool, call.arguments)

    guard_result = await guardrails.pre_tool(tool, args, run_context)
    if guard_result.blocked:
        return ToolObservation.blocked(...)

    auth = authority.evaluate_tool(tool, args, run_context)

    if auth.requires_approval:
        checkpoint = await checkpoints.before_interrupt(...)
        raise ApprovalInterrupt(auth.request, checkpoint)

    if auth.denied:
        return ToolObservation.denied(...)

    execution_ctx = await workspace.prepare(
        envelope=auth.execution_envelope,
        tool=tool,
    )

    try:
        raw = await dispatcher.invoke(
            tool,
            args,
            execution_ctx,
            cancel_token=run_context.cancel_token,
        )
    except Exception as exc:
        return error_normalizer.normalize(exc, tool, execution_ctx)

    limited = await output_limiter.process(raw, tool.output_policy)

    guarded = await guardrails.post_tool(
        tool,
        args,
        limited,
        run_context,
    )

    return ToolObservation.from_result(
        guarded,
        side_effects=execution_ctx.side_effect_report(),
    )
```

---


## 61. ACI ↔ Harness Contract Details

The boundary should carry enough information for good selection without leaking internal implementation.

### Harness → ACI

Send:

- normalized objective;
- task type/domain if known;
- environment summary;
- active capability IDs/versions;
- available execution features;
- authority summary;
- context/token constraints;
- prior capability failure evidence when relevant.

Avoid sending:

- secret values;
- raw private chain-of-thought;
- entire repository by default;
- entire chat history;
- provider credentials.

### ACI → Harness

Return:

- capability ID/version;
- payload reference;
- reason/fit metadata;
- dependencies;
- conflicts;
- required runtime features;
- required authority;
- context cost estimate;
- trust/provenance status;
- optional fallback candidates.

### Runtime rejection

Harness can reject an ACI recommendation due to runtime facts:

```text
insufficient authority
missing sandbox feature
missing MCP support
budget too low
conflict with active capability
capability hash mismatch
```

That rejection should become structured feedback, not silent failure.

---


## 62. Capability Trust Levels

Suggested runtime trust categories:

```text
OFFICIAL
VERIFIED
REVIEWED
UNVERIFIED
QUARANTINED
REVOKED
```

Policy examples:

- `OFFICIAL/VERIFIED`: normal eligibility.
- `REVIEWED`: allowed under standard policy.
- `UNVERIFIED`: require stricter sandbox or approval.
- `QUARANTINED`: not selectable in production.
- `REVOKED`: refuse activation.

Trust level is metadata from ACI/control plane; runtime policy decides what it means operationally.

---


## 63. Observability Correlation Model

Every operation should be connectable.

```text
trace_id
  └─ run_id
      ├─ turn_id
      │   ├─ model_call_id
      │   └─ tool_call_id
      ├─ capability_activation_id
      ├─ approval_id
      ├─ verification_id
      └─ child_run_id
```

A debugging UI should answer:

- What did the runtime know at turn 8?
- Which capability was active?
- Which permissions existed?
- Why was a tool approved?
- What changed in the workspace?
- Why did verification fail?
- Which recovery path was selected?
- What did the run cost?

---


## 64. Data Retention and Privacy Controls

Production telemetry must distinguish:

- metadata;
- prompts/context;
- tool outputs;
- artifacts;
- secrets;
- source code.

Provide policy controls:

```yaml
retention:
  event_metadata_days: 30
  prompt_content: disabled
  tool_output_content: artifact_only
  source_code_snapshots: disabled
  secret_redaction: strict
```

Do not assume traces may store everything.

---


## 65. Model Routing Policy

`ModelGateway` should route by workload class, not random provider switching.

Example classes:

```text
FAST_CLASSIFICATION
TOOL_CALLING
GENERAL_REASONING
DEEP_REASONING
VERIFICATION
SUMMARIZATION
```

Each class defines:

- allowed models;
- required features;
- cost ceiling;
- context size;
- fallback.

Do not let the Planner directly hardcode provider/model names.

---


## 66. Verification Profile Examples

### CodingStandard

```yaml
verifier:
  deterministic:
    - result_schema
    - changed_files_exist
  commands:
    - tests_if_present
    - lint_if_configured
  llm_evaluator:
    enabled: false
```

### CodingStrict

```yaml
verifier:
  deterministic:
    - result_schema
    - changed_files_exist
    - no_unexpected_binary
  commands:
    - unit_tests
    - typecheck
    - lint
  security:
    - dependency_scan_if_changed
  llm_evaluator:
    enabled: conditional
```

### ResearchEvidence

```yaml
verifier:
  deterministic:
    - citations_present
    - required_sections
  evidence:
    - source_refs_resolvable
  evaluator:
    enabled: conditional
```

Verifier profiles should be composable but bounded.

---


## 67. Runtime Policy Precedence

Recommended order, strongest first:

```text
hard platform safety policy
      ↓
organization policy
      ↓
workspace/project policy
      ↓
agent profile policy
      ↓
run-specific grants
      ↓
capability requirements
      ↓
model request
```

A lower layer can narrow authority but cannot widen beyond higher-layer maximums.

---


# PART VI — TESTING, BENCHMARKING, SECURITY VALIDATION, AND EVALUATION


## 32. Testing Strategy

### 32.1 Unit tests

Every manager independently:

- deterministic inputs;
- fake services;
- no real model.

### 32.2 Contract tests

Critical interfaces:

- ModelGateway normalized responses;
- ACI CapabilityBundle;
- ToolSpec;
- GrantEnvelope;
- Checkpoint schema;
- RunResult.

### 32.3 State transition tests

Test every legal/illegal transition.

Example:

```text
RUNNING → VERIFYING      allowed
VERIFYING → SUCCEEDED    allowed on PASS
CREATED → SUCCEEDED      forbidden
CANCELLED → RUNNING      forbidden
```

### 32.4 Recovery tests

Fault injection:

- model timeout;
- invalid JSON;
- MCP failure;
- workspace death;
- sandbox denial;
- verification failure;
- lost checkpoint;
- duplicate approval;
- capability unavailable.

### 32.5 Security tests

- path traversal;
- symlink escape;
- shell injection;
- approval replay;
- child privilege escalation;
- capability permission self-escalation;
- secret exfiltration into logs;
- MCP malicious output;
- prompt injection from repository;
- concurrent state races.

### 32.6 Replay tests

Persist event trace and replay with fake model/tool responses to reproduce bugs.

### 32.7 End-to-end coding scenarios

At minimum:

1. simple single-file edit;
2. multi-file refactor;
3. test failure → repair;
4. dependency install requiring approval;
5. capability loaded mid-run;
6. capability replacement after failure;
7. child agent isolated task;
8. cancellation during model streaming;
9. cancellation during tool process;
10. checkpoint + resume;
11. context compaction on long task;
12. sandbox failure/recovery.

---


## 33. Evaluation Suite

Harness evaluation is separate from ACI retrieval evaluation.

### 33.1 Harness metrics

- verified task success rate;
- average turns to success;
- tokens per success;
- cost per success;
- recovery success;
- false completion rate;
- unsafe action prevented;
- approval precision;
- checkpoint resume success;
- context compaction regression rate.

### 33.2 False completion rate

Critical metric:

\[
FCR =
\frac{\text{runs model claimed done but verifier failed}}
     {\text{runs model claimed done}}
\]

A high FCR means prompts/planning/verification feedback need work.

### 33.3 Recovery efficiency

\[
RecoveryEfficiency =
\frac{\text{recovered verified successes}}
     {\text{recovery attempts}}
\]

Also track incremental cost of recovery.

### 33.4 ACI-harness joint metrics

- capability request frequency;
- top-1 accepted capability;
- capability replacement rate;
- success conditional on selected capability;
- cost added by capability search;
- quality uplift versus no ACI;
- failure reduction versus static skills.

---


## 34. ACI Feedback Loop

```text
ACI selects Capability A
        ↓
AgentRuntime executes
        ↓
Verification produces evidence
        ↓
Outcome normalized
        ↓
capability.feedback()
        ↓
ACI telemetry/control plane
        ↓
offline quality update
```

Runtime feedback MUST contain outcomes, not opaque subjective ratings.

Good:

```text
tests_passed = true
verified_success = true
repair_count = 1
cost = ...
```

Weak:

```text
"skill felt good: 9/10"
```

ACI should aggregate real evidence over time.

---


## 42. Benchmark Strategy

Harness quality must be separated from model quality.

Comparison rule:

```text
same model
same task set
same tools
same environment
same budget
same capabilities
different harness variant
```

---


## 43. ACI-HarnessBench

Create a private benchmark suite.

Categories:

```text
simple implementation
multi-file implementation
hidden dependency
debugging
long-running research
context overflow
tool failure
permission denial
checkpoint/resume
verification failure
capability insufficiency
no-progress loop
model transient failure
workspace restart
client revision
```

---


## 44. Metrics

Primary:

```text
Task Success Rate
Pass@1
False Acceptance Rate
Recovery Rate
Cost / Successful Task
Tokens / Successful Task
Latency / Successful Task
```

Secondary:

```text
Turn count
Tool calls
Retry rate
Replan rate
Capability refresh rate
Context peak
Compaction frequency
Checkpoint overhead
```

---


## 45. Context Efficiency Ratio

Define:

```text
CER =
Orchestrator-visible summary tokens
/
Total service execution tokens
```

This measures whether hierarchical context isolation is working.

Do not optimize CER alone.

Constraint:

```text
minimize CER
subject to
task success >= target
false acceptance <= threshold
```

---


## 46. Benchmark Variants

Test:

```text
Harness A:
minimal loop

Harness B:
planning + context management

Harness C:
B + verification

Harness D:
C + recovery

Harness E:
D + checkpoint
```

This allows mechanism-level attribution.

---


## 47. Initial SLO Targets

These are engineering targets, not guarantees.

#### Runtime service availability

```text
>= 99.5% during development target
```

#### Checkpoint resume correctness

```text
no duplicate mutation in deterministic test suite
```

#### Permission leakage

```text
0 known unauthorized actions
```

#### Result contract validity

```text
100% schema-valid final responses
```

#### False success due to failed mandatory verifier

```text
0 allowed
```

If required verifier fails:

```text
status != completed
```

---


## 48. Testing Pyramid

---

### 48.1 Unit tests

Every manager independently:

```text
RunController
StateManager
ContextEngine
AuthorityManager
CheckpointCoordinator
RecoveryManager
VerificationManager
```

---

### 48.2 Contract tests

```text
SubtaskContract
CapabilityBundle
ToolGrant
VerificationReport
EvidencePack
ResultContract
```

---

### 48.3 Deterministic simulation tests

Fake model + fake tools.

Test exact lifecycle:

```text
turn
tool
failure
retry
verification
completion
```

These MUST be fast and stable.

---

### 48.4 Integration tests

Real:

```text
Postgres
object store
sandbox
capability router
MCP
```

---

### 48.5 Model-in-loop evals

Only after deterministic infrastructure is correct.

---


## 49. Security Tests

Mandatory:

```text
path traversal
symlink escape
shell command scope
network restrictions
secret leakage
tool grant escalation
child permission escalation
checkpoint tampering
artifact digest mismatch
prompt injection through capability content
prompt injection through repo files
prompt injection through tool output
MCP server trust boundary
```

---


## 50. Prompt Injection Treatment

All external content is data, not authority.

Sources:

```text
repo files
web pages
capability content
tool output
logs
documents
```

must not silently override:

```text
system policy
AgentProfile
ToolGrant
SubtaskContract
```

---


## 51. Capability Content Trust

Even promoted capability instructions should be treated as versioned controlled inputs.

At runtime:

```text
verify digest
verify active release
verify compatibility
verify policy
then load
```

---


## 52. Versioning

Version independently:

```text
HarnessKernel version
AgentProfile version
LoopPolicy version
VerifierProfile version
Capability versions
Model version
Tool provider version
```

Every run records all of these.

This is essential for benchmark reproducibility.

---


# PART VII — OPEN-SOURCE DONOR DISTILLATION AND ANTI-FRANKENSTEIN METHOD


## 22A. Donor Rule

Projects are architectural references, not mandatory dependencies.

The extraction workflow is:

```text
Observe
 ↓
Extract mechanism
 ↓
Identify invariant
 ↓
Identify failure mode
 ↓
Generalize
 ↓
Benchmark
 ↓
Reimplement behind ACI-native contract
```

A donor implementation is not copied merely because it is successful in its native environment.


## 22. Donor Architecture Mapping

ACI must extract mechanisms, not dependencies.

---

### 22.1 Strands Agents Harness SDK

Use primarily for:

```text
run lifecycle
turn limits
token budgets
cancellation
stop reasons
hooks
interventions
sessions
guardrails
tracing
evals
```

ACI mapping:

```text
RunController
EventBus
BudgetState
InterventionPipeline
```

Do not make Strands a mandatory runtime dependency.

---

### 22.2 Pydantic AI Harness

Use primarily for:

```text
capability composition
planning as composable behavior
subagent budget concepts
context controls
tool output limits
progressive tool/capability disclosure
memory/search
repo context
```

ACI mapping:

```text
HarnessCapability interface
ContextPolicy
CapabilityRuntime
ToolOutputPolicy
```

Key principle:

```text
behavior = composable capability
```

not giant subclass trees.

---

### 22.3 Deep Agents

Use primarily for:

```text
planning
filesystem as working memory
isolated subagent contexts
skills on demand
context offloading
```

ACI mapping:

```text
ContextEngine
ArtifactStore
Planner
future DelegationManager
```

Do not import its global orchestration assumptions wholesale.

---

### 22.4 Microsoft Agent Framework

Use primarily for:

```text
workflow checkpoints
HITL
pause/resume
multi-step workflow execution
workflow-level durability
```

ACI mapping:

```text
CheckpointCoordinator
Interrupt
Resume
future workflow layer
```

---

### 22.5 OpenAI Agents SDK

Use primarily for:

```text
Runner lifecycle
turn accounting
handoffs
guardrail ordering
hooks
sessions
interruption/resume semantics
```

ACI mapping:

```text
RunController semantics
Guardrail/Verifier ordering
Resume invariants
```

---

### 22.6 Google ADK 2

Use primarily for:

```text
session/state separation
workflow nodes
routing
fan-out/fan-in
retry
delegation
HITL
```

ACI mapping:

```text
future WorkflowRuntime
StateManager concepts
```

---

### 22.7 LangGraph

Use primarily for:

```text
checkpoint
resume
interrupt
durable graph state
fault recovery
```

ACI mapping:

```text
CheckpointStore contract
InterruptState
ResumeToken
```

Do NOT make LangGraph graph topology the core ACI runtime model.

---

### 22.8 Codex CLI

Use primarily for:

```text
execution policy
sandbox
approvals
tool boundaries
context/instruction layering
```

ACI mapping:

```text
AuthorityManager
WorkspaceManager
ContextPolicy
```

---

### 22.9 OpenHands Software Agent SDK

Use primarily for:

```text
runtime / workspace separation
agent server boundary
events
remote runtime
ephemeral workspaces
```

ACI mapping:

```text
Workspace interface
AgentRuntime service boundary
EventBus
```

This is one of the strongest structural references for the service runtime.

---

### 22.10 SWE-agent / mini-SWE-agent

Use primarily for:

```text
minimal agent-computer interface
simple loop
environment/model separation
hard execution limits
```

ACI mapping:

```text
LoopPolicy
Tool/Environment interface
anti-overengineering benchmark
```

---

### 22.11 OpenCode

Use primarily for:

```text
permission model
plan/read-only separation
subagent permission constraints
tool grants
provider abstraction
```

ACI mapping:

```text
AuthorityManager
AgentProfile permission model
```

Be especially careful with nested/non-interactive approval flows; service agents should fail closed rather than wait indefinitely for invisible approvals.

---

### 22.12 Cline

Use primarily for:

```text
plan/act distinction
checkpoint concepts
MCP integration
```

ACI mapping:

```text
read-only Architect mode
execution-mode authority switching
```

---

### 22.13 Aider

Use primarily for:

```text
repo map
context selection
architect → editor separation
```

ACI mapping:

```text
CodeContextProvider
ArchitectProfile → CoderProfile artifact handoff
```

Architect output MUST be validated as untrusted input by the executor.

---

### 22.14 Goose

Use primarily for:

```text
extension architecture
tool/MCP boundary
maintained plans
protocol-oriented extensibility
```

ACI mapping:

```text
ToolProvider
CapabilityProvider
protocol adapters
```

---

### 22.15 Gemini CLI

Use primarily for:

```text
sandboxing
approval modes
trusted folders
tool policies
sandbox expansion
MCP
```

ACI mapping:

```text
WorkspacePolicy
AuthorityManager
SandboxExpansionRequest
```

---

### 22.16 Mastra

Use primarily for:

```text
memory/storage
observability
workflow operations
```

ACI mapping:

```text
Telemetry
MemoryProvider
operations patterns
```

---

### 22.17 Agno

Use primarily for:

```text
runtime/control-plane split
sessions
services
team/workflow operations
```

ACI mapping:

```text
runtime/control plane boundary reference
```

---

### 22.18 smolagents

Use primarily for:

```text
minimal readable loop
small abstraction surface
```

Purpose:

```text
anti-overengineering reference
```

If ACI requires dozens of abstractions to perform a trivial run, compare against smolagents and simplify.

---

### 22.19 AutoGen

Reference only.

Study:

```text
message/event patterns
multi-agent historical designs
```

Do not choose as the new ACI foundation if newer successor architectures better match the target.

---

### 22.20 Semantic Kernel

Historical enterprise reference.

Study:

```text
plugin contracts
enterprise process patterns
```

Do not make it a primary runtime dependency.

---


## 23. Harness Capabilities

Harness behavior itself should become composable.

Recommended interfaces:

```text
HarnessCapability
├── before_run
├── before_turn
├── before_model
├── after_model
├── before_tool
├── after_tool
├── before_verify
├── after_verify
└── after_run
```

Examples:

```text
ContextCompactionCapability
CostTrackerCapability
RepoContextCapability
PlanningCapability
SecurityGuardCapability
CheckpointCapability
ToolOutputLimiterCapability
CapabilityLoadingCapability
```

Avoid hard-coding every behavior directly into RunController.

RunController coordinates.
Capabilities extend.

---


## 24. Lifecycle Ordering

Recommended strict ordering:

```text
1. receive SubtaskContract
2. validate task
3. resolve AgentProfile
4. create RunState
5. initialize workspace
6. initialize context
7. load initial CapabilityBundle
8. run pre-run policies
9. local planning if enabled
10. start turn

FOR EACH TURN:
    a. context selection
    b. budget check
    c. pre-model hooks
    d. model call
    e. validate structured model output
    f. determine actions
    g. authority check
    h. tool execution
    i. record observation
    j. update task/local plan
    k. checkpoint if required
    l. progress/no-progress check
    m. decide next step

WHEN CANDIDATE COMPLETION:
    a. enter VERIFYING
    b. run deterministic verifier
    c. run optional evaluator
    d. if fail → RecoveryManager
    e. if pass → build EvidencePack
    f. build ResultContract
    g. persist final checkpoint
    h. emit telemetry
    i. return
```

---


## 25. Hook / Intervention Pipeline

Suggested hook categories:

```text
before_run
after_run

before_turn
after_turn

before_model
after_model

before_tool
after_tool

before_capability_load
after_capability_load

before_checkpoint
after_checkpoint

before_verification
after_verification
```

Interventions:

```text
ALLOW
GUIDE
DENY
PAUSE
ESCALATE
```

Ordering principle:

```text
cheap deterministic checks first
expensive LLM steering last
```

Example:

```text
permission
→ schema validation
→ security policy
→ budget
→ deterministic guard
→ optional LLM steering
```

---


## 26. Model Abstraction

Harness MUST NOT assume one provider.

```python
ModelProvider.generate(...)
ModelProvider.stream(...)
ModelProvider.usage(...)
ModelProvider.cancel(...)
```

Record per turn:

```text
provider
model id
reasoning mode
input tokens
output tokens
cached tokens
latency
cost
```

---


## 27. Model Escalation

A stronger model is a recovery strategy, not the default.

Example policy:

```text
default model
    ↓ failure/no-progress
stronger model
    ↓
still fail
return escalated
```

Never silently switch models without recording it.

---


## 70. Repository Mining Workflow

When studying donor repos, use this template.

For each donor:

```yaml
project:

mechanism:
problem_solved:

inputs:
outputs:

state_owned:

lifecycle:

invariants:

failure_modes:

security_boundary:

context_cost:

benchmark_evidence:

aci_mapping:

reuse_strategy:
  copy: false
  concept_only: true

decision:
  adopt | adapt | reject | benchmark
```

Do not store merely:

```text
"OpenHands has sandbox"
```

Store:

```text
what problem sandbox abstraction solves
what contract it exposes
what failure modes it has
how ACI should implement the generalized mechanism
```

---


## 71. Mechanism Corpus

Create a dedicated architecture research corpus:

```text
research/
└── harness-mechanisms/
    ├── lifecycle/
    ├── context/
    ├── planning/
    ├── permissions/
    ├── workspace/
    ├── checkpoint/
    ├── recovery/
    ├── verification/
    ├── memory/
    ├── delegation/
    └── observability/
```

Example record:

```yaml
id: context.tool_output_spill

sources:
  - pydantic-ai-harness
  - gemini-cli

problem:
  large tool outputs exhaust context

mechanism:
  inline small results
  summarize medium results
  spill large results to artifact storage

aci_target:
  runtime/context/tool_output.py

benchmark:
  compare context peak and task success
```

---


## 72. Decision Rule for Adopting a Mechanism

A mechanism SHOULD NOT enter ACI merely because a top project uses it.

Require:

```text
1. problem exists in ACI
2. mechanism has understandable causal benefit
3. compatible with ACI boundaries
4. security implications understood
5. benchmarkable
6. simpler alternatives considered
7. implementation cost justified
```

---


## 73. Anti-Frankenstein Rule

Never design a class solely because one donor has it.

Wrong:

```text
Strands HookManager
LangGraph Node
DeepAgents Middleware
Pydantic Capability
OpenHands Event
```

all duplicated.

Instead normalize to ACI concepts:

```text
EventBus
HarnessCapability
Hook
Intervention
State
Workspace
Verifier
```

One concept, one owner.

---


## 74. Minimality Rule

Every subsystem must justify itself with one of:

```text
safety
reliability
context efficiency
recovery
observability
specialization
durability
```

If it provides none of these, remove it.

---


# PART VIII — IMPLEMENTATION ROADMAP, PRODUCTION GATES, AND FREEZE DECISIONS


## 35. Implementation Plan

Build vertical slices, not folders in isolation.

### Phase H0 — Architecture contracts

#### Build
- `RuntimeSpec`
- `RuntimeState`
- `RunStatus`
- `StopReason`
- `FailureEnvelope`
- `RunResult`
- typed event envelope

#### Tests
- serialization;
- schema stability;
- state transition table.

#### Done when
A fake run can move through valid states with no LLM or tool system.

---

### Phase H1 — Minimal deterministic run loop

#### Build
- RunController;
- StateManager;
- fake ModelGateway;
- turn lifecycle;
- budgets for turns/tokens;
- cancellation token;
- stop reasons.

#### Done when
A scripted fake model can execute a multi-turn sequence and produce a deterministic RunResult.

---

### Phase H2 — ToolRuntime

#### Build
- ToolRegistry;
- input schemas;
- Dispatcher;
- OutputLimiter;
- ErrorNormalizer;
- read-only sample tools.

#### Done when
Fake model → tool call → normalized observation → next turn works end to end.

---

### Phase H3 — Authority + guardrails

#### Build
- PolicyEvaluator;
- GrantLedger;
- approval contract;
- pre/post tool guardrails;
- ExecutionEnvelope.

#### Tests
- denied write;
- narrow approval;
- approval replay rejection;
- child grant subset.

#### Done when
No side-effect tool can execute without passing the full policy path.

---

### Phase H4 — Workspace abstraction

#### Build
- BaseWorkspace;
- LocalWorkspace;
- one SandboxWorkspace implementation;
- process timeout;
- process termination;
- snapshots/diffs where available.

#### Done when
Same ToolRuntime tests pass against local and sandbox backends.

---

### Phase H5 — ContextEngine

#### Build
- context items;
- priority selection;
- token estimation;
- tool-output offload;
- compaction;
- ArtifactStore.

#### Tests
- oversized tool output;
- long conversation;
- invariants survive compaction.

#### Done when
A long scripted run stays under a fixed context budget without losing task constraints.

---

### Phase H6 — Real ModelGateway

#### Build
- provider-neutral API;
- one primary provider adapter;
- normalized streaming;
- usage accounting;
- provider error normalization;
- bounded fallback.

#### Done when
RunController has zero provider-specific code.

---

### Phase H7 — Verification + recovery

#### Build
- deterministic verification;
- ResultContract;
- FailureClassifier;
- RecoveryManager;
- retry/repair/replan paths.

#### Done when
A deliberately broken coding task fails verification, repairs, and reaches verified success without manual orchestration.

---

### Phase H8 — ACI CapabilityRuntime

#### Build
- ACIClient;
- search/resolve contracts;
- capability cache;
- capability activation;
- skill injection;
- tool binding;
- feedback.

#### Tests
- capability lookup;
- version pinning;
- incompatible authority requirement;
- alternate capability after failure.

#### Done when
Agent can begin without a domain skill, request one dynamically, activate it, and finish a verified task.

---

### Phase H9 — Checkpoint / interrupt / resume

#### Build
- CheckpointCoordinator;
- CheckpointStore;
- approval interrupt;
- schema versioning;
- restore validation.

#### Done when
A run can pause for approval, process restart, restore, and continue without redoing completed side effects.

---

### Phase H10 — Delegation

#### Build
- child context projection;
- budget reservation;
- grant projection;
- recursion limits;
- child ResultContract;
- aggregation.

#### Done when
Security tests prove no child can gain authority or exceed reserved budget.

---

### Phase H11 — Server boundary

#### Build
- REST/WebSocket or equivalent;
- stream events;
- run/resume/cancel endpoints;
- auth;
- typed client.

#### Done when
OpenCode/custom client can operate runtime without embedding the SDK.

---

### Phase H12 — Production hardening

#### Build
- telemetry sinks;
- trace correlation;
- rate limits;
- backpressure;
- replay tooling;
- schema migrations;
- chaos/fault tests;
- security review;
- performance benchmarks.

#### Done when
The runtime meets defined SLOs and failure drills are reproducible.

---


## 36. Definition of Done for v2

Do not call the harness "v2 production-ready" until all are true.

### Lifecycle
- explicit state machine exists;
- every terminal/pause path has StopReason;
- cancellation tested at multiple checkpoints.

### State
- one authoritative StateManager;
- no hidden mutable manager state affecting correctness;
- checkpoint schemas versioned.

### Context
- hard budget enforced;
- large tool output offloaded;
- compaction preserves invariants.

### Tools
- typed ToolSpec;
- pre-side-effect validation;
- output limits;
- normalized errors.

### Authority
- least privilege;
- approval scoped to exact operation;
- child authority subset verified.

### Workspace
- local + isolated mode;
- runaway process termination;
- side-effect scope enforceable.

### Capability
- ACI search/resolve boundary working;
- capability versions pinned;
- manifests cannot self-grant permissions.

### Recovery
- failure taxonomy;
- bounded retries;
- no blind retry of uncertain non-idempotent effects.

### Verification
- model completion is not trusted;
- ResultContract enforced;
- evidence generated.

### Observability
- trace all model/tool/capability/approval/recovery/verification events;
- cost and token usage available;
- run replay/debug path exists.

### Security
- secrets redacted;
- approval replay tested;
- prompt injection boundary documented;
- MCP treated as untrusted integration.

---


## 37. v2 vs v3 Boundary

### Keep in v2
- single-agent primary loop;
- optional bounded children;
- dynamic ACI capabilities;
- explicit state;
- authority;
- context engine;
- verification;
- checkpoint/resume;
- policy-based recovery.

### Defer to v3 unless proven necessary
- learned router inside harness;
- large multi-agent organizations;
- distributed DAG scheduler;
- dynamic autonomous agent creation;
- autonomous production capability promotion;
- complex marketplace economics;
- full event-sourced distributed runtime;
- policy learning from model judgments;
- self-modifying runtime kernel.

The v2 interfaces should make these possible later without implementing them now.

---


## 38. Anti-Patterns to Reject

### 38.1 God Kernel

```text
HarnessKernel handles model + state + tools + sandbox + DB + ACI itself
```

Reject.

### 38.2 Manager spaghetti

```text
Planner → Workspace → Recovery → Context → Planner
```

Reject uncontrolled peer-to-peer mutation. Coordinate via RunController/events/contracts.

### 38.3 "Permissions" only in prompt

A sentence like "do not delete files" is not enforcement.

### 38.4 Full context cloning to child

Wasteful and dangerous.

### 38.5 Silent capability updates

Never change skill version in an active run without explicit transition.

### 38.6 Retry everything

Especially dangerous for external mutations.

### 38.7 LLM as security policy

LLM may assist review, but deterministic policy and hard constraints remain authoritative.

### 38.8 Unbounded stdout

Never feed raw unbounded output back to model.

### 38.9 Model declares success

Always gate by contract/verifier when verification is applicable.

### 38.10 MCP equals trusted

Transport does not imply trust.

---


## 39. Recommended ADRs

Create Architecture Decision Records for at least:

1. ACI vs AgentRuntime responsibility boundary.
2. One mutable runtime state authority.
3. ToolRuntime execution order.
4. Authority/approval/sandbox separation.
5. Capability version pinning.
6. Context budget and offloading strategy.
7. Checkpoint schema/version policy.
8. Child authority/budget inheritance.
9. Result verification policy.
10. Runtime server boundary.
11. Model provider abstraction.
12. Telemetry event schema.

---


## 40. Minimal First Vertical Slice

Before building every module, prove this path:

```text
User task
  ↓
Runtime.run
  ↓
Fake/real model
  ↓
read-only tool
  ↓
state update
  ↓
model requests capability
  ↓
stub ACI returns one capability
  ↓
capability activates
  ↓
model finishes
  ↓
deterministic verifier
  ↓
RunResult
```

Then add:

```text
write tool
→ authority
→ sandbox
→ approval
→ checkpoint
→ recovery
→ delegation
```

This prevents architecture-only development with no executable feedback.

---


## 41. Concrete Initial Schemas

### 41.1 RunResult

```json
{
  "run_id": "run_123",
  "status": "SUCCEEDED",
  "stop_reason": "SUCCESS",
  "result": {
    "summary": "...",
    "artifacts": []
  },
  "verification": {
    "verdict": "PASS",
    "evidence_refs": []
  },
  "usage": {
    "turns": 12,
    "model_input_tokens": 45000,
    "model_output_tokens": 9000,
    "tool_calls": 18,
    "cost_usd": 0.84
  },
  "trace_ref": "trace://run_123"
}
```

### 41.2 ToolObservation

```json
{
  "tool_call_id": "tc_123",
  "tool_id": "shell.exec",
  "status": "success",
  "summary": "pytest completed",
  "inline_output": "143 passed...",
  "artifact_ref": "artifact://...",
  "side_effects": {
    "state": "confirmed",
    "resources_changed": []
  },
  "metrics": {
    "duration_ms": 3231
  }
}
```

### 41.3 ApprovalRequest

```json
{
  "approval_id": "apr_123",
  "run_id": "run_123",
  "operation_hash": "...",
  "reason": "Dependency installation requires network access",
  "requested_delta": {
    "network": {
      "enabled": true,
      "allowed_hosts": ["pypi.org", "files.pythonhosted.org"]
    }
  },
  "expires_at": "..."
}
```

### 41.4 FailureEnvelope

```json
{
  "failure_id": "fail_123",
  "class": "CAPABILITY_INSUFFICIENT",
  "severity": "recoverable",
  "retryable": false,
  "side_effect_state": "none",
  "evidence_refs": ["artifact://verification/..."]
}
```

---


## 42. Recommended Review Checklist for Every Pull Request

#### Boundaries
- Is this logic in the correct subsystem?
- Does it duplicate ACI capability intelligence?
- Does it leak provider/workspace details into core?

#### State
- Does it create hidden mutable state?
- Is state update versioned/atomic?
- Can it be checkpointed?

#### Security
- Can model input bypass authority?
- Can capability metadata grant permissions?
- Is least privilege preserved?
- Are secrets redacted?

#### Budget
- Is the operation bounded?
- Does child work reserve parent budget?
- Is retry bounded?

#### Context
- Can this add unbounded prompt content?
- Is offloading supported?

#### Side effects
- Is idempotency known?
- Could retry duplicate an external mutation?
- Is evidence of effects captured?

#### Observability
- Are start/end/failure events emitted?
- Is correlation/run ID preserved?

#### Verification
- How do we know the operation/task succeeded?
- What evidence is produced?

---


## 43. Final Recommended Architecture

```text
                                   USER
                                     │
                                     ▼
                              AgentRuntime API
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         HARNESS KERNEL                              │
│                                                                     │
│  RunController                                                      │
│       │                                                             │
│       ├──────────────► StateManager                                 │
│       ├──────────────► ContextEngine                                │
│       ├──────────────► PlanningStrategy                             │
│       ├──────────────► CapabilityRuntime ───────► ACI v2            │
│       ├──────────────► ModelGateway                                 │
│       ├──────────────► ToolRuntime                                  │
│       │                    │                                        │
│       │                    ├─ GuardrailManager                      │
│       │                    ├─ AuthorityManager                      │
│       │                    └─ WorkspaceManager                      │
│       ├──────────────► DelegationManager                            │
│       ├──────────────► VerificationManager                          │
│       ├──────────────► RecoveryManager                              │
│       └──────────────► CheckpointCoordinator                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
          │                          │                     │
          ▼                          ▼                     ▼
    RuntimeServices             EventBus             Persistent Stores
 Model / Credentials /      traces / metrics     state / checkpoints /
 Sandbox / Clock                                  artifacts / evidence
```

And the system-level boundary remains:

```text
┌─────────────────────────────────────────────┐
│              AGENT HARNESS                  │
│ Think / Plan / Act / Verify                 │
│ Context / Authority / Budget / Recovery     │
└───────────────────┬─────────────────────────┘
                    │ Capability Contract
                    ▼
┌─────────────────────────────────────────────┐
│                 ACI v2                      │
│ Discover / Retrieve / Rank / Judge          │
│ Compose / Quality / Provenance              │
└───────────────────┬─────────────────────────┘
                    │ capability payload/tools
                    ▼
┌─────────────────────────────────────────────┐
│             EXECUTION PLANE                 │
│ Model / Tool / MCP / Workspace / Sandbox    │
│ Remote Services / Credentials               │
└─────────────────────────────────────────────┘
```

---


## 44. Final Design Decisions to Freeze for v2

Freeze these before implementation begins:

1. **ACI is capability intelligence; Harness is execution intelligence.**
2. **StateManager is the only mutable runtime source of truth.**
3. **RunController is the lifecycle coordinator, not a god object.**
4. **ToolRuntime is explicit and owns tool execution sequencing.**
5. **Authority policy, approval, grants, and sandbox enforcement are separate concepts.**
6. **Context is a budgeted resource with mandatory offloading/compaction.**
7. **Planning is adaptive and replaceable, not mandatory for every task.**
8. **Capabilities are dynamically loaded through ACI and pinned by immutable version.**
9. **Child agents inherit a strict subset of authority and reserved parent budget.**
10. **Recovery is failure-classified and bounded.**
11. **Verification—not model self-confidence—gates success.**
12. **Checkpoint/resume captures pending approvals and safe serializable state.**
13. **Telemetry is event-driven and complete enough to replay/debug a run.**
14. **Credentials remain outside prompts/checkpoints and are brokered at execution time.**
15. **v2 stays modular and thin enough that v3 can replace individual strategies without rewriting the kernel.**

---


## 45. Suggested Next Artifacts

After this plan is accepted, the next implementation artifacts should be produced in this order:

1. `contracts.md` — exact schemas and enums.
2. `state-machine.md` — legal transitions and event semantics.
3. `authority-policy.md` — policy language, grants, approvals, sandbox scopes.
4. `tool-runtime.md` — exact execution pipeline and ToolSpec.
5. `context-engine.md` — budget/compaction/offloading algorithms.
6. `aci-runtime-contract.md` — search/resolve/feedback API between harness and ACI.
7. `verification.md` — verifier profiles and evidence schema.
8. `recovery.md` — failure matrix and recovery rules.
9. `test-plan.md` — unit/contract/integration/security/replay/e2e test matrix.
10. `implementation-prompts.md` — one build prompt per vertical phase H0–H12.

The order matters: contracts and state semantics should stabilize before implementation spreads across modules.

---


## Profile Rollout Overlay

The core runtime implementation phases above remain canonical.  
After the generic runtime is proven, specialize profiles in this order:

```text
Coder
↓
Debugger
Researcher
Reviewer
↓
Tester
DevOps/SRE
Data Analyst
Architect
Security Analyst
```

Security is intentionally late because its sandbox, scope, and policy requirements are the strictest.


## Phase 8 — First Four Profiles

Implement:

```text
Coder
Debugger
Researcher
Reviewer
```

Why these first:

```text
Coder       → core execution
Debugger    → hypothesis loop
Researcher  → non-code long-context loop
Reviewer    → evaluation/read-only loop
```

These four exercise most kernel mechanisms.

---


## Phase 9 — Remaining Profiles

Add:

```text
Tester
DevOps
Data Analyst
Architect
Security
```

Security last because its policy/isolation requirements are strongest.

---


## Phase 10 — Benchmark Arena

Build:

```text
ACI-HarnessBench
profile-specific benchmark suites
ablation testing
harness comparisons
```

No runtime change should promote without benchmark evidence.

---


## 54. Recommended Implementation Order Inside Each Phase

For every new subsystem:

```text
1. define contract
2. write deterministic tests
3. implement minimal version
4. instrument telemetry
5. integration test
6. benchmark
7. only then optimize
```

Do not optimize before measurements.

---


## 55. What NOT to Build in Initial V2

Freeze:

```text
recursive multi-agent trees
distributed planner agents
LLM-based permission decisions
automatic production deployment
fully autonomous security actions
global workflow graph engine
complex memory vector store for every run
automatic model-market routing
self-modifying AgentProfiles
online learning directly changing production policy
```

These can be considered after the kernel proves itself.

---


## 61. Definition of Done — Harness V2

Harness V2 is NOT complete because all classes exist.

It is complete when the following are true:

#### Architecture

- [ ] Capability Plane and Runtime Plane remain independent.
- [ ] Global state and local state ownership are explicit.
- [ ] One common HarnessKernel serves multiple profiles.
- [ ] No profile requires a separate framework fork.

#### Runtime

- [ ] Run lifecycle is explicit and tested.
- [ ] Cancellation works.
- [ ] Budgets are enforced.
- [ ] Tool permissions are deterministic.
- [ ] Workspace isolation works.
- [ ] Context remains bounded.
- [ ] Tool output cannot accidentally flood context.
- [ ] Mandatory verification blocks false completion.
- [ ] Recovery terminates rather than looping forever.
- [ ] Checkpoint/resume preserves budgets and does not duplicate actions.
- [ ] Every result is schema-valid.

#### Capability integration

- [ ] Initial bundles come from existing ACI router.
- [ ] Full capabilities load only after selection.
- [ ] Dynamic capability request is bounded.
- [ ] Capability usage is observable.
- [ ] Outcomes can be attributed back to capability versions.

#### Observability

- [ ] Every model turn is traceable.
- [ ] Every tool action is traceable.
- [ ] Every capability load is traceable.
- [ ] Every verifier result is traceable.
- [ ] Cost/tokens/latency are recorded.
- [ ] Stop reason is explicit.

#### Benchmark

- [ ] Minimal harness baseline exists.
- [ ] Harness V2 beats or matches baseline quality.
- [ ] Cost increase is measured.
- [ ] Context growth is measured.
- [ ] Recovery benchmark exists.
- [ ] False acceptance benchmark exists.

---


## 62. Acceptance Criteria for Initial Production Candidate

Recommended production gate:

```text
P0 security issues: 0

permission escape tests:
100% pass

checkpoint idempotency tests:
100% pass

schema validity:
100% pass

mandatory verifier bypass:
0 occurrences

critical benchmark regression:
none beyond agreed threshold

telemetry coverage:
all mandatory lifecycle events present
```

Performance thresholds should be set only after baseline measurements.

---


## 63. Priority Classification

### P0 — mandatory

```text
RunController
StateManager
ContextEngine basic
CapabilityRuntime
AuthorityManager
Workspace isolation
VerificationManager
EvidencePack
ResultContract
Telemetry
```

### P1 — high priority

```text
CheckpointCoordinator
RecoveryManager
structured compaction
dynamic capability refresh
revision flow
```

### P2 — later

```text
child agents
advanced memory
workflow graphs
multi-model ensemble
background tools
dynamic workflows
advisor models
```

---


## 65. ADRs to Create

Recommended ADR set:

```text
ADR-H001 — Global vs Local State Ownership
ADR-H002 — HarnessKernel and AgentProfile Model
ADR-H003 — Four Loop Families
ADR-H004 — CapabilityRuntime Boundary
ADR-H005 — Context Tiering and Artifact Offload
ADR-H006 — Deterministic Authority Model
ADR-H007 — Workspace Isolation
ADR-H008 — Checkpoint and Resume Semantics
ADR-H009 — Verification-First Completion
ADR-H010 — Recovery and No-Progress Policy
ADR-H011 — ResultContract and EvidencePack
ADR-H012 — Non-Interactive Approval Behavior
ADR-H013 — Dynamic Capability Refresh Limits
ADR-H014 — Agent Profile Versioning
ADR-H015 — Delegation Disabled by Default
```

---


## 66. Suggested First Implementation Sprint

Do NOT begin with nine profiles.

Implement this vertical slice:

```text
POST /v1/agent-runs
      ↓
SubtaskContract
      ↓
CoderProfileMinimal
      ↓
existing Capability Router
      ↓
CapabilityBundle
      ↓
RunController
      ↓
ContextEngine basic
      ↓
Workspace
      ↓
model/tool loop
      ↓
code_verifier
      ↓
EvidencePack
      ↓
ResultContract
```

Test task:

```text
small repository bug
explicit failing test
clear acceptance criteria
```

Success condition:

```text
the service fixes it,
runs verification,
returns evidence,
and the client can decide accept/reject
without reading the agent's full transcript.
```

This validates the entire architectural thesis.

---


## 67. Second Implementation Sprint

Add failure paths:

```text
tool failure
verification failure
permission denied
capability insufficient
context oversized
client rejection
```

Build revision flow.

---


## 68. Third Implementation Sprint

Add:

```text
DebuggerProfile
ResearcherProfile
ReviewerProfile
```

These prove that HarnessKernel is genuinely general and not merely a coding harness.

If major kernel rewrites are needed for each new profile, abstraction boundaries are wrong.

---


## 69. Fourth Implementation Sprint

Add:

```text
CheckpointCoordinator
container workspace
long-running benchmark tasks
```

---


## 68. Production Readiness Gates

Before production exposure, require sign-off on:

### Gate A — Correctness
- state transition suite passes;
- checkpoint restore tests pass;
- run/result schemas stable.

### Gate B — Tool safety
- all side-effect tools classified;
- authority pipeline mandatory;
- output limits mandatory.

### Gate C — Sandbox
- isolation escape tests;
- file/network scopes tested;
- kill/timeout tested.

### Gate D — ACI
- manifest signature/hash validation;
- version pinning;
- capability reject/replace flow.

### Gate E — Recovery
- bounded retries;
- idempotency awareness;
- uncertain side-effect handling.

### Gate F — Verification
- success requires defined evidence for target workloads.

### Gate G — Observability
- trace correlation;
- costs;
- stop reason;
- recovery reason;
- approvals.

### Gate H — Security
- secret redaction;
- prompt injection boundary;
- child privilege tests;
- approval replay tests.

---


## 69. Priority Classification

Not all components need equal sophistication immediately.

### P0 — Must be correct before useful execution
- contracts;
- state;
- RunController;
- ToolRuntime;
- AuthorityManager;
- basic workspace;
- budgets;
- cancellation;
- result contract.

### P1 — Needed for strong ACI v2
- ContextEngine;
- CapabilityRuntime;
- VerificationManager;
- RecoveryManager;
- telemetry;
- sandbox backend.

### P2 — Needed for long-running production work
- checkpoint/resume;
- delegation;
- server boundary;
- richer model routing;
- advanced artifact store.

### P3 — Only after evidence of need
- complex child scheduling;
- learned context selection;
- distributed locks;
- sophisticated multi-agent graphs.

This protects v2 from unnecessary complexity while preserving the architecture.

---


## 70. Final Build Principle

When choosing between a clever abstraction and a smaller observable mechanism, prefer:

```text
explicit state
explicit contract
explicit budget
explicit authority
explicit evidence
```

over hidden agent magic.

The harness succeeds when the model can be changed, capabilities can be changed, workspace can be changed, and individual managers can be replaced **without changing the fundamental lifecycle guarantees**.

That is the long-term value of the harness: not merely making an agent "more autonomous", but making autonomy **bounded, inspectable, composable, recoverable, and verifiable**.

---

**Expanded end of `planharness.md`**


# PART IX — APPENDICES AND EXECUTION CHECKLISTS


## Appendix A — Primary Reference Projects

Verified as architecture references during preparation of this plan:

- Strands Agents Harness SDK  
  https://github.com/strands-agents/harness-sdk

- Pydantic AI / Pydantic AI Harness  
  https://github.com/pydantic/pydantic-ai  
  https://github.com/pydantic/pydantic-ai-harness

- Deep Agents  
  https://github.com/langchain-ai/deepagentsjs  
  https://github.com/langchain-ai/langchain-skills

- Microsoft Agent Framework documentation  
  https://learn.microsoft.com/

- OpenAI Agents SDK  
  https://github.com/openai/openai-agents-python  
  https://github.com/openai/openai-agents-js

- LangGraph  
  https://github.com/langchain-ai/langgraph

- OpenHands Software Agent SDK / Agent Server  
  https://github.com/OpenHands/software-agent-sdk  
  https://github.com/OpenHands/docs

- OpenCode  
  https://github.com/anomalyco/opencode

- Gemini CLI  
  https://github.com/google-gemini/gemini-cli

- Aider  
  https://github.com/Aider-AI/aider

- smolagents  
  https://github.com/huggingface/smolagents

---


## Appendix B — Source-to-ACI Quick Map

```text
Strands
→ lifecycle / budgets / hooks / interventions

Pydantic AI Harness
→ composable harness capabilities / context controls

Deep Agents
→ planning / filesystem memory / isolated subagents

OpenAI Agents SDK
→ runner semantics / guardrails / resume ordering

Microsoft Agent Framework
→ checkpoints / HITL / durable workflows

LangGraph
→ persistence / interrupts / resume

OpenHands
→ service runtime / workspace boundary

Codex
→ coding execution policy / sandbox / approvals

OpenCode
→ permission model / read-only planning / subagent restrictions

Gemini CLI
→ sandbox / approval / trusted workspace patterns

Aider
→ repo-map / architect-editor separation

Goose
→ extension / protocol architecture

Mastra
→ observability / storage / operational patterns

Agno
→ runtime/control-plane pattern

smolagents
→ minimality benchmark
```

---


## Appendix C — First 30 Engineering Tasks

1. Create `runtime/contracts/subtask.py`.
2. Create `runtime/contracts/result.py`.
3. Create `runtime/contracts/evidence.py`.
4. Define `AgentProfile`.
5. Define `RunState`.
6. Define `StopReason`.
7. Define `RunBudget`.
8. Define runtime event schema.
9. Implement EventBus.
10. Implement RunController skeleton.
11. Implement fake ModelProvider.
12. Implement fake ToolProvider.
13. Implement deterministic runtime simulation test.
14. Implement StateManager.
15. Implement simple LocalWorkspace.
16. Implement AuthorityManager with ALLOW/ASK/DENY.
17. Implement ToolGrant.
18. Implement VerificationReport.
19. Implement VerificationManager.
20. Implement EvidencePack builder.
21. Implement ResultContract builder.
22. Create `CoderProfileMinimal`.
23. Add existing CapabilityBundle input.
24. Implement CapabilityRuntime loader.
25. Connect CapabilityRuntime to existing object store digest verification.
26. Add agent runtime database migration.
27. Add `POST /v1/agent-runs`.
28. Add `GET /v1/agent-runs/{id}`.
29. Add lifecycle telemetry.
30. Run first end-to-end CoderProfile benchmark.

---


## Appendix D — First Benchmark Pack

Create at least these cases:

```text
H001 simple single-file change
H002 multi-file change
H003 failing test with obvious root cause
H004 failing test with hidden dependency
H005 oversized tool output
H006 permission denied
H007 tool transient failure
H008 verification catches false success
H009 capability insufficient then refresh
H010 no-progress loop
H011 checkpoint crash/resume
H012 client revision after rejection
H013 context compaction long run
H014 model transient failure
H015 workspace mutation rollback
H016 researcher contradictory sources
H017 reviewer false positive suppression
H018 debugger reproduction required
H019 architect read-only enforcement
H020 security scope enforcement
```

For each case record:

```text
success
false success
turns
tool calls
tokens
cost
latency
context peak
retries
replans
capability loads
verification result
```

---


## Appendix E — Core Principle

> **The harness is not the intelligence itself.  
> The harness is the system that makes model intelligence usable, bounded, observable, recoverable, and verifiable.**

For ACI:

> **Capability Intelligence chooses the best reusable knowledge.  
> Harness Intelligence controls how a specialized runtime uses that knowledge.  
> Deterministic verification decides whether the work is actually complete.**


# PART X — UNIFIED FREEZE CHECKLIST

Before implementation is considered architecture-stable, confirm:

## Boundary freeze

- [ ] Global Task DAG has exactly one owner: client orchestrator.
- [ ] Service agent owns exactly one delegated objective.
- [ ] Capability Intelligence is not embedded inside the agent loop.
- [ ] Runtime does not query corpus storage directly.
- [ ] `route_only` and `execute` remain separate surfaces.

## Kernel freeze

- [ ] `RunController` coordinates but is not a god class.
- [ ] `StateManager` is sole mutable state authority.
- [ ] `ToolRuntime` is the only normal side-effect execution path.
- [ ] `AuthorityManager` decides; execution layers enforce.
- [ ] `GuardrailManager` is distinct from authorization.
- [ ] `ContextEngine` has bounded output and artifact offloading.
- [ ] `CapabilityRuntime` pins immutable capability versions per run.
- [ ] `CheckpointCoordinator` cannot serialize secrets/live handles.
- [ ] `RecoveryManager` classifies failures before retrying.
- [ ] `VerificationManager` gates success.

## Profile freeze

- [ ] Nine profiles share one HarnessKernel.
- [ ] Four loop families are sufficient before adding a fifth.
- [ ] New profiles require benchmark evidence of a genuinely different execution pattern.
- [ ] Technology-specific expertise is normally a capability/skill, not a new agent archetype.

## Delegation freeze

- [ ] Local service-side delegation is feature-gated.
- [ ] Disabled by default for initial V2 profiles.
- [ ] Child authority is a subset of parent authority.
- [ ] Child budget is reserved from parent budget.
- [ ] Child context is projected, not cloned.
- [ ] Child result is verified before parent merge.

## Verification freeze

- [ ] Mandatory deterministic checks cannot be bypassed by model output.
- [ ] `FAILED mandatory verifier` can never produce `SUCCESS`.
- [ ] Evidence is artifact-backed where possible.
- [ ] Client acceptance does not require full agent transcript.

## Reproducibility freeze

Every run records:

```text
HarnessKernel version
RuntimeSpec version
AgentProfile version
LoopPolicy version
VerifierProfile version
Model provider/model
Capability versions/digests
Tool versions
Workspace identity/snapshot
Policy/grant versions
```

## Benchmark freeze

No major harness mechanism is promoted based only on intuition.

Required comparison:

```text
same model
same task set
same tools
same environment
same budget
different harness mechanism
```

Measure at minimum:

```text
Task Success Rate
Verified Success Rate
False Acceptance Rate
Pass@1
Recovery Rate
Cost / Verified Success
Tokens / Verified Success
Latency / Verified Success
Context Peak
Tool Calls
Retry Rate
```

---

# FINAL MASTER PRINCIPLE

```text
Open-source projects
        ↓
mechanism mining
        ↓
ACI-native contracts
        ↓
one HarnessKernel
        ↓
four loop families
        ↓
nine AgentProfiles
        ↓
dynamic ACI capabilities
        ↓
deterministic-first verification
        ↓
compact evidence-backed result
```

The system should remain replaceable at every major boundary:

```text
model
workspace
tool provider
capability router
context policy
planning strategy
verification strategy
storage
client orchestrator
```

without rewriting the rest of the runtime.

> **The harness is not the model's intelligence.  
> The harness is the control system that makes that intelligence bounded, composable, observable, recoverable, secure, and verifiable.**
