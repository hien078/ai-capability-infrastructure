"""HarnessKernel deterministic simulation tests (§48.3): fake model + fake
tools drive the exact lifecycle — turn → tool → verify → complete — and the
run loop's state/recovery/context behaviour, asserted through public state."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import (
    CapabilityRequest,
    ClarificationRequest,
    ContinueAction,
    DelegationRequest,
    FinalCandidate,
    PlanUpdateRequest,
    ToolCall,
    ToolCallBatchAction,
)
from aci.domain.runtime.evidence import CheckResult, ResultContract
from aci.domain.runtime.spec import LoopPolicy, RuntimeSpec
from aci.domain.runtime.state import BudgetLedger, CapabilityActivation, RuntimeStateSnapshot
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.subtask import SubtaskContract
from aci.domain.runtime.tools import SideEffectReport, ToolObservation, ToolSpec
from aci.runtime.cancellation import CancelToken
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.model_gateway import ModelMessage, ModelRequest, ModelResponse, ModelUsage
from aci.runtime.recovery import RecoveryManager
from aci.runtime.run_controller import HarnessKernel
from aci.runtime.state_manager import StateManager
from aci.runtime.verification import VerificationManager, VerifierCallable

RUN_ID = "run-sim-1"


class ScriptedModel:
    """Fake ModelGateway: records every request, pops scripted entries in
    order. An entry that is an Exception is raised (gateway failure); a
    callable receives the request. Exhausting the script is a test bug."""

    def __init__(self, actions: list[Any]) -> None:
        self._actions = list(actions)
        self.requests: list[ModelRequest] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._actions:
            raise AssertionError("ScriptedModel exhausted: the kernel asked for more turns")
        entry = self._actions.pop(0)
        if isinstance(entry, Exception):
            raise entry
        action = entry(request) if callable(entry) else entry
        return ModelResponse(
            action=action,
            raw_text="",
            usage=ModelUsage(input_tokens=100, output_tokens=50, latency_ms=10),
        )


ObservationFactory = Callable[[ToolCall], ToolObservation]


def _ok(call: ToolCall) -> ToolObservation:
    return ToolObservation(tool_call_id=call.call_id, tool_id=call.tool_id, summary="ok")


class FakeTools:
    """Fake ToolExecutor: one observation per call from ``observe``; an
    optional ``on_batch`` hook runs before the observations are returned."""

    def __init__(
        self,
        observe: ObservationFactory = _ok,
        *,
        on_batch: Callable[[list[ToolCall]], None] | None = None,
    ) -> None:
        self.batches: list[list[ToolCall]] = []
        self._observe = observe
        self._on_batch = on_batch

    def execute_batch(
        self, calls: list[ToolCall], *, snapshot: object, envelope: object
    ) -> list[ToolObservation]:
        self.batches.append(list(calls))
        if self._on_batch is not None:
            self._on_batch(list(calls))
        return [self._observe(c) for c in calls]

    def available_tools(self) -> list[ToolSpec]:
        return []


class FakeCapabilities:
    def __init__(self, activations: list[CapabilityActivation] | Exception) -> None:
        self._activations = activations
        self.requests: list[CapabilityRequest] = []

    def handle_request(
        self, request: CapabilityRequest, snapshot: RuntimeStateSnapshot
    ) -> list[CapabilityActivation]:
        self.requests.append(request)
        if isinstance(self._activations, Exception):
            raise self._activations
        return list(self._activations)


class FakeCheckpoints:
    def __init__(self) -> None:
        self.saved: list[tuple[str, RuntimeStateSnapshot]] = []

    def save(self, snapshot: RuntimeStateSnapshot, *, checkpoint_id: str) -> None:
        self.saved.append((checkpoint_id, snapshot))


def _contract(**overrides: Any) -> SubtaskContract:
    fields: dict[str, Any] = {
        "task_id": RUN_ID,
        "objective": "fix the failing test",
        "acceptance_criteria": [],
        "created_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return SubtaskContract(**fields)


def _spec(**budget: Any) -> RuntimeSpec:
    """RuntimeSpec whose loop policy and ledger agree on the given limits."""
    return RuntimeSpec(
        result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
        loop_policy=LoopPolicy(**budget),
        budget=BudgetLedger(**budget),
        created_at=datetime.now(UTC),
    )


def _pass() -> VerifierCallable:
    return VerifierCallable("always", lambda s, c: CheckResult(name="always", passed=True))


def _failing(detail: str = "1 failed") -> VerifierCallable:
    return VerifierCallable(
        "tests", lambda s, c: CheckResult(name="tests", passed=False, detail=detail)
    )


def _kernel(
    model: ScriptedModel,
    tools: FakeTools | None = None,
    checks: list[VerifierCallable] | None = None,
    *,
    state: StateManager | None = None,
    context_engine: ContextEngine | None = None,
    capability_runtime: object | None = None,
    sleep: Callable[[float], None] | None = None,
    checkpoints: object | None = None,
    checkpoint_interval_turns: int = 5,
) -> tuple[HarnessKernel, StateManager]:
    state = state or StateManager()
    kernel = HarnessKernel(
        state=state,
        model_gateway=model,
        tool_executor=tools or FakeTools(),
        context_engine=context_engine or ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=VerificationManager(checks or [_pass()]),
        recovery=RecoveryManager(),
        capability_runtime=capability_runtime or object(),
        checkpoints=checkpoints,
        checkpoint_interval_turns=checkpoint_interval_turns,
        sleep=sleep or (lambda _: None),
    )
    return kernel, state


def _messages(request: ModelRequest, role: str) -> list[ModelMessage]:
    return [m for m in request.messages if m.role == role]


def _has_text(request: ModelRequest, text: str, *, role: str | None = None) -> bool:
    return any(text in m.content for m in request.messages if role is None or m.role == role)


def _tool_call(call_id: str, tool_id: str = "fs.read", **arguments: Any) -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[ToolCall(call_id=call_id, tool_id=tool_id, arguments=arguments)]
    )


class TestHarnessKernel:
    def test_tool_then_verified_success(self) -> None:
        model = ScriptedModel(
            [
                ToolCallBatchAction(calls=[ToolCall(call_id="c1", tool_id="fs.read")]),
                FinalCandidate(summary="fixed the bug", changes=["src/x.py"]),
            ]
        )
        tools = FakeTools()
        kernel, state = _kernel(model, tools)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert result.stop_reason is StopReason.SUCCESS
        assert result.summary == "fixed the bug"
        assert result.usage.turns == 2
        assert result.usage.tool_calls == 1
        assert len(tools.batches) == 1
        assert state.snapshot(RUN_ID).run.status is RunStatus.SUCCEEDED

    def test_verification_failure_blocks_success(self) -> None:
        """INV-08 + §19.6: a rejected candidate is fed back as a repair turn;
        the same failure repeated past the recovery bound ends the run FAILED."""
        model = ScriptedModel([FinalCandidate(summary="done") for _ in range(3)])
        kernel, _ = _kernel(model, checks=[_failing("1 failed")])
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.VERIFICATION_FAILED
        assert model.calls == 3
        for request in model.requests[1:]:
            feedback = [m for m in _messages(request, "user") if "Verification FAILED" in m.content]
            assert feedback, "the verifier's verdict must reach the model"
            assert "check 'tests' failed: 1 failed" in feedback[-1].content
        assert not _has_text(model.requests[0], "Verification FAILED")

    def test_turn_limit_stops_with_reason(self) -> None:
        model = ScriptedModel([ContinueAction() for _ in range(50)])
        kernel, _ = _kernel(model)
        result = kernel.run(_contract(), _spec(), max_turns=3)
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.LIMIT_TURNS
        assert result.usage.turns == 3

    def test_cancellation_mid_run(self) -> None:
        model = ScriptedModel([ContinueAction() for _ in range(50)])
        kernel, _ = _kernel(model)
        token = CancelToken(run_id=RUN_ID)
        # Cancel after the first model call via a wrapper model.
        original_invoke = model.invoke

        def _cancel_then_invoke(request: ModelRequest) -> ModelResponse:
            result = original_invoke(request)
            token.cancel()
            return result

        model.invoke = _cancel_then_invoke  # type: ignore[method-assign]
        result = kernel.run(_contract(), _spec(), cancel_token=token)
        # §23.1: a cancelled run returns CANCELLED — the exception never leaks.
        assert result.status is RunStatus.CANCELLED
        assert result.stop_reason is StopReason.CANCELLED

    def test_state_lifecycle_transitions_recorded(self) -> None:
        model = ScriptedModel([FinalCandidate(summary="ok")])
        kernel, state = _kernel(model)
        kernel.run(_contract(), _spec())
        snap = state.snapshot(RUN_ID)
        assert snap.run.started_at is not None
        assert snap.run.completed_at is not None
        assert snap.run.version >= 5  # created→init→ready→running→verifying→succeeded


class TestTranscriptIsState:
    def test_tool_turn_lives_in_snapshot_and_next_request(self) -> None:
        """INV-01: the conversation is run state, not a kernel attribute; the
        next request is re-assembled from it with tool-call/result binding."""

        def observe(call: ToolCall) -> ToolObservation:
            if call.call_id == "c1":
                return ToolObservation(
                    tool_call_id="c1", tool_id="fs.read", inline_output="print('hi')"
                )
            return ToolObservation(
                tool_call_id="c2",
                tool_id="fs.write",
                status="denied",
                error_class="AUTHORITY_DENIED",
                summary="write outside grants",
            )

        model = ScriptedModel(
            [
                ToolCallBatchAction(
                    calls=[
                        ToolCall(call_id="c1", tool_id="fs.read", arguments={"path": "a.py"}),
                        ToolCall(call_id="c2", tool_id="fs.write", arguments={"path": "b.py"}),
                    ]
                ),
                FinalCandidate(summary="done"),
            ]
        )
        kernel, state = _kernel(model, FakeTools(observe))
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert not hasattr(kernel, "_history")

        transcript = state.snapshot(RUN_ID).transcript
        assistant = [e for e in transcript if e.role == "assistant"]
        assert len(assistant) == 1
        assert [c.call_id for c in assistant[0].tool_calls] == ["c1", "c2"]
        tool_entries = [e for e in transcript if e.role == "tool"]
        assert [e.tool_call_id for e in tool_entries] == ["c1", "c2"]
        assert tool_entries[0].content.startswith("status: success")
        assert "print('hi')" in tool_entries[0].content
        assert tool_entries[1].content.startswith("status: denied (AUTHORITY_DENIED)")
        assert "write outside grants" in tool_entries[1].content

        second = model.requests[1]
        assistant_msgs = _messages(second, "assistant")
        assert len(assistant_msgs) == 1
        assert [c.call_id for c in assistant_msgs[0].tool_calls] == ["c1", "c2"]
        tool_msgs = _messages(second, "tool")
        assert [m.tool_call_id for m in tool_msgs] == ["c1", "c2"]
        assert tool_msgs[0].content.startswith("status: success")
        assert tool_msgs[1].content.startswith("status: denied (AUTHORITY_DENIED)")
        # The first request had no history yet.
        assert not _messages(model.requests[0], "assistant")
        assert not _messages(model.requests[0], "tool")


class TestContextBudget:
    def test_oldest_groups_dropped_whole_within_budget(self) -> None:
        """INV-09: the newest transcript groups that fit are sent; older
        groups are omitted whole (never an orphaned tool result) and the
        omission is announced; harness-observed progress stays pinned."""
        big = "x" * 1_000

        def observe(call: ToolCall) -> ToolObservation:
            return ToolObservation(
                tool_call_id=call.call_id,
                tool_id="fs.write",
                inline_output=big,
                side_effects=SideEffectReport(
                    state="confirmed", resources_changed=[f"file:src/{call.call_id}.py"]
                ),
            )

        turns = 6
        model = ScriptedModel(
            [_tool_call(f"c{i}", "fs.write", path=f"src/c{i}.py") for i in range(1, turns + 1)]
            + [FinalCandidate(summary="done")]
        )
        kernel, _ = _kernel(
            model,
            FakeTools(observe),
            context_engine=ContextEngine(ContextBudget(total_tokens=1_500)),
        )
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert model.calls == turns + 1

        last = model.requests[-1]
        omitted = [
            m for m in _messages(last, "user") if "earlier turn(s) were omitted" in m.content
        ]
        assert len(omitted) == 1
        assert omitted[0].content.startswith("[context] ")
        tool_ids = [m.tool_call_id for m in _messages(last, "tool")]
        assert "c1" not in tool_ids
        assert f"c{turns}" in tool_ids
        assert not any(c.call_id == "c1" for m in last.messages for c in m.tool_calls)

        # Group integrity across every request: an assistant tool-call message
        # is always followed by a tool message per call id.
        for request in model.requests:
            msgs = request.messages
            for index, m in enumerate(msgs):
                if m.role != "assistant" or not m.tool_calls:
                    continue
                following = [x.tool_call_id for x in msgs[index + 1 :] if x.role == "tool"]
                for call in m.tool_calls:
                    assert call.call_id in following, f"orphaned tool call {call.call_id}"

        # The pinned progress summary carries the writes the harness observed.
        progress = [m for m in _messages(last, "system") if "Files changed so far" in m.content]
        assert len(progress) == 1
        assert "src/c1.py" in progress[0].content
        assert not _has_text(model.requests[0], "Files changed so far")
        assert not _has_text(model.requests[0], "earlier turn(s) were omitted")


class TestRecovery:
    def test_transient_model_error_retried_with_backoff(self) -> None:
        sleeps: list[float] = []
        model = ScriptedModel(
            [
                DomainError(ErrorCode.MODEL_UNAVAILABLE, "503"),
                DomainError(ErrorCode.MODEL_UNAVAILABLE, "503"),
                FinalCandidate(summary="done"),
            ]
        )
        kernel, state = _kernel(model, sleep=sleeps.append)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert result.stop_reason is StopReason.SUCCESS
        assert len(sleeps) == 2
        assert sleeps[0] < sleeps[1]
        # One provider-capacity STREAK (consecutive failures of one model
        # call) is one recovery, however many retries it took.
        assert state.snapshot(RUN_ID).budget.consumed_recoveries == 1
        assert result.usage.turns == 1

    def test_transient_model_error_escalates_after_a_long_streak(self) -> None:
        """A provider that stays down ends the run — bounded: 6 consecutive
        retries with exponential backoff (2 s doubling, capped 60 s, jitter
        in [0.75, 1]), the 7th consecutive failure escalates."""
        sleeps: list[float] = []
        model = ScriptedModel([DomainError(ErrorCode.MODEL_UNAVAILABLE, "503") for _ in range(20)])
        kernel, _ = _kernel(model, sleep=sleeps.append)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.MODEL_FAILURE
        assert result.detail_code == "TRANSIENT_MODEL"
        assert len(sleeps) == 6
        assert model.calls == 7
        for i, slept in enumerate(sleeps):
            ceiling = min(60.0, 2.0 * 2**i)
            assert 0.75 * ceiling <= slept <= ceiling, (i, slept)
        assert sleeps == sorted(sleeps)

    def test_sporadic_provider_errors_do_not_kill_a_long_run(self) -> None:
        """2026-10-03 (g-e2d Bp@20, 32/42 rows MODEL_FAILURE): the
        same-class counter was RUN-WIDE, so the 3rd transient gateway error
        of a run's whole life escalated, even with successful calls in
        between. A successful model call ends the streak: sporadic errors
        separated by successes never accumulate toward escalation."""
        sleeps: list[float] = []
        err = DomainError(ErrorCode.MODEL_UNAVAILABLE, "503")
        model = ScriptedModel(
            [err, ContinueAction(), err, ContinueAction(), err, ContinueAction(), err]
            + [FinalCandidate(summary="done")]
        )
        kernel, state = _kernel(model, sleep=sleeps.append)
        result = kernel.run(_contract(), _spec(max_turns=10))
        assert result.status is RunStatus.SUCCEEDED, result.stop_reason
        assert len(sleeps) == 4
        assert state.snapshot(RUN_ID).budget.consumed_recoveries == 4

    def test_rate_limited_streak_also_backs_off(self) -> None:
        sleeps: list[float] = []
        err = DomainError(ErrorCode.RATE_LIMITED, "429")
        model = ScriptedModel([err, err, err, err, FinalCandidate(summary="done")])
        kernel, state = _kernel(model, sleep=sleeps.append)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert len(sleeps) == 4
        assert state.snapshot(RUN_ID).budget.consumed_recoveries == 1

    def test_malformed_output_gets_a_repair_turn(self) -> None:
        sleeps: list[float] = []
        model = ScriptedModel(
            [
                DomainError(ErrorCode.MODEL_MALFORMED_OUTPUT, "bad json"),
                FinalCandidate(summary="done"),
            ]
        )
        kernel, _ = _kernel(model, sleep=sleeps.append)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert sleeps == []
        assert model.calls == 2
        repair = [
            m for m in _messages(model.requests[1], "user") if "could not be used" in m.content
        ]
        assert len(repair) == 1
        assert "bad json" in repair[0].content
        assert not _has_text(model.requests[0], "could not be used")

    def test_unclassified_exception_is_fatal_never_retried(self) -> None:
        sleeps: list[float] = []
        model = ScriptedModel([RuntimeError("boom"), FinalCandidate(summary="done")])
        kernel, _ = _kernel(model, sleep=sleeps.append)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.MODEL_FAILURE
        assert result.detail_code == "FATAL"
        assert sleeps == []
        assert model.calls == 1

    def test_recovery_budget_zero_makes_first_failure_terminal(self) -> None:
        model = ScriptedModel([FinalCandidate(summary="try 1"), FinalCandidate(summary="try 2")])
        kernel, state = _kernel(model, checks=[_failing()])
        result = kernel.run(_contract(), _spec(max_recoveries=0))
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.VERIFICATION_FAILED
        assert model.calls == 1
        assert state.snapshot(RUN_ID).budget.consumed_recoveries == 1


class TestStateCommit:
    def test_concurrent_write_during_batch_is_a_conflict(self) -> None:
        """§8.5 CAS: another writer touching the run mid-batch must surface as
        a conflict — the batch's observations are never silently committed."""
        state = StateManager()

        def touch(calls: list[ToolCall]) -> None:
            state.consume_budget(RUN_ID, tool_calls=0, wall_time_seconds=0.001)

        model = ScriptedModel([_tool_call("c1"), FinalCandidate(summary="done")])
        kernel, _ = _kernel(model, FakeTools(on_batch=touch), state=state)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.FATAL_ERROR
        assert result.detail_code == "StateCommitConflict"
        snap = state.snapshot(RUN_ID)
        assert snap.run.status is RunStatus.FAILED
        assert not [e for e in snap.transcript if e.role == "tool"]
        assert not [e for e in snap.transcript if e.tool_calls]
        assert model.calls == 1


