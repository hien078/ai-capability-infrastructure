"""Acquisition candidate lifecycle tests (auto.md §7).

The invariants that matter: quarantine is unskippable, promotion only
from promotion_proposed (the human/policy gate), terminal states are
immutable, and every transition records who decided (§40 audit).
"""

from datetime import UTC, datetime

import pytest

from aci.domain.acquisition.models import (
    CandidateProposal,
    CandidateRecord,
    advance_candidate,
)

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _proposal(cid: str = "cand-1") -> CandidateProposal:
    return CandidateProposal(
        candidate_id=cid,
        gap_id="gap-0142",
        source_repo="https://github.com/example/repo.git",
        source_path="skills/database-migration",
        observed_revision="abc123",
        reason="Contains a documented migration debugging procedure",
        signals={"source_known": False, "likely_relevant": True},
        discovery_confidence=0.82,
        proposed_at=NOW,
    )


def _record() -> CandidateRecord:
    return CandidateRecord(proposal=_proposal())


# ---------------------------------------------------------------------------
# Happy path walks the FULL lifecycle — no skipping
# ---------------------------------------------------------------------------


def test_happy_path_walks_every_stage_in_order() -> None:
    r = _record()
    for status in (
        "approved_for_fetch",
        "fetched",
        "quarantined",
        "ingestion_passed",
        "canonicalized",
        "staging",
        "promotion_proposed",
        "promoted",
    ):
        r = advance_candidate(r, status, decided_by="pipeline", decided_at=NOW)  # type: ignore[arg-type]
    assert r.status == "promoted"
    assert [s for s, _ in r.history] == [
        "approved_for_fetch",
        "fetched",
        "quarantined",
        "ingestion_passed",
        "canonicalized",
        "staging",
        "promotion_proposed",
        "promoted",
    ]


def test_quarantine_is_unskippable() -> None:
    """fetched → ingestion_passed directly is illegal: every candidate
    passes through quarantine (§10: raw snapshot is never client-visible)."""
    r = advance_candidate(_record(), "approved_for_fetch", decided_by="human", decided_at=NOW)
    r = advance_candidate(r, "fetched", decided_by="fetcher", decided_at=NOW)
    with pytest.raises(ValueError, match="illegal transition"):
        advance_candidate(r, "ingestion_passed", decided_by="pipeline", decided_at=NOW)


def test_promotion_requires_proposal_step() -> None:
    """staging → promoted directly is illegal: the human/policy gate
    (promotion_proposed, §24) cannot be bypassed by automation."""
    r = _record()
    for status in (
        "approved_for_fetch",
        "fetched",
        "quarantined",
        "ingestion_passed",
        "canonicalized",
        "staging",
    ):
        r = advance_candidate(r, status, decided_by="pipeline", decided_at=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="illegal transition"):
        advance_candidate(r, "promoted", decided_by="bot", decided_at=NOW)


def test_terminal_states_are_immutable() -> None:
    for terminal in ("promoted", "rejected_license", "rejected_security", "superseded"):
        r = CandidateRecord(
            proposal=_proposal(),
            status=terminal,  # type: ignore[arg-type]
            decided_at=NOW,
        )
        with pytest.raises(ValueError, match="terminal state"):
            advance_candidate(r, "proposed", decided_by="anyone", decided_at=NOW)


def test_rejection_records_reason_and_actor() -> None:
    r = advance_candidate(_record(), "approved_for_fetch", decided_by="human", decided_at=NOW)
    r = advance_candidate(r, "fetched", decided_by="fetcher", decided_at=NOW)
    r = advance_candidate(r, "quarantined", decided_by="pipeline", decided_at=NOW)
    r = advance_candidate(
        r,
        "rejected_license",
        decided_by="license-detector",
        decided_at=NOW,
        rejection_reason="unknown license, no human approval (§24)",
    )
    assert r.status == "rejected_license"
    assert r.rejection_reason is not None and "§24" in r.rejection_reason
    assert r.history[-1] == ("rejected_license", "license-detector")


def test_advance_never_mutates_in_place() -> None:
    r = _record()
    advanced = advance_candidate(r, "approved_for_fetch", decided_by="h", decided_at=NOW)
    assert r.status == "proposed" and advanced.status == "approved_for_fetch"
    assert advanced is not r


def test_discovery_confidence_is_not_quality() -> None:
    """§6: discovery confidence is about the SEARCH. The schema keeps it
    separate from any quality/verdict field — there is none on the
    proposal, by design."""
    p = _proposal()
    assert p.discovery_confidence == 0.82
    assert not hasattr(p, "quality_confidence")


def test_proposal_bounds_confidence() -> None:
    """The constructor bounds discovery_confidence to [0, 1] (§6: it is a
    search signal, never a percentage quality claim)."""
    with pytest.raises(Exception, match="discovery_confidence"):
        CandidateProposal(
            candidate_id="c",
            source_repo="r",
            source_path="p",
            observed_revision="a",
            reason="x",
            discovery_confidence=1.5,
            proposed_at=NOW,
        )
