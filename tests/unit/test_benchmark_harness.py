"""Phase 14 unit acceptance (§52, §70): the benchmark harness variant matrix.

A/B are platform-free baselines, C/D reuse the §14 stage components with
later stages skipped, E is the real service. Every result pins exact router
implementations and exact capability id/version/digest (§52 acceptance).
"""

from datetime import UTC, datetime
from typing import Any

from aci.domain.capability.models import BundleItem, CapabilityBundle, RouteCapabilitiesCommand
from aci.domain.policy.models import (
    EligibilityDecision,
    EligibleCandidate,
    RequestContext,
    RoutingRequestContext,
)
from aci.domain.routing.models import (
    RankedCandidate,
    RerankResult,
    RerankTrace,
    RetrievalResult,
    RetrievalTrace,
    RouteResult,
    RouteRun,
    ScoredCandidate,
    TaskDescriptor,
)
from aci.evaluation.cases import SMOKE_CASES
from aci.evaluation.harness import BenchmarkHarness
from aci.evaluation.metrics import export_report, result_metrics
from aci.evaluation.models import VARIANTS, BenchmarkCase

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def candidate(cid: str, *, score: float = 0.5) -> EligibleCandidate:
    return EligibleCandidate(
        capability_id=cid,
        version="1.0.0",
        digest=f"sha256:{cid}",
        kind="skill",
        channel="production",
        status="active",
    )


class FakeLoader:
    def __init__(self, active: list[EligibleCandidate]) -> None:
        self._active = active

    def load(self, channel: str) -> list[EligibleCandidate]:
        return list(self._active)


class FakeEligibility:
    def filter(
        self,
        candidates: list[EligibleCandidate],
        context: RoutingRequestContext,
        rules: Any,
        *,
        allowed_kinds: list[str],
    ) -> EligibilityDecision:
        return EligibilityDecision(kept=list(candidates))


class FakeRetriever:
    def __init__(self, scored: list[tuple[str, float]]) -> None:
        self._scored = scored

    def retrieve(self, query: str, eligible: list[EligibleCandidate], *, limit: int = 30) -> Any:
        by_id = {c.capability_id: c for c in eligible}
        candidates = [
            ScoredCandidate(candidate=by_id[cid], score=score)
            for cid, score in self._scored
            if cid in by_id
        ]
        return RetrievalResult(
            candidates=candidates,
            trace=RetrievalTrace(
                eligible_count=len(eligible),
                indexed_count=0,
                searched_count=len(candidates),
                returned_count=len(candidates),
                limit=limit,
                model_id="fake",
            ),
        )


class FakeReranker:
    def __init__(self, ranked: list[tuple[str, float]]) -> None:
        self._ranked = ranked

    def rerank(self, task: TaskDescriptor, candidates: Any, context: Any) -> RerankResult:
        by_id = {s.candidate.capability_id: s.candidate for s in candidates}
        ranked = [
            RankedCandidate(candidate=by_id[cid], score=score, retrieval_score=score, rank=i + 1)
            for i, (cid, score) in enumerate(self._ranked)
            if cid in by_id
        ]
        return RerankResult(
            ranked=ranked,
            trace=RerankTrace(
                implementation="fake-reranker",
                version="9.9",
                input_count=len(candidates),
                output_count=len(ranked),
            ),
        )


class FakePolicySnapshots:
    def latest_snapshot(self) -> None:
        return None


class FakeRouteRuns:
    def __init__(self) -> None:
        self.runs: dict[str, RouteRun] = {}

    def put(self, run: RouteRun) -> None:
        self.runs[run.route_run_id] = run

    def get_route_run(self, route_run_id: str) -> RouteRun | None:
        return self.runs.get(route_run_id)


class FakeRouteService:
    """Stands in for RouteCapabilitiesService: routes to one fixed bundle."""

    def __init__(self, route_runs: FakeRouteRuns, cap: str) -> None:
        self._route_runs = route_runs
        self._cap = cap
        self.calls: list[RouteCapabilitiesCommand] = []

    def route(
        self,
        command: RouteCapabilitiesCommand,
        context: RoutingRequestContext,
        *,
        request: RequestContext,
        now: datetime | None = None,
    ) -> RouteResult:
        self.calls.append(command)
        bundle = CapabilityBundle(
            bundle_id=f"bun_{self._cap}",
            route_run_id="route_fake",
            created_at=now or NOW,
            items=[
                BundleItem(
                    capability_id=self._cap,
                    version="1.0.0",
                    digest=f"sha256:{self._cap}",
                    kind="skill",
                )
            ],
        )
        self._route_runs.put(
            RouteRun(
                route_run_id="route_fake",
                request_id="req_fake",
                trace_id="trc_fake",
                created_at=now or NOW,
                principal_id="benchmark",
                client_type="benchmark-harness",
                protocol_type="benchmark",
                task_text=command.task_text,
                reranker_implementation="fake-reranker",
                reranker_version="9.9",
                composer_implementation="fake-composer",
                composer_version="7.7",
                latency_ms=33,
                bundle_id=bundle.bundle_id,
            )
        )
        return RouteResult(route_run_id="route_fake", bundle=bundle)


