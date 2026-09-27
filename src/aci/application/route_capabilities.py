"""Route capabilities: the full §14 pipeline as one application service.

eligibility → retrieval → rerank → dependency resolution → composition,
with every stage's trace persisted into the route run (§36 telemetry).
The service depends on protocols only (§44); transport adapters build the
RequestContext/commands and translate results (§13).
"""

import time
from datetime import UTC, datetime
from uuid import uuid4

from aci.application.list_candidates import ProductionCandidateLoader
from aci.application.protocols import (
    BundleComposer,
    BundleRepository,
    CandidateRetriever,
    CapabilityReranker,
    DependencyResolver,
    EligibilityPolicy,
    PolicySnapshotRepository,
    RouteRunRepository,
)
from aci.domain.capability.models import RouteCapabilitiesCommand
from aci.domain.policy.models import PolicyRules, RequestContext, RoutingRequestContext
from aci.domain.routing.models import RouteResult, RouteRun, TaskDescriptor


class RouteCapabilitiesService:
    def __init__(
        self,
        loader: ProductionCandidateLoader,
        eligibility: EligibilityPolicy,
        retriever: CandidateRetriever,
        reranker: CapabilityReranker,
        resolver: DependencyResolver,
        composer: BundleComposer,
        policy_snapshots: PolicySnapshotRepository,
        route_runs: RouteRunRepository,
        bundles: BundleRepository,
    ) -> None:
        self._loader = loader
        self._eligibility = eligibility
        self._retriever = retriever
        self._reranker = reranker
        self._resolver = resolver
        self._composer = composer
        self._policy_snapshots = policy_snapshots
        self._route_runs = route_runs
        self._bundles = bundles

    def route(
        self,
        command: RouteCapabilitiesCommand,
        context: RoutingRequestContext,
        *,
        request: RequestContext,
        now: datetime | None = None,
    ) -> RouteResult:
        started = time.perf_counter()
        occurred_at = now or datetime.now(UTC)
        route_run_id = f"route_{uuid4().hex}"

        # 0. Snapshot-able policy rules (§46); default rules when none stored.
        snapshot = self._policy_snapshots.latest_snapshot()
        rules = snapshot.rules if snapshot is not None else PolicyRules()

        # 1-2. Eligibility before anything else (ADR-009).
        candidates = self._loader.load(rules.required_channel)
        decision = self._eligibility.filter(
            candidates, context, rules, allowed_kinds=command.allowed_kinds
        )

        # 3. Retrieval over the eligible set only.
        retrieval = self._retriever.retrieve(command.task_text, decision.kept, limit=30)

        # 4. Rerank over trusted metadata only (§17.1).
        task = TaskDescriptor(task_text=command.task_text, context=command.context)
        rerank = self._reranker.rerank(task, retrieval.candidates, context)

        # 5. Dependency resolution (§18), then minimal composition (§19).
        resolution = self._resolver.resolve(rerank.ranked)
        bundle = self._composer.compose(
            resolution, command, route_run_id=route_run_id, now=occurred_at
        )

        latency_ms = int((time.perf_counter() - started) * 1000)
        run = RouteRun(
            route_run_id=route_run_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            created_at=occurred_at,
            principal_id=request.principal_id,
            organization_id=request.organization_id,
            workspace_id=request.workspace_id,
            client_type=request.client.type,
            client_version=request.client.version,
            protocol_type=request.protocol.type,
            task_text=command.task_text,
            policy_snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
            eligible_count=len(decision.kept),
            stages={
                "eligibility": {
                    "kept": len(decision.kept),
                    "excluded": [e.model_dump(mode="json") for e in decision.excluded],
                },
                "retrieval": retrieval.trace.model_dump(mode="json"),
                "retrieved": [
                    {
                        "capability_id": s.candidate.capability_id,
                        "version": s.candidate.version,
                        "score": s.score,
                    }
                    for s in retrieval.candidates
                ],
                "rerank": rerank.trace.model_dump(mode="json"),
                "reranked": [
                    {
                        "capability_id": r.candidate.capability_id,
                        "version": r.candidate.version,
                        "score": r.score,
                        "rank": r.rank,
                    }
                    for r in rerank.ranked
                ],
                "resolution": {
                    "trace": resolution.trace.model_dump(mode="json"),
                    "dropped": [d.model_dump(mode="json") for d in resolution.dropped],
                },
            },
            reranker_implementation=rerank.trace.implementation,
            reranker_version=rerank.trace.version,
            composer_implementation=self._composer.implementation,
            composer_version=self._composer.version,
            latency_ms=latency_ms,
            error_code=None,
            bundle_id=bundle.bundle_id,
        )
        # Telemetry first, then the bundle: bundles.route_run_id is FK'd to
        # route_runs, while route_runs.bundle_id is a plain pointer — so this
        # order satisfies every constraint without cross-repo transactions.
        self._route_runs.put_route_run(run)
        self._bundles.put_bundle(bundle)
        return RouteResult(route_run_id=route_run_id, bundle=bundle)
