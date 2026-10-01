"""RunController + HarnessKernel (harness.md §5, §7, §18, §59): lifecycle coordinator.

RunController owns turn sequencing, budget checks, cancellation, action
dispatch and recovery application — NOT business logic (§7.2). HarnessKernel
is the composition root (§5): one kernel serves all nine profiles; profiles
are data (RuntimeSpec). ALL run state, the conversation transcript included,
lives in the StateManager (INV-01); every model request is re-assembled from
it within the context budget (INV-09).
"""

import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import ValidationError

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import (
    CapabilityRequest,
    ClarificationRequest,
    DelegationRequest,
    FinalCandidate,
    PlanUpdateRequest,
    ToolCallBatchAction,
)
from aci.domain.runtime.authority import (
    ApprovalDecision,
    AuthorityDecision,
    AuthorityDecisionKind,
    ExecutionEnvelope,
    GrantEnvelope,
)
from aci.domain.runtime.evidence import CandidateResult, EvidenceKind, EvidencePack
from aci.domain.runtime.failures import FailureClass, FailureEnvelope
from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.state import (
    CapabilityActivation,
    PlanItem,
    RuntimeStateSnapshot,
    TaskState,
    TranscriptEntry,
)
from aci.domain.runtime.stop_reason import RUN_TRANSITIONS, RunStatus, StopReason, is_terminal
from aci.domain.runtime.subtask import RunResult, RunUsage, SubtaskContract
from aci.domain.runtime.tools import ToolCall, ToolObservation, ToolSpec
from aci.runtime.cancellation import CancelToken, RunCancelled
from aci.runtime.checkpoints import (
    Checkpoint,
    CheckpointError,
    PendingInterrupt,
    operation_hash,
    validate_for_resume,
)
from aci.runtime.context_engine import AssembledContext, estimate_tokens, select_transcript
from aci.runtime.event_bus import (
    CAPABILITY_LOADED,
    CHECKPOINT_RESTORED,
    CHECKPOINT_SAVED,
    CONTEXT_ASSEMBLED,
    MODEL_REQUEST_COMPLETED,
    MODEL_REQUEST_FAILED,
    MODEL_REQUEST_STARTED,
    RECOVERY_ACTION,
    RUN_CANCELLED,
    RUN_COMPLETED,
    RUN_CREATED,
    RUN_FAILED,
    RUN_PAUSED,
    RUN_RESUMED,
    RUN_STARTED,
    TOOL_APPROVAL_DECIDED,
    TOOL_APPROVAL_REQUESTED,
    TOOL_EXECUTION_COMPLETED,
    TOOL_EXECUTION_FAILED,
    TOOL_REQUESTED,
    TURN_STARTED,
    VERIFICATION_COMPLETED,
    VERIFICATION_STARTED,
    EventBus,
)
from aci.runtime.model_gateway import ModelMessage, ModelRequest, ModelResponse
from aci.runtime.recovery import (
    RETRY_ACTIONS,
    RecoveryAction,
    RecoveryManager,
    classify_tool_failure,
    tool_retry_safe,
)
from aci.runtime.state_manager import (
    StateCommitConflict,
    StateEvent,
    StateManager,
    transcript_event,
)
from aci.runtime.tool_runtime import envelope_expired
from aci.runtime.verification import VerificationManager

#: Per-request completion cap; the run-level output budget still applies.
_MAX_TOKENS_PER_REQUEST = 8_192
_DEFAULT_CONTEXT_TOKENS = 60_000
_MAX_BACKOFF_SECONDS = 8.0

#: Turn-budget signal: a pinned system note once the run is within this many
#: turns of its limit (the current turn included). It tells the model the
#: budget, nothing more — completion stays verifier-gated (INV-08). Public so
#: the H-bench naive arm injects the IDENTICAL note at the identical threshold.
TURN_BUDGET_NOTE_THRESHOLD = 2
TURN_BUDGET_NOTE = (
    "Turn budget: {turns_left} turn(s) left including this one. If your verification "
    "command already passed after your last change, propose completion now."
)


def turn_budget_note(turns_left: int) -> str:
    """The note for ``turns_left`` turns remaining (this one included), or ""
    while the run is not yet near its limit. Deterministic."""
    if turns_left > TURN_BUDGET_NOTE_THRESHOLD:
        return ""
    return TURN_BUDGET_NOTE.format(turns_left=max(1, turns_left))


class ModelGateway(Protocol):
    def invoke(self, request: ModelRequest) -> ModelResponse: ...


class ToolExecutor(Protocol):
    """What RunController needs from ToolRuntime (avoids circular imports).

    ``approved_call_ids`` is passed ONLY when re-executing a call a human
    approved (§13.6 resume); an executor that never sees approvals may omit
    the parameter. An optional ``preflight(call, *, envelope)`` seam lets
    the kernel skip an approval pause for a call authority would DENY."""

    def execute_batch(
        self,
        calls: list[ToolCall],
        *,
        snapshot: RuntimeStateSnapshot,
        envelope: ExecutionEnvelope,
        approved_call_ids: frozenset[str] = ...,
    ) -> list[ToolObservation]: ...

    def available_tools(self) -> list[ToolSpec]: ...


class ContextAssembler(Protocol):
    def build(self, snapshot: RuntimeStateSnapshot, *, turn: int) -> AssembledContext: ...


class CapabilityHandler(Protocol):
    def handle_request(
        self, request: CapabilityRequest, snapshot: RuntimeStateSnapshot
    ) -> list[CapabilityActivation]: ...


RunCancelledError = RunCancelled  # the token raises this; the kernel catches it


@dataclass
class _Usage:
    """Mutable accumulator; frozen RunUsage is built once at finalize."""

    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def to_run_usage(self, wall_time_seconds: float) -> RunUsage:
        return RunUsage(
            turns=self.turns,
            tool_calls=self.tool_calls,
            model_input_tokens=self.input_tokens,
            model_output_tokens=self.output_tokens,
            cost_usd=round(self.cost_usd, 6),
            wall_time_seconds=round(wall_time_seconds, 3),
        )


@dataclass
class _Run:
    """Per-run loop context (the authoritative state stays in StateManager)."""

    run_id: str
    contract: SubtaskContract
    spec: RuntimeSpec
    cancel: CancelToken
    max_turns: int
    started: float
    usage: _Usage = field(default_factory=_Usage)


@dataclass(frozen=True)
class _Gate:
    """A call that needs a human approval before it may execute (§13.6):
    ``remaining`` is the unexecuted rest of the batch, ``call`` first."""

    call: ToolCall
    remaining: list[ToolCall]
    reason: str


@dataclass
class _ToolBatch:
    """One tool batch's outcome, committed atomically by ``_run_tools``."""

    observations: list[ToolObservation] = field(default_factory=list)
    executions: int = 0  # every dispatch attempt, retries included (§7.6)
    recoveries: int = 0  # recovery decisions charged to the budget (§18.4)
    terminal: FailureClass | None = None
    gate: _Gate | None = None  # the batch paused here for an approval


#: ToolObservation.error_class ToolRuntime sets for a REQUIRE_APPROVAL
#: decision — nothing was dispatched; the kernel pauses the run on it.
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
#: Observation code for a call whose approval the delegating client denied.
APPROVAL_REJECTED = "APPROVAL_REJECTED"

#: The synthetic function tool through which the model asks the capability
#: plane for a skill. 2026-10-01 real-model finding (glm-5.3): a model that
#: works through function calls never emits the text-JSON capability_request
#: action, so registry skills never loaded. Offered only when the capability
#: handler is advertised; the kernel routes it to the capability path — it
#: NEVER reaches ToolRuntime/AuthorityManager, needs no grants, and grants
#: nothing (it loads instructions, never tools or permissions). Reserved id.
CAPABILITY_TOOL_ID = "request_capability"
CAPABILITY_TOOL = ToolSpec(
    tool_id=CAPABILITY_TOOL_ID,
    version="1",
    description=(
        "Ask the ACI capability registry for a vetted skill (domain-specific instructions) "
        "matching a need; the matching skill's instructions are added to your context on the "
        "next turn. Call it ALONE (no other tool call in the same message). It never grants "
        "extra tools or permissions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "objective": {
                "type": "string",
                "description": "What the skill should help with, in one sentence.",
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional constraints (language, framework, environment).",
            },
        },
        "required": ["objective"],
    },
    side_effect_class="READ_ONLY",
)
#: Observation code for a request_capability call the kernel did not process.
CAPABILITY_REQUEST_NOT_PROCESSED = "CAPABILITY_REQUEST_NOT_PROCESSED"
#: Observation code for a request_capability call with unusable arguments.
CAPABILITY_ARGUMENTS_INVALID = "CAPABILITY_ARGUMENTS_INVALID"


