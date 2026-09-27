"""Shared live-DB fixtures. Everything here skips when PostgreSQL is down."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import Engine, create_engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from aci.adapters.outbound.model_provider.hashing import HashingEmbedder  # noqa: E402
from aci.adapters.outbound.object_store.fs import FsObjectStore  # noqa: E402
from aci.adapters.outbound.pgvector.repository import (  # noqa: E402
    SqlAlchemyEmbeddingRepository,
)
from aci.adapters.outbound.postgres.assessments import (  # noqa: E402
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.adapters.outbound.postgres.base import make_session_factory  # noqa: E402
from aci.adapters.outbound.postgres.policy_snapshots import (  # noqa: E402
    SqlAlchemyPolicySnapshotRepository,
)
from aci.adapters.outbound.postgres.repositories import (  # noqa: E402
    SqlAlchemyArtifactStore,
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.source_records import (  # noqa: E402
    SqlAlchemySourceRecordRepository,
)
from aci.application.list_candidates import ProductionCandidateLoader  # noqa: E402
from aci.control_plane.promotion.service import PromotionService  # noqa: E402
from aci.providers.skills.ingestion import SkillIngestionService  # noqa: E402
from aci.routing.eligibility import DefaultEligibilityPolicy  # noqa: E402
from aci.routing.retrieval import EmbeddingRetriever  # noqa: E402

DB_URL = os.environ.get("ACI_DATABASE_URL", "postgresql+psycopg://aci:aci@localhost:5432/aci")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = create_engine(DB_URL, connect_args={"connect_timeout": 2})
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        eng.dispose()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    yield eng
    eng.dispose()


@pytest.fixture()
def sessions(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture()
def capability_repo(
    sessions: sessionmaker[Session],
) -> SqlAlchemyCapabilityRepository:
    return SqlAlchemyCapabilityRepository(sessions)


@pytest.fixture()
def release_repo(sessions: sessionmaker[Session]) -> SqlAlchemyReleaseRepository:
    return SqlAlchemyReleaseRepository(sessions)


@pytest.fixture()
def artifact_store(sessions: sessionmaker[Session]) -> SqlAlchemyArtifactStore:
    return SqlAlchemyArtifactStore(sessions)


@pytest.fixture()
def source_records(sessions: sessionmaker[Session]) -> SqlAlchemySourceRecordRepository:
    return SqlAlchemySourceRecordRepository(sessions)


@pytest.fixture()
def license_repo(sessions: sessionmaker[Session]) -> SqlAlchemyLicenseAssessmentRepository:
    return SqlAlchemyLicenseAssessmentRepository(sessions)


@pytest.fixture()
def security_repo(sessions: sessionmaker[Session]) -> SqlAlchemySecurityAssessmentRepository:
    return SqlAlchemySecurityAssessmentRepository(sessions)


@pytest.fixture()
def promotion(
    capability_repo: SqlAlchemyCapabilityRepository,
    release_repo: SqlAlchemyReleaseRepository,
    source_records: SqlAlchemySourceRecordRepository,
    license_repo: SqlAlchemyLicenseAssessmentRepository,
    security_repo: SqlAlchemySecurityAssessmentRepository,
) -> PromotionService:
    return PromotionService(
        capabilities=capability_repo,
        releases=release_repo,
        source_records=source_records,
        licenses=license_repo,
        securities=security_repo,
    )


@pytest.fixture()
def policy_snapshots(sessions: sessionmaker[Session]) -> SqlAlchemyPolicySnapshotRepository:
    return SqlAlchemyPolicySnapshotRepository(sessions)


@pytest.fixture()
def candidate_loader(
    capability_repo: SqlAlchemyCapabilityRepository,
    release_repo: SqlAlchemyReleaseRepository,
    license_repo: SqlAlchemyLicenseAssessmentRepository,
    security_repo: SqlAlchemySecurityAssessmentRepository,
) -> ProductionCandidateLoader:
    return ProductionCandidateLoader(
        capabilities=capability_repo,
        releases=release_repo,
        licenses=license_repo,
        securities=security_repo,
    )


@pytest.fixture()
def eligibility_policy() -> DefaultEligibilityPolicy:
    return DefaultEligibilityPolicy()


@pytest.fixture()
def embedder() -> HashingEmbedder:
    return HashingEmbedder()


@pytest.fixture()
def embedding_repo(sessions: sessionmaker[Session]) -> SqlAlchemyEmbeddingRepository:
    return SqlAlchemyEmbeddingRepository(sessions)


@pytest.fixture()
def retriever(
    capability_repo: SqlAlchemyCapabilityRepository,
    embedder: HashingEmbedder,
    embedding_repo: SqlAlchemyEmbeddingRepository,
) -> EmbeddingRetriever:
    return EmbeddingRetriever(
        capabilities=capability_repo, embedder=embedder, embeddings=embedding_repo
    )


@pytest.fixture()
def ingestion(
    capability_repo: SqlAlchemyCapabilityRepository,
    release_repo: SqlAlchemyReleaseRepository,
    artifact_store: SqlAlchemyArtifactStore,
    source_records: SqlAlchemySourceRecordRepository,
    tmp_path: Path,
) -> SkillIngestionService:
    return SkillIngestionService(
        capabilities=capability_repo,
        releases=release_repo,
        artifacts=artifact_store,
        source_records=source_records,
        objects=FsObjectStore(tmp_path / "objects"),
    )
