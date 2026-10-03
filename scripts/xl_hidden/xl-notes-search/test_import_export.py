"""Hidden acceptance: the import/export exchange format.

Round trips, merge semantics (existing ids kept unless replace), and
whole-document validation — a bad document imports nothing.
"""

from __future__ import annotations

import json

import pytest
from jotbase.errors import InvalidNote, JotbaseError
from jotbase.import_export import (
    export_notes,
    export_to_path,
    import_from_path,
    import_notes,
)
from jotbase.service import NoteService
from jotbase.storage import NoteStore

DOCUMENT = {
    "format": 1,
    "notes": [
        {
            "id": "note_ext001",
            "title": "External one",
            "body": "from elsewhere",
            "tags": ["imported"],
            "created_at": "2026-01-01T09:00:00+00:00",
            "updated_at": "2026-01-01T09:00:00+00:00",
        },
        {
            "id": "note_ext002",
            "title": "External two",
            "body": "",
            "tags": [],
            "created_at": "2026-01-02T09:00:00+00:00",
            "updated_at": "2026-01-02T09:00:00+00:00",
        },
    ],
}


def test_export_roundtrips_through_import(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    service.add("Local note", body="kept")
    document = export_notes(service)
    assert document["format"] == 1
    assert len(document["notes"]) == 1

    other = NoteService(NoteStore(tmp_path / "other"))
    result = import_notes(other, document)
    assert result.added == 1
    assert other.get(document["notes"][0]["id"]).title == "Local note"


def test_import_skips_existing_ids_by_default(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    service.add("Local note", body="kept")
    local_id = service.store.ids()[0]
    document = {
        "format": 1,
        "notes": [
            {
                "id": local_id,
                "title": "ATTACK OF THE REPLACEMENT",
                "body": "",
                "tags": [],
                "created_at": "2026-01-01T09:00:00+00:00",
                "updated_at": "2026-01-01T09:00:00+00:00",
            }
        ],
    }
    result = import_notes(service, document)
    assert result.added == 0
    assert result.skipped == 1
    assert service.get(local_id).title == "Local note"


def test_import_replaces_when_asked(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    service.add("Local note", body="kept")
    local_id = service.store.ids()[0]
    document = {
        "format": 1,
        "notes": [
            {
                "id": local_id,
                "title": "Replacement",
                "body": "new body",
                "tags": [],
                "created_at": "2026-01-01T09:00:00+00:00",
                "updated_at": "2026-01-01T09:00:00+00:00",
            }
        ],
    }
    result = import_notes(service, document, replace=True)
    assert result.added == 1
    assert service.get(local_id).title == "Replacement"
    assert service.get(local_id).body == "new body"


def test_a_bad_document_imports_nothing(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    service.add("Local note", body="kept")
    bad = {
        "format": 1,
        "notes": [
            DOCUMENT["notes"][0],
            {
                "id": "note_bad",
                "title": "",
                "body": "",
                "tags": [],
                "created_at": "2026-01-01T09:00:00+00:00",
                "updated_at": "2026-01-01T09:00:00+00:00",
            },
        ],
    }
    with pytest.raises(InvalidNote):
        import_notes(service, bad)
    assert len(service.store) == 1  # the local note, nothing imported


def test_path_roundtrip(tmp_path):
    service = NoteService(NoteStore(tmp_path / "data"))
    service.add("One")
    service.add("Two")
    out = tmp_path / "export.json"
    assert export_to_path(service, out) == 2
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["format"] == 1

    other = NoteService(NoteStore(tmp_path / "other"))
    result = import_from_path(other, out)
    assert result.added == 2
    assert len(other.store) == 2


def test_a_corrupt_import_file_is_a_clean_error(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(JotbaseError):
        import_from_path(service, bad)
