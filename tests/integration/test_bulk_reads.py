"""Bulk repository reads (§15/§46): one routing request must cost a handful of
queries, never O(catalog) single-row roundtrips. These tests pin the bulk
methods' semantics — they must agree exactly with their single-row counterparts,
including the assessments' latest-wins rule."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from aci.domain.capability.models import Capability, CapabilityVersion, SkillSpec
from aci.domain.provenance.models import (
    LicenseAssessment,
    LicensePermissions,
    SecurityAssessment,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def make_version(cid: str) -> CapabilityVersion:
    return CapabilityVersion(
        capability_id=cid,
        version="1.0.0",
        kind="skill",
        content_digest=f"sha256:{uuid.uuid4().hex}{uuid.uuid4().hex}",
        created_at=NOW,
        spec=SkillSpec(),
    )


def test_bulk_version_and_capability_reads_match_single_row(
    capability_repo,
) -> None:
    ids = [uid("cap") for _ in range(3)]
    versions = {}
    for cid in ids:
        capability_repo.create_capability(Capability(id=cid, kind="skill", created_at=NOW))
        versions[cid] = capability_repo.create_version(make_version(cid))

    pairs = [(cid, "1.0.0") for cid in ids] + [(uid("cap"), "9.9.9")]  # one ghost pair
    bulk = {(v.capability_id, v.version): v for v in capability_repo.get_versions(pairs)}
    assert set(bulk) == {(cid, "1.0.0") for cid in ids}  # ghosts simply absent
    for cid in ids:
        assert bulk[(cid, "1.0.0")] == versions[cid]
        assert bulk[(cid, "1.0.0")] == capability_repo.get_version(cid, "1.0.0")

    caps = {c.id: c for c in capability_repo.get_capabilities(ids + [uid("cap")])}
    assert set(caps) == set(ids)
    for cid in ids:
        assert caps[cid] == capability_repo.get_capability(cid)

    assert capability_repo.get_versions([]) == []
    assert capability_repo.get_capabilities([]) == []


def test_bulk_assessments_use_latest_wins(
    capability_repo,
    license_repo,
    security_repo,
) -> None:
    cid = uid("cap")
    capability_repo.create_capability(Capability(id=cid, kind="skill", created_at=NOW))
    capability_repo.create_version(make_version(cid))

    # Two license assessments: the later one must win, same as get_assessment.
    license_repo.put_assessment(
        LicenseAssessment(
            assessment_id=uid("lic"),
            capability_id=cid,
            version="1.0.0",
            license_identifier="UNKNOWN",
            permissions=LicensePermissions(can_redistribute=False),
            assessed_at=NOW,
            assessed_by="legal-1",
        )
    )
    later = LicenseAssessment(
        assessment_id=uid("lic"),
        capability_id=cid,
        version="1.0.0",
        license_identifier="MIT",
        permissions=LicensePermissions(can_redistribute=True),
        assessed_at=NOW + timedelta(minutes=1),
        assessed_by="legal-2",
    )
    license_repo.put_assessment(later)

    security_repo.put_assessment(
        SecurityAssessment(
            assessment_id=uid("sec"),
            capability_id=cid,
            version="1.0.0",
            scan_status="unknown",
            scanned_at=NOW,
            scanner_version="s",
        )
    )
    later_scan = SecurityAssessment(
        assessment_id=uid("sec"),
        capability_id=cid,
        version="1.0.0",
        scan_status="passed",
        scanned_at=NOW + timedelta(minutes=1),
        scanner_version="s",
    )
    security_repo.put_assessment(later_scan)

    pairs = [(cid, "1.0.0"), (uid("cap"), "1.0.0")]
    bulk_licenses = {(a.capability_id, a.version): a for a in license_repo.get_assessments(pairs)}
    bulk_scans = {(a.capability_id, a.version): a for a in security_repo.get_assessments(pairs)}

    assert set(bulk_licenses) == {(cid, "1.0.0")}  # ghosts simply absent
    assert bulk_licenses[(cid, "1.0.0")] == license_repo.get_assessment(cid, "1.0.0") == later
    assert bulk_scans[(cid, "1.0.0")] == security_repo.get_assessment(cid, "1.0.0") == later_scan

    assert license_repo.get_assessments([]) == []
    assert security_repo.get_assessments([]) == []
