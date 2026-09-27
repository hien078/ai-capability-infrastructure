"""Provenance & licensing gate contracts (plan §§23-24, 52 Phase 4).

Assessments attach to exact immutable versions. License policy is machine-readable
(§24): unknown/ambiguous license blocks production redistribution by default.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ScanStatus = Literal["passed", "failed", "unknown"]


class LicensePermissions(BaseModel):
    """Machine-readable legal-operational permissions (plan §24)."""

    model_config = {"frozen": True}

    can_ingest: bool = True
    can_modify: bool = False
    can_store: bool = True
    can_redistribute: bool = False
    commercial_use_allowed: bool = False
    attribution_required: bool = False
    share_alike_required: bool = False
    internal_only: bool = False
    unknown_requires_review: bool = False

    def blocks_production_redistribution(self) -> bool:
        """Unknown or non-redistributable content may not enter production."""
        return self.unknown_requires_review or not self.can_redistribute


class LicenseAssessment(BaseModel):
    model_config = {"frozen": True}

    assessment_id: str
    capability_id: str
    version: str
    license_identifier: str
    permissions: LicensePermissions
    assessed_at: datetime
    assessed_by: str
    notes: str = ""


class SecurityAssessment(BaseModel):
    model_config = {"frozen": True}

    assessment_id: str
    capability_id: str
    version: str
    scan_status: ScanStatus
    findings: list[str] = Field(default_factory=list)
    scanned_at: datetime
    scanner_version: str
    reviewed_by: str | None = None


class PromotionCheck(BaseModel):
    """One gate result for a promotion request (plan §37)."""

    model_config = {"frozen": True}

    name: str
    passed: bool
    detail: str = ""