class TestOtherActions:
    def test_continue_action_nudges_and_varies_the_request(self) -> None:
        model = ScriptedModel([ContinueAction(), ContinueAction(), FinalCandidate(summary="ok")])
        kernel, _ = _kernel(model)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert result.usage.turns == 3
        first, second = model.requests[0], model.requests[1]
        assert second.messages != first.messages
        assert _has_text(second, "No action was taken", role="user")
        assert not _has_text(first, "No action was taken")

    def test_plan_update_is_committed_and_pinned_in_context(self) -> None:
        model = ScriptedModel(
            [
                PlanUpdateRequest(
                    items=[{"objective": "read config"}, {"objective": "fix", "status": "bogus"}]
                ),
                FinalCandidate(summary="ok"),
            ]
        )
        kernel, state = _kernel(model)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        plan = state.snapshot(RUN_ID).plan
        assert [p.item_id for p in plan] == ["p1", "p2"]
        assert [p.objective for p in plan] == ["read config", "fix"]
        assert plan[1].status == "pending"
        assert _has_text(model.requests[1], "read config", role="system")
        assert not _has_text(model.requests[0], "read config")

    def test_clarification_interrupts_the_run(self) -> None:
        model = ScriptedModel([ClarificationRequest(question="which branch?")])
        kernel, state = _kernel(model)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.INTERRUPTED
        assert result.stop_reason is StopReason.INTERRUPTED
        assert result.detail_code == "CLARIFICATION_REQUIRED"
        assert "which branch?" in result.summary
        assert result.usage.turns == 1
        assert state.snapshot(RUN_ID).run.status is RunStatus.INTERRUPTED

    def test_delegation_is_disabled_by_default(self) -> None:
        model = ScriptedModel([DelegationRequest(subtask_objective="write the tests")])
        kernel, _ = _kernel(model)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.AUTHORITY_DENIED
        assert result.detail_code == "DELEGATION_DISABLED"

    def test_global_context_reaches_the_first_request(self) -> None:
        feedback = "Previous attempt failed criteria: X. Feedback: use Y"
        model = ScriptedModel([FinalCandidate(summary="ok")])
        kernel, _ = _kernel(model)
        kernel.run(_contract(global_context=feedback), _spec())
        assert _has_text(model.requests[0], feedback, role="system")


