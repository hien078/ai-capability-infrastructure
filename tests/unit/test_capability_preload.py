"""Run-start skill PRELOAD (user decision 2026-10-01): offered the
``request_capability`` tool, glm-5.3 made ZERO capability requests in 24
H-bench runs — the model never acquires skills unprompted. With
``preload_capabilities`` the kernel routes on the contract (objective +
constraints, never history) after RUN_STARTED and before the first turn, and
loads the selected skills through the SAME handler + commit path as a
model-initiated request.

Pinned here: the FIRST ModelRequest already carries the skill text; no
preload without an advertised plane (or with the option off); a preload
failure never fails the run and records only the exception TYPE; the
preload is not charged to the refresh budget (the model can still make its
own requests); one CAS commit; resume never preloads; the service setting /
per-request override / REST field reach the kernel.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from aci.adapters.inbound.rest.agent_run_wiring import (
    _NullCapabilityRuntime,
    build_agent_run_service,
)
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory, RunOptions
from aci.config import Settings
from aci.domain.runtime.actions import CapabilityRequest, FinalCandidate
from aci.domain.runtime.authority import ApprovalDecision
from aci.domain.runtime.spec import AgentProfileId
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.subtask import SubtaskContract
from aci.runtime.capability_runtime import CapabilityRuntime, CapabilitySearchError
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.event_bus import (
    CAPABILITY_LOADED,
    CAPABILITY_PRELOAD,
    RUN_STARTED,
    TURN_STARTED,
    EventBus,
)
from aci.runtime.model_gateway import FakeModelGateway, ModelRequest
from aci.runtime.profiles import runtime_spec_for
from aci.runtime.run_controller import HarnessKernel
from tests.unit.test_capability_runtime import FakeACI, FakeSelection
from tests.unit.test_capability_tool_call import (
    RUN_ID,
    SKILL_TEXT,
    FakeCapabilities,
    _batch,
    _cap_call,
    _kernel,
    _read,
    _skill_runtime,
    _tool_results,
)

OBJECTIVE = "fix the failing pytest in calc.py"
CONSTRAINTS = ["python", "no new dependencies"]


def _contract() -> SubtaskContract:
    return SubtaskContract(
        task_id=RUN_ID,
        objective=OBJECTIVE,
        constraints=CONSTRAINTS,
        global_context="earlier conversation: the user mentioned flaky CI",
        created_at=datetime.now(UTC),
    )


def _base_spec() -> Any:
    from aci.domain.runtime.authority import FilesystemScope, GrantEnvelope
    from aci.domain.runtime.evidence import ResultContract
    from aci.domain.runtime.spec import LoopPolicy, RuntimeSpec
    from aci.domain.runtime.state import BudgetLedger

    return RuntimeSpec(
        result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
        loop_policy=LoopPolicy(),
        budget=BudgetLedger(),
        initial_grants=GrantEnvelope(filesystem=FilesystemScope(read=["."])),
        created_at=datetime.now(UTC),
    )


def _run_plain(kernel: HarnessKernel, *, preload: bool = True) -> Any:
    return kernel.run(_contract(), _base_spec(), max_turns=10, preload_capabilities=preload)


def _system(request: ModelRequest) -> str:
    return "\n".join(m.content for m in request.messages if m.role == "system")


def _events(bus: EventBus, event_type: str) -> list[Any]:
    return [e for e in bus.history(RUN_ID) if e.event_type == event_type]


class _PreloadSeamHandler(FakeCapabilities):
    """A handler WITH the preload seam: records which path the kernel used."""

    advertised = True

    def __init__(self, outcome: Any = None) -> None:
        super().__init__(outcome)
        self.preloads: list[CapabilityRequest] = []

    def preload(self, request: CapabilityRequest, snapshot: object) -> list[Any]:
        self.preloads.append(request)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return list(self._outcome or [])


# -- the preload itself ----------------------------------------------------------


class TestPreload:
    def test_first_model_request_already_carries_the_skill(self) -> None:
        runtime, aci = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, state, tools = _kernel(model, runtime, events=bus)
        result = _run_plain(kernel)
        assert result.status is RunStatus.SUCCEEDED
        # Routed on the NORMALIZED NEED: objective + constraints, nothing else
        # (no global_context, no history, no reason text).
        assert [(r.objective, r.constraints, r.reason) for r in aci.search_calls] == [
            (OBJECTIVE, CONSTRAINTS, "")
        ]
        system = _system(model.requests[0])
        assert SKILL_TEXT.strip() in system
        assert "<<<BEGIN SKILL REFERENCE cap.pytest@1.2.0" in system
        # Lands in state exactly like a model-initiated activation.
        snapshot = state.snapshot(RUN_ID)
        assert [a.capability_id for a in snapshot.active_capabilities] == ["cap.pytest"]
        # No transcript entry, no tool, no turn spent on it.
        assert all(e.turn >= 1 for e in snapshot.transcript)
        assert result.usage.turns == 1
        assert tools.batches == [] and snapshot.budget.consumed_tool_calls == 0

    def test_events_order_and_shape(self) -> None:
        runtime, _ = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime, events=bus)
        _run_plain(kernel)
        types = [e.event_type for e in bus.history(RUN_ID)]
        started, first_turn = types.index(RUN_STARTED), types.index(TURN_STARTED)
        loaded_at = types.index(CAPABILITY_LOADED)
        preload_at = types.index(CAPABILITY_PRELOAD)
        assert started < loaded_at < preload_at < first_turn
        (loaded,) = _events(bus, CAPABILITY_LOADED)
        assert loaded.payload == {
            "capability_id": "cap.pytest",
            "version": "1.2.0",
            "preload": True,
        }
        (preload,) = _events(bus, CAPABILITY_PRELOAD)
        assert preload.payload == {"loaded": ["cap.pytest"], "count": 1}
        # ids only — never the skill text.
        assert "pytest triage" not in str(preload.payload) + str(loaded.payload)

    def test_off_by_default(self) -> None:
        runtime, aci = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, state, _ = _kernel(model, runtime, events=bus)
        kernel.run(_contract(), _base_spec(), max_turns=10)
        assert aci.search_calls == []
        assert SKILL_TEXT.strip() not in _system(model.requests[0])
        assert state.snapshot(RUN_ID).active_capabilities == []
        assert _events(bus, CAPABILITY_PRELOAD) == []

    def test_uses_the_handlers_preload_seam_when_present(self) -> None:
        handler = _PreloadSeamHandler([])
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, handler)
        _run_plain(kernel)
        assert [r.objective for r in handler.preloads] == [OBJECTIVE]
        assert handler.requests == []  # not routed through handle_request

    def test_handler_without_the_seam_is_asked_through_handle_request(self) -> None:
        handler = FakeCapabilities([])
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, handler)
        _run_plain(kernel)
        assert [r.objective for r in handler.requests] == [OBJECTIVE]

    def test_one_cas_commit_for_all_preloaded_activations(self) -> None:
        payloads = {"cap.a@1": b"# a\nskill a\n", "cap.b@1": b"# b\nskill b\n"}
        aci = FakeACI(
            [
                FakeSelection("cap.a", "1", hashlib.sha256(payloads["cap.a@1"]).hexdigest(), 20),
                FakeSelection("cap.b", "1", hashlib.sha256(payloads["cap.b@1"]).hexdigest(), 20),
            ],
            payloads,
        )
        runtime = CapabilityRuntime(aci)  # type: ignore[arg-type]
        seen: dict[str, int] = {}

        class _Spy:
            advertised = True

            def handle_request(self, request: Any, snapshot: Any) -> list[Any]:
                return runtime.handle_request(request, snapshot)

            def preload(self, request: Any, snapshot: Any) -> list[Any]:
                seen["before"] = snapshot.run.version
                return runtime.preload(request, snapshot)

        bus = EventBus()
        state_ref: list[Any] = []
        bus.subscribe(
            lambda e: (
                seen.__setitem__("after", state_ref[0].snapshot(RUN_ID).run.version)
                if e.event_type == CAPABILITY_PRELOAD
                else None
            )
        )
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, state, _ = _kernel(model, _Spy(), events=bus)
        state_ref.append(state)
        _run_plain(kernel)
        assert [a.capability_id for a in state.snapshot(RUN_ID).active_capabilities] == [
            "cap.a",
            "cap.b",
        ]
        assert seen["after"] - seen["before"] == 1  # both activations, ONE commit
        assert len(_events(bus, CAPABILITY_LOADED)) == 2

    def test_empty_bundle_records_count_zero_and_writes_nothing(self) -> None:
        handler = _PreloadSeamHandler([])
        bus = EventBus()
        seen: dict[str, int] = {}
        state_ref: list[Any] = []

        def _first(request: ModelRequest) -> FinalCandidate:
            return FinalCandidate(summary="done")

        def _version_at_preload(e: Any) -> None:
            if e.event_type == RUN_STARTED:
                seen["started"] = state_ref[0].snapshot(RUN_ID).run.version
            if e.event_type == CAPABILITY_PRELOAD:
                seen["preload"] = state_ref[0].snapshot(RUN_ID).run.version

        bus.subscribe(_version_at_preload)
        model = FakeModelGateway([_first])
        kernel, state, _ = _kernel(model, handler, events=bus)
        state_ref.append(state)
        assert _run_plain(kernel).status is RunStatus.SUCCEEDED
        (preload,) = _events(bus, CAPABILITY_PRELOAD)
        assert preload.payload == {"loaded": [], "count": 0}
        assert seen["preload"] == seen["started"]  # no commit at all
        assert _events(bus, CAPABILITY_LOADED) == []


# -- no plane, no preload ----------------------------------------------------------


class TestNotAdvertised:
    def test_null_runtime_never_preloads(self) -> None:
        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, _NullCapabilityRuntime(), events=bus)
        assert _run_plain(kernel).status is RunStatus.SUCCEEDED
        assert _events(bus, CAPABILITY_PRELOAD) == []

    def test_a_non_advertised_handler_is_never_called(self) -> None:
        class _Hidden(_PreloadSeamHandler):
            advertised = False

        handler = _Hidden([])
        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, handler, events=bus)
        _run_plain(kernel)
        assert handler.preloads == [] and handler.requests == []
        assert _events(bus, CAPABILITY_PRELOAD) == []

    def test_hbench_null_handler_never_preloads(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import run_hbench

        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, run_hbench._NullHandler(), events=bus)
        _run_plain(kernel)
        assert _events(bus, CAPABILITY_PRELOAD) == []


# -- best effort -------------------------------------------------------------------


class TestBestEffort:
    @pytest.mark.parametrize(
        "error",
        [
            CapabilitySearchError("registry down at 10.0.0.1"),
            RuntimeError("secret token sk-123 at /home/user/x"),
        ],
    )
    def test_failure_never_fails_the_run_and_records_the_type_only(self, error: Exception) -> None:
        handler = _PreloadSeamHandler(error)
        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, state, _ = _kernel(model, handler, events=bus)
        result = _run_plain(kernel)
        assert result.status is RunStatus.SUCCEEDED
        assert result.stop_reason is StopReason.SUCCESS
        assert len(model.requests) == 1  # the run went on to its first turn
        (preload,) = _events(bus, CAPABILITY_PRELOAD)
        assert preload.payload == {"loaded": [], "count": 0, "error": type(error).__name__}
        everything = "".join(str(e.payload) for e in bus.history(RUN_ID))
        for secret in ("10.0.0.1", "sk-123", "/home/user"):
            assert secret not in everything
            assert secret not in (result.detail_code or "") + result.summary
        assert state.snapshot(RUN_ID).active_capabilities == []

    def test_digest_mismatch_is_a_recorded_preload_failure(self) -> None:
        payload = SKILL_TEXT.encode()
        aci = FakeACI(
            [FakeSelection("cap.pytest", "1.2.0", "not-the-digest", 50)],
            {"cap.pytest@1.2.0": payload},
        )
        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, state, _ = _kernel(model, CapabilityRuntime(aci), events=bus)  # type: ignore[arg-type]
        assert _run_plain(kernel).status is RunStatus.SUCCEEDED
        (preload,) = _events(bus, CAPABILITY_PRELOAD)
        assert preload.payload["error"] == "CapabilitySearchError"
        assert SKILL_TEXT.strip() not in _system(model.requests[0])
        assert state.snapshot(RUN_ID).active_capabilities == []

    def test_max_loaded_zero_is_a_recorded_preload_failure(self) -> None:
        runtime, _ = _skill_runtime(max_loaded=0)
        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime, events=bus)
        assert _run_plain(kernel).status is RunStatus.SUCCEEDED
        (preload,) = _events(bus, CAPABILITY_PRELOAD)
        assert preload.payload["error"] == "CapabilitySearchError"


# -- refresh budget ----------------------------------------------------------------


class TestRefreshBudget:
    """Decision: a preload is the run's INITIAL load, not a mid-run refresh —
    it is not charged to ``max_refreshes``, so it never takes a request away
    from the model."""

    def test_model_can_still_request_after_a_preload_with_budget_one(self) -> None:
        runtime, aci = _skill_runtime(max_refreshes=1)
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime)
        result = _run_plain(kernel)
        assert result.status is RunStatus.SUCCEEDED, result
        assert len(aci.search_calls) == 2  # the preload + the model's own request
        assert _tool_results(model.requests[1])["k1"].startswith("Capability request result")

    def test_model_keeps_its_full_default_budget(self) -> None:
        runtime, aci = _skill_runtime()  # max_refreshes=2
        model = FakeModelGateway(
            [_batch(_cap_call("k1")), _batch(_cap_call("k2")), FinalCandidate(summary="done")]
        )
        kernel, _, _ = _kernel(model, runtime)
        assert _run_plain(kernel).status is RunStatus.SUCCEEDED
        assert len(aci.search_calls) == 3

    def test_the_refresh_budget_still_binds_the_model(self) -> None:
        runtime, aci = _skill_runtime(max_refreshes=1)
        model = FakeModelGateway(
            [_batch(_cap_call("k1")), _batch(_cap_call("k2")), FinalCandidate(summary="done")]
        )
        kernel, _, _ = _kernel(model, runtime)
        result = _run_plain(kernel)
        assert result.stop_reason is StopReason.CAPABILITY_UNAVAILABLE
        assert len(aci.search_calls) == 2

    def test_runtime_preload_is_once_per_run_and_uncharged(self) -> None:
        from tests.unit.test_capability_runtime import _snapshot

        runtime, aci = _skill_runtime(max_refreshes=1)
        snapshot = _snapshot()
        request = CapabilityRequest(objective="x")
        assert len(runtime.preload(request, snapshot)) == 1
        with pytest.raises(CapabilitySearchError, match="already preloaded"):
            runtime.preload(request, snapshot)
        assert len(runtime.handle_request(request, snapshot)) == 1  # budget untouched
        with pytest.raises(CapabilitySearchError, match="refresh budget"):
            runtime.handle_request(request, snapshot)
        assert len(aci.search_calls) == 2


# -- resume ------------------------------------------------------------------------


class TestResumeDoesNotPreload:
    def test_resumed_segment_keeps_the_activations_without_preloading_again(self) -> None:
        handler_runtime, aci = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway([_batch(_read())])
        kernel, _, tools = _kernel(model, handler_runtime, events=bus, approval_tools=("fs.read",))
        paused = _run_plain(kernel)
        assert paused.status is RunStatus.INTERRUPTED_APPROVAL
        assert len(aci.search_calls) == 1
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        assert [a.capability_id for a in checkpoint.snapshot.active_capabilities] == ["cap.pytest"]

        # A FRESH kernel (the restart case) with the same plane.
        bus2 = EventBus()
        model2 = FakeModelGateway([FinalCandidate(summary="done")])
        kernel2, _, _ = _kernel(model2, handler_runtime, events=bus2, approval_tools=("fs.read",))
        resumed = kernel2.resume(
            checkpoint,
            approval=ApprovalDecision(
                approval_id=paused.approval_id or "",
                approved=True,
                decided_by="client",
                decided_at=datetime.now(UTC),
            ),
        )
        assert resumed.status is RunStatus.SUCCEEDED, resumed
        assert len(aci.search_calls) == 1  # no second search
        assert _events(bus2, CAPABILITY_PRELOAD) == []
        assert _events(bus2, CAPABILITY_LOADED) == []
        # The restored activation is still rendered from state.
        assert SKILL_TEXT.strip() in _system(model2.requests[0])


# -- service + REST plumbing ---------------------------------------------------------


class _Factory:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def build(self) -> object:
        return self._obj


def _service(handler: object, *, default: bool, model: FakeModelGateway) -> AgentRunService:
    return AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _Factory(model)),
        tool_executor_factory=cast(ModelGatewayFactory, _Factory(FakeCapabilities())),
        capability_runtime_factory=cast(ModelGatewayFactory, _Factory(handler)),
        context_engine_factory=cast(
            ModelGatewayFactory, _Factory(ContextEngine(ContextBudget(total_tokens=60_000)))
        ),
        preload_capabilities=default,
    )


def _service_contract() -> SubtaskContract:
    return SubtaskContract(
        task_id=f"run_{uuid.uuid4().hex[:12]}",
        objective=OBJECTIVE,
        created_at=datetime.now(UTC),
    )


class TestServicePlumbing:
    @pytest.mark.parametrize(
        ("default", "override", "expected"),
        [
            (False, None, 0),
            (True, None, 1),
            (True, False, 0),
            (False, True, 1),
        ],
    )
    def test_setting_and_per_run_override(
        self, default: bool, override: bool | None, expected: int
    ) -> None:
        handler = _PreloadSeamHandler([])
        model = FakeModelGateway([FinalCandidate(summary="done")])
        service = _service(handler, default=default, model=model)
        service.run(
            _service_contract(),
            runtime_spec_for(AgentProfileId.RESEARCHER),
            preload_capabilities=override,
        )
        assert len(handler.preloads) == expected

    def test_revision_is_a_new_run_and_preloads_again(self) -> None:
        handler = _PreloadSeamHandler([])
        model = FakeModelGateway(
            [FinalCandidate(summary="first"), FinalCandidate(summary="second")]
        )
        service = _service(handler, default=True, model=model)
        first = service.run(_service_contract(), runtime_spec_for(AgentProfileId.RESEARCHER))
        service.revise(first.run_id, feedback="again")
        assert len(handler.preloads) == 2

    def test_run_options_round_trip_and_legacy_rows(self) -> None:
        options = RunOptions(preload_capabilities=True)
        assert options.to_json()["preload_capabilities"] is True
        assert RunOptions.from_json(options.to_json()) == options
        legacy = {k: v for k, v in RunOptions().to_json().items() if k != "preload_capabilities"}
        assert RunOptions.from_json(legacy).preload_capabilities is None
        with pytest.raises(ValueError, match="preload_capabilities"):
            RunOptions.from_json({"preload_capabilities": "yes"})

    def test_setting_defaults_off_and_reaches_the_service(self) -> None:
        assert Settings(agent_model_base_url="").agent_capability_preload is False
        on = build_agent_run_service(
            Settings(agent_model_base_url="", agent_capability_preload=True)
        )
        off = build_agent_run_service(Settings(agent_model_base_url=""))
        assert on._preload_capabilities is True  # noqa: SLF001
        assert off._preload_capabilities is False  # noqa: SLF001

    @pytest.mark.parametrize(("body_value", "expected"), [(True, 1), (False, 0), (None, 1)])
    def test_rest_field_overrides_the_server_default(
        self, body_value: bool | None, expected: int
    ) -> None:
        from tests.unit.test_agent_runs_rest import _body, _client

        handler = _PreloadSeamHandler([])
        model = FakeModelGateway([FinalCandidate(summary="done")])
        client = _client(_service(handler, default=True, model=model))
        body = _body(requested_profile="researcher")
        if body_value is not None:
            body["preload_capabilities"] = body_value
        response = client.post("/v1/agent-runs", json=body)
        assert response.status_code == 201, response.text
        assert len(handler.preloads) == expected
