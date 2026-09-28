"""Refinery tests (auto2.md §26-31, Phases 7-10).

The invariants: dedupe MATCHES never MERGES; clusters are comparison
groups (§26: never auto-merge on proximity — the relations-experiment
evidence: merging near-dups made recall WORSE); comparisons cite
per-cell evidence (§29); synthesis retains ALL lineage (§30); quality
is advisory with deliberately no pass/fail (§31).
"""

from datetime import UTC, datetime

from aci.application.refine import (
    HeuristicComparator,
    HeuristicSynthesizer,
    cluster,
    dedupe,
    quality_report,
)
from aci.domain.acquisition.extraction import ArtifactFileRef, RawCandidate

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _cand(cid: str, name: str, digest: str, provides: list[str], conf: float = 0.9) -> RawCandidate:
    return RawCandidate(
        candidate_id=cid,
        group_id="g",
        proposed_name=name,
        primary_files=[ArtifactFileRef(path=f"{name}/SKILL.md", sha256=digest)],
        inferred_provides=provides,
        detected_at=NOW,
        boundary_confidence=conf,
    )


# ---------------------------------------------------------------------------
# §27 dedupe — three layers, matches never merges
# ---------------------------------------------------------------------------


def test_exact_duplicate_by_digest() -> None:
    a = _cand("a", "debugging", "a" * 64, ["debugging"])
    b = _cand("b", "other-debugging", "a" * 64, ["other"])  # same digest
    m = dedupe([a, b])
    assert m == [] or m[0].kind == "exact"
    assert any(x.kind == "exact" and x.similarity == 1.0 for x in m)


def test_near_duplicate_by_text_jaccard() -> None:
    a = _cand("a", "debugging", "1" * 64, [])
    b = _cand("b", "debugging-two", "2" * 64, [])
    texts = {
        "a": "reproduce the failure collect evidence isolate variables find root cause",
        "b": "reproduce the failure collect evidence isolate variables identify root cause",
    }
    m = dedupe([a, b], texts=texts)
    assert any(x.kind == "near" and x.similarity >= 0.75 for x in m)


def test_semantic_duplicate_by_provides_overlap() -> None:
    a = _cand("a", "debugging", "1" * 64, ["root-cause-analysis", "bug-diagnosis", "verification"])
    b = _cand(
        "b",
        "systematic-debugging",
        "2" * 64,
        ["root-cause-analysis", "bug-diagnosis", "reproduce-failure"],
    )
    m = dedupe([a, b])
    assert any(x.kind == "semantic" and x.similarity >= 0.5 for x in m)


def test_distinct_candidates_match_nothing() -> None:
    a = _cand("a", "debugging", "1" * 64, ["debugging"])
    b = _cand("b", "canvas-painting", "2" * 64, ["painting"])
    texts = {"a": "reproduce failure evidence", "b": "pick colors brush strokes palette"}
    assert dedupe([a, b], texts=texts) == []


def test_matches_are_data_not_actions() -> None:
    """§27: the output is a list of DuplicateMatch — there is no merge,
    no delete, no status change. The CALLER decides (rejected_duplicate
    or variant link) from the evidence."""
    a = _cand("a", "x", "1" * 64, [])
    b = _cand("b", "x", "1" * 64, [])
    m = dedupe([a, b])
    assert all(hasattr(x, "detail") for x in m)  # evidence-bearing
    assert not any(hasattr(x, "action") for x in m)  # no action field exists


# ---------------------------------------------------------------------------
# §26 clustering — comparison groups, never merges
# ---------------------------------------------------------------------------


def test_cluster_groups_by_shared_provides() -> None:
    a = _cand("a", "debugging", "1" * 64, ["root-cause-analysis"])
    b = _cand("b", "systematic-debugging", "2" * 64, ["root-cause-analysis"])
    c = _cand("c", "canvas", "3" * 64, ["painting"])
    clusters = cluster([a, b, c], now=NOW)
    fams = {cl.family: cl.members for cl in clusters}
    assert "root-cause-analysis" in fams
    assert set(fams["root-cause-analysis"]) == {"a", "b"}
    # canvas alone is NOT a cluster — a comparison group needs ≥2 members
    assert "painting" not in fams


