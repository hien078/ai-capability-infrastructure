"""JSON-file persistence for notes.

Layout: ``<root>/notes.json`` is ``{"format": 1, "notes": {id: note}}``
— a dict keyed by id, in insertion order. The store works on plain dicts
(the persisted shape); the service owns the Note dataclass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jotbase.errors import JotbaseError


class NoteStore:
    """The notes file: load once, mutate in memory, save on every write."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._notes: dict[str, dict[str, Any]] = {}
        self._load()

    @property
    def path(self) -> Path:
        return self.root / "notes.json"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise JotbaseError(f"corrupt notes store: {exc}") from None
        notes = data.get("notes", {})
        if not isinstance(notes, dict):
            raise JotbaseError("corrupt notes store: notes must be an object")
        self._notes = notes

    def save(self) -> None:
        """Write the whole file."""
        self.path.write_text(
            json.dumps({"format": 1, "notes": self._notes}, indent=2) + "\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ CRUD

    def add(self, doc: dict[str, Any]) -> None:
        self._notes[doc["id"]] = dict(doc)
        self.save()

    def update(self, doc: dict[str, Any]) -> None:
        if doc["id"] not in self._notes:
            raise JotbaseError(f"cannot update missing note {doc['id']}")
        self._notes[doc["id"]] = dict(doc)
        self.save()

    def delete(self, note_id: str) -> bool:
        if note_id not in self._notes:
            return False
        del self._notes[note_id]
        self.save()
        return True

    def get(self, note_id: str) -> dict[str, Any] | None:
        doc = self._notes.get(note_id)
        return dict(doc) if doc is not None else None

    def ids(self) -> list[str]:
        return list(self._notes)

    def all_docs(self) -> list[dict[str, Any]]:
        return [dict(doc) for doc in self._notes.values()]

    def __len__(self) -> int:
        return len(self._notes)
