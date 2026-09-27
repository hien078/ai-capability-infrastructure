"""Phase 8 acceptance (plan §52): dependency resolver + composer, live DB.

Full §14 chain: eligibility → retrieval → rerank → resolve → compose.
Acceptance: empty bundle valid, no conflicting items, dependencies valid,
deterministic schema validation, context budget enforced.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aci.adapters.outbound.postgres.relations import SqlAlchemyRelationRepository
from aci.adapters.outbound.postgres.repositories import (
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.application.list_candidates import ProductionCandidateLoader
from aci.control_plane.promotion.service import PromotionService
from aci.domain.capability.models import (
    CapabilityRelation,
    RouteCapabilitiesCommand,
)
from aci.domain.policy.models import ClientDescriptor, RoutingRequestContext, ScopeContext
from aci.domain.routing.models import TaskDescriptor
from aci.providers.skills.ingestion import SkillIngestionService
from aci.routing.composer import MinimalBundleComposer
from aci.routing.dependencies import DefaultDependencyResolver
from aci.routing.rerankers.heuristic import HeuristicReranker
from aci.routing.retrieval import EmbeddingRetriever

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def ingest(ingestion: SkillIngestionService, tmp_path: Path, key: str, description: str) -> str:
    name = uid(f"skill-{key}")
    src = tmp_path / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: 1.0.0\n---\n\nbody\n",
        encoding="utf-8",
    )
    ingestion.ingest_local(src, now=NOW)
    return name


def open_gates_and_promote(promotion, license_repo, security_repo, cap: str) -> None:
    from aci.domain.provenance.models import (
        LicenseAssessment,
        LicensePermissions,
        SecurityAssessment,
    )

    license_repo.put_assessment(
        LicenseAssessment(
            assessment_id=f"lic-{uuid.uuid4().hex[:8]}",
            capability_id=cap,
            version="1.0.0",
            license_identifier="MIT",
            permissions=LicensePermissions(can_redistribute=True),
            assessed_at=NOW,
            assessed_by="legal-1",
        )
    )
    security_repo.put_assessment(
        SecurityAssessment(
            assessment_id=f"sec-{uuid.uuid4().hex[:8]}",
            capability_id=cap,
            version="1.0.0",
            scan_status="passed",
            scanned_at=NOW,
            scanner_version="s",
        )
    )
    promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)


def put_relation(relation_repo: SqlAlchemyRelationRepository, **kwargs: str) -> CapabilityRelation:
    relation = CapabilityRelation(
        relation_id=uid("rel"),
        source_version_constraint=None,
        target_version_constraint=None,
        metadata={},
        **kwargs,  # type: ignore[arg-type]
    )
    relation_repo.put_relation(relation)
    return relation


def _ctx() -> RoutingRequestContext:
    return RoutingRequestContext(
        client=ClientDescriptor(type="opencode", supported_features=["skills"]),
        scope=ScopeContext(principal_id="p-1"),
    )


def test_relation_repository_roundtrip(
    relation_repo: SqlAlchemyRelationRepository,
    capability_repo: SqlAlchemyCapabilityRepository,
) -> None:
    from aci.domain.capability.models import Capability

    cap = uid("cap")
    capability_repo.create_capability(Capability(id=cap, kind="skill", created_at=NOW))
    relation = CapabilityRelation(
        relation_id=uid("rel"),
        source_capability_id=cap,
        target_capability_id=cap,
        relation="checks",
        metadata={"note": "self-check"},
    )
    relation_repo.put_relation(relation)

    listed = relation_repo.list_relations(cap)
    assert len(listed) == 1
    assert listed[0].relation == "checks"
    assert listed[0].metadata == {"note": "self-check"}
    assert listed[0].target_capability_id == cap

    # Registry facts are append-only: the same relation_id may not be rewritten.
    with pytest.raises(ValueError, match="already exists"):
        relation_repo.put_relation(
            CapabilityRelation(
                relation_id=relation.relation_id,
                source_capability_id=cap,
                target_capability_id=cap,
                relation="requires",
            )
        )


def test_full_chain_composes_checked_bundle(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    candidate_loader: ProductionCandidateLoader,
    retriever: EmbeddingRetriever,
    reranker: HeuristicReranker,
    resolver: DefaultDependencyResolver,
    composer: MinimalBundleComposer,
    relation_repo: SqlAlchemyRelationRepository,
    capability_repo: SqlAlchemyCapabilityRepository,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap_debug = ingest(ingestion, tmp_path, "debug", "debug python tracebacks root cause")
    cap_verify = ingest(ingestion, tmp_path, "verify", "verify regression tests after debugging")
    for cap in (cap_debug, cap_verify):
        open_gates_and_promote(promotion, license_repo, security_repo, cap)
    put_relation(
        relation_repo,
        source_capability_id=cap_debug,
        relation="checks",
        target_capability_id=cap_verify,
    )

    eligible = [c for c in candidate_loader.load() if c.capability_id in {cap_debug, cap_verify}]
    retrieval = retriever.retrieve("debug python and verify regression", eligible, limit=5)
    reranked = reranker.rerank(
        TaskDescriptor(task_text="debug python and verify regression"), retrieval.candidates, _ctx()
    )
    resolution = resolver.resolve(reranked.ranked)
    bundle = composer.compose(
        resolution,
        RouteCapabilitiesCommand(task_text="debug python and verify regression"),
        route_run_id="route_test",
        now=NOW,
    )

    # CHECKS re-roles the verifier; debug stays primary (higher rank).
    roles = {i.capability_id: i.role for i in bundle.items}
    assert roles[cap_debug] == "primary"
    assert roles[cap_verify] == "check"
    assert bundle.execution_order[0] == cap_debug
    assert bundle.budget is not None and bundle.budget.max_items == 5

    # Version + digest pinning against the live registry (§52).
    for item in bundle.items:
        version = capability_repo.get_version(item.capability_id, item.version)
        assert version is not None
        assert item.digest == version.content_digest


def test_conflict_drops_lower_ranked_in_live_chain(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    candidate_loader: ProductionCandidateLoader,
    retriever: EmbeddingRetriever,
    reranker: HeuristicReranker,
    resolver: DefaultDependencyResolver,
    composer: MinimalBundleComposer,
    relation_repo: SqlAlchemyRelationRepository,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap_a = ingest(ingestion, tmp_path, "a", "debug python tracebacks")
    cap_b = ingest(ingestion, tmp_path, "b", "debug python tracebacks alternative")
    for cap in (cap_a, cap_b):
        open_gates_and_promote(promotion, license_repo, security_repo, cap)
    put_relation(
        relation_repo,
        source_capability_id=cap_a,
        relation="conflicts_with",
        target_capability_id=cap_b,
    )

    eligible = [c for c in candidate_loader.load() if c.capability_id in {cap_a, cap_b}]
    retrieval = retriever.retrieve("debug python tracebacks", eligible, limit=5)
    reranked = reranker.rerank(
        TaskDescriptor(task_text="debug python"), retrieval.candidates, _ctx()
    )
    resolution = resolver.resolve(reranked.ranked)
    bundle = composer.compose(
        resolution, RouteCapabilitiesCommand(task_text="debug python"), route_run_id="r", now=NOW
    )

    # No conflicting items in the bundle (§52 acceptance).
    ids = [i.capability_id for i in bundle.items]
    assert cap_a in ids
    assert cap_b not in ids
    assert [d.reason for d in resolution.dropped if d.capability_id == cap_b] == ["CONFLICTS_WITH"]


def test_requires_without_active_release_yields_empty_bundle(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    candidate_loader: ProductionCandidateLoader,
    retriever: EmbeddingRetriever,
    reranker: HeuristicReranker,
    resolver: DefaultDependencyResolver,
    composer: MinimalBundleComposer,
    relation_repo: SqlAlchemyRelationRepository,
    release_repo: SqlAlchemyReleaseRepository,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap_main = ingest(ingestion, tmp_path, "main", "debug python tracebacks")
    cap_dep = ingest(ingestion, tmp_path, "dep", "helper library")  # exists, never promoted
    open_gates_and_promote(promotion, license_repo, security_repo, cap_main)
    put_relation(
        relation_repo,
        source_capability_id=cap_main,
        relation="requires",
        target_capability_id=cap_dep,
    )

    eligible = [c for c in candidate_loader.load() if c.capability_id == cap_main]
    retrieval = retriever.retrieve("debug python tracebacks", eligible, limit=5)
    reranked = reranker.rerank(
        TaskDescriptor(task_text="debug python"), retrieval.candidates, _ctx()
    )
    resolution = resolver.resolve(reranked.ranked)
    bundle = composer.compose(
        resolution, RouteCapabilitiesCommand(task_text="debug python"), route_run_id="r", now=NOW
    )

    # Dependencies valid (§52): the dependent is dropped, and an empty bundle
    # is a normal successful result (§19.1, ADR-008).
    assert bundle.items == []
    assert [d.reason for d in resolution.dropped if d.capability_id == cap_main] == [
        "REQUIRES_UNRESOLVED"
    ]
    assert release_repo.get_release(cap_dep, "production") is None
