"""Capability activation ORIGIN tracking (m1-exposure-origin, 2026-10-01).

Independent-review limitation: when a skill is PRELOADED at run start and the
model later requests the SAME skill (the text-JSON capability_request action
or the request_capability tool), the two loads were indistinguishable — one
deterministic activation_id (sha(run_id:(id,version))), StateManager dedupe
by capability_id last-write-wins, and the terminal capability.exposure could
not tell preloaded from model-requested: origin was lost.

Pinned here:
- The KERNEL tags each load with its origin ("preload" / "model_request") on
  the activation (``CapabilityActivation.origins``); a re-activation of the
  same capability keeps ONE entry whose origins ACCUMULATE — the latest
  route/bundle provenance wins, the origin history is never dropped and
  never duplicated.
- CAPABILITY_LOADED carries ``origin`` (the label of THIS load event); the
  terminal capability.exposure carries the accumulated ``origins`` per
  activated capability. Ids/labels only — never skill text. Exposure stays
  OBSERVATIONAL, emitted once at a true terminal result.
- Legacy state/checkpoints recorded before the field parse and resume
  unchanged (origins == [], never a guess, never fabricated).
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from aci.domain.runtime.actions import CapabilityRequest, FinalCandidate
from aci.domain.runtime.authority import ApprovalDecision, GrantEnvelope
from aci.domain.runtime.state import BudgetLedger, CapabilityActivation, TaskState
from aci.domain.runtime.stop_reason import RunStatus
from aci.runtime.capability_runtime import CapabilityRuntime
from aci.runtime.checkpoints import Checkpoint
from aci.runtime.event_bus import CAPABILITY_EXPOSURE, CAPABILITY_LOADED, EventBus
from aci.runtime.model_gateway import FakeModelGateway
from aci.runtime.state_manager import StateManager
from tests.unit.test_capability_preload import _run_plain
from tests.unit.test_capability_tool_call import (
    RUN_ID,
    SKILL_TEXT,
    _batch,
    _cap_call,
    _kernel,
    _read,
    _skill_runtime,
)

SKILL_DIGEST = hashlib.sha256(SKILL_TEXT.encode()).hexdigest()


def _events(bus: EventBus, event_type: str) -> list[Any]:
    return [e for e in bus.history(RUN_ID) if e.event_type == event_type]


def _exposure(bus: EventBus) -> dict[str, Any]:
    """The single terminal exposure join (a pause emits none)."""
    (event,) = _events(bus, CAPABILITY_EXPOSURE)
    return event.payload


def _approval(paused: Any) -> ApprovalDecision:
    return ApprovalDecision(
        approval_id=paused.approval_id or "",
        approved=True,
        decided_by="client",
        decided_at=datetime.now(UTC),
    )


class _ProvenanceSelection:
    """An ACISelection WITH registry provenance (what RegistrySelection is)."""

    def __init__(self, route_run_id: str, bundle_id: str) -> None:
        self.capability_id = "cap.pytest"
        self.version = "1.2.0"
        self.payload_ref = "skill://cap.pytest@1.2.0/SKILL.md"
        self.digest = SKILL_DIGEST
        self.estimated_context_tokens = 50
        self.route_run_id = route_run_id
        self.bundle_id = bundle_id


class _SequentialProvenanceACI:
    """Each search mints a FRESH route/bundle pair, so "the latest provenance
    wins" on a re-activation of the same capability is observable."""

    def __init__(self) -> None:
        self.searches = 0

    def search(self, request: Any) -> list[_ProvenanceSelection]:
        self.searches += 1
        return [_ProvenanceSelection(f"rr-{self.searches}", f"bun-{self.searches}")]

    def resolve(self, capability_id: str, version: str) -> tuple[bytes, str]:
        return SKILL_TEXT.encode(), SKILL_DIGEST


# -- preload then request: BOTH origins, ONE entry ------------------------------


