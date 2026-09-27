"""Assemble annotated eligibility candidates from the registry (plan §15).

Application service: reads release pointers + versions + assessments and
annotates each candidate with trust/license/scope data so the pure eligibility
engine can decide. Revocation is read live here — no stale cache (§46).
"""

from aci.application.protocols import (
    CapabilityRepository,
    LicenseAssessmentRepository,
    ReleaseRepository,
    SecurityAssessmentRepository,
)
from aci.domain.capability.models import ReleaseChannel, ReleaseStatus
from aci.domain.policy.models import EligibleCandidate, TrustTier


def _trust_tier(scan_status: str | None) -> TrustTier:
    if scan_status == "passed":
        return "verified"
    if scan_status == "unknown":
        return "standard"
    return "untrusted"


class ProductionCandidateLoader:
    def __init__(
        self,
        capabilities: CapabilityRepository,
        releases: ReleaseRepository,
        licenses: LicenseAssessmentRepository,
        securities: SecurityAssessmentRepository,
    ) -> None:
        self._capabilities = capabilities
        self._releases = releases
        self._licenses = licenses
        self._securities = securities

    def load(
        self,
        channel: ReleaseChannel = "production",
        *,
        status: ReleaseStatus | None = "active",
    ) -> list[EligibleCandidate]:
        """Active releases on a channel → annotated candidates. Revoked drop out here."""
        out: list[EligibleCandidate] = []
        for release in self._releases.list_channel(channel, status=status):
            version = self._capabilities.get_version(release.capability_id, release.version)
            if version is None:
                continue
            capability = self._capabilities.get_capability(release.capability_id)
            if capability is None:
                continue
            security = self._securities.get_assessment(release.capability_id, release.version)
            license_ = self._licenses.get_assessment(release.capability_id, release.version)
            out.append(
                EligibleCandidate(
                    capability_id=release.capability_id,
                    version=release.version,
                    digest=version.content_digest,
                    kind=version.kind,
                    facets=version.facets,
                    channel=release.channel,
                    status=release.status,
                    trust_tier=_trust_tier(security.scan_status if security else None),
                    license_blocked=(
                        license_.permissions.blocks_production_redistribution()
                        if license_
                        else False
                    ),
                    owner_scope=capability.owner_scope,
                    owner_scope_id=capability.owner_scope_id,
                    compatibility=version.compatibility,
                )
            )
        return out
