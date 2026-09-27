"""Policy snapshot persistence (plan §46)."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import PolicySnapshotRow
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.policy.models import PolicySnapshot


def _snapshot_of(row: PolicySnapshotRow) -> PolicySnapshot:
    return PolicySnapshot.model_validate(
        {
            "snapshot_id": row.snapshot_id,
            "created_at": row.created_at,
            "rules": row.rules,
        }
    )


class SqlAlchemyPolicySnapshotRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_snapshot(self, snapshot: PolicySnapshot) -> PolicySnapshot:
        try:
            with self._sessions() as session, session.begin():
                session.add(
                    PolicySnapshotRow(
                        snapshot_id=snapshot.snapshot_id,
                        created_at=snapshot.created_at,
                        rules=snapshot.rules.model_dump(mode="json"),
                    )
                )
        except IntegrityError as exc:
            raise DomainError(
                ErrorCode.CAPABILITY_ALREADY_EXISTS,
                f"policy snapshot {snapshot.snapshot_id} already exists",
            ) from exc
        return snapshot

    def get_snapshot(self, snapshot_id: str) -> PolicySnapshot | None:
        with self._sessions() as session:
            row = session.get(PolicySnapshotRow, snapshot_id)
            return _snapshot_of(row) if row is not None else None

    def latest_snapshot(self) -> PolicySnapshot | None:
        with self._sessions() as session:
            row = (
                session.query(PolicySnapshotRow)
                .order_by(PolicySnapshotRow.created_at.desc())
                .first()
            )
            return _snapshot_of(row) if row is not None else None
