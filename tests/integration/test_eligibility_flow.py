"""Phase 5 acceptance (plan §52): ineligible capability never reaches the
reranker; a revoked release disappears immediately from route results.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aci.adapters.outbound.postgres.repositories import (
    SqlAlchemyReleaseRepository,
)
from aci.application.list_candidates import ProductionCandidateLoader
from aci.control_plane.promotion.service import PromotionService
from aci.domain.policy.models import (
    ClientDescriptor,
    PolicyRules,
    PolicySnapshot,
    RoutingRequestContext,
    ScopeContext,
)
from aci.domain.provenance.models import (
    LicenseAssessment,
    LicensePermissions,
    SecurityAssessment,
)
from aci.providers.skills.ingestion import SkillIngestionService
from aci.routing.eligibility import DefaultEligibilityPolicy

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def ingest(ingestion: SkillIngestionService, tmp_path: Path, name: str) -> str:
    src = tmp_path / f"src-{name}"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d.\nversion: 1.0.0\n---\n\nbody\n", encoding="utf-8"
    )
    ingestion.ingest_local(src, now=NOW)
    return name


def open_gates(license_repo, security_repo, cap: str) -> None:
    license_repo.put_assessment(
        LicenseAssessment(
            assessment_id=f"lic-{uuid.uuid4().hex[:8]}",
            capability_id=cap,
            version="1.0.0",
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
            version="1.0.0",
            scan_status="passed",
            scanned_at=NOW,
            scanner_version="s",
        )
    )


def ctx() -> RoutingRequestContext:
    return RoutingRequestContext(
        client=ClientDescriptor(type="opencode", supported_features=["skills"]),
        scope=ScopeContext(principal_id="p-1", organization_id="org-1", workspace_id="ws-1"),
        request_id="req-1",
    )


def test_only_production_active_releases_become_candidates(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    candidate_loader: ProductionCandidateLoader,
    eligibility_policy: DefaultEligibilityPolicy,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    promoted_cap = ingest(ingestion, tmp_path, uid("elig"))
    quarantined_cap = ingest(ingestion, tmp_path, uid("elig"))  # stays raw
    open_gates(license_repo, security_repo, promoted_cap)
    promotion.promote(promoted_cap, "1.0.0", "production", approved_by="rev-1", now=NOW)

    candidates = candidate_loader.load()
    decision = eligibility_policy.filter(candidates, ctx(), PolicyRules(), allowed_kinds=["skill"])

    ids = [c.capability_id for c in decision.kept]
    assert promoted_cap in ids
    assert quarantined_cap not in ids  # raw/quarantined never reaches the reranker
    assert all(c.channel == "production" and c.status == "active" for c in decision.kept)
    # The quarantined one is not even a candidate (loader reads production only).
    assert quarantined_cap not in [c.capability_id for c in decision.excluded]


def test_revoked_release_disappears_immediately(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    candidate_loader: ProductionCandidateLoader,
    eligibility_policy: DefaultEligibilityPolicy,
    release_repo: SqlAlchemyReleaseRepository,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap = ingest(ingestion, tmp_path, uid("elig"))
    open_gates(license_repo, security_repo, cap)
    promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)

    before = candidate_loader.load()
    assert cap in [c.capability_id for c in before]

    promotion.revoke(cap, "production", approved_by="rev-1")

    after = candidate_loader.load()
    assert cap not in [c.capability_id for c in after]  # gone from route results now
    stored = release_repo.get_release(cap, "production")
    assert stored is not None and stored.status == "revoked"


def test_disabled_release_excluded_with_reason(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    candidate_loader: ProductionCandidateLoader,
    eligibility_policy: DefaultEligibilityPolicy,
    release_repo: SqlAlchemyReleaseRepository,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap = ingest(ingestion, tmp_path, uid("elig"))
    open_gates(license_repo, security_repo, cap)
    promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)
    # Flip the pointer to disabled directly (control-plane style).
    from aci.domain.capability.models import CapabilityRelease

    release_repo.set_release(
        CapabilityRelease(
            capability_id=cap, version="1.0.0", channel="production", status="disabled"
        )
    )

    # The loader reads active releases only, so a disabled pointer drops out of
    # route inputs entirely; the engine's status rule is defense-in-depth.
    assert cap not in [c.capability_id for c in candidate_loader.load()]
    loaded_all = candidate_loader.load(status=None)
    decision = eligibility_policy.filter(loaded_all, ctx(), PolicyRules(), allowed_kinds=["skill"])
    assert cap not in [c.capability_id for c in decision.kept]
    mine = [e for e in decision.excluded if e.capability_id == cap]
    assert mine and mine[0].reason == "CAPABILITY_NOT_ELIGIBLE"


def test_candidate_annotations_come_from_assessments(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    candidate_loader: ProductionCandidateLoader,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap = ingest(ingestion, tmp_path, uid("elig"))
    open_gates(license_repo, security_repo, cap)
    promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)

    (candidate,) = [c for c in candidate_loader.load() if c.capability_id == cap]
    assert candidate.trust_tier == "verified"  # security scan passed
    assert candidate.license_blocked is False  # MIT redistributable
    assert candidate.owner_scope == "global"
    assert candidate.digest.startswith("sha256:")


def test_policy_snapshot_roundtrip_and_latest(
    policy_snapshots,
) -> None:
    rules = PolicyRules(min_trust_tier="verified", denied_capability_ids=frozenset({"bad-1"}))
    # now() so this snapshot is genuinely the latest even on a shared live DB.
    snap = PolicySnapshot(snapshot_id=uid("pol"), created_at=datetime.now(UTC), rules=rules)
    policy_snapshots.put_snapshot(snap)

    got = policy_snapshots.get_snapshot(snap.snapshot_id)
    assert got is not None and got.rules == rules
    latest = policy_snapshots.latest_snapshot()
    assert latest is not None and latest.snapshot_id == snap.snapshot_id
    assert policy_snapshots.get_snapshot("pol-missing") is None
