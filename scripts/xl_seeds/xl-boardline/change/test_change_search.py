"""CHANGE v2 — title search: GET /?q= filters cards, case-insensitive."""

from __future__ import annotations

from typing import Any


def test_change_search_form_present(page: Any) -> None:
    board = page()
    form = board.find("form", {"id": "search"})
    assert form is not None
    assert form.attrs.get("method") == "get"
    assert form.attrs.get("action") == "/"
    label = board.find("label", {"for": "search-box"})
    assert label is not None
    assert label.text_content().strip() == "Search"
    box = form.find("input", {"id": "search-box"})
    assert box is not None
    assert box.attrs.get("name") == "q"
    assert box.attrs.get("type") == "search"


def test_change_search_filters_cards(add: Any, page: Any) -> None:
    add("Fix login bug")
    add("Write API docs")
    board = page("/?q=fix")
    titles = [
        card.find("h3", {"class": "task-title"}).text_content()  # type: ignore[union-attr]
        for card in board.find_all("article", {"class": "task"})
    ]
    assert titles == ["Fix login bug"]


def test_change_search_is_case_insensitive(add: Any, page: Any) -> None:
    add("Fix login bug")
    add("Write API docs")
    board = page("/?q=FIX")
    titles = [
        card.find("h3", {"class": "task-title"}).text_content()  # type: ignore[union-attr]
        for card in board.find_all("article", {"class": "task"})
    ]
    assert titles == ["Fix login bug"]


def test_change_search_matches_substring(add: Any, page: Any) -> None:
    add("Fix login bug")
    board = page("/?q=logi")
    assert board.find("article", {"id": "task-t1"}) is not None


def test_change_search_no_match_shows_placeholders(add: Any, page: Any) -> None:
    add("Fix login bug")
    board = page("/?q=zzz")
    assert board.find_all("article", {"class": "task"}) == []
    for column_id in ("col-todo", "col-doing", "col-done"):
        section = board.find("section", {"id": column_id})
        assert section is not None
        assert section.find("p", {"class": "empty"}) is not None


def test_change_search_empty_shows_all(add: Any, page: Any) -> None:
    add("Fix login bug")
    add("Write API docs")
    board = page("/?q=")
    assert len(board.find_all("article", {"class": "task"})) == 2


def test_change_search_box_shows_query(add: Any, page: Any) -> None:
    add("Fix login bug")
    board = page("/?q=fix")
    box = board.find("input", {"id": "search-box"})
    assert box is not None
    assert box.attrs.get("value") == "fix"


def test_change_search_keeps_add_form(add: Any, page: Any) -> None:
    """The search form is ADDED, not a replacement — the add form still works."""
    add("Fix login bug")
    board = page("/?q=fix")
    assert board.find("form", {"id": "add-task"}) is not None
    result_id = add("Another fix")
    assert result_id == "t2"