class TestPreloadThenRequest:
    def test_exposure_shows_both_origins_in_one_entry(self) -> None:
        """The reported limitation, fixed: a preloaded skill the model then
        asks for again (request_capability tool) keeps ONE activation whose
        origins accumulate; the exposure join can finally tell."""
        runtime, _ = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, state, _ = _kernel(model, runtime, events=bus)
        result = _run_plain(kernel)
        assert result.status is RunStatus.SUCCEEDED
        # ONE activation entry (no duplicate), origins in acquisition order.
        snapshot = state.snapshot(RUN_ID)
        assert [a.capability_id for a in snapshot.active_capabilities] == ["cap.pytest"]
        assert snapshot.active_capabilities[0].origins == ["preload", "model_request"]
        # The terminal join carries the accumulated origins per capability.
        (cap,) = _exposure(bus)["capabilities"]
        assert cap["capability_id"] == "cap.pytest"
        assert cap["origins"] == ["preload", "model_request"]
        # Each CAPABILITY_LOADED labels ITS OWN load (ids/labels only).
        loaded = _events(bus, CAPABILITY_LOADED)
        assert [e.payload["origin"] for e in loaded] == ["preload", "model_request"]
        assert loaded[0].payload["preload"] is True
        assert "preload" not in loaded[1].payload

    def test_the_text_json_action_path_is_also_model_request(self) -> None:
        """The other model-request path: the text-JSON capability_request
        action re-activating a preloaded skill accumulates the same way."""
        runtime, _ = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway(
            [CapabilityRequest(objective="triage a failing pytest"), FinalCandidate(summary="done")]
        )
        kernel, state, _ = _kernel(model, runtime, events=bus)
        assert _run_plain(kernel).status is RunStatus.SUCCEEDED
        (activation,) = state.snapshot(RUN_ID).active_capabilities
        assert activation.origins == ["preload", "model_request"]
        (cap,) = _exposure(bus)["capabilities"]
        assert cap["origins"] == ["preload", "model_request"]
        loaded = _events(bus, CAPABILITY_LOADED)
        assert [e.payload["origin"] for e in loaded] == ["preload", "model_request"]

    def test_reactivation_keeps_latest_provenance_and_origin_history(self) -> None:
        """Latest route/bundle ids win (the re-activation's), but the origin
        history is never dropped."""
        runtime = CapabilityRuntime(_SequentialProvenanceACI())  # type: ignore[arg-type]
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, state, _ = _kernel(model, runtime, events=bus)
        assert _run_plain(kernel).status is RunStatus.SUCCEEDED
        (activation,) = state.snapshot(RUN_ID).active_capabilities
        assert (activation.route_run_id, activation.bundle_id) == ("rr-2", "bun-2")
        assert activation.origins == ["preload", "model_request"]
        (cap,) = _exposure(bus)["capabilities"]
        assert (cap["route_run_id"], cap["bundle_id"]) == ("rr-2", "bun-2")
        assert cap["origins"] == ["preload", "model_request"]


# -- single-path origins ----------------------------------------------------------


class TestSinglePathOrigins:
    def test_request_only_tool(self) -> None:
        runtime, _ = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime, events=bus)
        assert _run_plain(kernel, preload=False).status is RunStatus.SUCCEEDED
        (cap,) = _exposure(bus)["capabilities"]
        assert cap["origins"] == ["model_request"]
        (loaded,) = _events(bus, CAPABILITY_LOADED)
        assert loaded.payload["origin"] == "model_request"
        assert "preload" not in loaded.payload

    def test_request_only_text_json(self) -> None:
        runtime, _ = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway(
            [CapabilityRequest(objective="triage a failing pytest"), FinalCandidate(summary="done")]
        )
        kernel, _, _ = _kernel(model, runtime, events=bus)
        assert _run_plain(kernel, preload=False).status is RunStatus.SUCCEEDED
        (cap,) = _exposure(bus)["capabilities"]
        assert cap["origins"] == ["model_request"]

    def test_preload_only(self) -> None:
        runtime, _ = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime, events=bus)
        assert _run_plain(kernel).status is RunStatus.SUCCEEDED
        (cap,) = _exposure(bus)["capabilities"]
        assert cap["origins"] == ["preload"]
        (loaded,) = _events(bus, CAPABILITY_LOADED)
        assert loaded.payload["origin"] == "preload"
        assert loaded.payload["preload"] is True


