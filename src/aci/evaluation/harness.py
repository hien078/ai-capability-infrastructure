"""Benchmark harness (plan §§34, 70; §52 Phase 14): replay fixtures × variants.

Each case runs through the §70 variant matrix:

- A ``client_alone`` — no platform skills at all (baseline);
- B ``manual_baseline`` — the case's manually curated native skills (baseline);
- C ``retrieval_only`` — eligibility → pgvector retrieval, top-k by score;
- D ``retrieval_rerank`` — C plus the reranker's ranking;
- E ``full_pipeline`` — the real §14 service (telemetry included).

C/D reuse the exact stage components the production service uses, with the
later stages skipped — the harness never re-implements routing. Every result
pins the exact router implementations and the exact capability
id/version/digest selected, so reports can name the code and content behind
each number (§52 acceptance). Fixtures are replayable: re-running the same
case set produces a fresh run with fresh ids and the same measurements.
"""

import time
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from aci.application.list_candidates import ProductionCandidateLoader
from aci.application.protocols import (
    CandidateRetriever,
    CapabilityReranker,
    EligibilityPolicy,
    PolicySnapshotRepository,
    RouteRunRepository,
)
from aci.application.route_capabilities import RouteCapabilitiesService
from aci.domain.capability.models import RouteCapabilitiesCommand, TaskContext
from aci.domain.policy.models import (
    ClientDescriptor,
    EligibleCandidate,
    PolicyRules,
    ProtocolDescriptor,
    RequestContext,
)
from aci.domain.routing.models import TaskDescriptor
from aci.evaluation.metrics import result_metrics
from aci.evaluation.models import (
    VARIANTS,
    BenchmarkCase,
    BenchmarkResult,
    BenchmarkRun,
    RouterVersions,
    SelectedCapability,
    VariantId,
)

_MAX_ITEMS = 5  # matches the composer budget the full pipeline uses (§19)


class BenchmarkStore(Protocol):
    """Persistence port for benchmark cases/runs/results (§41)."""

    def put_case(self, case: BenchmarkCase) -> None: ...
    def put_run(self, run: BenchmarkRun) -> None: ...
    def put_result(self, result: BenchmarkResult) -> None: ...
    def list_results(self, run_id: str) -> list[BenchmarkResult]: ...


