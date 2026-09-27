"""Phase 9 unit acceptance: outcome ingestion (§33; ADR-010)."""

from datetime import UTC, datetime

import pytest

from aci.application.report_outcome import ReportOutcomeService, new_outcome_id
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import (
    BundleItem,
    CapabilityBundle,
    OutcomeEvidence,
    OutcomeVerdict,
)

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def bundle(bundle_id: str = "bun_1", run: str = "route_1") -> CapabilityBundle:
    return CapabilityBundle(
        bundle_id=bundle_id,
        route_run_id=run,
        created_at=NOW,
        items=[
            BundleItem(
                capability_id="cap-1",
                version="1.0.0",
                digest=f"sha256:{'a' * 64}",
                kind="skill",
            )
        ],
    )


def evidence(
    outcome_id: str = "out_1", run: str = "route_1", bid: str = "bun_1"
) -> OutcomeEvidence:
    return OutcomeEvidence(
        outcome_id=outcome_id,
        route_run_id=run,
        bundle_id=bid,
        received_at=NOW,
        verdicts=[
            OutcomeVerdict(source="test_harness", status="success", confidence="high"),
            OutcomeVerdict(source="agent_self_report", status="unknown", confidence="low"),
        ],
        tests_before={"failed": 3},
        tests_after={"failed": 0},
        latency_ms=1200,
    )


class FakeRecorder:
    def __init__(self) -> None:
        self.recorded: dict[str, OutcomeEvidence] = {}

    def record(self, evidence: OutcomeEvidence) -> OutcomeEvidence:
        self.recorded[evidence.outcome_id] = evidence
        return evidence

    def get_outcome(self, outcome_id: str) -> OutcomeEvidence | None:
        return self.recorded.get(outcome_id)


class FakeBundles:
    def __init__(self, bundles: dict[str, CapabilityBundle]) -> None:
        self.bundles = bundles

    def get_bundle(self, bundle_id: str) -> CapabilityBundle | None:
        return self.bundles.get(bundle_id)


def test_report_records_multi_source_evidence() -> None:
    recorder = FakeRecorder()
    service = ReportOutcomeService(recorder, FakeBundles({"bun_1": bundle()}))

    stored = service.report(evidence())
    assert stored is recorder.recorded["out_1"]
    # Multi-source verdicts survive verbatim; `unknown` stays `unknown` (ADR-010).
    assert [v.status for v in stored.verdicts] == ["success", "unknown"]
    assert stored.tests_before == {"failed": 3}
    assert stored.tests_after == {"failed": 0}
    assert service.get("out_1") is stored


def test_report_rejects_unknown_bundle() -> None:
    service = ReportOutcomeService(FakeRecorder(), FakeBundles({}))
    with pytest.raises(DomainError) as exc:
        service.report(evidence(bid="bun_missing"))
    assert exc.value.code == ErrorCode.BUNDLE_NOT_FOUND


def test_report_rejects_bundle_from_other_run() -> None:
    service = ReportOutcomeService(
        FakeRecorder(), FakeBundles({"bun_1": bundle(run="route_other")})
    )
    with pytest.raises(DomainError) as exc:
        service.report(evidence(run="route_1", bid="bun_1"))
    assert exc.value.code == ErrorCode.BUNDLE_VALIDATION_FAILED


def test_get_unknown_outcome_raises() -> None:
    service = ReportOutcomeService(FakeRecorder(), FakeBundles({}))
    with pytest.raises(DomainError) as exc:
        service.get("out_missing")
    assert exc.value.code == ErrorCode.OUTCOME_NOT_FOUND


def test_new_outcome_id_is_unique_and_prefixed() -> None:
    assert new_outcome_id() != new_outcome_id()
    assert new_outcome_id().startswith("out_")
