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
from collections.abc import Callable
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
from aci.domain.runtime.authority import ExecutionEnvelope
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
from aci.runtime.context_engine import AssembledContext, estimate_tokens, select_transcript
from aci.runtime.event_bus import (
    CAPABILITY_LOADED,
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
    RUN_STARTED,
    TOOL_EXECUTION_COMPLETED,
    TOOL_EXECUTION_FAILED,
    TOOL_REQUESTED,
    TURN_STARTED,
    VERIFICATION_COMPLETED,
    VERIFICATION_STARTED,
    EventBus,
)
from aci.runtime.model_gateway import ModelMessage, ModelRequest, ModelResponse
from aci.runtime.recovery import RecoveryAction, RecoveryManager
from aci.runtime.state_manager import StateEvent, StateManager, transcript_event
from aci.runtime.tool_runtime import envelope_expired
from aci.runtime.verification import VerificationManager

#: Per-request completion cap; the run-level output budget still applies.
_MAX_TOKENS_PER_REQUEST = 8_192
_DEFAULT_CONTEXT_TOKENS = 60_000
_MAX_BACKOFF_SECONDS = 8.0


class ModelGateway(Protocol):
    def invoke(self, request: ModelRequest) -> ModelResponse: ...


class ToolExecutor(Protocol):
    """What RunController needs from ToolRuntime (avoids circular imports)."""

    def execute_batch(
        self,
        calls: list[ToolCall],
        *,
        snapshot: RuntimeStateSnapshot,
        envelope: ExecutionEnvelope,
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
    ) -> None:
        self._state = state
        self._model = model_gateway
        self._tools = tool_executor
        self._context = context_engine
        self._verifier = verifier
        self._recovery = recovery
        self._capabilities = capability_runtime
        self._events = event_bus or EventBus()
        self._checkpoints = checkpoints
        self._checkpoint_interval = checkpoint_interval_turns
        self._sleep = sleep
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
        self._emit(
            RECOVERY_ACTION,
            r.run_id,
            payload={"failure_class": failure.failure_class.value, "action": action.action},
        )
        if (
            action.is_terminal
            or snapshot.budget.consumed_recoveries > snapshot.budget.max_recoveries
        ):
            return None
        return action

    # -- tools (§12, §59 STATE_COMMIT) -----------------------------------------

    def _run_tools(
        self, r: _Run, action: ToolCallBatchAction, raw: str, turn: int
    ) -> RunResult | None:
        self._emit(
            TOOL_REQUESTED,
            r.run_id,
            payload={"calls": [c.tool_id for c in action.calls]},
            turn_id=f"turn-{turn}",
        )
        # §7.6: the model call just consumed budget, and the batch must fit whole.
        pre = self._state.snapshot(r.run_id)
        stop = self._budget_stop_reason(pre) or _tool_call_stop(pre, len(action.calls))
        if stop is not None:
            return self._fail(r, stop)
        envelope = _envelope(pre)
        if envelope_expired(envelope):
            # Grants never renew mid-run: every further call would be denied.
            return self._fail(r, StopReason.AUTHORITY_DENIED, detail="AUTHORITY_EXPIRED")
        observations = self._tools.execute_batch(
            list(action.calls), snapshot=pre, envelope=envelope
        )
        entries = [
            TranscriptEntry(
                role="assistant", content=raw, tool_calls=list(action.calls), turn=turn
            ),
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
                StateEvent(event_type="budget.consumed", payload={"tool_calls": len(observations)}),
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
        r.usage.tool_calls += len(observations)
        for obs in observations:
            self._emit(
                TOOL_EXECUTION_COMPLETED if obs.status == "success" else TOOL_EXECUTION_FAILED,
                r.run_id,
                payload={"tool_id": obs.tool_id, "status": obs.status},
                turn_id=f"turn-{turn}",
            )
        # §23.1: cancellation between tools — a cancelled run never starts the
        # next batch, but the observations it already produced are committed.
        r.cancel.raise_if_cancelled()
        return None

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
        snapshot = self._state.snapshot(r.run_id)
        try:
            activations = self._capabilities.handle_request(request, snapshot)
        except Exception as exc:  # noqa: BLE001 — capability gaps are failures, not crashes
            return self._fail(r, StopReason.CAPABILITY_UNAVAILABLE, detail=type(exc).__name__)
        for activation in activations:
            self._state.activate_capability(r.run_id, activation)
            self._emit(
                CAPABILITY_LOADED,
                r.run_id,
                payload={"capability_id": activation.capability_id, "version": activation.version},
            )
        if activations:
            loaded = ", ".join(f"{a.capability_id}@{a.version}" for a in activations)
            note = f"Capability request result: loaded {loaded}; its instructions are in context."
        else:
            note = "Capability request result: nothing matched; continue with the available tools."
        self._append(r, turn, assistant=raw or request.model_dump_json(), user=note)
        return None

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
        (§0.3), so the run stops and surfaces the question — a revision
        carries the answer back as feedback."""
        self._append(r, turn, assistant=raw or action.model_dump_json(), user=None)
        self._state.transition(
            r.run_id,
            RunStatus.INTERRUPTED,
            stop_reason=StopReason.INTERRUPTED,
            detail_code="CLARIFICATION_REQUIRED",
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
        system = [_system_prompt(snapshot, r.spec)]
        if r.contract.constraints:
            system.append("Constraints: " + "; ".join(r.contract.constraints))
        if r.contract.global_context:
            system.append("Context from the delegating client:\n" + r.contract.global_context)
        system.extend(item.content for item in assembled.items if item.content)
        progress = _progress_summary(snapshot)
        if progress:
            system.append(progress)
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
            tools=self._advertised_tools(),
            model_class=r.spec.model_policy.default_class,
            token_limit=max(1, min(_MAX_TOKENS_PER_REQUEST, remaining_output)),
        )

    def _advertised_tools(self) -> list[ToolSpec]:
        """§12.2 — advertise registered tools to the model (OpenAI function
        calling); a ToolExecutor without the seam advertises nothing."""
        getter = getattr(self._tools, "available_tools", None)
        return [] if getter is None else list(getter())

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
    ) -> RunResult:
        snapshot = self._state.snapshot(r.run_id)
        return RunResult(
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
        elif result.status is RunStatus.INTERRUPTED:
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

_CONTINUE_PROMPT = (
    "No action was taken. Continue: call a tool to make progress, or reply with the "
    "final_candidate JSON if the objective is met."
)

_REPAIR_PROMPT = (
    "Your previous reply could not be used: {error}. Reply again with either a tool "
    "call or exactly one JSON action object as specified."
)

_VERIFICATION_FEEDBACK = (
    "Verification FAILED — the result was NOT accepted:\n{hints}\n"
    "Fix the problems using the tools, then reply with a new final_candidate."
)


def _system_prompt(snapshot: RuntimeStateSnapshot, spec: RuntimeSpec) -> str:
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
    return "\n".join(parts)
