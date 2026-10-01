"""Optional integration coverage for scripts/evidence_completeness.py —
DEV DB (`aci`) ONLY.

The §3.2 instrument is an operational-DB reader, but this test never touches
aci_bench or any remote host: it runs the fetch/build path against the
dev/test DB and proves the read-only enforcement (a write is REFUSED on the
script's connection). Never asserts global emptiness — the dev DB is shared
and accumulates rows.
"""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import OperationalError

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import evidence_completeness as ec  # noqa: E402
import usage_report as ur  # noqa: E402

pytestmark = pytest.mark.integration

#: The DEV DB only — NEVER aci_bench (the operational corpus + telemetry) and
#: NEVER a remote host. ACI_DATABASE_URL is honored only when it names the
#: local `aci` database; anything else (aci_bench, a remote host, a socket
#: URL with another database) skips this test rather than connecting.
DEV_DB_URL = os.environ.get("ACI_DATABASE_URL", "postgresql+psycopg://aci:aci@localhost:5432/aci")


def _dev_url_or_skip() -> str:
    url = make_url(DEV_DB_URL)
    host = url.host
    local = host in (None, "localhost", "127.0.0.1", "::1")
    if (url.database or "") != "aci" or not local:
        pytest.skip(
            "evidence_completeness integration test targets ONLY the dev `aci` "
            f"DB on localhost (got database={url.database!r} host={host!r})"
        )
    return DEV_DB_URL


def _engine_or_skip(database_url: str):
    engine = create_engine(
        database_url, connect_args={"connect_timeout": 2, "options": ur.READ_ONLY_OPTION}
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    return engine


def test_completeness_builds_over_dev_db() -> None:
    from sqlalchemy.exc import ProgrammingError

    url = _dev_url_or_skip()
    engine = _engine_or_skip(url)
    try:
        generated_at = datetime.now(UTC)
        with engine.connect() as conn:
            ec.assert_read_only(conn)
            data = ec.fetch_evidence_data(conn, since=None, until=None)
    except ProgrammingError as exc:
        pytest.skip(f"dev DB not migrated (agent_runs/route_runs schema): {exc}")
    finally:
        engine.dispose()
    report = ec.build_report(
        data, generated_at=generated_at, database="aci", since=None, until=None
    )
    # Observational only: the dev DB accumulates rows across runs, so assert
    # structure, not emptiness or any absolute count.
    assert report.read_only is True
    assert report.agent_runs["total"] >= 0
    assert report.routed_bundles["route_runs_in_window"] >= 0
    assert 0.0 <= report.agent_runs["complete_fraction_plane_runs"] <= 1.0
    assert 0.0 <= report.routed_bundles["outcome_coverage_fraction"] <= 1.0
    for row in report.per_run:
        assert row["category"]
    assert ec.render_text(report)


def test_connection_refuses_writes() -> None:
    """The script's engine cannot mutate anything: a no-op UPDATE (0 rows)
    is refused because the transaction is read-only — server-side."""
    from sqlalchemy.exc import SQLAlchemyError

    url = _dev_url_or_skip()
    engine = _engine_or_skip(url)
    try:
        with engine.connect() as conn:
            ec.assert_read_only(conn)
            with pytest.raises(SQLAlchemyError, match="[Rr]ead.only"):
                conn.execute(text("UPDATE route_runs SET principal_id = principal_id WHERE false"))
    finally:
        engine.dispose()
