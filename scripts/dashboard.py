"""Telemetry + governance dashboard (V2, plan §55).

Read-only aggregates over the §41 tables — the CLI version of the
promotion/telemetry dashboards. No new infrastructure (§50): plain SQL
counts and means, printed as text. Everything here is observational;
nothing mutates.

Sections:
    corpus     capabilities, releases by channel/status, license + scan state
    routing    §36 route telemetry: runs, latency, bundle sizes, router versions
    outcomes   §33 evidence: events, verdicts by source/status/confidence
    benchmark  §41 runs by label with the latest aggregate recall

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/dashboard.py [--section NAME]
"""

import argparse
import sys
from typing import Any

from sqlalchemy import create_engine, text

from aci.config import Settings

SECTIONS = ("corpus", "routing", "outcomes", "benchmark")


def _rows(conn: Any, sql: str) -> list[tuple[Any, ...]]:
    return list(conn.execute(text(sql)))


def _one(conn: Any, sql: str) -> Any:
    return conn.execute(text(sql)).scalar()


def section_corpus(conn: Any) -> None:
    print("== corpus ==")
    total = _one(conn, "SELECT count(*) FROM capabilities")
    print(f"capabilities: {total}")
    for kind, count in _rows(
        conn, "SELECT kind, count(*) FROM capabilities GROUP BY kind ORDER BY kind"
    ):
        print(f"  {kind}: {count}")
    print("releases:")
    for channel, status, count in _rows(
        conn,
        "SELECT channel, status, count(*) FROM capability_releases "
        "GROUP BY channel, status ORDER BY channel, status",
    ):
        print(f"  {channel}/{status}: {count}")
    print("licenses (latest assessment per capability+version):")
    for spdx, count in _rows(
        conn,
        """
        SELECT license_identifier, count(*) FROM (
            SELECT DISTINCT ON (capability_id, version)
                capability_id, version, license_identifier
            FROM license_assessments
            ORDER BY capability_id, version, assessed_at DESC
        ) latest GROUP BY license_identifier ORDER BY count(*) DESC
        """,
    ):
        print(f"  {spdx}: {count}")
    scans = _one(
        conn,
        """
        SELECT count(*) FROM (
            SELECT DISTINCT ON (capability_id, version) scan_status
            FROM security_assessments
            ORDER BY capability_id, version, scanned_at DESC
        ) latest WHERE scan_status = 'passed'
        """,
    )
    findings = _one(
        conn,
        """
        SELECT count(*) FROM (
            SELECT DISTINCT ON (capability_id, version) findings
            FROM security_assessments
            ORDER BY capability_id, version, scanned_at DESC
        ) latest CROSS JOIN LATERAL jsonb_array_elements(findings) AS f
        """,
    )
    print(f"security: {scans} passed (latest per version), {findings} finding(s) recorded")


def section_routing(conn: Any) -> None:
    print("== routing (§36) ==")
    runs = _one(conn, "SELECT count(*) FROM route_runs")
    if not runs:
        print("no route runs recorded yet.")
        return
    mean_latency = _one(
        conn, "SELECT round(avg(latency_ms)) FROM route_runs WHERE latency_ms IS NOT NULL"
    )
    mean_eligible = _one(
        conn, "SELECT round(avg(eligible_count)) FROM route_runs WHERE error_code IS NULL"
    )
    print(f"route runs: {runs} (mean latency {mean_latency} ms, mean eligible {mean_eligible})")
    print("by principal:")
    for principal, count in _rows(
        conn,
        "SELECT principal_id, count(*) FROM route_runs GROUP BY principal_id "
        "ORDER BY count(*) DESC LIMIT 10",
    ):
        print(f"  {principal}: {count}")
    print("router versions in use:")
    for reranker, composer, count in _rows(
        conn,
        "SELECT reranker_implementation || '/' || reranker_version, "
        "composer_implementation || '/' || composer_version, count(*) "
        "FROM route_runs GROUP BY 1, 2 ORDER BY count(*) DESC LIMIT 5",
    ):
        print(f"  rerank {reranker} + compose {composer}: {count}")
    bundles = _one(conn, "SELECT count(*) FROM bundles")
    empty = _one(
        conn,
        "SELECT count(*) FROM bundles b WHERE NOT EXISTS "
        "(SELECT 1 FROM bundle_items i WHERE i.bundle_id = b.bundle_id)",
    )
    mean_items = _one(
        conn,
        "SELECT round(avg(n)) FROM (SELECT bundle_id, count(*) n FROM bundle_items "
        "GROUP BY bundle_id) s",
    )
    print(
        f"bundles: {bundles} ({empty} empty — a valid success per ADR-008; mean {mean_items} items)"
    )


def section_outcomes(conn: Any) -> None:
    print("== outcomes (§33) ==")
    events = _one(conn, "SELECT count(*) FROM outcome_events")
    if not events:
        print("no outcome events recorded yet.")
        return
    print(f"events: {events}")
    print("verdicts by source/status:")
    for source, status, count in _rows(
        conn,
        "SELECT source, status, count(*) FROM outcome_verdicts "
        "GROUP BY source, status ORDER BY count(*) DESC",
    ):
        print(f"  {source}/{status}: {count}")
    print("confidence:")
    for confidence, count in _rows(
        conn,
        "SELECT confidence, count(*) FROM outcome_verdicts "
        "GROUP BY confidence ORDER BY count(*) DESC",
    ):
        print(f"  {confidence}: {count}")


def section_benchmark(conn: Any) -> None:
    print("== benchmark (§41) ==")
    runs = _one(conn, "SELECT count(*) FROM benchmark_runs")
    if not runs:
        print("no benchmark runs recorded yet.")
        return
    print(f"runs: {runs}")
    for label, count, created in _rows(
        conn,
        "SELECT label, count(*), max(created_at)::date FROM benchmark_runs "
        "GROUP BY label ORDER BY max(created_at) DESC LIMIT 10",
    ):
        print(f"  {label}: {count} run(s), last {created}")
    print("latest run per label (mean recall over non-null results):")
    for label, recall in _rows(
        conn,
        """
        SELECT label, round(avg((metrics->>'recall')::numeric), 3) FROM (
            SELECT DISTINCT ON (r.label, res.case_id)
                r.label, res.case_id, res.variant, res.metrics
            FROM benchmark_runs r
            JOIN benchmark_results res ON res.run_id = r.run_id
            WHERE res.variant = 'full_pipeline'
            ORDER BY r.label, res.case_id, r.created_at DESC
        ) latest GROUP BY label ORDER BY label
        """,
    ):
        print(f"  {label}: full_pipeline mean recall {recall}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", choices=SECTIONS, help="show only one section (default: all)")
    args = parser.parse_args()

    engine = create_engine(Settings().database_url)
    with engine.connect() as conn:
        for name in [args.section] if args.section else SECTIONS:
            if name == "corpus":
                section_corpus(conn)
            elif name == "routing":
                section_routing(conn)
            elif name == "outcomes":
                section_outcomes(conn)
            else:
                section_benchmark(conn)
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
