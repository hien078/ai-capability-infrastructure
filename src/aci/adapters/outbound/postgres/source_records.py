"""Source-record persistence for the provenance trail (plan §23)."""

from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import SourceRecordRow
from aci.domain.skills.models import SourceProvenance


def _record_of(row: SourceRecordRow) -> SourceProvenance:
    return SourceProvenance.model_validate(
        {
            "record_id": row.record_id,
            "capability_id": row.capability_id,
            "version": row.version,
            "source_type": row.source_type,
            "source_repository": row.source_repository,
            "source_path": row.source_path,
            "source_url_reference": row.source_url_reference,
            "commit_sha": row.commit_sha,
            "source_version": row.source_version,
            "raw_snapshot_digest": row.raw_snapshot_digest,
            "license_identifier": row.license_identifier,
            "ingested_at": row.ingested_at,
            "ingestion_tool_version": row.ingestion_tool_version,
            "local_transformations": row.local_transformations,
            "derived_from": row.derived_from,
        }
    )


class SqlAlchemySourceRecordRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def add_source_record(self, record: SourceProvenance) -> SourceProvenance:
        with self._sessions() as session, session.begin():
            session.add(
                SourceRecordRow(
                    record_id=record.record_id,
                    capability_id=record.capability_id,
                    version=record.version,
                    source_type=record.source_type,
                    source_repository=record.source_repository,
                    source_path=record.source_path,
                    source_url_reference=record.source_url_reference,
                    commit_sha=record.commit_sha,
                    source_version=record.source_version,
                    raw_snapshot_digest=record.raw_snapshot_digest,
                    license_identifier=record.license_identifier,
                    ingested_at=record.ingested_at,
                    ingestion_tool_version=record.ingestion_tool_version,
                    local_transformations=list(record.local_transformations),
                    derived_from=record.derived_from,
                )
            )
        return record

    def list_source_records(self, capability_id: str) -> list[SourceProvenance]:
        with self._sessions() as session:
            rows = (
                session.query(SourceRecordRow)
                .filter_by(capability_id=capability_id)
                # deterministic even when several records share ingested_at
                .order_by(SourceRecordRow.ingested_at, SourceRecordRow.record_id)
                .all()
            )
            return [_record_of(r) for r in rows]
