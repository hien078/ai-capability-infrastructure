"""Deterministic rubric evaluator — the honest default (V4 §57).

A ``BundleEvaluator`` that checks the task summary against each rubric
criterion with simple containment matching. This is a FLOOR, not a judge
(the same honesty the pattern scanner records): it never guesses — a
criterion whose words do not appear in the summary is ``unknown``, not
``fail`` — and a real LLM judge plugs in through the same protocol
without any platform change (§50: the intelligence is a client).
"""

import re

from aci.domain.evaluation.models import CriterionResult, EvaluationRequest

EVALUATOR_VERSION = "rubric-containment:1.0.0"


class RubricContainmentEvaluator:
    """Deterministic floor: criterion words present in the summary → pass."""

    evaluator_version = EVALUATOR_VERSION

    def evaluate(self, request: EvaluationRequest) -> list[CriterionResult]:
        summary = request.task_summary.lower()
        results: list[CriterionResult] = []
        for criterion in request.rubric.criteria:
            words = _content_words(criterion)
            if not words:
                results.append(
                    CriterionResult(criterion=criterion, status="unknown", note="empty criterion")
                )
                continue
            hits = [w for w in words if w in summary]
            if len(hits) == len(words):
                results.append(
                    CriterionResult(criterion=criterion, status="pass", note="all words present")
                )
            elif hits:
                results.append(
                    CriterionResult(
                        criterion=criterion,
                        status="unknown",
                        note=f"partial match: {sorted(hits)}",
                    )
                )
            else:
                results.append(
                    CriterionResult(
                        criterion=criterion,
                        status="unknown",
                        note="no criterion words in summary",
                    )
                )
        return results


def _content_words(criterion: str) -> list[str]:
    """Lowercased word tokens, stopwords dropped — matching is on content."""
    stop = {"a", "an", "the", "of", "and", "or", "to", "in", "is", "are", "for", "with", "that"}
    return [w for w in re.findall(r"[a-z0-9]+", criterion.lower()) if w not in stop]
