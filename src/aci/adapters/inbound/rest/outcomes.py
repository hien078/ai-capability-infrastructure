"""Outcome ingestion (plan §33; ADR-010): multi-source evidence, never a lone flag."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from aci.adapters.inbound.rest.schemas import OutcomeRequest
from aci.adapters.inbound.rest.wiring import Container, get_container
from aci.application.report_outcome import new_outcome_id
from aci.domain.capability.models import OutcomeEvidence, OutcomeVerdict

router = APIRouter(prefix="/v1/outcomes", tags=["outcomes"])


@router.post("", status_code=201)
def report_outcome(
    body: OutcomeRequest, container: Annotated[Container, Depends(get_container)]
) -> OutcomeEvidence:
    evidence = OutcomeEvidence(
        outcome_id=new_outcome_id(),
        route_run_id=body.route_run_id,
        bundle_id=body.bundle_id,
        received_at=datetime.now(UTC),
        verdicts=[
            OutcomeVerdict(source=v.source, status=v.status, confidence=v.confidence)
            for v in body.verdicts
        ],
        tests_before=dict(body.tests_before),
        tests_after=dict(body.tests_after),
        latency_ms=body.latency_ms,
    )
    return container.outcome_service.report(evidence)
