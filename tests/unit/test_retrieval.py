"""Unit tests for trusted routing documents, the hashing embedder, and the
retriever (plan §16/§16.1; ADR-009: retrieval only sees eligible candidates)."""

from datetime import UTC, datetime
from math import sqrt

import pytest

from aci.adapters.outbound.model_provider.hashing import HashingEmbedder
from aci.domain.capability.models import AgentSpec, CapabilityVersion, SkillSpec
from aci.domain.policy.models import EligibleCandidate
from aci.domain.routing.models import RetrievedDocument, TrustedRoutingDocument
from aci.routing.retrieval import MAX_DOCUMENT_CHARS, EmbeddingRetriever, build_trusted_document

DIGEST = "sha256:" + "ab" * 32
NOW = datetime(2026, 9, 27, tzinfo=UTC)


def make_version(**overrides: object) -> CapabilityVersion:
    base: dict = {
        "capability_id": "cap-x",
        "version": "1.0.0",
        "kind": "skill",
        "content_digest": DIGEST,
        "created_at": NOW,
        "display_name": "Python Traceback Debugger",
        "description": "Debugs python tracebacks.",
        "spec": SkillSpec(provides=["root-cause-analysis"]),
    }
    base.update(overrides)
    return CapabilityVersion.model_validate(base)


def make_candidate(capability_id: str = "cap-x", digest: str = DIGEST) -> EligibleCandidate:
    return EligibleCandidate(
        capability_id=capability_id,
        version="1.0.0",
        digest=digest,
        kind="skill",
        channel="production",
        status="active",
    )


# ---------- trusted routing document (§16.1) ----------


def test_document_contains_normalized_metadata_lines() -> None:
    version = make_version(
        facets={"domain": ["software-engineering"], "technology": ["python"]},
        spec=SkillSpec(
            provides=["root-cause-analysis"],
            routing_hints={"task_types": ["debugging"]},
        ),
    )
    doc = build_trusted_document(version, model_id="hashing-256-v1", now=NOW)
    assert doc.text == "\n".join(
        [
            "name: Python Traceback Debugger",
            "description: Debugs python tracebacks.",
            "provides: root-cause-analysis",
            "task types: debugging",
            "domain: software-engineering",
            "technology: python",
            "kind: skill",
        ]
    )
    assert doc.digest == DIGEST
    assert doc.doc_type == "routing"


def test_document_is_truncated_to_max_chars() -> None:
    version = make_version(description="x" * 10_000)
    doc = build_trusted_document(version, model_id="m", now=NOW)
    assert len(doc.text) <= MAX_DOCUMENT_CHARS


def test_document_uses_only_curated_fields_not_bodies() -> None:
    """The builder reads version metadata; raw SKILL.md bodies are unreachable."""
    version = make_version()
    doc = build_trusted_document(version, model_id="m", now=NOW)
    # Every line is a curated key: value pair — no free-form body content.
    for line in doc.text.splitlines():
        assert ": " in line


def test_document_includes_agent_provides() -> None:
    version = make_version(kind="agent", spec=AgentSpec(provides=["planning"]))
    doc = build_trusted_document(version, model_id="m", now=NOW)
    assert "provides: planning" in doc.text
    assert "kind: agent" in doc.text


# ---------- hashing embedder ----------


def test_embedder_is_deterministic_across_instances() -> None:
    a = HashingEmbedder().embed(["debug python traceback"])[0]
    b = HashingEmbedder().embed(["debug python traceback"])[0]
    assert a == b


def test_embedder_output_is_l2_normalized() -> None:
    vector = HashingEmbedder().embed(["debug python traceback"])[0]
    assert len(vector) == 256
    assert sqrt(sum(v * v for v in vector)) == pytest.approx(1.0)


def test_embedder_empty_text_gives_zero_vector() -> None:
    vector = HashingEmbedder().embed(["   "])[0]
    assert vector == [0.0] * 256


def test_embedder_similarity_tracks_shared_tokens() -> None:
    embedder = HashingEmbedder()
    query = embedder.embed(["debug python traceback"])[0]
    close = embedder.embed(["python traceback debugging"])[0]
    far = embedder.embed(["write marketing copy"])[0]

    def cosine(u: list[float], v: list[float]) -> float:
        return sum(x * y for x, y in zip(u, v, strict=True))

    assert cosine(query, close) > cosine(query, far)


def test_embedder_model_id_encodes_dims() -> None:
    assert HashingEmbedder(dims=64).model_id == "hashing-64-v1"
    with pytest.raises(ValueError):
        HashingEmbedder(dims=0)


# ---------- retriever (with fakes) ----------


