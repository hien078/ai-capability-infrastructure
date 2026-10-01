"""Agent run persistence (HarnessKernel §41.1): SqlAlchemyAgentRunRepository.

Implements the AgentRunStore protocol (application/protocols.py). Rows are
projections of frozen domain models — `model_dump()` in, `model_validate()`
out; the store never mutates a live run (INV-01).
"""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import (
    AgentRunCheckpointRow,
    AgentRunEventRow,
    AgentRunRow,
)
from aci.domain.runtime.persistence import (
    AgentRunCheckpointRecord,
    AgentRunEventRecord,
    AgentRunRecord,
)


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
            "contract": row.contract,
            "run_options": row.run_options,
            "run_dir": row.run_dir,
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
                    contract=record.contract,
                    run_options=record.run_options,
                    # Server-side only (excluded from the record's dumps):
                    # read off the attribute, never via model_dump().
                    run_dir=record.run_dir,
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
                        "contract": record.contract,
                        "run_options": record.run_options,
                        "run_dir": record.run_dir,
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

    # -- migration 0019: pause checkpoints + resumed-segment events -----------

    def append_events(self, events: list[AgentRunEventRecord]) -> None:
        """Append a resumed segment after the run's stored events: ``seq`` is
        re-based past the current maximum (the bus history of a resumed
        segment starts at 0 again). An event id already stored is skipped
        (a re-append of the same segment is the same fact)."""
        if not events:
            return
        with self._sessions() as session, session.begin():
            by_run: dict[str, list[AgentRunEventRecord]] = {}
            for event in events:
                by_run.setdefault(event.run_id, []).append(event)
            for run_id, run_events in by_run.items():
                base = session.scalar(
                    select(func.coalesce(func.max(AgentRunEventRow.seq), -1)).where(
                        AgentRunEventRow.run_id == run_id
                    )
                )
                offset = int(base if base is not None else -1) + 1
                ordered = sorted(run_events, key=lambda e: e.seq)
                session.execute(
                    pg_insert(AgentRunEventRow)
                    .values(
                        [
                            {
                                "event_id": e.event_id,
                                "run_id": e.run_id,
                                "seq": offset + i,
                                "event_type": e.event_type,
                                "turn_id": e.turn_id,
                                "payload": e.payload,
                                "recorded_at": e.recorded_at,
                            }
                            for i, e in enumerate(ordered)
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=["event_id"])
                )

    def record_checkpoint(self, record: AgentRunCheckpointRecord) -> None:
        """Insert a pause checkpoint (idempotent by checkpoint_id; a stored
        checkpoint — and its consumed_at — is never overwritten)."""
        with self._sessions() as session, session.begin():
            session.execute(
                pg_insert(AgentRunCheckpointRow)
                .values(
                    checkpoint_id=record.checkpoint_id,
                    run_id=record.run_id,
                    kind=record.kind,
                    approval_id=record.approval_id,
                    payload=record.payload,
                    created_at=record.created_at,
                    consumed_at=record.consumed_at,
                )
                .on_conflict_do_nothing(index_elements=["checkpoint_id"])
            )

    def latest_checkpoint(self, run_id: str) -> AgentRunCheckpointRecord | None:
        with self._sessions() as session:
            row = session.scalars(
                select(AgentRunCheckpointRow)
                .where(AgentRunCheckpointRow.run_id == run_id)
                .order_by(AgentRunCheckpointRow.created_at.desc())
                .limit(1)
            ).first()
            if row is None:
                return None
            return AgentRunCheckpointRecord(
                checkpoint_id=row.checkpoint_id,
                run_id=row.run_id,
                kind=row.kind,
                approval_id=row.approval_id,
                payload=row.payload,
                created_at=row.created_at,
                consumed_at=row.consumed_at,
            )

    def consume_checkpoint(self, checkpoint_id: str, *, at: datetime) -> bool:
        """Compare-and-set ``consumed_at`` NULL → ``at``: exactly one caller
        wins, concurrently or across processes (row-level lock of the UPDATE)."""
        with self._sessions() as session, session.begin():
            claimed = session.execute(
                update(AgentRunCheckpointRow)
                .where(
                    AgentRunCheckpointRow.checkpoint_id == checkpoint_id,
                    AgentRunCheckpointRow.consumed_at.is_(None),
                )
                .values(consumed_at=at)
                .returning(AgentRunCheckpointRow.checkpoint_id)
            ).first()
            return claimed is not None
