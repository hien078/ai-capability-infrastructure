"""Optional integration coverage for scripts/usage_report.py — DEV DB (`aci`) ONLY.

The E3 instrument is an aci_bench reader, but this test never touches
aci_bench: it runs the fetch/build path against the dev/test DB and proves
the read-only enforcement (a write is REFUSED on the script's connection).
Never asserts global emptiness — the dev DB is shared and accumulates rows.
"""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import usage_report as ur  # noqa: E402

pytestmark = pytest.mark.integration

#: The DEV DB only — never aci_bench (the operational corpus + telemetry).
DEV_DB_URL = os.environ.get("ACI_DATABASE_URL", "postgresql+psycopg://aci:aci@localhost:5432/aci")


def _engine_or_skip():
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError

    engine = create_engine(
        DEV_DB_URL, connect_args={"connect_timeout": 2, "options": ur.READ_ONLY_OPTION}
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    return engine


def test_report_builds_over_dev_db() -> None:
    from sqlalchemy.exc import ProgrammingError

    engine = _engine_or_skip()
    try:
        generated_at = datetime.now(UTC)
        with engine.connect() as conn:
            ur.assert_read_only(conn)
            data = ur.fetch_report_data(conn, generated_at=generated_at)
    except ProgrammingError as exc:
        pytest.skip(f"dev DB not migrated (agent_runs/route_runs schema): {exc}")
    finally:
        engine.dispose()
    report = ur.build_report(data, generated_at=generated_at, database="aci")
    # Observational only: the dev DB accumulates rows across runs, so assert
    # structure, not emptiness or any absolute count.
    assert report["read_only"] is True
    assert report["weeks"], "at least one ISO week (or a fully empty dev DB)"
    for week in report["weeks"]:
        assert week["route_runs"]["total"] >= 0
        assert week["agent_runs"]["total"] >= 0
    assert ur.render_text(report)


def test_connection_refuses_writes() -> None:
    """The script's engine cannot mutate anything: a no-op UPDATE (0 rows)
    is refused because the transaction is read-only — server-side."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    engine = _engine_or_skip()
    try:
        with engine.connect() as conn:
            ur.assert_read_only(conn)
            with pytest.raises(SQLAlchemyError, match="[Rr]ead.only"):
                conn.execute(text("UPDATE route_runs SET principal_id = principal_id WHERE false"))
    finally:
        engine.dispose()
