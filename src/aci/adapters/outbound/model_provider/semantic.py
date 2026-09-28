"""Local ONNX semantic embedder (V2, plan §55 "more sophisticated rerankers").

Backs the same ``Embedder`` protocol as the hashing baseline; vectors
coexist per ``model_id`` (§46). Uses fastembed (ONNX runtime, no torch):
fully local after a one-time model download, deterministic, no API keys.

The module imports fastembed lazily so the core install stays light —
``fastembed`` is an optional extra (``pip install '.[semantic]'``) and
the hashing baseline remains the default (``ACI_EMBEDDER=hashing``).
"""

from __future__ import annotations

from typing import Any

from aci.adapters.outbound.pgvector.orm import EMBEDDING_DIMS

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class FastEmbedEmbedder:
    """Implements the Embedder protocol via a local ONNX model."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ImportError(
                "fastembed is not installed; install the optional extra "
                "(pip install '.[semantic]') or set ACI_EMBEDDER=hashing"
            ) from exc
        self._model_name = model_name
        self._model: Any = TextEmbedding(model_name=model_name)

    @property
    def model_id(self) -> str:
        return f"fastembed:{self._model_name}"

    @property
    def dims(self) -> int:
        return EMBEDDING_DIMS

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self._model.embed(texts)]
