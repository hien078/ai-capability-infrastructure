"""Agent run persistence (HarnessKernel §41.1): SqlAlchemyAgentRunRepository.

Implements the AgentRunStore protocol (application/protocols.py). Rows are
projections of frozen domain models — `model_dump()` in, `model_validate()`
out; the store never mutates a live run (INV-01).
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import AgentRunEventRow, AgentRunRow
from aci.domain.runtime.persistence import AgentRunEventRecord, AgentRunRecord


def _record_of(row: AgentRunRow) -> AgentRunRecord:
    return AgentRunRecord.model_validate(
        {
            "run_id": row.run_id,
            "parent_run_id": row.parent_run_id,
            "profile_id": row.profile_id,
            "objective": row.objective,
            "workspace": row.workspace,
            "status": row.status,
            "stop_reason": row.stop_reason,
            "detail_code": row.detail_code,
            "summary": row.summary,
            "artifacts": row.artifacts,
            "evidence": row.evidence,
            "usage": row.usage,
            "spec": row.spec,
            "trace_ref": row.trace_ref,
            "verification_command": row.verification_command,
            "created_at": row.created_at,
            "finished_at": row.finished_at,
        }
    )


class SqlAlchemyAgentRunRepository:
    """Implements the AgentRunStore protocol (migration 0016)."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record_run(self, record: AgentRunRecord) -> None:
        """Insert-or-replace the run row (idempotent by run_id — a re-record
        of the same terminal state is the same fact)."""
        with self._sessions() as session, session.begin():
            session.execute(
                pg_insert(AgentRunRow)
                .values(
                    run_id=record.run_id,
                    parent_run_id=record.parent_run_id,
                    profile_id=record.profile_id,
                    objective=record.objective,
                    workspace=record.workspace,
                    status=record.status,
                    stop_reason=record.stop_reason,
                    detail_code=record.detail_code,
                    summary=record.summary,
                    artifacts=record.artifacts,
                    evidence=record.evidence,
                    usage=record.usage,
                    spec=record.spec,
                    trace_ref=record.trace_ref,
                    verification_command=record.verification_command,
                    created_at=record.created_at,
                    finished_at=record.finished_at,
                )
                .on_conflict_do_update(
                    index_elements=["run_id"],
                    set_={
                        "status": record.status,
                        "stop_reason": record.stop_reason,
                        "detail_code": record.detail_code,
                        "summary": record.summary,
                        "artifacts": record.artifacts,
                        "evidence": record.evidence,
                        "usage": record.usage,
                        "trace_ref": record.trace_ref,
                        "finished_at": record.finished_at,
                    },
                )
            )

    def record_events(self, events: list[AgentRunEventRecord]) -> None:
        """Replace the run's event rows (the bus history is the full truth for
        a run — a re-record is the same fact, not a duplicate append)."""
        if not events:
            return
        run_ids = {e.run_id for e in events}
        with self._sessions() as session, session.begin():
            for run_id in run_ids:
                session.query(AgentRunEventRow).filter(AgentRunEventRow.run_id == run_id).delete(
                    synchronize_session=False
                )
            session.add_all(
                [
                    AgentRunEventRow(
                        event_id=e.event_id,
                        run_id=e.run_id,
                        seq=e.seq,
                        event_type=e.event_type,
                        turn_id=e.turn_id,
                        payload=e.payload,
                        recorded_at=e.recorded_at,
                    )
                    for e in events
                ]
            )

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        with self._sessions() as session:
            row = session.get(AgentRunRow, run_id)
            return _record_of(row) if row is not None else None

    def list_recent(self, limit: int = 50) -> list[AgentRunRecord]:
        """Newest-first by created_at (the console/ops view)."""
        with self._sessions() as session:
            rows = session.scalars(
                select(AgentRunRow).order_by(AgentRunRow.created_at.desc()).limit(limit)
            ).all()
            return [_record_of(r) for r in rows]
