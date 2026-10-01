"""READ-ONLY routing replay: the real §14 stages, zero telemetry persisted.

Committed replay instrument for paired A/B on composer/ranking changes
(plan §3.1, docs/plans/aci-improvement-2026-10.md; §34: every
reranker/embedder/composer change carries a paired A/B on the held-out set
plus the dev sets). Replays the REAL stage classes in the same composition
as the REST Container (adapters/inbound/rest/wiring.py):

    ProductionCandidateLoader -> DefaultEligibilityPolicy
    -> EmbeddingRetriever(fastembed) -> HeuristicReranker
    -> DefaultDependencyResolver -> MinimalBundleComposer(ArtifactPayloadSizes)

The ONLY thing skipped vs RouteCapabilitiesService is telemetry persistence
(route_runs/bundles) — that is the point: routing is measured without
writing anything. The engine connects with
``default_transaction_read_only=on`` and the script verifies the setting
before running (fail closed), so an accidental write — e.g. the retriever
lazily indexing a missing document — aborts loudly instead of mutating the
database. A pre-check fails EARLY when the corpus is not fully indexed under
the chosen embedder (that replay would need to write). The relations repo is
wrapped in a per-run memo (``_MemoRelations``): the resolver's per-resolve
memo (main ca01d10) still issues one lookup per distinct capability id per
case, which is thousands of roundtrips over a remote registry for zero
behavioral difference — the replay measures ranking/composition, not DB
latency.

Per case the replay records: expected ids, the reranked candidates
(id, score), the composed bundle items, the composer stop_reason +
spent tokens + skipped-for-budget count, hit@1 / hit@3 /
hit-in-bundle, the empty-bundle flag, misroutes, whether an irrelevant item
lands above a relevant one in the bundle, and abstention correctness for the
cases whose CORRECT answer is an empty bundle. Output: JSON (rows +
aggregates) and a markdown table. The JSON is DETERMINISTIC — no timestamps,
no generated ids — so two runs of the same code over the same corpus are
byte-identical (the NaN-fix byte-identity proof relies on this).

``--composer {current,skip-oversized}`` selects the MinimalBundleComposer
oversized policy (default ``current`` = the shipped stop-at-first-oversized
rule). No weights, thresholds, or ranking logic are configurable: this is a
measurement instrument, not a tuning harness (§34 — the held-out set must
never be tuned against).

Usage:
    .venv/bin/python scripts/routing_replay.py \\
        --database-url postgresql+psycopg://... --cases all \\
        --composer current --output data/routing-replay/current.json
    .venv/bin/python scripts/routing_replay.py ... --composer skip-oversized \\
        --output data/routing-replay/skip.json
    .venv/bin/python scripts/routing_replay.py --compare current.json skip.json

``--compare`` needs no database: it diffs two replay reports and evaluates
the PRE-REGISTERED adoption criteria below.

PRE-REGISTERED DECISION (written before any A/B ran; plan §3.1): recommend
adopting ``skip-oversized`` as the composer default ONLY IF all three hold
on the paired replay (heldout 30 + dev 31 + kernel 29, budget 8000
unchanged, no weight/ranking change):
  (a) held-out hit-in-bundle IMPROVES (more heldout cases with >= 1 relevant
      item in the bundle under skip than under current);
  (b) ZERO dev/kernel cases flip from hit to miss (hit-in-bundle);
  (c) NO case gains an irrelevant item ranked above a relevant one in the
      bundle (bundle order) that current did not have.
Otherwise ``current`` stays the default and the variant stays available
behind the option. §34: this is one structural rule judged on a paired
replay — directional, routing-side only, no acceptance tests execute.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import create_engine, make_url, text
from sqlalchemy.engine import Connection, Engine

# Run THIS checkout's code, not whatever the shared .venv's editable install
# points at: a replay must measure the router of the worktree it lives in.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aci.adapters.outbound.model_provider.hashing import HashingEmbedder  # noqa: E402
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
from aci.application.list_candidates import ProductionCandidateLoader  # noqa: E402
from aci.application.payload_sizes import ArtifactPayloadSizes  # noqa: E402
from aci.domain.capability.models import (  # noqa: E402
    CapabilityRelation,
    RouteCapabilitiesCommand,
    TaskContext,
)
from aci.domain.policy.models import (  # noqa: E402
    ClientDescriptor,
    EligibleCandidate,
    PolicyRules,
    ProtocolDescriptor,
    RequestContext,
)
from aci.domain.routing.models import (  # noqa: E402
    CompositionOversizedPolicy,
    CompositionResult,
    RerankResult,
    RetrievalResult,
    TaskDescriptor,
)
from aci.evaluation.dev_cases import DEV_CASES  # noqa: E402
from aci.evaluation.heldout_cases import EMPTY_BUNDLE_CASE_IDS, HELDOUT_CASES  # noqa: E402
from aci.evaluation.kernel_query_cases import KERNEL_QUERY_CASES  # noqa: E402
from aci.evaluation.metrics import result_metrics  # noqa: E402
from aci.evaluation.models import BenchmarkCase  # noqa: E402
from aci.routing.composer import MinimalBundleComposer  # noqa: E402
from aci.routing.dependencies import DefaultDependencyResolver  # noqa: E402
from aci.routing.eligibility import DefaultEligibilityPolicy  # noqa: E402
from aci.routing.rerankers.heuristic import HeuristicReranker  # noqa: E402
from aci.routing.retrieval import EmbeddingRetriever  # noqa: E402

READ_ONLY_OPTION = "-c default_transaction_read_only=on"
#: The §14 pipeline's retrieval limit (route_capabilities.py) and item cap.
RETRIEVAL_LIMIT = 30
PRINCIPAL = "routing-replay"

CaseSetId = Literal["heldout", "dev", "kernel", "all"]
ComposerVariantId = Literal["current", "skip-oversized"]
EmbedderId = Literal["fastembed", "hashing"]

#: The pre-registered adoption criteria, printed with every --compare so the
#: decision rule travels with the measurement (see module docstring).
PREREGISTERED_DECISION: tuple[str, ...] = (
    "(a) heldout hit_in_bundle(skip) > heldout hit_in_bundle(current)",
    "(b) dev+kernel hit->miss flips == 0",
    "(c) irrelevant-above-relevant gains == 0",
    "adopt skip-oversized as default ONLY if (a) AND (b) AND (c)",
)


def case_sets(name: CaseSetId) -> list[tuple[str, list[BenchmarkCase]]]:
    """(set_name, cases) pairs in a deterministic order."""
    sets: list[tuple[str, list[BenchmarkCase]]] = []
    if name in ("heldout", "all"):
        sets.append(("heldout", HELDOUT_CASES))
    if name in ("dev", "all"):
        sets.append(("dev", DEV_CASES))
    if name in ("kernel", "all"):
        sets.append(("kernel", KERNEL_QUERY_CASES))
    if not sets:
        raise ValueError(f"unknown case set {name!r}")
    return sets


# ---------------------------------------------------------------------------
# Pure per-case record + aggregation (unit-tested with fakes, no DB).
# ---------------------------------------------------------------------------


def _dedup(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def irrelevant_above_relevant(
    bundle: list[str], expected: list[str], irrelevant: list[str]
) -> bool:
    """True when an irrelevant item sits ABOVE a relevant one in the bundle.

    Bundle order is rank order, so this is the harmful misroute shape: the
    confuser displaces attention before the relevant skill loads.
    """
    exp = set(expected)
    irr = set(irrelevant)
    for position, capability_id in enumerate(bundle):
        if capability_id not in irr:
            continue
        if any(other in exp for other in bundle[position + 1 :]):
            return True
    return False


def build_row(
    *,
    case: BenchmarkCase,
    set_name: str,
    retrieval: RetrievalResult,
    rerank: RerankResult,
    composition: CompositionResult,
) -> dict[str, Any]:
    """The deterministic per-case record (JSON-ready; nothing generated here)."""
    ranked_ids = [r.candidate.capability_id for r in rerank.ranked]
    bundle_ids = [item.capability_id for item in composition.bundle.items]
    expected = _dedup([*case.relevant_strong, *case.relevant_acceptable])
    irrelevant = list(case.relevant_irrelevant)
    expects_empty = case.case_id in EMPTY_BUNDLE_CASE_IDS
    metrics = result_metrics(case, bundle_ids, latency_ms=None)
    trace = composition.trace
    return {
        "case_id": case.case_id,
        "set": set_name,
        "category": case.category,
        "fixture": case.fixture,
        "expects_empty": expects_empty,
        "expected_ids": expected,
        "irrelevant_ids": irrelevant,
        "ranked": [
            {"capability_id": r.candidate.capability_id, "score": r.score} for r in rerank.ranked
        ],
        "bundle_items": bundle_ids,
        "stop_reason": trace.stop_reason,
        "oversized_policy": trace.oversized_policy,
        "spent_tokens": trace.spent_tokens,
        "skipped_for_budget": sum(
            1 for c in trace.items if c.excluded_reason == "max_context_tokens"
        ),
        "max_items": trace.max_items,
        "max_context_tokens": trace.max_context_tokens,
        "retrieval_model": retrieval.trace.model_id,
        "retrieval_indexed": retrieval.trace.indexed_count,
        # nonfinite_scores lands with the NaN-boundary fix; getattr keeps this
        # instrument runnable on the pre-fix code so the fix's byte-identity
        # proof (replay before vs after) can use the SAME instrument.
        "retrieval_nonfinite": getattr(retrieval.trace, "nonfinite_scores", 0),
        "rerank_version": rerank.trace.version,
        "composer_version": trace.version,
        "hit1": bool(ranked_ids) and ranked_ids[0] in expected,
        "hit3": any(cid in expected for cid in ranked_ids[:3]),
        "hit_in_bundle": any(cid in expected for cid in bundle_ids),
        "empty_bundle": not bundle_ids,
        "abstention_correct": (not bundle_ids) if expects_empty else None,
        "misroutes_in_bundle": metrics["misroutes"],
        "recall": metrics["recall"],
        "irrelevant_above_relevant": irrelevant_above_relevant(bundle_ids, expected, irrelevant),
    }


@dataclass
class ReplayRouter:
    """The real §14 stage objects + the once-per-run loaded candidate set.

    Fields are typed as the concrete stage classes on purpose: the DB
    builder wires exactly the composition the REST Container wires, and the
    unit tests wire the SAME classes over fakes — the replay never
    re-implements a stage (that would measure a copy, not the router).
    """

    eligibility: DefaultEligibilityPolicy
    retriever: EmbeddingRetriever
    reranker: HeuristicReranker
    resolver: DefaultDependencyResolver
    composer: MinimalBundleComposer
    rules: PolicyRules
    candidates: list[EligibleCandidate]

    def run_case(self, case: BenchmarkCase, *, set_name: str) -> dict[str, Any]:
        """One case through the real pipeline; NOTHING is persisted."""
        envelope = RequestContext(
            # Deterministic ids: the canary split (§27) is reproducible and
            # two runs of the same code produce byte-identical reports.
            request_id=f"req_replay_{case.case_id}",
            trace_id=f"trc_replay_{case.case_id}",
            principal_id=PRINCIPAL,
            client=ClientDescriptor(type="benchmark-harness"),
            protocol=ProtocolDescriptor(type="benchmark", version="1"),
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
            resolution, command, route_run_id=f"replay_{case.case_id}", now=datetime.now(UTC)
        )
        return build_row(
            case=case,
            set_name=set_name,
            retrieval=retrieval,
            rerank=rerank,
            composition=composition,
        )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one case set (deterministic; means rounded for readability)."""
    measured = [r["recall"] for r in rows if r["recall"] is not None]
    empty_expected = [r for r in rows if r["expects_empty"]]
    spent = [r["spent_tokens"] for r in rows]
    return {
        "n_cases": len(rows),
        "hit1": sum(1 for r in rows if r["hit1"]),
        "hit3": sum(1 for r in rows if r["hit3"]),
        "hit_in_bundle": sum(1 for r in rows if r["hit_in_bundle"]),
        "empty_bundles": sum(1 for r in rows if r["empty_bundle"]),
        "abstention_correct": sum(1 for r in empty_expected if r["abstention_correct"]),
        "empty_expected_cases": len(empty_expected),
        "misroutes_in_bundle": sum(r["misroutes_in_bundle"] for r in rows),
        "irrelevant_above_relevant": sum(1 for r in rows if r["irrelevant_above_relevant"]),
        "total_spent_tokens": sum(spent),
        "mean_spent_tokens": round(sum(spent) / len(spent), 1) if spent else 0.0,
        "mean_recall": round(sum(measured) / len(measured), 3) if measured else None,
        "total_skipped_for_budget": sum(r["skipped_for_budget"] for r in rows),
    }


