"""HarnessKernel deterministic simulation tests (§48.3): fake model + fake
tools drive the exact lifecycle — turn → tool → verify → complete."""

from datetime import UTC, datetime

from aci.domain.runtime.actions import (
    ContinueAction,
    FinalCandidate,
    ToolCall,
    ToolCallBatchAction,
)
from aci.domain.runtime.evidence import CheckResult, ResultContract
from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.subtask import SubtaskContract
from aci.domain.runtime.tools import ToolObservation
from aci.runtime.cancellation import CancelToken
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.recovery import RecoveryManager
from aci.runtime.run_controller import HarnessKernel
from aci.runtime.state_manager import StateManager
from aci.runtime.verification import VerificationManager, VerifierCallable


class ScriptedModel:
    """Fake ModelGateway: pops scripted actions in order."""

    def __init__(self, actions: list[object]) -> None:
        self._actions = list(actions)
        self.calls = 0

    def invoke(self, request: object) -> object:
        from aci.runtime.model_gateway import ModelResponse, ModelUsage

        return ModelResponse(
            action=self._actions.pop(0),
            usage=ModelUsage(input_tokens=100, output_tokens=50, latency_ms=10),
        )


class FakeTools:
    def __init__(self) -> None:
        self.batches: list[list[ToolCall]] = []

    def execute_batch(
        self, calls: list[ToolCall], *, snapshot: object, envelope: object
    ) -> list[ToolObservation]:
        self.batches.append(list(calls))
        return [
            ToolObservation(tool_call_id=c.call_id, tool_id=c.tool_id, summary="ok") for c in calls
        ]


def _contract() -> SubtaskContract:
    return SubtaskContract(
        task_id="run-sim-1",
        objective="fix the failing test",
        acceptance_criteria=[],
        created_at=datetime.now(UTC),
    )


def _spec() -> RuntimeSpec:
    return RuntimeSpec(
        result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
        budget=BudgetLedger(),
        created_at=datetime.now(UTC),
    )


def _kernel(
    model: ScriptedModel,
    tools: FakeTools,
    checks: list[VerifierCallable] | None = None,
) -> tuple[HarnessKernel, StateManager]:
    state = StateManager()
    verifier = VerificationManager(
        checks or [VerifierCallable("always", lambda s, c: CheckResult(name="always", passed=True))]
    )
    kernel = HarnessKernel(
        state=state,
        model_gateway=model,
        tool_executor=tools,
        context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=verifier,
        recovery=RecoveryManager(),
        capability_runtime=object(),
    )
    return kernel, state


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
        assert state.snapshot("run-sim-1").run.status is RunStatus.SUCCEEDED

    def test_verification_failure_blocks_success(self) -> None:
        model = ScriptedModel([FinalCandidate(summary="done")])
        failing_check = VerifierCallable(
            "tests", lambda s, c: CheckResult(name="tests", passed=False, detail="1 failed")
        )
        kernel, _ = _kernel(model, FakeTools(), checks=[failing_check])
        result = kernel.run(_contract(), _spec())
        # INV-08: model claimed done, verifier failed → never SUCCEEDED.
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.VERIFICATION_FAILED

    def test_turn_limit_stops_with_reason(self) -> None:
        model = ScriptedModel([ContinueAction() for _ in range(50)])
        kernel, _ = _kernel(model, FakeTools())
        result = kernel.run(_contract(), _spec(), max_turns=3)
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.LIMIT_TURNS
        assert result.usage.turns == 3

    def test_cancellation_mid_run(self) -> None:
        model = ScriptedModel([ContinueAction() for _ in range(50)])
        kernel, _ = _kernel(model, FakeTools())
        token = CancelToken(run_id="run-sim-1")
        # Cancel after the first model call via a wrapper model.
        original_invoke = model.invoke

        def _cancel_then_invoke(request: dict) -> object:
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
        kernel, state = _kernel(model, FakeTools())
        kernel.run(_contract(), _spec())
        snap = state.snapshot("run-sim-1")
        assert snap.run.started_at is not None
        assert snap.run.completed_at is not None
        assert snap.run.version >= 5  # created→init→ready→running→verifying→succeeded
