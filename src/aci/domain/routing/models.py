"""Routing/retrieval domain contracts (plan §§14, 16, 16.1).

The trusted routing document is the ONLY text that gets embedded by default:
normalized metadata (name/description/provides/facets), never raw third-party
skill bodies (§16.1 prompt-injection boundary).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
    """An eligible candidate plus its retrieval score (§16 output)."""

    model_config = {"frozen": True}

    candidate: EligibleCandidate
    score: float


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
