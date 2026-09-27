"""Phase 4 acceptance (plan §52): unknown license blocks production,
unreviewed raw skill cannot be production, every production version has a
provenance chain. Plus §37.1 rollback/revoke as pointer flips.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aci.adapters.outbound.postgres.repositories import (
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.source_records import (
    SqlAlchemySourceRecordRepository,
)
from aci.control_plane.promotion.service import PromotionService
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.provenance.models import (
    LicenseAssessment,
    LicensePermissions,
    SecurityAssessment,
)
from aci.providers.skills.ingestion import SkillIngestionService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def ingest_skill(ingestion: SkillIngestionService, tmp_path: Path, name: str) -> str:
    src = tmp_path / "skill-src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d.\nversion: 1.0.0\n---\n\nbody\n",
        encoding="utf-8",
    )
    ingestion.ingest_local(src, now=NOW)
    return name


def mit_assessment(cap: str) -> LicenseAssessment:
    return LicenseAssessment(
        assessment_id=f"lic-{uuid.uuid4().hex[:8]}",
        capability_id=cap,
        version="1.0.0",
        license_identifier="MIT",
        permissions=LicensePermissions(
            can_ingest=True,
            can_modify=True,
            can_store=True,
            can_redistribute=True,
            commercial_use_allowed=True,
            attribution_required=True,
        ),
        assessed_at=NOW,
        assessed_by="legal-1",
    )


def unknown_assessment(cap: str) -> LicenseAssessment:
    return LicenseAssessment(
        assessment_id=f"lic-{uuid.uuid4().hex[:8]}",
        capability_id=cap,
        version="1.0.0",
        license_identifier="UNKNOWN",
        permissions=LicensePermissions(unknown_requires_review=True),
        assessed_at=NOW,
        assessed_by="legal-1",
    )


def passed_scan(cap: str) -> SecurityAssessment:
    return SecurityAssessment(
        assessment_id=f"sec-{uuid.uuid4().hex[:8]}",
        capability_id=cap,
        version="1.0.0",
        scan_status="passed",
        scanned_at=NOW,
        scanner_version="scanner-0.1",
        reviewed_by="sec-1",
    )


def test_pre_production_channel_needs_no_gates(
    promotion: PromotionService,
    ingestion: SkillIngestionService,
    release_repo: SqlAlchemyReleaseRepository,
    tmp_path: Path,
) -> None:
    cap = ingest_skill(ingestion, tmp_path, uid("gate"))
    release = promotion.promote(cap, "1.0.0", "staging", approved_by="rev-1", now=NOW)
    assert release.channel == "staging"
    assert release_repo.get_release(cap, "staging") is not None


def test_production_blocked_without_any_assessment(
    promotion: PromotionService,
    ingestion: SkillIngestionService,
    tmp_path: Path,
) -> None:
    cap = ingest_skill(ingestion, tmp_path, uid("gate"))
    with pytest.raises(DomainError) as exc:
        promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)
    assert exc.value.code == ErrorCode.POLICY_DENIED
    assert "license-redistributable" in str(exc.value)
    assert "security-passed" in str(exc.value)


def test_unknown_license_blocks_production(
    promotion: PromotionService,
    ingestion: SkillIngestionService,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap = ingest_skill(ingestion, tmp_path, uid("gate"))
    license_repo.put_assessment(unknown_assessment(cap))
    security_repo.put_assessment(passed_scan(cap))

    with pytest.raises(DomainError) as exc:
        promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)
    assert exc.value.code == ErrorCode.POLICY_DENIED
    assert "license-redistributable" in str(exc.value)


def test_failed_security_scan_blocks_production(
    promotion: PromotionService,
    ingestion: SkillIngestionService,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap = ingest_skill(ingestion, tmp_path, uid("gate"))
    license_repo.put_assessment(mit_assessment(cap))
    security_repo.put_assessment(
        SecurityAssessment(
            assessment_id=f"sec-{uuid.uuid4().hex[:8]}",
            capability_id=cap,
            version="1.0.0",
            scan_status="failed",
            findings=["exfil pattern in SKILL.md"],
            scanned_at=NOW,
            scanner_version="scanner-0.1",
        )
    )
    with pytest.raises(DomainError) as exc:
        promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)
    assert "security-passed" in str(exc.value)


def test_full_gates_allow_production_with_provenance(
    promotion: PromotionService,
    ingestion: SkillIngestionService,
    release_repo: SqlAlchemyReleaseRepository,
    source_records: SqlAlchemySourceRecordRepository,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap = ingest_skill(ingestion, tmp_path, uid("gate"))
    license_repo.put_assessment(mit_assessment(cap))
    security_repo.put_assessment(passed_scan(cap))

    release = promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)

    assert release.channel == "production"
    assert release.approved_by == "rev-1"
    stored = release_repo.get_release(cap, "production")
    assert stored is not None and stored.version == "1.0.0"
    # Every production version has a provenance chain (§52 acceptance).
    assert len(source_records.list_source_records(cap)) >= 1


def test_production_requires_provenance_chain(
    promotion: PromotionService,
    capability_repo: SqlAlchemyCapabilityRepository,
    license_repo,
    security_repo,
    sessions,
) -> None:
    # A version created outside ingestion has no source record → provenance gate fails.
    from aci.domain.capability.models import Capability, CapabilityVersion, SkillSpec

    cap = uid("noprov")
    capability_repo.create_capability(Capability(id=cap, kind="skill", created_at=NOW))
    capability_repo.create_version(
        CapabilityVersion(
            capability_id=cap,
            version="1.0.0",
            kind="skill",
            content_digest="sha256:" + "ab" * 32,
            created_at=NOW,
            spec=SkillSpec(),
        )
    )
    license_repo.put_assessment(mit_assessment(cap))
    security_repo.put_assessment(passed_scan(cap))

    with pytest.raises(DomainError) as exc:
        promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)
    assert "provenance-chain" in str(exc.value)


def test_rollback_moves_pointer_back(
    promotion: PromotionService,
    ingestion: SkillIngestionService,
    release_repo: SqlAlchemyReleaseRepository,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap = ingest_skill(ingestion, tmp_path, uid("gate"))
    src = tmp_path / "skill-src"
    (src / "SKILL.md").write_text(
        f"---\nname: {cap}\ndescription: d.\nversion: 2.0.0\n---\n\nbody2\n", encoding="utf-8"
    )
    ingestion.ingest_local(src, now=NOW)

    for ver in ("1.0.0", "2.0.0"):
        license_repo.put_assessment(
            LicenseAssessment(
                assessment_id=f"lic-{uuid.uuid4().hex[:8]}",
                capability_id=cap,
                version=ver,
                license_identifier="MIT",
                permissions=LicensePermissions(can_redistribute=True),
                assessed_at=NOW,
                assessed_by="legal-1",
            )
        )
        security_repo.put_assessment(
            SecurityAssessment(
                assessment_id=f"sec-{uuid.uuid4().hex[:8]}",
                capability_id=cap,
                version=ver,
                scan_status="passed",
                scanned_at=NOW,
                scanner_version="s",
            )
        )

    promotion.promote(cap, "2.0.0", "production", approved_by="rev-1", now=NOW)
    promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)  # rollback
    prod = release_repo.get_release(cap, "production")
    assert prod is not None and prod.version == "1.0.0"


def test_revoke_flips_status_keeps_version(
    promotion: PromotionService,
    ingestion: SkillIngestionService,
    release_repo: SqlAlchemyReleaseRepository,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap = ingest_skill(ingestion, tmp_path, uid("gate"))
    license_repo.put_assessment(mit_assessment(cap))
    security_repo.put_assessment(passed_scan(cap))
    promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)

    revoked = promotion.revoke(cap, "production", approved_by="rev-1")

    assert revoked.status == "revoked"
    stored = release_repo.get_release(cap, "production")
    assert stored is not None and stored.status == "revoked"
    assert stored.version == "1.0.0"  # version untouched


def test_prerequisites_report_without_mutation(
    promotion: PromotionService,
    ingestion: SkillIngestionService,
    release_repo: SqlAlchemyReleaseRepository,
    tmp_path: Path,
) -> None:
    cap = ingest_skill(ingestion, tmp_path, uid("gate"))
    checks = promotion.prerequisites(cap, "1.0.0", "production")
    names = [c.name for c in checks]
    assert names == [
        "version-exists",
        "provenance-chain",
        "license-redistributable",
        "security-passed",
    ]
    assert release_repo.get_release(cap, "production") is None  # nothing written
