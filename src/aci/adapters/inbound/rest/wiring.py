"""REST wiring: one container per process, protocol-typed services (§§13, 44).

The adapter owns infrastructure (engine, session factory, repository
implementations) and composes the application services, which depend on
protocols only. Nothing here reaches into domain internals beyond models.
"""

from pathlib import Path

from fastapi import Request

from aci.adapters.inbound.opencode.catalog import CatalogProjection
from aci.adapters.outbound.model_provider.hashing import HashingEmbedder
from aci.adapters.outbound.object_store.fs import FsObjectStore
from aci.adapters.outbound.pgvector.repository import SqlAlchemyEmbeddingRepository
from aci.adapters.outbound.postgres.assessments import (
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.adapters.outbound.postgres.base import make_engine, make_session_factory
from aci.adapters.outbound.postgres.benchmarks import SqlAlchemyBenchmarkStore
from aci.adapters.outbound.postgres.bundles import SqlAlchemyBundleRepository
from aci.adapters.outbound.postgres.outcomes import SqlAlchemyOutcomeRecorder
from aci.adapters.outbound.postgres.policy_snapshots import (
    SqlAlchemyPolicySnapshotRepository,
)
from aci.adapters.outbound.postgres.relations import SqlAlchemyRelationRepository
from aci.adapters.outbound.postgres.repositories import (
    SqlAlchemyArtifactStore,
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.route_runs import SqlAlchemyRouteRunRepository
from aci.application.list_candidates import ProductionCandidateLoader
from aci.application.report_outcome import ReportOutcomeService
from aci.application.resolve_capability import ResolveCapabilityService
from aci.application.route_capabilities import RouteCapabilitiesService
from aci.application.search_capabilities import SearchCapabilitiesService
from aci.config import Settings
from aci.evaluation.harness import BenchmarkHarness
from aci.routing.composer import MinimalBundleComposer
from aci.routing.dependencies import DefaultDependencyResolver
from aci.routing.eligibility import DefaultEligibilityPolicy
from aci.routing.rerankers.heuristic import HeuristicReranker
from aci.routing.retrieval import EmbeddingRetriever


class Container:
    """Infrastructure + service composition root for the REST adapter."""

    def __init__(self, settings: Settings) -> None:
        engine = make_engine(settings.database_url)
        sessions = make_session_factory(engine)

        capabilities = SqlAlchemyCapabilityRepository(sessions)
        releases = SqlAlchemyReleaseRepository(sessions)
        artifacts = SqlAlchemyArtifactStore(sessions)
        licenses = SqlAlchemyLicenseAssessmentRepository(sessions)
        securities = SqlAlchemySecurityAssessmentRepository(sessions)
        policy_snapshots = SqlAlchemyPolicySnapshotRepository(sessions)
        relations = SqlAlchemyRelationRepository(sessions)
        embeddings = SqlAlchemyEmbeddingRepository(sessions)
        route_runs = SqlAlchemyRouteRunRepository(sessions)
        bundles = SqlAlchemyBundleRepository(sessions)
        outcomes = SqlAlchemyOutcomeRecorder(sessions)
        benchmark_store = SqlAlchemyBenchmarkStore(sessions)

        loader = ProductionCandidateLoader(capabilities, releases, licenses, securities)
        eligibility = DefaultEligibilityPolicy()
        retriever = EmbeddingRetriever(capabilities, HashingEmbedder(), embeddings)
        reranker = HeuristicReranker()
        resolver = DefaultDependencyResolver(relations, releases)
        composer = MinimalBundleComposer()
        objects = FsObjectStore(Path(settings.object_store_root))

        self.route_service = RouteCapabilitiesService(
            loader,
            eligibility,
            retriever,
            reranker,
            resolver,
            composer,
            policy_snapshots,
            route_runs,
            bundles,
        )
        self.search_service = SearchCapabilitiesService(loader, retriever, capabilities)
        self.resolve_service = ResolveCapabilityService(capabilities, artifacts)
        self.outcome_service = ReportOutcomeService(outcomes, bundles)
        self.catalog = CatalogProjection(releases, capabilities, artifacts, objects)
        self.benchmark_harness = BenchmarkHarness(
            self.route_service,
            loader,
            eligibility,
            retriever,
            reranker,
            policy_snapshots,
            route_runs,
            benchmark_store,
        )
        self.benchmark_store = benchmark_store
        self.route_runs = route_runs
        self.bundles = bundles
        # Raw protocol handles, for inbound adapters that project the registry
        # directly (MCP skills extension reads releases/artifacts/objects).
        self.releases = releases
        self.capabilities = capabilities
        self.artifacts = artifacts
        self.objects = objects


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container
