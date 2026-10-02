"""E2D analysis — R2 (the v2 'actionable' skill text) vs R (the v1 prose),
from the round's JSON rows + traces + workspaces.

The pre-registered analysis (data/hbench/E2D-FORMAT-PREREGISTERED.md):

- PRIMARY: pooled tests_pass_at_end R2 vs R, Fisher exact two-sided
  ("format matters" iff R2 > R with p < 0.05).
- SECONDARY: per-fixture; the E2C-excluded 4 vs the E2C-valid 3; turns- and
  wall-to-first-source-edit (from --trace: the first tool.observed event
  whose resources include a non-test source file); LIMIT_TURNS and
  acceptance rates; tokens; digest-brute-force/dump scripts written
  (model-written scratch .py mentioning sha256/hexdigest/digest — the E2C
  failure mode); false_success; the example-atom scan (any worked-example
  atom hard-coded in a final source of a PASSING run — a rule BYPASS would
  show the example input/output pair in the source instead of the rule).

Fisher is e2c_analyze.fisher_exact_two_sided — the implementation the E2C
round verified against the E2/E2B published values BEFORE use (re-verified
on every invocation here).

Usage:
    .venv/bin/python scripts/e2d_analyze.py <stamp-dir> <round.json> [<topup.json> ...]
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2c_analyze import fisher_exact_two_sided  # noqa: E402
from private2_tasks import PRIVATE2_TASKS  # noqa: E402

BY_NAME = {t["name"]: t for t in PRIVATE2_TASKS}
#: The E2C reach-in-budget verdict (validity round, n=3/fixture): the 3
#: fixtures that passed its gate vs the 4 it excluded at max_turns 12.
E2C_VALID = {
    "private2-drawbridge-rollout",
    "private2-palisade-redaction",
    "private2-vellum-order-id",
}
E2C_EXCLUDED = set(BY_NAME) - E2C_VALID

#: Worked-example atoms that exist ONLY in a skill text's examples (never
#: in the rules, never in the shipped sources): a final source carrying
#: one is a rule BYPASS signal (the example input/output pair hard-coded
#: instead of the rule) — every hit is inspected by hand.
EXAMPLE_ATOMS: dict[str, list[str]] = {
    "private2-cairn-money": ["5.678", "88.5", "0.12345", "1.234567891", "XYZ", "FY2024-P11"],
    "private2-drawbridge-rollout": ["billing-spark", "u-77", "B27586", "27586"],
    "private2-palisade-redaction": [
        "6011111111111117",
        "19ff47cc8024",
        "tok_q9w8",
        "jo.reyes@example.com",
        "+1-312-555-0177",
        "60614",
        "signed in from mobile",
    ],
    "private2-cairn-sunset": ["20260401", "20261201", "20270601", "20260928"],
    "private2-vellum-order-id": ["NA-0008642-X", "EU00130579X", "AP-000042U", "0008642", "130579"],
    "private2-queue-consumer-retry": [
        "m-77",
        "m-78",
        "m-79",
        "m-80",
        "settle-9",
        "probe-2",
        "audit-3",
        "claim-4",
    ],
    "private2-infra-config": [
        "Pool_Size",
        "pool.size",
        "HBR_POOL__SIZE",
        "pool-size=45@profile",
        "admin_password",
        "session_tokens",
        "vault_secret",
        "pw-1",
        "st-1",
        "vs-1",
    ],
}

INVALID_STOP = ("MODEL_FAILURE", "crashed")
N_CAP = 6


def self_test() -> None:
    """The pre-registration's requirement: verify Fisher against the
    E2/E2B published values BEFORE any E2D number is read."""
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


def load_cells(paths: list[str]) -> dict[tuple, list[dict]]:
    rows: list[dict] = []
    for path in paths:
        rows += json.loads(Path(path).read_text(encoding="utf-8"))["results"]
    cells: dict[tuple, list[dict]] = defaultdict(list)
    invalid = 0
    for r in rows:
        if (
            r.get("stop_reason") in INVALID_STOP
            or r.get("status") == "crashed"
            or r.get("invalid_reason")  # e.g. REGISTRY_UNAVAILABLE (Bp/Bq)
        ):
            invalid += 1
            continue
        key = (r["fixture"], r["arm"])
        if len(cells[key]) < N_CAP:
            cells[key].append(r)
    print(f"rows loaded: {len(rows)}; INVALID (MODEL_FAILURE/crashed) excluded: {invalid}")
    return cells


def _passes(rows: list[dict]) -> tuple[int, int]:
    p = sum(1 for r in rows if r.get("tests_pass_at_end"))
    return p, len(rows) - p


def _first_source_edit(trace: Path) -> tuple[int, float] | None:
    """(turn, wall seconds) of the first WRITE call (write_file/edit_file),
    from a run's trace. The trace's tool events carry tool ids only — the
    event that names the touched FILES (tool.observed, a StateEvent with
    resources) is committed to the run's state, not published to the bus —
    so the pre-registered 'first SOURCE edit' is measured as the first
    write/edit call, with the workspace diff (sources_edited) separating
    runs that wrote sources from runs that only wrote scratch."""
    events = json.loads(trace.read_text(encoding="utf-8"))
    if not events:
        return None
    start = datetime.fromisoformat(events[0]["ts"])
    for event in events:
        if event["event"] != "tool.execution.completed":
            continue
        if event.get("payload", {}).get("tool_id") not in ("write_file", "edit_file"):
            continue
        if event.get("payload", {}).get("status") != "success":
            continue
        turn = event.get("turn")
        try:
            turn_no = int(str(turn).rsplit("-", 1)[-1] or 0)
        except ValueError:
            turn_no = 0
        wall = (datetime.fromisoformat(event["ts"]) - start).total_seconds()
        return turn_no, wall
    return None


def _workspace_scan(run_dir: Path, fixture: dict) -> dict:
    """One workspace: dump-script candidates (model-written scratch .py
    mentioning digests), example atoms in the FINAL non-test sources, and
    WHICH sources were edited at all (vs shipped) — the companion signal
    to first-write: a run that never touched a source file is the E2C
    failure mode regardless of when it first wrote scratch."""
    scratch = []
    for p in sorted(run_dir.rglob("*.py")):
        if p.name in fixture["files"] or p.name.startswith("test_"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(word in text.lower() for word in ("sha256", "hexdigest", "digest")):
            scratch.append(p.name)
    atoms_hit = []
    sources_edited = []
    for rel, shipped in fixture["files"].items():
        path = run_dir / rel
        if rel.startswith("test_"):
            continue
        if not path.is_file():
            sources_edited.append(rel)
            continue
        text = path.read_text(encoding="utf-8")
        if text != shipped:
            sources_edited.append(rel)
        hit = [a for a in EXAMPLE_ATOMS.get(fixture["name"], []) if a in text]
        if hit:
            atoms_hit.append((rel, hit))
    return {
        "scratch": scratch,
        "example_atoms_in_source": atoms_hit,
        "sources_edited": sources_edited,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    stamp_dir = Path(argv[0])
    print("Fisher self-test against the E2/E2B published values:")
    self_test()
    cells = load_cells(argv[1:])
    fixtures = sorted({fx for fx, _ in cells})
    arms = sorted({arm for _, arm in cells})
    if arms != ["R", "R2"]:
        print(f"\nWARNING: expected arms R,R2 — found {arms}")

    print(f"\n=== tests_pass_at_end per cell (valid rows, capped n={N_CAP}) ===")
    table: dict[tuple, tuple[int, int]] = {}
    for fx in fixtures:
        for arm in arms:
            p, f = _passes(cells.get((fx, arm), []))
            table[(fx, arm)] = (p, f)
            print(f"  {fx:32s} {arm:3s} {p}/{p + f}")

    pooled: dict[str, tuple[int, int]] = {}
    for arm in arms:
        p = sum(table[(fx, arm)][0] for fx in fixtures)
        n = sum(table[(fx, arm)][0] + table[(fx, arm)][1] for fx in fixtures)
        pooled[arm] = (p, n - p)
        print(f"\npooled {arm}: {p}/{n}")

    if "R2" in pooled and "R" in pooled:
        r2p, r2f = pooled["R2"]
        rp, rf = pooled["R"]
        p = fisher_exact_two_sided(r2p, r2f, rp, rf)
        verdict = (
            "FORMAT MATTERS (R2 > R, p < 0.05)"
            if (r2p > rp and p < 0.05)
            else "NO SEPARATION"
            if r2p >= rp
            else "R2 WORSE (directional)"
        )
        print(f"\nPRIMARY: pooled R2 {r2p}/{r2p + r2f} vs R {rp}/{rp + rf} -> Fisher p={p:.6g}")
        print(f"  -> {verdict}")

    print("\n=== per-fixture Fisher (R2 vs R) ===")
    for fx in fixtures:
        if (fx, "R2") in table and (fx, "R") in table:
            r2p, r2f = table[(fx, "R2")]
            rp, rf = table[(fx, "R")]
            p = fisher_exact_two_sided(r2p, r2f, rp, rf)
            print(f"  {fx:32s} R2 {r2p}/{r2p + r2f} vs R {rp}/{rp + rf} -> p={p:.6g}")

    print("\n=== the E2C-excluded 4 vs the E2C-valid 3 (pre-registered split) ===")
    for label, group in (("E2C-valid 3", E2C_VALID), ("E2C-excluded 4", E2C_EXCLUDED)):
        for arm in arms:
            p = sum(table[(fx, arm)][0] for fx in fixtures if fx in group)
            n = sum(table[(fx, arm)][0] + table[(fx, arm)][1] for fx in fixtures if fx in group)
            print(f"  {label:16s} {arm:3s} {p}/{n}")

    print("\n=== secondary mechanics per arm (valid rows) ===")
    for arm in arms:
        rows = [r for fx in fixtures for r in cells.get((fx, arm), [])]
        if not rows:
            continue
        limit = sum(1 for r in rows if r.get("stop_reason") == "LIMIT_TURNS")
        accepted = sum(1 for r in rows if r.get("accepted"))
        false_success = sum(1 for r in rows if r.get("false_success"))
        tokens_in = sum(r.get("tokens_in", 0) for r in rows) / len(rows)
        tokens_out = sum(r.get("tokens_out", 0) for r in rows) / len(rows)
        turns = sum(r.get("turns", 0) for r in rows) / len(rows)
        wall = sum(r.get("wall_seconds", 0.0) for r in rows) / len(rows)
        print(
            f"  {arm:3s} n={len(rows)} LIMIT_TURNS {limit} accepted {accepted} "
            f"false_success {false_success} turns_mean {turns:.1f} wall_mean {wall:.0f}s "
            f"tokens_in_mean {tokens_in:.0f} tokens_out_mean {tokens_out:.0f}"
        )

    print("\n=== time-to-first-source-edit (from the traces) ===")
    traces = stamp_dir / "traces"
    edits: dict[str, list[tuple]] = defaultdict(list)
    never_edited: dict[str, int] = defaultdict(int)
    for (fx, arm), rows in cells.items():
        for r in rows:
            trace = traces / f"trace-{fx}-{r['run_id']}.json"
            if not trace.is_file():
                continue
            first = _first_source_edit(trace)
            if first is None:
                never_edited[arm] += 1
            else:
                edits[arm].append(first)
    for arm in arms:
        if not edits[arm]:
            print(f"  {arm:3s} no source edit in any traced run")
            continue
        turns = [t for t, _w in edits[arm]]
        walls = [w for _t, w in edits[arm]]
        print(
            f"  {arm:3s} edited {len(edits[arm])} runs "
            f"(never-edited {never_edited[arm]}): first-edit turn mean "
            f"{sum(turns) / len(turns):.1f} (min {min(turns)}, max {max(turns)}), "
            f"wall mean {sum(walls) / len(walls):.0f}s"
        )

    print("\n=== dump/brute-force scripts + example-atom scan (all workspaces) ===")
    scratch_total: dict[str, int] = defaultdict(int)
    never_touched_sources: dict[str, int] = defaultdict(int)
    bypass_hits: list = []
    tampered: list = []
    for (fx, arm), rows in cells.items():
        fixture = BY_NAME.get(fx)

        if fixture is None:
            continue
        for r in rows:
            run_dir = stamp_dir / "runs" / r["run_id"]
            if not run_dir.is_dir():
                continue
            # test-file integrity (the pre-registered tamper rule)
            for rel, content in fixture["files"].items():
                if not rel.startswith("test_"):
                    continue
                path = run_dir / rel
                if not path.is_file() or path.read_text(encoding="utf-8") != content:
                    tampered.append((fx, arm, r["run_id"], rel))
            scan = _workspace_scan(run_dir, fixture)
            if scan["scratch"]:
                scratch_total[arm] += len(scan["scratch"])
                print(f"  {fx:32s} {arm:3s} {r['run_id']} scratch: {scan['scratch']}")
            if not scan["sources_edited"]:
                never_touched_sources[arm] += 1
            if scan["example_atoms_in_source"]:
                passing = " (PASSING — inspect)" if r.get("tests_pass_at_end") else " (failing)"
                bypass_hits.append((fx, arm, r["run_id"], scan["example_atoms_in_source"]))
                print(
                    f"  {fx:32s} {arm:3s} {r['run_id']} EXAMPLE ATOMS{passing}: "
                    f"{scan['example_atoms_in_source']}"
                )
    print(f"\nscratch digest-scripts total: {dict(scratch_total)}")
    print(f"runs that never touched a source file: {dict(never_touched_sources)}")
    print(f"test-file TAMPERED: {len(tampered)}")
    for t in tampered:
        print("  ", t)
    print(f"example-atom hits (inspect each): {len(bypass_hits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