def build_report(
    rows: list[dict[str, Any]],
    *,
    composer_variant: ComposerVariantId,
    embedder_model: str,
    database: str,
) -> dict[str, Any]:
    """The full replay report — deterministic (see module docstring)."""
    sets: dict[str, Any] = {}
    for set_name in ("heldout", "dev", "kernel"):
        set_rows = [r for r in rows if r["set"] == set_name]
        if set_rows:
            sets[set_name] = summarize(set_rows)
    return {
        "composer_variant": composer_variant,
        "embedder_model": embedder_model,
        "database": database,
        "read_only": True,
        "n_cases": len(rows),
        "sets": sets,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Paired A/B comparison (pure; --compare needs no database).
# ---------------------------------------------------------------------------


def _set_hits(rows: list[dict[str, Any]], set_name: str) -> int:
    return sum(1 for r in rows if r["set"] == set_name and r["hit_in_bundle"])


def compare_reports(current: dict[str, Any], skip: dict[str, Any]) -> dict[str, Any]:
    """Paired per-case diff + the pre-registered criteria, evaluated."""
    cur = {r["case_id"]: r for r in current["rows"]}
    skp = {r["case_id"]: r for r in skip["rows"]}
    if cur.keys() != skp.keys():
        raise ValueError("the two reports cover different cases; nothing to compare")

    diffs: list[dict[str, Any]] = []
    hit_to_miss: list[str] = []
    miss_to_hit: list[str] = []
    i_above_r_gains: list[str] = []
    for case_id, c in cur.items():
        s = skp[case_id]
        if c["hit_in_bundle"] and not s["hit_in_bundle"]:
            hit_to_miss.append(case_id)
        if s["hit_in_bundle"] and not c["hit_in_bundle"]:
            miss_to_hit.append(case_id)
        if s["irrelevant_above_relevant"] and not c["irrelevant_above_relevant"]:
            i_above_r_gains.append(case_id)
        if (
            c["bundle_items"] == s["bundle_items"]
            and c["spent_tokens"] == s["spent_tokens"]
            and c["stop_reason"] == s["stop_reason"]
        ):
            continue
        diffs.append(
            {
                "case_id": case_id,
                "set": c["set"],
                "category": c["category"],
                "hit_in_bundle": f"{int(c['hit_in_bundle'])} -> {int(s['hit_in_bundle'])}",
                "current_bundle": c["bundle_items"],
                "skip_bundle": s["bundle_items"],
                "added": [i for i in s["bundle_items"] if i not in c["bundle_items"]],
                "removed": [i for i in c["bundle_items"] if i not in s["bundle_items"]],
                "spent_tokens": f"{c['spent_tokens']} -> {s['spent_tokens']}",
                "stop_reason": f"{c['stop_reason']} -> {s['stop_reason']}",
                "irrelevant_above_relevant": (
                    f"{int(c['irrelevant_above_relevant'])} "
                    f"-> {int(s['irrelevant_above_relevant'])}"
                ),
            }
        )

    sets: dict[str, Any] = {}
    for set_name in ("heldout", "dev", "kernel"):
        cur_rows = [r for r in current["rows"] if r["set"] == set_name]
        skp_rows = [r for r in skip["rows"] if r["set"] == set_name]
        if not cur_rows:
            continue
        sets[set_name] = {
            "n": len(cur_rows),
            "hit_in_bundle_current": _set_hits(current["rows"], set_name),
            "hit_in_bundle_skip": _set_hits(skip["rows"], set_name),
            "empty_current": sum(1 for r in cur_rows if r["empty_bundle"]),
            "empty_skip": sum(1 for r in skp_rows if r["empty_bundle"]),
            "abstention_correct_current": sum(1 for r in cur_rows if r["abstention_correct"]),
            "abstention_correct_skip": sum(1 for r in skp_rows if r["abstention_correct"]),
            "misroutes_current": sum(r["misroutes_in_bundle"] for r in cur_rows),
            "misroutes_skip": sum(r["misroutes_in_bundle"] for r in skp_rows),
            "irrelevant_above_relevant_current": sum(
                1 for r in cur_rows if r["irrelevant_above_relevant"]
            ),
            "irrelevant_above_relevant_skip": sum(
                1 for r in skp_rows if r["irrelevant_above_relevant"]
            ),
            "mean_spent_current": round(
                sum(r["spent_tokens"] for r in cur_rows) / len(cur_rows), 1
            ),
            "mean_spent_skip": round(sum(r["spent_tokens"] for r in skp_rows) / len(skp_rows), 1),
        }

    dev_kernel_hit_to_miss = [cid for cid in hit_to_miss if skp[cid]["set"] in ("dev", "kernel")]
    criteria_a = _set_hits(skip["rows"], "heldout") > _set_hits(current["rows"], "heldout")
    criteria_b = not dev_kernel_hit_to_miss
    criteria_c = not i_above_r_gains
    return {
        "current_variant": current["composer_variant"],
        "skip_variant": skip["composer_variant"],
        "n_cases": len(cur),
        "n_changed": len(diffs),
        "sets": sets,
        "hit_to_miss": hit_to_miss,
        "miss_to_hit": miss_to_hit,
        "irrelevant_above_relevant_gains": i_above_r_gains,
        "criteria": {
            "preregistered": list(PREREGISTERED_DECISION),
            "a_heldout_hit_in_bundle_improves": criteria_a,
            "b_zero_dev_kernel_hit_to_miss": criteria_b,
            "c_zero_irrelevant_above_relevant_gains": criteria_c,
            "adopt_skip_oversized": criteria_a and criteria_b and criteria_c,
        },
        "diffs": diffs,
    }


# ---------------------------------------------------------------------------
# Rendering (markdown).
# ---------------------------------------------------------------------------


def render_summary(report: dict[str, Any]) -> str:
    lines = [
        f"# routing replay — composer={report['composer_variant']} "
        f"embedder={report['embedder_model']}",
        f"# database: {report['database']} (read-only connection; persisted nothing)",
        "",
        "| set | n | hit@1 | hit@3 | hitB | empty | abst-ok | misr | i>a |"
        " mean spent | mean recall |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for set_name, s in report["sets"].items():
        lines.append(
            f"| {set_name} | {s['n_cases']} | {s['hit1']} | {s['hit3']} | {s['hit_in_bundle']} "
            f"| {s['empty_bundles']} | {s['abstention_correct']}/{s['empty_expected_cases']} "
            f"| {s['misroutes_in_bundle']} | {s['irrelevant_above_relevant']} "
            f"| {s['mean_spent_tokens']} | {s['mean_recall']} |"
        )
    return "\n".join(lines)


def render_table(rows: list[dict[str, Any]]) -> str:
    """Per-case markdown table (compact; the JSON carries the full detail)."""
    lines = [
        "| case | set | hit@1 | hit@3 | hitB | empty | abst | misr | i>a | spent | stop | bundle |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        abst = "-" if r["abstention_correct"] is None else int(r["abstention_correct"])
        lines.append(
            f"| {r['case_id']} | {r['set']} | {int(r['hit1'])} | {int(r['hit3'])} "
            f"| {int(r['hit_in_bundle'])} | {int(r['empty_bundle'])} | {abst} "
            f"| {r['misroutes_in_bundle']} | {int(r['irrelevant_above_relevant'])} "
            f"| {r['spent_tokens']} | {r['stop_reason'] or '-'} "
            f"| {', '.join(r['bundle_items']) or '(empty)'} |"
        )
    return "\n".join(lines)


def render_compare(compare: dict[str, Any]) -> str:
    lines = [
        f"# paired A/B — current={compare['current_variant']} vs skip={compare['skip_variant']}",
        f"# cases: {compare['n_cases']} (changed: {compare['n_changed']})",
        "",
        "| set | n | hitB cur | hitB skip | empty cur | empty skip | misr cur | misr skip "
        "| i>a cur | i>a skip | mean spent cur | mean spent skip |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for set_name, s in compare["sets"].items():
        lines.append(
            f"| {set_name} | {s['n']} | {s['hit_in_bundle_current']} | {s['hit_in_bundle_skip']} "
            f"| {s['empty_current']} | {s['empty_skip']} | {s['misroutes_current']} "
            f"| {s['misroutes_skip']} | {s['irrelevant_above_relevant_current']} "
            f"| {s['irrelevant_above_relevant_skip']} | {s['mean_spent_current']} "
            f"| {s['mean_spent_skip']} |"
        )
    lines.append("")
    lines.append("## pre-registered decision")
    for item in compare["criteria"]["preregistered"]:
        lines.append(f"- {item}")
    c = compare["criteria"]
    lines.append(f"- (a) heldout hit-in-bundle improves: {c['a_heldout_hit_in_bundle_improves']}")
    lines.append(f"- (b) zero dev/kernel hit->miss flips: {c['b_zero_dev_kernel_hit_to_miss']}")
    lines.append(
        f"- (c) zero irrelevant-above-relevant gains: {c['c_zero_irrelevant_above_relevant_gains']}"
    )
    lines.append(f"- ADOPT skip-oversized as default: {c['adopt_skip_oversized']}")
    lines.append("")
    lines.append(
        f"hit->miss: {compare['hit_to_miss'] or '(none)'} · "
        f"miss->hit: {compare['miss_to_hit'] or '(none)'} · "
        f"i>a gains: {compare['irrelevant_above_relevant_gains'] or '(none)'}"
    )
    lines.append("")
    lines.append("## changed cases")
    if compare["diffs"]:
        lines.append("| case | set | hitB | removed | added | spent | stop | i>a |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for d in compare["diffs"]:
            lines.append(
                f"| {d['case_id']} | {d['set']} | {d['hit_in_bundle']} "
                f"| {', '.join(d['removed']) or '-'} | {', '.join(d['added']) or '-'} "
                f"| {d['spent_tokens']} | {d['stop_reason']} | {d['irrelevant_above_relevant']} |"
            )
    else:
        lines.append("(no case changed)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB layer — read-only; the only engine this script may open.
# ---------------------------------------------------------------------------


def open_read_only_engine(database_url: str) -> Engine:
    """Engine whose every connection opens read-only (server-side)."""
    return create_engine(
        database_url, pool_pre_ping=True, connect_args={"options": READ_ONLY_OPTION}
    )


def assert_read_only(conn: Connection) -> None:
    """Fail closed unless the server confirms the read-only transaction mode."""
    setting = conn.execute(text("SELECT current_setting('default_transaction_read_only')")).scalar()
    if setting != "on":
        raise RuntimeError(
            "refusing to replay: default_transaction_read_only is "
            f"{setting!r} (expected 'on') — this script never writes"
        )


def _assert_fully_indexed(
    candidates: list[EligibleCandidate],
    embeddings: SqlAlchemyEmbeddingRepository,
    model_id: str,
) -> None:
    """Fail EARLY when a replay would need to WRITE (cache miss / stale digest).

    The retriever lazily indexes on a miss; on the read-only connection that
    write fails mid-replay with a raw driver error. This check turns it into
    an actionable message before any case runs.
    """
    expected = {(c.capability_id, c.version): c.digest for c in candidates if c.kind == "skill"}
    indexed = {
        (d.capability_id, d.version): d
        for d in embeddings.get_indexed_documents(list(expected), model_id)
    }
    missing = [
        pair
        for pair, digest in expected.items()
        if indexed.get(pair) is None or indexed[pair].digest != digest
    ]
    if missing:
        raise RuntimeError(
            f"{len(missing)}/{len(expected)} eligible skill documents are not indexed under "
            f"{model_id!r} (or stale by digest): the replay would need to WRITE them, and "
            "this connection is read-only (fail closed). Index the corpus from a "
            "write-capable process first, then re-run."
        )


class _MemoRelations:
    """Per-run memo around the relations repo (read-only rows).

    ``DefaultDependencyResolver`` memoizes per resolve() since main ca01d10
    (one lookup per distinct capability id per call); this extends it to one
    lookup per distinct id per RUN — the replay resolves 90 cases over the
    same 36-candidate corpus, so the per-run memo saves ~3,200 more
    roundtrips against a remote registry. Zero behavioral difference: the
    resolver is a pure function of what the repo returns, and the replay
    measures ranking/composition, not DB latency. Instrument optimization,
    not a product change (the real service reads live, §46).
    """

    def __init__(self, inner: SqlAlchemyRelationRepository) -> None:
        self._inner = inner
        self._memo: dict[str, list[CapabilityRelation]] = {}

    def list_relations(self, source_capability_id: str) -> list[CapabilityRelation]:
        if source_capability_id not in self._memo:
            self._memo[source_capability_id] = self._inner.list_relations(source_capability_id)
        return self._memo[source_capability_id]


def build_router(
    database_url: str,
    *,
    composer_variant: ComposerVariantId,
    embedder_name: EmbedderId,
) -> tuple[ReplayRouter, Engine]:
    """Wire the REAL §14 stages over a read-only engine — the same
    composition as the REST Container (adapters/inbound/rest/wiring.py)."""
    engine = open_read_only_engine(database_url)
    with engine.connect() as conn:
        assert_read_only(conn)
    sessions = make_session_factory(engine)

    capabilities = SqlAlchemyCapabilityRepository(sessions)
    releases = SqlAlchemyReleaseRepository(sessions)
    artifacts = SqlAlchemyArtifactStore(sessions)
    licenses = SqlAlchemyLicenseAssessmentRepository(sessions)
    securities = SqlAlchemySecurityAssessmentRepository(sessions)
    policy_snapshots = SqlAlchemyPolicySnapshotRepository(sessions)
    relations = SqlAlchemyRelationRepository(sessions)
    embeddings = SqlAlchemyEmbeddingRepository(sessions)

    loader = ProductionCandidateLoader(capabilities, releases, licenses, securities)
    embedder: HashingEmbedder | FastEmbedEmbedder = (
        FastEmbedEmbedder() if embedder_name == "fastembed" else HashingEmbedder()
    )
    oversized: CompositionOversizedPolicy = (
        "skip" if composer_variant == "skip-oversized" else "stop"
    )
    snapshot = policy_snapshots.latest_snapshot()
    rules = snapshot.rules if snapshot is not None else PolicyRules()
    candidates = loader.load(rules.required_channel)

    router = ReplayRouter(
        eligibility=DefaultEligibilityPolicy(),
        retriever=EmbeddingRetriever(capabilities, embedder, embeddings),
        reranker=HeuristicReranker(),
        # _MemoRelations: same rows, same resolver answers, thousands fewer
        # roundtrips per run against a remote registry (see its docstring).
        resolver=DefaultDependencyResolver(_MemoRelations(relations), releases),
        composer=MinimalBundleComposer(
            ArtifactPayloadSizes(capabilities, artifacts), oversized_policy=oversized
        ),
        rules=rules,
        candidates=candidates,
    )
    _assert_fully_indexed(candidates, embeddings, embedder.model_id)
    return router, engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--cases", default="all", choices=["heldout", "dev", "kernel", "all"])
    parser.add_argument(
        "--composer",
        default="current",
        choices=["current", "skip-oversized"],
        help="MinimalBundleComposer oversized policy (default: current)",
    )
    parser.add_argument(
        "--embedder",
        default="fastembed",
        choices=["fastembed", "hashing"],
        help="embedder whose vectors the replay retrieves with (default: fastembed)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ACI_DATABASE_URL"),
        help="registry DB URL (no silent default: pass it or set ACI_DATABASE_URL)",
    )
    parser.add_argument("--output", help="write the JSON report to this path")
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("CURRENT_JSON", "SKIP_JSON"),
        help="diff two replay reports and evaluate the pre-registered criteria (no DB)",
    )
    args = parser.parse_args(argv)

    if args.compare:
        current = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        skip = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        compare = compare_reports(current, skip)
        print(render_compare(compare))
        if args.output:
            Path(args.output).write_text(json.dumps(compare, indent=1) + "\n", encoding="utf-8")
        return 0

    if not args.database_url:
        parser.error(
            "--database-url (or ACI_DATABASE_URL) is required — "
            "this instrument never defaults to a database"
        )

    router, engine = build_router(
        args.database_url,
        composer_variant=args.composer,
        embedder_name=args.embedder,
    )
    rows: list[dict[str, Any]] = []
    try:
        for set_name, cases in case_sets(args.cases):
            for case in cases:
                rows.append(router.run_case(case, set_name=set_name))
    finally:
        engine.dispose()

    report = build_report(
        rows,
        composer_variant=args.composer,
        embedder_model=router.retriever.model_id,
        database=make_url(args.database_url).database or "?",
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
