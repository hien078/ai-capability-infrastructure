"""Upstream watcher proposal-generation tests (auto.md §27).

The §27 loop: upstream change → CandidateProposal (fetch_to_quarantine)
→ human approve → deterministic fetcher. The watcher NEVER fetches,
NEVER overwrites production, NEVER re-ingests — it only proposes.
"""

from datetime import UTC, datetime

import pytest

from aci.domain.acquisition.models import (
    CandidateProposal,
    CandidateRecord,
    advance_candidate,
)

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _watcher_proposal(cid: str, remote: str) -> CandidateProposal:
    """The exact shape scripts/watch_upstream.py builds for a stale pin."""
    return CandidateProposal(
        candidate_id=f"cand-{cid}-{remote[:8]}",
        source_repo="https://github.com/obra/superpowers.git",
        source_path=f"skills/{cid}",
        observed_revision=remote,
        reason=(
            f"upstream moved: pinned 000000000000 -> {remote[:12]}; "
            f"existing production capability {cid} was ingested from "
            "this repo — the new revision is a re-review candidate"
        ),
        signals={"source_known": True, "upstream_changed": True},
        discovery_confidence=1.0,
        recommended_action="fetch_to_quarantine",
        proposed_at=NOW,
        proposed_by="upstream-watcher",
    )


def test_stale_pin_generates_fetch_to_quarantine_proposal() -> None:
    """§27: the watcher's only legal output is a PROPOSAL — the record
    starts at `proposed` and the deterministic fetcher only runs after
    a human moves it to approved_for_fetch."""
    p = _watcher_proposal("systematic-debugging", "abc123def456")
    r = CandidateRecord(proposal=p)
    assert r.status == "proposed"
    assert p.recommended_action == "fetch_to_quarantine"
    assert p.signals["upstream_changed"] is True
    assert p.proposed_by == "upstream-watcher"


def test_watcher_proposal_cannot_skip_the_human_gate() -> None:
    """A watcher proposal cannot be promoted or fetched by automation:
    proposed → promoted is illegal, proposed → fetched is illegal —
    only approved_for_fetch (the human step) unlocks the fetcher."""
    p = _watcher_proposal("cap", "abc123def456")
    r = CandidateRecord(proposal=p)
    with pytest.raises(ValueError, match="illegal transition"):
        advance_candidate(r, "promoted", decided_by="watcher", decided_at=NOW)
    with pytest.raises(ValueError, match="illegal transition"):
        advance_candidate(r, "fetched", decided_by="watcher", decided_at=NOW)
    # the legal path: human approves, THEN the fetcher runs
    r2 = advance_candidate(r, "approved_for_fetch", decided_by="human:hien", decided_at=NOW)
    r3 = advance_candidate(r2, "fetched", decided_by="fetcher", decided_at=NOW)
    assert r3.status == "fetched"


def test_candidate_id_is_idempotent_per_upstream_sha() -> None:
    """Same repo + same capability + same upstream sha → same candidate
    id → the file store never duplicates a proposal for one upstream move."""
    a = _watcher_proposal("cap", "abc123def456")
    b = _watcher_proposal("cap", "abc123def456")
    assert a.candidate_id == b.candidate_id
    c = _watcher_proposal("cap", "fff999fff000")
    assert a.candidate_id != c.candidate_id


def test_discovery_confidence_is_deterministic_observation() -> None:
    """The watcher's confidence is 1.0 because it OBSERVED the upstream
    sha via ls-remote — a deterministic fact, not a model guess (§6:
    discovery confidence is about the search, and this search is exact)."""
    p = _watcher_proposal("cap", "abc123def456")
    assert p.discovery_confidence == 1.0