def test_cluster_never_mutates_members() -> None:
    """§26: membership is a LIST OF IDS for comparison — the cluster
    carries no merge, no demotion, no promotion."""
    a = _cand("a", "debugging", "1" * 64, ["root-cause"])
    b = _cand("b", "other", "2" * 64, ["root-cause"])
    cl = cluster([a, b], now=NOW)[0]
    assert sorted(cl.members) == ["a", "b"]
    assert not hasattr(cl, "merged_into")


# ---------------------------------------------------------------------------
# §29 comparative analysis — matrix with per-cell evidence
# ---------------------------------------------------------------------------


def test_comparison_matrix_cites_evidence_per_cell() -> None:
    a = _cand("a", "debugging", "1" * 64, ["root-cause"], conf=0.9)
    b = _cand("b", "loose-notes", "2" * 64, ["root-cause"], conf=0.3)
    cl = cluster([a, b], now=NOW)[0]
    report = HeuristicComparator().compare(cl, [a, b], now=NOW)
    assert report.cluster_id == cl.cluster_id
    # every cell carries non-empty evidence (§29 requirement)
    assert report.cells and all(c.evidence for c in report.cells)
    by_dim = {d: [c for c in report.cells if c.dimension == d] for d in report.dimensions}
    assert len(by_dim["boundary_confidence"]) == 2  # both members compared


# ---------------------------------------------------------------------------
# §30 canonical synthesis — lineage retained
# ---------------------------------------------------------------------------


def test_synthesis_retains_all_lineage() -> None:
    """§30 verbatim: 'Do not erase source lineage' — derived_from lists
    EVERY member, even the weak ones."""
    a = _cand("a", "debugging", "1" * 64, ["root-cause"], conf=0.9)
    b = _cand("b", "loose-notes", "2" * 64, ["root-cause"], conf=0.3)
    cl = cluster([a, b], now=NOW)[0]
    synth = HeuristicSynthesizer().synthesize(cl, [a, b], now=NOW)
    assert sorted(synth.derived_from) == ["a", "b"]
    assert synth.proposed_name == "debugging"  # strongest member wins
    assert synth.procedure and synth.synthesized_by


def test_synthesis_is_still_a_proposal() -> None:
    """The synthesis walks the §12.2 lifecycle like any candidate — it
    carries no status, no approval, no production claim."""
    a = _cand("a", "debugging", "1" * 64, ["root-cause"])
    cl = cluster([a, _cand("b", "x", "2" * 64, ["root-cause"])], now=NOW)[0]
    synth = HeuristicSynthesizer().synthesize(cl, [a], now=NOW)
    assert not hasattr(synth, "status")
    assert not hasattr(synth, "approved")


# ---------------------------------------------------------------------------
# §31 quality filter — advisory, never an approval
# ---------------------------------------------------------------------------


def test_quality_report_is_advisory_only() -> None:
    """§31 verbatim: never equate quality score with production
    approval. The report deliberately has NO pass/fail field."""
    a = _cand("a", "debugging", "1" * 64, ["root-cause"], conf=0.9)
    q = quality_report(a, text="reproduce failure collect evidence verify", now=NOW)
    assert q.signals["boundary_confidence"] == 0.9
    assert not hasattr(q, "passed")
    assert not hasattr(q, "approved")


def test_quality_signals_are_deterministic() -> None:
    a = _cand("a", "debugging", "1" * 64, ["root-cause"])
    q1 = quality_report(a, text="same text", now=NOW)
    q2 = quality_report(a, text="same text", now=NOW)
    assert q1.signals == q2.signals
