"""Canary monitor + auto-rollback (crawl.md §27, STEP 11).

The §27 loop: a canary release routes its percentage share; the monitor
compares its verified outcomes against the incumbent baseline; on
regression it flips the release to disabled (the rollback path — a
pointer move, artifacts stay for audit §30) and reports. On sustained
health it graduates the canary to active (full traffic).

The monitor NEVER deletes anything and NEVER touches versions — release
pointer moves only (ADR-012, §45 crawl.md: promotion must only change
release state).

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/canary_monitor.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

#: §27 rollback policy: a canary rolls back when its verified-failure
#: share exceeds the incumbent's by this margin over enough evidence.
MIN_CANARY_OUTCOMES = 3
REGRESSION_MARGIN = 0.2  # canary fail-rate > incumbent fail-rate + 20%


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default="postgresql+psycopg://aci:aci@localhost:5432/aci_bench"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = create_engine(args.database_url)
    actions = 0

    with engine.connect() as c:
        canaries = c.execute(
            text(
                "SELECT capability_id, version, canary_percent FROM capability_releases "
                "WHERE status = 'canary'"
            )
        ).fetchall()

        for cap, ver, percent in canaries:
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
            fails, succ = row[0], row[1]
            total = fails + succ

            # incumbent baseline: the previous active production version
            # of the same capability (if any)
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
            b_fails, b_succ = base[0], base[1]
            b_total = b_fails + b_succ

            canary_rate = fails / total if total else None
            base_rate = b_fails / b_total if b_total else None
            print(
                f"canary {cap}@{ver} ({percent}%): {fails}F/{succ}S"
                + (f" rate={canary_rate:.2f}" if canary_rate is not None else " no evidence")
            )
            if base_rate is not None:
                print(f"  incumbent baseline: {b_fails}F/{b_succ}S rate={base_rate:.2f}")

            # §27 rollback trigger
            if total >= MIN_CANARY_OUTCOMES and canary_rate is not None:
                if base_rate is not None and canary_rate > base_rate + REGRESSION_MARGIN:
                    print(
                        f"  -> ROLLBACK: canary regresses beyond margin "
                        f"({canary_rate:.2f} > {base_rate:.2f} + {REGRESSION_MARGIN})"
                    )
                    if not args.dry_run:
                        c.execute(
                            text(
                                "UPDATE capability_releases SET status = 'disabled', "
                                "approved_by = 'canary-monitor' "
                                "WHERE capability_id = :cap AND version = :ver "
                                "AND status = 'canary'"
                            ),
                            {"cap": cap, "ver": ver},
                        )
                    actions += 1
                elif canary_rate <= (base_rate or 0):
                    print(
                        "  -> HEALTHY: canary at or below baseline — "
                        "graduate to active on human confirm (capctl)"
                    )
        if not args.dry_run and actions:
            c.commit()

    print(f"\n{actions} rollback action(s)" + (" (dry-run)" if args.dry_run else ""))
    print("graduation stays human: capctl promotion approve — the monitor only rolls BACK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
