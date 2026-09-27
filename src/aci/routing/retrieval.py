"""Retrieval over trusted routing documents (plan §16; §16.1 embedding policy).

Pipeline position (§14): eligibility filter → **retrieval** → rerank → …
The retriever only ever sees eligible candidates (ADR-009) and only ever
embeds normalized trusted metadata — never raw third-party skill bodies.
"""

from datetime import UTC, datetime
from uuid import uuid4

from aci.application.protocols import (
    CapabilityRepository,
    Embedder,
    EmbeddingRepository,
)
from aci.domain.capability.models import AgentSpec, CapabilityVersion, SkillSpec
from aci.domain.policy.models import EligibleCandidate
from aci.domain.routing.models import (
    RetrievalResult,
    RetrievalTrace,
    ScoredCandidate,
    TrustedRoutingDocument,
)

MAX_DOCUMENT_CHARS = 2000


def build_trusted_document(
    version: CapabilityVersion, *, model_id: str, now: datetime
) -> TrustedRoutingDocument:
    """Normalized trusted summary (§16.1): name/description/provides/facets.

    Built exclusively from curated version metadata. Raw SKILL.md bodies live
    only in content-addressed blobs and are structurally unreachable here.
    """

    def _facet_lines() -> list[str]:
        lines: list[str] = []
        for facet in ("domain", "technology", "task_type", "concern"):
            values = version.facets.get(facet)
            if values:
                lines.append(f"{facet}: {', '.join(values)}")
        return lines

    lines: list[str] = []
    if version.display_name:
        lines.append(f"name: {version.display_name}")
    if version.description:
        lines.append(f"description: {version.description}")
    spec = version.spec
    if isinstance(spec, SkillSpec | AgentSpec) and spec.provides:
        lines.append(f"provides: {', '.join(spec.provides)}")
    if isinstance(spec, SkillSpec) and spec.routing_hints:
        task_types = [value for values in spec.routing_hints.values() for value in values]
        if task_types:
            lines.append(f"task types: {', '.join(task_types)}")
    lines.extend(_facet_lines())
    lines.append(f"kind: {version.kind}")
    text = "\n".join(lines)[:MAX_DOCUMENT_CHARS]

    return TrustedRoutingDocument(
        document_id=uuid4().hex,
        capability_id=version.capability_id,
        version=version.version,
        digest=version.content_digest,
        doc_type="routing",
        model_id=model_id,
        text=text,
        created_at=now,
    )


class EmbeddingRetriever:
    """Implements the CandidateRetriever protocol (§44) over pgvector.

    Lazily indexes eligible candidates (immutable content, §46: re-embedding is
    idempotent per version+model), then searches restricted to the eligible
    (capability_id, version) pairs — ineligible content is never even searched.
    """

    def __init__(
        self,
        capabilities: CapabilityRepository,
        embedder: Embedder,
        embeddings: EmbeddingRepository,
    ) -> None:
        self._capabilities = capabilities
        self._embedder = embedder
        self._embeddings = embeddings

    def retrieve(
        self, query: str, eligible: list[EligibleCandidate], *, limit: int = 30
    ) -> RetrievalResult:
        indexed = 0
        pairs: list[tuple[str, str]] = []
        for candidate in eligible:
            pairs.append((candidate.capability_id, candidate.version))
            indexed += self._ensure_indexed(candidate)

        if not pairs:
            return RetrievalResult(
                candidates=[],
                trace=RetrievalTrace(
                    eligible_count=len(eligible),
                    indexed_count=indexed,
                    searched_count=0,
                    returned_count=0,
                    limit=limit,
                    model_id=self._embedder.model_id,
                ),
            )

        query_vector = self._embedder.embed([query])[0]
        hits = self._embeddings.search(
            query_vector, pairs, model_id=self._embedder.model_id, limit=limit
        )
        by_key = {(c.capability_id, c.version): c for c in eligible}
        scored = [
            ScoredCandidate(candidate=by_key[(hit.capability_id, hit.version)], score=hit.score)
            for hit in hits
            if (hit.capability_id, hit.version) in by_key
        ]
        return RetrievalResult(
            candidates=scored,
            trace=RetrievalTrace(
                eligible_count=len(eligible),
                indexed_count=indexed,
                searched_count=len(pairs),
                returned_count=len(scored),
                limit=limit,
                model_id=self._embedder.model_id,
            ),
        )

    def _ensure_indexed(self, candidate: EligibleCandidate) -> int:
        """Index on cache miss; returns 1 if this run embedded the candidate."""
        existing = self._embeddings.get_indexed_document(
            candidate.capability_id, candidate.version, self._embedder.model_id
        )
        if existing is not None and existing.digest == candidate.digest:
            return 0  # immutable content already embedded (§46)
        version = self._capabilities.get_version(candidate.capability_id, candidate.version)
        if version is None:
            return 0  # eligible implies the version exists; defensive
        document = build_trusted_document(
            version, model_id=self._embedder.model_id, now=datetime.now(UTC)
        )
        vector = self._embedder.embed([document.text])[0]
        self._embeddings.put_document(document, vector)
        return 1
