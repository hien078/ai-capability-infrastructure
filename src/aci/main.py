"""App entry: health/readiness (Phase 1) + REST runtime API (Phase 9, §43)
+ OpenCode skill catalog (Phase 10, §28.2, ADR-005)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import create_engine, text

from aci.adapters.inbound.a2a.gateway import A2AGateway, create_a2a_router
from aci.adapters.inbound.opencode import catalog as opencode_catalog
from aci.adapters.inbound.rest import bundles as rest_bundles
from aci.adapters.inbound.rest import capabilities as rest_capabilities
from aci.adapters.inbound.rest import outcomes as rest_outcomes
from aci.adapters.inbound.rest import routes as rest_routes
from aci.adapters.inbound.rest.errors import register_error_handlers
from aci.adapters.inbound.rest.wiring import Container
from aci.config import settings
from aci.observability.logging import setup_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging(settings.log_level)
    log.info("startup complete")
    yield


def create_app(container: Container | None = None) -> FastAPI:
    """Build the full app. Tests pass their own container (e.g. a temporary
    object-store root); production uses the process-wide settings."""
    app = FastAPI(title="AI Capability Infrastructure", lifespan=lifespan)
    container = container if container is not None else Container(settings)
    app.state.container = container
    app.state.catalog = container.catalog
    register_error_handlers(app)
    app.include_router(rest_capabilities.router)
    app.include_router(rest_routes.router)
    app.include_router(rest_bundles.router)
    app.include_router(rest_outcomes.router)
    app.include_router(opencode_catalog.router)
    app.include_router(
        create_a2a_router(
            A2AGateway(
                container.agent_runtime,
                container.tasks,
                container.releases,
                container.capabilities,
                container.agent_profiles,
                service_url=settings.service_url,
            )
        )
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> dict[str, str]:
        engine = create_engine(settings.database_url)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
        return {"status": "ready"}

    return app


app = create_app()
