"""Unit tests for scripts/routing_replay.py — the §3.1 paired-A/B instrument.

Everything here exercises the PURE parts (row building, aggregation, the
paired compare, the pre-registered criteria, rendering) plus one end-to-end
run_case over the REAL stage classes with fakes — no DB. The DB layer
(build_router) is the only part not covered: it is a thin read-only wiring
of the same repos the integration tests already cover.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from aci.adapters.outbound.model_provider.hashing import HashingEmbedder
from aci.domain.capability.models import (
    BundleBudget,
    BundleItem,
    CapabilityBundle,
    CapabilityVersion,
    SkillSpec,
)
from aci.domain.policy.models import EligibleCandidate, PolicyRules
from aci.domain.routing.models import (
    ComposedItemCost,
    CompositionResult,
    CompositionTrace,
    RankedCandidate,
    RerankResult,
    RerankTrace,
    RetrievalResult,
    RetrievalTrace,
)
from aci.evaluation.heldout_cases import EMPTY_BUNDLE_CASE_IDS, HELDOUT_CASES
from aci.evaluation.metrics import result_metrics
from aci.evaluation.models import BenchmarkCase
from aci.routing.composer import MinimalBundleComposer
from aci.routing.dependencies import DefaultDependencyResolver
from aci.routing.eligibility import DefaultEligibilityPolicy
from aci.routing.rerankers.heuristic import HeuristicReranker
from aci.routing.retrieval import EmbeddingRetriever

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import routing_replay as rr  # noqa: E402

DIGEST = "sha256:" + "ab" * 32
NOW = datetime(2026, 10, 2, tzinfo=UTC)


# ---------- fakes (same shapes the existing unit suites use) ----------


class FakeEmbeddings:
    """In-memory EmbeddingRepository (test_retrieval.py shape)."""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], Any] = {}
        self.vectors: dict[tuple[str, str, str], list[float]] = {}
        self.put_count = 0

    def put_document(self, document: Any, vector: list[float]) -> None:
        self.docs[(document.capability_id, document.version)] = document
        self.vectors[(document.capability_id, document.version, document.model_id)] = vector
        self.put_count += 1

    def get_indexed_documents(self, pairs: list[tuple[str, str]], model_id: str) -> list[Any]:
        return [
            doc
            for (cap, ver), doc in self.docs.items()
            if (cap, ver, model_id) in self.vectors and (cap, ver) in set(pairs)
        ]

    def search(
        self, query_vector: list[float], pairs: list[tuple[str, str]], *, model_id: str, limit: int
    ) -> list[Any]:
        from aci.domain.routing.models import RetrievedDocument

        hits = [
            RetrievedDocument(
                capability_id=cap,
                version=ver,
                score=0.5,  # uniform: ranking is the reranker's job here
            )
            for cap, ver in pairs
            if (cap, ver, model_id) in self.vectors
        ]
        return hits[:limit]


class FakeCapabilities:
    def __init__(self, versions: list[CapabilityVersion]) -> None:
        self._versions = {(v.capability_id, v.version): v for v in versions}

    def get_version(self, capability_id: str, version: str) -> CapabilityVersion | None:
        return self._versions.get((capability_id, version))


class FakeRelations:
    def list_relations(self, source_capability_id: str) -> list[Any]:
        return []


class FakeReleases:
    def get_release(self, capability_id: str, channel: str) -> None:
        return None


class FakePayloadSizes:
    def __init__(self, sizes: dict[str, int]) -> None:
        self._sizes = sizes

    def entry_sizes(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
        return {p: self._sizes[p[0]] for p in pairs if p[0] in self._sizes}


def make_version(capability_id: str, description: str) -> CapabilityVersion:
    return CapabilityVersion.model_validate(
        {
            "capability_id": capability_id,
            "version": "1.0.0",
            "kind": "skill",
            "content_digest": DIGEST,
            "created_at": NOW,
            "display_name": capability_id,
            "description": description,
            "spec": SkillSpec(provides=["x"]),
        }
    )


def make_candidate(capability_id: str) -> EligibleCandidate:
    return EligibleCandidate(
        capability_id=capability_id,
        version="1.0.0",
        digest=DIGEST,
        kind="skill",
        channel="production",
        status="active",
    )


def make_router(
    versions: list[CapabilityVersion],
    candidates: list[EligibleCandidate],
    *,
    sizes: dict[str, int] | None = None,
    oversized: Any = "stop",
) -> rr.ReplayRouter:
    """The REAL stage classes over fakes — the same wiring build_router does."""
    return rr.ReplayRouter(
        eligibility=DefaultEligibilityPolicy(),
        retriever=EmbeddingRetriever(
            FakeCapabilities(versions),  # type: ignore[arg-type]
            HashingEmbedder(),
            FakeEmbeddings(),  # type: ignore[arg-type]
        ),
        reranker=HeuristicReranker(),
        resolver=DefaultDependencyResolver(FakeRelations(), FakeReleases()),  # type: ignore[arg-type]
        composer=MinimalBundleComposer(
            FakePayloadSizes(sizes or {}),  # type: ignore[arg-type]
            oversized_policy=oversized,
        ),
        rules=PolicyRules(),
        candidates=candidates,
    )


# ---------- case sets ----------


def test_case_sets_select_and_all() -> None:
    (heldout,) = [c for _, c in rr.case_sets("heldout")]
    assert heldout is HELDOUT_CASES
    all_sets = rr.case_sets("all")
    assert [name for name, _ in all_sets] == ["heldout", "dev", "kernel"]
    assert sum(len(cases) for _, cases in all_sets) == 30 + 31 + 29


def test_case_sets_unknown_name_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown case set"):
        rr.case_sets("bogus")  # type: ignore[arg-type]


# ---------- pure helpers ----------


def test_irrelevant_above_relevant_detects_the_harmful_order() -> None:
    expected = ["relevant-a"]
    irrelevant = ["confuser"]
    # confuser above a relevant item -> True
    assert rr.irrelevant_above_relevant(["confuser", "relevant-a"], expected, irrelevant)
    # confuser below every relevant item -> False
    assert not rr.irrelevant_above_relevant(["relevant-a", "confuser"], expected, irrelevant)
    # confuser alone -> nothing relevant below it -> False
    assert not rr.irrelevant_above_relevant(["confuser"], expected, irrelevant)
    # no confuser -> False
    assert not rr.irrelevant_above_relevant(["relevant-a"], expected, irrelevant)


# ---------- build_row (hand-built stage outputs) ----------


def _retrieval(model_id: str = "hashing-384-v2") -> RetrievalResult:
    return RetrievalResult(
        candidates=[],
        trace=RetrievalTrace(
            eligible_count=3,
            indexed_count=0,
            searched_count=3,
            returned_count=0,
            limit=30,
            model_id=model_id,
        ),
    )


def _ranked(capability_id: str, score: float, rank: int) -> RankedCandidate:
    return RankedCandidate(
        candidate=make_candidate(capability_id),
        score=score,
        retrieval_score=score / 2,
        rank=rank,
        document_text="",
    )


def _rerank(ranked: list[RankedCandidate]) -> RerankResult:
    return RerankResult(
        ranked=ranked,
        trace=RerankTrace(
            implementation="heuristic-reranker",
            version="3",
            input_count=0,
            output_count=len(ranked),
        ),
    )


def _composition(
    bundle_ids: list[str],
    *,
    spent: int,
    stop_reason: Any,
    policy: Any = "stop",
    excluded: list[tuple[str, Any]] | None = None,
) -> CompositionResult:
    items = [
        BundleItem(
            capability_id=cid,
            version="1.0.0",
            digest=DIGEST,
            kind="skill",
        )
        for cid in bundle_ids
    ]
    costs = [
        ComposedItemCost(
            capability_id=cid,
            version="1.0.0",
            estimated_tokens=10,
            estimate_source="artifact_entry",
            included=reason is None,
            excluded_reason=reason,
        )
        for cid, reason in (excluded or [(cid, None) for cid in bundle_ids])
    ]
    bundle = CapabilityBundle(
        bundle_id="bun_x",
        route_run_id="replay_x",
        created_at=NOW,
        items=items,
        execution_order=bundle_ids,
        budget=BundleBudget(),
    )
    trace = CompositionTrace(
        implementation="minimal-bundle-composer",
        version="2",
        max_items=5,
        max_context_tokens=8000,
        spent_tokens=spent,
        stop_reason=stop_reason,
        oversized_policy=policy,
        items=costs,
    )
    return CompositionResult(bundle=bundle, trace=trace)


def test_build_row_measures_hits_empty_and_misroutes() -> None:
    case = BenchmarkCase(
        case_id="case-hit",
        category="debugging",
        fixture="heldout/user-task",
        task_text="find the memory leak",
        relevant_strong=["relevant-a"],
        relevant_acceptable=["relevant-b"],
        relevant_irrelevant=["confuser"],
    )
    row = rr.build_row(
        case=case,
        set_name="heldout",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("relevant-a", 0.9, 1), _ranked("confuser", 0.8, 2)]),
        composition=_composition(["relevant-a", "confuser"], spent=20, stop_reason=None),
    )
    assert row["expected_ids"] == ["relevant-a", "relevant-b"]
    assert row["hit1"] and row["hit3"] and row["hit_in_bundle"]
    assert row["empty_bundle"] is False
    assert row["abstention_correct"] is None  # not an empty-expectation case
    assert row["misroutes_in_bundle"] == 1
    assert row["irrelevant_above_relevant"] is False  # confuser is BELOW the relevant item
    assert row["stop_reason"] is None
    assert row["spent_tokens"] == 20
    assert row["skipped_for_budget"] == 0
    assert row["oversized_policy"] == "stop"
    assert [r["capability_id"] for r in row["ranked"]] == ["relevant-a", "confuser"]
    assert row["recall"] == pytest.approx(0.5)  # 1 of {relevant-a, relevant-b}
    # deterministic: a second identical build is byte-identical JSON
    again = rr.build_row(
        case=case,
        set_name="heldout",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("relevant-a", 0.9, 1), _ranked("confuser", 0.8, 2)]),
        composition=_composition(["relevant-a", "confuser"], spent=20, stop_reason=None),
    )
    assert json.dumps(row, sort_keys=True) == json.dumps(again, sort_keys=True)


def test_build_row_counts_budget_skips_from_excluded_reasons() -> None:
    case = BenchmarkCase(
        case_id="case-skip",
        category="debugging",
        fixture="heldout/user-task",
        task_text="t",
        relevant_strong=["small"],
    )
    row = rr.build_row(
        case=case,
        set_name="heldout",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("big", 0.9, 1), _ranked("small", 0.8, 2)]),
        composition=_composition(
            ["small"],
            spent=10,
            stop_reason="max_context_tokens",
            policy="skip",
            excluded=[("big", "max_context_tokens"), ("small", None)],
        ),
    )
    assert row["skipped_for_budget"] == 1
    assert row["oversized_policy"] == "skip"
    assert row["stop_reason"] == "max_context_tokens"
    assert row["hit_in_bundle"]


def test_build_row_empty_expectation_case_measures_abstention() -> None:
    case = next(c for c in HELDOUT_CASES if c.case_id in EMPTY_BUNDLE_CASE_IDS)
    assert case.relevant_strong == []
    row = rr.build_row(
        case=case,
        set_name="heldout",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("writing-plans", 0.9, 1)]),
        composition=_composition([], spent=0, stop_reason="max_context_tokens"),
    )
    assert row["expects_empty"] is True
    assert row["empty_bundle"] is True
    assert row["abstention_correct"] is True
    assert row["recall"] is None  # nothing annotated -> unmeasured, never 0
    # a non-empty bundle on an empty-expectation case is a failed abstention
    row2 = rr.build_row(
        case=case,
        set_name="heldout",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("writing-plans", 0.9, 1)]),
        composition=_composition(["writing-plans"], spent=10, stop_reason=None),
    )
    assert row2["abstention_correct"] is False
    assert row2["misroutes_in_bundle"] == 1  # writing-plans is an annotated confuser


def test_build_row_irrelevant_above_relevant_in_the_bundle() -> None:
    case = BenchmarkCase(
        case_id="case-order",
        category="debugging",
        fixture="heldout/user-task",
        task_text="t",
        relevant_strong=["relevant-a"],
        relevant_irrelevant=["confuser"],
    )
    row = rr.build_row(
        case=case,
        set_name="heldout",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("confuser", 0.9, 1), _ranked("relevant-a", 0.8, 2)]),
        composition=_composition(["confuser", "relevant-a"], spent=20, stop_reason=None),
    )
    assert row["irrelevant_above_relevant"] is True


# ---------- run_case end-to-end over the real stages (fakes, no DB) ----------


def test_run_case_replays_the_real_pipeline_without_persisting() -> None:
    versions = [
        make_version("diagnosing-bugs", "root cause analysis for python debugging"),
        make_version("debugging", "systematic debugging investigation"),
        make_version("theme-factory", "visual themes and palettes"),
    ]
    candidates = [make_candidate(v.capability_id) for v in versions]
    router = make_router(versions, candidates, sizes={"diagnosing-bugs": 400})
    case = HELDOUT_CASES[0]  # heldout-debug-memory: strong=diagnosing-bugs, systematic-debugging

    row = router.run_case(case, set_name="heldout")
    assert row["case_id"] == case.case_id
    assert row["set"] == "heldout"
    assert row["retrieval_model"] == "hashing-384-v2"
    assert row["max_items"] == 5
    assert row["max_context_tokens"] == 8000
    assert len(row["ranked"]) == 3  # all three eligible skills retrieved + reranked
    scores = [r["score"] for r in row["ranked"]]
    assert scores == sorted(scores, reverse=True)
    assert row["bundle_items"]  # the relevant skill(s) compose into the bundle
    assert row["spent_tokens"] > 0
    # The first call lazily indexes the fake store (retrieval_indexed=3);
    # from then on the cache is warm — the real replay starts pre-indexed
    # (build_router fails closed otherwise), so determinism is measured from
    # the warm state.
    assert row["retrieval_indexed"] == 3
    again = router.run_case(case, set_name="heldout")
    assert again["retrieval_indexed"] == 0
    assert json.dumps(row, sort_keys=True) != json.dumps(again, sort_keys=True)  # indexed differs
    third = router.run_case(case, set_name="heldout")
    assert json.dumps(again, sort_keys=True) == json.dumps(third, sort_keys=True)


def test_run_case_composer_variant_changes_the_bundle() -> None:
    """skip-oversized must reach the real composer through the router."""
    versions = [make_version("big", "debug python"), make_version("small", "testing")]
    candidates = [make_candidate(v.capability_id) for v in versions]
    sizes = {"big": 40_000, "small": 400}  # big = 10000 tokens > 8000 budget
    stop = make_router(versions, candidates, sizes=sizes, oversized="stop")
    skip = make_router(versions, candidates, sizes=sizes, oversized="skip")
    case = HELDOUT_CASES[0]

    row_stop = stop.run_case(case, set_name="heldout")
    row_skip = skip.run_case(case, set_name="heldout")
    assert row_stop["oversized_policy"] == "stop"
    assert row_skip["oversized_policy"] == "skip"
    # the structural rule under test: stop empties on an oversized rank-1,
    # skip keeps the later candidate that fits.
    assert row_stop["bundle_items"] != row_skip["bundle_items"]
    # stop mode excludes the oversized rank-1 AND everything after it for the
    # same budget reason; skip mode excludes only the oversized one.
    assert row_stop["skipped_for_budget"] == 2
    assert row_skip["skipped_for_budget"] == 1


# ---------- aggregation ----------


def _row(case_id: str, set_name: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "case_id": case_id,
        "set": set_name,
        "category": "debugging",
        "fixture": "heldout/user-task",
        "expects_empty": False,
        "expected_ids": ["relevant-a"],
        "irrelevant_ids": ["confuser"],
        "ranked": [],
        "bundle_items": ["relevant-a"],
        "stop_reason": None,
        "oversized_policy": "stop",
        "spent_tokens": 100,
        "skipped_for_budget": 0,
        "max_items": 5,
        "max_context_tokens": 8000,
        "retrieval_model": "m",
        "retrieval_indexed": 0,
        "retrieval_nonfinite": 0,
        "rerank_version": "3",
        "composer_version": "2",
        "hit1": True,
        "hit3": True,
        "hit_in_bundle": True,
        "empty_bundle": False,
        "abstention_correct": None,
        "misroutes_in_bundle": 0,
        "recall": 1.0,
        "irrelevant_above_relevant": False,
    }
    row.update(overrides)
    return row


def test_summarize_aggregates_one_set() -> None:
    rows = [
        _row("a", "heldout"),
        _row(
            "b",
            "heldout",
            hit_in_bundle=False,
            empty_bundle=True,
            spent_tokens=0,
            bundle_items=[],
            hit1=False,
            hit3=False,
            recall=0.0,
        ),
        _row(
            "e",
            "heldout",
            expects_empty=True,
            empty_bundle=True,
            abstention_correct=True,
            spent_tokens=0,
            bundle_items=[],
            hit_in_bundle=False,
            hit1=False,
            hit3=False,
            expected_ids=[],
            recall=None,
        ),
    ]
    s = rr.summarize(rows)
    assert s["n_cases"] == 3
    assert s["hit_in_bundle"] == 1
    assert s["empty_bundles"] == 2
    assert s["abstention_correct"] == 1
    assert s["empty_expected_cases"] == 1
    assert s["mean_spent_tokens"] == pytest.approx(33.3)
    assert s["mean_recall"] == pytest.approx(0.5)  # only rows with a measurable recall


def test_build_report_groups_by_set_and_is_deterministic() -> None:
    rows = [_row("a", "heldout"), _row("b", "dev"), _row("c", "kernel")]
    report = rr.build_report(
        rows, composer_variant="current", embedder_model="m", database="aci_ab"
    )
    assert report["n_cases"] == 3
    assert set(report["sets"]) == {"heldout", "dev", "kernel"}
    assert report["composer_variant"] == "current"
    # deterministic: no timestamps / generated ids anywhere in the payload
    payload = json.dumps(report, sort_keys=True)
    assert "generated_at" not in payload
    again = rr.build_report(
        [_row("a", "heldout"), _row("b", "dev"), _row("c", "kernel")],
        composer_variant="current",
        embedder_model="m",
        database="aci_ab",
    )
    assert payload == json.dumps(again, sort_keys=True)


# ---------- paired compare + the pre-registered criteria ----------


def _report(variant: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "composer_variant": variant,
        "embedder_model": "m",
        "database": "aci_ab",
        "read_only": True,
        "n_cases": len(rows),
        "sets": {},
        "rows": rows,
    }


def test_compare_reports_flags_the_three_criteria() -> None:
    current = _report(
        "current",
        [
            _row("h1", "heldout", hit_in_bundle=False, empty_bundle=True, bundle_items=[]),
            _row("h2", "heldout"),
            _row("d1", "dev"),
            _row("k1", "kernel"),
        ],
    )
    skip = _report(
        "skip-oversized",
        [
            _row("h1", "heldout", bundle_items=["relevant-a"], spent_tokens=50),
            _row("h2", "heldout"),
            _row("d1", "dev", hit_in_bundle=False, bundle_items=[], empty_bundle=True),
            _row("k1", "kernel"),
        ],
    )
    compare = rr.compare_reports(current, skip)
    # (a) heldout hit-in-bundle improves (1 -> 2)
    assert compare["criteria"]["a_heldout_hit_in_bundle_improves"] is True
    # (b) one dev case flipped hit -> miss -> NOT adoptable
    assert compare["criteria"]["b_zero_dev_kernel_hit_to_miss"] is False
    assert compare["hit_to_miss"] == ["d1"]
    assert compare["miss_to_hit"] == ["h1"]
    assert compare["criteria"]["adopt_skip_oversized"] is False
    assert compare["n_changed"] == 2  # h1 and d1 changed; h2/k1 identical
    assert [d["case_id"] for d in compare["diffs"]] == ["h1", "d1"]


def test_compare_reports_criteria_c_irrelevant_above_relevant_gain_blocks_adoption() -> None:
    current = _report("current", [_row("h1", "heldout", bundle_items=["relevant-a"])])
    skip = _report(
        "skip-oversized",
        [
            _row(
                "h1",
                "heldout",
                bundle_items=["confuser", "relevant-a"],
                irrelevant_above_relevant=True,
                misroutes_in_bundle=1,
                spent_tokens=120,
            )
        ],
    )
    compare = rr.compare_reports(current, skip)
    assert compare["criteria"]["a_heldout_hit_in_bundle_improves"] is False  # 1 -> 1
    assert compare["criteria"]["c_zero_irrelevant_above_relevant_gains"] is False
    assert compare["irrelevant_above_relevant_gains"] == ["h1"]
    assert compare["criteria"]["adopt_skip_oversized"] is False


def test_compare_reports_all_three_pass_adopts() -> None:
    current = _report("current", [_row("h1", "heldout", hit_in_bundle=False, bundle_items=[])])
    skip = _report("skip-oversized", [_row("h1", "heldout", bundle_items=["relevant-a"])])
    compare = rr.compare_reports(current, skip)
    assert compare["criteria"]["adopt_skip_oversized"] is True


def test_compare_reports_rejects_mismatched_case_sets() -> None:
    current = _report("current", [_row("a", "heldout")])
    skip = _report("skip-oversized", [_row("b", "heldout")])
    with pytest.raises(ValueError, match="different cases"):
        rr.compare_reports(current, skip)


def test_preregistered_decision_is_recorded_in_the_instrument() -> None:
    """The decision rule travels with every compare output (§3.1)."""
    assert any(item.startswith("(a)") for item in rr.PREREGISTERED_DECISION)
    assert any(item.startswith("(b)") for item in rr.PREREGISTERED_DECISION)
    assert any(item.startswith("(c)") for item in rr.PREREGISTERED_DECISION)


# ---------- rendering ----------


def test_render_summary_and_table_contain_every_row() -> None:
    rows = [_row("a", "heldout"), _row("b", "dev", empty_bundle=True, bundle_items=[])]
    report = rr.build_report(rows, composer_variant="current", embedder_model="m", database="d")
    summary = rr.render_summary(report)
    table = rr.render_table(rows)
    assert "composer=current" in summary
    assert "heldout" in summary and "dev" in summary
    assert "| case |" in table
    assert "a" in table and "b" in table
    assert "(empty)" in table  # the empty bundle renders readably


def test_render_compare_prints_criteria_and_changed_cases() -> None:
    current = _report("current", [_row("h1", "heldout", hit_in_bundle=False, bundle_items=[])])
    skip = _report("skip-oversized", [_row("h1", "heldout", bundle_items=["relevant-a"])])
    text = rr.render_compare(rr.compare_reports(current, skip))
    assert "pre-registered decision" in text
    assert "ADOPT skip-oversized as default: True" in text
    assert "h1" in text  # the changed case is listed


# ---------- read-only guard ----------


class _ScalarResult:
    def __init__(self, value: str) -> None:
        self._value = value

    def scalar(self) -> str:
        return self._value


class FakeConn:
    def __init__(self, setting: str) -> None:
        self._setting = setting

    def execute(self, statement: Any) -> _ScalarResult:
        assert "default_transaction_read_only" in str(statement)
        return _ScalarResult(self._setting)


def test_assert_read_only_fails_closed_when_off() -> None:
    rr.assert_read_only(FakeConn("on"))
    with pytest.raises(RuntimeError, match="never writes"):
        rr.assert_read_only(FakeConn("off"))


# ---------- the replay never persists anything ----------


def test_run_case_writes_no_telemetry_surfaces() -> None:
    """The §36 surfaces (route_runs/bundles) are not even reachable from the
    router: the replay composes the same stages WITHOUT the persistence
    repos — structurally, not by convention."""
    import inspect

    router_fields = {f for f in rr.ReplayRouter.__dataclass_fields__}
    assert "route_runs" not in router_fields
    assert "bundles" not in router_fields
    source = inspect.getsource(rr.ReplayRouter.run_case)
    assert "put_route_run" not in source and "put_bundle" not in source


def test_replay_row_matches_result_metrics_conventions() -> None:
    """recall/misroutes come from the standing §71 metric helper — the replay
    must not invent its own scoring."""
    case = HELDOUT_CASES[0]
    bundle = ["diagnosing-bugs", "theme-factory"]
    metrics = result_metrics(case, bundle, latency_ms=None)
    row = rr.build_row(
        case=case,
        set_name="heldout",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("diagnosing-bugs", 0.9, 1), _ranked("theme-factory", 0.8, 2)]),
        composition=_composition(bundle, spent=20, stop_reason=None),
    )
    assert row["recall"] == metrics["recall"]
    assert row["misroutes_in_bundle"] == metrics["misroutes"]