class FakeStore:
    def __init__(self) -> None:
        self.cases: dict[str, BenchmarkCase] = {}
        self.runs: list[Any] = []
        self.results: list[Any] = []

    def put_case(self, case: BenchmarkCase) -> None:
        self.cases[case.case_id] = case

    def put_run(self, run: Any) -> None:
        self.runs.append(run)

    def put_result(self, result: Any) -> None:
        self.results.append(result)

    def list_results(self, run_id: str) -> list[Any]:
        return [r for r in self.results if r.run_id == run_id]


def make_harness(
    *,
    active: list[EligibleCandidate] | None = None,
    scored: list[tuple[str, float]] | None = None,
    ranked: list[tuple[str, float]] | None = None,
    routed_cap: str = "cap-relevant",
) -> tuple[BenchmarkHarness, FakeStore, FakeRouteService]:
    active = active if active is not None else [candidate("cap-relevant"), candidate("cap-noise")]
    scored = scored if scored is not None else [("cap-relevant", 0.9), ("cap-noise", 0.1)]
    ranked = ranked if ranked is not None else [("cap-relevant", 0.9), ("cap-noise", 0.2)]
    route_runs = FakeRouteRuns()
    route_service = FakeRouteService(route_runs, routed_cap)
    store = FakeStore()
    harness = BenchmarkHarness(
        route_service,  # type: ignore[arg-type]
        FakeLoader(active),  # type: ignore[arg-type]
        FakeEligibility(),  # type: ignore[arg-type]
        FakeRetriever(scored),  # type: ignore[arg-type]
        FakeReranker(ranked),  # type: ignore[arg-type]
        FakePolicySnapshots(),  # type: ignore[arg-type]
        route_runs,  # type: ignore[arg-type]
        store,  # type: ignore[arg-type]
    )
    return harness, store, route_service


def case(**overrides: Any) -> BenchmarkCase:
    base: dict[str, Any] = {
        "case_id": "case-1",
        "category": "debugging",
        "fixture": "repo/fixture-v1",
        "task_text": "fix the flaky auth race",
        "relevant_strong": ["cap-relevant"],
        "relevant_irrelevant": ["cap-bad"],
        "baseline_capability_ids": ["cap-relevant", "cap-missing"],
    }
    base.update(overrides)
    return BenchmarkCase.model_validate(base)


def test_matrix_runs_every_case_through_all_five_variants() -> None:
    harness, store, _ = make_harness()
    results = harness.run([case()], label="smoke", now=NOW)
    assert [r.variant for r in results] == VARIANTS
    assert len(results) == 5
    assert len(store.runs) == 1
    assert store.cases["case-1"].case_id == "case-1"
    assert all(r.run_id == store.runs[0].run_id for r in results)


def test_client_alone_is_an_empty_baseline() -> None:
    harness, _, _ = make_harness()
    results = harness.run([case()], label="smoke", now=NOW)
    alone = results[0]
    assert alone.variant == "client_alone"
    assert alone.selected == []
    assert alone.route_run_id is None
    assert alone.metrics["abstained"] == 1
    assert alone.metrics["latency_ms"] is None


def test_manual_baseline_resolves_active_production_and_records_missing() -> None:
    harness, _, _ = make_harness()
    results = harness.run([case()], label="smoke", now=NOW)
    manual = results[1]
    assert manual.variant == "manual_baseline"
    assert [s.capability_id for s in manual.selected] == ["cap-relevant"]
    assert manual.selected[0].version == "1.0.0"
    assert manual.selected[0].digest == "sha256:cap-relevant"
    assert manual.metrics["baseline_missing"] == ["cap-missing"]
    assert manual.route_run_id is None


