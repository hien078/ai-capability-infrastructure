"""Hidden acceptance: the search query model and page math.

Validation is part of the contract: an empty query or an out-of-range
page is a caller error, never a silent default.
"""

from __future__ import annotations

import pytest
from jotbase.model import Note
from jotbase.search import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    SearchError,
    SearchQuery,
    tokenize,
)


def test_query_defaults():
    query = SearchQuery("deploy runbook")
    assert query.text == "deploy runbook"
    assert query.page == 1
    assert query.page_size == DEFAULT_PAGE_SIZE


def test_query_rejects_empty_text():
    for bad in ("", "   ", None):
        with pytest.raises(SearchError):
            SearchQuery(bad)  # type: ignore[arg-type]


def test_query_rejects_a_bad_page():
    with pytest.raises(SearchError):
        SearchQuery("x", page=0)
    with pytest.raises(SearchError):
        SearchQuery("x", page=-1)


def test_query_bounds_the_page_size():
    with pytest.raises(SearchError):
        SearchQuery("x", page_size=0)
    with pytest.raises(SearchError):
        SearchQuery("x", page_size=MAX_PAGE_SIZE + 1)
    assert SearchQuery("x", page_size=1).page_size == 1
    assert SearchQuery("x", page_size=MAX_PAGE_SIZE).page_size == MAX_PAGE_SIZE


def test_tokenize_casefolds_and_splits():
    assert tokenize("Deploy RUNBOOK, v2!") == ["deploy", "runbook", "v2"]
    assert tokenize("  ") == []
    assert tokenize("Übung") == ["bung"]


def test_page_total_pages_math():
    page = Page(query="x", page=1, page_size=20, total=41)
    assert page.total_pages == 3
    assert Page(query="x", page=1, page_size=20, total=0).total_pages == 0
    assert Page(query="x", page=1, page_size=20, total=20).total_pages == 1


def test_page_envelope_shape():
    note = Note(id="note_a", title="t", body="b", tags=["x"])
    page = Page(query="q", page=2, page_size=5, total=7, items=[note])
    payload = page.to_dict()
    assert set(payload) == {
        "query",
        "page",
        "page_size",
        "total",
        "total_pages",
        "items",
    }
    assert payload["page"] == 2
    assert payload["total_pages"] == 2
    assert payload["items"][0]["id"] == "note_a"
