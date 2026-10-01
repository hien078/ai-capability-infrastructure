"""Kernel capability EVIDENCE JOIN (task B, 2026-10-01): the immutable
provenance of an actually loaded capability (route run + bundle + entry
digest + activation id) and the observed run result must be joinable through
durable run events/checkpoints — including resume — without changing
routing, grants, model protocol, preload default or success semantics.

Pinned here:
- RegistrySelection carries route_run_id/bundle_id; CapabilityRuntime.activate
  copies them onto the activation (None for provenance-less clients — never
  fabricated); they ride in authoritative run state (INV-01) so checkpoints
  carry them (INV-13).
- CAPABILITY_LOADED (after the CAS commit) carries the join: run
  association, route/bundle ids where known, exact version + ENTRY digest,
  activation id, context cost — never skill text, task text, paths, secrets
  or model-generated values.
- At a TRUE terminal result (never a pause) the kernel emits
  CAPABILITY_EXPOSURE: the run's stop reason + verifier verdict joined with
  the actually activated set. Loaded-in-context is never causation; unknown
  and LIMIT_TURNS stay failures even when a command passed.
- The optional feedback seam (report_outcome) is called per activated
  capability with the RUN's outcome; a failing seam never fails the run; an
  unwired one is a no-op.
- Legacy activations/checkpoints (no provenance fields) validate and resume
  unchanged.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from aci.domain.runtime.actions import FinalCandidate
from aci.domain.runtime.authority import ApprovalDecision
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.runtime.capability_runtime import CapabilityRuntime
from aci.runtime.event_bus import (
    CAPABILITY_EXPOSURE,
    CAPABILITY_LOADED,
    RUN_PAUSED,
    EventBus,
)
from aci.runtime.model_gateway import FakeModelGateway
from aci.runtime.run_controller import HarnessKernel, cancel_paused
from aci.runtime.state_manager import StateManager
from tests.unit.test_capability_tool_call import (
    RUN_ID,
    SKILL_TEXT,
    FakeCapabilities,
    _batch,
    _cap_call,
    _kernel,
    _read,
    _run,
    _skill_runtime,
)

SKILL_DIGEST = hashlib.sha256(SKILL_TEXT.encode()).hexdigest()


class ProvenanceSelection:
    """An ACISelection WITH registry provenance (what RegistrySelection is)."""

    def __init__(self, route_run_id: str, bundle_id: str) -> None:
        self.capability_id = "cap.pytest"
        self.version = "1.2.0"
        self.payload_ref = "skill://cap.pytest@1.2.0/SKILL.md"
        self.digest = SKILL_DIGEST
        self.estimated_context_tokens = 50
        self.route_run_id = route_run_id
        self.bundle_id = bundle_id


class ProvenanceACI:
    """ACIClient serving one provenance-carrying selection."""

    def __init__(self, route_run_id: str = "rr-1", bundle_id: str = "bun-1") -> None:
        self._selection = ProvenanceSelection(route_run_id, bundle_id)

    def search(self, request: Any) -> list[ProvenanceSelection]:
        return [self._selection]

    def resolve(self, capability_id: str, version: str) -> tuple[bytes, str]:
        return SKILL_TEXT.encode(), SKILL_DIGEST


class ExplodingFeedback:
    """A feedback sink whose failure must never reach the run."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def report(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        raise RuntimeError("feedback sink exploded")


