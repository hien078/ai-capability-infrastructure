"""Validation: 422 error region, preserved values, 404s (README §4)."""

from __future__ import annotations

from typing import Any

from conftest import parse_html


def test_empty_title_422_with_error_region(client: Any) -> None:
    result = client("POST", "/tasks", form={"title": ""})
    assert result.status == 422
    dom = parse_html(result.body)
    region = dom.find("div", {"id": "form-errors"})
    assert region is not None
    assert region.attrs.get("role") == "alert"
    error = dom.find("p", {"class": "field-error", "id": "title-error"})
    assert error is not None
    assert error.text_content().strip() != ""


def test_whitespace_title_422(client: Any) -> None:
    result = client("POST", "/tasks", form={"title": "   "})
    assert result.status == 422


def test_long_title_422_and_value_preserved(client: Any) -> None:
    long_title = "x" * 81
    result = client("POST", "/tasks", form={"title": long_title, "assignee": "ana"})
    assert result.status == 422
    dom = parse_html(result.body)
    assert dom.find("p", {"id": "title-error"}) is not None
    title_input = dom.find("input", {"id": "task-title"})
    assert title_input is not None
    assert title_input.attrs.get("value") == long_title
    assignee_input = dom.find("input", {"id": "task-assignee"})
    assert assignee_input is not None
    assert assignee_input.attrs.get("value") == "ana"


def test_assignee_too_long_422(client: Any) -> None:
    result = client("POST", "/tasks", form={"title": "ok", "assignee": "a" * 41})
    assert result.status == 422
    dom = parse_html(result.body)
    assert dom.find("p", {"id": "assignee-error"}) is not None


def test_points_rejected_422(client: Any) -> None:
    """Points outside 0..8 are invalid (README §4)."""
    for bad in ("9", "abc", "3.5", "-1"):
        result = client("POST", "/tasks", form={"title": "ok", "points": bad})
        assert result.status == 422, bad
        dom = parse_html(result.body)
        assert dom.find("p", {"id": "points-error"}) is not None, bad


def test_points_empty_defaults_to_one(add: Any, page: Any) -> None:
    add("Fix login bug", points="")
    card = page().find("article", {"id": "task-t1"})
    badge = card.find("span", {"class": "points"})  # type: ignore[union-attr]
    assert badge.text_content() == "1"  # type: ignore[union-attr]


def test_bad_column_422(client: Any) -> None:
    result = client("POST", "/tasks", form={"title": "ok", "column": "blocked"})
    assert result.status == 422
    dom = parse_html(result.body)
    assert dom.find("p", {"id": "column-error"}) is not None


def test_move_unknown_task_404(client: Any) -> None:
    result = client("POST", "/tasks/t99/move", form={"column": "todo"})
    assert result.status == 404


def test_move_bad_column_422(add: Any, client: Any) -> None:
    tid = add("Fix login bug")
    result = client("POST", f"/tasks/{tid}/move", form={"column": "sideways"})
    assert result.status == 422
    dom = parse_html(result.body)
    assert dom.find("p", {"id": "column-error"}) is not None


def test_delete_unknown_404(client: Any) -> None:
    result = client("POST", "/tasks/t99/delete")
    assert result.status == 404
