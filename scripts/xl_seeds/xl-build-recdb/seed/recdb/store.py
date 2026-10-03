"""The JSONL store: load, insert, update, delete, atomic save.

Unimplemented skeleton — see README.md §2 (records), §5 (store file
semantics) for the contract this module must implement.
"""

from __future__ import annotations

from typing import Any


def default_path() -> str:
    """Resolve the store path: --db flag > $RECDB_HOME/store.jsonl > ./recdb.jsonl."""
    raise NotImplementedError("recdb store is not implemented yet")


def load(path: str) -> Any:
    """Read the store: records in file order, corrupt lines, duplicate ids."""
    raise NotImplementedError("recdb store is not implemented yet")


def save(path: str, records: list[dict[str, Any]]) -> None:
    """Atomically write records as JSON Lines."""
    raise NotImplementedError("recdb store is not implemented yet")