class RecordingFeedback:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def report(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _events(bus: EventBus, event_type: str) -> list[Any]:
    return [e for e in bus.history(RUN_ID) if e.event_type == event_type]


# -- provenance rides from selection to activation to state -------------------


class TestProvenanceCarries:
    def test_activate_copies_route_and_bundle_ids(self) -> None:
        runtime = CapabilityRuntime(ProvenanceACI("rr-42", "bun-42"))  # type: ignore[arg-type]
        (activation,) = runtime.handle_request(_request(), _empty_snapshot())
        assert (activation.route_run_id, activation.bundle_id) == ("rr-42", "bun-42")

    def test_provenance_less_client_activates_with_none_never_fabricated(self) -> None:
        runtime, _ = _skill_runtime()
        (activation,) = runtime.handle_request(_request(), _empty_snapshot())
        assert activation.route_run_id is None and activation.bundle_id is None

    def test_state_and_checkpoint_round_trip_the_provenance(self) -> None:
        runtime = CapabilityRuntime(ProvenanceACI())  # type: ignore[arg-type]
        state = StateManager()
        _create_state(state)
        (activation,) = runtime.handle_request(_request(), state.snapshot(RUN_ID))
        state.activate_capability(RUN_ID, activation)
        snap = state.snapshot(RUN_ID)
        assert snap.active_capabilities[0].route_run_id == "rr-1"
        # INV-13: the checkpointed form round-trips provenance unchanged.
        round_tripped = type(snap).model_validate(snap.model_dump(mode="json"))
        assert round_tripped.active_capabilities[0].bundle_id == "bun-1"

    def test_legacy_activation_without_provenance_validates(self) -> None:
        from aci.domain.runtime.state import CapabilityActivation

        legacy = CapabilityActivation.model_validate(
            {
                "capability_id": "cap.old",
                "version": "1.0.0",
                "digest": "d0",
                "activation_id": "act-0",
            }
        )
        assert legacy.route_run_id is None and legacy.bundle_id is None


def _request() -> Any:
    from aci.domain.runtime.actions import CapabilityRequest

    return CapabilityRequest(objective="triage a failing pytest")


def _empty_snapshot() -> Any:
    from tests.unit.test_capability_runtime import _snapshot

    return _snapshot()


def _create_state(state: StateManager) -> None:
    from aci.domain.runtime.authority import GrantEnvelope
    from aci.domain.runtime.state import BudgetLedger, TaskState

    state.create(
        run_id=RUN_ID,
        task=TaskState(task_id="t", objective="o"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
    )


# -- CAPABILITY_LOADED carries the join ----------------------------------------


class TestLoadedEventJoin:
    def test_payload_carries_provenance_after_the_commit(self) -> None:
        runtime = CapabilityRuntime(ProvenanceACI("rr-9", "bun-9"))  # type: ignore[arg-type]
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, state, _ = _kernel(model, runtime, events=bus)
        _run(kernel)
        (loaded,) = _events(bus, CAPABILITY_LOADED)
        assert loaded.payload == {
            "capability_id": "cap.pytest",
            "version": "1.2.0",
            "digest": SKILL_DIGEST,
            "activation_id": state.snapshot(RUN_ID).active_capabilities[0].activation_id,
            "context_tokens": state.snapshot(RUN_ID).active_capabilities[0].context_tokens,
            "route_run_id": "rr-9",
            "bundle_id": "bun-9",
        }

    def test_provenance_less_selection_omits_the_ids(self) -> None:
        runtime, _ = _skill_runtime()
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime, events=bus)
        _run(kernel)
        (loaded,) = _events(bus, CAPABILITY_LOADED)
        assert "route_run_id" not in loaded.payload
        assert "bundle_id" not in loaded.payload

    def test_no_skill_text_task_text_or_paths_in_any_capability_event(self) -> None:
        runtime = CapabilityRuntime(ProvenanceACI())  # type: ignore[arg-type]
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime, events=bus)
        _run(kernel)
        # The CAPABILITY events carry ids/digests only — never the skill text,
        # the payload_ref path, or any model-generated value.
        capability_events = [
            e
            for e in bus.history(RUN_ID)
            if e.event_type in (CAPABILITY_LOADED, CAPABILITY_EXPOSURE)
        ]
        assert capability_events
        everything = "".join(json.dumps(e.payload) for e in capability_events)
        for banned in ("pytest triage", "Run the single failing test", "SKILL.md", "skill://"):
            assert banned not in everything


# -- CAPABILITY_EXPOSURE at a true terminal result -----------------------------


