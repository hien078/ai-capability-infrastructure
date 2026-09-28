"""Refinery services (auto2.md §26-31, Phases 7-10) — deterministic floor.

Every stage is proposal-grade and deterministic (§1.3): the LLM variants
(comparative analyzer, canonical synthesizer with real methodology
extraction) implement the same protocols later; the heuristic floor
keeps the pipeline runnable offline and honest about what it CANNOT
know (§26: never auto-merge on embedding proximity — the
relations-experiment evidence: merging near-dups made recall WORSE,
0.712 → 0.659).

- dedupe (§27): exact (digest) / near (token jaccard) / semantic
  (provides overlap) — matches, never merges.
- cluster (§26): group by shared family signal (provides/name stem) —
  comparison groups, nothing more.
- compare (§29): matrix over shared dimensions with per-cell evidence.
- synthesize (§30): vendor-neutral procedure from the strongest member,
  ALL lineage retained in derived_from.
"""

import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Protocol

from aci.domain.acquisition.extraction import RawCandidate
from aci.domain.acquisition.refinery import (
    CandidateCluster,
    CanonicalSynthesis,
    ComparisonCell,
    ComparisonReport,
    DuplicateMatch,
    QualityReport,
)

_WORD = re.compile(r"[a-z0-9]{2,}")
_STOP = {"the", "and", "for", "with", "that", "this", "you", "your", "use", "when"}


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# §27 Duplicate detection — three layers, matches never merges
# ---------------------------------------------------------------------------


def dedupe(
    candidates: list[RawCandidate],
    *,
    texts: dict[str, str] | None = None,
    exact_threshold: float = 1.0,
    near_threshold: float = 0.75,
    semantic_threshold: float = 0.5,
) -> list[DuplicateMatch]:
    """Pairwise duplicate detection (§27). `texts` maps candidate_id →
    body text (optional; without it only exact/near-by-name run).

    Exact = same content digest (always 1.0). Near = token-jaccard over
    the body. Semantic = provides-set overlap (different wording, same
    methodology). Every match is EVIDENCE for the refinery — the caller
    decides (rejected_duplicate or variant link), never this function.
    """
    texts = texts or {}
    matches: list[DuplicateMatch] = []
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            # exact: same primary-file digest
            a_dig = {f.sha256 for f in a.primary_files}
            b_dig = {f.sha256 for f in b.primary_files}
            if a_dig & b_dig:
                matches.append(
                    DuplicateMatch(
                        left=a.candidate_id,
                        right=b.candidate_id,
                        kind="exact",
                        similarity=1.0,
                        detail="shared primary-file digest",
                    )
                )
                continue
            # near: body token jaccard
            ta, tb = _tokens(texts.get(a.candidate_id, "")), _tokens(texts.get(b.candidate_id, ""))
            if ta and tb:
                j = _jaccard(ta, tb)
                if j >= near_threshold:
                    matches.append(
                        DuplicateMatch(
                            left=a.candidate_id,
                            right=b.candidate_id,
                            kind="near",
                            similarity=round(j, 3),
                            detail=f"token jaccard {j:.3f}",
                        )
                    )
                    continue
            # semantic: provides overlap
            pa, pb = set(a.inferred_provides), set(b.inferred_provides)
            if pa and pb:
                ov = _jaccard(pa, pb)
                if ov >= semantic_threshold:
                    matches.append(
                        DuplicateMatch(
                            left=a.candidate_id,
                            right=b.candidate_id,
                            kind="semantic",
                            similarity=round(ov, 3),
                            detail=f"provides overlap {ov:.3f}",
                        )
                    )
    return matches


# ---------------------------------------------------------------------------
# §26 Clustering — comparison groups, never merges
# ---------------------------------------------------------------------------


def cluster(
    candidates: list[RawCandidate],
    *,
    now: datetime | None = None,
) -> list[CandidateCluster]:
    """Group candidates by family signal (shared provides term or name
    stem). §26: a cluster is a COMPARISON GROUP — membership does not
    merge, demote, or promote anything."""
    formed_at = now or datetime.now(UTC)
    by_signal: dict[str, list[str]] = defaultdict(list)
    for c in candidates:
        signals = set(c.inferred_provides) or {c.proposed_name.split("-")[0]}
        for s in signals:
            by_signal[s].append(c.candidate_id)
    # one candidate may appear in several clusters — that is honest
    # (a debugging skill may also be a testing skill); comparison
    # handles the overlap.
    out: list[CandidateCluster] = []
    for i, (signal, members) in enumerate(sorted(by_signal.items())):
        if len(members) < 2:
            continue  # a comparison group needs something to compare
        out.append(
            CandidateCluster(
                cluster_id=f"cluster-{i:03d}",
                family=signal,
                members=sorted(set(members)),
                formed_at=formed_at,
            )
        )
    return out


# ---------------------------------------------------------------------------
# §29 Comparative analysis — matrix with per-cell evidence
# ---------------------------------------------------------------------------

