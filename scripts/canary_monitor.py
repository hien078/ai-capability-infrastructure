"""Canary monitor + auto-rollback (crawl.md §27, STEP 11).

The §27 loop: a canary release routes its percentage share; the monitor
compares its verified outcomes against the incumbent baseline; on
regression it flips the release to disabled (the rollback path — a
pointer move, artifacts stay for audit §30) and reports. On sustained
health it graduates the canary to active (full traffic).

The monitor NEVER deletes anything and NEVER touches versions — release
pointer moves only (ADR-012, §45 crawl.md: promotion must only change
release state). It also never overwrites `approved_by`: the human who
promoted stays on record; the rollback itself is the audit event.

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/canary_monitor.py [--dry-run]
"""

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

#: §27 rollback policy: a canary rolls back when its verified-failure
#: share exceeds the incumbent's by this margin over enough evidence.
MIN_CANARY_OUTCOMES = 3
REGRESSION_MARGIN = 0.2  # canary fail-rate > incumbent fail-rate + 20%
#: §27 "acceptable failure rate": with NO incumbent baseline to compare
#: against, a canary failing more than this share rolls back on its own
#: absolute evidence — never silently healthy on 3/3 failures.
MAX_ACCEPTABLE_FAIL_RATE = 0.5


def rollback_decision(fails: int, succ: int, base_fails: int = 0, base_succ: int = 0) -> str:
    """§27 decision for one canary from its verified outcome counts.

    Returns ``"rollback"`` | ``"healthy"`` | ``"insufficient"`` (not
    enough canary evidence yet). With an incumbent baseline the check
    is relative (fail-rate > base + margin); without one it is the
    absolute acceptable-failure-rate check.
    """
    total = fails + succ
    if total < MIN_CANARY_OUTCOMES:
        return "insufficient"
    canary_rate = fails / total
    base_total = base_fails + base_succ
    if base_total == 0:
        return "rollback" if canary_rate > MAX_ACCEPTABLE_FAIL_RATE else "healthy"
    base_rate = base_fails / base_total
    if canary_rate > base_rate + REGRESSION_MARGIN:
        return "rollback"
    return "healthy"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "ACI_DATABASE_URL", "postgresql+psycopg://aci:aci@localhost:5432/aci"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    engine = create_engine(args.database_url)
    actions = 0

    with engine.connect() as c:
        # PK is (capability_id, channel): one row per channel — the
        # rollback below keys on the SAME triple, never a bare
        # (capability_id, version) that would hit every channel.
        canaries = c.execute(
            text(
                "SELECT capability_id, channel, version, canary_percent "
                "FROM capability_releases WHERE status = 'canary'"
            )
        ).fetchall()

        for cap, channel, ver, percent in canaries:
            # canary outcomes: bundles containing this capability whose
            # events carry test_harness verdicts
            row = c.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (WHERE v.status = 'failure') AS fails,
                      count(*) FILTER (WHERE v.status = 'success') AS succ
                    FROM bundle_items bi
                    JOIN outcome_events oe ON oe.bundle_id = bi.bundle_id
                    JOIN outcome_verdicts v ON v.outcome_id = oe.outcome_id
                    WHERE bi.capability_id = :cap AND bi.version = :ver
                      AND v.source IN ('test_harness', 'static_analysis')
                    """
                ),
                {"cap": cap, "ver": ver},
            ).fetchone()
            fails, succ = (row[0], row[1]) if row is not None else (0, 0)

            # incumbent baseline (§27): the previous stable version's
            # telemetry. The release table holds ONE row per
            # (capability, channel) — the canary pointer REPLACED the
            # incumbent's — so the baseline is the pooled verified
            # outcomes of every OTHER version of the capability (the
            # canary itself is excluded). A version with zero evidence
            # contributes nothing, so the margin can never pass
            # vacuously.
            b_fails = b_succ = 0
            base = c.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (WHERE v.status = 'failure') AS fails,
                      count(*) FILTER (WHERE v.status = 'success') AS succ
                    FROM bundle_items bi
                    JOIN outcome_events oe ON oe.bundle_id = bi.bundle_id
                    JOIN outcome_verdicts v ON v.outcome_id = oe.outcome_id
                    WHERE bi.capability_id = :cap AND bi.version != :ver
                      AND v.source IN ('test_harness', 'static_analysis')
                    """
                ),
                {"cap": cap, "ver": ver},
            ).fetchone()
            b_fails, b_succ = (base[0], base[1]) if base is not None else (0, 0)

            decision = rollback_decision(fails, succ, b_fails, b_succ)
            total = fails + succ
            print(
                f"canary {cap}@{ver} [{channel}] ({percent}%): {fails}F/{succ}S"
                + (f" rate={fails / total:.2f}" if total else " no evidence")
            )
            if b_fails + b_succ:
                base_rate = b_fails / (b_fails + b_succ)
                print(f"  incumbent baseline: {b_fails}F/{b_succ}S rate={base_rate:.2f}")
            else:
                print("  no incumbent baseline — absolute acceptable-failure-rate check applies")

            if decision == "rollback":
                print(f"  -> ROLLBACK: decision={decision}")
                if not args.dry_run:
                    c.execute(
                        text(
                            "UPDATE capability_releases SET status = 'disabled' "
                            "WHERE capability_id = :cap AND channel = :channel "
                            "AND version = :ver AND status = 'canary'"
                        ),
                        {"cap": cap, "channel": channel, "ver": ver},
                    )
                actions += 1
            elif decision == "healthy":
                print(
                    "  -> HEALTHY: canary at or below baseline — "
                    "graduate to active on human confirm (capctl)"
                )
            else:
                print(f"  -> INSUFFICIENT: fewer than {MIN_CANARY_OUTCOMES} verified outcomes")
        if not args.dry_run and actions:
            c.commit()

    print(f"\n{actions} rollback action(s)" + (" (dry-run)" if args.dry_run else ""))
    print("graduation stays human: capctl promotion approve — the monitor only rolls BACK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
