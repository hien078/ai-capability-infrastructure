"""E3 usage report — is ACI used for real work? (docs/plans/aci-improvement-2026-10.md §2)

READ-ONLY instrument over the OPERATIONAL DB (default aci_bench — the real
corpus + telemetry; never the dev/test `aci` DB). Per ISO week:

  * route_runs split ORGANIC (real clients: opencode, mcp-client,
    rest-client, console, antigravity/goose) vs MEASUREMENT
    (benchmark-harness, probe, sec-suite, and harness-kernel runs started
    by run_hbench);
  * outcomes per bundle (coverage + multi-source verdicts);
  * agent_runs by status.

The harness-kernel split (real delegated work vs run_hbench arm S/P) has no
foreign key to go by: the kernel's capability client routes with a random
request_id and principal 'harness-kernel' on BOTH paths
(adapters/outbound/agent_capabilities.py). The tell-apart signal is
agent-run LINKAGE — a route_run is ORGANIC when

  1. its id appears in a persisted agent_run event payload
     (payload->>'route_run_id' — the durable-run event bridge, migration
     0016), or
  2. its created_at falls inside a persisted agent_run's
     [created_at, finished_at] window (finished_at NULL = still live →
     extends to the report generation moment);

    otherwise MEASUREMENT — run_hbench runs the kernel in-process with no
    run store, so its route_runs link to nothing. Both signals are
    approximations: see LIMITATIONS below before quoting any number.

Never writes: the engine connects with default_transaction_read_only=on and
the script verifies the setting before reading (fail closed), so it cannot
mutate any database even when pointed at one by mistake.

Usage:
    .venv/bin/python scripts/usage_report.py [--json] [--database-url URL]

    ACI_DATABASE_URL=... .venv/bin/python scripts/usage_report.py --json
"""

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, make_url, text
from sqlalchemy.engine import Connection, Engine

DEFAULT_DATABASE_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci_bench"

#: Read-only enforcement: every connection opens inside a server-side
#: read-only transaction; assert_read_only() verifies it before any query.
READ_ONLY_OPTION = "-c default_transaction_read_only=on"

ORGANIC = "organic"
MEASUREMENT = "measurement"
UNKNOWN = "unknown"

#: Real clients (E3 §2): a human or a real agent harness driving ACI.
ORGANIC_CLIENT_TYPES = frozenset(
    {"opencode", "mcp-client", "rest-client", "console", "antigravity", "goose"}
)
#: Measurement drivers: the benchmark arena, the §34 probes, the security suite.
MEASUREMENT_CLIENT_TYPES = frozenset({"benchmark-harness", "probe", "sec-suite"})
#: The kernel's capability client — used by BOTH real delegated runs
#: (REST /v1/agent-runs) and run_hbench arm S/P; split by agent-run linkage.
HARNESS_KERNEL = "harness-kernel"
#: Principals that are INFRASTRUCTURE, not use: a known check/worker identity
#: is never organic regardless of its client_type. AGENTS.md (Mac worker
#: record, 2026-10-02): the Mac's OpenCode instance routes worker health
#: checks to aci_bench under principal ``opencode-mac-check`` with client_type
#: ``opencode`` — without this set they would count ORGANIC and corrupt the
#: E3 30-day organic count (the branch-decision variable). Extend this set
#: when a new worker/check principal starts driving a real client surface.
MEASUREMENT_PRINCIPAL_IDS = frozenset({"opencode-mac-check"})

