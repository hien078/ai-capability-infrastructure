"""Interactions: add/move/delete over real HTTP, rendered state (README §3)."""

from __future__ import annotations

from typing import Any


def test_add_task_shows_in_todo(add: Any, page: Any) -> None:
    add("Fix login bug")
    board = page()
    todo = board.find("section", {"id": "col-todo"})
    assert todo is not None
    card = todo.find("article", {"class": "task"})
    assert card is not None
    assert card.find("h3", {"class": "task-title"}).text_content() == "Fix login bug"  # type: ignore[union-attr]


def test_add_to_specific_column(add: Any, page: Any) -> None:
    add("Write API docs", column="doing")
    add("Ship v1", column="done")
    board = page()
    doing = board.find("section", {"id": "col-doing"})
    done = board.find("section", {"id": "col-done"})
    assert doing.find("article", {"id": "task-t1"}) is not None
    assert done.find("article", {"id": "task-t2"}) is not None
    assert board.find("section", {"id": "col-todo"}).find("p", {"class": "empty"}) is not None


def test_move_task(add: Any, client: Any, page: Any) -> None:
    tid = add("Fix login bug")
    result = client("POST", f"/tasks/{tid}/move", form={"column": "doing"})
    assert result.status == 303
    assert result.location == "/"
    board = page()
    moved = board.find("article", {"id": f"task-{tid}"})
    assert moved is not None
    assert moved.attrs.get("data-column") == "doing"
    doing = board.find("section", {"id": "col-doing"})
    assert doing.find("article", {"id": f"task-{tid}"}) is not None


def test_move_back_and_forth(add: Any, client: Any, page: Any) -> None:
    tid = add("Fix login bug")
    assert client("POST", f"/tasks/{tid}/move", form={"column": "done"}).status == 303
    assert client("POST", f"/tasks/{tid}/move", form={"column": "todo"}).status == 303
    board = page()
    assert board.find("article", {"id": f"task-{tid}"}).attrs.get("data-column") == "todo"  # type: ignore[union-attr]


def test_delete_task(add: Any, client: Any, page: Any) -> None:
    tid = add("Fix login bug")
    result = client("POST", f"/tasks/{tid}/delete")
    assert result.status == 303
    assert result.location == "/"
    board = page()
    assert board.find("article", {"id": f"task-{tid}"}) is None
    todo = board.find("section", {"id": "col-todo"})
    assert todo.find("p", {"class": "empty"}) is not None


def test_assignee_shown(add: Any, page: Any) -> None:
    add("Fix login bug", assignee="ana")
    card = page().find("article", {"id": "task-t1"})
    assert card.find("p", {"class": "task-assignee"}).text_content() == "ana"  # type: ignore[union-attr]


def test_unassigned_rendered(add: Any, page: Any) -> None:
    add("Fix login bug")
    card = page().find("article", {"id": "task-t1"})
    assert card.find("p", {"class": "task-assignee"}).text_content() == "Unassigned"  # type: ignore[union-attr]


def test_points_badge(add: Any, page: Any) -> None:
    add("Fix login bug", points="5")
    card = page().find("article", {"id": "task-t1"})
    badge = card.find("span", {"class": "points"})  # type: ignore[union-attr]
    assert badge.text_content() == "5"  # type: ignore[union-attr]


def test_ids_increment(add: Any, page: Any) -> None:
    add("first")
    add("second")
    board = page()
    assert board.find("article", {"id": "task-t1"}) is not None
    assert board.find("article", {"id": "task-t2"}) is not None


def test_cards_in_insertion_order(add: Any, page: Any) -> None:
    add("first")
    add("second")
    add("third")
    board = page()
    todo = board.find("section", {"id": "col-todo"})
    titles = [
        card.find("h3", {"class": "task-title"}).text_content()  # type: ignore[union-attr]
        for card in todo.find_all("article", {"class": "task"})
    ]
    assert titles == ["first", "second", "third"]
