"""Phase 1 foundation: health/readiness."""

import logging

from fastapi import FastAPI
from sqlalchemy import create_engine, text

from aci.config import settings

log = logging.getLogger(__name__)
app = FastAPI(title="AI Capability Infrastructure")


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
