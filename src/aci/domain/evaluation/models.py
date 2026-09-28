"""Evaluation service contracts (V4 §57; plan §33.1 external_evaluator).

The evaluation service is a ``kind=service`` capability (§57: services
register through the SAME canonical registry — no parallel registries).
Its verb is ``call`` (§32): a client (or the platform itself) submits a
routed bundle run for evaluation and receives a verdict.

The verdict it produces is ONE SOURCE among many (§33.1): it records as
``external_evaluator`` evidence on the bundle's outcome trail — it never
replaces or merges the other sources, and ``unknown`` stays ``unknown``.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RubricStatus = Literal["pass", "fail", "unknown"]


class EvaluationRubric(BaseModel):
    """What the evaluator checks — declarative, inspectable (§27 quality)."""

    model_config = {"frozen": True}

    criteria: list[str] = Field(min_length=1)
    #: How strict the verdict synthesis is: all = every criterion must pass;
    #: any = at least one; majority = more than half.
    aggregation: Literal["all", "any", "majority"] = "all"


class EvaluationRequest(BaseModel):
    """One bundle run submitted for evaluation."""

    model_config = {"frozen": True}

    evaluation_id: str
    route_run_id: str
    bundle_id: str
    #: Free-text description of what the run was supposed to accomplish —
    #: the evaluator sees this plus the rubric, never raw repo dumps (§25.4).
    task_summary: str = Field(min_length=1, max_length=8000)
    rubric: EvaluationRubric
    created_at: datetime


class CriterionResult(BaseModel):
    model_config = {"frozen": True}

    criterion: str
    status: RubricStatus
    #: Short evidence note per criterion — inspectable, never just a flag.
    note: str = ""


class EvaluationVerdict(BaseModel):
    """The evaluation result — one external_evaluator source (§33.1)."""

    model_config = {"frozen": True}

    evaluation_id: str
    route_run_id: str
    bundle_id: str
    results: list[CriterionResult] = Field(default_factory=list)
    status: RubricStatus
    #: Confidence the evaluator declares about its own verdict.
    confidence: Literal["high", "medium", "low"] = "low"
    evaluator_version: str
    evaluated_at: datetime

    def aggregate(self, rubric: EvaluationRubric) -> RubricStatus:
        """Synthesize per-criterion results into one status per the rubric.

        A rubric criterion with NO result row counts as ``unknown`` — the
        evaluator skipped it, which is absence of evidence, not success
        (§60.9). ``unknown`` is never silently converted: if the
        aggregation would pass but any criterion is unknown, the
        aggregate is unknown.
        """
        by_criterion = {r.criterion: r.status for r in self.results}
        statuses = [by_criterion.get(c, "unknown") for c in rubric.criteria]
        if not statuses:
            return "unknown"
        passed = sum(1 for s in statuses if s == "pass")
        failed = sum(1 for s in statuses if s == "fail")
        unknown = len(statuses) - passed - failed
        if rubric.aggregation == "all":
            if failed:
                return "fail"
            if unknown:
                return "unknown"  # no failures, but evidence is missing
            return "pass"
        if rubric.aggregation == "any":
            if passed:
                return "pass"
            if failed:
                return "fail"
            return "unknown"
        # majority: more than half passed; a real failure with no majority
        # is a fail, a no-majority-no-failure is unknown (missing evidence).
        if passed * 2 > len(statuses):
            return "pass"
        if failed:
            return "fail"
        return "unknown"
