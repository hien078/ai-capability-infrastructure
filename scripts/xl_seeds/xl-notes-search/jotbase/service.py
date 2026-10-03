"""The note service: validation, CRUD, listing, stats.

The service is the only writer: it owns ids, timestamps and tag
normalization. ``list_notes`` orders by creation time, newest first,
tie-broken by id ascending — the order is pinned (the CLI and the API
both show it).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jotbase.errors import InvalidNote, NoteNotFound
from jotbase.model import Note, new_id, normalize_tags
from jotbase.storage import NoteStore


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NoteService:
    """Everything a client does to notes."""

    def __init__(self, store: NoteStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ CRUD

    def add(self, title: str, body: str = "", tags: list[str] | tuple[str, ...] = ()) -> Note:
        """Create a note (validated, id-assigned, saved)."""
        now = _utcnow()
        note = Note(
            id=new_id(),
            title=title,
            body=body,
            tags=normalize_tags(list(tags)),
            created_at=now,
            updated_at=now,
        )
        note.validate()
        self.store.add(note.to_dict())
        return note

    def edit(
        self,
        note_id: str,
        title: str | None = None,
        body: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
    ) -> Note:
        """Edit fields (None = unchanged); bumps updated_at."""
        doc = self.store.get(note_id)
        if doc is None:
            raise NoteNotFound(note_id)
        note = Note.from_dict(doc)
        if title is not None:
            note.title = title
        if body is not None:
            note.body = body
        if tags is not None:
            note.tags = normalize_tags(list(tags))
        note.updated_at = _utcnow()
        note.validate()
        self.store.update(note.to_dict())
        return note

    def delete(self, note_id: str) -> None:
        if not self.store.delete(note_id):
            raise NoteNotFound(note_id)

    def get(self, note_id: str) -> Note:
        doc = self.store.get(note_id)
        if doc is None:
            raise NoteNotFound(note_id)
        return Note.from_dict(doc)

    # --------------------------------------------------------------- listing

    def list_notes(self, tag: str | None = None, limit: int | None = None) -> list[Note]:
        """Notes newest-first (created_at desc, then id asc); optional tag filter."""
        notes = [Note.from_dict(doc) for doc in self.store.all_docs()]
        if tag is not None:
            slug = normalize_tags([tag])
            if not slug:
                raise InvalidNote("tag must not be empty")
            notes = [n for n in notes if slug[0] in n.tags]
        notes.sort(key=lambda n: (-n.created_at.timestamp(), n.id))
        if limit is not None:
            if limit < 0:
                raise InvalidNote("limit must be >= 0")
            notes = notes[:limit]
        return notes

    def stats(self) -> dict[str, Any]:
        """Counts for the dashboard."""
        notes = [Note.from_dict(doc) for doc in self.store.all_docs()]
        tags: dict[str, int] = {}
        for note in notes:
            for tag in note.tags:
                tags[tag] = tags.get(tag, 0) + 1
        return {"notes": len(notes), "tags": dict(sorted(tags.items()))}
