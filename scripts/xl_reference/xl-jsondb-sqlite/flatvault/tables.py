"""One table — the same public API as the JSON engine, sqlite behind it.

The file path is kept as the legacy backup pointer; the live store is
the engine's sqlite database. ``reload``/``save`` stay in the API as
no-ops (every write is already committed — that is the point).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flatvault.engine import Engine
from flatvault.errors import TableError
from flatvault.queries import Query


class Table:
    """The sqlite-backed table facade."""

    def __init__(self, name: str, path: Path, engine: Engine | None = None) -> None:
        self.name = name
        self.path = Path(path)
        self._engine = engine if engine is not None else Engine(self.path.parent)

    # ------------------------------------------------------------------ load

    def reload(self) -> None:
        """Kept for API compat — the engine is the source of truth."""

    def save(self) -> None:
        """Kept for API compat — every write commits immediately."""

    # ------------------------------------------------------------------ CRUD

    def insert(self, doc: dict[str, Any]) -> int:
        """Append a row, assigning its id; returns the id."""
        if not isinstance(doc, dict):
            raise TableError("a row must be a dict")
        if "id" in doc:
            raise TableError("id is assigned by the store")
        return self._engine.insert(self.name, doc)

    def get(self, row_id: int) -> dict[str, Any] | None:
        """One row by id (a copy), or None."""
        return self._engine.get(self.name, row_id)

    def find(self, query: Query | None = None) -> list[dict[str, Any]]:
        """Matching rows (copies), in insertion order."""
        return self._engine.find(self.name, query)

    def update(self, row_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        """Merge fields into one row; returns the updated copy (None if missing)."""
        if "id" in fields:
            raise TableError("id cannot be updated")
        return self._engine.update(self.name, row_id, fields)

    def delete(self, row_id: int) -> bool:
        """Delete one row; True when it was there."""
        return self._engine.delete(self.name, row_id)

    def all(self) -> list[dict[str, Any]]:
        """Every row (copies), in insertion order."""
        return self._engine.iter_rows(self.name)

    def count(self, query: Query | None = None) -> int:
        """How many rows (matching the query when given)."""
        return self._engine.count(self.name, query)

    def delete_where(self, query: Query) -> int:
        """Delete every matching row; returns how many."""
        return self._engine.delete_where(self.name, query)

    def __len__(self) -> int:
        return self._engine.count(self.name)
