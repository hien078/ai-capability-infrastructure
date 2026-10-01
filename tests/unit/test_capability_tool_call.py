"""request_capability as a FUNCTION TOOL (2026-10-01 real-model finding: a
model that works through function calls — glm-5.3 — never emitted the
text-JSON capability_request action, so registry skills never loaded).

Pinned here: the synthetic tool is offered only when a capability plane is
advertised; a call to it goes through the SAME capability path as the
text-JSON action (handler, activation, CAPABILITY_LOADED, result note,
failure handling) and NEVER reaches ToolRuntime/AuthorityManager; the
transcript stays wire-valid for OpenAI tool calling (every tool_call id of
the assistant entry is answered by a role="tool" entry); mixed batches run
the real tools and answer request_capability "not processed — call it
alone"; the refresh budget still applies.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from aci.adapters.inbound.rest.agent_run_wiring import _NullCapabilityRuntime
from aci.domain.runtime.actions import (
    CapabilityRequest,
    FinalCandidate,
    ToolCall,
    ToolCallBatchAction,
)
from aci.domain.runtime.authority import ExecutionEnvelope, FilesystemScope, GrantEnvelope
from aci.domain.runtime.evidence import CheckResult, ResultContract
from aci.domain.runtime.spec import LoopPolicy, RuntimeSpec
from aci.domain.runtime.state import BudgetLedger, RuntimeStateSnapshot
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.subtask import SubtaskContract
from aci.domain.runtime.tools import SideEffectReport, ToolAuthority, ToolObservation, ToolSpec
from aci.runtime.capability_runtime import CapabilityRuntime, CapabilitySearchError
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.event_bus import CAPABILITY_LOADED, TOOL_REQUESTED, EventBus
from aci.runtime.model_gateway import (
    FakeModelGateway,
    FakeResponse,
    ModelMessage,
    ModelRequest,
    OpenAICompatGateway,
)
from aci.runtime.protocols import ToolDispatchResult
from aci.runtime.recovery import RecoveryManager
from aci.runtime.run_controller import (
    CAPABILITY_ARGUMENTS_INVALID,
    CAPABILITY_REQUEST_NOT_PROCESSED,
    CAPABILITY_TOOL_ID,
    HarnessKernel,
)
from aci.runtime.state_manager import StateManager
from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime
from aci.runtime.verification import VerificationManager, VerifierCallable
from tests.unit.test_capability_runtime import FakeACI, FakeSelection

RUN_ID = "run-cap-tool"
SKILL_TEXT = "# pytest triage\nRun the single failing test with -x first.\n"


# -- doubles -------------------------------------------------------------------


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        self.calls.append(tool.tool_id)
        return ToolDispatchResult(
            output=f"{tool.tool_id} ok", side_effects=SideEffectReport(), duration_ms=1
        )


class GuardedTools:
    """A real ToolRuntime (authority evaluated per call) behind a guard that
    FAILS the test if the synthetic tool ever reaches it — execution or the
    approval preflight (the AuthorityManager seam)."""

    def __init__(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                tool_id="fs.read",
                version="1",
                input_schema={"properties": {"path": {"type": "string"}}},
                idempotency_class="IDEMPOTENT",
                authority_requirements=ToolAuthority(read_path_args=["path"]),
            )
        )
        self.dispatcher = RecordingDispatcher()
        self.runtime = ToolRuntime(registry, self.dispatcher)
        self.batches: list[list[str]] = []

    @staticmethod
    def _guard(calls: list[ToolCall]) -> None:
        if any(c.tool_id == CAPABILITY_TOOL_ID for c in calls):
            raise AssertionError("request_capability reached ToolRuntime")

    def execute_batch(
        self,
        calls: list[ToolCall],
        *,
        snapshot: RuntimeStateSnapshot,
        envelope: ExecutionEnvelope,
        approved_call_ids: frozenset[str] = frozenset(),
    ) -> list[ToolObservation]:
        self._guard(calls)
        self.batches.append([c.tool_id for c in calls])
        return self.runtime.execute_batch(
            calls, snapshot=snapshot, envelope=envelope, approved_call_ids=approved_call_ids
        )

    def preflight(self, call: ToolCall, *, envelope: ExecutionEnvelope) -> Any:
        self._guard([call])
        return self.runtime.preflight(call, envelope=envelope)

    def available_tools(self) -> list[ToolSpec]:
        return self.runtime.available_tools()


class FakeCapabilities:
    def __init__(self, outcome: Any = None) -> None:
        self._outcome = outcome
        self.requests: list[CapabilityRequest] = []

    def handle_request(self, request: CapabilityRequest, snapshot: object) -> list[Any]:
        self.requests.append(request)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return list(self._outcome or [])


def _skill_runtime(**kwargs: int) -> tuple[CapabilityRuntime, FakeACI]:
    payload = SKILL_TEXT.encode()
    aci = FakeACI(
        [FakeSelection("cap.pytest", "1.2.0", hashlib.sha256(payload).hexdigest(), 50)],
        {"cap.pytest@1.2.0": payload},
    )
    return CapabilityRuntime(aci, **kwargs), aci  # type: ignore[arg-type]


def _kernel(
    model: Any,
    capabilities: Any,
    *,
    tools: GuardedTools | None = None,
    events: EventBus | None = None,
    approval_tools: tuple[str, ...] = (),
) -> tuple[HarnessKernel, StateManager, GuardedTools]:
    state = StateManager()
    tools = tools or GuardedTools()
    kernel = HarnessKernel(
        state=state,
        model_gateway=model,
        tool_executor=tools,
        context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=VerificationManager(
            [VerifierCallable("always", lambda s, c: CheckResult(name="always", passed=True))]
        ),
        recovery=RecoveryManager(),
        capability_runtime=capabilities,
        event_bus=events,
        sleep=lambda _s: None,
        approval_required_tools=approval_tools,
    )
    return kernel, state, tools


def _run(kernel: HarnessKernel) -> Any:
    spec = RuntimeSpec(
        result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
        loop_policy=LoopPolicy(),
        budget=BudgetLedger(),
        initial_grants=GrantEnvelope(filesystem=FilesystemScope(read=["."])),
        created_at=datetime.now(UTC),
    )
    contract = SubtaskContract(task_id=RUN_ID, objective="fix it", created_at=datetime.now(UTC))
    return kernel.run(contract, spec, max_turns=10)


def _cap_call(call_id: str = "k1", **arguments: Any) -> ToolCall:
    args = arguments or {"objective": "triage a failing pytest", "constraints": ["python"]}
    return ToolCall(call_id=call_id, tool_id=CAPABILITY_TOOL_ID, arguments=args)


def _read(call_id: str = "r1") -> ToolCall:
    return ToolCall(call_id=call_id, tool_id="fs.read", arguments={"path": "README.md"})


def _batch(*calls: ToolCall) -> ToolCallBatchAction:
    return ToolCallBatchAction(calls=list(calls))


def _text(request: ModelRequest) -> str:
    return "\n".join(m.content for m in request.messages)


def _tool_results(request: ModelRequest) -> dict[str, str]:
    return {
        m.tool_call_id: m.content for m in request.messages if m.role == "tool" and m.tool_call_id
    }


def assert_wire_valid(messages: list[ModelMessage]) -> None:
    """OpenAI tool calling: every assistant tool_call id is answered by a
    role="tool" message before the next non-tool message, and no tool
    message is orphaned."""
    pending: set[str] = set()
    for m in messages:
        if m.role == "tool":
            assert m.tool_call_id in pending, f"orphaned tool result {m.tool_call_id}"
            pending.discard(m.tool_call_id)
            continue
        assert not pending, f"unanswered tool_call ids {pending}"
        if m.role == "assistant":
            pending = {c.call_id for c in m.tool_calls}
    assert not pending, f"unanswered tool_call ids {pending}"


# -- advertisement -------------------------------------------------------------


def _offered(capabilities: Any) -> set[str]:
    model = FakeModelGateway([FinalCandidate(summary="done")])
    kernel, _, _ = _kernel(model, capabilities)
    _run(kernel)
    return {t.tool_id for t in model.requests[0].tools}


class TestAdvertisement:
    def test_offered_next_to_the_real_tools_when_a_plane_is_wired(self) -> None:
        offered = _offered(FakeCapabilities())
        assert offered == {"fs.read", CAPABILITY_TOOL_ID}

    def test_schema_requires_an_objective_and_grants_nothing(self) -> None:
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, FakeCapabilities())
        _run(kernel)
        spec = next(t for t in model.requests[0].tools if t.tool_id == CAPABILITY_TOOL_ID)
        assert spec.input_schema["required"] == ["objective"]
        assert set(spec.input_schema["properties"]) == {"objective", "constraints"}
        assert "never grants extra tools or permissions" in spec.description
        assert spec.authority_requirements == ToolAuthority()

    def test_not_offered_without_a_plane(self) -> None:
        assert CAPABILITY_TOOL_ID not in _offered(_NullCapabilityRuntime())

    def test_not_offered_by_the_hbench_null_handler(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import run_hbench

        assert CAPABILITY_TOOL_ID not in _offered(run_hbench._NullHandler())

    def test_not_offered_for_a_handler_that_cannot_handle_requests(self) -> None:
        assert CAPABILITY_TOOL_ID not in _offered(object())

    def test_without_a_plane_a_call_is_an_ordinary_unknown_tool(self) -> None:
        """advertised=False: nothing intercepts — ToolRuntime reports it."""

        class _Passthrough(GuardedTools):
            @staticmethod
            def _guard(calls: list[ToolCall]) -> None:
                return None

        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, tools = _kernel(model, _NullCapabilityRuntime(), tools=_Passthrough())
        assert _run(kernel).status is RunStatus.SUCCEEDED
        assert tools.batches == [[CAPABILITY_TOOL_ID]]
        assert "TOOL_NOT_FOUND" in _tool_results(model.requests[1])["k1"]


# -- the capability path -------------------------------------------------------


class TestCapabilityToolCall:
    def test_call_loads_the_skill_into_the_next_request(self) -> None:
        runtime, aci = _skill_runtime()
        events = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, state, tools = _kernel(model, runtime, events=events)
        result = _run(kernel)
        assert result.status is RunStatus.SUCCEEDED
        # The SAME request the text-JSON action would have produced.
        assert [(r.objective, r.constraints) for r in aci.search_calls] == [
            ("triage a failing pytest", ["python"])
        ]
        snapshot = state.snapshot(RUN_ID)
        assert [a.capability_id for a in snapshot.active_capabilities] == ["cap.pytest"]
        assert SKILL_TEXT.strip() not in _text(model.requests[0])
        system = "\n".join(m.content for m in model.requests[1].messages if m.role == "system")
        assert SKILL_TEXT.strip() in system
        assert "<<<BEGIN SKILL REFERENCE cap.pytest@1.2.0" in system
        loaded = [e for e in events.history(RUN_ID) if e.event_type == CAPABILITY_LOADED]
        assert [e.payload["capability_id"] for e in loaded] == ["cap.pytest"]
        # Not a tool: never executed, never charged, no TOOL_REQUESTED.
        assert tools.batches == [] and tools.dispatcher.calls == []
        assert snapshot.budget.consumed_tool_calls == 0
        assert TOOL_REQUESTED not in [e.event_type for e in events.history(RUN_ID)]

    def test_transcript_is_wire_valid(self) -> None:
        runtime, _ = _skill_runtime()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime)
        _run(kernel)
        messages = model.requests[1].messages
        assert_wire_valid(messages)
        assistant = next(m for m in messages if m.role == "assistant")
        assert [c.call_id for c in assistant.tool_calls] == ["k1"]
        result = _tool_results(model.requests[1])["k1"]
        assert result.startswith("Capability request result: loaded cap.pytest@1.2.0")

    def test_nothing_matched_is_answered_and_the_run_continues(self) -> None:
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, state, _ = _kernel(model, FakeCapabilities([]))
        assert _run(kernel).status is RunStatus.SUCCEEDED
        assert state.snapshot(RUN_ID).active_capabilities == []
        assert "nothing matched" in _tool_results(model.requests[1])["k1"]
        assert_wire_valid(model.requests[1].messages)

    def test_one_commit_per_turn_path(self) -> None:
        """CAS: activation + transcript land in ONE versioned commit."""
        runtime, _ = _skill_runtime()
        versions: list[int] = []
        state_ref: list[StateManager] = []

        def _observe(request: ModelRequest) -> FinalCandidate:
            versions.append(state_ref[0].snapshot(RUN_ID).run.version)
            return FinalCandidate(summary="done")

        def _first(request: ModelRequest) -> ToolCallBatchAction:
            versions.append(state_ref[0].snapshot(RUN_ID).run.version)
            return _batch(_cap_call())

        model = FakeModelGateway([_first, _observe])
        kernel, state, _ = _kernel(model, runtime)
        state_ref.append(state)
        _run(kernel)
        # turn 1 after the model call: consume_budget (+1), capability commit
        # (+1); turn 2 before the request: wall-time sync (+0/1), advance (+1).
        before, after = versions
        assert 3 <= after - before <= 4

    def test_extra_capability_calls_in_one_message_are_answered_not_processed(self) -> None:
        handler = FakeCapabilities([])
        model = FakeModelGateway(
            [_batch(_cap_call("k1"), _cap_call("k2")), FinalCandidate(summary="done")]
        )
        kernel, _, _ = _kernel(model, handler)
        assert _run(kernel).status is RunStatus.SUCCEEDED
        assert len(handler.requests) == 1  # one refresh, not two
        results = _tool_results(model.requests[1])
        assert "nothing matched" in results["k1"]
        assert CAPABILITY_REQUEST_NOT_PROCESSED in results["k2"]
        assert_wire_valid(model.requests[1].messages)

    def test_unusable_arguments_are_answered_without_a_search(self) -> None:
        handler = FakeCapabilities([])
        model = FakeModelGateway(
            [_batch(_cap_call(constraints=["x"])), FinalCandidate(summary="done")]
        )
        kernel, _, _ = _kernel(model, handler)
        assert _run(kernel).status is RunStatus.SUCCEEDED
        assert handler.requests == []
        assert CAPABILITY_ARGUMENTS_INVALID in _tool_results(model.requests[1])["k1"]
        assert_wire_valid(model.requests[1].messages)

    def test_bare_string_constraints_are_one_constraint(self) -> None:
        handler = FakeCapabilities([])
        model = FakeModelGateway(
            [_batch(_cap_call(objective="x", constraints="py")), FinalCandidate(summary="done")]
        )
        kernel, _, _ = _kernel(model, handler)
        _run(kernel)
        assert handler.requests[0].constraints == ["py"]


class TestFailures:
    def test_search_failure_ends_the_run_like_the_text_action(self) -> None:
        handler = FakeCapabilities(CapabilitySearchError("registry down at 10.0.0.1"))
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, tools = _kernel(model, handler)
        result = _run(kernel)
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.CAPABILITY_UNAVAILABLE
        assert result.detail_code == "CapabilitySearchError"
        assert "10.0.0.1" not in (result.detail_code or "") + result.summary
        assert len(model.requests) == 1
        assert tools.batches == []

    def test_refresh_budget_still_applies(self) -> None:
        runtime, aci = _skill_runtime(max_refreshes=1)
        model = FakeModelGateway(
            [_batch(_cap_call("k1")), _batch(_cap_call("k2")), FinalCandidate(summary="done")]
        )
        kernel, _, _ = _kernel(model, runtime)
        result = _run(kernel)
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.CAPABILITY_UNAVAILABLE
        assert result.detail_code == "CapabilitySearchError"
        assert len(aci.search_calls) == 1

    def test_max_loaded_still_applies(self) -> None:
        runtime, aci = _skill_runtime(max_loaded=0)
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, state, _ = _kernel(model, runtime)
        result = _run(kernel)
        assert result.stop_reason is StopReason.CAPABILITY_UNAVAILABLE
        assert state.snapshot(RUN_ID).active_capabilities == []
        assert aci.search_calls == []


# -- mixed batches + authority ---------------------------------------------------


class TestMixedBatch:
    def test_real_tools_run_and_the_capability_call_is_told_to_go_alone(self) -> None:
        handler = FakeCapabilities([])
        events = EventBus()
        model = FakeModelGateway(
            [_batch(_read("r1"), _cap_call("k1")), FinalCandidate(summary="done")]
        )
        kernel, state, tools = _kernel(model, handler, events=events)
        assert _run(kernel).status is RunStatus.SUCCEEDED
        assert handler.requests == []  # no refresh budget spent
        assert tools.batches == [["fs.read"]]
        assert tools.dispatcher.calls == ["fs.read"]
        assert state.snapshot(RUN_ID).budget.consumed_tool_calls == 1
        results = _tool_results(model.requests[1])
        assert results["r1"].startswith("status: success")
        assert CAPABILITY_REQUEST_NOT_PROCESSED in results["k1"]
        assert "ALONE" in results["k1"]
        assert_wire_valid(model.requests[1].messages)
        requested = next(e for e in events.history(RUN_ID) if e.event_type == TOOL_REQUESTED)
        assert requested.payload["calls"] == ["fs.read"]

    def test_capability_first_in_the_batch_is_the_same_rule(self) -> None:
        handler = FakeCapabilities([])
        model = FakeModelGateway(
            [_batch(_cap_call("k1"), _read("r1")), FinalCandidate(summary="done")]
        )
        kernel, _, tools = _kernel(model, handler)
        _run(kernel)
        assert handler.requests == [] and tools.batches == [["fs.read"]]
        assert_wire_valid(model.requests[1].messages)

    def test_an_approval_pause_never_carries_the_capability_call(self) -> None:
        """The pending (unexecuted) calls of a paused mixed batch hold the
        real tools only; the capability call is already answered."""
        model = FakeModelGateway([_batch(_cap_call("k1"), _read("r1"))])
        kernel, state, tools = _kernel(model, FakeCapabilities([]), approval_tools=("fs.read",))
        paused = _run(kernel)
        assert paused.status is RunStatus.INTERRUPTED_APPROVAL
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None and checkpoint.pending is not None
        assert [c.call_id for c in checkpoint.pending.calls] == ["r1"]
        answered = [e.tool_call_id for e in state.snapshot(RUN_ID).transcript if e.role == "tool"]
        assert answered == ["k1"]
        assert tools.batches == []


class TestAuthority:
    def test_never_reaches_the_approval_overlay_or_tool_runtime(self) -> None:
        """Even listed as approval-required, the synthetic tool is not a
        tool: no pause, no preflight, no ToolRuntime call (the guard raises)."""
        runtime, _ = _skill_runtime()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, state, tools = _kernel(model, runtime, approval_tools=(CAPABILITY_TOOL_ID,))
        result = _run(kernel)
        assert result.status is RunStatus.SUCCEEDED
        assert tools.batches == []
        # Grants are untouched by a loaded skill.
        assert state.snapshot(RUN_ID).grants == GrantEnvelope(
            filesystem=FilesystemScope(read=["."])
        )


# -- the real wire: OpenAI-compatible function calling ---------------------------


def _completion(message: dict[str, Any]) -> FakeResponse:
    body = {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}
    return FakeResponse(200, json.dumps(body))


class TestOpenAIWire:
    def test_function_call_from_the_provider_loads_the_skill(self) -> None:
        """The glm-5.3 shape: the model calls the advertised function; the
        follow-up body is a valid OpenAI transcript with the skill in it."""
        bodies: list[dict[str, Any]] = []
        replies = [
            _completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": CAPABILITY_TOOL_ID,
                                "arguments": json.dumps({"objective": "triage a failing pytest"}),
                            },
                        }
                    ],
                }
            ),
            _completion(
                {"role": "assistant", "content": '{"type": "final_candidate", "summary": "ok"}'}
            ),
        ]

        def transport(url: str, headers: dict[str, Any], json: dict[str, Any]) -> FakeResponse:
            bodies.append(json)
            return replies.pop(0)

        gateway = OpenAICompatGateway(
            base_url="http://gateway.local/v1", api_key="k", model_id="m", transport=transport
        )
        runtime, _ = _skill_runtime()
        kernel, state, _ = _kernel(gateway, runtime)
        assert _run(kernel).status is RunStatus.SUCCEEDED
        offered = [t["function"]["name"] for t in bodies[0]["tools"]]
        assert CAPABILITY_TOOL_ID in offered
        assert [a.capability_id for a in state.snapshot(RUN_ID).active_capabilities] == [
            "cap.pytest"
        ]
        follow_up = bodies[1]["messages"]
        assert SKILL_TEXT.strip() in follow_up[0]["content"]  # the system message
        call_ids = [
            c["id"] for m in follow_up if m["role"] == "assistant" for c in m.get("tool_calls", [])
        ]
        answered = [m["tool_call_id"] for m in follow_up if m["role"] == "tool"]
        assert call_ids == answered == ["call_abc"]