class HarnessKernel:
    """§5 composition root. Business logic lives in the managers; the kernel
    wires them and RunController sequences the turns (§59 run loop)."""

    def __init__(
        self,
        *,
        state: StateManager,
        model_gateway: ModelGateway,
        tool_executor: ToolExecutor,
        context_engine: ContextAssembler,
        verifier: VerificationManager,
        recovery: RecoveryManager,
        capability_runtime: CapabilityHandler,
        event_bus: EventBus | None = None,
        checkpoints: object | None = None,
        checkpoint_interval_turns: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        approval_required_tools: Iterable[str] = (),
    ) -> None:
        self._state = state
        self._model = model_gateway
        self._tools = tool_executor
        self._context = context_engine
        self._verifier = verifier
        self._recovery = recovery
        self._capabilities = capability_runtime
        # A handler that can never load anything (no capability plane wired)
        # declares advertised=False: the model is then not told about
        # capability_request at all, rather than offered a dead action.
        # A handler without ``handle_request`` cannot load anything either.
        self._capabilities_advertised = bool(
            getattr(capability_runtime, "advertised", True)
        ) and callable(getattr(capability_runtime, "handle_request", None))
        self._events = event_bus or EventBus()
        self._checkpoints = checkpoints
        self._checkpoint_interval = checkpoint_interval_turns
        self._sleep = sleep
        #: §13.6 approval overlay: tool ids whose calls pause the run for a
        #: human decision even when authority ALLOWS them. Narrowing only —
        #: it turns ALLOW into REQUIRE_APPROVAL, never DENY into anything.
        self._approval_tools = frozenset(t for t in approval_required_tools if t)
        #: Pause checkpoints by run id (honest-null RAM copy; the service
        #: persists them durably when a store is wired).
        self._pause_checkpoints: dict[str, Checkpoint] = {}
        self._consumed_checkpoints: set[str] = set()
        budget = getattr(context_engine, "budget", None)
        self._context_tokens: int = getattr(budget, "total_tokens", _DEFAULT_CONTEXT_TOKENS)

    def run(
        self,
        contract: SubtaskContract,
        spec: RuntimeSpec,
        *,
        cancel_token: CancelToken | None = None,
        max_turns: int = 40,
        workspace_id: str | None = None,
    ) -> RunResult:
        """§59 — the deterministic control shape: budget preflight → context →
        model → normalize action → dispatch → commit → loop; FinalCandidate
        goes through verification (INV-08)."""
        r = _Run(
            run_id=contract.task_id,
            contract=contract,
            spec=spec,
            cancel=cancel_token or CancelToken(run_id=contract.task_id),
            max_turns=max_turns,
            started=time.monotonic(),
        )
        self._state.create(
            run_id=r.run_id,
            task=task_state_from(contract),
            budget=spec.budget,
            grants=spec.initial_grants,
            workspace_id=workspace_id,
        )
        self._emit(RUN_CREATED, r.run_id, payload={"objective": contract.objective})
        self._state.transition(r.run_id, RunStatus.INITIALIZING)
        self._state.transition(r.run_id, RunStatus.READY)
        self._state.transition(r.run_id, RunStatus.RUNNING)
        self._emit(RUN_STARTED, r.run_id)
        try:
            result = self._loop(r)
        except RunCancelledError:
            result = self._cancelled(r)
        except Exception as exc:  # noqa: BLE001 — a run never ends stuck in a live state
            result = self._fail_fatal(r, exc)
        self._emit_terminal(result)
        return result

    # -- pause / resume (§13.6, §17.4) -----------------------------------------

    def pending_checkpoint(self, run_id: str) -> Checkpoint | None:
        """The pause checkpoint of a run this kernel interrupted (approval or
        clarification), or None. Self-contained: a FRESH kernel can resume
        from it (the restart case)."""
        return self._pause_checkpoints.get(run_id)

    def resume(
        self,
        checkpoint: Checkpoint,
        *,
        approval: ApprovalDecision | None = None,
        answer: str | None = None,
        cancel_token: CancelToken | None = None,
        grants: GrantEnvelope | None = None,
        workspace_id: str | None = None,
    ) -> RunResult:
        """§17.4 — continue a paused run from its checkpoint, at most once.

        StateManager is restored from the snapshot WITH its version (CAS
        continuity, INV-01); budgets continue from what was consumed (never
        reset). ``approval`` (approval pause): approved → exactly the pending
        calls execute once, the gated one under a one-shot approval that
        never widens grants (INV-02 — authority is still evaluated for every
        call); denied → the model sees a denial observation. ``answer``
        (clarification pause) is appended as the client's answer. ``grants``
        (the CURRENT ceiling, recomputed by the caller) can only narrow the
        checkpointed grants; an expired grant stays expired. ``workspace_id``
        names the re-bound working copy (a restart mints a new id).

        Invalid requests raise DomainError BEFORE anything changes:
        RUN_NOT_RESUMABLE, APPROVAL_REPLAY_INVALID (approval id mismatch /
        no approval pending), CLIENT_INCOMPATIBLE (wrong input kind),
        CHECKPOINT_CONSUMED (second resume)."""
        try:
            restored = validate_for_resume(checkpoint)
        except CheckpointError as exc:
            raise DomainError(ErrorCode.RUN_NOT_RESUMABLE, exc.reason) from exc
        pending, contract, spec = restored.pending, restored.contract, restored.spec
        if pending is None or contract is None or spec is None:  # validated above
            raise DomainError(ErrorCode.RUN_NOT_RESUMABLE, "checkpoint is incomplete")
        check_resume_input(restored, pending, approval, answer)
        consumed = DomainError(
            ErrorCode.CHECKPOINT_CONSUMED,
            f"run {restored.run_id}: this pause was already resumed",
        )
        if restored.checkpoint_id in self._consumed_checkpoints:
            raise consumed
        snapshot = restored.snapshot
        try:
            # Idempotent for a fresh manager or an unchanged run (same version).
            self._state.restore(snapshot)
        except StateCommitConflict as exc:
            raise DomainError(
                ErrorCode.RUN_NOT_RESUMABLE,
                f"run {restored.run_id}: state advanced since the pause — stale checkpoint",
            ) from exc
        if not self._claim(restored.checkpoint_id):
            raise consumed
        self._pause_checkpoints.pop(restored.run_id, None)
        prior = restored.usage or RunUsage()
        r = _Run(
            run_id=restored.run_id,
            contract=contract,
            spec=spec,
            cancel=cancel_token or CancelToken(run_id=restored.run_id),
            max_turns=restored.max_turns or 40,
            # Wall time continues from the paused value: the time spent
            # waiting for the client is not charged, nothing is refunded.
            started=time.monotonic() - snapshot.budget.consumed_wall_time_seconds,
            usage=_Usage(
                turns=prior.turns,
                tool_calls=prior.tool_calls,
                input_tokens=prior.model_input_tokens,
                output_tokens=prior.model_output_tokens,
                cost_usd=prior.cost_usd,
            ),
        )
        if grants is not None:
            self._state.narrow_grants(r.run_id, grants)
        if workspace_id is not None:
            # The workspace was re-bound (a new process mints a new id for
            # the SAME working copy); the binding itself is the caller's.
            self._state.set_workspace(r.run_id, workspace_id)
        self._emit(
            CHECKPOINT_RESTORED,
            r.run_id,
            payload={
                "checkpoint_id": restored.checkpoint_id,
                "state_version": snapshot.run.version,
            },
        )
        self._state.transition(r.run_id, RunStatus.RUNNING)
        self._emit(RUN_RESUMED, r.run_id, payload={"kind": pending.kind})
        try:
            result = self._resume_pending(r, pending, approval, answer)
            if result is None:
                result = self._loop(r)
        except RunCancelledError:
            result = self._cancelled(r)
        except Exception as exc:  # noqa: BLE001 — a run never ends stuck in a live state
            result = self._fail_fatal(r, exc)
        self._emit_terminal(result)
        return result

    def _claim(self, checkpoint_id: str) -> bool:
        """At most one resume per checkpoint: this kernel's own record, plus
        the wired coordinator/store's when it can claim (``consume``)."""
        if checkpoint_id in self._consumed_checkpoints:
            return False
        consume = getattr(self._checkpoints, "consume", None)
        if consume is not None and not consume(checkpoint_id):
            return False
        self._consumed_checkpoints.add(checkpoint_id)
        return True

    def _resume_pending(
        self,
        r: _Run,
        pending: PendingInterrupt,
        approval: ApprovalDecision | None,
        answer: str | None,
    ) -> RunResult | None:
        if pending.kind == "clarification":
            self._state.append_transcript(
                r.run_id,
                [
                    TranscriptEntry(
                        role="user",
                        content=_CLARIFICATION_ANSWER.format(answer=answer or ""),
                        turn=pending.turn,
                    )
                ],
            )
            return None
        if approval is None or pending.gated_call_id is None:
            raise DomainError(ErrorCode.RUN_NOT_RESUMABLE, "approval resume without a gated call")
        gated = pending.calls[0]
        self._emit(
            TOOL_APPROVAL_DECIDED,
            r.run_id,
            payload={
                "approval_id": pending.approval_id,
                "approved": approval.approved,
                "tool_id": gated.tool_id,
            },
            turn_id=f"turn-{pending.turn}",
        )
        pre = self._state.snapshot(r.run_id)
        if not approval.approved:
            denials = [
                _approval_denied(call, gated=call.call_id == gated.call_id)
                for call in pending.calls
            ]
            return self._commit_batch(r, pre, pending.turn, [], _ToolBatch(observations=denials))
        stop = _tool_call_stop(pre, len(pending.calls))
        if stop is not None:
            return self._fail(r, stop)
        envelope = _envelope(pre)
        if envelope_expired(envelope):
            # Expired grants stay expired: a resume never renews authority.
            return self._fail(r, StopReason.AUTHORITY_DENIED, detail="AUTHORITY_EXPIRED")
        return self._execute_and_commit(
            r,
            list(pending.calls),
            pre,
            envelope,
            pending.turn,
            lead=[],
            approved=frozenset({pending.gated_call_id}),
        )

    def _interrupt_for_approval(self, r: _Run, gate: _Gate, turn: int) -> RunResult:
        """§13.6 — the run pauses BEFORE the gated call: everything executed
        so far is already committed; the checkpoint holds the full snapshot
        plus the unexecuted rest of the batch, bound to a fresh approval id.
        Telemetry carries tool ids only — never arguments or paths."""
        approval_id = f"apr_{uuid.uuid4().hex[:16]}"
        pending = PendingInterrupt(
            kind="approval",
            turn=turn,
            approval_id=approval_id,
            gated_call_id=gate.call.call_id,
            operation_hash=operation_hash(gate.call),
            calls=list(gate.remaining),
            reason=gate.reason,
        )
        self._state.transition(
            r.run_id,
            RunStatus.INTERRUPTED_APPROVAL,
            stop_reason=StopReason.AWAITING_APPROVAL,
            detail_code=APPROVAL_REQUIRED,
        )
        self._emit(
            TOOL_APPROVAL_REQUESTED,
            r.run_id,
            payload={
                "approval_id": approval_id,
                "tool_id": gate.call.tool_id,
                "pending_tool_ids": [c.tool_id for c in gate.remaining],
            },
            turn_id=f"turn-{turn}",
        )
        self._save_pause_checkpoint(r, pending)
        return self._finalize(
            r,
            StopReason.AWAITING_APPROVAL,
            summary=f"approval required for tool {gate.call.tool_id}",
            approval_id=approval_id,
        )

    def _save_pause_checkpoint(self, r: _Run, pending: PendingInterrupt) -> Checkpoint:
        """§17.2 "before human approval interrupt" — always taken (RAM, the
        honest-null); also handed to a wired coordinator that can ``record``."""
        snapshot = self._state.snapshot(r.run_id)
        checkpoint = Checkpoint(
            checkpoint_id=f"chk-{r.run_id}-{uuid.uuid4().hex[:12]}",
            run_id=r.run_id,
            state_version=snapshot.run.version,
            turn=snapshot.run.current_turn,
            snapshot=snapshot,
            pending_approval_id=pending.approval_id,
            pending=pending,
            contract=r.contract,
            spec=r.spec,
            max_turns=r.max_turns,
            usage=r.usage.to_run_usage(time.monotonic() - r.started),
        )
        validate_for_resume(checkpoint)  # INV-13: refuse a checkpoint that cannot resume
        self._pause_checkpoints[r.run_id] = checkpoint
        record = getattr(self._checkpoints, "record", None)
        if record is not None:
            record(checkpoint)
        self._emit(
            CHECKPOINT_SAVED,
            r.run_id,
            payload={
                "turn": checkpoint.turn,
                "checkpoint_id": checkpoint.checkpoint_id,
                "reason": pending.kind,
            },
        )
        return checkpoint

    # -- turn loop --------------------------------------------------------------

    def _loop(self, r: _Run) -> RunResult:
        while True:
            r.cancel.raise_if_cancelled()
            self._sync_wall_time(r)
            snapshot = self._state.snapshot(r.run_id)
            stop = self._budget_stop_reason(snapshot)
            if stop is None and snapshot.run.current_turn >= r.max_turns:
                stop = StopReason.LIMIT_TURNS
            if stop is not None:
                if stop is StopReason.LIMIT_TURNS:
                    return self._fail(r, stop, evidence=self._at_limit_evidence(r))
                return self._fail(r, stop)
            turn = snapshot.run.current_turn + 1
            self._state.advance_turn(r.run_id)
            r.usage.turns += 1
            self._emit(TURN_STARTED, r.run_id, turn_id=f"turn-{turn}")
            self._maybe_checkpoint(r.run_id, turn)
            # Context assembly stays OUTSIDE the model try-block: a context
            # failure is FATAL_ERROR, not a misreported MODEL_FAILURE.
            request = self._build_model_request(self._state.snapshot(r.run_id), r, turn)
            outcome = self._call_model(r, request, turn)
            if isinstance(outcome, RunResult):
                return outcome
            if outcome is None:
                continue  # a repair turn was scheduled (§18.2 REPAIR_OUTPUT)
            result = self._dispatch(r, outcome, turn)
            if result is not None:
                return result

    def _dispatch(self, r: _Run, response: ModelResponse, turn: int) -> RunResult | None:
        action = response.action
        raw = response.raw_text
        if isinstance(action, ToolCallBatchAction):
            return self._run_tools(r, action, raw, turn)
        if isinstance(action, FinalCandidate):
            return self._verify_and_finish(r, action, raw, turn)
        if isinstance(action, CapabilityRequest):
            return self._handle_capability_request(r, action, raw, turn)
        if isinstance(action, DelegationRequest):
            # §0.3: delegation OFF by default — the request fails closed.
            return self._fail(r, StopReason.AUTHORITY_DENIED, detail="DELEGATION_DISABLED")
        if isinstance(action, PlanUpdateRequest):
            self._record_plan(r, action, raw, turn)
            return None
        if isinstance(action, ClarificationRequest):
            return self._interrupt_for_clarification(r, action, raw, turn)
        # ContinueAction: the model produced no action — keep its text (it
        # may be reasoning) and ask for progress, so the next request differs.
        self._append(r, turn, assistant=raw, user=_CONTINUE_PROMPT)
        return None

    # -- model call + recovery (§18) -------------------------------------------

    def _call_model(
        self, r: _Run, request: ModelRequest, turn: int
    ) -> ModelResponse | RunResult | None:
        """Invoke the gateway; classified failures go through RecoveryManager:
        transient → bounded retry with backoff, malformed → repair turn,
        anything else → terminal. Returns the response, a terminal result, or
        None when a repair turn was appended."""
        attempt = 0
        while True:
            try:
                response = self._invoke_model(r.run_id, request)
            except RunCancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — model failures are classified, not crashes
                failure_class = _classify_model_error(exc)
                self._emit(
                    MODEL_REQUEST_FAILED,
                    r.run_id,
                    payload={"failure_class": failure_class.value},
                    turn_id=f"turn-{turn}",
                )
                decision = self._decide(
                    r, _failure(failure_class, "model_gateway", [_error_text(exc)])
                )
                if decision is None:
                    return self._fail(r, StopReason.MODEL_FAILURE, detail=failure_class.value)
                if decision.action in ("RETRY_BACKOFF", "RETRY_SAME"):
                    self._sleep(min(_MAX_BACKOFF_SECONDS, float(2**attempt)))
                    attempt += 1
                    r.cancel.raise_if_cancelled()
                    continue
                if decision.action == "REPAIR_OUTPUT":
                    self._state.append_transcript(
                        r.run_id,
                        [
                            TranscriptEntry(
                                role="user",
                                content=_REPAIR_PROMPT.format(error=_error_text(exc)),
                                turn=turn,
                            )
                        ],
                    )
                    return None
                return self._fail(r, StopReason.MODEL_FAILURE, detail=failure_class.value)
            # §7.6: consume usage as soon as it is known.
            usage = response.usage
            self._state.consume_budget(
                r.run_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
            )
            r.usage.input_tokens += usage.input_tokens
            r.usage.output_tokens += usage.output_tokens
            r.usage.cost_usd += usage.cost_usd
            return response

    def _invoke_model(self, run_id: str, request: ModelRequest) -> ModelResponse:
        self._emit(MODEL_REQUEST_STARTED, run_id)
        response = self._model.invoke(request)
        self._emit(
            MODEL_REQUEST_COMPLETED,
            run_id,
            payload={"action_type": type(response.action).__name__},
        )
        return response

    def _decide(self, r: _Run, failure: FailureEnvelope) -> RecoveryAction | None:
        """§18.4: recovery consumes budget; None means terminal."""
        action = self._recovery.decide(failure)
        snapshot = self._state.count_recovery(r.run_id)
        return self._gate(
            r,
            failure,
            action,
            consumed=snapshot.budget.consumed_recoveries,
            maximum=snapshot.budget.max_recoveries,
        )

    def _gate(
        self,
        r: _Run,
        failure: FailureEnvelope,
        action: RecoveryAction,
        *,
        consumed: int,
        maximum: int,
        extra: dict[str, object] | None = None,
    ) -> RecoveryAction | None:
        """Apply the §18.4 recovery budget to a decision and emit it. A
        decision past the budget is recorded as the FAIL it becomes, so a
        RECOVERY_ACTION event never claims a recovery that was not taken."""
        if consumed > maximum and not action.is_terminal:
            action = RecoveryAction("FAIL", "recovery budget exhausted")
        self._emit(
            RECOVERY_ACTION,
            r.run_id,
            payload={
                "failure_class": failure.failure_class.value,
                "action": action.action,
                "component": failure.component,
                **(extra or {}),
            },
        )
        return None if action.is_terminal else action

    # -- tools (§12, §59 STATE_COMMIT) -----------------------------------------

    def _run_tools(
        self, r: _Run, action: ToolCallBatchAction, raw: str, turn: int
    ) -> RunResult | None:
        """A function-call batch. ``request_capability`` calls never reach
        ToolRuntime: a batch of ONLY such calls goes to the capability path;
        in a MIXED batch the real tools execute normally and every
        request_capability call is answered "not processed — call it alone"
        (deterministic, no refresh budget spent, one CAS commit). Every
        tool_call id gets a role="tool" result (OpenAI wire validity)."""
        capability_calls = [c for c in action.calls if self._is_capability_call(c)]
        if capability_calls and len(capability_calls) == len(action.calls):
            return self._capability_tool_call(r, action, raw, turn)
        calls = [c for c in action.calls if not self._is_capability_call(c)]
        self._emit(
            TOOL_REQUESTED,
            r.run_id,
            payload={"calls": [c.tool_id for c in calls]},
            turn_id=f"turn-{turn}",
        )
        # §7.6: the model call just consumed budget, and the batch must fit whole.
        pre = self._state.snapshot(r.run_id)
        stop = self._budget_stop_reason(pre) or _tool_call_stop(pre, len(calls))
        if stop is not None:
            return self._fail(r, stop)
        envelope = _envelope(pre)
        if envelope_expired(envelope):
            # Grants never renew mid-run: every further call would be denied.
            return self._fail(r, StopReason.AUTHORITY_DENIED, detail="AUTHORITY_EXPIRED")
        assistant = TranscriptEntry(
            role="assistant", content=raw, tool_calls=list(action.calls), turn=turn
        )
        return self._execute_and_commit(
            r,
            calls,
            pre,
            envelope,
            turn,
            lead=[assistant],
            approved=frozenset(),
            answered=[
                _capability_not_processed(c, _CAPABILITY_NOT_ALONE) for c in capability_calls
            ],
        )

    def _is_capability_call(self, call: ToolCall) -> bool:
        return self._capabilities_advertised and call.tool_id == CAPABILITY_TOOL_ID

    def _execute_and_commit(
        self,
        r: _Run,
        calls: list[ToolCall],
        pre: RuntimeStateSnapshot,
        envelope: ExecutionEnvelope,
        turn: int,
        *,
        lead: list[TranscriptEntry],
        approved: frozenset[str],
        answered: list[ToolObservation] | None = None,
    ) -> RunResult | None:
        """``answered``: results for calls the kernel answered WITHOUT
        executing (request_capability in a mixed batch) — committed with the
        batch, so every tool_call id of the assistant entry gets its result."""
        batch = self._execute_with_recovery(r, calls, pre, envelope, turn, approved)
        batch.observations.extend(answered or [])
        return self._commit_batch(r, pre, turn, lead, batch)

    def _commit_batch(
        self,
        r: _Run,
        pre: RuntimeStateSnapshot,
        turn: int,
        lead: list[TranscriptEntry],
        batch: _ToolBatch,
    ) -> RunResult | None:
        """One CAS commit for a batch (or its executed prefix, when it paused
        for an approval): observations, tool-call and recovery charges,
        progress, confirmed effects and the transcript (§59 STATE_COMMIT)."""
        observations = batch.observations
        entries = [
            *lead,
            *(
                TranscriptEntry(
                    role="tool",
                    content=_render_observation(o),
                    tool_call_id=o.tool_call_id,
                    turn=turn,
                )
                for o in observations
            ),
        ]
        # §8.5 CAS: nothing else may have written this run's state while the
        # tools executed — a conflict is a bug, never a silent overwrite.
        self._state.commit(
            r.run_id,
            expected_version=pre.run.version,
            events=[
                StateEvent(event_type="budget.consumed", payload={"tool_calls": batch.executions}),
                # §18.4: tool recoveries are charged in the same atomic commit.
                *(StateEvent(event_type="recovery.counted") for _ in range(batch.recoveries)),
                StateEvent(
                    event_type="task.progress",
                    payload={"append": [f"turn {turn}: {len(observations)} tool observation(s)"]},
                ),
                # INV-08 evidence: only effects the tool path confirmed.
                StateEvent(
                    event_type="tool.observed",
                    payload={
                        "resources": [
                            res
                            for o in observations
                            if o.status == "success" and o.side_effects.state == "confirmed"
                            for res in o.side_effects.resources_changed
                        ],
                        "evidence": [
                            e.model_dump(mode="json") for o in observations for e in o.evidence
                        ],
                    },
                ),
                transcript_event(entries),
            ],
        )
        r.usage.tool_calls += batch.executions
        # §23.1: cancellation between tools — a cancelled run never starts the
        # next batch, but the observations it already produced are committed.
        r.cancel.raise_if_cancelled()
        if batch.terminal is not None:
            # §18.4: recovery exhausted (or terminal by policy) — the run ends
            # through the stop-reason path, the observations stay committed.
            return self._fail(r, StopReason.TOOL_FAILURE, detail=batch.terminal.value)
        if batch.gate is not None:
            return self._interrupt_for_approval(r, batch.gate, turn)
        return None

    def _approval_gate(self, call: ToolCall, envelope: ExecutionEnvelope) -> str | None:
        """The approval overlay (§13.6): a call of an approval-required tool
        pauses the run — unless authority would refuse it anyway (preflight
        DENY, or a failure before authority): a human is never asked to
        approve what cannot execute, and approval never rescues a DENY."""
        if call.tool_id not in self._approval_tools:
            return None
        preflight = getattr(self._tools, "preflight", None)
        if preflight is not None:
            decision: AuthorityDecision | None = preflight(call, envelope=envelope)
            if decision is None or decision.kind is AuthorityDecisionKind.DENY:
                return None
        return f"tool {call.tool_id} requires approval on this run"

    def _execute_with_recovery(
        self,
        r: _Run,
        calls: list[ToolCall],
        pre: RuntimeStateSnapshot,
        envelope: ExecutionEnvelope,
        turn: int,
        approved: frozenset[str] = frozenset(),
    ) -> _ToolBatch:
        """§12 + §18: execute calls in order; an infrastructure failure
        (``classify_tool_failure``) goes through the RecoveryManager.
        A retry-safe tool (read-only + idempotent) is re-run with backoff;
        any other tool's failure is surfaced to the model, never re-run
        (§12.6 retry-block). Deterministic failures, denials and unknown
        tools are plain observations. NO state is written here — the
        caller commits observations, tool calls and recovery charges in one
        CAS commit (INV-01, §8.5).

        §13.6: a call that needs approval (the overlay, or a REQUIRE_APPROVAL
        authority decision) and is not in ``approved`` stops the batch
        BEFORE it executes — ``batch.gate`` holds it plus the rest."""
        specs = {t.tool_id: t for t in self._advertised_tools()}
        batch = _ToolBatch()
        for index, call in enumerate(calls):
            remaining_after = len(calls) - index - 1
            is_approved = call.call_id in approved
            if not is_approved:
                reason = self._approval_gate(call, envelope)
                if reason is not None:
                    batch.gate = _Gate(call=call, remaining=list(calls[index:]), reason=reason)
                    return batch
            attempt = 0
            while True:
                obs = self._execute_one(r, call, pre, envelope, turn, batch, approved=is_approved)
                if obs.error_class == APPROVAL_REQUIRED and not is_approved:
                    batch.gate = _Gate(call=call, remaining=list(calls[index:]), reason=obs.summary)
                    return batch
                failure_class = classify_tool_failure(obs)
                if failure_class is None:
                    break
                tool = specs.get(call.tool_id)
                batch.recoveries += 1
                failure = FailureEnvelope(
                    failure_id=f"fail-{uuid.uuid4().hex[:12]}",
                    failure_class=failure_class,
                    component="tool_runtime",
                    message=obs.error_class or failure_class.value,
                    retryable=tool_retry_safe(tool),
                    side_effect_state=obs.side_effects.state,
                )
                decision = self._gate(
                    r,
                    failure,
                    self._recovery.decide_tool(failure, tool=tool),
                    consumed=pre.budget.consumed_recoveries + batch.recoveries,
                    maximum=pre.budget.max_recoveries,
                    extra={"tool_id": call.tool_id, "attempt": attempt + 1},
                )
                if decision is None:
                    batch.observations.append(obs)
                    batch.terminal = failure_class
                    return batch  # no further effects in a terminating run
                fits = (
                    pre.budget.consumed_tool_calls + batch.executions + 1 + remaining_after
                    <= pre.budget.max_tool_calls
                )
                if decision.action not in RETRY_ACTIONS or not fits or r.cancel.cancelled:
                    if decision.action not in RETRY_ACTIONS:
                        obs = obs.model_copy(
                            update={"summary": f"{obs.summary} — {_NOT_RETRIED_NOTE}"}
                        )
                    break
                self._sleep(min(_MAX_BACKOFF_SECONDS, float(2**attempt)))
                attempt += 1
                if r.cancel.cancelled:
                    break
            batch.observations.append(obs)
        return batch

    def _execute_one(
        self,
        r: _Run,
        call: ToolCall,
        pre: RuntimeStateSnapshot,
        envelope: ExecutionEnvelope,
        turn: int,
        batch: _ToolBatch,
        *,
        approved: bool = False,
    ) -> ToolObservation:
        """One execution through ToolRuntime (INV-06) + its telemetry
        (INV-15 — every execution, a retried attempt included). An
        APPROVAL_REQUIRED refusal dispatched nothing: not charged, and the
        caller pauses the run on it."""
        if approved:
            observations = self._tools.execute_batch(
                [call],
                snapshot=pre,
                envelope=envelope,
                approved_call_ids=frozenset({call.call_id}),
            )
        else:
            observations = self._tools.execute_batch([call], snapshot=pre, envelope=envelope)
        obs = observations[0]
        if obs.error_class == APPROVAL_REQUIRED and not approved:
            return obs
        batch.executions += 1
        self._emit(
            TOOL_EXECUTION_COMPLETED if obs.status == "success" else TOOL_EXECUTION_FAILED,
            r.run_id,
            payload={"tool_id": obs.tool_id, "status": obs.status},
            turn_id=f"turn-{turn}",
        )
        return obs

    # -- completion (§19.6, INV-08) --------------------------------------------

    def _verify_and_finish(
        self, r: _Run, candidate_action: FinalCandidate, raw: str, turn: int
    ) -> RunResult | None:
        candidate = CandidateResult(
            summary=candidate_action.summary,
            changes=candidate_action.changes,
            artifacts=candidate_action.artifacts,
            claims=candidate_action.claims,
            criteria_addressed=candidate_action.criteria_addressed,
        )
        self._state.transition(r.run_id, RunStatus.VERIFYING)
        self._emit(VERIFICATION_STARTED, r.run_id)
        verification = self._verifier.verify(
            candidate, snapshot=self._state.snapshot(r.run_id), contract=r.spec.result_contract
        )
        self._emit(VERIFICATION_COMPLETED, r.run_id, payload={"verdict": verification.verdict})
        pack = VerificationManager.to_pack(verification)
        if verification.verdict == "PASS":
            self._state.transition(r.run_id, RunStatus.SUCCEEDED, stop_reason=StopReason.SUCCESS)
            return self._finalize(
                r,
                StopReason.SUCCESS,
                evidence=pack,
                summary=candidate.summary,
                artifacts=candidate.artifacts,
            )
        # INV-08: model said done, verifier disagreed → recovery, never success.
        decision = self._decide(
            r, _failure(FailureClass.VERIFICATION_FAILED, "verifier", verification.repair_hints)
        )
        if decision is None:
            self._state.transition(
                r.run_id, RunStatus.FAILED, stop_reason=StopReason.VERIFICATION_FAILED
            )
            return self._finalize(
                r,
                StopReason.VERIFICATION_FAILED,
                evidence=pack,
                summary="; ".join(verification.repair_hints[:3]),
            )
        # §19.6 "no → RECOVERING": the model gets the verifier's evidence back.
        self._state.transition(r.run_id, RunStatus.RECOVERING)
        self._state.transition(r.run_id, RunStatus.RUNNING)
        hints = "\n".join(f"- {h}" for h in verification.repair_hints[:10])
        self._append(
            r,
            turn,
            assistant=raw or candidate_action.model_dump_json(),
            user=_VERIFICATION_FEEDBACK.format(hints=hints),
        )
        return None

    # -- other actions ---------------------------------------------------------

    def _handle_capability_request(
        self, r: _Run, request: CapabilityRequest, raw: str, turn: int
    ) -> RunResult | None:
        """The text-JSON capability_request action (§47.1)."""
        pre = self._state.snapshot(r.run_id)
        outcome = self._acquire_capabilities(r, request, pre)
        if isinstance(outcome, RunResult):
            return outcome
        content = raw if raw.strip() else request.model_dump_json()
        entries = [
            TranscriptEntry(role="assistant", content=content, turn=turn),
            TranscriptEntry(role="user", content=_capability_note(outcome), turn=turn),
        ]
        self._commit_capabilities(r, pre, outcome, entries)
        return None

    def _capability_tool_call(
        self, r: _Run, action: ToolCallBatchAction, raw: str, turn: int
    ) -> RunResult | None:
        """``request_capability`` as a function call (a batch of only such
        calls): the FIRST is processed exactly like the text-JSON action
        (same handler, same failure handling, same result note); any further
        one is answered "not processed — one per message". Unusable arguments
        are answered as such without spending the refresh budget. One CAS
        commit: activations + the assistant entry carrying the tool_calls +
        one role="tool" result per tool_call id (wire-valid transcript)."""
        first, *rest = action.calls
        pre = self._state.snapshot(r.run_id)
        request = _capability_request_from(first.arguments)
        activations: list[CapabilityActivation] = []
        if request is None:
            result_text = _render_observation(
                _capability_not_processed(
                    first, _CAPABILITY_ARGS_HINT, CAPABILITY_ARGUMENTS_INVALID
                )
            )
        else:
            outcome = self._acquire_capabilities(r, request, pre)
            if isinstance(outcome, RunResult):
                return outcome
            activations = outcome
            result_text = _capability_note(activations)
        entries = [
            TranscriptEntry(
                role="assistant", content=raw, tool_calls=list(action.calls), turn=turn
            ),
            TranscriptEntry(
                role="tool", content=result_text, tool_call_id=first.call_id, turn=turn
            ),
            *(
                TranscriptEntry(
                    role="tool",
                    content=_render_observation(
                        _capability_not_processed(c, _CAPABILITY_ONE_PER_MESSAGE)
                    ),
                    tool_call_id=c.call_id,
                    turn=turn,
                )
                for c in rest
            ),
        ]
        self._commit_capabilities(r, pre, activations, entries)
        return None

    def _acquire_capabilities(
        self, r: _Run, request: CapabilityRequest, snapshot: RuntimeStateSnapshot
    ) -> list[CapabilityActivation] | RunResult:
        """CapabilityRuntime.handle_request (refresh budget + max_loaded
        apply there); a failure ends the run CAPABILITY_UNAVAILABLE with the
        exception TYPE only as detail. Writes no state."""
        try:
            return list(self._capabilities.handle_request(request, snapshot))
        except Exception as exc:  # noqa: BLE001 — capability gaps are failures, not crashes
            return self._fail(r, StopReason.CAPABILITY_UNAVAILABLE, detail=type(exc).__name__)

    def _commit_capabilities(
        self,
        r: _Run,
        pre: RuntimeStateSnapshot,
        activations: list[CapabilityActivation],
        entries: list[TranscriptEntry],
    ) -> None:
        """One CAS commit (§8.5): the activations (StateManager-owned,
        INV-01) + the transcript of this turn; CAPABILITY_LOADED after it."""
        self._state.commit(
            r.run_id,
            expected_version=pre.run.version,
            events=[
                *(
                    StateEvent(
                        event_type="capability.activated",
                        payload={"activation": a.model_dump(mode="json")},
                    )
                    for a in activations
                ),
                transcript_event(entries),
            ],
        )
        for activation in activations:
            self._emit(
                CAPABILITY_LOADED,
                r.run_id,
                payload={"capability_id": activation.capability_id, "version": activation.version},
            )

    def _record_plan(self, r: _Run, action: PlanUpdateRequest, raw: str, turn: int) -> None:
        """§10.3 — the planner proposes, StateManager commits; the plan then
        rides in every turn's context (ContextEngine pins active items)."""
        items = [
            PlanItem(item_id=f"p{i + 1}", objective=_plan_objective(d), status=_plan_status(d))
            for i, d in enumerate(action.items)
        ]
        self._state.set_plan(r.run_id, items)
        self._append(
            r,
            turn,
            assistant=raw or action.model_dump_json(),
            user=f"Plan recorded ({len(items)} item(s)); it stays in your context. Continue.",
        )

    def _interrupt_for_clarification(
        self, r: _Run, action: ClarificationRequest, raw: str, turn: int
    ) -> RunResult:
        """§7.5 resumable pause: the delegating client owns the dialogue
        (§0.3), so the run stops and surfaces the question. A pause
        checkpoint is saved: the client's answer RESUMES this run (same
        state, same budget) — a revision remains the other option."""
        self._append(r, turn, assistant=raw or action.model_dump_json(), user=None)
        self._state.transition(
            r.run_id,
            RunStatus.INTERRUPTED,
            stop_reason=StopReason.INTERRUPTED,
            detail_code="CLARIFICATION_REQUIRED",
        )
        self._save_pause_checkpoint(
            r, PendingInterrupt(kind="clarification", turn=turn, question=action.question)
        )
        return self._finalize(
            r, StopReason.INTERRUPTED, summary=f"clarification required: {action.question}"
        )

    # -- context assembly (§9) --------------------------------------------------

    def _build_model_request(
        self, snapshot: RuntimeStateSnapshot, r: _Run, turn: int
    ) -> ModelRequest:
        """§20.1 — pinned seed (identity, objective, constraints, client
        context, ContextEngine items, harness-observed progress) + the newest
        transcript groups that fit the remaining context budget."""
        assembled = self._context.build(snapshot, turn=turn)
        system = [_system_prompt(snapshot, r.spec, capabilities=self._capabilities_advertised)]
        if r.contract.constraints:
            system.append("Constraints: " + "; ".join(r.contract.constraints))
        if r.contract.global_context:
            system.append("Context from the delegating client:\n" + r.contract.global_context)
        system.extend(item.content for item in assembled.items if item.content)
        progress = _progress_summary(snapshot)
        if progress:
            system.append(progress)
        # Turns left INCLUDING this one, against whichever ceiling binds
        # first: the run's max_turns or the budget ledger (both already count
        # this turn — advance_turn ran before the request is built).
        budget = snapshot.budget
        turns_left = (
            min(r.max_turns - snapshot.run.current_turn, budget.max_turns - budget.consumed_turns)
            + 1
        )
        note = turn_budget_note(turns_left)
        if note:
            system.append(note)
        seed = [ModelMessage(role="system", content=c) for c in system]
        seed.append(ModelMessage(role="user", content=r.contract.objective))
        seed_tokens = sum(estimate_tokens(m.content) for m in seed)
        kept, dropped_turns = select_transcript(
            snapshot.transcript, max(0, self._context_tokens - seed_tokens)
        )
        messages = list(seed)
        if dropped_turns:
            messages.append(
                ModelMessage(
                    role="user",
                    content=(
                        f"[context] {dropped_turns} earlier turn(s) were omitted to fit the "
                        "context budget; the progress summary above reflects the current state."
                    ),
                )
            )
        messages.extend(_to_message(e) for e in kept)
        self._emit(
            CONTEXT_ASSEMBLED,
            r.run_id,
            payload={
                "total_tokens": seed_tokens + sum(estimate_tokens(e.content) for e in kept),
                "dropped": len(assembled.dropped_item_ids),
                "dropped_turns": dropped_turns,
            },
            turn_id=f"turn-{turn}",
        )
        remaining_output = (
            snapshot.budget.max_output_tokens - snapshot.budget.consumed_output_tokens
        )
        return ModelRequest(
            messages=messages,
            tools=self._model_tools(),
            model_class=r.spec.model_policy.default_class,
            token_limit=max(1, min(_MAX_TOKENS_PER_REQUEST, remaining_output)),
        )

    def _advertised_tools(self) -> list[ToolSpec]:
        """§12.2 — advertise registered tools to the model (OpenAI function
        calling); a ToolExecutor without the seam advertises nothing."""
        getter = getattr(self._tools, "available_tools", None)
        return [] if getter is None else list(getter())

    def _model_tools(self) -> list[ToolSpec]:
        """What the model is offered: the executor's tools, plus the
        synthetic ``request_capability`` when a capability plane is
        advertised (the id is reserved — an executor tool of that id is
        never offered, so the wire carries no duplicate function name)."""
        tools = self._advertised_tools()
        if not self._capabilities_advertised:
            return tools
        return [t for t in tools if t.tool_id != CAPABILITY_TOOL_ID] + [CAPABILITY_TOOL]

    def _append(self, r: _Run, turn: int, *, assistant: str, user: str | None) -> None:
        entries: list[TranscriptEntry] = []
        if assistant.strip():
            entries.append(TranscriptEntry(role="assistant", content=assistant, turn=turn))
        if user is not None:
            entries.append(TranscriptEntry(role="user", content=user, turn=turn))
        if entries:
            self._state.append_transcript(r.run_id, entries)

    # -- budget, checkpoints, termination ----------------------------------------

    def _sync_wall_time(self, r: _Run) -> None:
        """§7.6 wall time is real elapsed time, including tool runs and backoff."""
        elapsed = time.monotonic() - r.started
        ledger = self._state.snapshot(r.run_id).budget
        delta = elapsed - ledger.consumed_wall_time_seconds
        if delta > 0:
            self._state.consume_budget(r.run_id, wall_time_seconds=delta)

    def _budget_stop_reason(self, snapshot: RuntimeStateSnapshot) -> StopReason | None:
        """§7.6 preflight: turn/token/wall-time/cost ceilings. The tool-call
        ceiling gates tool batches (``_tool_call_stop``), not model turns — a
        run that spent its tool budget may still finalize."""
        b = snapshot.budget
        if b.consumed_turns >= b.max_turns:
            return StopReason.LIMIT_TURNS
        if b.consumed_input_tokens + b.consumed_output_tokens >= b.max_total_tokens:
            return StopReason.LIMIT_TOTAL_TOKENS
        if b.consumed_output_tokens >= b.max_output_tokens:
            return StopReason.LIMIT_OUTPUT_TOKENS
        if b.consumed_wall_time_seconds >= b.max_wall_time_seconds:
            return StopReason.LIMIT_WALL_TIME
        if b.consumed_cost_usd >= b.max_cost_usd:
            return StopReason.LIMIT_COST
        return None

    def _maybe_checkpoint(self, run_id: str, turn: int) -> None:
        """§17.2 periodic trigger — only when a coordinator is wired."""
        if self._checkpoints is None or turn % self._checkpoint_interval != 0:
            return
        self._checkpoints.save(  # type: ignore[attr-defined]
            self._state.snapshot(run_id),
            checkpoint_id=f"chk-{run_id}-turn-{turn}",
        )
        self._emit(CHECKPOINT_SAVED, run_id, payload={"turn": turn})

    def _fail(
        self,
        r: _Run,
        stop: StopReason,
        *,
        detail: str | None = None,
        evidence: EvidencePack | None = None,
    ) -> RunResult:
        self._state.transition(r.run_id, RunStatus.FAILED, stop_reason=stop, detail_code=detail)
        return self._finalize(r, stop, evidence=evidence)

    def _at_limit_evidence(self, r: _Run) -> EvidencePack | None:
        """2026-10-01 review: at the TURN limit the run is FAILED whatever the
        verifier says — but the verification command still runs once and its
        outcome is recorded as EVIDENCE, so "budget exhausted, fix landed,
        unclaimed" is distinguishable from "nothing landed" on the wire.
        INV-08 is never waived: a passing command here is NOT a success; the
        pack's verdict is INCONCLUSIVE and only the command check survives
        (renamed) — the profile checks are meaningless without a candidate
        and are discarded."""
        from aci.runtime.workspace_tools import VERIFICATION_CHECK_NAME

        self._emit(VERIFICATION_STARTED, r.run_id, payload={"at_limit": True})
        verification = self._verifier.verify(
            CandidateResult(summary=""),
            snapshot=self._state.snapshot(r.run_id),
            contract=r.spec.result_contract,
        )
        self._emit(
            VERIFICATION_COMPLETED,
            r.run_id,
            payload={"verdict": verification.verdict, "at_limit": True},
        )
        at_limit = [
            check.model_copy(update={"name": "verification_at_limit"})
            for check in verification.checks
            if check.name == VERIFICATION_CHECK_NAME
        ]
        if not at_limit:
            return None  # no verification command configured — nothing to record
        return EvidencePack(
            verification_verdict="INCONCLUSIVE",
            checks=[f"{'PASS' if c.passed else 'FAIL'}:{c.name}" for c in at_limit],
            evidence_refs=[i.ref for i in verification.evidence.items],
            summary="turn limit reached — verification outcome recorded as evidence, not success",
        )

    def _cancelled(self, r: _Run) -> RunResult:
        status = self._state.snapshot(r.run_id).run.status
        if RunStatus.CANCELLED in RUN_TRANSITIONS[status]:
            self._state.transition(r.run_id, RunStatus.CANCELLED, stop_reason=StopReason.CANCELLED)
        elif not is_terminal(status):
            self._state.transition(
                r.run_id,
                RunStatus.FAILED,
                stop_reason=StopReason.CANCELLED,
                detail_code="CANCELLED",
            )
        return self._finalize(r, StopReason.CANCELLED)

    def _fail_fatal(self, r: _Run, exc: Exception) -> RunResult:
        """An unexpected manager exception: FAILED/FATAL_ERROR, never a crash
        that leaves the run live (INV-07 — nothing model-driven escapes run())."""
        status = self._state.snapshot(r.run_id).run.status
        if not is_terminal(status) and RunStatus.FAILED in RUN_TRANSITIONS[status]:
            self._state.transition(
                r.run_id,
                RunStatus.FAILED,
                stop_reason=StopReason.FATAL_ERROR,
                detail_code=type(exc).__name__,
            )
        # The type only: raw exception text never reaches the caller (§61).
        summary = f"internal error ({type(exc).__name__})"
        return self._finalize(r, StopReason.FATAL_ERROR, summary=summary)

    def _finalize(
        self,
        r: _Run,
        stop_reason: StopReason,
        *,
        evidence: EvidencePack | None = None,
        summary: str = "",
        artifacts: list[str] | None = None,
        approval_id: str | None = None,
    ) -> RunResult:
        snapshot = self._state.snapshot(r.run_id)
        return RunResult(
            approval_id=approval_id,
            run_id=r.run_id,
            status=snapshot.run.status,
            stop_reason=stop_reason,
            detail_code=snapshot.run.detail_code,
            summary=summary,
            artifacts=list(artifacts or []),
            evidence=evidence,
            usage=r.usage.to_run_usage(time.monotonic() - r.started),
        )

    def _emit_terminal(self, result: RunResult) -> None:
        stop = result.stop_reason.value if result.stop_reason else None
        if result.status is RunStatus.SUCCEEDED:
            self._emit(RUN_COMPLETED, result.run_id, payload={"stop_reason": stop})
        elif result.status is RunStatus.CANCELLED:
            self._emit(RUN_CANCELLED, result.run_id)
        elif result.status in (RunStatus.INTERRUPTED, RunStatus.INTERRUPTED_APPROVAL):
            self._emit(RUN_PAUSED, result.run_id, payload={"detail_code": result.detail_code})
        else:
            self._emit(RUN_FAILED, result.run_id, payload={"stop_reason": stop})

    def _emit(
        self,
        event_type: str,
        run_id: str,
        *,
        payload: dict[str, object] | None = None,
        turn_id: str | None = None,
    ) -> None:
        self._events.emit(event_type, run_id=run_id, payload=payload, turn_id=turn_id)


