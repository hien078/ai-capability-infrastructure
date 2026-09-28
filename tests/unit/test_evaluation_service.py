"""Evaluation service unit tests (V4 §57; §33.1 external_evaluator).

Covers the domain aggregation semantics (unknown never silently
converts), the service guards (bundle must exist and belong to the run),
the verdict→outcome recording (external_evaluator source, never a
merge), and the deterministic rubric evaluator floor.
"""

from datetime import UTC, datetime

import pytest

from aci.application.evaluate_bundle import EvaluateBundleService
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import CapabilityBundle, OutcomeEvidence
from aci.domain.evaluation.models import (
    CriterionResult,
    EvaluationRequest,
    EvaluationRubric,
    EvaluationVerdict,
)
from aci.providers.evaluation.rubric_evaluator import RubricContainmentEvaluator

NOW = datetime(2026, 9, 28, tzinfo=UTC)


class FakeBundles:
    def __init__(self, bundles: dict[str, CapabilityBundle]) -> None:
        self._bundles = bundles

    def put_bundle(self, bundle: CapabilityBundle) -> CapabilityBundle:
        self._bundles[bundle.bundle_id] = bundle
        return bundle

    def get_bundle(self, bundle_id: str) -> CapabilityBundle | None:
        return self._bundles.get(bundle_id)


class FakeOutcomes:
    def __init__(self) -> None:
        self.recorded: list[OutcomeEvidence] = []

    def record(self, evidence: OutcomeEvidence) -> OutcomeEvidence:
        self.recorded.append(evidence)
        return evidence

    def get_outcome(self, outcome_id: str) -> OutcomeEvidence | None:
        return next((e for e in self.recorded if e.outcome_id == outcome_id), None)


class StubEvaluator:
    evaluator_version = "stub:1"

    def __init__(self, results: list[CriterionResult]) -> None:
        self._results = results
        self.seen: list[EvaluationRequest] = []

    def evaluate(self, request: EvaluationRequest) -> list[CriterionResult]:
        self.seen.append(request)
        return self._results


def _bundle(bundle_id: str, run_id: str) -> CapabilityBundle:
    return CapabilityBundle(
        bundle_id=bundle_id,
        route_run_id=run_id,
        created_at=NOW,
        items=[],
        execution_order=[],
        budget={"max_items": 5, "max_context_tokens": 6000},
    )


def _verdict(results: list[CriterionResult], agg: str = "all") -> EvaluationVerdict:
    return EvaluationVerdict(
        evaluation_id="eval-x",
        route_run_id="r-1",
        bundle_id="b-1",
        results=results,
        status="pass",
        confidence="medium",
        evaluator_version="stub:1",
        evaluated_at=NOW,
    )


# ---------------------------------------------------------------------------
# Domain: aggregation semantics
# ---------------------------------------------------------------------------


def test_aggregate_all_requires_every_criterion() -> None:
    """A rubric criterion with no result row is missing evidence — unknown,
    never pass (§60.9); a real fail row is a fail regardless."""
    rubric = EvaluationRubric(criteria=["a", "b"], aggregation="all")
    # b has no result row: missing evidence → unknown, not pass, not fail.
    assert _verdict([CriterionResult(criterion="a", status="pass")]).aggregate(rubric) == "unknown"
    # b explicitly failed: a real failure is a failure.
    v = _verdict(
        [
            CriterionResult(criterion="a", status="pass"),
            CriterionResult(criterion="b", status="fail"),
        ]
    )
    assert v.aggregate(rubric) == "fail"


def test_aggregate_unknown_never_silently_passes() -> None:
    """§60.9: absence of evidence is not success — a pass with an unknown
    criterion aggregates to unknown, never to pass."""
    rubric = EvaluationRubric(criteria=["a", "b"], aggregation="all")
    v = _verdict(
        [
            CriterionResult(criterion="a", status="pass"),
            CriterionResult(criterion="b", status="unknown"),
        ]
    )
    assert v.aggregate(rubric) == "unknown"


def test_aggregate_majority_and_any() -> None:
    rubric = EvaluationRubric(criteria=["a", "b", "c"], aggregation="majority")
    v = _verdict(
        [
            CriterionResult(criterion="a", status="pass"),
            CriterionResult(criterion="b", status="pass"),
            CriterionResult(criterion="c", status="fail"),
        ]
    )
    assert v.aggregate(rubric) == "pass"
    rubric_any = EvaluationRubric(criteria=["a", "b"], aggregation="any")
    v2 = _verdict(
        [
            CriterionResult(criterion="a", status="fail"),
            CriterionResult(criterion="b", status="pass"),
        ]
    )
    assert v2.aggregate(rubric_any) == "pass"


