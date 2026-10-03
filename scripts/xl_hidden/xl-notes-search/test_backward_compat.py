"""Hidden acceptance: backward compatibility — the hard requirement.

A data dir written by v1 loads and searches with zero migration;
``notes.json``'s format never changes; the pre-existing signatures and
response shapes are pinned.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import pytest
from jotbase.model import Note
from jotbase.service import NoteService
from jotbase.storage import NoteStore

UTC = UTC

#: A v1 data dir: notes only, no index, exactly the documented format.
V1_NOTES = {
    "format": 1,
    "notes": {
        "note_v1aaa": {
            "id": "note_v1aaa",
            "title": "Deploy runbook",
            "body": "how we ship",
            "tags": ["ops"],
            "created_at": "2025-11-01T09:00:00+00:00",
            "updated_at": "2025-11-01T09:00:00+00:00",
        },
        "note_v1bbb": {
            "id": "note_v1bbb",
            "title": "Garden",
            "body": "basil",
            "tags": [],
            "created_at": "2025-12-01T09:00:00+00:00",
            "updated_at": "2025-12-01T09:00:00+00:00",
        },
    },
}


@pytest.fixture
def v1_dir(tmp_path) -> Path:
    root = tmp_path / "v1data"
    root.mkdir()
    (root / "notes.json").write_text(json.dumps(V1_NOTES), encoding="utf-8")
    return root


def test_a_v1_data_dir_loads_without_migration(v1_dir):
    store = NoteStore(v1_dir)
    assert len(store) == 2
    service = NoteService(store)
    assert service.get("note_v1aaa").title == "Deploy runbook"


def test_a_v1_data_dir_searches(v1_dir):
    service = NoteService(NoteStore(v1_dir))
    result = service.search("runbook")
    assert result.total == 1
    assert result.items[0].id == "note_v1aaa"


def test_v1_notes_json_format_is_unchanged(v1_dir):
    before = (v1_dir / "notes.json").read_text()
    service = NoteService(NoteStore(v1_dir))
    service.search("runbook")
    service.add("New note", body="hello")
    raw = json.loads((v1_dir / "notes.json").read_text())
    assert set(raw) == {"format", "notes"}
    assert "note_v1aaa" in raw["notes"]
    assert before  # the file was real v1 content and still is, plus the add


def test_list_notes_signature_is_unchanged(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    service.add("A", tags=["x"])
    service.add("B", tags=["x"])
    assert service.list_notes() == service.list_notes()
    assert len(service.list_notes(tag="x")) == 2
    assert len(service.list_notes(limit=1)) == 1
    assert len(service.list_notes(tag="x", limit=1)) == 1


def test_list_notes_order_is_newest_first(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    first = service.add("First")
    second = service.add("Second")
    assert [n.id for n in service.list_notes()] == [second.id, first.id]


def test_note_dict_shape_is_pinned(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    note = service.add("Deploy", body="b", tags=["Ops Guide"])
    payload = note.to_dict()
    assert set(payload) == {"id", "title", "body", "tags", "created_at", "updated_at"}
    assert payload["tags"] == ["ops-guide"]
    assert Note.from_dict(payload) == note


def test_validation_rules_are_unchanged(tmp_path):
    from jotbase.errors import InvalidNote

    service = NoteService(NoteStore(tmp_path))
    with pytest.raises(InvalidNote):
        service.add("")
    with pytest.raises(InvalidNote):
        service.add("x" * 201)
    with pytest.raises(InvalidNote):
        service.add("t", tags=[f"tag{i}" for i in range(11)])
    # duplicates are normalized away, never an error
    assert service.add("t", tags=["a", "A", "a"]).tags == ["a"]