# -- module helpers ------------------------------------------------------------


def _envelope(snapshot: RuntimeStateSnapshot) -> ExecutionEnvelope:
    """Project the run's current grants into the per-batch execution envelope
    (§13.2 — the exact restrictions enforced for these operations)."""
    return ExecutionEnvelope(
        run_id=snapshot.run.run_id,
        workspace_id=snapshot.workspace_id or snapshot.run.run_id,
        filesystem=snapshot.grants.filesystem,
        network=snapshot.grants.network,
        process=snapshot.grants.process,
        expires_at=snapshot.grants.expires_at,
    )


def check_resume_input(
    checkpoint: Checkpoint,
    pending: PendingInterrupt,
    approval: ApprovalDecision | None,
    answer: str | None,
) -> None:
    """The resume input must answer the pause the checkpoint records."""
    status = checkpoint.snapshot.run.status
    if pending.kind == "approval":
        if status is not RunStatus.INTERRUPTED_APPROVAL:
            raise DomainError(ErrorCode.RUN_NOT_RESUMABLE, "the run is not awaiting an approval")
        if approval is None or answer is not None:
            raise DomainError(
                ErrorCode.CLIENT_INCOMPATIBLE,
                "this run awaits an approval decision (approval_id + approve)",
            )
        if approval.approval_id != pending.approval_id:
            raise DomainError(
                ErrorCode.APPROVAL_REPLAY_INVALID,
                "approval_id does not match the run's pending approval",
            )
        return
    if status is not RunStatus.INTERRUPTED:
        raise DomainError(ErrorCode.RUN_NOT_RESUMABLE, "the run is not awaiting an answer")
    if approval is not None:
        raise DomainError(ErrorCode.APPROVAL_REPLAY_INVALID, "no approval is pending on this run")
    if answer is None or not answer.strip():
        raise DomainError(ErrorCode.CLIENT_INCOMPATIBLE, "this run awaits a clarification answer")


