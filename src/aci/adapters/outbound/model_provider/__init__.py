"""Model-provider embedders (plan §50: no model gateway; local models only)."""

from aci.adapters.outbound.model_provider.hashing import (
    DEFAULT_DIMS,
    HashingEmbedder,
)
from aci.adapters.outbound.model_provider.semantic import (
    DEFAULT_MODEL,
    FastEmbedEmbedder,
)

__all__ = ["DEFAULT_DIMS", "DEFAULT_MODEL", "FastEmbedEmbedder", "HashingEmbedder"]
