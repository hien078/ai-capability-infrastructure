"""Unit tests for scripts/jev_eval.py — the §4 promotion-gate instrument.

Everything here exercises the PURE parts (organic-case loading, row
building, aggregation, rendering, percentiles) plus end-to-end run_case
over the REAL stage classes with fakes — no DB, no network, no live model.
The DB layer (build_router) is the only uncovered part: a thin read-only
wiring of the same repos the integration tests already cover (same shape
as scripts/routing_replay.py).
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from aci.adapters.outbound.model_provider.hashing import HashingEmbedder
from aci.domain.capability.models import (
    CapabilityVersion,
    SkillSpec,
)
from aci.domain.policy.models import EligibleCandidate, PolicyRules
from aci.domain.routing.models import (
    JudgeVerdict,
    RankedCandidate,
    RerankResult,
    RerankTrace,
    RetrievalResult,
    RetrievalTrace,
)
from aci.evaluation.models import BenchmarkCase
from aci.routing.composer import MinimalBundleComposer
from aci.routing.dependencies import DefaultDependencyResolver
from aci.routing.eligibility import DefaultEligibilityPolicy
from aci.routing.rerankers.heuristic import HeuristicReranker
from aci.routing.rerankers.jev import JevReranker
from aci.routing.retrieval import EmbeddingRetriever

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import jev_eval as je  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
DIGEST = "sha256:" + "ab" * 32
NOW = datetime(2026, 10, 7, tzinfo=UTC)


# ---------- fakes (same shapes the existing unit suites use) ----------


class FakeEmbeddings:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], Any] = {}
        self.vectors: dict[tuple[str, str, str], list[float]] = {}

    def put_document(self, document: Any, vector: list[float]) -> None:
        self.docs[(document.capability_id, document.version)] = document
        self.vectors[(document.capability_id, document.version, document.model_id)] = vector

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
            RetrievedDocument(capability_id=cap, version=ver, score=0.5)
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
    def get_release(self, capability_id: str, channel: str) -> None:  # noqa: ARG002
        return None


class FakePayloadSizes:
    def __init__(self, sizes: dict[str, int]) -> None:
        self._sizes = sizes

    def entry_sizes(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
        return {p: self._sizes[p[0]] for p in pairs if p[0] in self._sizes}


class ScriptedJudge:
    """Fake judge: returns one scripted verdict per call, records everything."""

    def __init__(self, verdicts: list[JudgeVerdict]) -> None:
        self._verdicts = list(verdicts)
        self.calls: list[tuple[str, list[str], int]] = []

    def judge(
        self,
        task_text: str,
        candidates: list[Any],
        max_select: int,  # type: ignore[override]
    ) -> JudgeVerdict:
        self.calls.append((task_text, [c.capability_id for c in candidates], max_select))
        return self._verdicts.pop(0)


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


def make_eval_router(
    versions: list[CapabilityVersion],
    candidates: list[EligibleCandidate],
    *,
    reranker: Any,
    sizes: dict[str, int] | None = None,
) -> je.EvalRouter:
    """The REAL stage classes over fakes — the same wiring build_router does."""
    return je.EvalRouter(
        eligibility=DefaultEligibilityPolicy(),
        retriever=EmbeddingRetriever(
            FakeCapabilities(versions),  # type: ignore[arg-type]
            HashingEmbedder(),
            FakeEmbeddings(),  # type: ignore[arg-type]
        ),
        reranker=reranker,
        resolver=DefaultDependencyResolver(FakeRelations(), FakeReleases()),  # type: ignore[arg-type]
        composer=MinimalBundleComposer(FakePayloadSizes(sizes or {})),  # type: ignore[arg-type]
        rules=PolicyRules(),
        candidates=candidates,
    )


# ---------- organic case loading ----------


def test_load_organic_cases_parses_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "organic.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "o-1",
                "task_text": "fix the flugel",
                "acceptable": ["fake-lint-helper"],
                "notes": "synthetic",
            }
        )
        + "\n"
        + json.dumps({"case_id": "o-2", "task_text": "water the plants", "acceptable": []})
        + "\n",
        encoding="utf-8",
    )
    cases = je.load_organic_cases(path)
    assert [c.case_id for c in cases] == ["o-1", "o-2"]
    assert cases[0].acceptable == ("fake-lint-helper",)
    assert cases[1].acceptable == ()


def test_load_organic_cases_rejects_bad_lines_loudly(tmp_path: Path) -> None:
    path = tmp_path / "organic.jsonl"
    path.write_text(
        '{"case_id": "o-1", "task_text": "t", "acceptable": []}\nnot json at all\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="line 2"):
        je.load_organic_cases(path)


def test_load_organic_cases_requires_task_text(tmp_path: Path) -> None:
    path = tmp_path / "organic.jsonl"
    path.write_text('{"case_id": "o-1", "acceptable": []}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="task_text"):
        je.load_organic_cases(path)


# ---------- build_row (hand-built stage outputs) ----------


def _retrieval() -> RetrievalResult:
    return RetrievalResult(
        candidates=[],
        trace=RetrievalTrace(
            eligible_count=3,
            indexed_count=0,
            searched_count=3,
            returned_count=0,
            limit=30,
            model_id="hashing-384-v2",
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


def _rerank(ranked: list[RankedCandidate], **trace: Any) -> RerankResult:
    return RerankResult(
        ranked=ranked,
        trace=RerankTrace(
            implementation="jev",
            version="1.0.0",
            input_count=0,
            output_count=len(ranked),
            **trace,
        ),
    )


def _composition(bundle_ids: list[str], *, spent: int = 20) -> Any:
    from aci.domain.capability.models import BundleBudget, BundleItem, CapabilityBundle
    from aci.domain.routing.models import (
        ComposedItemCost,
        CompositionResult,
        CompositionTrace,
    )

    items = [
        BundleItem(capability_id=cid, version="1.0.0", digest=DIGEST, kind="skill")
        for cid in bundle_ids
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
        items=[
            ComposedItemCost(
                capability_id=cid,
                version="1.0.0",
                estimated_tokens=10,
                estimate_source="artifact_entry",
                included=True,
            )
            for cid in bundle_ids
        ],
    )
    return CompositionResult(bundle=bundle, trace=trace)


def test_build_row_dev_case_measures_recall_and_hits() -> None:
    case = BenchmarkCase(
        case_id="dev-x",
        category="debugging",
        fixture="dev",
        task_text="find the memory leak",
        relevant_strong=["relevant-a"],
        relevant_acceptable=["relevant-b"],
        relevant_irrelevant=["confuser"],
    )
    row = je.build_row(
        case=case,
        set_name="dev",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("relevant-a", 0.9, 1)]),
        composition=_composition(["relevant-a"]),
        route_latency_ms=120,
    )
    assert row["set"] == "dev"
    assert row["bundle_items"] == ["relevant-a"]
    assert row["hit_in_bundle"] is True
    assert row["recall"] == pytest.approx(0.5)  # 1 of {relevant-a, relevant-b}
    assert row["misroutes"] == 0
    assert row["route_latency_ms"] == 120
    assert row["judge_status"] is None  # heuristic trace has no judge fields


def test_build_row_organic_case_measures_irrelevant_attach_and_abstention() -> None:
    case = je.OrganicCase(
        case_id="o-1", task_text="audit the token budget", acceptable=("fake-ci-auditor",)
    )
    # The bundle attached a skill OUTSIDE the acceptable set.
    row = je.build_row(
        case=case,
        set_name="organic",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("fake-security-booster", 0.9, 1)]),
        composition=_composition(["fake-security-booster"]),
        route_latency_ms=99,
    )
    assert row["irrelevant_attach"] is True
    assert row["clean_attach"] is False
    assert row["abstain_correct"] is None  # acceptable is non-empty
    assert row["acceptable"] == ["fake-ci-auditor"]

    # A bundle fully inside the acceptable set is a clean attach.
    clean = je.build_row(
        case=case,
        set_name="organic",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("fake-ci-auditor", 0.9, 1)]),
        composition=_composition(["fake-ci-auditor"]),
        route_latency_ms=99,
    )
    assert clean["irrelevant_attach"] is False
    assert clean["clean_attach"] is True

    # Empty acceptable -> the correct answer is abstention.
    abstain_case = je.OrganicCase(case_id="o-2", task_text="water the plants", acceptable=())
    ok = je.build_row(
        case=abstain_case,
        set_name="organic",
        retrieval=_retrieval(),
        rerank=_rerank([]),
        composition=_composition([], spent=0),
        route_latency_ms=50,
    )
    assert ok["abstain_correct"] is True
    assert ok["irrelevant_attach"] is False  # nothing attached at all
    bad = je.build_row(
        case=abstain_case,
        set_name="organic",
        retrieval=_retrieval(),
        rerank=_rerank([_ranked("fake-security-booster", 0.9, 1)]),
        composition=_composition(["fake-security-booster"]),
        route_latency_ms=50,
    )
    assert bad["abstain_correct"] is False
    assert bad["irrelevant_attach"] is True


def test_build_row_carries_judge_fields_from_the_trace() -> None:
    case = je.OrganicCase(case_id="o-1", task_text="t", acceptable=("debugging",))
    row = je.build_row(
        case=case,
        set_name="organic",
        retrieval=_retrieval(),
        rerank=_rerank(
            [_ranked("debugging", 0.9, 1)],
            judge_status="ok",
            judge_model_id="OneNexus/glm-5.3",
            judge_latency_ms=1500,
            judge_reason="relevant",
            selected_ids=["debugging"],
            invalid_ids=1,
        ),
        composition=_composition(["debugging"]),
        route_latency_ms=1700,
    )
    assert row["judge_status"] == "ok"
    assert row["judge_latency_ms"] == 1500
    assert row["invalid_ids"] == 1
    assert row["selected_ids"] == ["debugging"]


# ---------- run_case end-to-end over the real stages (fakes, no DB) ----------


def test_run_case_routes_without_persisting_and_records_judge() -> None:
    versions = [
        make_version("debugging", "systematic debugging investigation"),
        make_version("theme-factory", "visual themes and palettes"),
    ]
    candidates = [make_candidate(v.capability_id) for v in versions]
    judge = ScriptedJudge(
        [JudgeVerdict(status="ok", selected=["debugging"], reason="r", latency_ms=42)]
    )
    router = make_eval_router(
        versions, candidates, reranker=JevReranker(judge, candidate_limit=2, max_select=1)
    )
    case = je.OrganicCase(
        case_id="o-debug", task_text="fix a failing python test", acceptable=("debugging",)
    )
    row = router.run_case(case, set_name="organic")
    assert row["bundle_items"] == ["debugging"]
    assert row["judge_status"] == "ok"
    assert row["judge_latency_ms"] == 42
    assert row["clean_attach"] is True
    assert row["route_latency_ms"] >= 0
    # The judge saw the trusted document text, not bodies (§17.1).
    task_text, sent_ids, max_select = judge.calls[0]
    assert task_text == "fix a failing python test"
    assert sent_ids == ["debugging", "theme-factory"]
    assert max_select == 1


def test_run_case_judge_failure_is_counted_not_fatal() -> None:
    versions = [make_version("debugging", "debug")]
    candidates = [make_candidate(v.capability_id) for v in versions]
    judge = ScriptedJudge([JudgeVerdict(status="timeout", reason="slow")])
    router = make_eval_router(versions, candidates, reranker=JevReranker(judge))
    case = je.OrganicCase(case_id="o-t", task_text="t", acceptable=("debugging",))
    row = router.run_case(case, set_name="organic")
    assert row["bundle_items"] == []  # fail-safe = abstain
    assert row["judge_status"] == "timeout"
    # acceptable non-empty: abstention is a miss, not "correct"
    assert row["abstain_correct"] is None


def test_eval_router_has_no_telemetry_surfaces() -> None:
    """The §36 surfaces are structurally unreachable from the eval router."""
    import inspect

    fields = set(je.EvalRouter.__dataclass_fields__)
    assert "route_runs" not in fields
    assert "bundles" not in fields
    source = inspect.getsource(je.EvalRouter.run_case)
    assert "put_route_run" not in source and "put_bundle" not in source


# ---------- aggregation + rendering ----------


def _organic_row(case_id: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "case_id": case_id,
        "set": "organic",
        "task_chars": 20,
        "bundle_items": ["fake-ci-auditor"],
        "empty_bundle": False,
        "acceptable": ["fake-ci-auditor"],
        "irrelevant_attach": False,
        "clean_attach": True,
        "abstain_correct": None,
        "expected_ids": [],
        "hit_in_bundle": None,
        "recall": None,
        "misroutes": 0,
        "judge_status": "ok",
        "judge_latency_ms": 1500,
        "invalid_ids": 0,
        "selected_ids": ["fake-ci-auditor"],
        "route_latency_ms": 1600,
        "spent_tokens": 100,
    }
    row.update(overrides)
    return row


def test_summarize_produces_the_gate_table_fields() -> None:
    rows = [
        _organic_row("o-1"),
        _organic_row(
            "o-2",
            bundle_items=[],
            empty_bundle=True,
            abstain_correct=True,
            acceptable=[],
            judge_latency_ms=800,
            route_latency_ms=900,
            selected_ids=[],
            spent_tokens=0,
        ),
        _organic_row(
            "o-3",
            irrelevant_attach=True,
            clean_attach=False,
            judge_status="error",
            judge_latency_ms=None,
            route_latency_ms=2000,
        ),
    ]
    s = je.summarize(rows)
    assert s["n_cases"] == 3
    assert s["irrelevant_attach"] == 1
    assert s["irrelevant_attach_rate"] == 0.333  # rounded to 3 decimals
    assert s["abstention_expected"] == 1
    assert s["abstention_correct"] == 1
    assert s["correct_abstain_rate"] == pytest.approx(1.0)
    assert s["judge_status_counts"] == {"ok": 2, "error": 1}
    assert s["route_latency_p50_ms"] == 1600
    assert s["route_latency_p95_ms"] == 2000
    assert s["judge_latency_p50_ms"] == 800  # nearest-rank over [800, 1500]
    assert s["judge_latency_p95_ms"] == 1500
    assert s["total_judge_tokens"] is None  # the adapter does not report usage


def test_summarize_dev_set_measures_recall() -> None:
    rows = [
        {
            "case_id": "d-1",
            "set": "dev",
            "task_chars": 10,
            "bundle_items": ["relevant-a"],
            "empty_bundle": False,
            "acceptable": [],
            "irrelevant_attach": None,
            "clean_attach": None,
            "abstain_correct": None,
            "expected_ids": ["relevant-a", "relevant-b"],
            "hit_in_bundle": True,
            "recall": 0.5,
            "misroutes": 0,
            "judge_status": "ok",
            "judge_latency_ms": 100,
            "invalid_ids": 0,
            "selected_ids": ["relevant-a"],
            "route_latency_ms": 200,
            "spent_tokens": 50,
        },
        {
            "case_id": "d-2",
            "set": "dev",
            "task_chars": 10,
            "bundle_items": [],
            "empty_bundle": True,
            "acceptable": [],
            "irrelevant_attach": None,
            "clean_attach": None,
            "abstain_correct": None,
            "expected_ids": ["relevant-a"],
            "hit_in_bundle": False,
            "recall": 0.0,
            "misroutes": 0,
            "judge_status": "ok",
            "judge_latency_ms": 100,
            "invalid_ids": 0,
            "selected_ids": [],
            "route_latency_ms": 200,
            "spent_tokens": 0,
        },
    ]
    s = je.summarize(rows)
    assert s["n_cases"] == 2
    assert s["hit_in_bundle"] == 1
    assert s["mean_recall"] == pytest.approx(0.25)
    assert s["empty_bundles"] == 1


def test_percentile_nearest_rank() -> None:
    values = [5, 1, 3, 2, 4]
    assert je.percentile(values, 50) == 3
    assert je.percentile(values, 95) == 5
    assert je.percentile([], 50) is None


def test_render_summary_is_the_gate_table() -> None:
    rows = [_organic_row("o-1"), _organic_row("o-2", judge_status="timeout")]
    report = je.build_report(
        rows, reranker="jev", embedder_model="m", database="aci_bench", organic_source="x.jsonl"
    )
    text = je.render_summary(report)
    assert "irr-attach" in text
    assert "abst-ok" in text
    assert "judge" in text
    assert "organic" in text
    table = je.render_table(rows)
    assert "o-1" in table and "o-2" in table
    assert "timeout" in table


def test_report_is_deterministic() -> None:
    rows = [_organic_row("o-1")]
    one = je.build_report(
        rows, reranker="jev", embedder_model="m", database="d", organic_source="s"
    )
    two = je.build_report(
        [_organic_row("o-1")], reranker="jev", embedder_model="m", database="d", organic_source="s"
    )
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert "generated_at" not in json.dumps(one)


# ---------- the committed sample fixture, end-to-end against fakes ----------


def test_sample_fixture_produces_the_gate_table() -> None:
    """§2.6 accept: the synthetic --organic fixture (fake skills, NOT real task
    texts) runs through the REAL stages over fakes and renders the table."""
    cases = je.load_organic_cases(FIXTURES / "jev_eval_sample.jsonl")
    assert len(cases) >= 3
    # The fake corpus the fixture's acceptable ids refer to.
    versions = [
        make_version("fake-lint-helper", "configure linting for widget builds"),
        make_version("fake-ci-auditor", "audit CI pipelines and token budgets"),
        make_version(
            "fake-security-booster", "security token audit CI review rate limit hardening"
        ),
        make_version("fake-plant-care", "watering schedules for office plants"),
    ]
    candidates = [make_candidate(v.capability_id) for v in versions]
    judge = ScriptedJudge(
        [
            JudgeVerdict(status="ok", selected=["fake-lint-helper"], reason="lint task"),
            JudgeVerdict(status="ok", selected=[], reason="nothing applies"),
            JudgeVerdict(status="ok", selected=["fake-security-booster"], reason="words match"),
        ]
    )
    router = make_eval_router(
        versions, candidates, reranker=JevReranker(judge, candidate_limit=4, max_select=2)
    )
    rows = [router.run_case(case, set_name="organic") for case in cases]
    report = je.build_report(
        rows,
        reranker="jev",
        embedder_model="hashing-384-v2",
        database="(fakes)",
        organic_source=str(FIXTURES / "jev_eval_sample.jsonl"),
    )
    summary = je.render_summary(report)
    assert "| organic |" in summary
    organic = report["sets"]["organic"]
    assert organic["n_cases"] == len(cases)
    assert organic["judge_status_counts"] == {"ok": len(cases)}
    # The synthetic §1 failure mode is visible: the word-matching judge
    # attached the confuser on the audit case (irrelevant attach).
    assert organic["irrelevant_attach"] >= 1


def test_heuristic_arm_runs_the_same_instrument() -> None:
    """--reranker heuristic is the baseline arm of the §4 gate."""
    versions = [make_version("debugging", "debug failing tests")]
    candidates = [make_candidate(v.capability_id) for v in versions]
    router = make_eval_router(versions, candidates, reranker=HeuristicReranker())
    case = je.OrganicCase(
        case_id="o-h", task_text="debug a failing test", acceptable=("debugging",)
    )
    row = router.run_case(case, set_name="organic")
    assert row["bundle_items"] == ["debugging"]
    assert row["judge_status"] is None
    assert row["clean_attach"] is True


# ---------- fail-closed judge config (no DB needed) ----------


def test_judge_config_check_fails_closed() -> None:
    with pytest.raises(ValueError, match="ACI_JEV_BASE_URL"):
        je.judge_from_config(
            base_url="",
            api_key="",
            model="OneNexus/glm-5.3",
            reasoning_effort="low",
            timeout_s=8.0,
        )
    with pytest.raises(ValueError, match="ACI_JEV_API_KEY"):
        je.judge_from_config(
            base_url="http://j",
            api_key="",
            model="OneNexus/glm-5.3",
            reasoning_effort="low",
            timeout_s=8.0,
        )


def test_judge_from_config_builds_the_adapter() -> None:
    judge = je.judge_from_config(
        base_url="http://j/v1",
        api_key="sk-x",
        model="OneNexus/glm-5.3",
        reasoning_effort="low",
        timeout_s=8.0,
    )
    from aci.adapters.outbound.model_provider.judge import OpenAICompatSkillJudge

    assert isinstance(judge, OpenAICompatSkillJudge)


def test_reranker_from_config_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown reranker"):
        je.reranker_from_config("bogus")  # type: ignore[arg-type]
    heuristic = je.reranker_from_config("heuristic")
    assert isinstance(heuristic, HeuristicReranker)