#: detail_code of a PAUSED run cancelled by its client (POST .../cancel).
CANCELLED_WHILE_PAUSED = "CANCELLED_WHILE_PAUSED"
#: detail_code of a PAUSED run whose pause a revision took over (§29A).
SUPERSEDED_BY_REVISION = "SUPERSEDED_BY_REVISION"


def cancel_paused(
    checkpoint: Checkpoint,
    *,
    state: StateManager,
    event_bus: EventBus,
    detail_code: str = CANCELLED_WHILE_PAUSED,
) -> RunResult:
    """§7.3 INTERRUPTED/INTERRUPTED_APPROVAL → CANCELLED for a paused run
    that has no live loop (nothing to signal): the pause snapshot is
    restored into ``state`` (version continuity, INV-01) and the
    StateManager makes the move — the same status + stop reason as a
    cancelled live run (CANCELLED/CANCELLED), with ``detail_code`` naming
    why — then RUN_CANCELLED is emitted. Nothing pending executes.

    The CALLER claims the checkpoint first (at most one of resume / cancel /
    revise wins it); this function never consumes anything. A checkpoint
    whose snapshot is not paused is RUN_NOT_RESUMABLE (checked before any
    write)."""
    snapshot = checkpoint.snapshot
    if snapshot.run.run_id != checkpoint.run_id or snapshot.run.status not in (
        RunStatus.INTERRUPTED,
        RunStatus.INTERRUPTED_APPROVAL,
    ):
        raise DomainError(
            ErrorCode.RUN_NOT_RESUMABLE, f"run {checkpoint.run_id} is not paused at this checkpoint"
        )
    state.restore(snapshot)
    final = state.transition(
        checkpoint.run_id,
        RunStatus.CANCELLED,
        stop_reason=StopReason.CANCELLED,
        detail_code=detail_code,
    )
    kind = checkpoint.pending.kind if checkpoint.pending is not None else None
    event_bus.emit(
        RUN_CANCELLED,
        run_id=checkpoint.run_id,
        payload={"while_paused": kind, "detail_code": detail_code},
    )
    return RunResult(
        run_id=checkpoint.run_id,
        status=final.run.status,
        stop_reason=StopReason.CANCELLED,
        detail_code=final.run.detail_code,
        usage=checkpoint.usage or RunUsage(),
    )