#: Honest bounds of this instrument — printed with every report (text and JSON).
LIMITATIONS: tuple[str, ...] = (
    "The §80 proof loop and the corpus/domain probes drive the REAL OpenCode plugin "
    "and the REAL REST surface, so their route_runs carry the same client_type/principal "
    "as organic use ('opencode'/'opencode'; 'rest-client' with probe/test-shaped "
    "principals) and cannot be separated by telemetry. Week 2026-W40's opencode and "
    "rest-client counts are known from the run logs to be measurement-era (§80 rounds, "
    "2026-09-28), not organic.",
    "harness-kernel route_runs are split by agent-run linkage (event payload "
    "route_run_id, else the [created_at, finished_at] window of a persisted "
    "agent_run). A real delegated run whose agent_runs row failed to persist "
    "(persistence is telemetry, §50 — it never fails a run) is misclassified as "
    "MEASUREMENT; a run_hbench arm S/P route_run concurrent with a real run is "
    "misclassified as ORGANIC.",
    "Real kernel runs from before migration 0016 (2026-09-30) left no agent_runs rows "
    "and cannot be linked. On aci_bench none exist (the first harness-kernel route_run "
    "is 2026-10-01), but on another DB they would count as MEASUREMENT.",
    "A2A delegated tasks (agent_tasks) create no route_runs and are not reported; only "
    "HarnessKernel agent_runs are.",
    "Route runs by a principal in MEASUREMENT_PRINCIPAL_IDS (currently "
    f"{sorted(MEASUREMENT_PRINCIPAL_IDS)}) are counted MEASUREMENT regardless of "
    "client_type: they are worker/health-check identities (the Mac OpenCode worker's "
    "opencode-mac-check), not organic use. The runs stay visible in the report under "
    "MEASUREMENT by_principal — nothing is dropped, only classified.",
)


# ---------------------------------------------------------------------------
# Facts — plain rows read from the four telemetry surfaces (§41).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteRunFact:
    route_run_id: str
    created_at: datetime
    client_type: str
    principal_id: str
    #: harness-kernel only: linked to a persisted agent_run (see module docstring).
    agent_run_linked: bool = False


@dataclass(frozen=True)
class BundleFact:
    bundle_id: str
    created_at: datetime


@dataclass(frozen=True)
class OutcomeFact:
    outcome_id: str
    bundle_id: str
    received_at: datetime
    #: (source, status) pairs — multi-source evidence, §33; never collapsed.
    verdicts: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AgentRunFact:
    run_id: str
    created_at: datetime
    status: str


@dataclass(frozen=True)
class ReportData:
    route_runs: tuple[RouteRunFact, ...]
    bundles: tuple[BundleFact, ...]
    outcomes: tuple[OutcomeFact, ...]
    agent_runs: tuple[AgentRunFact, ...]


# ---------------------------------------------------------------------------
# Pure classification + aggregation (unit-tested without any DB).
# ---------------------------------------------------------------------------


def iso_week(moment: datetime) -> str:
    """ISO-8601 week label of a UTC moment, e.g. ``2026-W40``.

    tz-aware datetimes convert to UTC first (DB timestamps come back
    tz-aware); naive datetimes are taken as UTC.
    """
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    iso = moment.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def classify_route_run(
    client_type: str, agent_run_linked: bool = False, principal_id: str = ""
) -> str:
    """ORGANIC / MEASUREMENT / UNKNOWN for one route run.

    harness-kernel is the only ambivalent client_type (real delegated runs
    and run_hbench arm S/P share it): linked → ORGANIC, unlinked →
    MEASUREMENT. A known MEASUREMENT_PRINCIPAL_IDS check identity is
    MEASUREMENT whatever the client_type (a worker health check is not use).
    Unknown client_types are never silently bucketed.
    """
    if principal_id in MEASUREMENT_PRINCIPAL_IDS:
        return MEASUREMENT
    if client_type in ORGANIC_CLIENT_TYPES:
        return ORGANIC
    if client_type in MEASUREMENT_CLIENT_TYPES:
        return MEASUREMENT
    if client_type == HARNESS_KERNEL:
        return ORGANIC if agent_run_linked else MEASUREMENT
    return UNKNOWN


def in_any_window(moment: datetime, windows: Sequence[tuple[datetime, datetime]]) -> bool:
    """True when ``moment`` falls inside any [start, end] window (inclusive)."""
    return any(start <= moment <= end for start, end in windows)


