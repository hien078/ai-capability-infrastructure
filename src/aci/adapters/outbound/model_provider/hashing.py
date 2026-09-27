"""Deterministic offline embedder (V1 baseline, plan §50: no model gateway).

Hashing trick: tokens are blake2b-hashed into fixed dimensions with a sign
bit, then L2-normalized. Deterministic across processes (never Python's
randomized ``hash``), fully offline, and good enough for baseline semantic
retrieval. A real embedding model plugs in behind the same ``Embedder``
protocol later; its vectors coexist via ``model_id``.
"""

import hashlib
import re
from math import sqrt

_TOKEN = re.compile(r"[a-z0-9]+")

DEFAULT_DIMS = 256


class HashingEmbedder:
    """Implements the Embedder protocol."""

    def __init__(self, dims: int = DEFAULT_DIMS) -> None:
        if dims <= 0:
            raise ValueError("dims must be positive")
        self._dims = dims

    @property
    def model_id(self) -> str:
        return f"hashing-{self._dims}-v1"

    @property
    def dims(self) -> int:
        return self._dims

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dims
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self._dims
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = sqrt(sum(value * value for value in vector))
        if norm > 0.0:
            vector = [value / norm for value in vector]
        return vector