def _approval_denied(call: ToolCall, *, gated: bool) -> ToolObservation:
    """The observation for a call that did NOT run because its approval was
    denied (``gated``) or an earlier call of its batch was."""
    summary = (
        "the delegating client DENIED approval for this call; it was NOT executed — "
        "do not retry it, choose another approach"
        if gated
        else "NOT executed: an earlier call in this batch was denied approval"
    )
    return ToolObservation(
        tool_call_id=call.call_id,
        tool_id=call.tool_id,
        status="denied",
        summary=summary,
        error_class=APPROVAL_REJECTED,
    )


def _tool_call_stop(snapshot: RuntimeStateSnapshot, batch_size: int) -> StopReason | None:
    """§7.6 — a batch that does not fit the remaining tool budget never starts."""
    b = snapshot.budget
    if b.consumed_tool_calls + batch_size > b.max_tool_calls:
        return StopReason.LIMIT_TOOL_CALLS
    return None


def _classify_model_error(exc: Exception) -> FailureClass:
    """§18.1 — only provider-declared transient failures are retried; a
    programming error or a misconfiguration is FATAL, never looped on."""
    if isinstance(exc, DomainError):
        return {
            ErrorCode.MODEL_MALFORMED_OUTPUT: FailureClass.MODEL_MALFORMED_OUTPUT,
            ErrorCode.RATE_LIMITED: FailureClass.RATE_LIMITED,
            ErrorCode.MODEL_UNAVAILABLE: FailureClass.TRANSIENT_MODEL,
        }.get(exc.code, FailureClass.FATAL)
    if isinstance(exc, ValidationError):
        return FailureClass.MODEL_MALFORMED_OUTPUT
    return FailureClass.FATAL


