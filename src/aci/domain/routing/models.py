"""Routing/retrieval domain contracts (plan §§14, 16, 16.1).

The trusted routing document is the ONLY text that gets embedded by default:
normalized metadata (name/description/provides/facets), never raw third-party
skill bodies (§16.1 prompt-injection boundary).
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from aci.domain.capability.models import (
    CapabilityArtifact,
    CapabilityBundle,
    CapabilityKind,
    CapabilityVersion,
    TaskContext,
)
from aci.domain.policy.models import EligibleCandidate

DocType = Literal["routing"]


class TrustedRoutingDocument(BaseModel):
    """Normalized, trusted summary of one immutable version (§16.1).

    Keyed naturally by (capability_id, version, doc_type); the vector that
    derives from it is additionally keyed by embedder model_id (§46: cache keys
    include version/digest).
    """

    model_config = {"frozen": True}

    document_id: str
    capability_id: str
    version: str
    digest: str  # content_digest of the version this doc summarizes
    doc_type: DocType = "routing"
    model_id: str  # embedder that owns the derived vector (§46 cache key)
    text: str = Field(max_length=2000)
    created_at: datetime


class RetrievedDocument(BaseModel):
    """Raw search hit from the vector store."""

    model_config = {"frozen": True}

    capability_id: str
    version: str
    score: float  # cosine similarity, higher = closer


class ScoredCandidate(BaseModel):
    """An eligible candidate plus its retrieval score (§16 output).

    ``document_text`` carries the trusted routing document (§16.1) so the
    reranker can match against sanitized metadata — never raw skill bodies.
    """

    model_config = {"frozen": True}

    candidate: EligibleCandidate
    score: float
    document_text: str = ""


class TaskDescriptor(BaseModel):
    """Normalized task description for ranking (plan §17)."""

    model_config = {"frozen": True}

    task_text: str = Field(min_length=1, max_length=8000)
    context: TaskContext = Field(default_factory=TaskContext)


class RankedCandidate(BaseModel):
    """Reranker output: final score + why (§14 trace, §36 telemetry)."""

    model_config = {"frozen": True}

    candidate: EligibleCandidate
    score: float
    retrieval_score: float
    rank: int  # 1-based, stable ordering
    reasons: list[str] = Field(default_factory=list)
    document_text: str = ""


class ResolvedItem(BaseModel):
    """A candidate that survived dependency resolution, with its bundle role."""

    model_config = {"frozen": True}

    candidate: EligibleCandidate
    role: Literal["primary", "check", "support"]
    reason_code: str
    document_text: str = ""


class ResolutionDrop(BaseModel):
    """Why a ranked candidate did not survive resolution (§14 trace)."""

    model_config = {"frozen": True}

    capability_id: str
    version: str
    reason: str  # stable machine-readable code (§45-style)
    detail: str = ""


class ResolutionTrace(BaseModel):
    """Trace data for the dependency-resolution stage (§14)."""

    model_config = {"frozen": True}

    input_count: int
    selected_count: int
    dropped_count: int


class ResolutionResult(BaseModel):
    model_config = {"frozen": True}

    selected: list[ResolvedItem] = Field(default_factory=list)
    dropped: list[ResolutionDrop] = Field(default_factory=list)
    trace: ResolutionTrace


#: Where a bundle item's context-cost estimate came from (§19.1 budget).
#: ``artifact_entry`` = byte size of the entry file the client actually loads
#: (artifact manifest metadata — never the file's content); the other two are
#: fallbacks when no size is known: the trusted routing summary's length, or
#: a flat default when even that is empty.
TokenEstimateSource = Literal["artifact_entry", "routing_summary", "default"]
#: Which budget excluded a resolved item from the bundle.
CompositionStopReason = Literal["max_items", "max_context_tokens"]
#: What the composer does with a candidate whose cost does not fit the
#: REMAINING context budget (ADR-008): ``stop`` (the shipped default) ends
#: composition at the first oversized candidate — every later candidate is
#: excluded too; ``skip`` excludes only that candidate and keeps composing
#: later candidates that fit (max_items and rank order unchanged).
CompositionOversizedPolicy = Literal["stop", "skip"]


class ComposedItemCost(BaseModel):
    """One resolved item as the composer costed it (§14 trace, §19.1)."""

    model_config = {"frozen": True}

    capability_id: str
    version: str
    estimated_tokens: int = Field(ge=0)
    estimate_source: TokenEstimateSource
    included: bool
    #: Which budget excluded this item (None when included) — stable
    #: machine-readable code, per item, so a trace shows WHY each resolved
    #: candidate is missing from the bundle, not just that composition ended.
    excluded_reason: CompositionStopReason | None = None


class CompositionTrace(BaseModel):
    """Trace data for the composition stage (§14; §52: version in trace)."""

    model_config = {"frozen": True}

    implementation: str
    version: str
    max_items: int
    max_context_tokens: int
    spent_tokens: int = Field(ge=0)
    #: Which budget excluded resolved content: in ``stop`` mode the budget
    #: that ended composition; in ``skip`` mode the item cap if it ended
    #: composition, else the context budget if any candidate was skipped for
    #: it. None = every resolved item fit (nothing was excluded).
    stop_reason: CompositionStopReason | None = None
    #: Which oversized policy produced this trace (see
    #: ``CompositionOversizedPolicy``); telemetry disambiguates the two
    #: behaviours while they share one composer version.
    oversized_policy: CompositionOversizedPolicy = "stop"
    items: list[ComposedItemCost] = Field(default_factory=list)


class CompositionResult(BaseModel):
    model_config = {"frozen": True}

    bundle: CapabilityBundle
    trace: CompositionTrace


class RouteRun(BaseModel):
    """Persisted telemetry for one routing request (plan §36).

    Stage traces (§14) are stored as structured JSON: eligibility exclusions,
    retrieval counts, rerank scores, resolution drops. No raw secrets — the
    task text is the bounded routing input, never file contents (§25.4).
    """

    model_config = {"frozen": True}

    route_run_id: str
    request_id: str
    trace_id: str
    created_at: datetime
    principal_id: str
    organization_id: str | None = None
    workspace_id: str | None = None
    client_type: str
    client_version: str | None = None
    protocol_type: str
    task_text: str = Field(max_length=8000)
    policy_snapshot_id: str | None = None
    eligible_count: int = 0
    stages: dict[str, Any] = Field(default_factory=dict)
    reranker_implementation: str = ""
    reranker_version: str = ""
    composer_implementation: str = ""
    composer_version: str = ""
    latency_ms: int | None = None
    error_code: str | None = None
    bundle_id: str | None = None


class RouteResult(BaseModel):
    """What POST /v1/routes returns: the pinned bundle + run identity."""

    model_config = {"frozen": True}

    route_run_id: str
    bundle: CapabilityBundle


class CapabilitySearchResult(BaseModel):
    """One search hit (plan §11.2): trusted metadata + score."""

    model_config = {"frozen": True}

    capability_id: str
    version: str
    digest: str
    kind: CapabilityKind
    display_name: str = ""
    description: str = ""
    facets: dict[str, list[str]] = Field(default_factory=dict)
    score: float = 0.0


class ResolvedVersion(BaseModel):
    """A pinned version plus its immutable artifact (resolve, §12)."""

    model_config = {"frozen": True}

    version: CapabilityVersion
    artifact: CapabilityArtifact | None = None


#: Outcome of one judge call (docs/plans/jev-reranker.md §2.3). ``ok`` is the
#: only status that carries a selection; every other status means the
#: reranker must treat the verdict as "nothing selected" (fail-safe =
#: abstain, ADR-008 — never a silent fallback to the heuristic).
JudgeStatus = Literal["ok", "timeout", "error", "invalid_output"]

#: Per-pick necessity as reported by the judge (tuning exp 3, selection
#: policy): "required" = the task as written calls for that skill's
#: activity; "optional" = it would merely help. The reranker's necessity
#: gate (``JevReranker(necessity_gate=...)``) may drop optional picks —
#: dropping is subset-safe (§2.2.1); a judge that reports no necessities
#: (``None``, the pre-exp-3 shape) leaves the gate a no-op.
PickNecessity = Literal["required", "optional"]


class JudgeCandidate(BaseModel):
    """One candidate as the judge sees it (§17.1: trusted text only).

    ``document_text`` is the trusted routing document — sanitized metadata,
    never a raw ``SKILL.md`` body. The judge adapter truncates it further for
    the wire; this model carries the stage's full trusted text.
    """

    model_config = {"frozen": True}

    capability_id: str
    document_text: str = ""


class JudgeVerdict(BaseModel):
    """The judge's answer for one task (docs/plans/jev-reranker.md §2.3).

    ``selected`` is the judge's raw id list — NOT yet subset-validated; the
    reranker drops ids outside the candidate set and counts them in
    ``invalid_ids`` (which also accumulates parse-level drops from the
    adapter). ``reason`` is bounded (≤500 chars) so it can land in telemetry
    verbatim. ``necessities`` (exp 3) is per-pick necessity ALIGNED with
    ``selected`` by position; ``None`` = the judge did not report any (the
    pre-exp-3 output shape) and the reranker's necessity gate is a no-op.
    A length mismatch is likewise a no-op (a malformed report never drops
    picks silently).
    """

    model_config = {"frozen": True}

    status: JudgeStatus
    selected: list[str] = Field(default_factory=list)
    reason: str = Field(default="", max_length=500)
    model_id: str = ""
    latency_ms: int | None = None
    invalid_ids: int = Field(default=0, ge=0)
    #: Per-pick necessity, position-aligned with ``selected`` (exp 3).
    necessities: list[PickNecessity] | None = None


#: RerankTrace judge fields (below): dropped from serialization while
#: UNSET/None so a trace produced without a judge serializes exactly as
#: before JEV existed (byte-compatible telemetry — the heuristic never
#: emits nulls). P4: the drop runs via a ``model_serializer`` WRAP so it
#: applies on EVERY path — the old ``model_dump`` override was bypassed by
#: nested dumps (``RerankResult.model_dump`` serializes the trace through
#: the core serializer) and by ``model_dump_json``.
_JUDGE_TRACE_FIELDS = (
    "judge_status",
    "judge_model_id",
    "judge_latency_ms",
    "judge_reason",
    "selected_ids",
    "invalid_ids",
    "necessity_dropped_ids",
)


class RerankTrace(BaseModel):
    """Trace data for the rerank stage (§14; §52: version recorded in trace).

    The optional judge fields (docs/plans/jev-reranker.md §2.3) are set only
    by the JEV reranker; the wrap serializer below drops them while unset so
    the heuristic trace stays byte-compatible on every dump path.
    """

    model_config = {"frozen": True}

    implementation: str
    version: str
    input_count: int
    output_count: int
    judge_status: JudgeStatus | None = None
    judge_model_id: str | None = None
    judge_latency_ms: int | None = None
    #: The judge's bounded reason (≤500 chars, same bound as
    #: ``JudgeVerdict.reason``) — telemetry lands it verbatim.
    judge_reason: str | None = Field(default=None, max_length=500)
    #: Ids the judge selected that survived subset validation, in judge
    #: order (rank = position). An honest abstention records ``[]``.
    selected_ids: list[str] | None = None
    #: Judge-selected ids dropped as unknown + parse-level drops.
    invalid_ids: int | None = None
    #: Validated judge picks dropped by the necessity gate (exp 3), in
    #: judge order — telemetry for the selection policy; ``None`` = the
    #: gate did not run (``necessity_gate="none"`` or no necessities).
    necessity_dropped_ids: list[str] | None = None

    @model_serializer(mode="wrap")
    def _drop_unset_judge_fields(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, Any]:
        """Serialize, dropping UNSET/None judge fields (byte-compatibility).

        P4: a WRAP serializer (not a ``model_dump`` override) so the drop
        runs on every path — direct dumps, NESTED dumps
        (``RerankResult.model_dump``), and ``model_dump_json``.
        """
        dump: dict[str, Any] = handler(self)
        for field in _JUDGE_TRACE_FIELDS:
            if dump.get(field) is None:
                dump.pop(field, None)
        return dump


class RerankResult(BaseModel):
    model_config = {"frozen": True}

    ranked: list[RankedCandidate] = Field(default_factory=list)
    trace: RerankTrace


class RetrievalTrace(BaseModel):
    """Trace data for the retrieval stage (§14: every stage emits trace)."""

    model_config = {"frozen": True}

    eligible_count: int
    indexed_count: int  # documents embedded during this run (cache misses)
    searched_count: int  # candidates included in the vector search
    returned_count: int
    limit: int
    model_id: str
    #: Non-finite similarities sanitized at the retrieval boundary (§16):
    #: a NaN/inf score (pgvector's ``1 - cosine_distance`` is NaN for a zero
    #: vector) is treated as the lowest possible score, never propagated —
    #: strict JSON / JSONB reject NaN tokens (§36), and the reranker's
    #: clamp would silently boost it to the TOP retrieval signal.
    nonfinite_scores: int = 0


class RetrievalResult(BaseModel):
    model_config = {"frozen": True}

    candidates: list[ScoredCandidate] = Field(default_factory=list)
    trace: RetrievalTrace
