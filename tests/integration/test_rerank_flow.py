"""Phase 7 acceptance (plan §52): heuristic reranker over live retrieval output.

Full chain per §14: eligibility → retrieval → rerank. The reranker consumes
trusted metadata only (§17.1) and records implementation/version in trace.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aci.application.list_candidates import ProductionCandidateLoader
from aci.control_plane.promotion.service import PromotionService
from aci.domain.capability.models import TaskContext
from aci.domain.policy.models import ClientDescriptor, RoutingRequestContext, ScopeContext
from aci.domain.provenance.models import (
    LicenseAssessment,
    LicensePermissions,
    SecurityAssessment,
)
from aci.domain.routing.models import TaskDescriptor
from aci.providers.skills.ingestion import SkillIngestionService
from aci.routing.rerankers.heuristic import HeuristicReranker
from aci.routing.retrieval import EmbeddingRetriever

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


SKILLS = {
    "python": "debug python tracebacks step by step",
    "sql": "optimize slow sql queries with indexes",
    "docs": "write technical documentation pages",
}


def _ingest(ingestion: SkillIngestionService, tmp_path: Path, key: str) -> str:
    name = uid(f"skill-{key}")
    src = tmp_path / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {SKILLS[key]}\nversion: 1.0.0\n---\n\nbody\n",
        encoding="utf-8",
    )
    ingestion.ingest_local(src, now=NOW)
    return name


def _open_gates_and_promote(promotion, license_repo, security_repo, cap: str) -> None:
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


def _request_context() -> RoutingRequestContext:
    return RoutingRequestContext(
        client=ClientDescriptor(type="opencode", supported_features=["skills"]),
        scope=ScopeContext(principal_id="p-1"),
    )


def test_full_chain_rerank_ranks_relevant_skill_first(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    candidate_loader: ProductionCandidateLoader,
    retriever: EmbeddingRetriever,
    reranker: HeuristicReranker,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    caps = []
    for key in ("python", "sql", "docs"):
        cap = _ingest(ingestion, tmp_path, key)
        _open_gates_and_promote(promotion, license_repo, security_repo, cap)
        caps.append(cap)
    eligible = [c for c in candidate_loader.load() if c.capability_id in caps]
    assert len(eligible) == 3

    retrieval = retriever.retrieve("debug a python traceback", eligible, limit=3)
    result = reranker.rerank(
        TaskDescriptor(
            task_text="debug a python traceback",
            context=TaskContext(language="python"),
        ),
        retrieval.candidates,
        _request_context(),
    )

    # §52: reranker implementation/version recorded in trace.
    assert result.trace.implementation == "heuristic-reranker"
    assert result.trace.version == "2"
    assert result.trace.input_count == 3
    assert result.trace.output_count == 3

    # Ranks are 1..n and the relevant skill wins.
    assert [r.rank for r in result.ranked] == [1, 2, 3]
    assert result.ranked[0].candidate.capability_id == caps[0]
    assert result.ranked[0].score > 0.0
    assert result.ranked[0].retrieval_score > 0.0

    # §17.1: reranker consumed the trusted document, not raw bodies.
    assert "debug python tracebacks step by step" in retrieval.candidates[0].document_text
    assert "IGNORE" not in retrieval.candidates[0].document_text


def test_rerank_output_is_deterministic_and_subset_of_input(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    candidate_loader: ProductionCandidateLoader,
    retriever: EmbeddingRetriever,
    reranker: HeuristicReranker,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap = _ingest(ingestion, tmp_path, "python")
    _open_gates_and_promote(promotion, license_repo, security_repo, cap)
    eligible = [c for c in candidate_loader.load() if c.capability_id == cap]

    retrieval = retriever.retrieve("debug python", eligible)
    task = TaskDescriptor(task_text="debug python traceback")
    first = reranker.rerank(task, retrieval.candidates, _request_context())
    second = reranker.rerank(task, retrieval.candidates, _request_context())

    assert [r.candidate.capability_id for r in first.ranked] == [
        r.candidate.capability_id for r in second.ranked
    ]
    assert {r.candidate.capability_id for r in first.ranked} <= {cap}
    assert first.ranked[0].reasons  # trace explains the score (§14)