class TestExposure:
    def _run_with(
        self,
        model: FakeModelGateway,
        runtime: Any,
        bus: EventBus,
        *,
        approval_tools: tuple[str, ...] = (),
    ) -> Any:
        kernel, _, _ = _kernel(model, runtime, events=bus, approval_tools=approval_tools)
        return _run(kernel)

    def test_success_joins_verdict_and_provenance(self) -> None:
        runtime = CapabilityRuntime(ProvenanceACI("rr-s", "bun-s"))  # type: ignore[arg-type]
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        result = self._run_with(model, runtime, bus)
        assert result.status is RunStatus.SUCCEEDED
        (exposure,) = _events(bus, CAPABILITY_EXPOSURE)
        assert exposure.payload["stop_reason"] == "SUCCESS"
        assert exposure.payload["verifier_verdict"] == "PASS"
        assert exposure.payload["evidence_refs"] == []
        (cap,) = exposure.payload["capabilities"]
        assert (cap["capability_id"], cap["route_run_id"], cap["bundle_id"]) == (
            "cap.pytest",
            "rr-s",
            "bun-s",
        )
        assert cap["digest"] == SKILL_DIGEST

    def test_no_capabilities_no_exposure_event(self) -> None:
        bus = EventBus()
        model = FakeModelGateway([FinalCandidate(summary="done")])
        result = self._run_with(model, FakeCapabilities(), bus)
        assert result.status is RunStatus.SUCCEEDED
        assert _events(bus, CAPABILITY_EXPOSURE) == []

    def test_pause_is_not_terminal_no_exposure(self) -> None:
        runtime = CapabilityRuntime(ProvenanceACI())  # type: ignore[arg-type]
        bus = EventBus()
        # Load the skill first, THEN pause on an approval-gated read: the
        # checkpoint carries the activation, the pause emits no join.
        model = FakeModelGateway([_batch(_cap_call()), _batch(_read())])
        paused = self._run_with(model, runtime, bus, approval_tools=("fs.read",))
        assert paused.status is RunStatus.INTERRUPTED_APPROVAL
        assert _events(bus, CAPABILITY_EXPOSURE) == []
        assert _events(bus, RUN_PAUSED)

    def test_resumed_run_joins_once_without_duplicates(self) -> None:
        runtime = CapabilityRuntime(ProvenanceACI("rr-r", "bun-r"))  # type: ignore[arg-type]
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), _batch(_read())])
        kernel, _, _ = _kernel(model, runtime, events=bus, approval_tools=("fs.read",))
        paused = _run(kernel)
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        assert checkpoint.snapshot.active_capabilities
        # A FRESH kernel (the restart case): the restored activations carry
        # their provenance; the terminal result joins exactly once.
        bus2 = EventBus()
        model2 = FakeModelGateway([FinalCandidate(summary="done")])
        kernel2, _, _ = _kernel(model2, runtime, events=bus2, approval_tools=("fs.read",))
        resumed = kernel2.resume(
            checkpoint,
            approval=ApprovalDecision(
                approval_id=paused.approval_id or "",
                approved=True,
                decided_by="client",
                decided_at=datetime.now(UTC),
            ),
        )
        assert resumed.status is RunStatus.SUCCEEDED
        exposures = _events(bus2, CAPABILITY_EXPOSURE)
        assert len(exposures) == 1
        (cap,) = exposures[0].payload["capabilities"]
        assert cap["capability_id"] == "cap.pytest"
        assert cap["route_run_id"] == "rr-r"
        assert cap["activation_id"] == checkpoint.snapshot.active_capabilities[0].activation_id

    def test_limit_turns_stays_a_failure_even_when_a_command_passed(self) -> None:
        """INV-08 never waived: the at-limit pack records the verification
        outcome as EVIDENCE (verdict INCONCLUSIVE), and the exposure join
        carries the run's own stop reason LIMIT_TURNS — never success."""
        from aci.domain.runtime.evidence import CheckResult
        from aci.runtime.verification import VerifierCallable
        from aci.runtime.workspace_tools import VERIFICATION_CHECK_NAME

        runtime, _ = _skill_runtime()
        bus = EventBus()
        passing = VerifierCallable(
            VERIFICATION_CHECK_NAME,
            lambda s, c: CheckResult(name=VERIFICATION_CHECK_NAME, passed=True),
        )
        model = FakeModelGateway([_batch(_cap_call())])
        kernel = _kernel_with_verifier(model, runtime, [passing], events=bus)
        result = kernel.run(
            _contract(),
            _spec(),
            max_turns=1,
        )
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.LIMIT_TURNS
        (exposure,) = _events(bus, CAPABILITY_EXPOSURE)
        assert exposure.payload["stop_reason"] == "LIMIT_TURNS"
        assert exposure.payload["verifier_verdict"] == "INCONCLUSIVE"

    def test_cancel_paused_emits_the_exposure_join(self) -> None:
        runtime = CapabilityRuntime(ProvenanceACI("rr-c", "bun-c"))  # type: ignore[arg-type]
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), _batch(_read())])
        kernel, _, _ = _kernel(model, runtime, events=bus, approval_tools=("fs.read",))
        _run(kernel)
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        assert checkpoint.snapshot.active_capabilities
        cancel_bus = EventBus()
        result = cancel_paused(checkpoint, state=StateManager(), event_bus=cancel_bus)
        assert result.status is RunStatus.CANCELLED
        (exposure,) = [e for e in cancel_bus.history(RUN_ID) if e.event_type == CAPABILITY_EXPOSURE]
        assert exposure.payload["stop_reason"] == "CANCELLED"
        (cap,) = exposure.payload["capabilities"]
        assert cap["route_run_id"] == "rr-c"


