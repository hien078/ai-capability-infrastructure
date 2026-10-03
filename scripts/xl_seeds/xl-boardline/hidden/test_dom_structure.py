"""Page structure: the exact DOM the spec pins (README §5)."""

from __future__ import annotations

from typing import Any


def test_doctype_head_lang_and_title(page: Any) -> None:
    result = page()
    html_node = result.find("html")
    assert html_node is not None
    assert html_node.attrs.get("lang") == "en"
    assert result.find("meta", {"charset": "utf-8"}) is not None
    title = result.find("title")
    assert title is not None
    assert title.text_content() == "Task board"


def test_h1(page: Any) -> None:
    h1 = page().find("h1")
    assert h1 is not None
    assert h1.text_content().strip() == "Task board"


def test_three_columns_with_headings(page: Any) -> None:
    board = page()
    for column_id, heading in (
        ("col-todo", "To do"),
        ("col-doing", "In progress"),
        ("col-done", "Done"),
    ):
        section = board.find("section", {"id": column_id, "class": "column"})
        assert section is not None, column_id
        h2 = section.find("h2")
        assert h2 is not None
        assert h2.text_content().strip() == heading


def test_add_form_inputs(page: Any) -> None:
    board = page()
    form = board.find("form", {"id": "add-task"})
    assert form is not None
    assert form.attrs.get("method") == "post"
    assert form.attrs.get("action") == "/tasks"
    title = form.find("input", {"id": "task-title"})
    assert title is not None
    assert title.attrs.get("name") == "title"
    assert title.attrs.get("required") is not None
    assert title.attrs.get("maxlength") == "80"
    assignee = form.find("input", {"id": "task-assignee"})
    assert assignee is not None
    assert assignee.attrs.get("name") == "assignee"
    assert assignee.attrs.get("maxlength") == "40"
    points = form.find("input", {"id": "task-points"})
    assert points is not None
    assert points.attrs.get("name") == "points"
    assert points.attrs.get("type") == "number"
    assert points.attrs.get("min") == "0"


def test_points_input_bounds(page: Any) -> None:
    points = page().find("input", {"id": "task-points"})
    assert points is not None
    assert points.attrs.get("max") == "8"


def test_add_form_labels(page: Any) -> None:
    board = page()
    for control_id, text in (
        ("task-title", "Title"),
        ("task-assignee", "Assignee"),
        ("task-points", "Points"),
        ("task-column", "Column"),
    ):
        label = board.find("label", {"for": control_id})
        assert label is not None, control_id
        assert label.text_content().strip() == text


def test_add_form_column_select(page: Any) -> None:
    select = page().find("select", {"id": "task-column"})
    assert select is not None
    assert select.attrs.get("name") == "column"
    options = {o.attrs.get("value"): o.text_content().strip() for o in select.find_all("option")}
    assert options == {"todo": "To do", "doing": "In progress", "done": "Done"}


def test_add_submit_button(page: Any) -> None:
    button = page().find("button", {"id": "add-task-submit"})
    assert button is not None
    assert button.attrs.get("type") == "submit"
    assert button.text_content().strip() == "Add task"


def test_empty_column_placeholder(page: Any) -> None:
    board = page()
    for column_id in ("col-todo", "col-doing", "col-done"):
        section = board.find("section", {"id": column_id})
        assert section is not None
        empty = section.find("p", {"class": "empty"})
        assert empty is not None, column_id
        assert empty.text_content().strip() == "No tasks yet."


def test_card_structure(add: Any, page: Any) -> None:
    add("Fix login bug", assignee="ana", points="3")
    board = page()
    card = board.find("article", {"class": "task"})
    assert card is not None
    assert card.attrs.get("id") == "task-t1"
    assert card.attrs.get("data-column") == "todo"
    heading = card.find("h3", {"class": "task-title"})
    assert heading is not None
    assert heading.text_content() == "Fix login bug"
    assignee = card.find("p", {"class": "task-assignee"})
    assert assignee is not None
    assert assignee.text_content() == "ana"
    points = card.find("p", {"class": "task-points"})
    assert points is not None
    badge = points.find("span", {"class": "points"})
    assert badge is not None
    assert badge.text_content() == "3"


def test_card_forms(add: Any, page: Any) -> None:
    add("Fix login bug")
    board = page()
    card = board.find("article", {"id": "task-t1"})
    assert card is not None
    move = card.find("form", {"class": "task-move"})
    assert move is not None
    assert move.attrs.get("method") == "post"
    assert move.attrs.get("action") == "/tasks/t1/move"
    move_select = move.find("select", {"id": "move-t1"})
    assert move_select is not None
    assert move_select.attrs.get("name") == "column"
    move_button = move.find("button", {"class": "task-move-submit"})
    assert move_button is not None
    assert move_button.text_content().strip() == "Move"
    delete = card.find("form", {"class": "task-delete"})
    assert delete is not None
    assert delete.attrs.get("action") == "/tasks/t1/delete"
    delete_button = delete.find("button", {"class": "task-delete-submit"})
    assert delete_button is not None


def test_move_select_marks_current_column(add: Any, page: Any) -> None:
    add("Write docs", column="doing")
    board = page()
    select = board.find("select", {"id": "move-t1"})
    assert select is not None
    selected = select.find("option", {"selected": ""})
    assert selected is not None
    assert selected.attrs.get("value") == "doing"
