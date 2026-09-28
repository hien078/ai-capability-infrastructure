"""Deterministic offline embedder (V1 baseline, plan §50: no model gateway).

Hashing trick: tokens are blake2b-hashed into fixed dimensions with a sign
bit, then L2-normalized. Deterministic across processes (never Python's
randomized ``hash``), fully offline, and good enough for baseline semantic
retrieval. A real embedding model plugs in behind the same ``Embedder``
protocol later; its vectors coexist via ``model_id``.

v2 (§75 step 23, benchmark ``smoke-10-clean-catalog``): tokens are folded
by the shared domain normalizer before hashing — raw tokens could not
bridge even trivial morphology ("failures" vs "test failure"), capping
smoke-set recall at tie-luck. The fold is deterministic, so v2 vectors are
reproducible; the model_id bump lets them coexist with any v1 vectors
(§46: cache keys carry the model_id).
"""

import hashlib
from math import sqrt

from aci.domain.routing.text import normalize_tokens

DEFAULT_DIMS = 256


class HashingEmbedder:
    """Implements the Embedder protocol."""

    def __init__(self, dims: int = DEFAULT_DIMS) -> None:
        if dims <= 0:
            raise ValueError("dims must be positive")
        self._dims = dims

    @property
    def model_id(self) -> str:
        return f"hashing-{self._dims}-v2"

    @property
    def dims(self) -> int:
        return self._dims

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dims
        for token in normalize_tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self._dims
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = sqrt(sum(value * value for value in vector))
        if norm > 0.0:
            vector = [value / norm for value in vector]
        return vector
