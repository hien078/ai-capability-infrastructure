"""CHANGE v2 — points scale extended to 0..13 (was 0..8)."""

from __future__ import annotations

from typing import Any

from conftest import parse_html


def test_change_points_13_accepted(add: Any, page: Any) -> None:
    add("Big feature", points="13")
    card = page().find("article", {"id": "task-t1"})
    badge = card.find("span", {"class": "points"})  # type: ignore[union-attr]
    assert badge.text_content() == "13"  # type: ignore[union-attr]


def test_change_points_9_now_valid(add: Any, page: Any) -> None:
    """9 was rejected before the change; it is a valid score now."""
    add("Medium feature", points="9")
    card = page().find("article", {"id": "task-t1"})
    badge = card.find("span", {"class": "points"})  # type: ignore[union-attr]
    assert badge.text_content() == "9"  # type: ignore[union-attr]


def test_change_points_14_rejected(client: Any) -> None:
    result = client("POST", "/tasks", form={"title": "ok", "points": "14"})
    assert result.status == 422
    dom = parse_html(result.body)
    assert dom.find("p", {"id": "points-error"}) is not None


def test_change_points_input_max_13(page: Any) -> None:
    points = page().find("input", {"id": "task-points"})
    assert points is not None
    assert points.attrs.get("max") == "13"
    assert points.attrs.get("min") == "0"
