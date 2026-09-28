"""Ingestion gates (plan §22 lifecycle, §12 gate split; ADR-012 spirit).

Separate state machine from promotion gates: these decide whether UNTRUSTED
imported material may become an accepted canonical capability and reach
STAGING — never production directly. The gates map to the lifecycle:

    G1 structure/schema validity   (parse + package shape — enforced by the
                                    ingestion service before persistence)
    G2 provenance integrity        (source record exists for the version)
    G3 license policy              (detected license is redistributable)
    G4 security policy             (pattern scan verdict is `passed`)

Passing G1-G4 → the source record flips `quarantined → accepted` and the
caller may request a STAGING release. Failing G3/G4 → the record stays
`quarantined` (or flips to `rejected` when the caller asks) — the skill is
never advertised on any release channel.

License/security here are POLICY decisions over recorded assessments
(detection facts stay immutable in their own tables, §8 detection != policy).
"""

from aci.application.protocols import (
    LicenseAssessmentRepository,
    SecurityAssessmentRepository,
    SourceRecordRepository,
)
from aci.domain.provenance.models import PromotionCheck


class IngestionGateService:
    def __init__(
        self,
        source_records: SourceRecordRepository,
        licenses: LicenseAssessmentRepository,
        securities: SecurityAssessmentRepository,
    ) -> None:
        self._source_records = source_records
        self._licenses = licenses
        self._securities = securities

    def evaluate(self, capability_id: str, version: str) -> tuple[list[PromotionCheck], bool]:
        """G2-G4 for one ingested version. G1 (structure) already passed —
        the ingestion service refuses to persist an invalid package."""
        checks: list[PromotionCheck] = []

        records = [
            r
            for r in self._source_records.list_source_records(capability_id)
            if r.version == version
        ]
        checks.append(
            PromotionCheck(
                name="provenance-integrity",
                passed=bool(records),
                detail=(
                    f"{len(records)} source record(s) for {capability_id}@{version}"
                    if records
                    else "no source record — ingest first"
                ),
            )
        )

        license_ = self._licenses.get_assessment(capability_id, version)
        license_ok = (
            license_ is not None and not license_.permissions.blocks_production_redistribution()
        )
        checks.append(
            PromotionCheck(
                name="license-policy",
                passed=license_ok,
                detail=(license_.license_identifier if license_ else "no license assessment"),
            )
        )

        security = self._securities.get_assessment(capability_id, version)
        checks.append(
            PromotionCheck(
                name="security-policy",
                passed=security is not None and security.scan_status == "passed",
                detail=(
                    f"scan_status={security.scan_status}" if security else "no security assessment"
                ),
            )
        )
        return checks, all(c.passed for c in checks)

    def accept(self, capability_id: str, version: str) -> bool:
        """Flip the source record `quarantined → accepted` when G2-G4 pass.

        Returns True when the version is accepted (eligible for staging).
        A failed gate leaves the record quarantined — never rejected
        automatically: rejection is a human decision (§22).
        """
        checks, passed = self.evaluate(capability_id, version)
        if not passed:
            return False
        return self._source_records.set_ingestion_status(capability_id, version, "accepted")
