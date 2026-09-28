"""License + security assessment persistence (Phase 4, plan §§24-25)."""

from sqlalchemy import tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import (
    LicenseAssessmentRow,
    SecurityAssessmentRow,
)
from aci.domain.capability.errors import DomainError, ErrorCode
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
        """Append-only by assessment_id: gate evidence keeps its audit trail.
        Re-assessing means writing a new assessment_id; the latest one wins."""
        try:
            with self._sessions() as session, session.begin():
                row = session.get(
                    LicenseAssessmentRow,
                    (assessment.assessment_id, assessment.capability_id, assessment.version),
                )
                if row is not None:
                    raise DomainError(
                        ErrorCode.CAPABILITY_ALREADY_EXISTS,
                        f"license assessment {assessment.assessment_id} already exists; "
                        "append a new assessment_id instead of overwriting evidence",
                    )
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
        except IntegrityError as exc:  # concurrent append of the same id
            raise DomainError(
                ErrorCode.CAPABILITY_ALREADY_EXISTS,
                f"license assessment {assessment.assessment_id} already exists",
            ) from exc
        return assessment

    def get_assessment(self, capability_id: str, version: str) -> LicenseAssessment | None:
        with self._sessions() as session:
            rows = (
                session.query(LicenseAssessmentRow)
                .filter_by(capability_id=capability_id, version=version)
                .order_by(LicenseAssessmentRow.assessed_at, LicenseAssessmentRow.assessment_id)
                .all()
            )
            if not rows:
                return None
            return _license_of(rows[-1])  # latest assessment wins

    def get_assessments(self, pairs: list[tuple[str, str]]) -> list[LicenseAssessment]:
        """Bulk read with the same latest-wins semantics as get_assessment."""
        if not pairs:
            return []
        with self._sessions() as session:
            rows = (
                session.query(LicenseAssessmentRow)
                .filter(
                    tuple_(LicenseAssessmentRow.capability_id, LicenseAssessmentRow.version).in_(
                        pairs
                    )
                )
                .order_by(LicenseAssessmentRow.assessed_at, LicenseAssessmentRow.assessment_id)
                .all()
            )
            latest: dict[tuple[str, str], LicenseAssessmentRow] = {}
            for row in rows:  # ordered: the last row per pair is the latest
                latest[(row.capability_id, row.version)] = row
            return [_license_of(row) for row in latest.values()]


class SqlAlchemySecurityAssessmentRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_assessment(self, assessment: SecurityAssessment) -> SecurityAssessment:
        """Append-only by assessment_id: scan evidence keeps its audit trail."""
        try:
            with self._sessions() as session, session.begin():
                row = session.get(
                    SecurityAssessmentRow,
                    (assessment.assessment_id, assessment.capability_id, assessment.version),
                )
                if row is not None:
                    raise DomainError(
                        ErrorCode.CAPABILITY_ALREADY_EXISTS,
                        f"security assessment {assessment.assessment_id} already exists; "
                        "append a new assessment_id instead of overwriting evidence",
                    )
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
        except IntegrityError as exc:  # concurrent append of the same id
            raise DomainError(
                ErrorCode.CAPABILITY_ALREADY_EXISTS,
                f"security assessment {assessment.assessment_id} already exists",
            ) from exc
        return assessment

    def get_assessment(self, capability_id: str, version: str) -> SecurityAssessment | None:
        with self._sessions() as session:
            rows = (
                session.query(SecurityAssessmentRow)
                .filter_by(capability_id=capability_id, version=version)
                .order_by(SecurityAssessmentRow.scanned_at, SecurityAssessmentRow.assessment_id)
                .all()
            )
            if not rows:
                return None
            return _security_of(rows[-1])  # latest scan wins

    def get_assessments(self, pairs: list[tuple[str, str]]) -> list[SecurityAssessment]:
        """Bulk read with the same latest-wins semantics as get_assessment."""
        if not pairs:
            return []
        with self._sessions() as session:
            rows = (
                session.query(SecurityAssessmentRow)
                .filter(
                    tuple_(SecurityAssessmentRow.capability_id, SecurityAssessmentRow.version).in_(
                        pairs
                    )
                )
                .order_by(SecurityAssessmentRow.scanned_at, SecurityAssessmentRow.assessment_id)
                .all()
            )
            latest: dict[tuple[str, str], SecurityAssessmentRow] = {}
            for row in rows:  # ordered: the last row per pair is the latest
                latest[(row.capability_id, row.version)] = row
            return [_security_of(row) for row in latest.values()]