def _kernel_with_verifier(
    model: Any, capabilities: Any, checks: list[Any], *, events: EventBus | None = None
) -> Any:
    from aci.runtime.recovery import RecoveryManager
    from aci.runtime.verification import VerificationManager
    from tests.unit.test_capability_tool_call import GuardedTools

    return HarnessKernel(
        state=StateManager(),
        model_gateway=model,
        tool_executor=GuardedTools(),
        context_engine=_context_engine(),
        verifier=VerificationManager(checks),
        recovery=RecoveryManager(),
        capability_runtime=capabilities,
        event_bus=events,
        sleep=lambda _: None,
    )


def _context_engine() -> Any:
    from aci.runtime.context_engine import ContextBudget, ContextEngine

    return ContextEngine(ContextBudget(total_tokens=60_000))


def _contract() -> Any:
    from aci.domain.runtime.subtask import SubtaskContract

    return SubtaskContract(task_id=RUN_ID, objective="fix it", created_at=datetime.now(UTC))


def _spec() -> Any:
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


# -- the feedback seam ----------------------------------------------------------


class TestFeedbackSeam:
    def _runtime_with(self, feedback: Any) -> CapabilityRuntime:
        return CapabilityRuntime(ProvenanceACI(), feedback=feedback)  # type: ignore[arg-type]

    def test_reported_per_activated_capability_with_the_runs_outcome(self) -> None:
        feedback = RecordingFeedback()
        runtime = self._runtime_with(feedback)
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime, events=bus)
        _run(kernel)
        (call,) = feedback.calls
        assert call["capability_id"] == "cap.pytest"
        assert call["version"] == "1.2.0"
        assert call["outcome"] == "run_success"
        assert call["failure_class"] is None

    def test_failure_class_is_the_stop_reason_on_a_failed_run(self) -> None:
        feedback = RecordingFeedback()
        runtime = self._runtime_with(feedback)
        bus = EventBus()
        # The model asks for a skill, then the run ends FAILED at the turn
        # limit — a failure, never converted to success (INV-08 not waived).
        model = FakeModelGateway([_batch(_cap_call())])
        kernel = _kernel_with_verifier(model, runtime, [], events=bus)
        result = kernel.run(_contract(), _spec(), max_turns=1)
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.LIMIT_TURNS
        (call,) = feedback.calls
        assert call["capability_id"] == "cap.pytest"
        assert call["outcome"] == "run_limit_turns"
        assert call["failure_class"] == "LIMIT_TURNS"

    def test_a_failing_sink_never_fails_or_alters_the_run(self) -> None:
        runtime = self._runtime_with(ExplodingFeedback())
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime, events=bus)
        result = _run(kernel)
        assert result.status is RunStatus.SUCCEEDED
        assert result.stop_reason is StopReason.SUCCESS
        (exposure,) = _events(bus, CAPABILITY_EXPOSURE)
        assert exposure.payload["stop_reason"] == "SUCCESS"

    def test_unwired_seam_is_a_noop(self) -> None:
        runtime = CapabilityRuntime(ProvenanceACI())  # type: ignore[arg-type]
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime, events=bus)
        result = _run(kernel)
        assert result.status is RunStatus.SUCCEEDED
        assert len(_events(bus, CAPABILITY_EXPOSURE)) == 1


