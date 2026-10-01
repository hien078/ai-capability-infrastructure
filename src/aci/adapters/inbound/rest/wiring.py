"""REST wiring: one container per process, protocol-typed services (§§13, 44).

The adapter owns infrastructure (engine, session factory, repository
implementations) and composes the application services, which depend on
protocols only. Nothing here reaches into domain internals beyond models.
"""

import json
from pathlib import Path

from fastapi import Request

from aci.adapters.inbound.opencode.catalog import CatalogProjection
from aci.adapters.outbound.model_provider.executor import OpenAICompatExecutor
from aci.adapters.outbound.model_provider.hashing import HashingEmbedder
from aci.adapters.outbound.model_provider.semantic import FastEmbedEmbedder
from aci.adapters.outbound.object_store.fs import FsObjectStore
from aci.adapters.outbound.pgvector.repository import SqlAlchemyEmbeddingRepository
from aci.adapters.outbound.postgres.agent_runs import SqlAlchemyAgentRunRepository
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
from aci.adapters.outbound.postgres.tasks import SqlAlchemyTaskRepository
from aci.application.delegate_task import ProfileDrivenAgentRuntime, UnconfiguredExecutor
from aci.application.evaluate_bundle import EvaluateBundleService
from aci.application.list_candidates import ProductionCandidateLoader
from aci.application.protocols import AgentExecutor
from aci.application.report_outcome import ReportOutcomeService
from aci.application.resolve_capability import ResolveCapabilityService
from aci.application.route_capabilities import RouteCapabilitiesService
from aci.application.run_agent_task import AgentRunService
from aci.application.search_capabilities import SearchCapabilitiesService
from aci.config import Settings
from aci.domain.agent.models import AgentProfile
from aci.evaluation.harness import BenchmarkHarness
from aci.providers.evaluation.rubric_evaluator import RubricContainmentEvaluator
from aci.routing.composer import MinimalBundleComposer
from aci.routing.dependencies import DefaultDependencyResolver
from aci.routing.eligibility import DefaultEligibilityPolicy
from aci.routing.rerankers.heuristic import HeuristicReranker
from aci.routing.retrieval import EmbeddingRetriever


def _build_embedder(settings: Settings) -> HashingEmbedder | FastEmbedEmbedder:
    """Pick the embedder from settings (§55: swappable behind the protocol).

    ``hashing`` (default) keeps everything offline and deterministic;
    ``fastembed`` uses a local ONNX semantic model (needs the `semantic`
    extra + one-time model download).
    """
    if settings.embedder == "fastembed":
        return FastEmbedEmbedder(model_name=settings.embedder_model)
    if settings.embedder != "hashing":
        raise ValueError(f"unknown ACI_EMBEDDER {settings.embedder!r} (hashing|fastembed)")
    return HashingEmbedder()


def _load_agent_profiles(path: str) -> dict[str, AgentProfile]:
    """Profiles are deployment DATA (§56.1): a JSON file of AgentProfile records.

    Accepts a bare list or ``{"profiles": [...]}``; every record is validated
    by the domain model (delegation-only execution mode included). An empty
    path means no profiles — the A2A surface then answers with clear
    unknown-profile errors instead of guessing.
    """
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    records = raw["profiles"] if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise ValueError(f"{path}: expected a JSON list of profile records")
    profiles = [AgentProfile.model_validate(r) for r in records]
    return {p.profile_id: p for p in profiles}


def _build_executor(
    settings: Settings,
    capabilities: SqlAlchemyCapabilityRepository,
    artifacts: SqlAlchemyArtifactStore,
    objects: FsObjectStore,
) -> AgentExecutor:
    """Pick the delegated-task executor (§56.1): a model client, or the honest null."""
    if settings.agent_model_base_url:
        return OpenAICompatExecutor(
            base_url=settings.agent_model_base_url,
            api_key=settings.agent_model_api_key,
            capabilities=capabilities,
            artifacts=artifacts,
            objects=objects,
        )
    return UnconfiguredExecutor()


def _build_agent_run_service(
    settings: Settings, run_store: object | None = None
) -> AgentRunService:
    """HarnessKernel wiring (ADR-014): a real model gateway when configured,
    else the run fails caller-visibly — never a silent default model."""
    from aci.adapters.inbound.rest.agent_run_wiring import build_agent_run_service

    return build_agent_run_service(settings, run_store=run_store)


class Container:
    """Infrastructure + service composition root for the REST adapter."""

    def __init__(self, settings: Settings) -> None:
        engine = make_engine(settings.database_url)
        sessions = make_session_factory(engine)
        #: exposed for read-only adapters (the ops UI) that need the SAME
        #: database as the repos — never a second URL source of truth.
        self.settings = settings

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
        agent_run_store = SqlAlchemyAgentRunRepository(sessions)

        loader = ProductionCandidateLoader(capabilities, releases, licenses, securities)
        eligibility = DefaultEligibilityPolicy()
        retriever = EmbeddingRetriever(capabilities, _build_embedder(settings), embeddings)
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
        # V4 evaluation service (§57): pluggable BundleEvaluator — the
        # deterministic rubric checker is the honest default; an LLM judge
        # plugs in through the same protocol without platform changes.
        self.evaluation_service = EvaluateBundleService(
            bundles, outcomes, RubricContainmentEvaluator()
        )
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
        # V3 agent platform (§56): delegated-task persistence + the runtime.
        # Profiles are deployment DATA (§56.1) loaded from ACI_AGENT_PROFILES;
        # the executor is a model client when ACI_AGENT_MODEL_BASE_URL is set,
        # otherwise the UnconfiguredExecutor keeps the A2A surface honest.
        self.tasks: SqlAlchemyTaskRepository = SqlAlchemyTaskRepository(sessions)
        self.agent_runtime = ProfileDrivenAgentRuntime(
            self.tasks,
            releases,
            _build_executor(settings, capabilities, artifacts, objects),
        )
        self.agent_profiles: dict[str, AgentProfile] = _load_agent_profiles(
            settings.agent_profiles_path
        )
        # HarnessKernel surface (ADR-014): the service-side agent runtime.
        # The model gateway is a real provider client when
        # ACI_AGENT_MODEL_BASE_URL is set; otherwise the run fails
        # caller-visibly (the honest default, same rule as the A2A executor).
        self.agent_run_service = _build_agent_run_service(settings, agent_run_store)
        # Raw protocol handles, for inbound adapters that project the registry
        # directly (MCP skills extension reads releases/artifacts/objects).
        self.releases = releases
        self.capabilities = capabilities
        self.artifacts = artifacts
        self.objects = objects


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container