# -- the state-manager merge rule -------------------------------------------------


class TestStateManagerMerge:
    def _state(self) -> StateManager:
        state = StateManager()
        state.create(
            run_id=RUN_ID,
            task=TaskState(task_id="t", objective="o"),
            budget=BudgetLedger(),
            grants=GrantEnvelope(),
        )
        return state

    @staticmethod
    def _activation(origins: list[str]) -> CapabilityActivation:
        return CapabilityActivation(
            capability_id="cap-x",
            version="1",
            digest="d1",
            activation_id="act-1",
            origins=origins,  # type: ignore[arg-type]
        )

    def test_reactivation_accumulates_origins_in_one_entry(self) -> None:
        state = self._state()
        state.activate_capability(RUN_ID, self._activation(["preload"]))
        snap = state.activate_capability(RUN_ID, self._activation(["model_request"]))
        assert len(snap.active_capabilities) == 1
        assert snap.active_capabilities[0].origins == ["preload", "model_request"]

    def test_a_repeated_origin_never_duplicates(self) -> None:
        state = self._state()
        state.activate_capability(RUN_ID, self._activation(["model_request"]))
        snap = state.activate_capability(RUN_ID, self._activation(["model_request"]))
        assert snap.active_capabilities[0].origins == ["model_request"]

    def test_a_legacy_activation_without_origins_merges_to_the_new_origin(self) -> None:
        state = self._state()
        state.activate_capability(RUN_ID, self._activation([]))
        snap = state.activate_capability(RUN_ID, self._activation(["model_request"]))
        assert snap.active_capabilities[0].origins == ["model_request"]


# -- legacy checkpoints + resume -------------------------------------------------


class TestLegacyAndResume:
    def test_a_checkpoint_without_the_field_resumes_fine(self) -> None:
        """Backward compat: a pre-fix checkpoint (no ``origins`` on the
        activation) validates and resumes; the exposure is honestly empty,
        never a guess."""
        runtime, _ = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway([_batch(_read())])
        kernel, _, _ = _kernel(model, runtime, events=bus, approval_tools=("fs.read",))
        paused = _run_plain(kernel)
        assert paused.status is RunStatus.INTERRUPTED_APPROVAL
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        wire = json.loads(checkpoint.model_dump_json())
        for cap in wire["snapshot"]["active_capabilities"]:
            cap.pop("origins", None)
        legacy = Checkpoint.model_validate(wire)
        bus2 = EventBus()
        model2 = FakeModelGateway([FinalCandidate(summary="done")])
        kernel2, state2, _ = _kernel(model2, runtime, events=bus2, approval_tools=("fs.read",))
        resumed = kernel2.resume(legacy, approval=_approval(paused))
        assert resumed.status is RunStatus.SUCCEEDED
        (activation,) = state2.snapshot(RUN_ID).active_capabilities
        assert activation.origins == []
        (cap,) = _exposure(bus2)["capabilities"]
        assert cap["origins"] == []

    def test_resume_does_not_duplicate_origins(self) -> None:
        """Preload + model request before an approval pause; the resumed
        segment asks for the SAME skill again — the origins stay exactly
        ["preload", "model_request"], one entry, no duplicates."""
        runtime, _ = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), _batch(_read())])
        kernel, _, _ = _kernel(model, runtime, events=bus, approval_tools=("fs.read",))
        paused = _run_plain(kernel)
        assert paused.status is RunStatus.INTERRUPTED_APPROVAL
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        assert checkpoint.snapshot.active_capabilities[0].origins == ["preload", "model_request"]
        bus2 = EventBus()
        model2 = FakeModelGateway([_batch(_cap_call("k2")), FinalCandidate(summary="done")])
        kernel2, state2, _ = _kernel(model2, runtime, events=bus2, approval_tools=("fs.read",))
        resumed = kernel2.resume(checkpoint, approval=_approval(paused))
        assert resumed.status is RunStatus.SUCCEEDED
        (activation,) = state2.snapshot(RUN_ID).active_capabilities
        assert activation.origins == ["preload", "model_request"]
        (cap,) = _exposure(bus2)["capabilities"]
        assert cap["origins"] == ["preload", "model_request"]
