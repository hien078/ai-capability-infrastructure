"""JEV eval harness — the §4 promotion gate instrument (READ-ONLY).

docs/plans/jev-reranker.md §2.6. Measures the heuristic vs the JEV judge on
the SAME corpus with the REAL §14 stages (eligibility → retrieval →
reranker → dependency resolve → compose) — the same composition as the REST
Container (adapters/inbound/rest/wiring.py) — WITHOUT persisting
route_runs/bundles: the eval router composes the stages directly and holds
no telemetry repo at all (structurally, not by convention — pinned by
test). The engine connects with ``default_transaction_read_only=on`` and
the script verifies the setting before running (fail closed), reusing the
guard logic from scripts/routing_replay.py.

Inputs:
  --cases dev        DEV_CASES + KERNEL_QUERY_CASES (src/aci/evaluation/)
  --organic PATH     jsonl of ORGANIC cases (the user's private product
                     work — §3: lives only in data/jev-eval/, gitignored;
                     NEVER committed, never printed; this script prints
                     case IDS and metrics only, never task texts)
  --reranker         heuristic | jev (the two arms of the gate)

Per set the report carries (§2.6): recall@bundle (dev), irrelevant-attach
rate (organic: a bundle containing any skill not in the case's ``acceptable``
set), correct-abstain rate (organic cases whose ``acceptable`` is empty),
judge status counts, latency p50/p95, total judge tokens (None — the
adapter does not report usage; extend JudgeVerdict if the gate needs real
token counts).

P2 (independent review 2026-10-07): a row whose judge did not answer
(``judge_status`` not None/"ok") is EXCLUDED from the irrelevant-attach /
clean / correct-abstain numerators AND denominators — a judge failure
produces an empty bundle, which is not a clean attach, not a correct
abstention (the empty-acceptable trap), and not a measurement of attach
quality. Those rows are reported separately (``judge_failed`` count) and
each set summary carries ``judge_ok_rate`` (ok / judged rows; None when no
judge ran — the heuristic arm).

THE §4 GATE (pre-registered before any JEV number is seen — it travels
with every report):
  1. Organic irrelevant-attach rate: JEV <= 10% (heuristic baseline on the
     SAME set, expected ~80%).
  2. DEV_CASES recall@bundle: JEV >= heuristic - 5 points.
  3. Latency on home-sever -> 9router: p95 <= 4 s; judge ok rate >= 95%.
  4. Security tests green; no invalid id ever reaches a bundle.
Switching ACI_RERANKER=jev on home-sever is the USER's call after ALL four
hold; this instrument only measures.

Usage:
    .venv/bin/python scripts/jev_eval.py --cases dev \\
        --database-url postgresql+psycopg://... --reranker heuristic
    ACI_JEV_BASE_URL=... ACI_JEV_API_KEY=... .venv/bin/python scripts/jev_eval.py \\
        --organic data/jev-eval/organic.jsonl --reranker jev --database-url ...

§34: routing-side only, no acceptance tests execute, author-labeled organic
set — every number is directional until the gate is judged on both arms.
"""

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import make_url
from sqlalchemy.engine import Engine

# Run THIS checkout's code, not whatever the shared .venv's editable install
# points at: an eval must measure the router of the worktree it lives in.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
# The read-only engine + fail-closed guards are ONE instrument family with
# routing_replay.py — reuse them rather than fork the fail-closed logic.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import routing_replay as replay  # noqa: E402

