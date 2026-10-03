"""Hidden acceptance: ranking order.

Title tokens outrank body tokens, recently-edited notes get the recency
boost, more matched tokens score higher, and ties break by created_at
descending then id ascending — the same tail as ``list_notes``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from jotbase.service import NoteService
from jotbase.storage import NoteStore

UTC = UTC
NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    """A controllable service clock for deterministic created_at stamps."""
    holder = {"now": datetime(2026, 1, 1, 12, 0, tzinfo=UTC)}

    def fake_now():
        return holder["now"]

    monkeypatch.setattr("jotbase.service._utcnow", fake_now)
    return holder


def test_title_beats_body(tmp_path, clock):
    service = NoteService(NoteStore(tmp_path))
    clock["now"] = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    title_note = service.add("Deploy runbook", body="nothing here")
    clock["now"] = datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    body_note = service.add("Unrelated", body="deploy")
    result = service.search("deploy", now=NOW)
    # the title note is OLDER — only the title weight puts it first
    assert [n.id for n in result.items] == [title_note.id, body_note.id]


def test_recency_boost_outranks_stale(tmp_path, clock):
    service = NoteService(NoteStore(tmp_path))
    clock["now"] = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    fresh = service.add("Deploy", body="procedure")
    clock["now"] = datetime(2026, 6, 1, tzinfo=UTC)
    stale = service.add("Deploy", body="procedure")  # newer created_at
    clock["now"] = datetime(2026, 9, 30, tzinfo=UTC)
    service.edit(fresh.id, body="procedure")  # fresh edited recently
    result = service.search("deploy", now=NOW)
    # stale is NEWER — only the recency boost puts fresh first
    assert [n.id for n in result.items] == [fresh.id, stale.id]


def test_more_matched_tokens_score_higher(tmp_path, clock):
    service = NoteService(NoteStore(tmp_path))
    clock["now"] = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    one = service.add("A", body="deploy")
    clock["now"] = datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    both = service.add("B", body="deploy runbook")
    result = service.search("deploy runbook", now=NOW)
    assert [n.id for n in result.items] == [both.id, one.id]


def test_ties_break_by_created_desc_then_id(tmp_path, clock):
    service = NoteService(NoteStore(tmp_path))
    clock["now"] = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    older = service.add("Deploy", body="same words exactly")
    clock["now"] = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    newer = service.add("Deploy", body="same words exactly")
    result = service.search("deploy", now=NOW)
    assert [n.id for n in result.items] == [newer.id, older.id]


def test_identical_notes_break_by_id(tmp_path, clock):
    service = NoteService(NoteStore(tmp_path))
    clock["now"] = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for _ in range(3):
        service.add("Deploy", body="same")
    result = service.search("deploy", now=NOW)
    ids = [n.id for n in result.items]
    assert ids == sorted(ids)


def test_no_match_is_an_empty_page_not_an_error(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    service.add("Deploy runbook", body="how we ship")
    result = service.search("zzz-nothing")
    assert result.total == 0
    assert result.items == []
    assert result.total_pages == 0


def test_search_accepts_a_plain_string(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    note = service.add("Deploy runbook", body="how we ship")
    result = service.search("runbook")
    assert result.total == 1
    assert result.items[0].id == note.id


def test_tag_token_is_plain_text(tmp_path):
    """v1: `tag:foo` tokenizes to plain [tag, foo] — tags are not queryable."""
    service = NoteService(NoteStore(tmp_path))
    note = service.add("Conventions", body="the tag:foo convention", tags=["deploy"])
    result = service.search("tag:foo")
    assert [n.id for n in result.items] == [note.id]
