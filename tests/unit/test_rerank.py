"""Unit tests for the heuristic reranker (plan §17; §52 Phase 7 acceptance)."""

from aci.domain.capability.models import Compatibility, TaskContext
from aci.domain.policy.models import EligibleCandidate, RoutingRequestContext
from aci.domain.routing.models import ScoredCandidate, TaskDescriptor
from aci.routing.rerankers.heuristic import HeuristicReranker

DIGEST = "sha256:" + "ab" * 32


def candidate(capability_id: str, **overrides: object) -> EligibleCandidate:
    base: dict = {
        "capability_id": capability_id,
        "version": "1.0.0",
        "digest": DIGEST,
        "kind": "skill",
        "channel": "production",
        "status": "active",
    }
    base.update(overrides)
    return EligibleCandidate.model_validate(base)


def scored(
    capability_id: str,
    *,
    score: float,
    text: str = "",
    **candidate_overrides: object,
) -> ScoredCandidate:
    return ScoredCandidate(
        candidate=candidate(capability_id, **candidate_overrides),
        score=score,
        document_text=text,
    )


def task(text: str = "debug a python traceback", **context: object) -> TaskDescriptor:
    return TaskDescriptor(
        task_text=text,
        context=TaskContext.model_validate(context) if context else TaskContext(),
    )


def context() -> RoutingRequestContext:
    return RoutingRequestContext.model_validate(
        {
            "client": {"type": "opencode", "supported_features": ["skills"]},
            "scope": {"principal_id": "p-1"},
        }
    )


def test_rerank_beats_retrieval_only_baseline() -> None:
    """§52 acceptance: reranking must be comparable against retrieval-only.

    v3 calibration semantics: signals are min-max normalized within the
    candidate set, so influence = declared weight × observed spread. Two
    properties (probed on the real 30-candidate pipeline):

    1. A promotion must be earned: near-top retrieval + full token
       overlap + verified trust beats the raw-cosine leader.
    2. A huge semantic gap is NOT overturned by lexical signals — the
       defect v3 fixes was the lexical signal carrying ~2x its declared
       weight and demoting rank-2-retrieval candidates.
    """
    reranker = HeuristicReranker()

    # Property 1: earned promotion (three candidates anchor the min).
    earned = reranker.rerank(
        task(),
        [
            scored("a-leader", score=0.95, text="write marketing copy", trust_tier="untrusted"),
            scored(
                "b-relevant",
                score=0.85,
                text="debug python traceback root cause analysis",
                trust_tier="verified",
            ),
            scored("c-anchor", score=0.45, text="sql", trust_tier="untrusted"),
        ],
        context(),
    )
    assert earned.ranked[0].candidate.capability_id == "b-relevant"
    assert earned.ranked[0].rank == 1
    # The reranker reordered against the retrieval-only baseline.
    retrieval_only = sorted(earned.ranked, key=lambda r: r.retrieval_score, reverse=True)
    assert retrieval_only[0].candidate.capability_id == "a-leader"

    # Property 2: a huge semantic gap stays on top.
    blowout = reranker.rerank(
        task(),
        [
            scored("a-far", score=0.95, text="write marketing copy", trust_tier="untrusted"),
            scored("b-weak", score=0.55, text="debug python traceback", trust_tier="standard"),
            scored("c-anchor", score=0.45, text="sql", trust_tier="untrusted"),
        ],
        context(),
    )
    assert blowout.ranked[0].candidate.capability_id == "a-far"


def test_rerank_records_reasons_and_trace() -> None:
    result = HeuristicReranker().rerank(
        task(),
        [scored("c-1", score=0.6, text="debug python traceback", trust_tier="verified")],
        context(),
    )
    assert result.trace.implementation == "heuristic-reranker"
    assert result.trace.version == "3"
    assert result.trace.input_count == 1
    assert result.trace.output_count == 1
    top = result.ranked[0]
    # A single candidate cannot discriminate: every calibrated signal is 0.
    assert top.reasons == [
        "retrieval=0.000",
        "token_overlap=0.000",
        "facet_match=0.000",
        "trust=0.000",
    ]
    assert top.retrieval_score == 0.6


