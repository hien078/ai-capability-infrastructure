"""Hidden acceptance: the inverted index — incremental, lazy, crash-safe.

The index is a separate file, maintained on every mutation, built
lazily for old data dirs, and rebuilt — never fatal — when missing,
corrupt, stale-versioned or out of step with the store.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from jotbase.search import SearchIndex, tokenize
from jotbase.service import NoteService
from jotbase.storage import NoteStore

UTC = UTC


def _doc(note_id, title, body, updated="2026-01-01T00:00:00+00:00"):
    return {
        "id": note_id,
        "title": title,
        "body": body,
        "tags": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": updated,
    }


def test_index_is_built_and_persisted(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    service.add("Deploy runbook", body="how we ship", tags=["ops"])
    assert (tmp_path / "index.json").exists()
    reloaded = SearchIndex(tmp_path)
    assert reloaded.load()
    assert "deploy" in reloaded.docs[service.get(service.store.ids()[0]).id]["tokens"]


def test_index_tracks_edits_and_deletes(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    note = service.add("Garden", body="tomatoes")
    assert service.search("tomatoes").total == 1
    service.edit(note.id, body="basil")
    assert service.search("tomatoes").total == 0
    assert service.search("basil").total == 1
    service.delete(note.id)
    assert service.search("basil").total == 0


def test_search_builds_the_index_lazily_for_old_dirs(tmp_path):
    """A v1 data dir (notes, no index) searches with zero migration."""
    store = NoteStore(tmp_path)
    store.add(_doc("note_old", "Deploy runbook", "how we ship"))
    assert not (tmp_path / "index.json").exists()
    service = NoteService(store)
    result = service.search("runbook")
    assert result.total == 1
    assert result.items[0].id == "note_old"
    assert (tmp_path / "index.json").exists()


def test_a_corrupt_index_is_rebuilt_never_fatal(tmp_path):
    store = NoteStore(tmp_path)
    store.add(_doc("note_a", "Deploy runbook", "how we ship"))
    service = NoteService(store)
    service.search("runbook")  # builds the index
    (tmp_path / "index.json").write_text("{ not json", encoding="utf-8")
    fresh = NoteService(NoteStore(tmp_path))
    assert fresh.search("runbook").total == 1


def test_a_stale_version_index_is_rebuilt(tmp_path):
    store = NoteStore(tmp_path)
    store.add(_doc("note_a", "Deploy runbook", "how we ship"))
    (tmp_path / "index.json").write_text(
        json.dumps({"version": 9999, "docs": {}}), encoding="utf-8"
    )
    service = NoteService(store)
    assert service.search("runbook").total == 1


def test_an_out_of_step_index_is_rebuilt(tmp_path):
    """An external writer (a v1 process) adds a note behind our back."""
    store = NoteStore(tmp_path)
    store.add(_doc("note_a", "Deploy runbook", "how we ship"))
    service = NoteService(store)
    service.search("runbook")  # index built
    store.add(_doc("note_external", "Basil", "herb"))  # no index update
    result = service.search("basil")
    assert result.total == 1
    assert result.items[0].id == "note_external"


def test_search_never_rewrites_notes_json(tmp_path):
    """The index is a separate file; the v1 notes format is untouched."""
    store = NoteStore(tmp_path)
    store.add(_doc("note_a", "Deploy runbook", "how we ship"))
    service = NoteService(store)
    service.search("runbook")  # builds the index
    service.search("deploy")  # and again, from the index
    raw = json.loads((tmp_path / "notes.json").read_text())
    assert set(raw) == {"format", "notes"}
    assert set(raw["notes"]) == {"note_a"}
    assert raw["notes"]["note_a"]["title"] == "Deploy runbook"


def test_indexed_tokens_cover_title_and_body(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    note = service.add("Runbook", body="emergency deploy procedure")
    index = SearchIndex(tmp_path)
    assert index.load()
    stats = index.docs[note.id]
    assert stats["tokens"]["emergency"] == 1
    assert stats["title_tokens"]["runbook"] == 1
    assert "runbook" in stats["tokens"]
    assert tokenize("Deploy!") == ["deploy"]


def test_candidates_and_scores_come_from_the_index(tmp_path):
    from datetime import timedelta

    service = NoteService(NoteStore(tmp_path))
    a = service.add("Deploy", body="")
    b = service.add("Other", body="deploy deploy")
    index = SearchIndex(tmp_path)
    assert index.load()
    recent_now = datetime.now(UTC) + timedelta(days=1)
    old_now = datetime.now(UTC) + timedelta(days=100)
    hits = index.candidates(["deploy"])
    assert {a.id, b.id} <= hits
    # title weight 2.0, tf 2 -> 2.0; recency multiplies by 1.5
    assert index.score(b.id, ["deploy"], recent_now) == pytest.approx(2.0 * 1.5)
    assert index.score(a.id, ["deploy"], recent_now) == pytest.approx(2.0 * 1.5)
    assert index.score(b.id, ["deploy"], old_now) == pytest.approx(2.0)
