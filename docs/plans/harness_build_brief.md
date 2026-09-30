# Harness build brief — READ FIRST (shared context for all build subagents)

You are building ONE subsystem of the ACI HarnessKernel (service-side agent
runtime) per `docs/plans/harness.md`. The plan is 8600+ lines — do NOT read it
all. This brief gives you everything you need; read the plan sections it cites
only if your task says so.

## Repo conventions (MANDATORY)

- Python 3.12+, Pydantic v2, `model_config = {"frozen": True}` on all domain
  data. mypy STRICT must pass. ruff (line-length 100, rules E,F,I,UP,B) must
  pass. Tests: pytest, `pythonpath=["src"]` already set.
- Domain (`src/aci/domain/**`) imports NOTHING upward: no application,
  providers, routing, control_plane, adapters, no FastAPI/SQLAlchemy. Boundary
  tests (`tests/unit/test_architecture_boundaries.py`) enforce this — your
  files are auto-covered because they rglob `domain/`.
- Application layer (`src/aci/application/**`) may import domain only; depends
  on Protocols. No adapter imports.
- Errors: `DomainError(code: ErrorCode, message)` from
  `aci.domain.capability.errors`. ADD new codes to `ErrorCode` in
  `src/aci/domain/capability/errors.py` when you need them (StrEnum).
- Tests live in `tests/unit/test_<module>.py`. Use plain fakes, no mocks of
  concrete classes. Deterministic only — NO real LLM/network in unit tests.
- Run checks before finishing:
  `.venv/bin/python -m pytest -q tests/unit/test_<your>_*.py`
  `ruff check src tests && ruff format --check src tests`
  `.venv/bin/python -m mypy src`
- Docstrings: one short line max at module top explaining WHY the module
  exists (which plan section). No comment noise. No emojis.

## H0 contracts ALREADY BUILT (import from `aci.domain.runtime.*`)

Read these files before coding — they are your vocabulary:
- `src/aci/domain/runtime/stop_reason.py` — `RunStatus` (14 states),
  `RUN_TRANSITIONS` (legal edges), `StopReason`, `is_terminal()`.
- `src/aci/domain/runtime/failures.py` — `FailureClass` (22 classes),
  `FailureEnvelope`.
- `src/aci/domain/runtime/authority.py` — `AuthorityDecisionKind`,
  `GrantEnvelope` (filesystem/network/process/secrets scopes),
  `AuthorityRequirement`, `AuthorityDecision`, `ApprovalRequest`,
  `ApprovalDecision`, `ExecutionEnvelope`, `intersect_grants()`.
- `src/aci/domain/runtime/tools.py` — `ToolSpec` (side_effect_class,
  idempotency_class, output_policy, concurrency_safe), `ToolCall`,
  `ToolObservation`, `SideEffectReport`, `OutputPolicy`.
- `src/aci/domain/runtime/evidence.py` — `EvidenceBundle/Item/Pack`,
  `CheckResult`, `VerificationResult`, `ResultContract`, `CandidateResult`.
- `src/aci/domain/runtime/state.py` — `BudgetLedger`, `RunState`, `TaskState`,
  `PlanItem`, `CapabilityActivation`, `RuntimeStateSnapshot`.
- `src/aci/domain/runtime/spec.py` — `RuntimeSpec`, `LoopFamily`,
  `AgentProfileId` (9), `ModelPolicy`, `LoopPolicy`, `PlanningPolicy`,
  `DelegationPolicy` (delegation OFF by default).
- `src/aci/domain/runtime/actions.py` — `ModelAction` union:
  `FinalCandidate | ToolCallBatchAction | CapabilityRequest |
  DelegationRequest | PlanUpdateRequest | ClarificationRequest | ContinueAction`.
- `src/aci/domain/runtime/subtask.py` — `SubtaskContract`,
  `AcceptanceCriterion`, `RunResult`, `RunUsage`.
- `src/aci/domain/runtime/events.py` — `EventEnvelope`.

## Where your code goes

- Domain contracts → `src/aci/domain/runtime/` (pure data).
- Kernel managers → `src/aci/runtime/` (NEW top-level package, sibling of
  `routing/`): `src/aci/runtime/<manager>.py`. These are ENGINE components —
  they may import `aci.domain.runtime` and each other's Protocols but must
  stay importable without FastAPI/SQLAlchemy (unit-testable with fakes).
  Define your manager's Protocol in `src/aci/runtime/protocols.py` (append;
  do not rewrite others' entries).
- Tests → `tests/unit/test_<name>.py`.

## Invariants that gate review (harness.md §4)

- INV-01 StateManager is the only mutable state authority; others read
  snapshots, emit events, return typed results.
- INV-02 child authority ⊆ parent authority. INV-03 child budget carved from
  parent reserve.
- INV-04 AuthorityManager decides; Workspace/ToolRuntime/Sandbox enforce.
- INV-06 no side effect before validation + guardrail + authority + envelope.
- INV-07 model output is untrusted input — validate every action.
- INV-08 completion is verifier-gated, never model-gated.
- INV-09 context is budgeted. INV-10 tool output bounded.
- INV-13 checkpoint only serializable state. INV-14 credentials are
  references, never prompt content. INV-15 every external effect emits
  telemetry.

## Async vs sync

The repo is sync (FastAPI sync endpoints, sync SQLAlchemy). Use SYNC methods
(`def`, not `async def`) throughout the kernel. The plan's `async` sketches
are design targets, not syntax requirements.
