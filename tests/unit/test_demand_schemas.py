"""Demand & acquisition schema tests (crawl.md STEP 1, §2-5, §12, §15, §19).

The invariants: DemandSignal carries NO quality field (demand never
implies quality — §2.1); ExcellenceVector never compresses to a scalar
verdict (§15); every EvidenceItem has a counter_evidence field even
when empty (§19); MinedCapability separates claim types (§30).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aci.domain.acquisition.demand import (
    EXCELLENCE_DIMENSIONS,
    AcquisitionOpportunity,
    DemandSignal,
    DiscoveryCandidate,
    EvidenceItem,
    EvidencePackage,
    ExcellenceVector,
    MinedCapability,
)

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _demand(**over: object) -> DemandSignal:
    return DemandSignal(
        signal_id="sig-1",
        source="recent_client_interest",
        observed_at=NOW,
        **over,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# §2.1 Demand ≠ quality — the architectural boundary
# ---------------------------------------------------------------------------


def test_demand_signal_has_no_quality_field() -> None:
    """crawl.md §2.1: demand may decide what to investigate, never imply
    quality. The schema enforces it structurally — there is no score,
    no rating, no excellence field on a demand signal."""
    sig = _demand()
    assert not hasattr(sig, "quality")
    assert not hasattr(sig, "score")
    assert not hasattr(sig, "excellence")
    for banned in ("quality", "score", "excellence", "elite"):
        assert banned not in DemandSignal.model_fields


def test_opportunity_priority_is_investigation_not_verdict() -> None:
    """§40: P0..P5 rank INVESTIGATION urgency; the field name and the
    rationale keep that meaning — high priority is a hurting gap, not
    a quality claim about what will be found."""
    opp = AcquisitionOpportunity(
        opportunity_id="opp-1",
        topic="browser-verification",
        reasons=["recent_client_demand_spike", "corpus_gap"],
        priority="P1",
        priority_rationale="explicit client need with missing capability",
        created_at=NOW,
    )
    assert opp.priority == "P1"
    assert "missing capability" in opp.priority_rationale


def test_discovery_candidate_trend_is_never_quality() -> None:
    """§3.6: TREND = reason to investigate. The trend_signals dict is
    metadata; there is no derived 'quality' or 'excellence' field."""
    cand = DiscoveryCandidate(
        candidate_id="disc-1",
        source="github",
        source_type="repository",
        canonical_url="https://github.com/x/y",
        discovered_at=NOW,
        trend_signals={"star_velocity": 12.3, "fork_velocity": 1.1},
    )
    assert cand.trend_signals["star_velocity"] == 12.3
    assert not hasattr(cand, "quality")


# ---------------------------------------------------------------------------
# §12 capability mining — mechanisms, not repos
# ---------------------------------------------------------------------------


def test_mined_capability_separates_claim_types() -> None:
    """§30: claimed_by_author vs observed_in_code are stored separately —
    never collapsed into one 'is_good' field."""
    cap = MinedCapability(
        mined_id="mine-1",
        source_refs=["src/retry.py"],
        type="agent-pattern",
        name="iterative-tool-failure-recovery",
        claimed_benefits=["robust recovery"],
        observed_in_code=True,
        mined_by="capability-miner:1.0.0",
        mined_at=NOW,
    )
    assert cap.claimed_benefits == ["robust recovery"]  # author's words
    assert cap.observed_in_code is True  # miner's observation
    assert not hasattr(cap, "is_good")


def test_mined_capability_types_are_the_closed_set() -> None:
    with pytest.raises(ValidationError):
        MinedCapability(
            mined_id="m",
            source_refs=["x"],
            type="not-a-type",  # type: ignore[arg-type]
            name="n",
            mined_at=NOW,
        )


# ---------------------------------------------------------------------------
# §19 evidence rules
# ---------------------------------------------------------------------------


def test_evidence_item_requires_evidence_not_just_score() -> None:
    """§19: no unsupported '8.9/10' — every claim carries evidence lines."""
    item = EvidenceItem(
        claim="robust tool-failure recovery",
        evidence=["retry state transition exists in implementation"],
        counter_evidence=["timeout recovery is not tested"],
        confidence="medium_high",
    )
    assert item.evidence
    assert item.counter_evidence  # the critic's voice is structural
    with pytest.raises(ValidationError):
        EvidenceItem(claim="x", evidence=[], confidence="high")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# §15 Excellence Vector — never a scalar verdict
# ---------------------------------------------------------------------------


def test_excellence_vector_keeps_dimensions_not_scalar() -> None:
    vec = ExcellenceVector(
        vector_id="v1",
        mined_id="mine-1",
        scores={"correctness": 4.0, "novelty": 3.0, "empirical_performance": None},
        evaluated_at=NOW,
    )
    assert vec.scores["empirical_performance"] is None  # unknown stays unknown
    assert not hasattr(vec, "verdict")
    assert not hasattr(vec, "total_score")


def test_excellence_vector_rejects_unknown_dimensions() -> None:
    vec = ExcellenceVector(
        vector_id="v",
        mined_id="m",
        scores={"made_up_dimension": 5.0},
        evaluated_at=NOW,
    )
    assert vec.validate_dimensions() == ["made_up_dimension"]


def test_excellence_dimensions_cover_the_plan_set() -> None:
    """§15 verbatim list — all 21 dimensions from the plan are present."""
    expected = {
        "correctness",
        "empirical_performance",
        "reliability",
        "robustness",
        "security",
        "novelty",
        "generalizability",
        "composability",
        "maintainability",
        "observability",
        "reproducibility",
        "efficiency",
        "token_efficiency",
        "latency",
        "documentation",
        "maturity",
        "provenance_confidence",
        "freshness",
        "task_relevance",
        "capability_gain",
        "redundancy",
    }
    assert expected == set(EXCELLENCE_DIMENSIONS)


# ---------------------------------------------------------------------------
# §54 evidence package — controlled context for judges
# ---------------------------------------------------------------------------


def test_evidence_package_is_the_judge_context() -> None:
    pkg = EvidencePackage(
        package_id="pkg-1",
        mined_id="mine-1",
        security_findings=["pipe-to-shell in install docs"],
        assembled_at=NOW,
    )
    assert pkg.security_findings
    # judges consume this package — it has no verdict field either
    assert not hasattr(pkg, "verdict")
