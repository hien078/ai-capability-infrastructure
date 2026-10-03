"""Import and export notes as one JSON document.

The exchange format is ``{"format": 1, "notes": [note dicts]}`` — the
same note shape as ``notes.json``, as a list. Import merges: a note
whose id already exists is kept (skipped) unless ``replace=True``;
every note is validated before anything is written, so a bad document
imports nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jotbase.errors import InvalidNote, JotbaseError
from jotbase.model import Note
from jotbase.service import NoteService

EXPORT_FORMAT = 1


@dataclass(frozen=True)
class ImportResult:
    """What an import did."""

    added: int
    skipped: int
    ids: list[str] = field(default_factory=list)


def export_notes(service: NoteService) -> dict[str, Any]:
    """The whole store as one exchange document (insertion order)."""
    return {
        "format": EXPORT_FORMAT,
        "notes": [doc for doc in service.store.all_docs()],
    }


def export_to_path(service: NoteService, path: Path) -> int:
    """Write the exchange document to a file; returns the note count."""
    document = export_notes(service)
    Path(path).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return len(document["notes"])


def _validate_document(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or not isinstance(document.get("notes"), list):
        raise InvalidNote('an exchange document is {"format": 1, "notes": [...]}')
    notes = document["notes"]
    for doc in notes:
        if not isinstance(doc, dict):
            raise InvalidNote("every note must be a JSON object")
        Note.from_dict(doc).validate()
    return notes


def import_notes(service: NoteService, document: Any, replace: bool = False) -> ImportResult:
    """Merge an exchange document into the store.

    Existing ids are kept (skipped) unless ``replace=True``. The whole
    document is validated first — a bad note imports nothing.
    """
    notes = _validate_document(document)
    added, skipped, ids = 0, 0, []
    for doc in notes:
        if doc["id"] in service.store.ids() and not replace:
            skipped += 1
            continue
        if doc["id"] in service.store.ids():
            service.store.update(doc)
        else:
            service.store.add(doc)
        added += 1
        ids.append(doc["id"])
    return ImportResult(added=added, skipped=skipped, ids=ids)


def import_from_path(service: NoteService, path: Path, replace: bool = False) -> ImportResult:
    """Read an exchange document from a file and import it."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JotbaseError(f"corrupt import file: {exc}") from None
    return import_notes(service, document, replace=replace)
