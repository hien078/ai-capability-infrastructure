"""Non-finite retrieval scores must die at the retrieval boundary (§16/§36).

Measured defect (heldout job, 2026-10-01): a NaN raw retrieval score
(``1 - cosine_distance`` — NaN whenever a stored vector is all zeros)
reached the ``route_runs.stages`` JSONB insert and Postgres rejected the
NaN token, failing a live-DB test flakily. Two propagation paths made it
worse than a crash:

- the §36 stages payload carries the raw score (``stages.retrieved``), and
  strict JSON / JSONB reject NaN tokens — the insert fails;
- the reranker's clamp silently turns a NaN into the TOP retrieval signal
  (``max(0.0, min(1.0, nan)) == 1.0``) — a rank boost for the poisoned hit.

The fix is at the retrieval boundary: a non-finite score is treated as the
LOWEST possible similarity and flagged in the stage trace — never
propagated. These tests are red on the old code and green after; finite
scores must pass through byte-identically (the replay before/after the fix
is byte-identical on all 90 cases).
"""

import json
import math
from datetime import UTC, datetime
from typing import Any

from aci.adapters.outbound.model_provider.hashing import HashingEmbedder
from aci.application.route_capabilities import RouteCapabilitiesService
from aci.domain.capability.models import CapabilityVersion, RouteCapabilitiesCommand, SkillSpec
from aci.domain.policy.models import (
    ClientDescriptor,
    EligibleCandidate,
    PolicySnapshot,
    ProtocolDescriptor,
    RequestContext,
    RoutingRequestContext,
    ScopeContext,
)
from aci.domain.routing.models import TaskDescriptor
from aci.routing.composer import MinimalBundleComposer
from aci.routing.dependencies import DefaultDependencyResolver
from aci.routing.eligibility import DefaultEligibilityPolicy
from aci.routing.rerankers.heuristic import HeuristicReranker
from aci.routing.retrieval import EmbeddingRetriever

DIGEST = "sha256:" + "ab" * 32
NOW = datetime(2026, 10, 2, tzinfo=UTC)


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


class FakeCapabilities:
    def __init__(self, versions: list[CapabilityVersion]) -> None:
        self._versions = {(v.capability_id, v.version): v for v in versions}

    def get_version(self, capability_id: str, version: str) -> CapabilityVersion | None:
        return self._versions.get((capability_id, version))


class PoisonedEmbeddings:
    """EmbeddingRepository fake whose search yields per-candidate scores.

    A score of NaN/inf is exactly what pgvector returns for a zero document
    vector (``1 - cosine_distance`` = NaN); Postgres sorts NaN ABOVE every
    finite value in DESC order, so the poisoned hit arrives FIRST — the
    fake reproduces that ordering too.
    """

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores
        self.docs: dict[tuple[str, str], Any] = {}

    def put_document(self, document: Any, vector: list[float]) -> None:
        self.docs[(document.capability_id, document.version)] = document

    def get_indexed_documents(self, pairs: list[tuple[str, str]], model_id: str) -> list[Any]:
        return [doc for (cap, ver), doc in self.docs.items() if (cap, ver) in set(pairs)]

    def search(
        self, query_vector: list[float], pairs: list[tuple[str, str]], *, model_id: str, limit: int
    ) -> list[Any]:
        from aci.domain.routing.models import RetrievedDocument

        hits = [
            RetrievedDocument(
                capability_id=cap,
                version=ver,
                score=self._scores.get(cap, 0.5),
            )
            for cap, ver in pairs
        ]
        # Postgres DESC: NaN first (NaN > everything), then finite scores.
        hits.sort(key=lambda h: (not math.isnan(h.score), -h.score))
        return hits[:limit]


class FakeRelations:
    def list_relations(self, source_capability_id: str) -> list[Any]:
        return []


class FakeReleases:
    def get_release(self, capability_id: str, channel: str) -> None:
        return None


class FakePolicySnapshots:
    def __init__(self, snapshot: PolicySnapshot | None = None) -> None:
        self.snapshot = snapshot

    def latest_snapshot(self) -> PolicySnapshot | None:
        return self.snapshot


