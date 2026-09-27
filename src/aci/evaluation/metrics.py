"""Benchmark metrics + report export (plan §§34, 70, 71).

Per-result metrics are computed at execution time and persisted with the
result row; the report aggregates them per variant. Everything here measures
the *routing* side — the primary score still comes from task acceptance
criteria (§35), which live with the fixture.

Metric associations are observational, not causal (§34): without controlled
task execution per variant, exported differences do not prove what caused
what. The report says so explicitly.
"""

from typing import Any

from aci.evaluation.models import VARIANTS, BenchmarkCase, BenchmarkResult, BenchmarkRun, VariantId


def result_metrics(
    case: BenchmarkCase,
    selected_ids: list[str],
    *,
    latency_ms: int | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Routing-side metrics for one (case × variant) measurement (§71).

    ``recall`` is None when the case annotates no relevant capabilities —
    an unmeasurable case is reported as unmeasured, never as 0.
    """
    relevant = set(case.relevant_strong) | set(case.relevant_acceptable)
    selected = set(selected_ids)
    metrics: dict[str, Any] = {
        "selected_count": len(selected_ids),
        "abstained": int(not selected_ids),
        "relevant_selected": len(selected & relevant),
        "recall": (len(selected & relevant) / len(relevant)) if relevant else None,
        "misroutes": len(selected & set(case.relevant_irrelevant)),
        "latency_ms": latency_ms,
    }
    if extra:
        metrics.update(extra)
    return metrics


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def export_report(run: BenchmarkRun, results: list[BenchmarkResult]) -> dict[str, Any]:
    """Per-variant aggregates + per-case rows + versioned references.

    Every row names the exact router implementations and the exact
    capability id/version/digest it selected (§52 acceptance), so a number
    can always be traced back to the code and content that produced it.
    """
    by_variant: dict[VariantId, list[BenchmarkResult]] = {v: [] for v in VARIANTS}
    for result in results:
        by_variant[result.variant].append(result)

    variants: dict[str, Any] = {}
    for variant, rows in by_variant.items():
        latencies = [
            r.metrics["latency_ms"] for r in rows if r.metrics.get("latency_ms") is not None
        ]
        recalls = [r.metrics["recall"] for r in rows if r.metrics.get("recall") is not None]
        variants[variant] = {
            "results": len(rows),
            "mean_latency_ms": _mean([float(x) for x in latencies]),
            "mean_selected_count": _mean([float(r.metrics["selected_count"]) for r in rows]),
            "abstention_rate": _mean([float(r.metrics["abstained"]) for r in rows]),
            "mean_recall": _mean([float(x) for x in recalls]),
            "misroute_rate": _mean([float(r.metrics["misroutes"]) for r in rows]),
        }

    return {
        "run_id": run.run_id,
        "label": run.label,
        "created_at": run.created_at.isoformat(),
        "case_count": run.case_count,
        "variants": variants,
        "results": [
            {
                "result_id": r.result_id,
                "case_id": r.case_id,
                "variant": r.variant,
                "route_run_id": r.route_run_id,
                "bundle_id": r.bundle_id,
                "selected": [s.model_dump(mode="json") for s in r.selected],
                "router": r.router.model_dump(mode="json"),
                "metrics": dict(r.metrics),
            }
            for r in results
        ],
        # §34: label the association explicitly — never present these
        # aggregates as causal findings without controlled execution.
        "notes": [
            "Metric associations are observational, not causal (§34): variants "
            "A/B are baselines; without controlled task execution per variant, "
            "differences do not prove what caused what.",
            "Primary score comes from task acceptance criteria (§35), which live "
            "with the fixture — these metrics cover the routing side only.",
        ],
    }
