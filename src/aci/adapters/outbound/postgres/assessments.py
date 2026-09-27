"""License + security assessment persistence (Phase 4, plan §§24-25)."""

from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import (
    LicenseAssessmentRow,
    SecurityAssessmentRow,
)
from aci.domain.provenance.models import (
    LicenseAssessment,
    SecurityAssessment,
)


def _license_of(row: LicenseAssessmentRow) -> LicenseAssessment:
    return LicenseAssessment.model_validate(
        {
            "assessment_id": row.assessment_id,
            "capability_id": row.capability_id,
            "version": row.version,
            "license_identifier": row.license_identifier,
            "permissions": row.permissions,
            "assessed_at": row.assessed_at,
            "assessed_by": row.assessed_by,
            "notes": row.notes,
        }
    )


def _security_of(row: SecurityAssessmentRow) -> SecurityAssessment:
    return SecurityAssessment.model_validate(
        {
            "assessment_id": row.assessment_id,
            "capability_id": row.capability_id,
            "version": row.version,
            "scan_status": row.scan_status,
            "findings": row.findings,
            "scanned_at": row.scanned_at,
            "scanner_version": row.scanner_version,
            "reviewed_by": row.reviewed_by,
        }
    )


class SqlAlchemyLicenseAssessmentRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_assessment(self, assessment: LicenseAssessment) -> LicenseAssessment:
        with self._sessions() as session, session.begin():
            row = session.get(
                LicenseAssessmentRow,
                (assessment.assessment_id, assessment.capability_id, assessment.version),
            )
            if row is None:
                session.add(
                    LicenseAssessmentRow(
                        assessment_id=assessment.assessment_id,
                        capability_id=assessment.capability_id,
                        version=assessment.version,
                        license_identifier=assessment.license_identifier,
                        permissions=assessment.permissions.model_dump(),
                        assessed_at=assessment.assessed_at,
                        assessed_by=assessment.assessed_by,
                        notes=assessment.notes,
                    )
                )
            else:
                row.license_identifier = assessment.license_identifier
                row.permissions = assessment.permissions.model_dump()
                row.assessed_at = assessment.assessed_at
                row.assessed_by = assessment.assessed_by
                row.notes = assessment.notes
        return assessment

    def get_assessment(self, capability_id: str, version: str) -> LicenseAssessment | None:
        with self._sessions() as session:
            rows = (
                session.query(LicenseAssessmentRow)
                .filter_by(capability_id=capability_id, version=version)
                .order_by(LicenseAssessmentRow.assessed_at)
                .all()
            )
            if not rows:
                return None
            return _license_of(rows[-1])  # latest assessment wins


class SqlAlchemySecurityAssessmentRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_assessment(self, assessment: SecurityAssessment) -> SecurityAssessment:
        with self._sessions() as session, session.begin():
            row = session.get(
                SecurityAssessmentRow,
                (assessment.assessment_id, assessment.capability_id, assessment.version),
            )
            if row is None:
                session.add(
                    SecurityAssessmentRow(
                        assessment_id=assessment.assessment_id,
                        capability_id=assessment.capability_id,
                        version=assessment.version,
                        scan_status=assessment.scan_status,
                        findings=list(assessment.findings),
                        scanned_at=assessment.scanned_at,
                        scanner_version=assessment.scanner_version,
                        reviewed_by=assessment.reviewed_by,
                    )
                )
            else:
                row.scan_status = assessment.scan_status
                row.findings = list(assessment.findings)
                row.scanned_at = assessment.scanned_at
                row.scanner_version = assessment.scanner_version
                row.reviewed_by = assessment.reviewed_by
        return assessment

    def get_assessment(self, capability_id: str, version: str) -> SecurityAssessment | None:
        with self._sessions() as session:
            rows = (
                session.query(SecurityAssessmentRow)
                .filter_by(capability_id=capability_id, version=version)
                .order_by(SecurityAssessmentRow.scanned_at)
                .all()
            )
            if not rows:
                return None
            return _security_of(rows[-1])  # latest scan wins