from aci.adapters.outbound.model_provider.hashing import HashingEmbedder  # noqa: E402
from aci.adapters.outbound.model_provider.judge import OpenAICompatSkillJudge  # noqa: E402
from aci.adapters.outbound.model_provider.semantic import FastEmbedEmbedder  # noqa: E402
from aci.adapters.outbound.pgvector.repository import (  # noqa: E402
    SqlAlchemyEmbeddingRepository,
)
from aci.adapters.outbound.postgres.assessments import (  # noqa: E402
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.adapters.outbound.postgres.base import make_session_factory  # noqa: E402
from aci.adapters.outbound.postgres.policy_snapshots import (  # noqa: E402
    SqlAlchemyPolicySnapshotRepository,
)
from aci.adapters.outbound.postgres.relations import SqlAlchemyRelationRepository  # noqa: E402
from aci.adapters.outbound.postgres.repositories import (  # noqa: E402
    SqlAlchemyArtifactStore,
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.application.payload_sizes import ArtifactPayloadSizes  # noqa: E402
from aci.application.protocols import CapabilityReranker, SkillJudge  # noqa: E402
from aci.domain.capability.models import RouteCapabilitiesCommand, TaskContext  # noqa: E402
from aci.domain.policy.models import (  # noqa: E402
    ClientDescriptor,
    EligibleCandidate,
    PolicyRules,
    ProtocolDescriptor,
    RequestContext,
)
from aci.domain.routing.models import (  # noqa: E402
    CompositionResult,
    RetrievalResult,
    TaskDescriptor,
)
from aci.evaluation.dev_cases import DEV_CASES  # noqa: E402
from aci.evaluation.kernel_query_cases import KERNEL_QUERY_CASES  # noqa: E402
from aci.evaluation.metrics import result_metrics  # noqa: E402
from aci.evaluation.models import BenchmarkCase  # noqa: E402
from aci.routing.composer import MinimalBundleComposer  # noqa: E402
from aci.routing.dependencies import DefaultDependencyResolver  # noqa: E402
from aci.routing.eligibility import DefaultEligibilityPolicy  # noqa: E402
from aci.routing.rerankers.heuristic import HeuristicReranker  # noqa: E402
from aci.routing.rerankers.jev import JevReranker  # noqa: E402
from aci.routing.retrieval import EmbeddingRetriever  # noqa: E402

#: The §14 pipeline's retrieval limit (route_capabilities.py).
RETRIEVAL_LIMIT = replay.RETRIEVAL_LIMIT
PRINCIPAL = "jev-eval"

#: The pre-registered §4 gate, printed with every report.
GATE: tuple[str, ...] = (
    "1. organic irrelevant-attach rate: JEV <= 10% (heuristic baseline, same set)",
    "2. DEV_CASES recall@bundle: JEV >= heuristic - 5 points",
    "3. latency home-sever -> 9router: p95 <= 4s; judge ok rate >= 95%",
    "4. security tests green; no invalid id ever reaches a bundle",
)


# ---------------------------------------------------------------------------
# Organic cases (§3: private data — ids and metrics only ever leave this path)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrganicCase:
    """One labeled real-traffic prompt: ``acceptable`` lists the skills that
    would have been acceptable to attach (EMPTY = the correct answer is
    abstention). The task text is PRIVATE (§3): never committed, never
    printed by this script."""

    case_id: str
    task_text: str
    acceptable: tuple[str, ...] = ()


def load_organic_cases(path: str | Path) -> list[OrganicCase]:
    """Parse one JSON object per line; fail LOUDLY on a malformed line."""
    cases: list[OrganicCase] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: line {number} is not JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: line {number} is not a JSON object")
        if not raw.get("task_text"):
            raise ValueError(f"{path}: line {number} has no task_text")
        cases.append(
            OrganicCase(
                case_id=str(raw.get("case_id", f"organic-line-{number}")),
                task_text=str(raw["task_text"]),
                acceptable=tuple(str(a) for a in raw.get("acceptable", [])),
            )
        )
    return cases


# ---------------------------------------------------------------------------
# The eval router: the REAL §14 stages, NO telemetry surfaces.
# ---------------------------------------------------------------------------


@dataclass
class EvalRouter:
    """The real stage classes in the Container's composition — minus the
    persistence repos. Fields are typed as the concrete stage classes on
    purpose: the DB builder wires exactly what the REST Container wires,
    and the unit tests wire the SAME classes over fakes (the eval never
    re-implements a stage — that would measure a copy, not the router)."""

    eligibility: DefaultEligibilityPolicy
    retriever: EmbeddingRetriever
    reranker: CapabilityReranker
    resolver: DefaultDependencyResolver
    composer: MinimalBundleComposer
    rules: PolicyRules
    candidates: list[EligibleCandidate]

    def run_case(self, case: BenchmarkCase | OrganicCase, *, set_name: str) -> dict[str, Any]:
        """One case through the real pipeline; NOTHING is persisted."""
        started = time.perf_counter()
        envelope = RequestContext(
            request_id=f"req_jev_eval_{case.case_id}",
            trace_id=f"trc_jev_eval_{case.case_id}",
            principal_id=PRINCIPAL,
            client=ClientDescriptor(type="jev-eval"),
            protocol=ProtocolDescriptor(type="eval", version="1"),
        )
        context = envelope.to_routing_context(TaskContext())
        decision = self.eligibility.filter(
            self.candidates, context, self.rules, allowed_kinds=["skill"]
        )
        retrieval = self.retriever.retrieve(case.task_text, decision.kept, limit=RETRIEVAL_LIMIT)
        task = TaskDescriptor(task_text=case.task_text, context=TaskContext())
        rerank = self.reranker.rerank(task, retrieval.candidates, context)
        resolution = self.resolver.resolve(rerank.ranked)
        command = RouteCapabilitiesCommand(task_text=case.task_text)  # default budgets 5/8000
        composition = self.composer.compose(
            resolution, command, route_run_id=f"jev_eval_{case.case_id}", now=datetime.now(UTC)
        )
        route_latency_ms = int((time.perf_counter() - started) * 1000)
        return build_row(
            case=case,
            set_name=set_name,
            retrieval=retrieval,
            rerank=rerank,
            composition=composition,
            route_latency_ms=route_latency_ms,
        )


def _dedup(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def build_row(
    *,
    case: BenchmarkCase | OrganicCase,
    set_name: str,
    retrieval: RetrievalResult,
    rerank: Any,
    composition: CompositionResult,
    route_latency_ms: int,
) -> dict[str, Any]:
    """The deterministic per-case record (JSON-ready; nothing generated)."""
    bundle_ids = [item.capability_id for item in composition.bundle.items]
    trace = rerank.trace
    row: dict[str, Any] = {
        "case_id": case.case_id,
        "set": set_name,
        "task_chars": len(case.task_text),  # §3: length only, never the text
        "bundle_items": bundle_ids,
        "empty_bundle": not bundle_ids,
        "spent_tokens": composition.trace.spent_tokens,
        "route_latency_ms": route_latency_ms,
        "judge_status": trace.judge_status,
        "judge_latency_ms": trace.judge_latency_ms,
        "invalid_ids": trace.invalid_ids,
        "selected_ids": trace.selected_ids or [],
        "necessity_dropped_ids": trace.necessity_dropped_ids or [],
        "retrieval_model": retrieval.trace.model_id,
    }
    if isinstance(case, BenchmarkCase):
        expected = _dedup([*case.relevant_strong, *case.relevant_acceptable])
        metrics = result_metrics(case, bundle_ids, latency_ms=None)
        row.update(
            {
                "expected_ids": expected,
                "acceptable": expected,
                "hit_in_bundle": any(cid in expected for cid in bundle_ids),
                "recall": metrics["recall"],
                "misroutes": metrics["misroutes"],
                "irrelevant_attach": None,
                "clean_attach": None,
                "abstain_correct": None,
            }
        )
    else:
        acceptable = set(case.acceptable)
        row.update(
            {
                "expected_ids": [],
                "acceptable": list(case.acceptable),
                "hit_in_bundle": None,
                "recall": None,
                "misroutes": None,
                "irrelevant_attach": any(cid not in acceptable for cid in bundle_ids),
                "clean_attach": all(cid in acceptable for cid in bundle_ids),
                "abstain_correct": (not bundle_ids) if not acceptable else None,
            }
        )
    return row


# ---------------------------------------------------------------------------
# Aggregation + rendering (pure).
# ---------------------------------------------------------------------------


def percentile(values: list[int], p: float) -> int | None:
    """Nearest-rank percentile; None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(p / 100 * len(ordered))))
    return ordered[rank - 1]


def _judge_failed(row: dict[str, Any]) -> bool:
    """A row whose judge did not answer (P2): ``judge_status`` set but not
    ``ok``. Excluded from the attach metrics — a judge failure produces an
    empty bundle, which is not a measurement of attach quality."""
    status = row.get("judge_status")
    return status is not None and status != "ok"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one case set (deterministic; the §2.6 gate table fields).

    P2: judge-failed rows leave the irrelevant-attach / clean /
    correct-abstain numerators AND denominators and are reported separately
    (``judge_failed``); ``judge_ok_rate`` is ok/judged (None when no judge
    ran — the heuristic arm never runs one).
    """
    organic = [r for r in rows if r["set"] == "organic"]
    # P2: only rows the judge ANSWERED (or no judge was involved) measure
    # attach quality.
    organic_measured = [r for r in organic if not _judge_failed(r)]
    devish = [r for r in rows if r["set"] in ("dev", "kernel")]
    recalls = [r["recall"] for r in devish if r["recall"] is not None]
    route_latencies = [r["route_latency_ms"] for r in rows]
    judge_latencies = [r["judge_latency_ms"] for r in rows if r["judge_latency_ms"] is not None]
    abstention_expected = [r for r in organic_measured if r["abstain_correct"] is not None]
    judged = [r for r in rows if r["judge_status"] is not None]
    spent = [r["spent_tokens"] for r in rows]
    return {
        "n_cases": len(rows),
        "hit_in_bundle": sum(1 for r in devish if r["hit_in_bundle"]),
        "mean_recall": round(sum(recalls) / len(recalls), 3) if recalls else None,
        "empty_bundles": sum(1 for r in rows if r["empty_bundle"]),
        "irrelevant_attach": sum(1 for r in organic_measured if r["irrelevant_attach"]),
        "irrelevant_attach_rate": (
            round(
                sum(1 for r in organic_measured if r["irrelevant_attach"]) / len(organic_measured),
                3,
            )
            if organic_measured
            else None
        ),
        "clean_attach": sum(1 for r in organic_measured if r["clean_attach"]),
        "abstention_expected": len(abstention_expected),
        "abstention_correct": sum(1 for r in abstention_expected if r["abstain_correct"]),
        "correct_abstain_rate": (
            round(
                sum(1 for r in abstention_expected if r["abstain_correct"])
                / len(abstention_expected),
                3,
            )
            if abstention_expected
            else None
        ),
        "judge_failed": sum(1 for r in rows if _judge_failed(r)),
        "judge_ok_rate": (
            round(sum(1 for r in judged if r["judge_status"] == "ok") / len(judged), 3)
            if judged
            else None
        ),
        "judge_status_counts": dict(Counter(r["judge_status"] or "none" for r in rows)),
        "necessity_dropped": sum(len(r.get("necessity_dropped_ids") or []) for r in rows),
        "route_latency_p50_ms": percentile(route_latencies, 50),
        "route_latency_p95_ms": percentile(route_latencies, 95),
        "judge_latency_p50_ms": percentile(judge_latencies, 50),
        "judge_latency_p95_ms": percentile(judge_latencies, 95),
        # The adapter does not report usage; extend JudgeVerdict if the gate
        # needs real token counts (§2.6 "if reported").
        "total_judge_tokens": None,
        "mean_spent_tokens": round(sum(spent) / len(spent), 1) if spent else 0.0,
    }


def build_report(
    rows: list[dict[str, Any]],
    *,
    reranker: str,
    embedder_model: str,
    database: str,
    organic_source: str | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The full eval report — deterministic (no timestamps, no generated ids)."""
    sets: dict[str, Any] = {}
    for set_name in ("dev", "kernel", "organic"):
        set_rows = [r for r in rows if r["set"] == set_name]
        if set_rows:
            sets[set_name] = summarize(set_rows)
    return {
        "reranker": reranker,
        "embedder_model": embedder_model,
        "database": database,
        "organic_source": organic_source,
        "read_only": True,
        "config": config or {},
        "n_cases": len(rows),
        "gate": list(GATE),
        "sets": sets,
        "rows": rows,
        "notes": [
            "§34: routing-side only, no acceptance tests execute; the organic set "
            "is author-labeled — directional until the gate is judged on both arms.",
            "§3: organic task texts are private — this report carries ids, "
            "metrics and lengths only.",
        ],
    }


def render_summary(report: dict[str, Any]) -> str:
    lines = [
        f"# jev eval — reranker={report['reranker']} embedder={report['embedder_model']}",
        f"# database: {report['database']} (read-only connection; persisted nothing)",
        f"# organic source: {report['organic_source'] or '(none)'}",
        "",
        "| set | n | hitB | recall | irr-attach | abst-ok | empty |"
        " judge ok/err/to/inv | judge-ok% | route p50/p95 | judge p50/p95 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for set_name, s in report["sets"].items():
        counts = s["judge_status_counts"]
        judge = "/".join(
            str(counts.get(status, 0)) for status in ("ok", "error", "timeout", "invalid_output")
        )
        lines.append(
            f"| {set_name} | {s['n_cases']} | {s['hit_in_bundle']} | {s['mean_recall']} "
            f"| {s['irrelevant_attach']} ({s['irrelevant_attach_rate']}) "
            f"| {s['abstention_correct']}/{s['abstention_expected']} "
            f"| {s['empty_bundles']} | {judge} | {s['judge_ok_rate']} "
            f"| {s['route_latency_p50_ms']}/{s['route_latency_p95_ms']} "
            f"| {s['judge_latency_p50_ms']}/{s['judge_latency_p95_ms']} |"
        )
    lines.append("")
    lines.append("## the §4 gate (pre-registered)")
    lines.extend(f"- {item}" for item in report["gate"])
    return "\n".join(lines)


def render_table(rows: list[dict[str, Any]]) -> str:
    """Per-case markdown table — ids and metrics only (§3: never task texts).

    P2: a judge-failed row prints ``judge-failed`` in the attach column —
    never ``clean``/``abstain-ok`` (a failed judge's empty bundle is not an
    attach-quality verdict).
    """
    lines = [
        "| case | set | bundle | judge | judge_ms | route_ms | attach |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        attach = "-"
        if _judge_failed(r):
            attach = "judge-failed"
        elif r["set"] == "organic":
            attach = "irrelevant" if r["irrelevant_attach"] else "clean"
            if r["abstain_correct"] is not None:
                attach = "abstain-ok" if r["abstain_correct"] else "abstain-miss"
        lines.append(
            f"| {r['case_id']} | {r['set']} | {', '.join(r['bundle_items']) or '(empty)'} "
            f"| {r['judge_status'] or 'heuristic'} | {r['judge_latency_ms']} "
            f"| {r['route_latency_ms']} | {attach} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Config (fail-closed, mirrors rest/wiring.py).
# ---------------------------------------------------------------------------


def judge_from_config(
    *,
    base_url: str,
    api_key: str,
    model: str,
    reasoning_effort: str,
    timeout_s: float,
    max_inflight: int = 2,
) -> SkillJudge:
    """The §2.3 rule: jev requires base_url+api_key+model, else fail closed."""
    missing = [
        name
        for name, value in (
            ("ACI_JEV_BASE_URL", base_url),
            ("ACI_JEV_API_KEY", api_key),
            ("ACI_JEV_MODEL", model),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"--reranker jev requires {' + '.join(missing)} to be set — "
            "refusing to run a half-configured judge (fail closed)"
        )
    return OpenAICompatSkillJudge(
        base_url=base_url,
        api_key=api_key,
        model=model,
        reasoning_effort=reasoning_effort,
        timeout_s=timeout_s,
        max_inflight=max_inflight,
    )


def reranker_from_config(
    name: str,
    *,
    judge: SkillJudge | None = None,
    candidate_limit: int = 12,
    max_select: int = 2,
    on_failure: str = "abstain",
    necessity_gate: str = "none",
) -> CapabilityReranker:
    """Pick the eval arm: heuristic baseline or the JEV judge."""
    if name == "heuristic":
        return HeuristicReranker()
    if name != "jev":
        raise ValueError(f"unknown reranker {name!r} (heuristic|jev)")
    if judge is None:
        raise ValueError("--reranker jev requires a judge (judge_from_config)")
    return JevReranker(
        judge,
        candidate_limit=candidate_limit,
        max_select=max_select,
        on_failure=on_failure,  # type: ignore[arg-type]
        fallback=HeuristicReranker() if on_failure == "heuristic" else None,
        necessity_gate=necessity_gate,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# DB layer — read-only; reuses the routing_replay guards.
# ---------------------------------------------------------------------------


def build_router(
    database_url: str,
    *,
    reranker_name: str,
    judge: SkillJudge | None = None,
    embedder_name: str = "fastembed",
    candidate_limit: int = 12,
    max_select: int = 2,
    on_failure: str = "abstain",
    necessity_gate: str = "none",
) -> tuple[EvalRouter, Engine]:
    """Wire the REAL §14 stages over a read-only engine — the same
    composition as the REST Container (adapters/inbound/rest/wiring.py)."""
    engine = replay.open_read_only_engine(database_url)
    with engine.connect() as conn:
        replay.assert_read_only(conn)
    sessions = make_session_factory(engine)

    capabilities = SqlAlchemyCapabilityRepository(sessions)
    releases = SqlAlchemyReleaseRepository(sessions)
    artifacts = SqlAlchemyArtifactStore(sessions)
    licenses = SqlAlchemyLicenseAssessmentRepository(sessions)
    securities = SqlAlchemySecurityAssessmentRepository(sessions)
    policy_snapshots = SqlAlchemyPolicySnapshotRepository(sessions)
    relations = SqlAlchemyRelationRepository(sessions)
    embeddings = SqlAlchemyEmbeddingRepository(sessions)

    from aci.application.list_candidates import ProductionCandidateLoader  # noqa: E402

    loader = ProductionCandidateLoader(capabilities, releases, licenses, securities)
    embedder: HashingEmbedder | FastEmbedEmbedder = (
        FastEmbedEmbedder() if embedder_name == "fastembed" else HashingEmbedder()
    )
    snapshot = policy_snapshots.latest_snapshot()
    rules = snapshot.rules if snapshot is not None else PolicyRules()
    candidates = loader.load(rules.required_channel)

    router = EvalRouter(
        eligibility=DefaultEligibilityPolicy(),
        retriever=EmbeddingRetriever(capabilities, embedder, embeddings),
        reranker=reranker_from_config(
            reranker_name,
            judge=judge,
            candidate_limit=candidate_limit,
            max_select=max_select,
            on_failure=on_failure,
            necessity_gate=necessity_gate,
        ),
        # _MemoRelations: same rows, same resolver answers, thousands fewer
        # roundtrips per run against a remote registry (see routing_replay).
        resolver=DefaultDependencyResolver(replay._MemoRelations(relations), releases),  # noqa: SLF001
        composer=MinimalBundleComposer(ArtifactPayloadSizes(capabilities, artifacts)),
        rules=rules,
        candidates=candidates,
    )
    replay._assert_fully_indexed(candidates, embeddings, embedder.model_id)  # noqa: SLF001
    return router, engine


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--cases",
        default="none",
        choices=["dev", "none"],
        help="built-in case sets: dev = DEV_CASES + KERNEL_QUERY_CASES",
    )
    parser.add_argument(
        "--organic",
        help="jsonl of organic cases (PRIVATE, §3 — e.g. data/jev-eval/organic.jsonl)",
    )
    parser.add_argument(
        "--reranker",
        default="heuristic",
        choices=["heuristic", "jev"],
        help="which reranker arm to measure (default: heuristic baseline)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ACI_DATABASE_URL"),
        help="registry DB URL (no silent default: pass it or set ACI_DATABASE_URL)",
    )
    parser.add_argument(
        "--embedder",
        default="fastembed",
        choices=["fastembed", "hashing"],
        help="embedder whose vectors the eval retrieves with (default: fastembed)",
    )
    parser.add_argument("--output", help="write the JSON report to this path")
    parser.add_argument("--jev-base-url", default=os.environ.get("ACI_JEV_BASE_URL", ""))
    parser.add_argument("--jev-api-key", default=os.environ.get("ACI_JEV_API_KEY", ""))
    parser.add_argument("--jev-model", default=os.environ.get("ACI_JEV_MODEL", "OneNexus/glm-5.3"))
    parser.add_argument("--jev-reasoning-effort", default="low")
    parser.add_argument("--jev-timeout", type=float, default=8.0)
    parser.add_argument("--jev-candidates", type=int, default=12)
    parser.add_argument("--jev-max-select", type=int, default=2)
    parser.add_argument(
        "--jev-max-inflight",
        type=int,
        default=int(os.environ.get("ACI_JEV_MAX_INFLIGHT") or 2),
        help="max judge requests in flight (abandoned overruns included) before "
        "a call answers 'judge busy' without an HTTP call (default: 2)",
    )
    parser.add_argument("--jev-on-failure", default="abstain", choices=["abstain", "heuristic"])
    parser.add_argument(
        "--jev-necessity-gate",
        default="none",
        choices=["none", "second", "all"],
        help="per-pick necessity gate (exp 3): none = v1 behavior; second = a "
        "second pick only when the judge marked it required; all = every pick "
        "must be required (default: none)",
    )
    args = parser.parse_args(argv)

    if not args.database_url:
        parser.error(
            "--database-url (or ACI_DATABASE_URL) is required — "
            "this instrument never defaults to a database"
        )
    if args.cases == "none" and not args.organic:
        parser.error("nothing to evaluate: pass --cases dev and/or --organic PATH")

    judge: SkillJudge | None = None
    if args.reranker == "jev":
        judge = judge_from_config(
            base_url=args.jev_base_url,
            api_key=args.jev_api_key,
            model=args.jev_model,
            reasoning_effort=args.jev_reasoning_effort,
            timeout_s=args.jev_timeout,
            max_inflight=args.jev_max_inflight,
        )

    cases: list[tuple[str, Any]] = []
    if args.cases == "dev":
        cases.extend(("dev", case) for case in DEV_CASES)
        cases.extend(("kernel", case) for case in KERNEL_QUERY_CASES)
    if args.organic:
        cases.extend(("organic", case) for case in load_organic_cases(args.organic))

    router, engine = build_router(
        args.database_url,
        reranker_name=args.reranker,
        judge=judge,
        embedder_name=args.embedder,
        candidate_limit=args.jev_candidates,
        max_select=args.jev_max_select,
        on_failure=args.jev_on_failure,
        necessity_gate=args.jev_necessity_gate,
    )
    rows: list[dict[str, Any]] = []
    try:
        for set_name, case in cases:
            rows.append(router.run_case(case, set_name=set_name))
    finally:
        engine.dispose()

    from aci.adapters.outbound.model_provider.judge import JEV_PROMPT_VERSION

    report = build_report(
        rows,
        reranker=args.reranker,
        embedder_model=router.retriever.model_id,
        database=make_url(args.database_url).database or "?",
        organic_source=args.organic,
        config={
            "prompt_version": JEV_PROMPT_VERSION if args.reranker == "jev" else None,
            "reasoning_effort": args.jev_reasoning_effort if args.reranker == "jev" else None,
            "max_select": args.jev_max_select,
            "candidate_limit": args.jev_candidates,
            "necessity_gate": args.jev_necessity_gate,
            "timeout_s": args.jev_timeout,
            "on_failure": args.jev_on_failure,
        },
    )
    print(render_summary(report))
    print()
    print(render_table(rows))
    payload = json.dumps(report, indent=1) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"\n# JSON written to {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