def _sorted(counter: Counter[str]) -> dict[str, int]:
    """Deterministic count dict: count desc, then key asc (stable output)."""
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _class_bucket() -> dict[str, Any]:
    return {"total": 0, "by_client_type": Counter(), "by_principal": Counter()}


def _route_run_bucket() -> dict[str, Any]:
    return {
        "total": 0,
        ORGANIC: _class_bucket(),
        MEASUREMENT: _class_bucket(),
        UNKNOWN: _class_bucket(),
    }


def summarize_route_runs(facts: Iterable[RouteRunFact]) -> dict[str, Any]:
    """One bucket over ALL given facts (no week split)."""
    bucket = _route_run_bucket()
    for fact in facts:
        bucket["total"] += 1
        cls = classify_route_run(fact.client_type, fact.agent_run_linked, fact.principal_id)
        cb = bucket[cls]
        cb["total"] += 1
        cb["by_client_type"][fact.client_type] += 1
        cb["by_principal"][fact.principal_id] += 1
    for cls in (ORGANIC, MEASUREMENT, UNKNOWN):
        bucket[cls]["by_client_type"] = _sorted(bucket[cls]["by_client_type"])
        bucket[cls]["by_principal"] = _sorted(bucket[cls]["by_principal"])
    return bucket


def aggregate_route_runs(facts: Sequence[RouteRunFact]) -> dict[str, dict[str, Any]]:
    """week → summarize_route_runs bucket over that week's facts."""
    by_week: dict[str, list[RouteRunFact]] = {}
    for fact in facts:
        by_week.setdefault(iso_week(fact.created_at), []).append(fact)
    return {week: summarize_route_runs(week_facts) for week, week_facts in by_week.items()}


def summarize_outcomes(
    bundles: Iterable[BundleFact], outcomes: Iterable[OutcomeFact]
) -> dict[str, Any]:
    """Outcomes-per-bundle bucket over ALL given facts (no week split)."""
    created = 0
    covered: set[str] = set()
    events = 0
    verdicts: Counter[str] = Counter()
    for _bundle in bundles:
        created += 1
    for outcome in outcomes:
        events += 1
        covered.add(outcome.bundle_id)
        for source, status in outcome.verdicts:
            verdicts[f"{source}/{status}"] += 1
    return {
        "bundles_created": created,
        "bundles_with_outcomes": len(covered),
        "outcome_events": events,
        "verdicts": _sorted(verdicts),
    }


def aggregate_outcomes(
    bundles: Sequence[BundleFact], outcomes: Sequence[OutcomeFact]
) -> dict[str, dict[str, Any]]:
    """week → summarize_outcomes bucket (bundles by created_at week, outcomes
    by received_at week — a bundle and its outcome may land in different
    weeks; each is counted where it happened)."""
    bundles_by_week: dict[str, list[BundleFact]] = {}
    for bundle in bundles:
        bundles_by_week.setdefault(iso_week(bundle.created_at), []).append(bundle)
    outcomes_by_week: dict[str, list[OutcomeFact]] = {}
    for outcome in outcomes:
        outcomes_by_week.setdefault(iso_week(outcome.received_at), []).append(outcome)
    weeks: dict[str, dict[str, Any]] = {}
    for week in set(bundles_by_week) | set(outcomes_by_week):
        weeks[week] = summarize_outcomes(
            bundles_by_week.get(week, ()), outcomes_by_week.get(week, ())
        )
    return weeks


def summarize_agent_runs(facts: Iterable[AgentRunFact]) -> dict[str, Any]:
    total = 0
    by_status: Counter[str] = Counter()
    for fact in facts:
        total += 1
        by_status[fact.status] += 1
    return {"total": total, "by_status": _sorted(by_status)}


def aggregate_agent_runs(facts: Sequence[AgentRunFact]) -> dict[str, dict[str, Any]]:
    by_week: dict[str, list[AgentRunFact]] = {}
    for fact in facts:
        by_week.setdefault(iso_week(fact.created_at), []).append(fact)
    return {week: summarize_agent_runs(week_facts) for week, week_facts in by_week.items()}


