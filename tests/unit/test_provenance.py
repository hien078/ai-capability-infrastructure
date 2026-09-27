"""Unit tests for provenance/licensing gate models (plan §24)."""

import pytest
from pydantic import ValidationError

from aci.domain.provenance.models import (
    LicenseAssessment,
    LicensePermissions,
    PromotionCheck,
    SecurityAssessment,
)


def test_default_permissions_block_redistribution() -> None:
    # Unassessed content defaults to non-redistributable (§24).
    p = LicensePermissions()
    assert p.can_ingest is True
    assert p.can_redistribute is False
    assert p.blocks_production_redistribution() is True


def test_permissive_permissions_allow_redistribution() -> None:
    p = LicensePermissions(
        can_redistribute=True,
        commercial_use_allowed=True,
        attribution_required=True,
    )
    assert p.blocks_production_redistribution() is False


def test_unknown_license_blocks_even_if_redistributable_flag_set() -> None:
    p = LicensePermissions(can_redistribute=True, unknown_requires_review=True)
    assert p.blocks_production_redistribution() is True


def test_models_are_frozen() -> None:
    p = LicensePermissions()
    with pytest.raises(ValidationError):
        p.can_redistribute = True  # type: ignore[misc]
    c = PromotionCheck(name="x", passed=True)
    with pytest.raises(ValidationError):
        c.passed = False  # type: ignore[misc]


def test_scan_status_unknown_is_valid_and_distinct() -> None:
    s = SecurityAssessment.model_validate(
        {
            "assessment_id": "s1",
            "capability_id": "c",
            "version": "1.0.0",
            "scan_status": "unknown",
            "scanned_at": "2026-09-27T00:00:00Z",
            "scanner_version": "s",
        }
    )
    assert s.scan_status == "unknown"  # unknown stays unknown (§60.9)
    assert s.findings == []
    with pytest.raises(ValidationError):
        SecurityAssessment.model_validate(
            {
                "assessment_id": "s1",
                "capability_id": "c",
                "version": "1.0.0",
                "scan_status": "maybe",
                "scanned_at": "2026-09-27T00:00:00Z",
                "scanner_version": "s",
            }
        )


def test_assessment_roundtrip() -> None:
    a = LicenseAssessment.model_validate(
        {
            "assessment_id": "l1",
            "capability_id": "c",
            "version": "1.0.0",
            "license_identifier": "MIT",
            "permissions": {"can_redistribute": True},
            "assessed_at": "2026-09-27T00:00:00Z",
            "assessed_by": "legal-1",
        }
    )
    assert a.permissions.attribution_required is False
    dumped = a.model_dump()
    restored = LicenseAssessment.model_validate(dumped)
    assert restored == a