def test_retrieval_only_takes_topk_by_score_without_reranker() -> None:
    harness, _, _ = make_harness()
    results = harness.run([case()], label="smoke", now=NOW)
    retrieval = results[2]
    assert retrieval.variant == "retrieval_only"
    assert [s.capability_id for s in retrieval.selected] == ["cap-relevant", "cap-noise"]
    assert retrieval.router.reranker_implementation is None
    assert retrieval.metrics["latency_ms"] is not None
    assert retrieval.route_run_id is None


def test_retrieval_rerank_uses_reranker_order_and_versions() -> None:
    harness, _, _ = make_harness()
    results = harness.run([case()], label="smoke", now=NOW)
    rerank = results[3]
    assert rerank.variant == "retrieval_rerank"
    assert [s.capability_id for s in rerank.selected] == ["cap-relevant", "cap-noise"]
    assert rerank.router.reranker_implementation == "fake-reranker"
    assert rerank.router.reranker_version == "9.9"
    assert rerank.router.composer_implementation is None


def test_full_pipeline_pins_route_run_bundle_and_router_versions() -> None:
    harness, _, route_service = make_harness()
    results = harness.run([case()], label="smoke", now=NOW)
    full = results[4]
    assert full.variant == "full_pipeline"
    assert full.route_run_id == "route_fake"
    assert full.bundle_id == "bun_cap-relevant"
    assert full.router.reranker_implementation == "fake-reranker"
    assert full.router.composer_implementation == "fake-composer"
    assert full.router.composer_version == "7.7"
    assert full.metrics["latency_ms"] == 33
    # §52 acceptance: the selection references the exact version + digest.
    assert full.selected[0].capability_id == "cap-relevant"
    assert full.selected[0].version == "1.0.0"
    assert full.selected[0].digest == "sha256:cap-relevant"
    assert route_service.calls[0].task_text == "fix the flaky auth race"


def test_metrics_recall_misroutes_and_unmeasurable() -> None:
    c = case()
    metrics = result_metrics(c, ["cap-relevant", "cap-bad"], latency_ms=5)
    assert metrics["recall"] == 1.0  # relevant selected / relevant total
    assert metrics["misroutes"] == 1  # cap-bad is annotated irrelevant
    assert metrics["abstained"] == 0

    unmeasurable = result_metrics(
        case(relevant_strong=[], relevant_acceptable=[]), [], latency_ms=None
    )
    assert unmeasurable["recall"] is None  # unmeasured, never 0
    assert unmeasurable["abstained"] == 1


def test_report_export_aggregates_per_variant_and_labels_non_causal() -> None:
    harness, store, _ = make_harness()
    results = harness.run([case(), case(case_id="case-2")], label="smoke", now=NOW)
    report = export_report(store.runs[0], results)
    assert set(report["variants"]) == set(VARIANTS)
    for variant in VARIANTS:
        row = report["variants"][variant]
        assert row["results"] == 2
        assert row["abstention_rate"] is not None
    assert report["case_count"] == 2
    assert len(report["results"]) == 10
    # §34: the report labels its associations as non-causal.
    assert any("not causal" in note for note in report["notes"])
    # Every row names its exact router + capability versions (§52).
    full_rows = [r for r in report["results"] if r["variant"] == "full_pipeline"]
    assert all(r["route_run_id"] == "route_fake" for r in full_rows)
    assert all(r["router"]["composer_version"] == "7.7" for r in full_rows)


def test_fixture_replay_creates_a_new_run_with_the_same_measurements() -> None:
    harness, store, _ = make_harness()
    first = harness.run([case()], label="smoke-1", now=NOW)
    second = harness.run([case()], label="smoke-2", now=NOW)
    assert first[0].run_id != second[0].run_id
    assert store.runs[0].label == "smoke-1"
    assert store.runs[1].label == "smoke-2"
    # Same fixture, same variant behavior: A stays empty in both runs.
    assert first[0].selected == second[0].selected == []
    assert [r.variant for r in first] == [r.variant for r in second]


def test_smoke_case_set_is_ten_unique_replayable_fixtures() -> None:
    """§70/§75 step 22: the smoke set is exactly 10 tasks, ids unique."""
    assert len(SMOKE_CASES) == 10
    ids = [c.case_id for c in SMOKE_CASES]
    assert len(set(ids)) == 10
    assert all(c.task_text for c in SMOKE_CASES)
    assert all(c.fixture for c in SMOKE_CASES)
    assert all(c.baseline_capability_ids for c in SMOKE_CASES)
    # Every case runs the full matrix unchanged (§35: annotations never
    # define the only solution path — they only annotate).
    assert all(c.acceptance_tests for c in SMOKE_CASES)
