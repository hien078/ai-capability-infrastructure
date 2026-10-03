"""CHANGE v2 — or: OR between terms, LOWER precedence than the implicit AND."""

from __future__ import annotations

import json
from typing import Any


def _seed(recdb: Any, *adds: tuple[str, ...]) -> None:
    for add in adds:
        assert recdb("add", *add).returncode == 0


def _titles(proc: Any) -> list[str]:
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line)["title"] for line in proc.stdout.splitlines()]


def test_change_or_basic(recdb: Any) -> None:
    _seed(recdb, ("title=a",), ("title=b",), ("title=c",))
    assert _titles(recdb("query", "title=a or title=c")) == ["a", "c"]


def test_change_or_no_match_exit_0(recdb: Any) -> None:
    _seed(recdb, ("title=a",))
    proc = recdb("query", "title=zz or title=yy")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_change_and_binds_tighter_than_or(recdb: Any) -> None:
    _seed(
        recdb,
        ("title=both", "a=1", "b=2"),
        ("title=one", "a=1", "b=9"),
        ("title=third", "c=3"),
    )
    assert _titles(recdb("query", "a=1 b=2 or c=3")) == ["both", "third"]


def test_change_or_three_groups(recdb: Any) -> None:
    _seed(recdb, ("title=a",), ("title=b",), ("title=c",), ("title=d",))
    assert _titles(recdb("query", "title=b or title=d or title=a")) == ["a", "b", "d"]


def test_change_or_missing_field(recdb: Any) -> None:
    _seed(recdb, ("title=a",), ("title=b",))
    assert _titles(recdb("query", "status=open or title=a")) == ["a"]


def test_change_or_in_where_flag(recdb: Any) -> None:
    _seed(recdb, ("title=a",), ("title=b",))
    assert _titles(recdb("list", "--where", "title=a or title=b")) == ["a", "b"]


def test_change_dangling_or_exit_2(recdb: Any) -> None:
    _seed(recdb, ("title=a",))
    proc = recdb("query", "title=a or")
    assert proc.returncode == 2
