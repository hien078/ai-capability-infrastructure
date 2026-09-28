"""Heuristic reranker — V1 baseline (plan §17; §52 Phase 7).

Pure, offline, deterministic. Consumes ONLY trusted routing metadata (§17.1):
the retrieval score, the trusted routing document text (sanitized summary),
facets/compatibility, and the trust tier. Raw skill bodies are never seen.

Signals (weights sum to 1.0):
  retrieval     — clamped cosine similarity from the retrieval stage
  token_overlap — fraction of task tokens present in the trusted document
  facet_match   — task language/framework matches candidate technology facets
                  or declared compatibility (§48: filter already ran; this is
                  a soft preference among compatible candidates)
  trust         — prior from the security assessment tier

The interface (CapabilityReranker) is swappable: an embedding or LLM
reranker can replace this without touching the pipeline (§52 acceptance).

v2 (§75 step 23): token_overlap folds tokens with the shared domain
normalizer — the same one the embedder uses — so the signal bridges
morphology ("tests" vs "test") instead of exact-match only.
"""

from pydantic import BaseModel

from aci.domain.policy.models import EligibleCandidate, RoutingRequestContext
from aci.domain.routing.models import (
    RankedCandidate,
    RerankResult,
    RerankTrace,
    ScoredCandidate,
    TaskDescriptor,
)
from aci.domain.routing.text import normalize_tokens

IMPLEMENTATION = "heuristic-reranker"
VERSION = "2"

_TRUST_PRIOR: dict[str, float] = {"verified": 1.0, "standard": 0.6, "untrusted": 0.3}


class HeuristicWeights(BaseModel):
    """Tunable signal weights; kept as data so benchmarks can vary them."""

    model_config = {"frozen": True}

    retrieval: float = 0.5
    token_overlap: float = 0.3
    facet_match: float = 0.1
    trust: float = 0.1


def _tokens(text: str) -> set[str]:
    return set(normalize_tokens(text))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class HeuristicReranker:
    """Implements the CapabilityReranker protocol (§44/§17)."""

    def __init__(self, weights: HeuristicWeights | None = None) -> None:
        self._weights = weights if weights is not None else HeuristicWeights()

    def rerank(
        self,
        task: TaskDescriptor,
        candidates: list[ScoredCandidate],
        context: RoutingRequestContext,
    ) -> RerankResult:
        _ = context  # client/scope already constrained eligibility; no signal here
        task_tokens = _tokens(task.task_text)
        ranked: list[RankedCandidate] = []
        for scored in candidates:
            signals = self._signals(task, task_tokens, scored.candidate, scored)
            score = sum(getattr(self._weights, name) * value for name, value in signals.items())
            ranked.append(
                RankedCandidate(
                    candidate=scored.candidate,
                    score=score,
                    retrieval_score=scored.score,
                    rank=0,
                    reasons=[f"{name}={value:.3f}" for name, value in signals.items()],
                    document_text=scored.document_text,
                )
            )
        # Deterministic: score desc, then natural key asc.
        ranked.sort(key=lambda r: (-r.score, r.candidate.capability_id, r.candidate.version))
        for position, item in enumerate(ranked, start=1):
            ranked[position - 1] = item.model_copy(update={"rank": position})
        return RerankResult(
            ranked=ranked,
            trace=RerankTrace(
                implementation=IMPLEMENTATION,
                version=VERSION,
                input_count=len(candidates),
                output_count=len(ranked),
            ),
        )

    def _signals(
        self,
        task: TaskDescriptor,
        task_tokens: set[str],
        candidate: EligibleCandidate,
        scored: ScoredCandidate,
    ) -> dict[str, float]:
        document_tokens = _tokens(scored.document_text)
        overlap = len(task_tokens & document_tokens) / len(task_tokens) if task_tokens else 0.0
        facet_match = self._facet_match(task, candidate)
        return {
            "retrieval": _clamp(scored.score),
            "token_overlap": _clamp(overlap),
            "facet_match": facet_match,
            "trust": _TRUST_PRIOR.get(candidate.trust_tier, 0.3),
        }

    def _facet_match(self, task: TaskDescriptor, candidate: EligibleCandidate) -> float:
        """Soft technology/language preference among already-eligible candidates."""
        wanted = {t.lower() for t in [task.context.language or "", *task.context.frameworks] if t}
        if not wanted:
            return 0.0
        declared: set[str] = set(candidate.facets.get("technology", []))
        compatibility = candidate.compatibility
        if compatibility is not None:
            declared.update(x.lower() for x in compatibility.supported_languages)
            declared.update(x.lower() for x in compatibility.supported_frameworks)
        declared = {d.lower() for d in declared}
        return 1.0 if wanted & declared else 0.0