def build_report(data: ReportData, *, generated_at: datetime, database: str) -> dict[str, Any]:
    """The full E3 report (JSON-serializable): weeks + totals + limitations."""
    route_run_weeks = aggregate_route_runs(data.route_runs)
    outcome_weeks = aggregate_outcomes(data.bundles, data.outcomes)
    agent_run_weeks = aggregate_agent_runs(data.agent_runs)
    weeks = []
    for week in sorted(set(route_run_weeks) | set(outcome_weeks) | set(agent_run_weeks)):
        weeks.append(
            {
                "week": week,
                "route_runs": route_run_weeks.get(week, summarize_route_runs(())),
                "outcomes": outcome_weeks.get(week, summarize_outcomes((), ())),
                "agent_runs": agent_run_weeks.get(week, summarize_agent_runs(())),
            }
        )
    return {
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "database": database,
        "read_only": True,
        "weeks": weeks,
        "totals": {
            "route_runs": summarize_route_runs(data.route_runs),
            "outcomes": summarize_outcomes(data.bundles, data.outcomes),
            "agent_runs": summarize_agent_runs(data.agent_runs),
        },
        "limitations": list(LIMITATIONS),
    }


def _fmt_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key} {value}" for key, value in counts.items()) or "(none)"


def render_text(report: dict[str, Any]) -> str:
    """The human table (the --json flag prints the report dict instead)."""
    lines: list[str] = []
    lines.append(f"== ACI usage report (E3) — database: {report['database']} ==")
    lines.append(
        f"generated {report['generated_at']} · read-only connection "
        "(default_transaction_read_only=on)"
    )
    lines.append("")
    lines.append("-- route_runs per ISO week: ORGANIC (real clients) vs MEASUREMENT --")
    lines.append("week         total  organic  measurement  unknown")
    for week in report["weeks"]:
        rr = week["route_runs"]
        lines.append(
            f"{week['week']:<11}  {rr['total']:>5}  {rr[ORGANIC]['total']:>6}  "
            f"{rr[MEASUREMENT]['total']:>11}  {rr[UNKNOWN]['total']:>7}"
        )
        for cls in (ORGANIC, MEASUREMENT, UNKNOWN):
            cb = rr[cls]
            if cb["total"]:
                lines.append(
                    f"  {week['week']} {cls} by client_type: {_fmt_counts(cb['by_client_type'])}"
                )
                lines.append(
                    f"  {week['week']} {cls} by principal: {_fmt_counts(cb['by_principal'])}"
                )
    lines.append("")
    lines.append("-- outcomes per bundle, per ISO week --")
    lines.append("week        bundles  with outcomes  outcome events")
    for week in report["weeks"]:
        out = week["outcomes"]
        lines.append(
            f"{week['week']:<11} {out['bundles_created']:>7}  "
            f"{out['bundles_with_outcomes']:>13}  {out['outcome_events']:>13}"
        )
        if out["verdicts"]:
            lines.append(f"  {week['week']} verdicts: {_fmt_counts(out['verdicts'])}")
    lines.append("")
    lines.append("-- agent_runs per ISO week, by status --")
    lines.append("week         total  by status")
    for week in report["weeks"]:
        ar = week["agent_runs"]
        lines.append(f"{week['week']:<11}  {ar['total']:>5}  {_fmt_counts(ar['by_status'])}")
    lines.append("")
    lines.append("-- totals (all weeks) --")
    totals = report["totals"]
    lines.append(
        f"route_runs {totals['route_runs']['total']} "
        f"(organic {totals['route_runs'][ORGANIC]['total']}, "
        f"measurement {totals['route_runs'][MEASUREMENT]['total']}, "
        f"unknown {totals['route_runs'][UNKNOWN]['total']})"
    )
    lines.append(
        f"outcomes: {totals['outcomes']['outcome_events']} events over "
        f"{totals['outcomes']['bundles_with_outcomes']} of "
        f"{totals['outcomes']['bundles_created']} bundles"
    )
    lines.append(
        f"agent_runs {totals['agent_runs']['total']}: "
        f"{_fmt_counts(totals['agent_runs']['by_status'])}"
    )
    lines.append("")
    lines.append("-- limitations (read before quoting any number) --")
    for position, limitation in enumerate(report["limitations"], start=1):
        lines.append(f"  {position}. {limitation}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB layer — read-only queries only; every query here is a SELECT.
# ---------------------------------------------------------------------------


def open_read_only_engine(database_url: str) -> Engine:
    """Engine whose every connection opens read-only (server-side)."""
    return create_engine(database_url, connect_args={"options": READ_ONLY_OPTION})


def assert_read_only(conn: Connection) -> None:
    """Fail closed unless the server confirms the read-only transaction mode."""
    setting = conn.execute(text("SELECT current_setting('default_transaction_read_only')")).scalar()
    if setting != "on":
        raise RuntimeError(
            "refusing to report: default_transaction_read_only is "
            f"{setting!r} (expected 'on') — this script never writes"
        )


def fetch_report_data(conn: Connection, *, generated_at: datetime) -> ReportData:
    """Read the four telemetry surfaces and compute harness-kernel linkage."""
    linked_ids: set[str] = {
        row[0]
        for row in conn.execute(
            text(
                "SELECT DISTINCT payload->>'route_run_id' FROM agent_run_events "
                "WHERE payload->>'route_run_id' IS NOT NULL"
            )
        )
    }
    # A live run (finished_at NULL) extends to the report generation moment.
    windows = [
        (row[0], row[1] if row[1] is not None else generated_at)
        for row in conn.execute(text("SELECT created_at, finished_at FROM agent_runs"))
    ]
    route_runs = tuple(
        RouteRunFact(
            route_run_id=row[0],
            created_at=row[1],
            client_type=row[2],
            principal_id=row[3],
            agent_run_linked=(row[0] in linked_ids) or in_any_window(row[1], windows),
        )
        for row in conn.execute(
            text("SELECT route_run_id, created_at, client_type, principal_id FROM route_runs")
        )
    )
    bundles = tuple(
        BundleFact(bundle_id=row[0], created_at=row[1])
        for row in conn.execute(text("SELECT bundle_id, created_at FROM bundles"))
    )
    collected: dict[str, list[tuple[str, str]]] = {}
    for outcome_id, source, status in conn.execute(
        text("SELECT outcome_id, source, status FROM outcome_verdicts")
    ):
        collected.setdefault(outcome_id, []).append((source, status))
    outcomes = tuple(
        OutcomeFact(
            outcome_id=row[0],
            bundle_id=row[1],
            received_at=row[2],
            verdicts=tuple(collected.get(row[0], ())),
        )
        for row in conn.execute(
            text("SELECT outcome_id, bundle_id, received_at FROM outcome_events")
        )
    )
    agent_runs = tuple(
        AgentRunFact(run_id=row[0], created_at=row[1], status=row[2])
        for row in conn.execute(text("SELECT run_id, created_at, status FROM agent_runs"))
    )
    return ReportData(
        route_runs=route_runs, bundles=bundles, outcomes=outcomes, agent_runs=agent_runs
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ACI_DATABASE_URL", DEFAULT_DATABASE_URL),
        help="operational DB URL (default: ACI_DATABASE_URL, else aci_bench on localhost)",
    )
    args = parser.parse_args(argv)

    generated_at = datetime.now(UTC)
    engine = open_read_only_engine(args.database_url)
    try:
        with engine.connect() as conn:
            assert_read_only(conn)
            data = fetch_report_data(conn, generated_at=generated_at)
    finally:
        engine.dispose()
    report = build_report(
        data,
        generated_at=generated_at,
        database=make_url(args.database_url).database or "?",
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
