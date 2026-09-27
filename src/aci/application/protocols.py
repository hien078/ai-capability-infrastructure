"""Application-layer repository protocols (plan §44).

Implementations live in adapters (e.g. Postgres) and own their sessions:
no SQLAlchemy/FastAPI/protocol types may appear in this module.
"""

from datetime import datetime
from typing import Protocol

from aci.domain.capability.models import (
    Capability,
    CapabilityArtifact,
    CapabilityBinding,
    CapabilityBundle,
    CapabilityRelation,
    CapabilityRelease,
    CapabilityVersion,
    ReleaseChannel,
    ReleaseStatus,
    RouteCapabilitiesCommand,
)
from aci.domain.policy.models import (
    EligibilityDecision,
    EligibleCandidate,
    PolicyRules,
    PolicySnapshot,
    RoutingRequestContext,
)
from aci.domain.provenance.models import LicenseAssessment, SecurityAssessment
from aci.domain.routing.models import (
    RankedCandidate,
    RerankResult,
    ResolutionResult,
    RetrievalResult,
    RetrievedDocument,
    ScoredCandidate,
    TaskDescriptor,
    TrustedRoutingDocument,
)
from aci.domain.skills.models import SourceProvenance


class CapabilityRepository(Protocol):
    """One authoritative registry: identities + immutable versions + bindings."""

    def create_capability(self, capability: Capability) -> Capability: ...
    def get_capability(self, capability_id: str) -> Capability | None: ...
    def create_version(self, version: CapabilityVersion) -> CapabilityVersion: ...
    def get_version(self, capability_id: str, version: str) -> CapabilityVersion | None: ...
    def list_versions(self, capability_id: str) -> list[CapabilityVersion]: ...
    def put_binding(self, binding: CapabilityBinding) -> CapabilityBinding: ...
    def get_binding(self, binding_id: str) -> CapabilityBinding | None: ...


class ReleaseRepository(Protocol):
    """Mutable channel/state pointers over immutable versions (ADR-002)."""

    def set_release(self, release: CapabilityRelease) -> CapabilityRelease: ...
    def get_release(
        self, capability_id: str, channel: ReleaseChannel
    ) -> CapabilityRelease | None: ...
    def list_releases(self, capability_id: str) -> list[CapabilityRelease]: ...
    def list_channel(
        self, channel: ReleaseChannel, *, status: ReleaseStatus | None = None
    ) -> list[CapabilityRelease]: ...


class ArtifactStore(Protocol):
    """Immutable content-package metadata, keyed by exact version."""

    def put_artifact(self, artifact: CapabilityArtifact) -> CapabilityArtifact: ...
    def get_artifact(self, capability_id: str, version: str) -> CapabilityArtifact | None: ...


class ObjectStore(Protocol):
    """Content-addressed blob storage. Keys are SHA-256 hex digests."""

    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def exists(self, key: str) -> bool: ...


class SourceRecordRepository(Protocol):
    """Provenance trail for ingested sources (plan §23)."""

    def add_source_record(self, record: SourceProvenance) -> SourceProvenance: ...
    def list_source_records(self, capability_id: str) -> list[SourceProvenance]: ...


class LicenseAssessmentRepository(Protocol):
    """License gate records per exact version (plan §24)."""

    def put_assessment(self, assessment: LicenseAssessment) -> LicenseAssessment: ...
    def get_assessment(self, capability_id: str, version: str) -> LicenseAssessment | None: ...


class SecurityAssessmentRepository(Protocol):
    """Security gate records per exact version (plan §25)."""

    def put_assessment(self, assessment: SecurityAssessment) -> SecurityAssessment: ...
    def get_assessment(self, capability_id: str, version: str) -> SecurityAssessment | None: ...


class EligibilityPolicy(Protocol):
    """Filter candidates before retrieval/rerank (ADR-009). Never rescues."""

    def filter(
        self,
        candidates: list[EligibleCandidate],
        context: RoutingRequestContext,
        rules: PolicyRules,
        *,
        allowed_kinds: list[str],
    ) -> EligibilityDecision: ...


class PolicySnapshotRepository(Protocol):
    """Snapshot-able eligibility rules (plan §46)."""

    def put_snapshot(self, snapshot: PolicySnapshot) -> PolicySnapshot: ...
    def get_snapshot(self, snapshot_id: str) -> PolicySnapshot | None: ...
    def latest_snapshot(self) -> PolicySnapshot | None: ...


class Embedder(Protocol):
    """Turns trusted text into vectors. Implementations live in adapters."""

    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingRepository(Protocol):
    """Trusted routing documents + their vectors (plan §§41, 46).

    Documents are immutable per (capability_id, version, doc_type); vectors are
    per (document, model_id) so embedder changes never collide.
    """

    def put_document(self, document: TrustedRoutingDocument, vector: list[float]) -> None: ...
    def get_indexed_document(
        self, capability_id: str, version: str, model_id: str
    ) -> TrustedRoutingDocument | None: ...
    def search(
        self,
        query_vector: list[float],
        pairs: list[tuple[str, str]],
        *,
        model_id: str,
        limit: int,
    ) -> list[RetrievedDocument]: ...


class CandidateRetriever(Protocol):
    """Retrieve 10-30 candidates from the eligible set (plan §16, ADR-009)."""

    def retrieve(
        self, query: str, eligible: list[EligibleCandidate], *, limit: int = 30
    ) -> RetrievalResult: ...


class CapabilityReranker(Protocol):
    """Rank retrieved candidates (plan §17). Swappable interface.

    Implementations consume trusted/sanitized routing metadata only (§17.1):
    never raw skill bodies, and they can never rescue ineligible content —
    eligibility already ran (ADR-009).
    """

    def rerank(
        self,
        task: TaskDescriptor,
        candidates: list[ScoredCandidate],
        context: RoutingRequestContext,
    ) -> RerankResult: ...


class RelationRepository(Protocol):
    """Directed capability relations (plan §18; V1: requires/conflicts_with/checks)."""

    def put_relation(self, relation: CapabilityRelation) -> CapabilityRelation: ...
    def list_relations(self, source_capability_id: str) -> list[CapabilityRelation]: ...


class DependencyResolver(Protocol):
    """Resolve relations after rerank (plan §18). Drops, never rescues."""

    def resolve(self, ranked: list[RankedCandidate]) -> ResolutionResult: ...


class BundleComposer(Protocol):
    """Compose a minimal sufficient bundle, 0-5 items (plan §19; ADR-008)."""

    def compose(
        self,
        resolution: ResolutionResult,
        command: RouteCapabilitiesCommand,
        *,
        route_run_id: str,
        now: datetime,
    ) -> CapabilityBundle: ...