# -- durable serialization (the events ARE the evidence bridge) ---------------


class TestDurableSerialization:
    def test_exposure_event_serializes_into_an_agent_run_event_record(self) -> None:
        from aci.domain.runtime.persistence import AgentRunEventRecord

        runtime = CapabilityRuntime(ProvenanceACI("rr-d", "bun-d"))  # type: ignore[arg-type]
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), FinalCandidate(summary="done")])
        kernel, _, _ = _kernel(model, runtime, events=bus)
        _run(kernel)
        (exposure,) = _events(bus, CAPABILITY_EXPOSURE)
        record = AgentRunEventRecord(
            event_id=exposure.event_id,
            run_id=exposure.run_id,
            seq=0,
            event_type=exposure.event_type,
            turn_id=exposure.turn_id,
            payload=dict(exposure.payload),
            recorded_at=exposure.timestamp,
        )
        round_tripped = AgentRunEventRecord.model_validate(record.model_dump(mode="json"))
        assert round_tripped.payload["capabilities"][0]["route_run_id"] == "rr-d"
        assert "pytest triage" not in json.dumps(round_tripped.payload)

    def test_checkpointed_provenance_survives_a_restart_resume(self) -> None:
        """The full seam: registry selection → activation → checkpoint →
        fresh-kernel resume → terminal exposure join, provenance intact."""
        runtime = CapabilityRuntime(ProvenanceACI("rr-x", "bun-x"))  # type: ignore[arg-type]
        bus = EventBus()
        model = FakeModelGateway([_batch(_cap_call()), _batch(_read())])
        kernel, _, _ = _kernel(model, runtime, events=bus, approval_tools=("fs.read",))
        _run(kernel)
        checkpoint = kernel.pending_checkpoint(RUN_ID)
        assert checkpoint is not None
        # The durable form (what migration 0019 stores) round-trips it.
        from aci.runtime.checkpoints import validate_for_resume

        stored = validate_for_resume(checkpoint)
        assert stored.snapshot.active_capabilities[0].route_run_id == "rr-x"
        bus2 = EventBus()
        model2 = FakeModelGateway([FinalCandidate(summary="done")])
        kernel2, _, _ = _kernel(model2, runtime, events=bus2, approval_tools=("fs.read",))
        resumed = kernel2.resume(
            stored,
            approval=ApprovalDecision(
                approval_id=checkpoint.pending_approval_id or "",
                approved=True,
                decided_by="client",
                decided_at=datetime.now(UTC),
            ),
        )
        assert resumed.status is RunStatus.SUCCEEDED
        (exposure,) = _events(bus2, CAPABILITY_EXPOSURE)
        (cap,) = exposure.payload["capabilities"]
        assert (cap["route_run_id"], cap["bundle_id"]) == ("rr-x", "bun-x")
        assert cap["activation_id"] == stored.snapshot.active_capabilities[0].activation_id
