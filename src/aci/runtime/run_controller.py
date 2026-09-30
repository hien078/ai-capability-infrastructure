"""RunController + HarnessKernel (harness.md §5, §7, §59): lifecycle coordinator.

RunController owns turn sequencing, budget checks, cancellation, action
dispatch — NOT business logic (§7.2). HarnessKernel is the composition root
(§5): one kernel serves all nine profiles; profiles are data (RuntimeSpec).
"""

import json
import time
from dataclasses import dataclass
from typing import Protocol, cast

from aci.domain.runtime.actions import (
    CapabilityRequest,
    ContinueAction,
    DelegationRequest,
    FinalCandidate,
    PlanUpdateRequest,
    ToolCallBatchAction,
)
from aci.domain.runtime.authority import ExecutionEnvelope
from aci.domain.runtime.evidence import CandidateResult, EvidencePack
from aci.domain.runtime.failures import FailureClass, FailureEnvelope
from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.state import RuntimeStateSnapshot, TaskState
from aci.domain.runtime.stop_reason import RUN_TRANSITIONS, RunStatus, StopReason, is_terminal
from aci.domain.runtime.subtask import RunResult, RunUsage, SubtaskContract
from aci.domain.runtime.tools import ToolCall, ToolObservation, ToolSpec
from aci.runtime.cancellation import CancelToken, RunCancelled
from aci.runtime.event_bus import (
    CAPABILITY_LOADED,
    CHECKPOINT_SAVED,
    CONTEXT_ASSEMBLED,
    MODEL_REQUEST_COMPLETED,
    MODEL_REQUEST_STARTED,
    RECOVERY_ACTION,
    RUN_CANCELLED,
    RUN_COMPLETED,
    RUN_CREATED,
    RUN_FAILED,
    RUN_STARTED,
    TOOL_EXECUTION_COMPLETED,
    TOOL_EXECUTION_FAILED,
    TOOL_REQUESTED,
    TURN_STARTED,
    VERIFICATION_COMPLETED,
    VERIFICATION_STARTED,
    EventBus,
)
from aci.runtime.model_gateway import ModelRequest, ModelUsage
from aci.runtime.recovery import RecoveryManager
from aci.runtime.state_manager import StateEvent, StateManager
from aci.runtime.tool_runtime import envelope_expired
from aci.runtime.verification import VerificationManager

_FAILURE_SEQ = 1000


class ModelGateway(Protocol):
    def invoke(self, request: "object") -> "object": ...  # replaced in kernel wiring


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


