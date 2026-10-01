"""Application-layer repository protocols (plan §44).

Implementations live in adapters (e.g. Postgres) and own their sessions:
no SQLAlchemy/FastAPI/protocol types may appear in this module.
"""

from datetime import datetime
from typing import Protocol

from aci.domain.agent.models import (
    AgentProfile,
    AgentTask,
    ExecutorResult,
    SkillGrant,
    TaskArtifact,
    TaskMessage,
)
from aci.domain.capability.models import (
    Capability,
    CapabilityArtifact,
    CapabilityBinding,
    CapabilityBundle,
    CapabilityKind,
    CapabilityRelation,
    CapabilityRelease,
    CapabilityVersion,
    OutcomeEvidence,
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
    RouteRun,
    ScoredCandidate,
    TaskDescriptor,
    TrustedRoutingDocument,
)
from aci.domain.runtime.persistence import AgentRunEventRecord, AgentRunRecord
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
    # Bulk reads: the candidate loader annotates every active release, so
    # per-candidate single-row lookups would be O(catalog) roundtrips per
    # request — these keep one routing request at a handful of queries.
    def get_versions(self, pairs: list[tuple[str, str]]) -> list[CapabilityVersion]: ...
    def get_capabilities(self, ids: list[str]) -> list[Capability]: ...


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
    def get_artifacts(self, pairs: list[tuple[str, str]]) -> list[CapabilityArtifact]: ...


class ObjectStore(Protocol):
    """Content-addressed blob storage. Keys are SHA-256 hex digests."""

    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def exists(self, key: str) -> bool: ...


class SourceRecordRepository(Protocol):
    """Provenance trail for ingested sources (plan §23)."""

    def add_source_record(self, record: SourceProvenance) -> SourceProvenance: ...
    def list_source_records(self, capability_id: str) -> list[SourceProvenance]: ...
    def set_ingestion_status(self, capability_id: str, version: str, status: str) -> bool: ...


class LicenseAssessmentRepository(Protocol):
    """License gate records per exact version (plan §24)."""

    def put_assessment(self, assessment: LicenseAssessment) -> LicenseAssessment: ...
    def get_assessment(self, capability_id: str, version: str) -> LicenseAssessment | None: ...
    def get_assessments(self, pairs: list[tuple[str, str]]) -> list[LicenseAssessment]: ...


class SecurityAssessmentRepository(Protocol):
    """Security gate records per exact version (plan §25)."""

    def put_assessment(self, assessment: SecurityAssessment) -> SecurityAssessment: ...
    def get_assessment(self, capability_id: str, version: str) -> SecurityAssessment | None: ...
    def get_assessments(self, pairs: list[tuple[str, str]]) -> list[SecurityAssessment]: ...


class EligibilityPolicy(Protocol):
    """Filter candidates before retrieval/rerank (ADR-009). Never rescues."""

    def filter(
        self,
        candidates: list[EligibleCandidate],
        context: RoutingRequestContext,
        rules: PolicyRules,
        *,
        allowed_kinds: list[CapabilityKind],
    ) -> EligibilityDecision: ...


class PolicySnapshotRepository(Protocol):
    """Snapshot-able eligibility rules (plan §46)."""

    def put_snapshot(self, snapshot: PolicySnapshot) -> PolicySnapshot: ...
    def get_snapshot(self, snapshot_id: str) -> PolicySnapshot | None: ...
    def latest_snapshot(self) -> PolicySnapshot | None: ...


class Embedder(Protocol):
    """Turns trusted text into vectors. Implementations live in adapters."""

    @property
    def model_id(self) -> str: ...

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
    def get_indexed_documents(
        self, pairs: list[tuple[str, str]], model_id: str
    ) -> list[TrustedRoutingDocument]: ...
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

    implementation: str
    version: str

    def compose(
        self,
        resolution: ResolutionResult,
        command: RouteCapabilitiesCommand,
        *,
        route_run_id: str,
        now: datetime,
    ) -> CapabilityBundle: ...


class RouteRunRepository(Protocol):
    """Persisted routing telemetry (plan §36)."""

    def put_route_run(self, run: RouteRun) -> RouteRun: ...
    def get_route_run(self, route_run_id: str) -> RouteRun | None: ...


class BundleRepository(Protocol):
    """Immutable composed bundles, items FK to exact versions (§41.1)."""

    def put_bundle(self, bundle: CapabilityBundle) -> CapabilityBundle: ...
    def get_bundle(self, bundle_id: str) -> CapabilityBundle | None: ...


class OutcomeRecorder(Protocol):
    """Multi-source outcome evidence ingestion (plan §33; ADR-010)."""

    def record(self, evidence: OutcomeEvidence) -> OutcomeEvidence: ...
    def get_outcome(self, outcome_id: str) -> OutcomeEvidence | None: ...


class TaskRepository(Protocol):
    """Delegated-task persistence (V3 §56; §30.1 Task/Message/Artifact).

    Task rows project immutable domain states: ``put_task`` upserts the
    latest validated state (transitions happen in the domain via
    ``advance_task``, never here); messages/artifacts are append-only.
    """

    def put_task(self, task: AgentTask) -> AgentTask: ...
    def get_task(self, task_id: str) -> AgentTask | None: ...
    def put_message(self, message: TaskMessage) -> TaskMessage: ...
    def list_messages(self, task_id: str) -> list[TaskMessage]: ...
    def put_artifact(self, artifact: TaskArtifact) -> TaskArtifact: ...
    def list_artifacts(self, task_id: str) -> list[TaskArtifact]: ...


class AgentExecutor(Protocol):
    """Pluggable intelligence for delegated work (V3 §56.1; §32 delegated_task).

    The runtime orchestrates — lifecycle, policy checks, persistence; the
    executor does the work under the profile's budget and tool grants. No model
    gateway in V1 (§50): a model client, a human operator, or a test double
    plugs in here. Execution authority stays with the executor's host (§76);
    the platform never gains write authority from a profile.
    """

    def execute(
        self,
        task: AgentTask,
        profile: AgentProfile,
        skills: list[SkillGrant],
        *,
        now: datetime,
    ) -> ExecutorResult: ...


class AgentRunStore(Protocol):
    """Durable storage for finished agent runs (§41.1, the §36-equivalent for
    the HarnessKernel plane). Honest-null: a service built without a store
    stays RAM-only — restart loses runs, exactly as before the store existed.

    Rows are written ONCE at terminal state from the frozen RunResult; the
    store is never a source of truth for a live run (StateManager is, INV-01).
    """

    def record_run(self, record: AgentRunRecord) -> None: ...

    def record_events(self, events: list[AgentRunEventRecord]) -> None: ...

    def get_run(self, run_id: str) -> AgentRunRecord | None: ...

    def list_recent(self, limit: int = 50) -> list[AgentRunRecord]: ...
