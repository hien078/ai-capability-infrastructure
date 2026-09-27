"""Requires live PostgreSQL (`docker compose up -d db`); skipped otherwise."""

import os

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from sqlalchemy.exc import OperationalError  # noqa: E402

pytestmark = pytest.mark.integration

URL = os.environ.get("ACI_DATABASE_URL", "postgresql+psycopg://aci:aci@localhost:5432/aci")


def test_postgres_reachable() -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(URL, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    finally:
        engine.dispose()


def test_pgvector_extension_enabled() -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(URL, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as conn:
            names = [row[0] for row in conn.execute(text("SELECT extname FROM pg_extension"))]
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    finally:
        engine.dispose()
    assert "vector" in names, "run `alembic upgrade head` first"
