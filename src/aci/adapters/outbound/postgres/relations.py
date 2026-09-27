"""Capability relation persistence (plan §18; V1 subset of relations)."""

from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import CapabilityRelationRow
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import CapabilityRelation


def _relation_of(row: CapabilityRelationRow) -> CapabilityRelation:
    return CapabilityRelation.model_validate(
        {
            "relation_id": row.relation_id,
            "source_capability_id": row.source_capability_id,
            "source_version_constraint": row.source_version_constraint,
            "target_capability_id": row.target_capability_id,
            "target_version_constraint": row.target_version_constraint,
            "relation": row.relation,
            "metadata": row.meta,
        }
    )


def _row_of(relation: CapabilityRelation) -> CapabilityRelationRow:
    return CapabilityRelationRow(
        relation_id=relation.relation_id,
        source_capability_id=relation.source_capability_id,
        source_version_constraint=relation.source_version_constraint,
        target_capability_id=relation.target_capability_id,
        target_version_constraint=relation.target_version_constraint,
        relation=relation.relation,
        meta=dict(relation.metadata),
    )


class SqlAlchemyRelationRepository:
    """Implements the RelationRepository protocol."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_relation(self, relation: CapabilityRelation) -> CapabilityRelation:
        with self._sessions() as session, session.begin():
            existing = session.get(CapabilityRelationRow, relation.relation_id)
            if existing is not None:
                # Relations are registry facts: same id must not silently change.
                raise DomainError(
                    ErrorCode.CAPABILITY_ALREADY_EXISTS,
                    f"relation {relation.relation_id} already exists; "
                    "use a new relation_id for corrected facts",
                )
            session.add(_row_of(relation))
        return relation

    def list_relations(self, source_capability_id: str) -> list[CapabilityRelation]:
        with self._sessions() as session:
            rows: list[CapabilityRelationRow] = (
                session.query(CapabilityRelationRow)
                .filter_by(source_capability_id=source_capability_id)
                .order_by(CapabilityRelationRow.relation_id)
                .all()
            )
            return [_relation_of(row) for row in rows]
