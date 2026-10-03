"""Hidden acceptance: pagination.

Defaults, bounds, the page-beyond-the-end contract and the totals math
the envelope promises.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from jotbase.search import SearchError, SearchQuery
from jotbase.service import NoteService
from jotbase.storage import NoteStore

UTC = UTC
NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def five_notes(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    for i in range(5):
        service.add(f"Deploy note {i}", body="runbook procedure")
    return service


def test_default_page_is_one_of_twenty(five_notes):
    result = five_notes.search("deploy", now=NOW)
    assert result.page == 1
    assert result.page_size == 20
    assert result.total == 5
    assert len(result.items) == 5


def test_page_two_of_two_has_the_rest(five_notes):
    result = five_notes.search(SearchQuery("deploy", page=2, page_size=2), now=NOW)
    assert result.total == 5
    assert result.total_pages == 3
    assert len(result.items) == 2


def test_page_beyond_the_end_is_empty_but_true(five_notes):
    result = five_notes.search(SearchQuery("deploy", page=4, page_size=2), now=NOW)
    assert result.items == []
    assert result.total == 5
    assert result.total_pages == 3


def test_last_partial_page(five_notes):
    result = five_notes.search(SearchQuery("deploy", page=3, page_size=2), now=NOW)
    assert len(result.items) == 1


def test_page_size_one(five_notes):
    result = five_notes.search(SearchQuery("deploy", page=1, page_size=1), now=NOW)
    assert len(result.items) == 1
    assert result.total == 5
    assert result.total_pages == 5


def test_out_of_range_page_is_a_caller_error(five_notes):
    with pytest.raises(SearchError):
        SearchQuery("deploy", page=0)
    with pytest.raises(SearchError):
        SearchQuery("deploy", page_size=0)
    with pytest.raises(SearchError):
        SearchQuery("deploy", page_size=101)


def test_pages_are_disjoint_and_stable(five_notes):
    first = five_notes.search(SearchQuery("deploy", page=1, page_size=2), now=NOW)
    second = five_notes.search(SearchQuery("deploy", page=2, page_size=2), now=NOW)
    ids = {n.id for n in first.items} | {n.id for n in second.items}
    assert len(ids) == 4
    assert not ({n.id for n in first.items} & {n.id for n in second.items})
