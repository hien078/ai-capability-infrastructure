"""Routing/retrieval domain contracts (plan §§14, 16, 16.1).

The trusted routing document is the ONLY text that gets embedded by default:
normalized metadata (name/description/provides/facets), never raw third-party
skill bodies (§16.1 prompt-injection boundary).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from aci.domain.capability.models import TaskContext
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


class RerankTrace(BaseModel):
    """Trace data for the rerank stage (§14; §52: version recorded in trace)."""

    model_config = {"frozen": True}

    implementation: str
    version: str
    input_count: int
    output_count: int


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


class RetrievalResult(BaseModel):
    model_config = {"frozen": True}

    candidates: list[ScoredCandidate] = Field(default_factory=list)
    trace: RetrievalTrace
