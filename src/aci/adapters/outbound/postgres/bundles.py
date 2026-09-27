"""Bundle persistence (plan §41; items FK to exact versions, §41.1)."""

from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import BundleItemRow, BundleRow
from aci.domain.capability.models import BundleBudget, BundleItem, CapabilityBundle


class SqlAlchemyBundleRepository:
    """Implements the BundleRepository protocol."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_bundle(self, bundle: CapabilityBundle) -> CapabilityBundle:
        with self._sessions() as session, session.begin():
            session.add(
                BundleRow(
                    bundle_id=bundle.bundle_id,
                    route_run_id=bundle.route_run_id,
                    created_at=bundle.created_at,
                    execution_order=list(bundle.execution_order),
                    budget=bundle.budget.model_dump(mode="json") if bundle.budget else None,
                    policy_snapshot_id=bundle.policy_snapshot_id,
                )
            )
            # Flush the parent first: the UOW does not order raw-FK inserts
            # (bundle_items sorts before bundles), so make the dependency explicit.
            session.flush()
            for position, item in enumerate(bundle.items):
                session.add(
                    BundleItemRow(
                        bundle_id=bundle.bundle_id,
                        position=position,
                        capability_id=item.capability_id,
                        version=item.version,
                        digest=item.digest,
                        kind=item.kind,
                        role=item.role,
                        load_mode=item.load_mode,
                        reason_code=item.reason_code,
                    )
                )
        return bundle

    def get_bundle(self, bundle_id: str) -> CapabilityBundle | None:
        with self._sessions() as session:
            row = session.get(BundleRow, bundle_id)
            if row is None:
                return None
            item_rows: list[BundleItemRow] = (
                session.query(BundleItemRow)
                .filter_by(bundle_id=bundle_id)
                .order_by(BundleItemRow.position)
                .all()
            )
            items = [
                BundleItem(
                    capability_id=item.capability_id,
                    version=item.version,
                    digest=item.digest,
                    kind=item.kind,  # type: ignore[arg-type]
                    role=item.role,  # type: ignore[arg-type]
                    load_mode=item.load_mode,  # type: ignore[arg-type]
                    reason_code=item.reason_code,
                )
                for item in item_rows
            ]
            budget = BundleBudget.model_validate(row.budget) if row.budget is not None else None
            return CapabilityBundle(
                bundle_id=row.bundle_id,
                route_run_id=row.route_run_id,
                created_at=row.created_at,
                items=items,
                execution_order=list(row.execution_order),
                budget=budget,
                policy_snapshot_id=row.policy_snapshot_id,
            )