class FakeRouteRuns:
    def __init__(self) -> None:
        self.runs: dict[str, Any] = {}

    def put_route_run(self, run: Any) -> Any:
        self.runs[run.route_run_id] = run
        return run

    def get_route_run(self, route_run_id: str) -> Any:
        return self.runs.get(route_run_id)


class FakeBundles:
    def __init__(self) -> None:
        self.bundles: dict[str, Any] = {}

    def put_bundle(self, bundle: Any) -> Any:
        self.bundles[bundle.bundle_id] = bundle
        return bundle

    def get_bundle(self, bundle_id: str) -> Any:
        return self.bundles.get(bundle_id)


def make_retriever(scores: dict[str, float], versions: list[CapabilityVersion]):
    return EmbeddingRetriever(
        FakeCapabilities(versions),  # type: ignore[arg-type]
        HashingEmbedder(),
        PoisonedEmbeddings(scores),  # type: ignore[arg-type]
    )


def make_service(retriever: EmbeddingRetriever, candidates: list[EligibleCandidate]):
    runs = FakeRouteRuns()
    bundles = FakeBundles()
    service = RouteCapabilitiesService(
        loader=_FakeLoader(candidates),
        eligibility=DefaultEligibilityPolicy(),
        retriever=retriever,
        reranker=HeuristicReranker(),
        resolver=DefaultDependencyResolver(FakeRelations(), FakeReleases()),  # type: ignore[arg-type]
        composer=MinimalBundleComposer(),
        policy_snapshots=FakePolicySnapshots(),
        route_runs=runs,  # type: ignore[arg-type]
        bundles=bundles,  # type: ignore[arg-type]
    )
    return service, runs


class _FakeLoader:
    def __init__(self, candidates: list[EligibleCandidate]) -> None:
        self.candidates = candidates

    def load(self, channel: str = "production", *, status: Any = None) -> list[EligibleCandidate]:
        return list(self.candidates)


def _routing_context() -> RoutingRequestContext:
    return RoutingRequestContext(
        client=ClientDescriptor(type="rest-client"),
        scope=ScopeContext(principal_id="p-1"),
        request_id="req_1",
    )


def _request_context() -> RequestContext:
    return RequestContext(
        request_id="req_1",
        trace_id="trc_1",
        principal_id="p-1",
        client=ClientDescriptor(type="rest-client"),
        protocol=ProtocolDescriptor(type="rest", version="1"),
    )


# ---------- the defect: NaN reaches the §36 stages payload (red on old code) ----------


def test_nan_retrieval_score_never_reaches_the_route_run_stages() -> None:
    """A NaN similarity from the vector store must not reach the stages
    payload: strict JSON (allow_nan=False) must serialize it — Postgres
    rejects NaN tokens in JSONB, which is the original flaky failure."""
    versions = [
        make_version("cap-poisoned", "debug python"),
        make_version("cap-clean", "debug python"),
    ]
    candidates = [make_candidate("cap-poisoned"), make_candidate("cap-clean")]
    retriever = make_retriever({"cap-poisoned": math.nan}, versions)
    service, runs = make_service(retriever, candidates)

    result = service.route(
        RouteCapabilitiesCommand(task_text="debug python"),
        _routing_context(),
        request=_request_context(),
        now=NOW,
    )
    run = runs.runs[result.route_run_id]

    # Strict JSON: raises ValueError on any NaN/Infinity token.
    payload = json.dumps(run.stages, allow_nan=False)
    assert "NaN" not in payload
    assert "Infinity" not in payload

    # Every score that reached the payload is finite; the boundary flagged
    # the poisoned hit in the stage trace.
    scores = [s["score"] for s in run.stages["retrieved"]]
    assert all(math.isfinite(s) for s in scores)
    assert run.stages["retrieval"]["nonfinite_scores"] == 1