class TestCapabilityRequests:
    @staticmethod
    def _activation() -> CapabilityActivation:
        return CapabilityActivation(
            capability_id="cap.pytest", version="1.2.0", digest="sha256:abc", activation_id="act-1"
        )

    def test_loaded_capability_is_state_and_context(self) -> None:
        runtime = FakeCapabilities([self._activation()])
        model = ScriptedModel(
            [CapabilityRequest(objective="run tests"), FinalCandidate(summary="ok")]
        )
        kernel, state = _kernel(model, capability_runtime=runtime)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        active = state.snapshot(RUN_ID).active_capabilities
        assert [(a.capability_id, a.version) for a in active] == [("cap.pytest", "1.2.0")]
        assert len(runtime.requests) == 1
        assert _has_text(
            model.requests[1], "Capability request result: loaded cap.pytest@1.2.0", role="user"
        )

    def test_nothing_matched_is_reported(self) -> None:
        model = ScriptedModel(
            [CapabilityRequest(objective="run tests"), FinalCandidate(summary="ok")]
        )
        kernel, state = _kernel(model, capability_runtime=FakeCapabilities([]))
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert state.snapshot(RUN_ID).active_capabilities == []
        assert _has_text(model.requests[1], "nothing matched", role="user")

    def test_runtime_failure_ends_the_run_with_type_only(self) -> None:
        model = ScriptedModel(
            [CapabilityRequest(objective="run tests"), FinalCandidate(summary="ok")]
        )
        runtime = FakeCapabilities(RuntimeError("registry down at 10.0.0.1"))
        kernel, _ = _kernel(model, capability_runtime=runtime)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.CAPABILITY_UNAVAILABLE
        assert result.detail_code == "RuntimeError"
        assert "10.0.0.1" not in (result.detail_code or "") + result.summary
        assert model.calls == 1


