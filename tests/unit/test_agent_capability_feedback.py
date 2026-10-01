"""§3.2 wiring: the kernel outcome seam → §33 evidence on the linked bundle.

The seam (``run_controller`` terminal state) fires ``report_outcome`` once
per ACTIVATED capability; the §33 model attaches evidence to the BUNDLE
(ADR-010). The sink therefore records ONE event per routed bundle — the
run's own outcome, joined to what it routed, never a claim that a skill
caused it (ADR-014 amendments 14–17).

Verdict mapping is the honest one:
- ``run_success`` is verifier-gated (INV-08: the kernel only reports
  succeeded on a verified proposal) → ``test_harness success/high`` is a REAL
  observation, plus the run's own ``agent_self_report success/medium``.
- ``VERIFICATION_FAILED`` → the verifier ran and refuted →
  ``test_harness failure/high`` + the run's claim.
- any OTHER failure class (MODEL_FAILURE, TOOL_FAILURE, …) → the run failed
  for reasons that are NOT a test observation → ``agent_self_report`` only;
  the tests state is unclaimed, never guessed.
- no failure class (limits, cancelled, partial) → ``unknown/low`` — §33
  keeps unknown as unknown.
"""

from types import SimpleNamespace
from typing import Any

from aci.adapters.outbound.agent_capabilities import (
    RegistryCapabilityClient,
    RegistryCapabilityFeedback,
    kernel_outcome_verdicts,
)
from aci.application.report_outcome import ReportOutcomeService


class _Outcomes:
    """OutcomeRecorder fake: record/get in memory."""

    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    def record(self, evidence: Any) -> Any:
        self.rows[evidence.outcome_id] = evidence
        return evidence

    def get_outcome(self, outcome_id: str) -> Any:
        return self.rows.get(outcome_id)


class _Bundles:
    """BundleRepository fake: bundles live in their route run."""

    def __init__(self, owners: dict[str, str]) -> None:
        self._owners = owners

    def get_bundle(self, bundle_id: str) -> Any:
        if bundle_id not in self._owners:
            return None
        return SimpleNamespace(bundle_id=bundle_id, route_run_id=self._owners[bundle_id])


def _client(
    routed: list[tuple[str, str]] | None = None,
) -> RegistryCapabilityClient:
    """A RegistryCapabilityClient stand-in carrying only what the sink reads:
    the (route_run_id, bundle_id) pairs its searches routed with kept skills."""
    client = RegistryCapabilityClient.__new__(RegistryCapabilityClient)
    client.routed_bundles = routed or []
    return client  # type: ignore[return-value]


def _service(owners: dict[str, str]) -> ReportOutcomeService:
    return ReportOutcomeService(_Outcomes(), _Bundles(owners))


def test_success_maps_to_verifier_plus_self_report() -> None:
    verdicts = kernel_outcome_verdicts("run_success", None)
    assert [(v.source, v.status, v.confidence) for v in verdicts] == [
        ("test_harness", "success", "high"),
        ("agent_self_report", "success", "medium"),
    ]


def test_verification_failed_maps_to_a_real_test_observation() -> None:
    verdicts = kernel_outcome_verdicts("run_verification_failed", "VERIFICATION_FAILED")
    assert [(v.source, v.status, v.confidence) for v in verdicts] == [
        ("test_harness", "failure", "high"),
        ("agent_self_report", "failure", "medium"),
    ]


def test_other_failures_never_claim_a_test_observation() -> None:
    """MODEL_FAILURE is not a test result: only the run's own claim, and the
    tests state stays unclaimed — never guessed (§33 unknown stays unknown)."""
    verdicts = kernel_outcome_verdicts("run_model_failure", "MODEL_FAILURE")
    assert [(v.source, v.status, v.confidence) for v in verdicts] == [
        ("agent_self_report", "failure", "medium")
    ]


def test_limits_and_cancel_map_to_unknown() -> None:
    verdicts = kernel_outcome_verdicts("run_limit_turns", None)
    assert [(v.source, v.status, v.confidence) for v in verdicts] == [
        ("agent_self_report", "unknown", "low")
    ]


def test_seam_records_one_event_per_routed_bundle() -> None:
    """The seam fires once per activated capability; §33 attaches to the
    bundle — one event per bundle, the rest deduped."""
    client = _client(routed=[("route_1", "bun_1")])
    outcomes = _Outcomes()
    service = ReportOutcomeService(outcomes, _Bundles({"bun_1": "route_1"}))
    sink = RegistryCapabilityFeedback(client, service)

    sink.report(
        capability_id="cap-a",
        version="1.0.0",
        outcome="run_success",
        failure_class=None,
        evidence_refs=["ev-1"],
    )
    sink.report(
        capability_id="cap-b",
        version="1.0.0",
        outcome="run_success",
        failure_class=None,
        evidence_refs=["ev-1"],
    )
    assert len(outcomes.rows) == 1
    (evidence,) = outcomes.rows.values()
    assert evidence.route_run_id == "route_1"
    assert evidence.bundle_id == "bun_1"
    assert evidence.client_status == "run_success"
    assert len(evidence.verdicts) == 2


def test_multi_route_run_records_every_routed_bundle() -> None:
    """A run that routed twice (preload + a mid-run request) joins its
    outcome to BOTH bundles — each is a real §33 attach point."""
    client = _client(routed=[("route_1", "bun_1"), ("route_2", "bun_2")])
    outcomes = _Outcomes()
    service = ReportOutcomeService(outcomes, _Bundles({"bun_1": "route_1", "bun_2": "route_2"}))
    sink = RegistryCapabilityFeedback(client, service)
    sink.report(
        capability_id="cap-a",
        version="1.0.0",
        outcome="run_success",
        failure_class=None,
        evidence_refs=[],
    )
    assert {e.bundle_id for e in outcomes.rows.values()} == {"bun_1", "bun_2"}


def test_no_route_is_a_noop() -> None:
    """A run that never routed has nothing to attach to — the sink stays
    silent rather than fabricating ids (§33 attaches to routed bundles)."""
    client = _client(routed=[])
    outcomes = _Outcomes()
    service = ReportOutcomeService(outcomes, _Bundles({}))
    sink = RegistryCapabilityFeedback(client, service)
    sink.report(
        capability_id="cap-a",
        version="1.0.0",
        outcome="run_success",
        failure_class=None,
        evidence_refs=[],
    )
    assert not outcomes.rows
