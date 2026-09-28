"""Unit tests for the V2 semantic embedder (plan §55).

Skipped when fastembed (optional `semantic` extra) or its model is
unavailable — the hashing baseline stays the default so the rest of the
suite never needs a model download.

The acceptance property under test is the one the §80 measurement found
missing: a real embedding model must bridge vocabulary the lexical
hashing trick cannot ("fix a mutable default argument bug" must land
closer to a debugging document than to a design document).
"""

import math

import pytest

from aci.adapters.outbound.model_provider.semantic import FastEmbedEmbedder

pytest.importorskip("fastembed", reason="optional `semantic` extra not installed")


@pytest.fixture(scope="module")
def embedder() -> FastEmbedEmbedder:
    return FastEmbedEmbedder()


def test_model_id_and_dims(embedder: FastEmbedEmbedder) -> None:
    assert embedder.model_id == "fastembed:BAAI/bge-small-en-v1.5"
    assert embedder.dims == 384


def test_embed_shapes_and_determinism(embedder: FastEmbedEmbedder) -> None:
    a = embedder.embed(["hello world", "debug python"])
    b = embedder.embed(["hello world", "debug python"])
    assert len(a) == 2
    assert all(len(v) == 384 for v in a)
    assert a == b  # deterministic: same input, same vector


def test_embed_empty_is_empty(embedder: FastEmbedEmbedder) -> None:
    assert embedder.embed([]) == []


def test_semantic_bridges_vocabulary_the_hashing_trick_cannot(
    embedder: FastEmbedEmbedder,
) -> None:
    """The §80 miss: 'python-bugfix' could not reach diagnosing-bugs/debugging."""
    query, debugging, design = embedder.embed(
        [
            "Fix a mutable default argument bug in a Python function",
            "Diagnosis loop for hard bugs: reproduce, minimise, hypothesise, instrument",
            "Design a landing page with brand colors and typography",
        ]
    )
    assert _cos(query, debugging) > _cos(query, design)


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b)))
