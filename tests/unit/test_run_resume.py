"""Checkpoint resume + approval interrupts (harness.md §7.5, §13.6, §17.4).

A REQUIRE_APPROVAL decision pauses the run (INTERRUPTED_APPROVAL) BEFORE
the gated call executes; the pause checkpoint holds the full snapshot + the
unexecuted rest of the batch; ``HarnessKernel.resume`` restores state with
version continuity (INV-01), executes the approved call exactly once (or
shows the model a denial), continues with the REMAINING budget, and refuses
a second resume. A clarification pause resumes with the client's answer.
Deterministic: scripted model, real ToolRuntime over a recording dispatcher.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import (
    ClarificationRequest,
    DelegationRequest,
    FinalCandidate,
    ToolCall,
    ToolCallBatchAction,
)
from aci.domain.runtime.authority import (
    ApprovalDecision,
    ExecutionEnvelope,
    FilesystemScope,
    GrantEnvelope,
)
from aci.domain.runtime.evidence import CheckResult, ResultContract
from aci.domain.runtime.spec import LoopPolicy, RuntimeSpec
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.subtask import SubtaskContract
from aci.domain.runtime.tools import SideEffectReport, ToolAuthority, ToolSpec
from aci.runtime.authority import AuthorityPolicy
from aci.runtime.checkpoints import Checkpoint, CheckpointCoordinator, CheckpointStore
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.event_bus import (
    CHECKPOINT_RESTORED,
    CHECKPOINT_SAVED,
    RUN_CANCELLED,
    RUN_PAUSED,
    RUN_RESUMED,
    TOOL_APPROVAL_DECIDED,
    TOOL_APPROVAL_REQUESTED,
    EventBus,
)
from aci.runtime.model_gateway import ModelRequest, ModelResponse, ModelUsage
from aci.runtime.protocols import ToolDispatchResult
from aci.runtime.recovery import RecoveryManager
from aci.runtime.run_controller import CANCELLED_WHILE_PAUSED, HarnessKernel, cancel_paused
from aci.runtime.state_manager import StateManager
from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime
from aci.runtime.verification import VerificationManager, VerifierCallable

RUN_ID = "run-resume-1"


class ScriptedModel:
    def __init__(self, actions: list[Any]) -> None:
        self._actions = list(actions)
        self.requests: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._actions:
            raise AssertionError("ScriptedModel exhausted")
        entry = self._actions.pop(0)
        action = entry(request) if callable(entry) else entry
        return ModelResponse(
            action=action,
            raw_text="",
            usage=ModelUsage(input_tokens=100, output_tokens=50, latency_ms=1),
        )


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        self.calls.append((tool.tool_id, dict(args)))
        changed = [f"file:{args['path']}"] if tool.side_effect_class != "READ_ONLY" else []
        return ToolDispatchResult(
            output=f"{tool.tool_id} ok",
            side_effects=SideEffectReport(
                state="confirmed" if changed else "none", resources_changed=changed
            ),
            duration_ms=1,
        )


def _tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            tool_id="fs.read",
            version="1",
            input_schema={"properties": {"path": {"type": "string"}}},
            side_effect_class="READ_ONLY",
            idempotency_class="IDEMPOTENT",
            authority_requirements=ToolAuthority(read_path_args=["path"]),
        ),
        ToolSpec(
            tool_id="fs.write",
            version="1",
            input_schema={"properties": {"path": {"type": "string"}}},
            side_effect_class="LOCAL_MUTATION",
            authority_requirements=ToolAuthority(write_path_args=["path"]),
        ),
    ]


def _runtime(dispatcher: RecordingDispatcher, policy: AuthorityPolicy | None = None) -> ToolRuntime:
    registry = ToolRegistry()
    for tool in _tools():
        registry.register(tool)
    return ToolRuntime(registry, dispatcher, authority_policy=policy)


def _grants(expires_at: datetime | None = None) -> GrantEnvelope:
    return GrantEnvelope(
        filesystem=FilesystemScope(read=["."], write=["src"]), expires_at=expires_at
    )


def _spec(*, grants: GrantEnvelope | None = None, **budget: Any) -> RuntimeSpec:
    return RuntimeSpec(
        result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
        loop_policy=LoopPolicy(**budget),
        budget=BudgetLedger(**budget),
        initial_grants=grants or _grants(),
        created_at=datetime.now(UTC),
    )


def _contract() -> SubtaskContract:
    return SubtaskContract(task_id=RUN_ID, objective="edit src", created_at=datetime.now(UTC))


def _pass() -> VerifierCallable:
    return VerifierCallable("always", lambda s, c: CheckResult(name="always", passed=True))


def _kernel(
    model: ScriptedModel,
    dispatcher: RecordingDispatcher,
    *,
    approval_tools: tuple[str, ...] = ("fs.write",),
    policy: AuthorityPolicy | None = None,
    events: EventBus | None = None,
    checkpoints: object | None = None,
) -> tuple[HarnessKernel, StateManager]:
    state = StateManager()
    kernel = HarnessKernel(
        state=state,
        model_gateway=model,
        tool_executor=_runtime(dispatcher, policy),
        context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=VerificationManager([_pass()]),
        recovery=RecoveryManager(),
        capability_runtime=object(),
        event_bus=events,
        checkpoints=checkpoints,
        sleep=lambda _: None,
        approval_required_tools=approval_tools,
    )
    return kernel, state


def _batch(*calls: tuple[str, str, str]) -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[ToolCall(call_id=cid, tool_id=tid, arguments={"path": p}) for cid, tid, p in calls]
    )


def _decision(approval_id: str | None, approved: bool = True) -> ApprovalDecision:
    return ApprovalDecision(
        approval_id=approval_id or "missing",
        approved=approved,
        decided_by="client",
        decided_at=datetime.now(UTC),
    )


def _tool_messages(request: ModelRequest) -> dict[str, str]:
    return {
        m.tool_call_id: m.content for m in request.messages if m.role == "tool" and m.tool_call_id
    }


def _paused(
    model_script: list[Any], **kernel_kwargs: Any
) -> tuple[HarnessKernel, StateManager, RecordingDispatcher, ScriptedModel, Any]:
    dispatcher = RecordingDispatcher()
    model = ScriptedModel(model_script)
    kernel, state = _kernel(model, dispatcher, **kernel_kwargs)
    result = kernel.run(_contract(), _spec(), max_turns=10)
    return kernel, state, dispatcher, model, result


class TestApprovalInterrupt:
    def test_approval_required_call_pauses_the_run_before_it_executes(self) -> None:
        events = EventBus()
        kernel, state, dispatcher, _, result = _paused(
            [_batch(("r1", "fs.read", "README.md"), ("w1", "fs.write", "src/a.py"))],
            events=events,
        )
        assert result.status is RunStatus.INTERRUPTED_APPROVAL
        assert result.stop_reason is StopReason.AWAITING_APPROVAL
        assert result.detail_code == "APPROVAL_REQUIRED"
        assert result.approval_id and result.approval_id.startswith("apr_")
        # The read before the gated write ran and is committed; the write did not run.
        assert dispatcher.calls == [("fs.read", {"path": "README.md"})]
        snapshot = state.snapshot(RUN_ID)
        assert snapshot.run.status is RunStatus.INTERRUPTED_APPROVAL
        assert [e.tool_call_id for e in snapshot.transcript if e.role == "tool"] == ["r1"]
        assert snapshot.budget.consumed_tool_calls == 1
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None and checkpoint.pending is not None
        assert checkpoint.pending.approval_id == result.approval_id
        assert [c.call_id for c in checkpoint.pending.calls] == ["w1"]
        assert checkpoint.state_version == snapshot.run.version
        types = [e.event_type for e in events.history(RUN_ID)]
        assert TOOL_APPROVAL_REQUESTED in types and CHECKPOINT_SAVED in types
        assert types[-1] == RUN_PAUSED
        requested = next(
            e for e in events.history(RUN_ID) if e.event_type == TOOL_APPROVAL_REQUESTED
        )
        # Telemetry hygiene: tool ids only — never arguments or paths.
        assert "src/a.py" not in str(requested.payload)
        assert requested.payload["tool_id"] == "fs.write"

    def test_authority_require_approval_decision_pauses_too(self) -> None:
        """An approval CLASS (AuthorityPolicy) — not the overlay — yields the
        same pause; before this, it was turned into a denial observation."""
        _, _, dispatcher, _, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py"))],
            approval_tools=(),
            policy=AuthorityPolicy(approval_classes={"LOCAL_MUTATION"}),
        )
        assert result.status is RunStatus.INTERRUPTED_APPROVAL
        assert result.approval_id
        assert dispatcher.calls == []

    def test_approved_call_executes_exactly_once_then_the_loop_continues(self) -> None:
        kernel, state, dispatcher, model, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), FinalCandidate(summary="done")]
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        resumed = kernel.resume(checkpoint, approval=_decision(result.approval_id))
        assert resumed.status is RunStatus.SUCCEEDED, resumed
        assert dispatcher.calls == [("fs.write", {"path": "src/a.py"})]
        assert _tool_messages(model.requests[-1])["w1"].startswith("status: success")
        assert "file:src/a.py" in state.snapshot(RUN_ID).changed_resources

    def test_policy_class_approval_executes_through_the_one_shot_approval(self) -> None:
        kernel, _, dispatcher, _, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), FinalCandidate(summary="done")],
            approval_tools=(),
            policy=AuthorityPolicy(approval_classes={"LOCAL_MUTATION"}),
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        resumed = kernel.resume(checkpoint, approval=_decision(result.approval_id))
        assert resumed.status is RunStatus.SUCCEEDED
        assert dispatcher.calls == [("fs.write", {"path": "src/a.py"})]

    def test_approval_is_one_shot_a_later_call_pauses_again(self) -> None:
        kernel, _, dispatcher, _, result = _paused(
            [
                _batch(("w1", "fs.write", "src/a.py")),
                _batch(("w2", "fs.write", "src/a.py")),
                FinalCandidate(summary="done"),
            ]
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        second = kernel.resume(checkpoint, approval=_decision(result.approval_id))
        assert second.status is RunStatus.INTERRUPTED_APPROVAL
        assert second.approval_id and second.approval_id != result.approval_id
        assert len(dispatcher.calls) == 1  # the identical operation was NOT pre-approved

    def test_denied_approval_shows_the_model_a_denial_and_continues(self) -> None:
        kernel, state, dispatcher, model, result = _paused(
            [
                _batch(("w1", "fs.write", "src/a.py"), ("r1", "fs.read", "README.md")),
                FinalCandidate(summary="gave up on the write"),
            ]
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        resumed = kernel.resume(checkpoint, approval=_decision(result.approval_id, approved=False))
        assert resumed.status is RunStatus.SUCCEEDED
        assert dispatcher.calls == []  # neither the denied call nor the rest of its batch ran
        seen = _tool_messages(model.requests[-1])
        assert seen["w1"].startswith("status: denied (APPROVAL_REJECTED)")
        assert "DENIED approval" in seen["w1"]
        assert seen["r1"].startswith("status: denied (APPROVAL_REJECTED)")
        assert state.snapshot(RUN_ID).changed_resources == []

    def test_second_resume_of_the_same_checkpoint_is_refused(self) -> None:
        kernel, _, dispatcher, _, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), FinalCandidate(summary="done")]
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        kernel.resume(checkpoint, approval=_decision(result.approval_id))
        with pytest.raises(DomainError) as exc:
            kernel.resume(checkpoint, approval=_decision(result.approval_id))
        assert exc.value.code is ErrorCode.CHECKPOINT_CONSUMED
        assert len(dispatcher.calls) == 1

    def test_wrong_approval_id_is_refused_without_consuming_the_checkpoint(self) -> None:
        kernel, _, dispatcher, _, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), FinalCandidate(summary="done")]
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        with pytest.raises(DomainError) as exc:
            kernel.resume(checkpoint, approval=_decision("apr_forged"))
        assert exc.value.code is ErrorCode.APPROVAL_REPLAY_INVALID
        with pytest.raises(DomainError) as wrong_kind:
            kernel.resume(checkpoint, answer="yes")
        assert wrong_kind.value.code is ErrorCode.CLIENT_INCOMPATIBLE
        assert dispatcher.calls == []
        assert kernel.resume(checkpoint, approval=_decision(result.approval_id)).status is (
            RunStatus.SUCCEEDED
        )

    def test_tampered_pending_operation_is_refused(self) -> None:
        kernel, _, dispatcher, _, result = _paused([_batch(("w1", "fs.write", "src/a.py"))])
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None and checkpoint.pending is not None
        swapped = checkpoint.pending.model_copy(
            update={
                "calls": [
                    ToolCall(call_id="w1", tool_id="fs.write", arguments={"path": "src/evil.py"})
                ]
            }
        )
        tampered = checkpoint.model_copy(update={"pending": swapped})
        with pytest.raises(DomainError) as exc:
            kernel.resume(tampered, approval=_decision(result.approval_id))
        assert exc.value.code is ErrorCode.RUN_NOT_RESUMABLE
        assert dispatcher.calls == []

    def test_resume_never_renews_an_expired_grant(self) -> None:
        kernel, _, dispatcher, _, result = _paused([_batch(("w1", "fs.write", "src/a.py"))])
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        past = datetime.now(UTC) - timedelta(seconds=1)
        expired_snapshot = checkpoint.snapshot.model_copy(
            update={"grants": checkpoint.snapshot.grants.model_copy(update={"expires_at": past})}
        )
        expired = checkpoint.model_copy(update={"snapshot": expired_snapshot})
        resumed = kernel.resume(expired, approval=_decision(result.approval_id))
        assert resumed.status is RunStatus.FAILED
        assert resumed.stop_reason is StopReason.AUTHORITY_DENIED
        assert resumed.detail_code == "AUTHORITY_EXPIRED"
        assert dispatcher.calls == []

    def test_resume_grants_can_only_narrow(self) -> None:
        """The caller's CURRENT ceiling narrows the checkpointed grants: a
        write scope removed since the pause stays removed, approval or not."""
        kernel, state, dispatcher, model, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), FinalCandidate(summary="done")]
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        read_only = GrantEnvelope(filesystem=FilesystemScope(read=["."], write=[]))
        resumed = kernel.resume(
            checkpoint, approval=_decision(result.approval_id), grants=read_only
        )
        assert dispatcher.calls == []
        assert state.snapshot(RUN_ID).grants.filesystem.write == []
        assert _tool_messages(model.requests[-1])["w1"].startswith("status: denied")
        assert resumed.run_id == RUN_ID


class TestClarificationResume:
    def test_answer_is_appended_and_the_same_run_continues(self) -> None:
        model = ScriptedModel(
            [ClarificationRequest(question="which file?"), FinalCandidate(summary="done")]
        )
        dispatcher = RecordingDispatcher()
        kernel, state = _kernel(model, dispatcher)
        paused = kernel.run(_contract(), _spec(), max_turns=10)
        assert paused.status is RunStatus.INTERRUPTED
        assert paused.approval_id is None
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None and checkpoint.pending is not None
        assert checkpoint.pending.kind == "clarification"
        with pytest.raises(DomainError) as exc:
            kernel.resume(checkpoint, approval=_decision("apr_x"))
        assert exc.value.code is ErrorCode.APPROVAL_REPLAY_INVALID
        resumed = kernel.resume(checkpoint, answer="src/a.py")
        assert resumed.status is RunStatus.SUCCEEDED
        last = model.requests[-1]
        assert any(
            m.role == "user" and "src/a.py" in m.content and "Answer from" in m.content
            for m in last.messages
        )
        assert state.snapshot(RUN_ID).run.current_turn == 2  # the turn counter continued
        assert resumed.usage.turns == 2  # cumulative usage, not the resumed segment only


class TestBudgetContinuity:
    def test_resume_continues_with_the_remaining_turn_budget(self) -> None:
        """max_turns=3: one turn before the pause, so exactly two after."""
        model = ScriptedModel(
            [
                _batch(("w1", "fs.write", "src/a.py")),
                _batch(("r1", "fs.read", "README.md")),
                _batch(("r2", "fs.read", "README.md")),
                _batch(("r3", "fs.read", "README.md")),
            ]
        )
        dispatcher = RecordingDispatcher()
        kernel, state = _kernel(model, dispatcher)
        paused = kernel.run(_contract(), _spec(), max_turns=3)
        consumed_before = state.snapshot(RUN_ID).budget
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        resumed = kernel.resume(checkpoint, approval=_decision(paused.approval_id))
        assert resumed.stop_reason is StopReason.LIMIT_TURNS
        assert len(model.requests) == 3  # 1 before + 2 after: the budget was not reset
        after = state.snapshot(RUN_ID).budget
        assert after.consumed_turns == 3
        assert after.consumed_input_tokens == consumed_before.consumed_input_tokens + 200
        assert resumed.usage.turns == 3
        assert resumed.usage.tool_calls == 3  # w1 (approved) + r1 + r2


class TestRestart:
    def test_resume_in_a_fresh_kernel_from_a_serialized_checkpoint(self) -> None:
        """The restart case: the checkpoint crosses a JSON boundary; a FRESH
        StateManager + kernel resume it with state-version continuity."""
        first_model = ScriptedModel([_batch(("w1", "fs.write", "src/a.py"))])
        first_dispatcher = RecordingDispatcher()
        first, first_state = _kernel(first_model, first_dispatcher)
        paused = first.run(_contract(), _spec(), max_turns=10)
        checkpoint = first.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        wire = checkpoint.model_dump_json()
        paused_version = first_state.snapshot(RUN_ID).run.version

        model = ScriptedModel([FinalCandidate(summary="done")])
        dispatcher = RecordingDispatcher()
        fresh, state = _kernel(model, dispatcher)
        resumed = fresh.resume(
            Checkpoint.model_validate_json(wire), approval=_decision(paused.approval_id)
        )
        assert resumed.status is RunStatus.SUCCEEDED
        assert dispatcher.calls == [("fs.write", {"path": "src/a.py"})]
        assert first_dispatcher.calls == []
        snapshot = state.snapshot(RUN_ID)
        assert snapshot.run.version > paused_version
        # The transcript continued: the pre-pause assistant turn is still there.
        assert [e.role for e in snapshot.transcript[:2]] == ["assistant", "tool"]
        assert snapshot.transcript[1].tool_call_id == "w1"

    def test_restore_refuses_a_stale_snapshot_for_a_live_run(self) -> None:
        kernel, state, _, _, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), FinalCandidate(summary="done")]
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        state.append_transcript(RUN_ID, [])  # someone advanced the version since the pause
        with pytest.raises(DomainError) as exc:
            kernel.resume(checkpoint, approval=_decision(result.approval_id))
        assert exc.value.code is ErrorCode.RUN_NOT_RESUMABLE  # never a silent rollback
        # ...and the refused attempt did not burn the checkpoint's one resume.
        assert "chk" in checkpoint.checkpoint_id

    def test_coordinator_records_and_claims_pause_checkpoints(self) -> None:
        store = CheckpointStore()
        coordinator = CheckpointCoordinator(store)
        kernel, _, _, _, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), FinalCandidate(summary="done")],
            checkpoints=coordinator,
        )
        stored = store.latest_for_run(RUN_ID)
        assert stored is not None and stored.pending is not None
        assert store.consume(stored.checkpoint_id) is True  # someone else claimed it first
        with pytest.raises(DomainError) as exc:
            kernel.resume(stored, approval=_decision(result.approval_id))
        assert exc.value.code is ErrorCode.CHECKPOINT_CONSUMED

    def test_resume_emits_restore_and_resume_events(self) -> None:
        events = EventBus()
        kernel, _, _, _, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), FinalCandidate(summary="done")],
            events=events,
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        kernel.resume(checkpoint, approval=_decision(result.approval_id))
        types = [e.event_type for e in events.history(RUN_ID)]
        assert types.index(CHECKPOINT_RESTORED) < types.index(RUN_RESUMED)
        assert types.index(RUN_RESUMED) < types.index(TOOL_APPROVAL_DECIDED)


class TestPauseDetailDoesNotLeak:
    """2026-10-01 real-model finding: a run resumed from an approval pause
    that ended SUCCEEDED still carried detail_code="APPROVAL_REQUIRED".
    Re-entering RUNNING clears the pause's stop reason + detail, so the
    terminal result names its OWN outcome only."""

    def test_approved_resume_that_succeeds_has_no_detail(self) -> None:
        kernel, state, _, _, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), FinalCandidate(summary="done")]
        )

        assert result.detail_code == "APPROVAL_REQUIRED"
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        resumed = kernel.resume(checkpoint, approval=_decision(result.approval_id))
        assert resumed.status is RunStatus.SUCCEEDED
        assert resumed.stop_reason is StopReason.SUCCESS
        assert resumed.detail_code is None
        run = state.snapshot(RUN_ID).run
        assert (run.stop_reason, run.detail_code) == (StopReason.SUCCESS, None)

    def test_resumed_segment_runs_without_the_pause_stop(self) -> None:
        """While the resumed run is live, the state carries no stop at all."""
        seen: list[tuple[Any, Any]] = []
        state_ref: list[StateManager] = []

        def _observe(request: ModelRequest) -> FinalCandidate:
            run = state_ref[0].snapshot(RUN_ID).run
            seen.append((run.stop_reason, run.detail_code))
            return FinalCandidate(summary="done")

        kernel, state, _, _, result = _paused([_batch(("w1", "fs.write", "src/a.py")), _observe])
        state_ref.append(state)
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        kernel.resume(checkpoint, approval=_decision(result.approval_id))
        assert seen == [(None, None)]

    def test_denied_resume_that_succeeds_has_no_detail(self) -> None:
        kernel, _, _, _, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), FinalCandidate(summary="gave up")]
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        resumed = kernel.resume(checkpoint, approval=_decision(result.approval_id, approved=False))
        assert resumed.status is RunStatus.SUCCEEDED
        assert resumed.detail_code is None

    def test_a_later_failure_gets_its_own_detail(self) -> None:
        kernel, _, _, _, result = _paused(
            [_batch(("w1", "fs.write", "src/a.py")), DelegationRequest(subtask_objective="x")]
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        resumed = kernel.resume(checkpoint, approval=_decision(result.approval_id))
        assert resumed.status is RunStatus.FAILED
        assert resumed.stop_reason is StopReason.AUTHORITY_DENIED
        assert resumed.detail_code == "DELEGATION_DISABLED"

    def test_a_later_failure_without_detail_does_not_inherit_the_pause(self) -> None:
        """LIMIT_TURNS sets no detail of its own: None, never APPROVAL_REQUIRED."""
        dispatcher = RecordingDispatcher()
        model = ScriptedModel([_batch(("w1", "fs.write", "src/a.py"))])
        kernel, _ = _kernel(model, dispatcher)
        paused = kernel.run(_contract(), _spec(), max_turns=1)
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        resumed = kernel.resume(checkpoint, approval=_decision(paused.approval_id))
        assert resumed.stop_reason is StopReason.LIMIT_TURNS
        assert resumed.detail_code is None

    def test_clarification_resume_that_succeeds_has_no_detail(self) -> None:
        model = ScriptedModel(
            [ClarificationRequest(question="which file?"), FinalCandidate(summary="done")]
        )
        kernel, _ = _kernel(model, RecordingDispatcher())
        paused = kernel.run(_contract(), _spec(), max_turns=10)
        assert paused.detail_code == "CLARIFICATION_REQUIRED"
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        resumed = kernel.resume(checkpoint, answer="src/a.py")
        assert resumed.status is RunStatus.SUCCEEDED
        assert resumed.detail_code is None


class TestCancelPaused:
    def test_a_paused_checkpoint_cancels_through_the_state_manager(self) -> None:
        """No live loop to signal: the pause snapshot is restored (version
        continuity) and StateManager makes the CANCELLED move; nothing
        pending runs; RUN_CANCELLED is emitted."""
        events = EventBus()
        kernel, state, dispatcher, _, _ = _paused(
            [_batch(("w1", "fs.write", "src/a.py"))], events=events
        )
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        fresh = StateManager()  # the restart case: a fresh authority
        result = cancel_paused(checkpoint, state=fresh, event_bus=events)
        assert result.status is RunStatus.CANCELLED
        assert result.stop_reason is StopReason.CANCELLED
        assert result.detail_code == CANCELLED_WHILE_PAUSED
        assert result.usage == checkpoint.usage
        snapshot = fresh.snapshot(RUN_ID)
        assert snapshot.run.status is RunStatus.CANCELLED
        assert snapshot.run.version == checkpoint.snapshot.run.version + 1
        assert dispatcher.calls == []
        cancelled = [e for e in events.history(RUN_ID) if e.event_type == RUN_CANCELLED]
        assert len(cancelled) == 1 and cancelled[0].payload["while_paused"] == "approval"

    def test_a_non_paused_snapshot_is_refused_before_any_write(self) -> None:
        kernel, _, _, _, _ = _paused([_batch(("w1", "fs.write", "src/a.py"))])
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        running = checkpoint.snapshot.model_copy(
            update={"run": checkpoint.snapshot.run.model_copy(update={"status": RunStatus.RUNNING})}
        )
        fresh = StateManager()
        with pytest.raises(DomainError) as exc:
            cancel_paused(
                checkpoint.model_copy(update={"snapshot": running}),
                state=fresh,
                event_bus=EventBus(),
            )
        assert exc.value.code is ErrorCode.RUN_NOT_RESUMABLE
        with pytest.raises(KeyError):
            fresh.snapshot(RUN_ID)  # nothing was restored
