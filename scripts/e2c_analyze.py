"""E2C analysis — Fisher exact two-sided + per-fixture/directness splits.

Analysis FROM THE JSON ROWS ONLY (never the runner aggregate). The Fisher
implementation is verified against the E2/E2B published values BEFORE any
E2C number is read (the pre-registration's requirement):

    E2  (hbench-20261002-003148): pooled K 0/20 vs R 17/20 -> 2.57e-08
         per-case atlas 0/10 vs 8/10 -> 0.000714
         per-case meridian 0/10 vs 9/10 -> 0.000119
    E2B (hbench-20261002-034726 + clean KR): F 9/20 vs R 17/20 -> 0.0187
         Bp 19/20 vs F 9/20 -> 0.00125
         Bq 9/20 vs clean K 0/20 -> 0.00123

Usage:
    .venv/bin/python scripts/e2c_analyze.py <round.json> [<topup.json> ...]
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Fisher's exact test, two-sided, for the 2x2 table [[a, b], [c, d]].

    Sum of the hypergeometric probabilities of all tables with the same
    margins that are no more probable than the observed one (the standard
    "point probability" method, what scipy's fisher_exact uses).
    """
    row1, row2 = a + b, c + d
    col1, col2 = a + c, b + d
    n = row1 + row2

    def log_p(x: int) -> float:  # P(table with cell[0][0] = x)
        return (
            math.lgamma(row1 + 1)
            + math.lgamma(row2 + 1)
            + math.lgamma(col1 + 1)
            + math.lgamma(col2 + 1)
            - math.lgamma(n + 1)
            - math.lgamma(x + 1)
            - math.lgamma(row1 - x + 1)
            - math.lgamma(col1 - x + 1)
            - math.lgamma(col2 - row1 + x + 1)
        )

    lo = max(0, row1 - col2, col1 - row2)
    hi = min(row1, col1)
    p_obs = log_p(a)
    total = 0.0
    for x in range(lo, hi + 1):
        lp = log_p(x)
        if lp <= p_obs + 1e-7:
            total += math.exp(lp)
    return min(1.0, total)


def passes_fails(rows: list[dict]) -> tuple[int, int]:
    """(passes, fails) over the VALID rows (MODEL_FAILURE/crashed excluded).

    A crashed row carries ``status='crashed'`` with ``stop_reason`` set to
    the exception TYPE NAME (the runner records ``type(exc).__name__``), so
    BOTH signals are checked (2026-10-02, e2d-turns: no E2C round ever had
    a crashed row — verified over all 12 e2c-*.json — so no published
    number changes; this is hardening)."""
    valid = [
        r
        for r in rows
        if r.get("stop_reason") not in ("MODEL_FAILURE", "crashed") and r.get("status") != "crashed"
    ]
    p = sum(1 for r in valid if r.get("tests_pass_at_end"))
    return p, len(valid) - p


def self_test() -> None:
    """Verify against the E2/E2B published values (the pre-registration)."""
    checks = [
        ("E2 pooled K vs R", (0, 20, 17, 3), 2.57e-08),
        ("E2 atlas K vs R", (0, 10, 8, 2), 0.000714),
        ("E2 meridian K vs R", (0, 10, 9, 1), 0.000119),
        ("E2B F vs R", (9, 11, 17, 3), 0.0187),
        ("E2B Bp vs F", (19, 1, 9, 11), 0.00125),
        ("E2B Bq vs clean K", (9, 11, 0, 20), 0.00123),
    ]
    ok = True
    for label, table, expected in checks:
        got = fisher_exact_two_sided(*table)
        match = abs(got - expected) <= 2e-3 * max(expected, 1e-10) or abs(got - expected) < 1e-9
        print(f"  {label:24s} p={got:.6g} (published {expected:g}) {'OK' if match else 'MISMATCH'}")
        ok = ok and match
    if not ok:
        raise SystemExit("Fisher self-test FAILED — do not use these numbers")


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(__doc__)
    print("Fisher self-test against the E2/E2B published values:")
    self_test()
    rows: list[dict] = []
    for path in argv:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        rows += report["results"]
    # Deduplicate: a top-up round may re-run a (fixture, arm) cell already at
    # n=8; keep the FIRST 8 valid rows per cell (the runner's repeat labels
    # restart at 1 per invocation, so dedupe on (fixture, arm) FIFO).
    cells: dict[tuple, list[dict]] = defaultdict(list)
    invalid: dict[tuple, int] = defaultdict(int)
    for r in rows:
        key = (r["fixture"], r["arm"])
        if r.get("stop_reason") in ("MODEL_FAILURE", "crashed"):
            invalid[key] += 1
            continue
        if len(cells[key]) < 8:
            cells[key].append(r)
    fixtures = sorted({fx for fx, _ in cells})
    arms = sorted({arm for _, arm in cells})
    print(f"\nrows loaded: {len(rows)}; MODEL_FAILURE/crashed excluded: {sum(invalid.values())}")

    print("\n=== tests_pass_at_end per cell (valid rows, capped n=8) ===")
    table: dict[tuple, tuple[int, int]] = {}
    for fx in fixtures:
        for arm in arms:
            p, f = passes_fails(cells.get((fx, arm), []))
            table[(fx, arm)] = (p, f)
            n = p + f
            print(f"  {fx:32s} {arm:3s} {p}/{n}")
    print("\n=== pooled per arm ===")
    pooled: dict[str, tuple[int, int]] = {}
    for arm in arms:
        p = sum(table[(fx, arm)][0] for fx in fixtures)
        n = sum(table[(fx, arm)][0] + table[(fx, arm)][1] for fx in fixtures)
        pooled[arm] = (p, n - p)
        print(f"  {arm:3s} {p}/{n}")
    if "F" in pooled and "Bp" in pooled:
        fp, ff = pooled["F"]
        bp, bf = pooled["Bp"]
        p = fisher_exact_two_sided(bp, bf, fp, ff)
        print(f"\nPRIMARY: pooled Bp {bp}/{bp + bf} vs F {fp}/{fp + ff} -> Fisher p={p:.6g}")
        verdict = (
            "REPLICATES E2B (Bp > F, p < 0.05)" if (bp > fp and p < 0.05) else "DOES NOT replicate"
        )
        print(f"  -> {verdict}")
    print("\n=== per-fixture Fisher (Bp vs F) ===")
    for fx in fixtures:
        if (fx, "Bp") in table and (fx, "F") in table:
            bp, bf = table[(fx, "Bp")]
            fp, ff = table[(fx, "F")]
            p = fisher_exact_two_sided(bp, bf, fp, ff)
            print(f"  {fx:32s} Bp {bp}/{bp + bf} vs F {fp}/{fp + ff} -> p={p:.6g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