def test_aggregate_empty_results_is_unknown() -> None:
    rubric = EvaluationRubric(criteria=["a"], aggregation="all")
    assert _verdict([]).aggregate(rubric) == "unknown"


# ---------------------------------------------------------------------------
# Application service: guards + recording
# ---------------------------------------------------------------------------


def test_evaluate_unknown_bundle_raises() -> None:
    svc = EvaluateBundleService(FakeBundles({}), FakeOutcomes(), StubEvaluator([]))
    with pytest.raises(DomainError) as exc:
        svc.evaluate(
            route_run_id="r-1",
            bundle_id="missing",
            task_summary="did the thing",
            rubric=EvaluationRubric(criteria=["the thing"]),
        )
    assert exc.value.code == ErrorCode.BUNDLE_NOT_FOUND


def test_evaluate_bundle_run_mismatch_raises() -> None:
    svc = EvaluateBundleService(
        FakeBundles({"b-1": _bundle("b-1", "r-other")}), FakeOutcomes(), StubEvaluator([])
    )
    with pytest.raises(DomainError) as exc:
        svc.evaluate(
            route_run_id="r-1",
            bundle_id="b-1",
            task_summary="did the thing",
            rubric=EvaluationRubric(criteria=["the thing"]),
        )
    assert exc.value.code == ErrorCode.BUNDLE_VALIDATION_FAILED


def test_evaluate_records_external_evaluator_verdict() -> None:
    """The verdict lands on the outcome trail as external_evaluator
    evidence (§33.1) — one source, never a merge."""
    outcomes = FakeOutcomes()
    stub = StubEvaluator([CriterionResult(criterion="root cause", status="pass")])
    svc = EvaluateBundleService(FakeBundles({"b-1": _bundle("b-1", "r-1")}), outcomes, stub)
    verdict = svc.evaluate(
        route_run_id="r-1",
        bundle_id="b-1",
        task_summary="found the root cause and fixed it",
        rubric=EvaluationRubric(criteria=["root cause"]),
        now=NOW,
    )
    assert verdict.status == "pass"
    assert len(outcomes.recorded) == 1
    evidence = outcomes.recorded[0]
    assert evidence.route_run_id == "r-1" and evidence.bundle_id == "b-1"
    assert len(evidence.verdicts) == 1
    assert evidence.verdicts[0].source == "external_evaluator"
    assert evidence.verdicts[0].status == "success"
    # the evaluator received the request, never the outcome store
    assert len(stub.seen) == 1 and stub.seen[0].task_summary.startswith("found")


def test_evaluate_fail_verdict_records_failure() -> None:
    outcomes = FakeOutcomes()
    stub = StubEvaluator(
        [
            CriterionResult(criterion="root cause", status="pass"),
            CriterionResult(criterion="tests green", status="fail"),
        ]
    )
    svc = EvaluateBundleService(FakeBundles({"b-1": _bundle("b-1", "r-1")}), outcomes, stub)
    verdict = svc.evaluate(
        route_run_id="r-1",
        bundle_id="b-1",
        task_summary="fixed it",
        rubric=EvaluationRubric(criteria=["root cause", "tests green"]),
        now=NOW,
    )
    assert verdict.status == "fail"
    assert outcomes.recorded[0].verdicts[0].status == "failure"


# ---------------------------------------------------------------------------
# Deterministic evaluator floor
# ---------------------------------------------------------------------------


def _request(summary: str, criteria: list[str]) -> EvaluationRequest:
    return EvaluationRequest(
        evaluation_id="eval-t",
        route_run_id="r-1",
        bundle_id="b-1",
        task_summary=summary,
        rubric=EvaluationRubric(criteria=criteria),
        created_at=NOW,
    )


def test_rubric_evaluator_passes_on_full_containment() -> None:
    ev = RubricContainmentEvaluator()
    results = ev.evaluate(_request("verified the root cause before fixing", ["root cause"]))
    assert results[0].status == "pass"


def test_rubric_evaluator_unknown_not_fail_on_no_match() -> None:
    """The floor never guesses: absent words are unknown, not fail."""
    ev = RubricContainmentEvaluator()
    results = ev.evaluate(_request("did something unrelated", ["root cause"]))
    assert results[0].status == "unknown"


def test_rubric_evaluator_stopwords_do_not_count() -> None:
    ev = RubricContainmentEvaluator()
    results = ev.evaluate(_request("the fix is in", ["the fix of the system"]))
    # "system" is a content word that is absent → unknown, not pass
    assert results[0].status == "unknown"


def test_rubric_evaluator_version_pinned() -> None:
    ev = RubricContainmentEvaluator()
    assert ev.evaluator_version == "rubric-containment:1.0.0"