class HarnessKernel:
    """§5 composition root. Business logic lives in the managers; the kernel
    wires them and RunController sequences the turns (§59 run loop)."""

    def __init__(
        self,
        *,
        state: StateManager,
        model_gateway: object,
        tool_executor: ToolExecutor,
        context_engine: object,
        verifier: VerificationManager,
        recovery: RecoveryManager,
        capability_runtime: object,
        event_bus: EventBus | None = None,
        checkpoints: object | None = None,
        checkpoint_interval_turns: int = 5,
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
        # §7.4 turn lifecycle: the conversation history persists across turns —
        # each turn appends (assistant action, tool observations) so the model
        # sees its own previous tool results (without this the model re-issues
        # the same tool call forever because the result never reaches it).
        self._history: dict[str, list[object]] = {}

    def run(
        self,
        contract: SubtaskContract,
        spec: RuntimeSpec,
        *,
        cancel_token: CancelToken | None = None,
        max_turns: int = 40,
    ) -> RunResult:
        """§59 — the deterministic control shape: budget preflight → context →
        model → normalize action → dispatch → commit → loop; FinalCandidate
        goes through verification (INV-08)."""
        run_id = contract.task_id
        cancel = cancel_token or CancelToken(run_id=contract.task_id)
        self._state.create(
            run_id=run_id,
            task=task_state_from(contract),
            budget=spec.budget,
            grants=spec.initial_grants,
        )
        self._emit(RUN_CREATED, run_id, payload={"objective": contract.objective})
        self._state.transition(run_id, RunStatus.INITIALIZING)
        self._state.transition(run_id, RunStatus.READY)
        self._state.transition(run_id, RunStatus.RUNNING)
        self._emit(RUN_STARTED, run_id)
        started = time.monotonic()
        usage = _Usage()
        try:
            result = self._loop(run_id, contract, spec, cancel, max_turns, usage, started)
        except RunCancelledError:
            self._state.transition(run_id, RunStatus.CANCELLED, stop_reason=StopReason.CANCELLED)
            result = self._finalize(run_id, usage, started, StopReason.CANCELLED)
        except Exception as exc:  # noqa: BLE001 — a run never ends stuck in a live state
            result = self._fail_fatal(run_id, usage, started, exc)
        if result.status is RunStatus.SUCCEEDED:
            self._emit(
                RUN_COMPLETED,
                run_id,
                payload={"stop_reason": result.stop_reason.value if result.stop_reason else None},
            )
        elif result.status is RunStatus.CANCELLED:
            self._emit(RUN_CANCELLED, run_id)
        else:
            self._emit(
                RUN_FAILED,
                run_id,
                payload={"stop_reason": result.stop_reason.value if result.stop_reason else None},
            )
        return result

    def _emit(
        self,
        event_type: str,
        run_id: str,
        *,
        payload: dict[str, object] | None = None,
        turn_id: str | None = None,
    ) -> None:
        self._events.emit(event_type, run_id=run_id, payload=payload, turn_id=turn_id)

    def _advertised_tools(self) -> list[ToolSpec]:
        """§12.2 — advertise registered tools to the model (OpenAI function
        calling); a ToolExecutor without the seam advertises nothing."""
        getter = getattr(self._tools, "available_tools", None)
        if getter is None:
            return []
        return list(getter())

    def _append_history(
        self, run_id: str, action: ToolCallBatchAction, observations: list[ToolObservation]
    ) -> None:
        """§7.4 — record the assistant tool request + each observation as
        conversation turns so the next model call sees the results."""
        from aci.runtime.model_gateway import ModelMessage

        history = self._history.setdefault(run_id, [])
        history.append(
            ModelMessage(
                role="assistant",
                content=json.dumps(
                    {
                        "type": "tool_calls",
                        "calls": [
                            {"call_id": c.call_id, "tool_id": c.tool_id, "arguments": c.arguments}
                            for c in action.calls
                        ],
                    }
                ),
            )
        )
        for obs in observations:
            history.append(
                ModelMessage(
                    role="tool",
                    tool_call_id=obs.tool_call_id,
                    content=json.dumps(
                        {
                            "tool_id": obs.tool_id,
                            "status": obs.status,
                            "output": obs.inline_output[:4000],
                            "error_class": obs.error_class,
                        }
                    ),
                )
            )

    def _maybe_checkpoint(self, run_id: str, turn: int) -> None:
        """§17.2 periodic trigger — only when a coordinator is wired."""
        if self._checkpoints is None or turn % self._checkpoint_interval != 0:
            return
        self._checkpoints.save(  # type: ignore[attr-defined]
            self._state.snapshot(run_id),
            checkpoint_id=f"chk-{run_id}-turn-{turn}",
        )
        self._emit(CHECKPOINT_SAVED, run_id, payload={"turn": turn})

    # -- internals ------------------------------------------------------------

    def _envelope(self, run_id: str) -> ExecutionEnvelope:
        """Project the run's current grants into the per-batch execution envelope
        (§13.2 — the exact restrictions enforced for these operations)."""
        snapshot = self._state.snapshot(run_id)
        return ExecutionEnvelope(
            run_id=run_id,
            workspace_id=snapshot.workspace_id or run_id,
            filesystem=snapshot.grants.filesystem,
            network=snapshot.grants.network,
            process=snapshot.grants.process,
            expires_at=snapshot.grants.expires_at,
        )

    def _loop(
        self,
        run_id: str,
        contract: SubtaskContract,
        spec: RuntimeSpec,
        cancel: CancelToken,
        max_turns: int,
        usage: _Usage,
        started: float,
    ) -> RunResult:
        while True:
            cancel.raise_if_cancelled()
            snapshot = self._state.snapshot(run_id)
            # §7.6 budget preflight before the model call.
            budget_stop = self._budget_stop_reason(snapshot)
            if budget_stop is not None:
                self._state.transition(run_id, RunStatus.FAILED, stop_reason=budget_stop)
                return self._finalize(run_id, usage, started, budget_stop)
            if snapshot.run.current_turn >= max_turns:
                self._state.transition(run_id, RunStatus.FAILED, stop_reason=StopReason.LIMIT_TURNS)
                return self._finalize(run_id, usage, started, StopReason.LIMIT_TURNS)
            self._state.advance_turn(run_id)
            usage.turns += 1
            self._emit(TURN_STARTED, run_id, turn_id=f"turn-{snapshot.run.current_turn + 1}")
            # §17.2 periodic checkpoint trigger for long runs.
            self._maybe_checkpoint(run_id, snapshot.run.current_turn + 1)
            # Context assembly stays OUTSIDE the model try-block: a context
            # failure is FATAL_ERROR, not a misreported MODEL_FAILURE.
            request = self._build_model_request(snapshot, contract, spec)
            try:
                action, model_usage_raw = self._invoke_model(run_id, request)
            except RunCancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — model failures are classified, not crashes
                return self._fail_model(run_id, usage, started, exc)
            # §7.6: consume usage as soon as it is known.
            model_usage = cast("ModelUsage", model_usage_raw)
            if model_usage is not None:
                self._state.consume_budget(
                    run_id,
                    input_tokens=model_usage.input_tokens,
                    output_tokens=model_usage.output_tokens,
                    cost_usd=model_usage.cost_usd,
                    wall_time_seconds=model_usage.latency_ms / 1000,
                )
                usage.input_tokens += model_usage.input_tokens
                usage.output_tokens += model_usage.output_tokens
                usage.cost_usd += model_usage.cost_usd
            if isinstance(action, ToolCallBatchAction):
                self._emit(
                    TOOL_REQUESTED,
                    run_id,
                    payload={"calls": [c.tool_id for c in action.calls]},
                    turn_id=f"turn-{snapshot.run.current_turn + 1}",
                )
                # §7.6: budget check before each tool execution — the model
                # call just consumed budget, and the batch must fit whole.
                pre = self._state.snapshot(run_id)
                tool_stop = self._budget_stop_reason(pre) or _tool_call_stop(pre, len(action.calls))
                if tool_stop is not None:
                    self._state.transition(run_id, RunStatus.FAILED, stop_reason=tool_stop)
                    return self._finalize(run_id, usage, started, tool_stop)
                envelope = self._envelope(run_id)
                if envelope_expired(envelope):
                    # Grants never renew mid-run: every further call would be
                    # denied, so stop instead of burning model turns.
                    self._state.transition(
                        run_id,
                        RunStatus.FAILED,
                        stop_reason=StopReason.AUTHORITY_DENIED,
                        detail_code="AUTHORITY_EXPIRED",
                    )
                    return self._finalize(run_id, usage, started, StopReason.AUTHORITY_DENIED)
                batch_started = time.monotonic()
                observations = self._tools.execute_batch(
                    list(action.calls), snapshot=self._state.snapshot(run_id), envelope=envelope
                )
                self._state.consume_budget(
                    run_id,
                    tool_calls=len(observations),
                    wall_time_seconds=time.monotonic() - batch_started,
                )
                usage.tool_calls += len(observations)
                # §7.4: append the assistant action + observations to the
                # conversation history so the model sees its own tool results.
                self._append_history(run_id, action, observations)
                # §23.1: cancellation check between tools — a cancelled run
                # never starts the next batch.
                cancel.raise_if_cancelled()
                # §59 STATE_COMMIT: observations are committed to authoritative
                # state (CAS on the snapshot version taken this turn).
                self._state.commit(
                    run_id,
                    expected_version=self._state.snapshot(run_id).run.version,
                    events=[
                        StateEvent(
                            event_type="task.progress",
                            payload={
                                "progress": [
                                    *(snapshot.task.progress or []),
                                    f"turn {snapshot.run.current_turn + 1}: "
                                    f"{len(observations)} tool observation(s)",
                                ]
                            },
                        ),
                        # INV-08 evidence: only effects the tool path confirmed.
                        StateEvent(
                            event_type="tool.observed",
                            payload={
                                "resources": [
                                    r
                                    for o in observations
                                    if o.status == "success" and o.side_effects.state == "confirmed"
                                    for r in o.side_effects.resources_changed
                                ]
                            },
                        ),
                    ],
                )
                for obs in observations:
                    self._emit(
                        TOOL_EXECUTION_COMPLETED
                        if obs.status == "success"
                        else TOOL_EXECUTION_FAILED,
                        run_id,
                        payload={"tool_id": obs.tool_id, "status": obs.status},
                        turn_id=f"turn-{snapshot.run.current_turn + 1}",
                    )
                continue
            if isinstance(action, FinalCandidate):
                return self._verify_and_finish(run_id, contract, spec, action, usage, started)
            if isinstance(action, CapabilityRequest):
                if not self._handle_capability_request(run_id, action):
                    return self._finalize(run_id, usage, started, StopReason.CAPABILITY_UNAVAILABLE)
                continue
            if isinstance(action, DelegationRequest):
                # §0.3: delegation OFF by default — the request fails closed.
                self._state.transition(
                    run_id, RunStatus.FAILED, stop_reason=StopReason.AUTHORITY_DENIED
                )
                return self._finalize(run_id, usage, started, StopReason.AUTHORITY_DENIED)
            if isinstance(action, (PlanUpdateRequest, ContinueAction)):
                continue
            # ClarificationRequest or unknown → treat as no-progress turn.
            continue

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

    def _fail_fatal(self, run_id: str, usage: _Usage, started: float, exc: Exception) -> RunResult:
        """An unexpected manager exception: FAILED/FATAL_ERROR, never a crash
        that leaves the run live (INV-07 — nothing model-driven escapes run())."""
        status = self._state.snapshot(run_id).run.status
        if not is_terminal(status) and RunStatus.FAILED in RUN_TRANSITIONS[status]:
            self._state.transition(
                run_id,
                RunStatus.FAILED,
                stop_reason=StopReason.FATAL_ERROR,
                detail_code=type(exc).__name__,
            )
        # The type only: raw exception text never reaches the caller (§61).
        summary = f"internal error ({type(exc).__name__})"
        return self._finalize(run_id, usage, started, StopReason.FATAL_ERROR, summary=summary)

    def _fail_model(self, run_id: str, usage: _Usage, started: float, exc: Exception) -> RunResult:
        """§18: classify the model failure, run recovery, never crash the run."""
        from aci.domain.capability.errors import DomainError, ErrorCode
        from aci.domain.runtime.failures import FailureClass

        if isinstance(exc, DomainError) and exc.code in (
            ErrorCode.MODEL_MALFORMED_OUTPUT,
            ErrorCode.RATE_LIMITED,
        ):
            failure_class = (
                FailureClass.MODEL_MALFORMED_OUTPUT
                if exc.code is ErrorCode.MODEL_MALFORMED_OUTPUT
                else FailureClass.RATE_LIMITED
            )
        else:
            failure_class = FailureClass.TRANSIENT_MODEL
        failure = _failure(failure_class, "model_gateway", [str(exc)])
        action = self._recovery.decide(failure)
        self._state.count_recovery(run_id)
        self._emit(
            RECOVERY_ACTION,
            run_id,
            payload={"failure_class": failure.failure_class.value, "action": action.action},
        )
        if action.is_terminal:
            self._state.transition(run_id, RunStatus.FAILED, stop_reason=StopReason.MODEL_FAILURE)
            return self._finalize(run_id, usage, started, StopReason.MODEL_FAILURE)
        # Non-terminal recovery on a model failure without a replayable action:
        # retrying the same call needs a fresh turn — surface as MODEL_FAILURE
        # rather than looping (§18.4 no infinite self-healing).
        self._state.transition(run_id, RunStatus.FAILED, stop_reason=StopReason.MODEL_FAILURE)
        return self._finalize(run_id, usage, started, StopReason.MODEL_FAILURE)

    def _build_model_request(
        self, snapshot: RuntimeStateSnapshot, contract: SubtaskContract, spec: RuntimeSpec
    ) -> ModelRequest:
        """§20.1 — the provider-neutral ModelRequest (system identity +
        objective + constraints + active capability instructions)."""
        from aci.runtime.model_gateway import ModelMessage

        run_id = snapshot.run.run_id
        history = self._history.setdefault(run_id, [])
        if not history:
            # First turn: system + objective + constraints seed the conversation.
            messages: list[object] = [
                ModelMessage(role="system", content=_system_prompt(snapshot, spec)),
                ModelMessage(role="user", content=contract.objective),
            ]
            if contract.constraints:
                messages.append(
                    ModelMessage(
                        role="system",
                        content="Constraints: " + "; ".join(contract.constraints),
                    )
                )
            # §9.4: the ContextEngine selects the working set for this turn; its
            # items ride as additional context messages within the token budget.
            assembled = self._context.build(snapshot, turn=snapshot.run.current_turn + 1)  # type: ignore[attr-defined]
            for item in assembled.items:
                if item.content:
                    messages.append(ModelMessage(role="system", content=item.content))
            self._emit(
                CONTEXT_ASSEMBLED,
                run_id,
                payload={
                    "total_tokens": assembled.total_tokens,
                    "dropped": len(assembled.dropped_item_ids),
                },
            )
            history.extend(messages)
        else:
            # Later turns: the context engine may add NEW items; the persisted
            # history (objective + prior actions + observations) is authoritative.
            assembled = self._context.build(snapshot, turn=snapshot.run.current_turn + 1)  # type: ignore[attr-defined]
            self._emit(
                CONTEXT_ASSEMBLED,
                run_id,
                payload={
                    "total_tokens": assembled.total_tokens,
                    "dropped": len(assembled.dropped_item_ids),
                },
            )
        return ModelRequest(
            messages=[m for m in history if isinstance(m, ModelMessage)],
            tools=self._advertised_tools(),
            model_class=spec.model_policy.default_class,
            token_limit=spec.loop_policy.max_output_tokens,
        )

    def _invoke_model(self, run_id: str, request: ModelRequest) -> tuple[object, object]:
        """Invoke the gateway; return (normalized action, usage) (§47)."""
        self._emit(MODEL_REQUEST_STARTED, run_id)
        response = self._model.invoke(request)  # type: ignore[attr-defined]
        self._emit(
            MODEL_REQUEST_COMPLETED,
            run_id,
            payload={"action_type": type(response.action).__name__},
        )
        return response.action, response.usage

    def _handle_capability_request(self, run_id: str, request: CapabilityRequest) -> bool:
        """Returns False when the capability gap is terminal for the run."""
        snapshot = self._state.snapshot(run_id)
        try:
            activations = self._capabilities.handle_request(request, snapshot)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — capability gaps are failures, not crashes
            self._state.transition(
                run_id,
                RunStatus.FAILED,
                stop_reason=StopReason.CAPABILITY_UNAVAILABLE,
                detail_code=str(exc),
            )
            return False
        for activation in activations:
            self._state.activate_capability(run_id, activation)
            self._emit(
                CAPABILITY_LOADED,
                run_id,
                payload={
                    "capability_id": activation.capability_id,
                    "version": activation.version,
                },
            )
        return True

    def _verify_and_finish(
        self,
        run_id: str,
        contract: SubtaskContract,
        spec: RuntimeSpec,
        candidate_action: FinalCandidate,
        usage: _Usage,
        started: float,
    ) -> RunResult:
        candidate = CandidateResult(
            summary=candidate_action.summary,
            changes=candidate_action.changes,
            artifacts=candidate_action.artifacts,
            claims=candidate_action.claims,
            criteria_addressed=candidate_action.criteria_addressed,
        )
        self._state.transition(run_id, RunStatus.VERIFYING)
        self._emit(VERIFICATION_STARTED, run_id)
        snapshot = self._state.snapshot(run_id)
        verification = self._verifier.verify(
            candidate, snapshot=snapshot, contract=spec.result_contract
        )
        self._emit(VERIFICATION_COMPLETED, run_id, payload={"verdict": verification.verdict})
        if verification.verdict == "PASS":
            self._state.transition(run_id, RunStatus.SUCCEEDED, stop_reason=StopReason.SUCCESS)
            pack = VerificationManager.to_pack(verification)
            return self._finalize(
                run_id, usage, started, StopReason.SUCCESS, evidence=pack, summary=candidate.summary
            )
        # INV-08: model said done, verifier disagreed → recovery, never success.
        failure = _failure(FailureClass.VERIFICATION_FAILED, "verifier", verification.repair_hints)
        action = self._recovery.decide(failure)
        self._state.count_recovery(run_id)
        self._emit(
            RECOVERY_ACTION,
            run_id,
            payload={"failure_class": failure.failure_class.value, "action": action.action},
        )
        self._state.transition(run_id, RunStatus.FAILED, stop_reason=StopReason.VERIFICATION_FAILED)
        return self._finalize(
            run_id,
            usage,
            started,
            StopReason.VERIFICATION_FAILED,
            summary="; ".join(verification.repair_hints[:3]),
        )

    def _finalize(
        self,
        run_id: str,
        usage: _Usage,
        started: float,
        stop_reason: StopReason,
        *,
        evidence: EvidencePack | None = None,
        summary: str = "",
    ) -> RunResult:
        snapshot = self._state.snapshot(run_id)
        return RunResult(
            run_id=run_id,
            status=snapshot.run.status,
            stop_reason=stop_reason,
            summary=summary,
            evidence=evidence,
            usage=usage.to_run_usage(time.monotonic() - started),
        )


def _tool_call_stop(snapshot: RuntimeStateSnapshot, batch_size: int) -> StopReason | None:
    """§7.6 — a batch that does not fit the remaining tool budget never starts."""
    b = snapshot.budget
    if b.consumed_tool_calls + batch_size > b.max_tool_calls:
        return StopReason.LIMIT_TOOL_CALLS
    return None


def _failure(cls: FailureClass, component: str, evidence: list[str]) -> FailureEnvelope:
    global _FAILURE_SEQ
    _FAILURE_SEQ += 1
    return FailureEnvelope(
        failure_id=f"fail-{_FAILURE_SEQ}",
        failure_class=cls,
        component=component,
        message="; ".join(evidence[:2]) or cls.value,
        evidence_refs=evidence,
    )


def task_state_from(contract: SubtaskContract) -> "TaskState":
    from aci.domain.runtime.state import TaskState

    return TaskState(
        task_id=contract.task_id,
        objective=contract.objective,
        constraints=contract.constraints,
        acceptance_criteria=[c.description for c in contract.acceptance_criteria],
    )


_ACTION_PROTOCOL = """\
Respond with EXACTLY ONE JSON object on the final line — no prose around it:
{"type": "final_candidate", "summary": "...", "changes": [...], "artifacts": [...], \
"claims": [...], "criteria_addressed": [...]}
Use it when the objective is met. "summary" must be non-empty. "changes", \
"artifacts", "claims", "criteria_addressed" are arrays of PLAIN STRINGS only — \
never objects. Each "changes" entry is the workspace-relative path of a file \
you changed through a tool, optionally followed by ": note" (e.g. \
"src/app.py: fix off-by-one"); claimed changes are checked against the \
observed tool effects. Do not emit any other JSON shape."""


def _system_prompt(snapshot: RuntimeStateSnapshot, spec: RuntimeSpec) -> str:
    """Tier-0 context (§9.2): invariants + objective + authority summary."""
    from aci.runtime.profiles import PROFILES

    profile = PROFILES[spec.profile_id]
    parts = [profile.instructions, f"Objective: {snapshot.task.objective}"]
    if snapshot.grants.filesystem.write:
        parts.append("You may write inside the granted scopes.")
    else:
        parts.append("You have READ-ONLY authority: do not claim file mutations.")
    parts.append(_ACTION_PROTOCOL)
    return "\n".join(parts)