def test_calibration_makes_declared_weights_true_influence() -> None:
    """v3: raw ranges are incomparable; normalized ones are not.

    Retrieval cosine lives in a narrow band (~[0.47, 0.55] on the real
    corpus) while token_overlap spans [0, 1]; uncalibrated, the 0.3-weight
    lexical signal out-influenced the 0.5-weight retrieval signal. After
    calibration the candidate with the higher retrieval score wins when
    the lexical signal is equal, and the trace records normalized values.
    """
    reranker = HeuristicReranker()
    result = reranker.rerank(
        task("debug python"),
        [
            scored("c-low", score=0.50, text="debug python", trust_tier="verified"),
            scored("c-high", score=0.55, text="debug python", trust_tier="verified"),
        ],
        context(),
    )
    assert result.ranked[0].candidate.capability_id == "c-high"
    reasons = {r.candidate.capability_id: r.reasons for r in result.ranked}
    assert reasons["c-high"] == [
        "retrieval=1.000",
        "token_overlap=0.000",
        "facet_match=0.000",
        "trust=0.000",
    ]
    assert reasons["c-low"] == [
        "retrieval=0.000",
        "token_overlap=0.000",
        "facet_match=0.000",
        "trust=0.000",
    ]


def test_rerank_never_adds_candidates() -> None:
    """ADR-009: the reranker reorders its input; it can never rescue or add."""
    candidates = [
        scored("c-1", score=0.2, text="sql"),
        scored("c-2", score=0.9, text="python debug"),
    ]
    result = HeuristicReranker().rerank(task("anything"), candidates, context())
    assert {r.candidate.capability_id for r in result.ranked} == {"c-1", "c-2"}
    assert result.trace.input_count == result.trace.output_count == 2


def test_rerank_empty_input_is_valid() -> None:
    result = HeuristicReranker().rerank(task(), [], context())
    assert result.ranked == []
    assert result.trace.input_count == 0
    assert result.trace.output_count == 0


def test_facet_match_signal_from_facets_and_compatibility() -> None:
    reranker = HeuristicReranker()
    result = reranker.rerank(
        task("write code", language="python"),
        [
            scored("c-none", score=0.9, text="write code"),
            scored(
                "c-facet",
                score=0.9,
                text="write code",
                facets={"technology": ["python"]},
            ),
            scored(
                "c-compat",
                score=0.9,
                text="write code",
                compatibility=Compatibility.model_validate({"supported_languages": ["Python"]}),
            ),
        ],
        context(),
    )
    # Both matching candidates outrank the non-matching one at equal retrieval.
    ids = [r.candidate.capability_id for r in result.ranked]
    assert ids.index("c-none") > ids.index("c-facet")
    assert ids.index("c-none") > ids.index("c-compat")
    facet_reasons = {r.candidate.capability_id: r.reasons for r in result.ranked}
    assert "facet_match=1.000" in facet_reasons["c-facet"]
    assert "facet_match=1.000" in facet_reasons["c-compat"]
    assert "facet_match=0.000" in facet_reasons["c-none"]


def test_rerank_is_deterministic_with_stable_tiebreak() -> None:
    candidates = [
        scored("b", score=0.5, text="same text", trust_tier="verified"),
        scored("a", score=0.5, text="same text", trust_tier="verified"),
    ]
    first = HeuristicReranker().rerank(task("same text"), candidates, context())
    second = HeuristicReranker().rerank(task("same text"), list(reversed(candidates)), context())
    assert [r.candidate.capability_id for r in first.ranked] == ["a", "b"]
    assert [r.candidate.capability_id for r in second.ranked] == ["a", "b"]


def test_interface_is_swappable() -> None:
    """§52 acceptance: any implementation can stand in behind the protocol."""

    class ThresholdReranker:
        def rerank(
            self,
            task: TaskDescriptor,
            candidates: list[ScoredCandidate],
            context: RoutingRequestContext,
        ) -> object:
            kept = [c for c in candidates if c.score > 0.4]
            from aci.domain.routing.models import RerankResult, RerankTrace

            return RerankResult(
                ranked=[],
                trace=RerankTrace(
                    implementation="threshold",
                    version="1",
                    input_count=len(candidates),
                    output_count=len(kept),
                ),
            )

    from aci.application.protocols import CapabilityReranker

    def run(r: CapabilityReranker) -> object:
        return r.rerank(task(), [scored("c-1", score=0.9)], context())

    result = run(ThresholdReranker())  # type: ignore[arg-type]
    assert result.trace.output_count == 1  # type: ignore[attr-defined]
    assert HeuristicReranker().rerank(task(), [scored("c-1", score=0.9)], context()).trace