class TestBudgetsAndCheckpoints:
    def test_wall_time_is_real_elapsed_time(self) -> None:
        """§7.6: tool time counts; the ceiling is enforced at the next preflight."""

        def slow(calls: list[ToolCall]) -> None:
            time.sleep(1.1)

        model = ScriptedModel([_tool_call("c1"), FinalCandidate(summary="ok")])
        kernel, state = _kernel(model, FakeTools(on_batch=slow))
        result = kernel.run(_contract(), _spec(max_wall_time_seconds=1))
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.LIMIT_WALL_TIME
        assert model.calls == 1
        assert state.snapshot(RUN_ID).budget.consumed_wall_time_seconds >= 1.0

    def test_periodic_checkpoint_carries_the_transcript(self) -> None:
        checkpoints = FakeCheckpoints()
        model = ScriptedModel(
            [_tool_call("c1"), _tool_call("c2"), _tool_call("c3"), FinalCandidate(summary="ok")]
        )
        kernel, _ = _kernel(model, checkpoints=checkpoints, checkpoint_interval_turns=2)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert [cid for cid, _ in checkpoints.saved] == [
            f"chk-{RUN_ID}-turn-2",
            f"chk-{RUN_ID}-turn-4",
        ]
        turn2 = checkpoints.saved[0][1]
        assert [e.role for e in turn2.transcript] == ["assistant", "tool"]
        assert turn2.transcript[1].tool_call_id == "c1"
        turn4 = checkpoints.saved[1][1]
        assert [e.tool_call_id for e in turn4.transcript if e.role == "tool"] == [
            "c1",
            "c2",
            "c3",
        ]

    def test_token_limit_is_bounded_by_remaining_output_budget(self) -> None:
        model = ScriptedModel([FinalCandidate(summary="ok")])
        kernel, _ = _kernel(model)
        kernel.run(_contract(), _spec(max_output_tokens=100))
        assert model.requests[0].token_limit == 100

    def test_token_limit_defaults_to_per_request_cap(self) -> None:
        model = ScriptedModel([FinalCandidate(summary="ok")])
        kernel, _ = _kernel(model)
        kernel.run(_contract(), _spec())
        assert model.requests[0].token_limit == 8_192
