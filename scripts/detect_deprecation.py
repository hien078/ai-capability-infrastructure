"""Production Health Monitor + Deprecation Detector (auto.md §28-29, proposal-only).

Reads REAL outcome telemetry (§33 multi-source verdicts joined to bundles)
and answers the health questions of §28:

    verified failure increases?
    irrelevant selection increases?
    human override high?
    zero effect (selected but no verified benefit)?

Rules (V1 automation, no ML — §28 health rules verbatim):

  H1 verified-failure rate — bundles carrying a skill whose test_harness /
     static_analysis verdicts are failure more often than success.
  H2 zero-evidence — skills selected many times with NO outcome evidence
     attached at all (nobody could tell if they ever helped).
  H3 human-correction — bundles where human_corrected flipped true.

Output: a proposal report — a HUMAN decides (§29: no bot-delete; §30:
deprecate ≠ delete, artifacts stay for audit/rollback). This script never
revokes, never writes lifecycle state (ADR-012).

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/detect_deprecation.py \
        [--min-uses N]
"""

import argparse
import sys

from sqlalchemy import create_engine, text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default="postgresql+psycopg://aci:aci@localhost:5432/aci_bench"
    )
    parser.add_argument(
        "--min-uses", type=int, default=3, help="min selections before a skill is assessed"
    )
    args = parser.parse_args()

    engine = create_engine(args.database_url)
    with engine.connect() as c:
        # Per-skill usage + verdict joins across bundles it appeared in.
        rows = c.execute(
            text(
                """
                SELECT bi.capability_id,
                       count(DISTINCT b.bundle_id) AS selections,
                       count(DISTINCT oe.outcome_id) FILTER (
                           WHERE oe.outcome_id IS NOT NULL) AS with_evidence,
                       count(DISTINCT (v.outcome_id, v.position)) FILTER (
                           WHERE v.source IN ('test_harness','static_analysis')
                             AND v.status = 'failure') AS verified_failures,
                       count(DISTINCT (v.outcome_id, v.position)) FILTER (
                           WHERE v.source IN ('test_harness','static_analysis')
                             AND v.status = 'success') AS verified_successes,
                       count(DISTINCT oe.outcome_id) FILTER (
                           WHERE oe.human_corrected = true) AS human_corrections
                FROM bundle_items bi
                JOIN bundles b ON b.bundle_id = bi.bundle_id
                LEFT JOIN outcome_events oe ON oe.bundle_id = b.bundle_id
                LEFT JOIN outcome_verdicts v ON v.outcome_id = oe.outcome_id
                GROUP BY bi.capability_id
                ORDER BY selections DESC
                """
            )
        ).fetchall()

    print("== PRODUCTION HEALTH MONITOR (proposal-only, auto.md §28-29) ==")
    print(f"skills assessed: {len(rows)} (min {args.min_uses} selections to judge)\n")

    proposals: list[str] = []
    print("-- per-skill health (selections / evidence / verified S-F / human-corrected)")
    for (
        cap,
        selections,
        with_ev,
        v_fail,
        v_succ,
        human_corr,
    ) in rows:
        flag = ""
        if selections >= args.min_uses:
            if v_fail and v_fail >= max(1, v_succ or 0):
                flag = " ← H1 verified failures >= successes"
                proposals.append(
                    f"DEPRECATE-REVIEW {cap}: {v_fail} verified failure(s) vs "
                    f"{v_succ or 0} success(es) across {selections} selections — "
                    "the skill is selected but does not help (§28)."
                )
            if with_ev == 0 and selections >= args.min_uses * 2:
                flag += " ← H2 zero evidence"
                proposals.append(
                    f"EVIDENCE-GAP {cap}: selected {selections} times, never any "
                    "outcome evidence attached — nobody can tell if it helps "
                    "(§28 zero effect). Route real usage through /v1/outcomes."
                )
            if human_corr and human_corr >= 1:
                flag += f" ← H3 human corrected ×{human_corr}"
        print(
            f"   {cap:34s} {selections:3d} / {with_ev:3d} / "
            f"{v_succ or 0:2d}S-{v_fail or 0:2d}F / {human_corr or 0:2d}{flag}"
        )

    print("\n== PROPOSAL ==")
    if not proposals:
        print(
            "no deprecation proposal — no skill shows verified-failure dominance "
            "or zero-evidence overuse right now (§29: proposals need evidence)."
        )
    for i, p in enumerate(proposals, 1):
        print(f"{i}. {p}")
    print(
        "\n(human decides: deprecate ≠ delete — artifacts stay for audit/rollback "
        "§30; this detector never revokes or writes lifecycle state, ADR-012)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