class BenchmarkHarness:
    """Executes cases through the variant matrix and records every result."""

    def __init__(
        self,
        route_service: RouteCapabilitiesService,
        loader: ProductionCandidateLoader,
        eligibility: EligibilityPolicy,
        retriever: CandidateRetriever,
        reranker: CapabilityReranker,
        policy_snapshots: PolicySnapshotRepository,
        route_runs: RouteRunRepository,
        store: BenchmarkStore,
    ) -> None:
        self._route_service = route_service
        self._loader = loader
        self._eligibility = eligibility
        self._retriever = retriever
        self._reranker = reranker
        self._policy_snapshots = policy_snapshots
        self._route_runs = route_runs
        self._store = store

    def run(
        self, cases: list[BenchmarkCase], *, label: str, now: datetime | None = None
    ) -> list[BenchmarkResult]:
        """Replay every case through every variant; returns the results.

        The case set is registered (idempotently) so results have
        referential integrity (§41); a re-run of the same fixtures creates a
        new run with fresh ids — the fixture itself never changes.
        """
        occurred_at = now or datetime.now(UTC)
        run = BenchmarkRun(
            run_id=f"bench_{uuid4().hex}",
            label=label,
            created_at=occurred_at,
            case_count=len(cases),
        )
        self._store.put_run(run)
        results: list[BenchmarkResult] = []
        for case in cases:
            self._store.put_case(case)
            for variant in VARIANTS:
                result = self._execute(case, variant, run.run_id, occurred_at)
                self._store.put_result(result)
                results.append(result)
        return results

    def _execute(
        self,
        case: BenchmarkCase,
        variant: VariantId,
        run_id: str,
        now: datetime,
    ) -> BenchmarkResult:
        def result(
            selected: list[SelectedCapability],
            metrics: dict[str, object],
            router: RouterVersions | None = None,
            route_run_id: str | None = None,
            bundle_id: str | None = None,
        ) -> BenchmarkResult:
            return BenchmarkResult(
                result_id=f"bres_{uuid4().hex}",
                run_id=run_id,
                case_id=case.case_id,
                variant=variant,
                created_at=now,
                route_run_id=route_run_id,
                bundle_id=bundle_id,
                selected=selected,
                router=router or RouterVersions(),
                metrics=dict(metrics),
            )

        if variant == "client_alone":
            # A: the client works with no platform skills — nothing to route.
            return result([], result_metrics(case, [], latency_ms=None))

        if variant == "manual_baseline":
            # B: the manually curated native baseline (§70) — resolved to
            # exact versions/digests from the active production set.
            active = {c.capability_id: c for c in self._loader.load("production")}
            selected = [
                SelectedCapability(
                    capability_id=c.capability_id,
                    version=c.version,
                    digest=c.digest,
                    kind=c.kind,
                )
                for c in (active.get(cid) for cid in case.baseline_capability_ids)
                if c is not None
            ]
            missing = [cid for cid in case.baseline_capability_ids if cid not in active]
            metrics = result_metrics(case, [s.capability_id for s in selected], latency_ms=None)
            metrics["baseline_missing"] = missing
            return result(selected, metrics)

        # C/D share the eligibility + retrieval prefix; D adds the reranker.
        started = time.perf_counter()
        snapshot = self._policy_snapshots.latest_snapshot()
        rules = snapshot.rules if snapshot is not None else PolicyRules()
        context = self._envelope().to_routing_context(TaskContext())
        candidates = self._loader.load(rules.required_channel)
        decision = self._eligibility.filter(candidates, context, rules, allowed_kinds=["skill"])
        retrieval = self._retriever.retrieve(case.task_text, decision.kept, limit=30)

        if variant == "retrieval_only":
            top = sorted(retrieval.candidates, key=lambda s: -s.score)[:_MAX_ITEMS]
            latency_ms = int((time.perf_counter() - started) * 1000)
            selected = [self._selected(s.candidate) for s in top]
            return result(
                selected,
                result_metrics(case, [s.capability_id for s in selected], latency_ms=latency_ms),
            )

        if variant == "retrieval_rerank":
            task = TaskDescriptor(task_text=case.task_text, context=TaskContext())
            rerank = self._reranker.rerank(task, retrieval.candidates, context)
            latency_ms = int((time.perf_counter() - started) * 1000)
            selected = [self._selected(r.candidate) for r in rerank.ranked[:_MAX_ITEMS]]
            return result(
                selected,
                result_metrics(case, [s.capability_id for s in selected], latency_ms=latency_ms),
                router=RouterVersions(
                    reranker_implementation=rerank.trace.implementation,
                    reranker_version=rerank.trace.version,
                ),
            )

        # E: the real §14 pipeline, §36 telemetry included.
        command = RouteCapabilitiesCommand(task_text=case.task_text)
        envelope = self._envelope()
        routed = self._route_service.route(
            command, envelope.to_routing_context(TaskContext()), request=envelope, now=now
        )
        run = self._route_runs.get_route_run(routed.route_run_id)
        selected = [
            SelectedCapability(
                capability_id=item.capability_id,
                version=item.version,
                digest=item.digest,
                kind=item.kind,
            )
            for item in routed.bundle.items
        ]
        router = RouterVersions()
        run_latency_ms: int | None = None
        if run is not None:
            router = RouterVersions(
                reranker_implementation=run.reranker_implementation,
                reranker_version=run.reranker_version,
                composer_implementation=run.composer_implementation,
                composer_version=run.composer_version,
            )
            run_latency_ms = run.latency_ms
        return result(
            selected,
            result_metrics(case, [s.capability_id for s in selected], latency_ms=run_latency_ms),
            router=router,
            route_run_id=routed.route_run_id,
            bundle_id=routed.bundle.bundle_id,
        )

    @staticmethod
    def _selected(candidate: EligibleCandidate) -> SelectedCapability:
        """EligibleCandidate → exact version+digest reference."""
        return SelectedCapability(
            capability_id=candidate.capability_id,
            version=candidate.version,
            digest=candidate.digest,
            kind=candidate.kind,
        )

    @staticmethod
    def _envelope() -> RequestContext:
        """Synthetic §11.1 envelope — the benchmark is its own principal."""
        return RequestContext(
            request_id=f"req_{uuid4().hex}",
            trace_id=f"trc_{uuid4().hex}",
            principal_id="benchmark",
            client=ClientDescriptor(type="benchmark-harness"),
            protocol=ProtocolDescriptor(type="benchmark", version="1"),
        )
