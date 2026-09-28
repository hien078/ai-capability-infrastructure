"""Artifact finding + capability boundary contracts (auto2.md §10-11).

The crawler must not search only for SKILL.md (§10): reusable intelligence
appears as prompts, rules, playbooks, workflows, checklists... The
Artifact Finder groups those files; the Boundary Detector then infers
capability boundaries — "1 file ≠ 1 capability" (§11):

    debugger.md + root-cause.md + reproduce.md + verification.md
        = ONE conceptual capability (systematic-debugging)

    one large file
        = possibly SEVERAL reusable procedures

Boundary inference is SEMANTIC work (§1.3) — an LLM implements the
``BoundaryDetector`` protocol in production; the deterministic heuristic
default keeps the pipeline honest offline (same pattern as
``BundleEvaluator`` / ``RubricContainmentEvaluator``). Either way the
output is a PROPOSAL (a RawCandidate), never a capability: the candidate
still walks the whole §12.2 lifecycle.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ArtifactFileRef(BaseModel):
    """One file an artifact group points at (path + digest)."""

    model_config = {"frozen": True}

    path: str = Field(min_length=1)
    sha256: str = Field(min_length=1)


class ArtifactGroup(BaseModel):
    """Artifact Finder output (§10): files that MIGHT be reusable
    intelligence, grouped by directory proximity, with a suspected role.

    A group is raw material — nothing here is trusted, nothing here is a
    capability. `suspected_role` is a routing hint for the boundary
    detector, never a final classification.
    """

    model_config = {"frozen": True}

    group_id: str = Field(min_length=1)
    files: list[ArtifactFileRef] = Field(min_length=1)
    suspected_role: str = ""
    source_repo: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    observed_revision: str = Field(min_length=1)


class RawCandidate(BaseModel):
    """Boundary Detector output (§11): a PROPOSED capability boundary.

    `primary_files` are the entrypoint-bearing files; `supporting_files`
    are references the capability needs. `inferred_provides` names what
    the candidate offers — routing vocabulary, not a guarantee. The
    candidate_id links into the §12.2 lifecycle; nothing here is trusted
    until the pipeline says so.
    """

    model_config = {"frozen": True}

    candidate_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    proposed_name: str = Field(min_length=1)
    primary_files: list[ArtifactFileRef] = Field(min_length=1)
    supporting_files: list[ArtifactFileRef] = Field(default_factory=list)
    inferred_kind: Literal["skill"] = "skill"
    inferred_provides: list[str] = Field(default_factory=list)
    #: Which detector produced this boundary — audit (§80).
    detected_by: str = "heuristic-boundary-detector"
    detected_at: datetime
    #: Confidence about the BOUNDARY GUESS — never quality (§6 rule).
    boundary_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
