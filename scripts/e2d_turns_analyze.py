"""E2D-TURNS analysis — is the 12-turn budget the bottleneck once the skill is delivered?

Pre-registration: ``data/hbench/E2D-TURNS-PREREGISTERED.md`` (written BEFORE
any model run). Analysis FROM THE JSON ROWS ONLY — never the runner's
aggregate. The Fisher implementation is ``e2c_analyze``'s (verified against
the E2/E2B published values; the self-test runs again before any E2D number
is read).

Round (this job): ``--set private2 --arms K,R,Bp --max-turns 20``,
n = K 2, R 6, Bp 6 per fixture. Baseline (cross-round, same
model/gateway/fixtures/sandbox): the E2C 12-turn rows — R validity
n=3/fixture (pooled 10/20), Bp main n=8 on the 3 valid fixtures (17/24),
K validity 0/13.

Usage:
    .venv/bin/python scripts/e2d_turns_analyze.py <e2d-round.json> [<topup.json> ...] \
        [--baseline-dir ~/Data/Projects/ACI/data/aci-improvement/mac]
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2c_analyze import fisher_exact_two_sided, self_test  # noqa: E402

#: Pre-registered per-cell caps (valid rows kept FIFO per (fixture, arm)).
E2D_CAPS = {"K": 2, "R": 6, "Bp": 6}
#: The E2C 12-turn baseline caps (valid rows kept FIFO per (fixture, arm)).
BASELINE_CAPS = {"R": 3, "Bp": 8, "K": 2}
#: The 3 fixtures that passed E2C's reach-in-budget gate at 12 turns.
E2C_VALID = (
    "private2-drawbridge-rollout",
    "private2-palisade-redaction",
    "private2-vellum-order-id",
)
#: The 4 fixtures E2C excluded at 12 turns (the recovery question).
E2C_EXCLUDED = (
    "private2-cairn-money",
    "private2-cairn-sunset",
    "private2-queue-consumer-retry",
    "private2-infra-config",
)


def valid(row: dict) -> bool:
    """A VALID row: not a gateway-5xx MODEL_FAILURE, not a crashed run.

    A crashed row carries ``status='crashed'`` with ``stop_reason`` set to
    the exception TYPE NAME (the runner records ``type(exc).__name__``), so
    a stop_reason-only check would miss it — both signals are checked."""
    return (
        row.get("stop_reason") not in ("MODEL_FAILURE", "crashed")
        and row.get("status") != "crashed"
        and not row.get("invalid_reason")  # e.g. REGISTRY_UNAVAILABLE (Bp/Bq)
    )


def load_cells(paths: list[Path], caps: dict[str, int]) -> dict[tuple[str, str], list[dict]]:
    """(fixture, arm) -> valid rows, FIFO-capped per the pre-registered n."""
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        for row in report["results"]:
            key = (str(row["fixture"]), str(row["arm"]))
            cap = caps.get(key[1], 0)
            if not valid(row):
                continue
            if len(cells[key]) < cap:
                cells[key].append(row)
    return cells


def pooled(cells: dict, arm: str, fixtures: list[str]) -> tuple[int, int]:
    """(passes, fails) pooled over the given fixtures for one arm."""
    p = f = 0
    for fx in fixtures:
        for row in cells.get((fx, arm), []):
            p, f = (p + 1, f) if row.get("tests_pass_at_end") else (p, f + 1)
    return p, f


def rate(cells: dict, arm: str, fixtures: list[str], field: str) -> tuple[float, int]:
    """(mean value, n) of a numeric row field over valid rows."""
    values = [float(row.get(field, 0)) for fx in fixtures for row in cells.get((fx, arm), [])]
    return (mean(values) if values else 0.0), len(values)


def limit_rate(cells: dict, arm: str, fixtures: list[str]) -> tuple[int, int]:
    """(LIMIT_TURNS rows, valid rows) — the budget-exhaustion rate."""
    n = sum(len(cells.get((fx, arm), [])) for fx in fixtures)
    hits = sum(
        1
        for fx in fixtures
        for row in cells.get((fx, arm), [])
        if row.get("stop_reason") == "LIMIT_TURNS"
    )
    return hits, n


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rounds", nargs="+", help="E2D round JSONs (K/R/Bp at 20 turns)")
    parser.add_argument(
        "--baseline-dir",
        default=str(Path.home() / "Data/Projects/ACI/data/aci-improvement/mac"),
        help="dir with the ARCHIVED glm-5.3 E2C 12-turn baseline JSONs "
        "(e2c-validity-R*/e2c-main*/e2c-validity-K*) — reference only, never pooled",
    )
    parser.add_argument(
        "--r12-dsv4",
        default="",
        help="comma-separated dsv4 R@12 round JSONs (THIS job's dsv4 baseline, "
        "--arms R --max-turns 12) — the PRIMARY R@20-vs-R@12 comparator. Without "
        "it the PRIMARY uses the archived glm E2C baseline (not valid post-switch)",
    )
    args = parser.parse_args(argv)

    print("Fisher self-test against the E2/E2B published values:")
    self_test()

    base = Path(args.baseline_dir)
    r12_paths = sorted(base.glob("e2c-validity-R*.json"))
    bp12_paths = sorted(base.glob("e2c-main*.json"))
    k12_paths = sorted(base.glob("e2c-validity-K*.json"))
    if not (r12_paths and bp12_paths and k12_paths):
        raise SystemExit(f"baseline JSONs missing under {base}")
    e2d = load_cells([Path(p) for p in args.rounds], E2D_CAPS)
    #: The dsv4 R@12 baseline (THIS job) — the PRIMARY comparator after the
    #: model switch, capped at n=6/fixture (its own round's --repeat 6, not the
    #: archived E2C n=3 cap). The archived glm E2C rows are reference only.
    r12_dsv4 = load_cells([Path(p) for p in args.r12_dsv4.split(",") if p], {"R": 6})
    r12_use_dsv4 = bool(args.r12_dsv4 and r12_dsv4)
    r12 = r12_dsv4 if r12_use_dsv4 else load_cells(r12_paths, BASELINE_CAPS)
    baseline_kind = "dsv4 (this job)" if r12_use_dsv4 else "glm-5.3 E2C ARCHIVED"
    bp12 = load_cells(bp12_paths, BASELINE_CAPS)
    k12 = load_cells(k12_paths, BASELINE_CAPS)
    fixtures = sorted({fx for fx, _ in e2d} | {fx for fx, _ in r12})
    print(f"\nE2D cells: {sorted(e2d)} (caps {E2D_CAPS})")
    print(f"PRIMARY baseline R@12: {baseline_kind}")
    print(f"baseline R@12 files: {[p.name for p in r12_paths if not r12_dsv4] or args.r12_dsv4}")
    print(f"baseline Bp@12 files: {[p.name for p in bp12_paths]}")
    print(f"baseline K@12 files: {[p.name for p in k12_paths]}")

    # ---- the knowledge gate at 20 turns (pre-registered: must stay 0) ----
    print("\n=== K@20 knowledge gate (pre-registered: 0 passes required) ===")
    k20_p, k20_f = pooled(e2d, "K", fixtures)
    for fx in fixtures:
        rows = e2d.get((fx, "K"), [])
        p = sum(1 for r in rows if r.get("tests_pass_at_end"))
        print(f"  {fx:35s} K {p}/{len(rows)}")
    print(f"  pooled K@20: {k20_p}/{k20_p + k20_f}")
    if k20_p:
        print("  !! LEAK — a naked run passed at 20 turns: inspect and report (pre-registered)")

    # ---- PRIMARY: pooled R@20 vs R@12 (Fisher exact two-sided) ----
    print("\n=== PRIMARY: pooled R tests_pass_at_end, 20 turns vs 12 turns ===")
    r20_p, r20_f = pooled(e2d, "R", fixtures)
    r12_p, r12_f = pooled(r12, "R", fixtures)
    p_primary = fisher_exact_two_sided(r20_p, r20_f, r12_p, r12_f)
    r20_rate = r20_p / max(1, r20_p + r20_f)
    r12_rate = r12_p / max(1, r12_p + r12_f)
    print(
        f"  R@20 {r20_p}/{r20_p + r20_f} ({r20_rate:.2f})  "
        f"vs R@12 {r12_p}/{r12_p + r12_f} ({r12_rate:.2f})"
    )
    print(f"  Fisher two-sided p={p_primary:.6g}")
    verdict = (
        "BUDGET IS A BOTTLENECK (R@20 > R@12, p < 0.05)"
        if r20_rate > r12_rate and p_primary < 0.05
        else "NOT shown at p < 0.05"
    )
    print(f"  -> {verdict}")

    # ---- SECONDARY: per-fixture table ----
    print("\n=== per-fixture (valid rows): R@20 | R@12 | Bp@20 | Bp@12* ===")
    print("  (* Bp@12 = ARCHIVED glm-5.3 E2C rows — reference only, never pooled on dsv4)")
    for fx in fixtures:
        cells = {
            "R@20": (e2d, "R"),
            "R@12": (r12, "R"),
            "Bp@20": (e2d, "Bp"),
            "Bp@12": (bp12, "Bp"),
        }
        parts = []
        for label, (table, arm) in cells.items():
            rows = table.get((fx, arm), [])
            p = sum(1 for r in rows if r.get("tests_pass_at_end"))
            parts.append(f"{label} {p}/{len(rows)}" if rows else f"{label} -")
        print(f"  {fx:35s} " + " | ".join(parts))

    # ---- SECONDARY: the 4 E2C-excluded fixtures at 20 turns ----
    print("\n=== the 4 E2C-excluded fixtures at 20 turns (>= 4/6 = 2/3-equivalent) ===")
    recovered = 0
    for fx in E2C_EXCLUDED:
        rows = e2d.get((fx, "R"), [])
        p = sum(1 for r in rows if r.get("tests_pass_at_end"))
        ok = p >= 4 and len(rows) >= 6
        recovered += ok
        print(f"  {fx:35s} R {p}/{len(rows)} {'>= 4/6 RECOVERED' if ok else ''}")
    print(f"  -> {recovered}/4 excluded fixtures reach >= 4/6 at 20 turns")

    # ---- SECONDARY: Bp@20 vs R@20 (does the router preserve the ceiling?) ----
    print("\n=== Bp@20 vs R@20 pooled (router preserves the ceiling? expect ~) ===")
    bp20_p, bp20_f = pooled(e2d, "Bp", fixtures)
    p_value = fisher_exact_two_sided(bp20_p, bp20_f, r20_p, r20_f)
    print(
        f"  Bp@20 {bp20_p}/{bp20_p + bp20_f} ({bp20_p / max(1, bp20_p + bp20_f):.2f})  "
        f"vs R@20 {r20_p}/{r20_p + r20_f} ({r20_rate:.2f})  Fisher p={p_value:.6g}"
    )

    # ---- SECONDARY: Bp@12-vs-Bp@20 is DROPPED (model switch) ----
    # The glm-5.3 E2C Bp@12 is ARCHIVED (never pooled on dsv4). Bp@20 is
    # measured on dsv4; the cross-model Bp@12 comparison is not re-run on
    # dsv4 (per the pre-registration amendment). bp12 rows print below only
    # as archived reference.
    print("\n=== Bp@20 (dsv4) — Bp@12-vs-Bp@20 cross-round DROPPED (glm-5.3 archived) ===")
    bp20_p, bp20_f = pooled(e2d, "Bp", list(E2C_VALID))
    print(
        f"  Bp@20 on the 3 E2C-valid fixtures {bp20_p}/{bp20_p + bp20_f} "
        f"({bp20_p / max(1, bp20_p + bp20_f):.2f})  "
        f"(glm-5.3 E2C Bp@12 was 17/24 — archived, NOT a dsv4 comparator)"
    )

    # ---- SECONDARY: budget mechanics at 20 vs 12 ----
    print("\n=== budget mechanics (valid rows): turns / LIMIT_TURNS / acceptance / tokens ===")
    for label, table, arm, fxs in (
        ("R@20", e2d, "R", fixtures),
        ("R@12", r12, "R", fixtures),
        ("Bp@20", e2d, "Bp", fixtures),
        ("Bp@12", bp12, "Bp", list(E2C_VALID)),
        ("K@20", e2d, "K", fixtures),
        ("K@12", k12, "K", fixtures),
    ):
        rows = [r for fx in fxs for r in table.get((fx, arm), [])]
        if not rows:
            print(f"  {label:6s} (no rows)")
            continue
        turns = [float(r["turns"]) for r in rows]
        t_sd = stdev(turns) if len(turns) > 1 else 0.0
        lim, n = limit_rate(table, arm, fxs)
        acc = sum(1 for r in rows if r.get("accepted"))
        fs = sum(1 for r in rows if r.get("false_success"))
        tin, _ = rate(table, arm, fxs, "tokens_in")
        tout, _ = rate(table, arm, fxs, "tokens_out")
        print(
            f"  {label:6s} n={n} turns {mean(turns):.1f}±{t_sd:.1f} "
            f"LIMIT_TURNS {lim}/{n} ({lim / n:.2f}) accepted {acc}/{n} "
            f"false_success {fs} tokens_in {tin / 1000:.1f}k out {tout / 1000:.1f}k"
        )

    # ---- the pre-registered reading table ----
    print("\n=== pre-registered reading table ===")
    r20_rate = r20_p / max(1, r20_p + r20_f)
    r12_rate = r12_p / max(1, r12_p + r12_f)
    if r20_rate > r12_rate and p_primary < 0.05:
        print("  -> BUDGET-BOUND (see PRIMARY above)")
    elif r20_rate <= r12_rate + 1e-9:
        print("  -> NOT budget-bound (no rise): fixture design or model capability")
    else:
        print("  -> PARTIAL (a rise that misses p < 0.05): report per-fixture detail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