def test_inf_and_neg_inf_scores_are_sanitized_too() -> None:
    """Every non-finite flavor (NaN, +inf, -inf) is poison to JSONB."""
    versions = [make_version(f"cap-{i}", "debug python") for i in range(3)]
    candidates = [make_candidate(f"cap-{i}") for i in range(3)]
    retriever = make_retriever({"cap-0": math.inf, "cap-1": math.nan, "cap-2": -math.inf}, versions)
    service, runs = make_service(retriever, candidates)
    service.route(
        RouteCapabilitiesCommand(task_text="debug python"),
        _routing_context(),
        request=_request_context(),
        now=NOW,
    )
    run = next(iter(runs.runs.values()))
    payload = json.dumps(run.stages, allow_nan=False)
    assert "NaN" not in payload and "Infinity" not in payload
    assert run.stages["retrieval"]["nonfinite_scores"] == 3


# ---------- the boundary behavior (retriever level) ----------


def test_nonfinite_hit_becomes_the_lowest_score_and_sinks_to_the_bottom() -> None:
    """The poisoned hit arrives FIRST from the store (Postgres sorts NaN
    above every finite value in DESC); sanitized to the lowest possible
    similarity it must sit at the BOTTOM, where its score says it belongs."""
    versions = [
        make_version("cap-poisoned", "debug python"),
        make_version("cap-clean", "debug python"),
    ]
    candidates = [make_candidate("cap-poisoned"), make_candidate("cap-clean")]
    retriever = make_retriever({"cap-poisoned": math.nan}, versions)

    retrieval = retriever.retrieve("debug python", candidates)
    assert all(math.isfinite(s.score) for s in retrieval.candidates)
    assert retrieval.trace.nonfinite_scores == 1
    by_id = {s.candidate.capability_id: s for s in retrieval.candidates}
    assert by_id["cap-poisoned"].score == -1.0  # the lowest possible cosine similarity
    # finite order preserved (store order), poisoned hit sunk to the bottom
    assert [s.candidate.capability_id for s in retrieval.candidates] == [
        "cap-clean",
        "cap-poisoned",
    ]


def test_sanitized_nan_is_not_clamped_into_the_top_rerank_signal() -> None:
    """The reranker's clamp turns a raw NaN into the TOP retrieval signal
    (``max(0.0, min(1.0, nan)) == 1.0``) — a silent rank boost for the
    poisoned hit. Sanitized to -1.0 it clamps to the BOTTOM instead."""
    versions = [
        make_version("cap-poisoned", "debug python"),
        make_version("cap-clean", "debug python"),
    ]
    candidates = [make_candidate("cap-poisoned"), make_candidate("cap-clean")]
    retriever = make_retriever({"cap-poisoned": math.nan}, versions)
    retrieval = retriever.retrieve("debug python", candidates)

    rerank = HeuristicReranker().rerank(
        TaskDescriptor(task_text="debug python"), retrieval.candidates, _routing_context()
    )
    # identical docs/trust/facets: only the retrieval signal discriminates,
    # and the sanitized hit must lose, not win.
    assert rerank.ranked[0].candidate.capability_id == "cap-clean"
    assert rerank.ranked[-1].candidate.capability_id == "cap-poisoned"
    assert rerank.ranked[-1].retrieval_score == -1.0


def test_finite_scores_pass_through_unchanged_and_unflagged() -> None:
    """The fix changes NOTHING for finite scores: same values, same order,
    zero flags — the byte-identity guarantee the replay proves end-to-end."""
    versions = [
        make_version("cap-a", "debug python"),
        make_version("cap-b", "debug python"),
        make_version("cap-c", "debug python"),
    ]
    candidates = [make_candidate(f"cap-{c}") for c in "abc"]
    retriever = make_retriever({"cap-a": 0.9, "cap-b": 0.7, "cap-c": 0.5}, versions)

    retrieval = retriever.retrieve("debug python", candidates)
    assert retrieval.trace.nonfinite_scores == 0
    assert [(s.candidate.capability_id, s.score) for s in retrieval.candidates] == [
        ("cap-a", 0.9),
        ("cap-b", 0.7),
        ("cap-c", 0.5),
    ]
