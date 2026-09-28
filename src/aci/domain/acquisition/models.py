"""Acquisition candidate contracts (auto.md §6-7; plan §22).

A Candidate is a PROPOSED skill package from a source — the thing being
CONSIDERED, never a capability. The authoritative Capability Registry only
ever sees candidates that passed the whole pipeline; the Candidate
Registry tracks the consideration lifecycle separately (§7: "Candidate
Registry = things being considered; Capability Registry = accepted
canonical capabilities" — two different things, no parallel authority).

Scout/agent output is a structured proposal (§6): confidence is
DISCOVERY confidence only — never production quality. Every transition
is explicit and auditable; failure states record WHY.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CandidateStatus = Literal[
    # happy path (§7 candidate lifecycle)
    "proposed",
    "approved_for_fetch",
    "fetched",
    "quarantined",
    "ingestion_passed",
    "canonicalized",
    "staging",
    "promotion_proposed",
    "promoted",
    # failure states (§7) — record WHY, never silently drop
    "rejected_source",
    "rejected_license",
    "rejected_security",
    "rejected_schema",
    "rejected_quality",
    "superseded",
]

#: Legal transitions (auto.md §7). A candidate never skips quarantine;
#: promotion only from promotion_proposed (human/policy gate, §24).
_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"approved_for_fetch", "rejected_source", "superseded"}),
    "approved_for_fetch": frozenset({"fetched", "rejected_source", "superseded"}),
    "fetched": frozenset({"quarantined", "rejected_schema", "superseded"}),
    "quarantined": frozenset(
        {
            "ingestion_passed",
            "rejected_license",
            "rejected_security",
            "rejected_schema",
            "superseded",
        }
    ),
    "ingestion_passed": frozenset({"canonicalized", "rejected_quality", "superseded"}),
    "canonicalized": frozenset({"staging", "rejected_quality", "superseded"}),
    "staging": frozenset({"promotion_proposed", "rejected_quality", "superseded"}),
    "promotion_proposed": frozenset({"promoted", "rejected_quality", "superseded"}),
    # terminal states are immutable
    "promoted": frozenset(),
    "rejected_source": frozenset(),
    "rejected_license": frozenset(),
    "rejected_security": frozenset(),
    "rejected_schema": frozenset(),
    "rejected_quality": frozenset(),
    "superseded": frozenset(),
}


class CandidateProposal(BaseModel):
    """Scout output (§6): structured, never "this skill is good".

    The `reason` is evidence-bearing prose; `signals` are booleans the
    scout observed; `discovery_confidence` is about the SEARCH, not the
    skill's quality — 0.82 does NOT mean "82% production quality".
    """

    model_config = {"frozen": True}

    candidate_id: str = Field(min_length=1)
    gap_id: str | None = None
    source_repo: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    observed_revision: str = Field(min_length=1)
    candidate_type: Literal["skill"] = "skill"
    reason: str = Field(min_length=1)
    signals: dict[str, bool] = Field(default_factory=dict)
    discovery_confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: Literal["fetch_to_quarantine", "reject", "hold"] = "fetch_to_quarantine"
    proposed_at: datetime
    proposed_by: str = "scout"


class CandidateRecord(BaseModel):
    """One candidate moving through the §7 lifecycle.

    Transitions go through :func:`advance_candidate` — illegal edges raise
    ``CANDIDATE_TRANSITION_INVALID`` and the record is frozen (same
    discipline as AgentTask.advance_task, §30.1).
    """

    model_config = {"frozen": True}

    candidate_id: str | None = Field(default=None, min_length=1)
    proposal: CandidateProposal
    status: CandidateStatus = "proposed"
    #: capability_id+version once the pipeline created one (staging+).
    capability_id: str | None = None
    version: str | None = None
    #: who/what moved it to the current status (audit §40).
    decided_by: str = "scout"
    decided_at: datetime | None = None
    rejection_reason: str | None = None
    history: list[tuple[str, str]] = Field(default_factory=list)


def advance_candidate(
    record: CandidateRecord,
    to_status: CandidateStatus,
    *,
    decided_by: str,
    decided_at: datetime,
    rejection_reason: str | None = None,
) -> CandidateRecord:
    """Validate one lifecycle edge and return the advanced record.

    Never mutates in place (frozen models); illegal edges raise
    ``ValueError`` naming both statuses so callers cannot silently skip
    quarantine or promote without the proposal step.
    """
    legal = _TRANSITIONS.get(record.status, frozenset())
    if to_status not in legal:
        raise ValueError(
            f"candidate {record.candidate_id}: illegal transition "
            f"{record.status!r} -> {to_status!r} "
            f"(legal: {sorted(legal) or 'none — terminal state'})"
        )
    return record.model_copy(
        update={
            "status": to_status,
            "decided_by": decided_by,
            "decided_at": decided_at,
            "rejection_reason": rejection_reason,
            "history": [
                *record.history,
                (to_status, decided_by),
            ],
        }
    )
