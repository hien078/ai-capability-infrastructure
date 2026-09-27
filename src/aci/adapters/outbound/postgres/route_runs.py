"""Route run telemetry persistence (plan §36)."""

from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import RouteRunRow
from aci.domain.routing.models import RouteRun


def _run_of(row: RouteRunRow) -> RouteRun:
    return RouteRun.model_validate(
        {
            "route_run_id": row.route_run_id,
            "request_id": row.request_id,
            "trace_id": row.trace_id,
            "created_at": row.created_at,
            "principal_id": row.principal_id,
            "organization_id": row.organization_id,
            "workspace_id": row.workspace_id,
            "client_type": row.client_type,
            "client_version": row.client_version,
            "protocol_type": row.protocol_type,
            "task_text": row.task_text,
            "policy_snapshot_id": row.policy_snapshot_id,
            "eligible_count": row.eligible_count,
            "stages": row.stages,
            "reranker_implementation": row.reranker_implementation,
            "reranker_version": row.reranker_version,
            "composer_implementation": row.composer_implementation,
            "composer_version": row.composer_version,
            "latency_ms": row.latency_ms,
            "error_code": row.error_code,
            "bundle_id": row.bundle_id,
        }
    )


class SqlAlchemyRouteRunRepository:
    """Implements the RouteRunRepository protocol."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_route_run(self, run: RouteRun) -> RouteRun:
        with self._sessions() as session, session.begin():
            session.add(
                RouteRunRow(
                    route_run_id=run.route_run_id,
                    request_id=run.request_id,
                    trace_id=run.trace_id,
                    created_at=run.created_at,
                    principal_id=run.principal_id,
                    organization_id=run.organization_id,
                    workspace_id=run.workspace_id,
                    client_type=run.client_type,
                    client_version=run.client_version,
                    protocol_type=run.protocol_type,
                    task_text=run.task_text,
                    policy_snapshot_id=run.policy_snapshot_id,
                    eligible_count=run.eligible_count,
                    stages=dict(run.stages),
                    reranker_implementation=run.reranker_implementation,
                    reranker_version=run.reranker_version,
                    composer_implementation=run.composer_implementation,
                    composer_version=run.composer_version,
                    latency_ms=run.latency_ms,
                    error_code=run.error_code,
                    bundle_id=run.bundle_id,
                )
            )
        return run

    def get_route_run(self, route_run_id: str) -> RouteRun | None:
        with self._sessions() as session:
            row = session.get(RouteRunRow, route_run_id)
            return _run_of(row) if row is not None else None
