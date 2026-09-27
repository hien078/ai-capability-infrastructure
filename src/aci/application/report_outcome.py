"""Structured outcome ingestion (plan §33; ADR-010).

Evidence stays multi-source with confidence; `unknown` stays `unknown`.
The recorder never collapses verdicts into a single success flag.
"""

from datetime import datetime
from uuid import uuid4

from aci.application.protocols import BundleRepository, OutcomeRecorder
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import OutcomeEvidence


class ReportOutcomeService:
    def __init__(self, outcomes: OutcomeRecorder, bundles: BundleRepository) -> None:
        self._outcomes = outcomes
        self._bundles = bundles

    def report(self, evidence: OutcomeEvidence, *, now: datetime | None = None) -> OutcomeEvidence:
        bundle = self._bundles.get_bundle(evidence.bundle_id)
        if bundle is None:
            raise DomainError(
                ErrorCode.BUNDLE_NOT_FOUND,
                f"unknown bundle {evidence.bundle_id}; outcomes attach to routed bundles",
            )
        if bundle.route_run_id != evidence.route_run_id:
            raise DomainError(
                ErrorCode.BUNDLE_VALIDATION_FAILED,
                f"bundle {evidence.bundle_id} belongs to run {bundle.route_run_id},"
                f" not {evidence.route_run_id}",
            )
        return self._outcomes.record(evidence)

    def get(self, outcome_id: str) -> OutcomeEvidence:
        evidence = self._outcomes.get_outcome(outcome_id)
        if evidence is None:
            raise DomainError(ErrorCode.OUTCOME_NOT_FOUND, f"unknown outcome {outcome_id}")
        return evidence


def new_outcome_id() -> str:
    return f"out_{uuid4().hex}"
