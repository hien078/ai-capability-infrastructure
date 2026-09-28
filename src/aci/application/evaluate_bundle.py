"""Evaluate a routed bundle run (V4 §57 Evaluation Service; §33.1).

Application service over protocols only: the intelligence is a pluggable
``BundleEvaluator`` (same pattern as ``AgentExecutor`` — §50: no model
gateway in the platform, a CLIENT plugs in), the persistence goes through
the existing ``OutcomeRecorder``/``BundleRepository`` protocols.

The service validates that the bundle exists and belongs to the run
(same guard as ``ReportOutcomeService``), calls the evaluator, then
records the verdict as ONE ``external_evaluator`` source on the bundle's
outcome trail — appending, never merging: if an outcome event already
exists for the bundle, the evaluation verdict is appended as an
additional verdict row (§33.1 multi-source; the recorder keeps order).
"""

import uuid
from datetime import UTC, datetime
from typing import Protocol

from aci.application.protocols import BundleRepository, OutcomeRecorder
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import OutcomeEvidence, OutcomeVerdict
from aci.domain.evaluation.models import (
    CriterionResult,
    EvaluationRequest,
    EvaluationRubric,
    EvaluationVerdict,
)


class BundleEvaluator(Protocol):
    """Pluggable evaluation intelligence (§50 — a client, not a gateway).

    Implementations may be an LLM judge, a test harness reader, a human
    operator UI, or a deterministic checker. They receive the request
    (task summary + rubric) and return per-criterion results; they never
    touch the outcome store themselves.
    """

    evaluator_version: str

    def evaluate(self, request: EvaluationRequest) -> list[CriterionResult]: ...  # pragma: no cover


class EvaluateBundleService:
    def __init__(
        self,
        bundles: BundleRepository,
        outcomes: OutcomeRecorder,
        evaluator: BundleEvaluator,
    ) -> None:
        self._bundles = bundles
        self._outcomes = outcomes
        self._evaluator = evaluator

    def evaluate(
        self,
        *,
        route_run_id: str,
        bundle_id: str,
        task_summary: str,
        rubric: EvaluationRubric,
        now: datetime | None = None,
    ) -> EvaluationVerdict:
        """Run one evaluation and record it as external_evaluator evidence."""
        evaluated_at = now or datetime.now(UTC)
        bundle = self._bundles.get_bundle(bundle_id)
        if bundle is None:
            raise DomainError(
                ErrorCode.BUNDLE_NOT_FOUND,
                f"unknown bundle {bundle_id}; evaluations attach to routed bundles",
            )
        if bundle.route_run_id != route_run_id:
            raise DomainError(
                ErrorCode.BUNDLE_VALIDATION_FAILED,
                f"bundle {bundle_id} belongs to run {bundle.route_run_id}, not {route_run_id}",
            )

        request = EvaluationRequest(
            evaluation_id=f"eval-{uuid.uuid4().hex[:12]}",
            route_run_id=route_run_id,
            bundle_id=bundle_id,
            task_summary=task_summary,
            rubric=rubric,
            created_at=evaluated_at,
        )
        results = self._evaluator.evaluate(request)
        verdict = EvaluationVerdict(
            evaluation_id=request.evaluation_id,
            route_run_id=route_run_id,
            bundle_id=bundle_id,
            results=results,
            status="unknown",  # set below via aggregate()
            confidence="medium",
            evaluator_version=self._evaluator.evaluator_version,
            evaluated_at=evaluated_at,
        )
        # aggregate() is the domain's single synthesis: a missing result
        # row for a rubric criterion is unknown, never silently pass.
        verdict = verdict.model_copy(update={"status": verdict.aggregate(rubric)})
        self._record_verdict(verdict, evaluated_at)
        return verdict

    # ---------------------------------------------------------------------------

    def _record_verdict(self, verdict: EvaluationVerdict, at: datetime) -> None:
        """Append the verdict as external_evaluator evidence (§33.1).

        The outcome recorder is append-only per source: recording the
        same evaluation twice would duplicate the verdict row, so the
        evaluation_id rides in the verdict's note for traceability.
        """
        status = (
            "success"
            if verdict.status == "pass"
            else ("failure" if verdict.status == "fail" else "unknown")
        )
        self._outcomes.record(
            OutcomeEvidence(
                outcome_id=f"out-{uuid.uuid4().hex[:12]}",
                route_run_id=verdict.route_run_id,
                bundle_id=verdict.bundle_id,
                verdicts=[
                    OutcomeVerdict(
                        source="external_evaluator",
                        status=status,  # type: ignore[arg-type]
                        confidence=verdict.confidence,
                    )
                ],
                client_status="completed",
                received_at=at,
            )
        )
