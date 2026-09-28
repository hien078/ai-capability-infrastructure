"""Promotion proposal (auto.md §24): evidence bundle for a human decision.

"Không promote ngay." Everything the pipeline learned about a candidate —
ingestion gates, compatibility report, benchmark delta, regression count —
is aggregated into ONE proposal a human reads and answers:

    approve | reject | request changes

The proposal is DATA, never an action: creating it changes nothing; the
human's approve calls the deterministic PromotionService (§25) which
moves the release pointer. This keeps ADR-012 intact — telemetry and
evaluation may generate proposals, never silently mutate production.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from aci.domain.provenance.models import PromotionCheck


class ProposalSection(BaseModel):
    """One evidence section of the proposal (auto.md §24 shape)."""

    model_config = {"frozen": True}

    name: str
    passed: bool | None = None  # None = informational, no pass/fail
    detail: str = ""
    checks: list[PromotionCheck] = Field(default_factory=list)


class PromotionProposal(BaseModel):
    """The full evidence bundle for one staging → production decision."""

    model_config = {"frozen": True}

    proposal_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    created_at: datetime
    created_by: str = "evaluation-worker"

    ingestion: ProposalSection
    compatibility: ProposalSection
    benchmark: ProposalSection
    regression: ProposalSection

    recommendation: Literal["promote", "hold", "reject"] = "hold"
    #: Evidence pointers (auto.md §40 audit: who/what/when/why/evidence).
    evidence: dict[str, str] = Field(default_factory=dict)

    @property
    def decision_ready(self) -> bool:
        """A proposal is decision-ready when every gate section that can
        pass/fail has passed. `unknown` sections (benchmark not run yet)
        keep it honest: the human sees them and decides with eyes open."""
        for section in (self.ingestion, self.compatibility, self.benchmark, self.regression):
            if section.passed is False:
                return False
        return True

    def summary_line(self) -> str:
        parts = [f"{self.capability_id}@{self.version}"]
        for name, section in (
            ("ing", self.ingestion),
            ("compat", self.compatibility),
            ("bench", self.benchmark),
            ("reg", self.regression),
        ):
            mark = {True: "✓", False: "✗", None: "?"}[section.passed]
            parts.append(f"{name}:{mark}")
        parts.append(f"rec={self.recommendation}")
        return " ".join(parts)
