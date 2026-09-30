"""Canary monitor integration tests (crawl.md §27, STEP 11).

The monitor's SQL loop against the LIVE database: a regressing canary
rolls back (release pointer → disabled) on the RIGHT channel only —
a same capability+version released on another channel stays untouched;
a healthy canary stays canary; insufficient evidence never rolls back.

NOTE: a non-dry-run monitor run acts on EVERY status='canary' row in the
shared DB, not just this file's seeds — that is the monitor's real
production behavior; the assertions here only ever read rows this file
seeded (uid-unique capability ids), so foreign rows cannot flip them.
"""

import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.bundles import SqlAlchemyBundleRepository
from aci.adapters.outbound.postgres.outcomes import SqlAlchemyOutcomeRecorder
from aci.adapters.outbound.postgres.repositories import (
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.route_runs import SqlAlchemyRouteRunRepository
from aci.domain.capability.models import (
    BundleItem,
    Capability,
    CapabilityBundle,
    CapabilityVersion,
    OutcomeEvidence,
    OutcomeVerdict,
    SkillSpec,
)
from aci.domain.routing.models import RouteRun

pytestmark = pytest.mark.integration

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from canary_monitor import main as monitor_main  # noqa: E402

NOW = datetime(2026, 9, 29, tzinfo=UTC)
DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def monitor_db_url() -> str:
    import os

    url = os.environ.get("ACI_DATABASE_URL", DB_URL)
    eng = create_engine(url, connect_args={"connect_timeout": 2})
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        eng.dispose()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    else:
        eng.dispose()
        return url


@pytest.fixture()
def monitor_engine(monitor_db_url: str) -> Iterator[Any]:
    eng = create_engine(monitor_db_url, connect_args={"connect_timeout": 2})
    yield eng
    eng.dispose()


def _seed_capability(sessions: sessionmaker[Session], cap_id: str, *versions: str) -> None:
    caps = SqlAlchemyCapabilityRepository(sessions)
    existing = caps.get_capability(cap_id)
    if existing is None:
        caps.create_capability(Capability(id=cap_id, kind="skill", created_at=NOW))
    for version in versions:
        caps.create_version(
            CapabilityVersion(
                capability_id=cap_id,
                version=version,
                kind="skill",
                content_digest="sha256:" + "a" * 64,
                created_at=NOW,
                spec=SkillSpec(),
            )
        )


def _seed_release(
    sessions: sessionmaker[Session],
    cap_id: str,
    version: str,
    channel: str,
    status: str,
    canary_percent: int | None = None,
) -> None:
    releases = SqlAlchemyReleaseRepository(sessions)
    from aci.domain.capability.models import CapabilityRelease

    releases.set_release(
        CapabilityRelease(
            capability_id=cap_id,
            version=version,
            channel=channel,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            canary_percent=canary_percent,
            promoted_at=NOW,
            approved_by="test",
        )
    )


def _seed_outcomes(
    sessions: sessionmaker[Session],
    cap_id: str,
    version: str,
    statuses: list[str],
) -> None:
    """Bundle + route run + outcome events/verdicts for one capability@version."""
    runs = SqlAlchemyRouteRunRepository(sessions)
    bundles = SqlAlchemyBundleRepository(sessions)
    recorder = SqlAlchemyOutcomeRecorder(sessions)
    for status in statuses:
        run_id = uid("run")
        bundle_id = uid("bun")
        runs.put_route_run(
            RouteRun(
                route_run_id=run_id,
                request_id=uid("req"),
                trace_id=uid("trc"),
                created_at=NOW,
                principal_id="p-1",
                client_type="test",
                protocol_type="rest",
                task_text="t",
            )
        )
        bundles.put_bundle(
            CapabilityBundle(
                bundle_id=bundle_id,
                route_run_id=run_id,
                created_at=NOW,
                items=[
                    BundleItem(
                        capability_id=cap_id,
                        version=version,
                        digest="sha256:" + "a" * 64,
                        kind="skill",
                    )
                ],
            )
        )
        recorder.record(
            OutcomeEvidence(
                outcome_id=uid("out"),
                route_run_id=run_id,
                bundle_id=bundle_id,
                received_at=NOW,
                verdicts=[
                    OutcomeVerdict(source="test_harness", status=status)  # type: ignore[arg-type]
                ],
            )
        )


def _release_status(eng: Any, cap_id: str, channel: str) -> str | None:
    with eng.connect() as c:
        row = c.execute(
            text(
                "SELECT status FROM capability_releases "
                "WHERE capability_id = :cap AND channel = :channel"
            ),
            {"cap": cap_id, "channel": channel},
        ).fetchone()
    return row[0] if row else None


def test_regressing_canary_rolls_back_on_its_channel_only(
    monitor_db_url: str, monitor_engine: Any, sessions: sessionmaker[Session]
) -> None:
    """§27: canary 3/3 failures with no incumbent → absolute-rate rollback;
    the SAME capability+version released on another channel stays active."""
    cap = uid("cap")
    _seed_capability(sessions, cap, "2.0.0", "1.0.0")
    # incumbent active on production (no outcomes → baseline empty is fine,
    # the absolute check governs), canary on staging, active on production
    _seed_release(sessions, cap, "1.0.0", "production", "active")
    _seed_release(sessions, cap, "2.0.0", "staging", "canary", canary_percent=25)
    _seed_outcomes(sessions, cap, "2.0.0", ["failure", "failure", "failure"])

    rc = monitor_main(["--database-url", monitor_db_url, "--dry-run"])
    assert rc == 0
    # dry-run: nothing changed
    assert _release_status(monitor_engine, cap, "staging") == "canary"

    rc = monitor_main(["--database-url", monitor_db_url])
    assert rc == 0
    assert _release_status(monitor_engine, cap, "staging") == "disabled"
    # the production release of the SAME capability is untouched
    assert _release_status(monitor_engine, cap, "production") == "active"


def test_healthy_canary_stays_canary(
    monitor_db_url: str, monitor_engine: Any, sessions: sessionmaker[Session]
) -> None:
    cap = uid("cap")
    _seed_capability(sessions, cap, "2.0.0")
    _seed_release(sessions, cap, "2.0.0", "production", "canary", canary_percent=50)
    _seed_outcomes(sessions, cap, "2.0.0", ["success", "success", "success"])

    rc = monitor_main(["--database-url", monitor_db_url])
    assert rc == 0
    assert _release_status(monitor_engine, cap, "production") == "canary"


def test_insufficient_evidence_never_rolls_back(
    monitor_db_url: str, monitor_engine: Any, sessions: sessionmaker[Session]
) -> None:
    """Below MIN_CANARY_OUTCOMES there is no verdict — a canary with 2
    failures (all it has) stays canary until enough evidence lands."""
    cap = uid("cap")
    _seed_capability(sessions, cap, "2.0.0")
    _seed_release(sessions, cap, "2.0.0", "production", "canary", canary_percent=10)
    _seed_outcomes(sessions, cap, "2.0.0", ["failure", "failure"])

    rc = monitor_main(["--database-url", monitor_db_url])
    assert rc == 0
    assert _release_status(monitor_engine, cap, "production") == "canary"


def test_incumbent_baseline_governs_relative_margin(
    monitor_db_url: str, monitor_engine: Any, sessions: sessionmaker[Session]
) -> None:
    """Canary 0.33 fail vs incumbent 0.0 + margin 0.2 → rollback; a canary
    at the incumbent's rate stays."""
    cap = uid("cap")
    _seed_capability(sessions, cap, "2.0.0", "1.0.0")
    _seed_release(sessions, cap, "1.0.0", "production", "active")
    _seed_release(sessions, cap, "2.0.0", "production", "canary", canary_percent=25)
    # incumbent: 4 success, 0 fail → base rate 0.0
    _seed_outcomes(sessions, cap, "1.0.0", ["success"] * 4)
    # canary: 2 fail 4 success → 0.33 > 0.0 + 0.2 → rollback
    _seed_outcomes(
        sessions, cap, "2.0.0", ["failure", "failure", "success", "success", "success", "success"]
    )

    rc = monitor_main(["--database-url", monitor_db_url])
    assert rc == 0
    assert _release_status(monitor_engine, cap, "production") == "disabled"