def _error_text(exc: Exception) -> str:
    text = str(exc) if isinstance(exc, (DomainError, ValidationError)) else type(exc).__name__
    return text[:500]


def _failure(cls: FailureClass, component: str, evidence: list[str]) -> FailureEnvelope:
    return FailureEnvelope(
        failure_id=f"fail-{uuid.uuid4().hex[:12]}",
        failure_class=cls,
        component=component,
        message="; ".join(evidence[:2]) or cls.value,
        evidence_refs=evidence,
    )


def _capability_request_from(arguments: dict[str, object]) -> CapabilityRequest | None:
    """request_capability arguments → CapabilityRequest, or None when they
    are unusable (no non-empty ``objective``). A bare-string ``constraints``
    is one constraint; non-string entries are dropped."""
    objective = arguments.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        return None
    raw = arguments.get("constraints")
    items = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    constraints = [c for c in items if isinstance(c, str) and c.strip()]
    try:
        return CapabilityRequest(objective=objective.strip(), constraints=constraints)
    except ValidationError:
        return None


def _capability_note(activations: list[CapabilityActivation]) -> str:
    if activations:
        loaded = ", ".join(f"{a.capability_id}@{a.version}" for a in activations)
        return f"Capability request result: loaded {loaded}; its instructions are in context."
    return "Capability request result: nothing matched; continue with the available tools."


