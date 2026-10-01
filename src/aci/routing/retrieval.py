"""Retrieval over trusted routing documents (plan §16; §16.1 embedding policy).

Pipeline position (§14): eligibility filter → **retrieval** → rerank → …
The retriever only ever sees eligible candidates (ADR-009) and only ever
embeds normalized trusted metadata — never raw third-party skill bodies.
"""

import math
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

#: A non-finite similarity is treated as the LOWEST possible cosine
#: similarity and flagged in the stage trace — never propagated downstream
#: (§16 boundary): strict JSON / JSONB reject NaN tokens (§36 telemetry),
#: and the reranker's clamp would silently turn NaN into the TOP retrieval
#: signal (``max(0.0, min(1.0, nan)) == 1.0``).
LOWEST_SIMILARITY = -1.0


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

    @property
    def model_id(self) -> str:
        """The embedder's model id (§46 cache key for this retriever's vectors)."""
        return self._embedder.model_id

    def retrieve(
        self, query: str, eligible: list[EligibleCandidate], *, limit: int = 30
    ) -> RetrievalResult:
        indexed = 0
        # One bulk lookup for the whole eligible set: a per-candidate query
        # would be O(catalog) roundtrips per route request (§16, §46).
        documents = {
            (d.capability_id, d.version): d
            for d in self._embeddings.get_indexed_documents(
                [(c.capability_id, c.version) for c in eligible], self._embedder.model_id
            )
        }
        pairs: list[tuple[str, str]] = []
        texts: dict[tuple[str, str], str] = {}
        for candidate in eligible:
            key = (candidate.capability_id, candidate.version)
            document = documents.get(key)
            if document is None or document.digest != candidate.digest:
                version = self._capabilities.get_version(key[0], key[1])
                if version is None:
                    continue  # eligible implies the version exists; defensive
                document = build_trusted_document(
                    version, model_id=self._embedder.model_id, now=datetime.now(UTC)
                )
                self._embeddings.put_document(document, self._embedder.embed([document.text])[0])
                indexed += 1
            pairs.append(key)  # only candidates that actually have a document get searched
            texts[key] = document.text

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
        scored: list[ScoredCandidate] = []
        nonfinite = 0
        for hit in hits:
            key = (hit.capability_id, hit.version)
            if key not in by_key:
                continue
            score = hit.score
            if not math.isfinite(score):
                # Non-finite similarity (NaN for a zero document vector):
                # lowest possible score, flagged in the trace, never propagated.
                score = LOWEST_SIMILARITY
                nonfinite += 1
            scored.append(
                ScoredCandidate(
                    candidate=by_key[key],
                    score=score,
                    document_text=texts.get(key, ""),
                )
            )
        if nonfinite:
            # The store orders NaN ABOVE every finite value (Postgres sorts
            # NaN as greater than everything in DESC); after sanitization the
            # poisoned hits sit at the bottom where their score belongs. A
            # stable sort keeps the finite order exactly as the store sent it.
            scored.sort(key=lambda s: -s.score)
        return RetrievalResult(
            candidates=scored,
            trace=RetrievalTrace(
                eligible_count=len(eligible),
                indexed_count=indexed,
                searched_count=len(pairs),
                returned_count=len(scored),
                limit=limit,
                model_id=self._embedder.model_id,
                nonfinite_scores=nonfinite,
            ),
        )
