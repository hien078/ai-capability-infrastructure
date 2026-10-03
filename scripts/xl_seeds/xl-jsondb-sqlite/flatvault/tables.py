"""One table = one JSON file: ``<root>/<name>.json``.

The file is ``{"rows": [row dicts], "next_id": n}`` — rows carry their
``id``; ids come from the never-reused ``next_id`` counter. Every write
rewrites the whole file (fine for a small blog; the reason the sqlite
refactor exists).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flatvault.errors import TableError
from flatvault.queries import Query, match
from flatvault.serialize import read_document, write_document


class Table:
    """The JSON-file table."""

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = Path(path)
        self._rows: list[dict[str, Any]] = []
        self._next_id = 1
        self.reload()

    # ------------------------------------------------------------------ load

    def reload(self) -> None:
        """Re-read the file (a missing file is an empty table)."""
        if not self.path.exists():
            self._rows = []
            self._next_id = 1
            return
        doc = read_document(self.path)
        if not isinstance(doc, dict) or not isinstance(doc.get("rows", []), list):
            raise TableError(f"corrupt table file {self.path}")
        rows = doc.get("rows", [])
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), int):
                raise TableError(f"corrupt row in {self.path}: {row!r}")
        self._rows = rows
        ids = [row["id"] for row in rows]
        self._next_id = doc.get("next_id", max(ids, default=0) + 1)

    def save(self) -> None:
        """Write the whole file."""
        write_document(self.path, {"rows": self._rows, "next_id": self._next_id})

    # ------------------------------------------------------------------ CRUD

    def insert(self, doc: dict[str, Any]) -> int:
        """Append a row, assigning its id; returns the id."""
        if not isinstance(doc, dict):
            raise TableError("a row must be a dict")
        if "id" in doc:
            raise TableError("id is assigned by the store")
        row = dict(doc)
        row["id"] = self._next_id
        self._next_id += 1
        self._rows.append(row)
        self.save()
        return row["id"]

    def get(self, row_id: int) -> dict[str, Any] | None:
        """One row by id (a copy), or None."""
        for row in self._rows:
            if row["id"] == row_id:
                return dict(row)
        return None

    def find(self, query: Query | None = None) -> list[dict[str, Any]]:
        """Matching rows (copies), in insertion order."""
        if query is None:
            return [dict(row) for row in self._rows]
        return [dict(row) for row in self._rows if match(query, row)]

    def update(self, row_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        """Merge fields into one row; returns the updated copy (None if missing)."""
        if "id" in fields:
            raise TableError("id cannot be updated")
        for row in self._rows:
            if row["id"] == row_id:
                row.update(fields)
                self.save()
                return dict(row)
        return None

    def delete(self, row_id: int) -> bool:
        """Delete one row; True when it was there."""
        for index, row in enumerate(self._rows):
            if row["id"] == row_id:
                del self._rows[index]
                self.save()
                return True
        return False

    def all(self) -> list[dict[str, Any]]:
        """Every row (copies), in insertion order."""
        return [dict(row) for row in self._rows]

    def __len__(self) -> int:
        return len(self._rows)