def _capability_not_processed(
    call: ToolCall, message: str, code: str = CAPABILITY_REQUEST_NOT_PROCESSED
) -> ToolObservation:
    """The result of a request_capability call the kernel did NOT process
    (nothing was searched, no refresh budget spent)."""
    return ToolObservation(
        tool_call_id=call.call_id,
        tool_id=call.tool_id,
        status="error",
        summary=message,
        error_class=code,
    )


def _render_observation(obs: ToolObservation) -> str:
    """The tool result as the model sees it: status first, then the message
    that explains a non-success (denials, unknown tools), then the output."""
    head = f"status: {obs.status}" + (f" ({obs.error_class})" if obs.error_class else "")
    parts = [head]
    if obs.status != "success" and obs.summary:
        parts.append(obs.summary)
    if obs.inline_output:
        parts.append(obs.inline_output)
    if obs.artifact_ref:
        parts.append(f"[full output: {obs.artifact_ref}]")
    return "\n".join(parts)


def _to_message(entry: TranscriptEntry) -> ModelMessage:
    return ModelMessage(
        role=entry.role,
        content=entry.content,
        tool_call_id=entry.tool_call_id,
        tool_calls=list(entry.tool_calls),
    )


def _progress_summary(snapshot: RuntimeStateSnapshot) -> str:
    """Harness-observed progress, pinned so it survives transcript compaction."""
    lines: list[str] = []
    changed = [res.removeprefix("file:") for res in snapshot.changed_resources]
    if changed:
        lines.append("Files changed so far: " + ", ".join(changed[-20:]))
    commands = [
        e.summary for e in snapshot.observed_evidence if e.kind is EvidenceKind.COMMAND_OUTPUT
    ]
    if commands:
        lines.append("Recent commands: " + " | ".join(commands[-3:]))
    return "Progress (observed by the harness):\n" + "\n".join(lines) if lines else ""