#: §29 comparison dimensions (heuristic floor: structural signals).
DIMENSIONS = ("provides_count", "supporting_files", "boundary_confidence", "name_clarity")


class ComparativeAnalyzer(Protocol):
    """§29 pluggable — the LLM implementation compares METHODOLOGY
    (reproduction steps, evidence gathering...); the heuristic floor
    compares structure. Both must cite evidence per cell."""

    analyzer_version: str

    def compare(
        self, cluster: CandidateCluster, candidates: list[RawCandidate], *, now: datetime
    ) -> ComparisonReport: ...


class HeuristicComparator:
    analyzer_version = "heuristic-comparator:1.0.0"

    def compare(
        self,
        cluster: CandidateCluster,
        candidates: list[RawCandidate],
        *,
        now: datetime | None = None,
    ) -> ComparisonReport:
        at = now or datetime.now(UTC)
        by_id = {c.candidate_id: c for c in candidates}
        cells: list[ComparisonCell] = []
        for member in cluster.members:
            c = by_id.get(member)
            if c is None:
                continue
            cells.append(
                ComparisonCell(
                    candidate_id=member,
                    dimension="provides_count",
                    value=str(len(c.inferred_provides)),
                    evidence=f"inferred_provides={c.inferred_provides}",
                )
            )
            cells.append(
                ComparisonCell(
                    candidate_id=member,
                    dimension="supporting_files",
                    value=str(len(c.supporting_files)),
                    evidence=f"{len(c.supporting_files)} supporting file(s) in the group",
                )
            )
            cells.append(
                ComparisonCell(
                    candidate_id=member,
                    dimension="boundary_confidence",
                    value=str(c.boundary_confidence),
                    evidence=f"detected_by={c.detected_by}",
                )
            )
            words = len(_tokens(c.proposed_name))
            cells.append(
                ComparisonCell(
                    candidate_id=member,
                    dimension="name_clarity",
                    value=str(words),
                    evidence=f"proposed_name={c.proposed_name!r}",
                )
            )
        return ComparisonReport(
            cluster_id=cluster.cluster_id,
            dimensions=list(DIMENSIONS),
            cells=cells,
            produced_by=self.analyzer_version,
            produced_at=at,
        )


# ---------------------------------------------------------------------------
# §30 Canonical synthesis — vendor-neutral, lineage-preserving
# ---------------------------------------------------------------------------


class CanonicalSynthesizer(Protocol):
    """§30 pluggable — the LLM implementation extracts methodology from
    the strongest members; the heuristic floor derives a procedure from
    the members' names/provides. BOTH retain derived_from lineage."""

    synthesizer_version: str

    def synthesize(
        self,
        cluster: CandidateCluster,
        candidates: list[RawCandidate],
        *,
        now: datetime,
    ) -> CanonicalSynthesis: ...


class HeuristicSynthesizer:
    synthesizer_version = "heuristic-synthesizer:1.0.0"

    def synthesize(
        self,
        cluster: CandidateCluster,
        candidates: list[RawCandidate],
        *,
        now: datetime | None = None,
    ) -> CanonicalSynthesis:
        at = now or datetime.now(UTC)
        members = [c for c in candidates if c.candidate_id in cluster.members]
        if not members:
            raise ValueError(f"cluster {cluster.cluster_id} has no resolvable members")
        # strongest member = highest boundary confidence (the entrypoint
        # convention beat the loose-file guesses); ties break by name.
        strongest = max(members, key=lambda c: (c.boundary_confidence, c.proposed_name))
        provides = sorted(
            {p for c in members for p in c.inferred_provides} or {strongest.proposed_name}
        )
        procedure = [
            f"follow the {strongest.proposed_name} methodology",
            *(f"apply {p}" for p in provides[:4]),
        ]
        return CanonicalSynthesis(
            synthesis_id=f"synth-{cluster.cluster_id}",
            cluster_id=cluster.cluster_id,
            proposed_name=strongest.proposed_name,
            provides=provides,
            procedure=procedure,
            derived_from=sorted(c.candidate_id for c in members),
            synthesized_by=self.synthesizer_version,
            synthesized_at=at,
        )


# ---------------------------------------------------------------------------
# §31 Quality filter — advisory signals, never an approval
# ---------------------------------------------------------------------------


def quality_report(
    candidate: RawCandidate,
    *,
    text: str = "",
    now: datetime | None = None,
) -> QualityReport:
    """§31 advisory signals. Deliberately NO pass/fail: a quality score
    is never equated with production approval (§31 verbatim)."""
    at = now or datetime.now(UTC)
    toks = _tokens(text) or _tokens(candidate.proposed_name)
    signals = {
        "boundary_confidence": candidate.boundary_confidence,
        "provides_count": float(len(candidate.inferred_provides)),
        "supporting_files": float(len(candidate.supporting_files)),
        "text_richness": float(len(toks)) / 100.0,
    }
    return QualityReport(
        candidate_id=candidate.candidate_id,
        signals={k: round(v, 3) for k, v in signals.items()},
        produced_at=at,
    )
