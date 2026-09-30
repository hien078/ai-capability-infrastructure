"""Phase 9 unit acceptance: RouteCapabilitiesService (§14 pipeline, §36 telemetry).

Real pure stages (eligibility, heuristic reranker, composer) + fake IO: the
service must orchestrate in order, persist every stage trace, and treat an
empty bundle as success (ADR-008).
"""

from datetime import UTC, datetime
from typing import Any

from aci.application.route_capabilities import RouteCapabilitiesService
from aci.domain.capability.models import RouteCapabilitiesCommand
from aci.domain.policy.models import (
    ClientDescriptor,
    EligibleCandidate,
    PolicyRules,
    PolicySnapshot,
    ProtocolDescriptor,
    RequestContext,
    RoutingRequestContext,
    ScopeContext,
)
from aci.domain.routing.models import (
    ResolutionResult,
    ResolutionTrace,
    ResolvedItem,
    RetrievalResult,
    RetrievalTrace,
    ScoredCandidate,
)
from aci.routing.composer import MinimalBundleComposer
from aci.routing.eligibility import DefaultEligibilityPolicy
from aci.routing.rerankers.heuristic import HeuristicReranker

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def candidate(cid: str, *, kind: str = "skill", trust: str = "verified") -> EligibleCandidate:
    return EligibleCandidate(
        capability_id=cid,
        version="1.0.0",
        digest=f"sha256:{'a' * 64}",
        kind=kind,  # type: ignore[arg-type]
        facets={"domain": ["software-engineering"]},
        channel="production",
        status="active",
        trust_tier=trust,  # type: ignore[arg-type]
    )


class FakeLoader:
    def __init__(self, candidates: list[EligibleCandidate]) -> None:
        self.candidates = candidates
        self.channels: list[str] = []

    def load(self, channel: str = "production", *, status: Any = None) -> list[EligibleCandidate]:
        self.channels.append(channel)
        return list(self.candidates)


class FakeRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def retrieve(
        self, query: str, eligible: list[EligibleCandidate], *, limit: int = 30
    ) -> RetrievalResult:
        self.calls.append((query, limit))
        return RetrievalResult(
            candidates=[
                ScoredCandidate(candidate=c, score=0.5, document_text=f"doc {c.capability_id}")
                for c in eligible
            ],
            trace=RetrievalTrace(
                eligible_count=len(eligible),
                indexed_count=0,
                searched_count=len(eligible),
                returned_count=len(eligible),
                limit=limit,
                model_id="fake-embedder",
            ),
        )


class FakeResolver:
    def resolve(self, ranked: list[Any]) -> ResolutionResult:
        return ResolutionResult(
            selected=[
                ResolvedItem(
                    candidate=r.candidate,
                    role="primary",
                    reason_code="rank",
                    document_text=r.document_text,
                )
                for r in ranked
            ],
            dropped=[],
            trace=ResolutionTrace(
                input_count=len(ranked),
                selected_count=len(ranked),
                dropped_count=0,
            ),
        )


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


def request_context() -> RequestContext:
    return RequestContext(
        request_id="req_1",
        trace_id="trc_1",
        principal_id="p-1",
        client=ClientDescriptor(type="rest-client"),
        protocol=ProtocolDescriptor(type="rest", version="1"),
    )


def make_service(
    loader: FakeLoader,
    *,
    snapshots: FakePolicySnapshots | None = None,
) -> tuple[RouteCapabilitiesService, FakeRouteRuns, FakeBundles, FakeRetriever]:
    runs = FakeRouteRuns()
    bundles = FakeBundles()
    retriever = FakeRetriever()
    service = RouteCapabilitiesService(
        loader=loader,
        eligibility=DefaultEligibilityPolicy(),
        retriever=retriever,
        reranker=HeuristicReranker(),
        resolver=FakeResolver(),
        composer=MinimalBundleComposer(),
        policy_snapshots=snapshots or FakePolicySnapshots(),
        route_runs=runs,
        bundles=bundles,
    )
    return service, runs, bundles, retriever


