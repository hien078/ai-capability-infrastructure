"""Composer v2 payload-size boundary (§16.1/§17.1; ADR-008/009) — static.

The composer budgets on real SKILL.md *sizes*. The size path must stay
metadata-only: neither the size source nor any routing stage may be able to
reach blob content (the object store), so raw skill bodies remain
structurally unreachable by embed/rerank/compose.
"""

import ast
import inspect
import pathlib

from aci.application import payload_sizes
from aci.application.payload_sizes import ArtifactPayloadSizes
from aci.routing.composer import MinimalBundleComposer

SRC = pathlib.Path(__file__).parents[2] / "src" / "aci"
ROUTING = SRC / "routing"
SIZE_SOURCE = pathlib.Path(payload_sizes.__file__)

BLOB_NAMES = {"ObjectStore", "FsObjectStore", "object_store", "objects"}


def _names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.update(node.name.split("."))
        elif isinstance(node, ast.arg):
            found.add(node.arg)
    return found


def test_size_source_and_routing_cannot_reach_blob_content() -> None:
    files = [SIZE_SOURCE, *(p for p in ROUTING.rglob("*.py") if "__pycache__" not in p.parts)]
    for path in files:
        leaked = _names(path) & BLOB_NAMES
        assert not leaked, f"{path} references blob storage: {leaked}"


def test_size_source_and_composer_take_no_object_store() -> None:
    size_params = set(inspect.signature(ArtifactPayloadSizes.__init__).parameters)
    assert size_params == {"self", "capabilities", "artifacts"}
    composer_params = set(inspect.signature(MinimalBundleComposer.__init__).parameters)
    assert composer_params == {"self", "payload_sizes"}
