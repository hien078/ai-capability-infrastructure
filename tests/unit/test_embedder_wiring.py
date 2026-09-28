"""Unit tests for embedder selection in the REST wiring (§55 swappable)."""

import pytest

from aci.adapters.inbound.rest.wiring import _build_embedder
from aci.adapters.outbound.model_provider.hashing import HashingEmbedder
from aci.config import Settings


def test_default_is_offline_hashing() -> None:
    embedder = _build_embedder(Settings())
    assert isinstance(embedder, HashingEmbedder)
    assert embedder.model_id == "hashing-384-v2"
    assert embedder.dims == 384


def test_unknown_embedder_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown ACI_EMBEDDER"):
        _build_embedder(Settings(embedder="bogus"))