_PlanStatus = Literal["pending", "running", "blocked", "done", "failed"]
_PLAN_STATUSES: tuple[_PlanStatus, ...] = ("pending", "running", "blocked", "done", "failed")


def _plan_objective(item: dict[str, str]) -> str:
    for key in ("objective", "step", "title", "description"):
        if item.get(key):
            return item[key]
    return "; ".join(f"{k}={v}" for k, v in item.items()) or "step"


def _plan_status(item: dict[str, str]) -> _PlanStatus:
    status = item.get("status", "pending")
    for known in _PLAN_STATUSES:
        if status == known:
            return known
    return "pending"


def task_state_from(contract: SubtaskContract) -> TaskState:
    return TaskState(
        task_id=contract.task_id,
        objective=contract.objective,
        constraints=contract.constraints,
        acceptance_criteria=[c.description for c in contract.acceptance_criteria],
    )


_ACTION_PROTOCOL = """\
Work on the workspace ONLY through the provided tools (function calls); paths are \
workspace-relative. When the objective is met, reply with EXACTLY ONE JSON object as the \
final line of your message and no tool call in that message:
{"type": "final_candidate", "summary": "...", "changes": [...], "artifacts": [...], \
"claims": [...], "criteria_addressed": [...]}
"summary" must be non-empty. The four lists hold PLAIN STRINGS only:
- "changes": files you changed, as "path" or "path: note";
- "artifacts": files you produced, as "path" or "path: note";
- "claims": findings, each starting with the source it is grounded in: "path[:line]: claim";
- "criteria_addressed": the acceptance criteria you satisfied.
The harness verifies every entry against what it observed you read, write and run; \
unverifiable entries are rejected and returned to you as feedback.
Other JSON actions: {"type": "plan_update", "items": [{"objective": "...", "status": \
"pending"}]} records a plan; {"type": "clarification", "question": "..."} stops the run to \
ask the delegating client."""

_CAPABILITY_PROTOCOL = (
    f"Skills: call the {CAPABILITY_TOOL_ID} function ALONE (no other tool call in that "
    'message), or reply with {"type": "capability_request", "objective": "...", '
    '"constraints": [...]}, to ask the ACI capability registry for a vetted skill '
    "(instructions) matching that need; the matching skills' instructions are added to your "
    "context on the next turn. Use it when a domain-specific procedure would help; it never "
    "grants extra tools or permissions."
)

_CAPABILITY_NOT_ALONE = (
    f"NOT processed: {CAPABILITY_TOOL_ID} must be called ALONE (no other tool call in the "
    "same message); the other calls of this message were handled normally. Call it again "
    "by itself if you still need a skill."
)

_CAPABILITY_ONE_PER_MESSAGE = (
    f"NOT processed: only the first {CAPABILITY_TOOL_ID} call of a message is handled; "
    "combine the needs into one request if the result above does not cover them."
)

_CAPABILITY_ARGS_HINT = (
    f'{CAPABILITY_TOOL_ID} needs {{"objective": "<non-empty string>"}} and optionally '
    '{"constraints": ["..."]}; nothing was searched.'
)

_CLARIFICATION_ANSWER = "Answer from the delegating client to your question: {answer}"

_CONTINUE_PROMPT = (
    "No action was taken. Continue: call a tool to make progress, or reply with the "
    "final_candidate JSON if the objective is met."
)

_REPAIR_PROMPT = (
    "Your previous reply could not be used: {error}. Reply again with either a tool "
    "call or exactly one JSON action object as specified."
)

_NOT_RETRIED_NOTE = (
    "not retried automatically (this tool is not safe to re-run blindly); check the "
    "current state before repeating the call"
)

_VERIFICATION_FEEDBACK = (
    "Verification FAILED — the result was NOT accepted:\n{hints}\n"
    "Fix the problems using the tools, then reply with a new final_candidate."
)


def _system_prompt(
    snapshot: RuntimeStateSnapshot, spec: RuntimeSpec, *, capabilities: bool = False
) -> str:
    """Tier-0 context (§9.2): identity + objective + authority summary + protocol."""
    from aci.runtime.profiles import PROFILES

    profile = PROFILES[spec.profile_id]
    grants = snapshot.grants
    parts = [profile.instructions, f"Objective: {snapshot.task.objective}"]
    if grants.filesystem.write:
        scopes = ", ".join(grants.filesystem.write)
        parts.append(
            f"You may modify files under: {scopes} "
            "(workspace-relative; '.' is the whole workspace)."
        )
    else:
        parts.append("You have READ-ONLY authority: do not claim file mutations.")
    if grants.process.allowed_prefixes:
        parts.append(
            "Commands you may run (argv prefixes): " + "; ".join(grants.process.allowed_prefixes)
        )
    parts.append(_ACTION_PROTOCOL)
    if capabilities:
        parts.append(_CAPABILITY_PROTOCOL)
    return "\n".join(parts)
