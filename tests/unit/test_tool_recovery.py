"""Tool failures through the RecoveryManager (harness.md §12.6, §18).

Driven through the real HarnessKernel + ToolRuntime with a scripted model and
a counting fake dispatcher, so "retried" / "never retried" is proved by how
often the tool actually ran — not by what the controller says it did.

Classification under test (``aci.runtime.recovery.classify_tool_failure``):
infrastructure failures (TOOL_TIMEOUT, TRANSIENT_TOOL) go through recovery;
only a retry-safe tool (read-only AND declared idempotent) is re-run, with
backoff through the kernel's injectable ``sleep``; every other tool's failure
is surfaced to the model and recorded as a recovery decision (retry-block).
Deterministic failures, denials and unknown tools stay plain observations.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import FinalCandidate, ToolCall, ToolCallBatchAction
from aci.domain.runtime.authority import (
    ExecutionEnvelope,
    FilesystemScope,
    GrantEnvelope,
    NetworkScope,
    ProcessScope,
)
from aci.domain.runtime.evidence import CheckResult, ResultContract
from aci.domain.runtime.failures import FailureClass, FailureEnvelope
from aci.domain.runtime.spec import LoopPolicy, RuntimeSpec
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.subtask import SubtaskContract
from aci.domain.runtime.tools import ToolAuthority, ToolObservation, ToolSpec
from aci.runtime.cancellation import CancelToken
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.event_bus import RECOVERY_ACTION, TOOL_EXECUTION_FAILED, EventBus
from aci.runtime.model_gateway import ModelRequest, ModelResponse, ModelUsage
from aci.runtime.protocols import ToolDispatchResult
from aci.runtime.recovery import (
    RecoveryManager,
    classify_tool_failure,
    tool_retry_safe,
)
from aci.runtime.run_controller import HarnessKernel
from aci.runtime.state_manager import StateManager
from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime
from aci.runtime.verification import VerificationManager, VerifierCallable

RUN_ID = "run-tool-recovery"
GRANTS = GrantEnvelope(filesystem=FilesystemScope(read=["out"], write=["out"]))


class ScriptedModel:
    def __init__(self, actions: list[object]) -> None:
        self._actions = list(actions)
        self.requests: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._actions:
            raise AssertionError("scripted model exhausted — the kernel should have stopped")
        return ModelResponse(
            action=self._actions.pop(0),  # type: ignore[arg-type]
            usage=ModelUsage(input_tokens=10, output_tokens=5, latency_ms=1),
        )


class FlakyDispatcher:
    """Counts every dispatch per tool; raises the scripted exceptions in
    order for a tool, then succeeds."""

    def __init__(self, failures: dict[str, list[Exception]] | None = None) -> None:
        self._failures = {k: list(v) for k, v in (failures or {}).items()}
        self.calls: dict[str, int] = {}

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        self.calls[tool.tool_id] = self.calls.get(tool.tool_id, 0) + 1
        pending = self._failures.get(tool.tool_id)
        if pending:
            raise pending.pop(0)
        return ToolDispatchResult(output=f"{tool.tool_id} ok")


class AlwaysFails(FlakyDispatcher):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        self.calls[tool.tool_id] = self.calls.get(tool.tool_id, 0) + 1
        raise self._exc


def _tool(
    tool_id: str,
    side_effect: str = "READ_ONLY",
    idempotency: str = "IDEMPOTENT",
) -> ToolSpec:
    mutating = side_effect != "READ_ONLY" and side_effect != "PURE"
    return ToolSpec(
        tool_id=tool_id,
        version="1.0.0",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        side_effect_class=side_effect,  # type: ignore[arg-type]
        idempotency_class=idempotency,  # type: ignore[arg-type]
        authority_requirements=(
            ToolAuthority(write_path_args=["path"])
            if mutating
            else ToolAuthority(read_path_args=["path"])
        ),
    )


READ = _tool("fs.read")
WRITE = _tool("fs.write", "LOCAL_MUTATION", "NON_IDEMPOTENT")


def _kernel(
    model: ScriptedModel,
    dispatcher: FlakyDispatcher,
    tools: list[ToolSpec] | None = None,
    *,
    sleeps: list[float] | None = None,
    sleep: Any = None,
) -> tuple[HarnessKernel, StateManager, EventBus]:
    registry = ToolRegistry()
    for spec in tools or [READ, WRITE]:
        registry.register(spec)
    state = StateManager()
    bus = EventBus()
    recorded = sleeps if sleeps is not None else []
    kernel = HarnessKernel(
        state=state,
        model_gateway=model,
        tool_executor=ToolRuntime(registry, dispatcher=dispatcher),
        context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=VerificationManager(
            [VerifierCallable("always", lambda s, c: CheckResult(name="always", passed=True))]
        ),
        recovery=RecoveryManager(),
        capability_runtime=object(),  # type: ignore[arg-type]
        event_bus=bus,
        sleep=sleep or recorded.append,
    )
    return kernel, state, bus


def _contract() -> SubtaskContract:
    return SubtaskContract(task_id=RUN_ID, objective="inspect out/", created_at=datetime.now(UTC))


def _spec(**budget: Any) -> RuntimeSpec:
    return RuntimeSpec(
        result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
        loop_policy=LoopPolicy(**budget),
        budget=BudgetLedger(**budget),
        initial_grants=GRANTS,
        created_at=datetime.now(UTC),
    )


def _call(call_id: str, tool_id: str = "fs.read", path: str = "out/a.py") -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[ToolCall(call_id=call_id, tool_id=tool_id, arguments={"path": path})]
    )


def _recoveries(bus: EventBus) -> list[dict[str, object]]:
    return [dict(e.payload) for e in bus.history(RUN_ID) if e.event_type == RECOVERY_ACTION]


def _tool_results(state: StateManager) -> list[str]:
    return [e.content for e in state.snapshot(RUN_ID).transcript if e.role == "tool"]


DONE = FinalCandidate(summary="done")


class TestRetrySafeToolRetried:
    @pytest.mark.parametrize(
        ("exc", "failure_class"),
        [
            (ConnectionResetError("reset by peer at /srv/x"), "TRANSIENT_TOOL"),
            (TimeoutError("slow"), "TOOL_TIMEOUT"),
        ],
    )
    def test_transient_read_failure_is_retried_then_succeeds(
        self, exc: Exception, failure_class: str
    ) -> None:
        dispatcher = FlakyDispatcher({"fs.read": [exc]})
        sleeps: list[float] = []
        model = ScriptedModel([_call("c1"), DONE])
        kernel, state, bus = _kernel(model, dispatcher, sleeps=sleeps)
        result = kernel.run(_contract(), _spec())

        assert result.status is RunStatus.SUCCEEDED
        assert dispatcher.calls["fs.read"] == 2  # failed once, re-run once
        assert sleeps == [1.0]  # backoff through the injectable sleep
        # The model sees ONE result for its one call — the successful one.
        assert _tool_results(state) == ["status: success\nfs.read ok"]
        assert _recoveries(bus) == [
            {
                "failure_class": failure_class,
                "action": "RETRY_SAME",
                "component": "tool_runtime",
                "tool_id": "fs.read",
                "attempt": 1,
            }
        ]
        budget = state.snapshot(RUN_ID).budget
        assert budget.consumed_recoveries == 1  # §18.4: recovery consumes budget
        assert budget.consumed_tool_calls == 2  # the retry is a real execution
        assert result.usage.tool_calls == 2
        # INV-15: the failed attempt is telemetry too.
        failed = [e for e in bus.history(RUN_ID) if e.event_type == TOOL_EXECUTION_FAILED]
        assert len(failed) == 1

    def test_backoff_grows_between_retries(self) -> None:
        dispatcher = FlakyDispatcher({"fs.read": [TimeoutError(), TimeoutError()]})
        sleeps: list[float] = []
        model = ScriptedModel([_call("c1"), DONE])
        kernel, _, _ = _kernel(model, dispatcher, sleeps=sleeps)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert dispatcher.calls["fs.read"] == 3
        assert sleeps == [1.0, 2.0]


class TestRetryBlock:
    @pytest.mark.parametrize(
        ("side_effect", "idempotency"),
        [
            ("LOCAL_MUTATION", "NON_IDEMPOTENT"),  # edit_file
            ("LOCAL_MUTATION", "UNKNOWN"),  # run_command
            ("LOCAL_MUTATION", "IDEMPOTENT"),  # write_file: mutating ⇒ never auto-rerun
            ("EXTERNAL_MUTATION", "IDEMPOTENT_WITH_KEY"),
            ("READ_ONLY", "NON_IDEMPOTENT"),
            ("READ_ONLY", "UNKNOWN"),  # undeclared idempotency ⇒ no blind retry
        ],
    )
    def test_non_retry_safe_tool_failure_is_never_rerun(
        self, side_effect: str, idempotency: str
    ) -> None:
        tool = _tool("fs.op", side_effect, idempotency)
        dispatcher = FlakyDispatcher({"fs.op": [ConnectionResetError("reset")]})
        sleeps: list[float] = []
        model = ScriptedModel([_call("c1", "fs.op"), DONE])
        kernel, state, bus = _kernel(model, dispatcher, [tool], sleeps=sleeps)
        result = kernel.run(_contract(), _spec())

        assert dispatcher.calls["fs.op"] == 1  # the counter proves: no re-run
        assert sleeps == []
        assert result.status is RunStatus.SUCCEEDED  # the run goes on: the model replans
        # Recorded as a recovery decision, charged to the budget.
        assert [(p["failure_class"], p["action"]) for p in _recoveries(bus)] == [
            ("TRANSIENT_TOOL", "REPLAN")
        ]
        assert state.snapshot(RUN_ID).budget.consumed_recoveries == 1
        # Surfaced to the model as an observation, with the retry-block note.
        [observation] = _tool_results(state)
        assert observation.startswith("status: error (TRANSIENT_TOOL)")
        assert "not retried automatically" in observation
        assert "ConnectionResetError" in observation

    def test_mutating_failure_reports_a_possible_side_effect(self) -> None:
        runtime = ToolRuntime(_registry(WRITE), dispatcher=AlwaysFails(TimeoutError()))
        obs = runtime.execute(
            ToolCall(call_id="c1", tool_id="fs.write", arguments={"path": "out/a.py"}),
            snapshot=None,  # type: ignore[arg-type]
            envelope=_envelope(),
        )
        assert obs.error_class == "TOOL_TIMEOUT"
        assert obs.side_effects.state == "possible"
        decision = RecoveryManager().decide_tool(_failure(obs), tool=WRITE)
        assert decision.action == "REPLAN"


class TestRecoveryBudgetTerminates:
    def test_persistent_transient_read_failure_terminates_the_run(self) -> None:
        """max_same_failure_retries=2: two retries, the third failure
        escalates — the run ENDS (TOOL_FAILURE), it does not loop."""
        dispatcher = AlwaysFails(ConnectionResetError("reset"))
        model = ScriptedModel([_call("c1"), DONE])
        kernel, state, bus = _kernel(model, dispatcher)
        result = kernel.run(_contract(), _spec())

        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.TOOL_FAILURE
        assert result.detail_code == "TRANSIENT_TOOL"
        assert dispatcher.calls["fs.read"] == 3
        assert len(model.requests) == 1  # never went back to the model
        assert [p["action"] for p in _recoveries(bus)] == ["RETRY_SAME", "RETRY_SAME", "ESCALATE"]
        snap = state.snapshot(RUN_ID)
        assert snap.budget.consumed_recoveries == 3
        # The failed observation is still committed (no lost history).
        assert [e.tool_call_id for e in snap.transcript if e.role == "tool"] == ["c1"]

    def test_run_recovery_budget_caps_tool_retries(self) -> None:
        dispatcher = AlwaysFails(ConnectionResetError("reset"))
        model = ScriptedModel([_call("c1"), DONE])
        kernel, state, bus = _kernel(model, dispatcher)
        result = kernel.run(_contract(), _spec(max_recoveries=1))

        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.TOOL_FAILURE
        assert dispatcher.calls["fs.read"] == 2
        # The over-budget decision is recorded as the FAIL it became.
        assert [p["action"] for p in _recoveries(bus)] == ["RETRY_SAME", "FAIL"]
        assert state.snapshot(RUN_ID).budget.consumed_recoveries == 2

    def test_repeated_mutating_failures_across_turns_terminate(self) -> None:
        dispatcher = AlwaysFails(ConnectionResetError("reset"))
        model = ScriptedModel([_call(f"c{i}", "fs.write") for i in range(5)] + [DONE])
        kernel, _, bus = _kernel(model, dispatcher)
        result = kernel.run(_contract(), _spec())

        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.TOOL_FAILURE
        assert dispatcher.calls["fs.write"] == 3  # one execution per turn, never re-run
        assert len(model.requests) == 3
        assert [p["action"] for p in _recoveries(bus)] == ["REPLAN", "REPLAN", "ESCALATE"]

    def test_retry_never_exceeds_the_tool_call_budget(self) -> None:
        dispatcher = FlakyDispatcher({"fs.read": [ConnectionResetError("reset")]})
        sleeps: list[float] = []
        model = ScriptedModel([_call("c1"), DONE])
        kernel, state, _ = _kernel(model, dispatcher, sleeps=sleeps)
        result = kernel.run(_contract(), _spec(max_tool_calls=1))

        assert dispatcher.calls["fs.read"] == 1
        assert sleeps == []
        assert state.snapshot(RUN_ID).budget.consumed_tool_calls == 1
        assert result.status is RunStatus.SUCCEEDED  # finalizing needs no tool budget

    def test_retry_keeps_room_for_the_rest_of_the_batch(self) -> None:
        dispatcher = FlakyDispatcher({"fs.read": [ConnectionResetError("reset")]})
        batch = ToolCallBatchAction(
            calls=[
                ToolCall(call_id="c1", tool_id="fs.read", arguments={"path": "out/a.py"}),
                ToolCall(call_id="c2", tool_id="fs.read", arguments={"path": "out/b.py"}),
            ]
        )
        model = ScriptedModel([batch, DONE])
        kernel, state, _ = _kernel(model, dispatcher)
        kernel.run(_contract(), _spec(max_tool_calls=2))
        assert dispatcher.calls["fs.read"] == 2  # c1 not retried: c2 still had to fit
        assert state.snapshot(RUN_ID).budget.consumed_tool_calls == 2


class TestPlainObservations:
    @pytest.mark.parametrize(
        ("exc", "error_class"),
        [
            (
                DomainError(ErrorCode.TOOL_ARGUMENT_INVALID, "old_string occurs 0 times"),
                "TOOL_INVALID_ARGUMENT",
            ),
            (
                DomainError(ErrorCode.TOOL_EXECUTION_FAILED, "No such file: 'out/a.py'"),
                "TOOL_EXECUTION_FAILED",
            ),
            (RuntimeError("internal /srv/path"), "TOOL_EXECUTION_FAILED"),
        ],
    )
    def test_deterministic_failure_is_feedback_not_recovery(
        self, exc: Exception, error_class: str
    ) -> None:
        dispatcher = FlakyDispatcher({"fs.read": [exc]})
        model = ScriptedModel([_call("c1"), DONE])
        kernel, state, bus = _kernel(model, dispatcher)
        result = kernel.run(_contract(), _spec())

        assert result.status is RunStatus.SUCCEEDED
        assert dispatcher.calls["fs.read"] == 1
        assert _recoveries(bus) == []
        assert state.snapshot(RUN_ID).budget.consumed_recoveries == 0
        [observation] = _tool_results(state)
        assert observation.startswith(f"status: error ({error_class})")
        assert "/srv/path" not in observation  # raw exception text never reaches the model

    def test_authority_denial_and_unknown_tool_are_plain_observations(self) -> None:
        dispatcher = FlakyDispatcher()
        model = ScriptedModel([_call("c1", "fs.write", "secrets/x"), _call("c2", "fs.nope"), DONE])
        kernel, state, bus = _kernel(model, dispatcher)
        result = kernel.run(_contract(), _spec())

        assert result.status is RunStatus.SUCCEEDED
        assert dispatcher.calls == {}
        assert _recoveries(bus) == []
        denied, unknown = _tool_results(state)
        assert denied.startswith("status: denied (AUTHORITY_DENIED)")
        assert unknown.startswith("status: error (TOOL_NOT_FOUND)")


class TestCancellation:
    def test_cancel_during_backoff_commits_and_stops(self) -> None:
        token = CancelToken(run_id=RUN_ID)
        dispatcher = FlakyDispatcher({"fs.read": [ConnectionResetError("reset")]})
        model = ScriptedModel([_call("c1"), DONE])
        kernel, state, _ = _kernel(model, dispatcher, sleep=lambda _s: token.cancel())
        result = kernel.run(_contract(), _spec(), cancel_token=token)

        assert result.status is RunStatus.CANCELLED
        assert dispatcher.calls["fs.read"] == 1  # no retry after the cancel
        assert [e.tool_call_id for e in state.snapshot(RUN_ID).transcript if e.role == "tool"] == [
            "c1"
        ]


class TestClassification:
    def test_classify_tool_failure_table(self) -> None:
        def obs(status: str, error_class: str | None) -> ToolObservation:
            return ToolObservation(
                tool_call_id="c",
                tool_id="t",
                status=status,  # type: ignore[arg-type]
                error_class=error_class,
            )

        assert classify_tool_failure(obs("timeout", "TOOL_TIMEOUT")) is FailureClass.TOOL_TIMEOUT
        assert classify_tool_failure(obs("error", "TRANSIENT_TOOL")) is FailureClass.TRANSIENT_TOOL
        for status, code in [
            ("success", None),
            ("error", "TOOL_INVALID_ARGUMENT"),
            ("error", "TOOL_EXECUTION_FAILED"),
            ("error", "TOOL_NOT_FOUND"),
            ("denied", "AUTHORITY_DENIED"),
            ("denied", "AUTHORITY_EXPIRED"),
            ("denied", "APPROVAL_REQUIRED"),
            ("blocked", "GUARDRAIL_BLOCKED"),
        ]:
            assert classify_tool_failure(obs(status, code)) is None, code

    def test_retry_safety(self) -> None:
        assert tool_retry_safe(READ)
        assert tool_retry_safe(_tool("t", "PURE", "IDEMPOTENT_WITH_KEY"))
        assert not tool_retry_safe(None)
        assert not tool_retry_safe(WRITE)
        assert not tool_retry_safe(_tool("t", "LOCAL_MUTATION", "IDEMPOTENT"))
        assert not tool_retry_safe(_tool("t", "READ_ONLY", "UNKNOWN"))

    def test_decide_tool_blocks_retry_even_when_policy_says_retry(self) -> None:
        """A permissive matrix override cannot re-enable blind retries of a
        mutating tool: the retry-block is applied after the policy lookup."""
        failure = FailureEnvelope(
            failure_id="f",
            failure_class=FailureClass.WORKSPACE_UNAVAILABLE,  # matrix: RETRY_BACKOFF
            component="tool_runtime",
            message="x",
        )
        assert RecoveryManager().decide_tool(failure, tool=WRITE).action == "REPLAN"
        assert RecoveryManager().decide_tool(failure, tool=READ).action == "RETRY_BACKOFF"


def _registry(*specs: ToolSpec) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in specs:
        registry.register(spec)
    return registry


def _envelope() -> ExecutionEnvelope:
    return ExecutionEnvelope(
        run_id=RUN_ID,
        workspace_id="ws",
        filesystem=FilesystemScope(read=["out"], write=["out"]),
        network=NetworkScope(),
        process=ProcessScope(),
    )


def _failure(obs: ToolObservation) -> FailureEnvelope:
    cls = classify_tool_failure(obs)
    assert cls is not None
    return FailureEnvelope(
        failure_id="f",
        failure_class=cls,
        component="tool_runtime",
        message=obs.error_class or cls.value,
        side_effect_state=obs.side_effects.state,
    )