def test_route_runs_pipeline_and_persists_telemetry() -> None:
    loader = FakeLoader([candidate("cap-debug")])
    service, runs, bundles, retriever = make_service(loader)

    result = service.route(
        RouteCapabilitiesCommand(task_text="debug python tracebacks"),
        RoutingRequestContext(
            client=ClientDescriptor(type="rest-client"),
            scope=ScopeContext(principal_id="p-1"),
            request_id="req_1",
        ),
        request=request_context(),
        now=NOW,
    )

    # Pipeline order: loader saw the channel, retriever saw the task text.
    assert loader.channels == ["production"]
    assert retriever.calls == [("debug python tracebacks", 30)]

    # Bundle pinned + persisted; run persisted with the bundle link.
    assert result.route_run_id.startswith("route_")
    assert result.bundle.bundle_id in bundles.bundles
    assert [i.capability_id for i in result.bundle.items] == ["cap-debug"]
    assert result.bundle.items[0].version == "1.0.0"
    assert result.bundle.items[0].digest == f"sha256:{'a' * 64}"

    run = runs.runs[result.route_run_id]
    assert run.request_id == "req_1"
    assert run.trace_id == "trc_1"
    assert run.principal_id == "p-1"
    assert run.client_type == "rest-client"
    assert run.protocol_type == "rest"
    assert run.eligible_count == 1
    assert run.bundle_id == result.bundle.bundle_id
    assert run.policy_snapshot_id is None
    assert run.error_code is None
    assert isinstance(run.latency_ms, int)
    assert run.reranker_implementation == "heuristic-reranker"
    assert run.composer_implementation == "minimal-bundle-composer"

    # §14: every stage emits trace data into the run record.
    assert set(run.stages) >= {"eligibility", "retrieval", "retrieved", "rerank", "reranked"}
    assert run.stages["eligibility"]["kept"] == 1
    assert run.stages["retrieved"][0]["capability_id"] == "cap-debug"
    assert run.stages["reranked"][0]["capability_id"] == "cap-debug"


def test_route_uses_latest_policy_snapshot() -> None:
    loader = FakeLoader([candidate("cap-debug")])
    snapshots = FakePolicySnapshots(
        PolicySnapshot(
            snapshot_id="snap-1",
            created_at=NOW,
            rules=PolicyRules(required_channel="production", min_trust_tier="verified"),
        )
    )
    service, runs, _, _ = make_service(loader, snapshots=snapshots)

    result = service.route(
        RouteCapabilitiesCommand(task_text="debug python"),
        RoutingRequestContext(
            client=ClientDescriptor(type="rest-client"),
            scope=ScopeContext(principal_id="p-1"),
            request_id="req_1",
        ),
        request=request_context(),
        now=NOW,
    )
    run = runs.runs[result.route_run_id]
    assert run.policy_snapshot_id == "snap-1"
    # Snapshot rules actually applied: verified candidate survives.
    assert run.eligible_count == 1


def test_route_policy_rules_exclude_low_trust() -> None:
    loader = FakeLoader([candidate("cap-shady", trust="untrusted")])
    snapshots = FakePolicySnapshots(
        PolicySnapshot(
            snapshot_id="snap-2",
            created_at=NOW,
            rules=PolicyRules(min_trust_tier="verified"),
        )
    )
    service, runs, bundles, _ = make_service(loader, snapshots=snapshots)

    result = service.route(
        RouteCapabilitiesCommand(task_text="debug python"),
        RoutingRequestContext(
            client=ClientDescriptor(type="rest-client"),
            scope=ScopeContext(principal_id="p-1"),
            request_id="req_1",
        ),
        request=request_context(),
        now=NOW,
    )
    run = runs.runs[result.route_run_id]
    assert run.eligible_count == 0
    assert run.stages["eligibility"]["kept"] == 0
    assert run.stages["eligibility"]["excluded"][0]["reason"] == "CAPABILITY_NOT_ELIGIBLE"
    # Empty bundle is a valid success (ADR-008): still persisted + linked.
    assert result.bundle.items == []
    assert run.bundle_id == result.bundle.bundle_id


def test_route_disallowed_kind_excluded_before_retrieval() -> None:
    loader = FakeLoader([candidate("cap-tool", kind="tool")])
    service, runs, _, retriever = make_service(loader)

    result = service.route(
        RouteCapabilitiesCommand(task_text="debug python", allowed_kinds=["skill"]),
        RoutingRequestContext(
            client=ClientDescriptor(type="rest-client"),
            scope=ScopeContext(principal_id="p-1"),
            request_id="req_1",
        ),
        request=request_context(),
        now=NOW,
    )
    run = runs.runs[result.route_run_id]
    assert run.eligible_count == 0
    assert run.stages["eligibility"]["excluded"][0]["reason"] == "CAPABILITY_NOT_ELIGIBLE"
    # Retrieval ran over an empty eligible set — never over ineligible content (ADR-009).
    assert retriever.calls[0][1] == 30
    assert result.bundle.items == []


def test_route_empty_registry_is_valid_success() -> None:
    loader = FakeLoader([])
    service, runs, bundles, _ = make_service(loader)

    result = service.route(
        RouteCapabilitiesCommand(task_text="anything"),
        RoutingRequestContext(
            client=ClientDescriptor(type="rest-client"),
            scope=ScopeContext(principal_id="p-1"),
            request_id="req_1",
        ),
        request=request_context(),
        now=NOW,
    )
    assert result.bundle.items == []
    assert result.bundle.bundle_id in bundles.bundles
    assert runs.runs[result.route_run_id].eligible_count == 0
