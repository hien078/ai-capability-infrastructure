"""Evaluation service REST surface (V4 §57; §33.1 external_evaluator).

POST /v1/evaluations: submit a routed bundle run for evaluation; the
verdict is recorded as ``external_evaluator`` evidence on the bundle's
outcome trail (§33) — one source among many, never a merge. The
evaluator itself is pluggable (``BundleEvaluator``); the default wired
here is the deterministic rubric checker, an honest floor like the
``UnconfiguredExecutor`` — a real LLM judge plugs in via the same
protocol without touching this adapter.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from aci.adapters.inbound.rest.auth import api_token, bearer_gate
from aci.adapters.inbound.rest.schemas import EvaluationRequestIn
from aci.adapters.inbound.rest.wiring import Container, get_container
from aci.domain.evaluation.models import EvaluationRubric

router = APIRouter(
    prefix="/v1/evaluations", tags=["evaluations"], dependencies=[Depends(bearer_gate(api_token))]
)


@router.post("", status_code=201)
def evaluate_bundle(
    body: EvaluationRequestIn, container: Annotated[Container, Depends(get_container)]
) -> dict[str, object]:
    verdict = container.evaluation_service.evaluate(
        route_run_id=body.route_run_id,
        bundle_id=body.bundle_id,
        task_summary=body.task_summary,
        rubric=EvaluationRubric(
            criteria=list(body.rubric.criteria),
            aggregation=body.rubric.aggregation,
        ),
        now=datetime.now(UTC),
    )
    return {
        "evaluation_id": verdict.evaluation_id,
        "route_run_id": verdict.route_run_id,
        "bundle_id": verdict.bundle_id,
        "status": verdict.status,
        "confidence": verdict.confidence,
        "evaluator_version": verdict.evaluator_version,
        "results": [r.model_dump() for r in verdict.results],
    }
