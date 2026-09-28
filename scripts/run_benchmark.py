"""Run the 10-task smoke benchmark (§§34–35, 70–71) and export the report.

Replays SMOKE_CASES through the full §70 variant matrix (A client_alone,
B manual_baseline, C retrieval_only, D retrieval_rerank, E full_pipeline)
against the live registry, persists every run/result via the benchmark
store (§41), and writes the §71 report (per-variant aggregates +
per-case rows with exact router versions and capability pins) to
``data/benchmark/<label>-<run_id>.json``.

The report's aggregates are observational, not causal (§34): the primary
score still comes from task acceptance criteria, which live with the
fixture. Re-running creates a fresh run with fresh ids and the same
measurements — fixtures never change.

Usage:
    .venv/bin/python scripts/run_benchmark.py [--label LABEL]

Requires the live DB (docker compose up -d db + alembic upgrade head).
"""

import argparse
import json
from pathlib import Path

from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.evaluation.cases import SMOKE_CASES
from aci.evaluation.dev_cases import DEV_CASES
from aci.evaluation.metrics import export_report
from aci.evaluation.models import BenchmarkRun

REPORT_ROOT = Path("data/benchmark")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        default="smoke-10",
        help="run label recorded with the benchmark run (default: %(default)s)",
    )
    parser.add_argument(
        "--cases",
        choices=("smoke", "dev"),
        default="smoke",
        help="which case set to run (default: %(default)s). 'dev' = the "
        "31-case set annotated against the real production corpus (§55).",
    )
    args = parser.parse_args()

    cases = SMOKE_CASES if args.cases == "smoke" else DEV_CASES
    container = Container(Settings())
    results = container.benchmark_harness.run(cases, label=args.label)
    run_id = results[0].run_id

    # The harness mints the run internally; reconstruct it for the report
    # (same pattern as tests/integration/test_benchmark_harness.py).
    run = BenchmarkRun(
        run_id=run_id,
        label=args.label,
        created_at=results[0].created_at,
        case_count=len(cases),
    )
    report = export_report(run, results)

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    out = REPORT_ROOT / f"{args.label}-{run_id}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"run {run_id} ({args.label}): {len(results)} results -> {out}\n")
    header = (
        f"{'variant':<20}{'results':>8}{'mean_sel':>10}"
        f"{'abstain%':>10}{'mean_recall':>13}{'misroute%':>11}{'lat_ms':>8}"
    )
    print(header)
    print("-" * len(header))
    for variant, agg in report["variants"].items():
        recall = agg["mean_recall"]
        print(
            f"{variant:<20}{agg['results']:>8}{agg['mean_selected_count']:>10.2f}"
            f"{agg['abstention_rate'] * 100:>10.1f}"
            f"{(recall if recall is not None else float('nan')):>13.3f}"
            f"{agg['misroute_rate'] * 100:>11.1f}"
            f"{(agg['mean_latency_ms'] or 0):>8.0f}"
        )
    print("\nNotes:")
    for note in report["notes"]:
        print(f"  - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
