"""Promotion gates (plan §§37, 52 Phase 4; ADR-012).

Control plane: changes what may exist in production. Never a synchronous hop in
route requests (ADR-003). Promotion to `production` requires the full gate set:
provenance chain, redistributable license, passed security scan. Promotion to
`staging` only requires the version to exist (ingestion gates are evaluated by
the ingestion pipeline, §22 — a quarantined/rejected source record blocks
staging). Rollback = promoting an older version; revoke = status flip. Both only
move release pointers — immutable versions are never touched (ADR-002).
"""

from datetime import UTC, datetime

from aci.application.protocols import (
    CapabilityRepository,
    LicenseAssessmentRepository,
    ReleaseRepository,
    SecurityAssessmentRepository,
    SourceRecordRepository,
)
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import CapabilityRelease, ReleaseChannel
from aci.domain.provenance.models import PromotionCheck

_PRE_PRODUCTION: frozenset[str] = frozenset({"staging"})


class PromotionService:
    def __init__(
        self,
        capabilities: CapabilityRepository,
        releases: ReleaseRepository,
        source_records: SourceRecordRepository,
        licenses: LicenseAssessmentRepository,
        securities: SecurityAssessmentRepository,
    ) -> None:
        self._capabilities = capabilities
        self._releases = releases
        self._source_records = source_records
        self._licenses = licenses
        self._securities = securities

    def prerequisites(
        self, capability_id: str, version: str, channel: ReleaseChannel
    ) -> list[PromotionCheck]:
        """Evaluate every gate for a promotion request without mutating anything."""
        checks: list[PromotionCheck] = []

        v = self._capabilities.get_version(capability_id, version)
        checks.append(
            PromotionCheck(
                name="version-exists",
                passed=v is not None,
                detail=f"{capability_id}@{version}" + ("" if v is not None else " not found"),
            )
        )
        if channel in _PRE_PRODUCTION:
            # Staging (§22): ingestion gates G1-G4 are evaluated by the
            # ingestion pipeline; promotion here only requires the version
            # to exist and its source record not to be rejected.
            provenance = [
                r
                for r in self._source_records.list_source_records(capability_id)
                if r.version == version
            ]
            checks.append(
                PromotionCheck(
                    name="ingestion-not-rejected",
                    passed=bool(provenance)
                    and not any(r.ingestion_status == "rejected" for r in provenance),
                    detail=(
                        f"{len(provenance)} source record(s)" if provenance else "no source record"
                    ),
                )
            )
            return checks

        # Production gates (§37, §24, §52 Phase 4 acceptance).
        provenance = [
            r
            for r in self._source_records.list_source_records(capability_id)
            if r.version == version
        ]
        checks.append(
            PromotionCheck(
                name="provenance-chain",
                passed=bool(provenance),
                detail=f"{len(provenance)} source record(s) for {capability_id}@{version}",
            )
        )

        license_ = self._licenses.get_assessment(capability_id, version)
        license_ok = (
            license_ is not None and not license_.permissions.blocks_production_redistribution()
        )
        checks.append(
            PromotionCheck(
                name="license-redistributable",
                passed=license_ok,
                detail=(f"{license_.license_identifier}" if license_ else "no license assessment")
                + (
                    " (unknown/non-redistributable)"
                    if license_ and license_.permissions.blocks_production_redistribution()
                    else ""
                ),
            )
        )

        security = self._securities.get_assessment(capability_id, version)
        checks.append(
            PromotionCheck(
                name="security-passed",
                passed=security is not None and security.scan_status == "passed",
                detail=(
                    f"scan_status={security.scan_status}" if security else "no security assessment"
                ),
            )
        )
        return checks

    def promote(
        self,
        capability_id: str,
        version: str,
        channel: ReleaseChannel,
        *,
        approved_by: str,
        now: datetime | None = None,
    ) -> CapabilityRelease:
        """Move a release pointer after gates pass. Rollback = promote an older version."""
        promoted_at = now or datetime.now(UTC)
        checks = self.prerequisites(capability_id, version, channel)
        failed = [c for c in checks if not c.passed]
        if failed:
            reasons = "; ".join(f"{c.name}: {c.detail}" for c in failed)
            raise DomainError(
                ErrorCode.POLICY_DENIED,
                f"promotion of {capability_id}@{version} to {channel} blocked — {reasons}",
            )
        return self._releases.set_release(
            CapabilityRelease(
                capability_id=capability_id,
                version=version,
                channel=channel,
                status="active",
                promoted_at=promoted_at,
                approved_by=approved_by,
            )
        )

    def revoke(
        self, capability_id: str, channel: ReleaseChannel, *, approved_by: str
    ) -> CapabilityRelease:
        """Flip a release pointer to revoked. Versions stay immutable (§37.1)."""
        current = self._releases.get_release(capability_id, channel)
        if current is None:
            raise DomainError(
                ErrorCode.CAPABILITY_NOT_FOUND,
                f"no release pointer for {capability_id} on {channel}",
            )
        return self._releases.set_release(
            CapabilityRelease(
                capability_id=capability_id,
                version=current.version,
                channel=channel,
                status="revoked",
                approved_by=approved_by,
            )
        )
