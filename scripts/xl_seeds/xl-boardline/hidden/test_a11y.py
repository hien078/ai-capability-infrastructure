"""Accessibility: labels, aria-labels, roles, heading hierarchy (README §6)."""

from __future__ import annotations

from typing import Any


def test_every_control_has_an_accessible_name(add: Any, page: Any) -> None:
    """Each input/select with an id has a <label for> or an aria-label."""
    add("Fix login bug")
    board = page()
    controls = board.find_all("input") + board.find_all("select")
    assert controls, "no form controls rendered"
    for control in controls:
        control_id = control.attrs.get("id", "")
        assert control_id, f"{control.tag} without id"
        has_label = board.find("label", {"for": control_id}) is not None
        has_aria = bool(control.attrs.get("aria-label"))
        assert has_label or has_aria, f"{control_id} has no accessible name"


def test_move_select_has_aria_label(add: Any, page: Any) -> None:
    add("Fix login bug")
    select = page().find("select", {"id": "move-t1"})
    assert select is not None
    assert select.attrs.get("aria-label") == "Move task to column"


def test_delete_button_has_aria_label(add: Any, page: Any) -> None:
    add("Fix login bug")
    button = page().find("button", {"class": "task-delete-submit"})
    assert button is not None
    assert button.attrs.get("aria-label") == "Delete task"


def test_buttons_have_text_or_aria_label(add: Any, page: Any) -> None:
    add("Fix login bug")
    board = page()
    buttons = board.find_all("button")
    assert buttons
    for button in buttons:
        assert button.text_content().strip() or button.attrs.get("aria-label"), (
            f"button {button.attrs.get('id', button.attrs.get('class'))} has no name"
        )


def test_heading_hierarchy(add: Any, page: Any) -> None:
    add("Fix login bug")
    board = page()
    h1s = board.find_all("h1")
    assert len(h1s) == 1
    h2s = board.find_all("h2")
    assert [h.text_content().strip() for h in h2s] == ["To do", "In progress", "Done"]
    card = board.find("article", {"id": "task-t1"})
    assert card is not None
    assert card.find("h3", {"class": "task-title"}) is not None
