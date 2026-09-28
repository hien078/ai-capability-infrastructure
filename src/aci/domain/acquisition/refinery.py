"""Refinery contracts (auto2.md §24-31): the "tinh luyện" stages.

The refinery converts raw candidates into canonical candidates:

    classification → clustering → dedupe → comparative analysis →
    canonical synthesis → quality filter → canonicalization

Everything here is PROPOSAL-GRADE: clusters create comparison groups
(§26: never auto-merge because embeddings are close), comparisons cite
their evidence, synthesis retains lineage (§30: derived_from), quality
scores are advisory (§31: never equate score>threshold with approval).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DuplicateKind = Literal["exact", "near", "semantic"]


class DuplicateMatch(BaseModel):
    """§27 one duplicate relation between two candidates."""

    model_config = {"frozen": True}

    left: str = Field(min_length=1)
    right: str = Field(min_length=1)
    kind: DuplicateKind
    #: 0..1 — exact is always 1.0; near/semantic carry their similarity.
    similarity: float = Field(ge=0.0, le=1.0)
    #: Deterministic evidence for the match (audit §80).
    detail: str = ""


class CandidateCluster(BaseModel):
    """§26 one comparison group — candidates that likely solve similar
    problems. A cluster NEVER merges its members automatically; it is
    the input unit for comparative analysis (§29)."""

    model_config = {"frozen": True}

    cluster_id: str = Field(min_length=1)
    #: The conceptual capability the members seem to share.
    family: str = Field(min_length=1)
    members: list[str] = Field(min_length=1)
    formed_by: str = "heuristic-clusterer"
    formed_at: datetime


class ComparisonCell(BaseModel):
    """§29 one cell of the comparison matrix — with the evidence that
    supports the observation (the analyzer must cite, never assert)."""

    model_config = {"frozen": True}

    candidate_id: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    value: str = Field(min_length=1)
    #: Which source element supports this observation (§29 requirement).
    evidence: str = ""


class ComparisonReport(BaseModel):
    """§29 the comparison matrix for one cluster."""

    model_config = {"frozen": True}

    cluster_id: str = Field(min_length=1)
    dimensions: list[str] = Field(min_length=1)
    cells: list[ComparisonCell] = Field(default_factory=list)
    produced_by: str = "heuristic-comparator"
    produced_at: datetime


class CanonicalSynthesis(BaseModel):
    """§30 the synthesized canonical candidate — vendor-neutral,
    lineage-preserving. This is STILL a proposal: it walks the §12.2
    lifecycle (refinery_ready → ingestion_passed) like any candidate."""

    model_config = {"frozen": True}

    synthesis_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    proposed_name: str = Field(min_length=1)
    provides: list[str] = Field(default_factory=list)
    procedure: list[str] = Field(default_factory=list)
    #: §30: retain ALL source lineage — never erase where it came from.
    derived_from: list[str] = Field(min_length=1)
    synthesized_by: str = "heuristic-synthesizer"
    synthesized_at: datetime


class QualityReport(BaseModel):
    """§31 advisory quality signals — never an approval."""

    model_config = {"frozen": True}

    candidate_id: str = Field(min_length=1)
    signals: dict[str, float] = Field(default_factory=dict)
    #: §31: a score is ADVISORY. There is deliberately no pass/fail here.
    produced_by: str = "heuristic-quality-filter"
    produced_at: datetime