class FakeEmbeddings:
    """In-memory EmbeddingRepository."""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], TrustedRoutingDocument] = {}
        self.vectors: dict[tuple[str, str, str], list[float]] = {}
        self.search_calls: list[tuple[tuple[tuple[str, str], ...], str, int]] = []
        self.put_count = 0

    def put_document(self, document: TrustedRoutingDocument, vector: list[float]) -> None:
        self.docs[(document.capability_id, document.version)] = document
        self.vectors[(document.capability_id, document.version, document.model_id)] = vector
        self.put_count += 1

    def get_indexed_document(
        self, capability_id: str, version: str, model_id: str
    ) -> TrustedRoutingDocument | None:
        if (capability_id, version, model_id) not in self.vectors:
            return None
        return self.docs.get((capability_id, version))

    def search(
        self,
        query_vector: list[float],
        pairs: list[tuple[str, str]],
        *,
        model_id: str,
        limit: int,
    ) -> list[RetrievedDocument]:
        self.search_calls.append((tuple(pairs), model_id, limit))

        def cosine(u: list[float], v: list[float]) -> float:
            nu = sqrt(sum(x * x for x in u))
            nv = sqrt(sum(x * x for x in v))
            if nu == 0.0 or nv == 0.0:
                return 0.0
            return sum(x * y for x, y in zip(u, v, strict=True)) / (nu * nv)

        hits = [
            RetrievedDocument(
                capability_id=cap,
                version=ver,
                score=cosine(query_vector, self.vectors[(cap, ver, model_id)]),
            )
            for cap, ver in pairs
            if (cap, ver, model_id) in self.vectors
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]


class FakeCapabilities:
    def __init__(self, versions: list[CapabilityVersion]) -> None:
        self._versions = {(v.capability_id, v.version): v for v in versions}

    def get_version(self, capability_id: str, version: str) -> CapabilityVersion | None:
        return self._versions.get((capability_id, version))


def test_retriever_indexes_once_then_reuses_cache() -> None:
    embeddings = FakeEmbeddings()
    retriever = EmbeddingRetriever(
        capabilities=FakeCapabilities([make_version()]),  # type: ignore[arg-type]
        embedder=HashingEmbedder(),
        embeddings=embeddings,  # type: ignore[arg-type]
    )
    eligible = [make_candidate()]

    first = retriever.retrieve("debug python", eligible)
    assert first.trace.indexed_count == 1
    assert first.trace.searched_count == 1
    assert first.trace.returned_count == 1
    assert embeddings.put_count == 1

    second = retriever.retrieve("debug python", eligible)
    assert second.trace.indexed_count == 0  # §46: immutable content stays cached
    assert embeddings.put_count == 1
    assert [s.candidate.capability_id for s in second.candidates] == ["cap-x"]


def test_retriever_reindexes_on_digest_mismatch() -> None:
    embeddings = FakeEmbeddings()
    retriever = EmbeddingRetriever(
        capabilities=FakeCapabilities([make_version()]),  # type: ignore[arg-type]
        embedder=HashingEmbedder(),
        embeddings=embeddings,  # type: ignore[arg-type]
    )
    retriever.retrieve("q", [make_candidate()])
    # Same natural key but a different content digest ⇒ stale cache, re-embed.
    retriever.retrieve("q", [make_candidate(digest="sha256:" + "cd" * 32)])
    assert embeddings.put_count == 2


def test_retriever_searches_only_eligible_pairs() -> None:
    embeddings = FakeEmbeddings()
    retriever = EmbeddingRetriever(
        capabilities=FakeCapabilities([make_version()]),  # type: ignore[arg-type]
        embedder=HashingEmbedder(),
        embeddings=embeddings,  # type: ignore[arg-type]
    )
    eligible = [make_candidate("cap-x"), make_candidate("cap-other")]
    retriever.retrieve("q", eligible)
    pairs, model_id, limit = embeddings.search_calls[0]
    assert set(pairs) == {("cap-x", "1.0.0"), ("cap-other", "1.0.0")}
    assert model_id == "hashing-256-v1"
    assert limit == 30


def test_retriever_empty_eligible_is_valid_empty_result() -> None:
    embeddings = FakeEmbeddings()
    retriever = EmbeddingRetriever(
        capabilities=FakeCapabilities([]),  # type: ignore[arg-type]
        embedder=HashingEmbedder(),
        embeddings=embeddings,  # type: ignore[arg-type]
    )
    result = retriever.retrieve("q", [])
    assert result.candidates == []
    assert result.trace.eligible_count == 0
    assert result.trace.searched_count == 0
    assert embeddings.search_calls == []


def test_retriever_respects_limit() -> None:
    embeddings = FakeEmbeddings()
    versions = [
        make_version(
            capability_id=f"cap-{i}",
            description="debug python traceback",
        )
        for i in range(3)
    ]
    retriever = EmbeddingRetriever(
        capabilities=FakeCapabilities(versions),  # type: ignore[arg-type]
        embedder=HashingEmbedder(),
        embeddings=embeddings,  # type: ignore[arg-type]
    )
    eligible = [make_candidate(f"cap-{i}") for i in range(3)]
    result = retriever.retrieve("debug python traceback", eligible, limit=2)
    assert len(result.candidates) == 2
    assert result.trace.limit == 2
    assert result.trace.returned_count == 2
    # Scores are descending.
    scores = [s.score for s in result.candidates]
    assert scores == sorted(scores, reverse=True)
