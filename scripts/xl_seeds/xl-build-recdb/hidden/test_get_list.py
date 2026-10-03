"""get/list: lookup, ordering, sort, limit/offset (README §4)."""

from __future__ import annotations

import json
from typing import Any


def _add_many(recdb: Any, *adds: tuple[str, ...]) -> None:
    """Each tuple is ONE add command's key=value pairs."""
    for add in adds:
        assert recdb("add", *add).returncode == 0


def _titles(proc: Any) -> list[str]:
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line)["title"] for line in proc.stdout.splitlines()]


def test_get_prints_record(recdb: Any) -> None:
    rid = json.loads(recdb("add", "title=x", "points=2").stdout)
    proc = recdb("get", str(rid))
    assert proc.returncode == 0, proc.stderr
    record = json.loads(proc.stdout)
    assert record["title"] == "x"
    assert record["points"] == 2


def test_get_not_found_exit_3(recdb: Any) -> None:
    proc = recdb("get", "99")
    assert proc.returncode == 3
    assert "not found" in proc.stderr


def test_get_id_argument_typing(recdb: Any) -> None:
    assert recdb("add", "id=5", "a=1").returncode == 0
    assert recdb("get", "5").returncode == 0
    assert recdb("add", 'id="5"', "b=2").returncode == 0
    proc = recdb("get", '"5"')
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["b"] == 2


def test_list_empty(recdb: Any) -> None:
    proc = recdb("list")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_list_store_order(recdb: Any) -> None:
    _add_many(recdb, ("title=first",), ("title=second",), ("title=third",))
    assert _titles(recdb("list")) == ["first", "second", "third"]


def test_list_window(recdb: Any) -> None:
    _add_many(
        recdb,
        ("title=a",),
        ("title=b",),
        ("title=c",),
        ("title=d",),
        ("title=e",),
    )
    assert _titles(recdb("list", "--limit", "2")) == ["a", "b"]
    assert _titles(recdb("list", "--offset", "3")) == ["d", "e"]
    assert _titles(recdb("list", "--offset", "1", "--limit", "2")) == ["b", "c"]
    assert recdb("list", "--limit", "0").stdout == ""


def test_list_sort_asc_and_desc(recdb: Any) -> None:
    _add_many(
        recdb,
        ("title=a", "points=3"),
        ("title=b", "points=1"),
        ("title=c", "points=2"),
    )
    assert _titles(recdb("list", "--sort", "points")) == ["b", "c", "a"]
    assert _titles(recdb("list", "--sort", "points:desc")) == ["a", "c", "b"]


def test_list_sort_missing_field_last_both_directions(recdb: Any) -> None:
    _add_many(recdb, ("title=a", "points=2"), ("title=b",), ("title=c", "points=1"))
    assert _titles(recdb("list", "--sort", "points")) == ["c", "a", "b"]
    assert _titles(recdb("list", "--sort", "points:desc")) == ["a", "c", "b"]


def test_list_sort_stable_on_ties(recdb: Any) -> None:
    _add_many(
        recdb,
        ("title=first", "points=1"),
        ("title=second", "points=1"),
        ("title=third", "points=1"),
    )
    assert _titles(recdb("list", "--sort", "points")) == ["first", "second", "third"]


def test_list_flag_errors_exit_2(recdb: Any) -> None:
    _add_many(recdb, ("title=a",))
    assert recdb("list", "--sort", "points:down").returncode == 2
    assert recdb("list", "--limit", "-1").returncode == 2


def test_list_where_flag(recdb: Any) -> None:
    _add_many(recdb, ("title=a", "points=1"), ("title=b", "points=5"))
    assert _titles(recdb("list", "--where", "points>=2")) == ["b"]
