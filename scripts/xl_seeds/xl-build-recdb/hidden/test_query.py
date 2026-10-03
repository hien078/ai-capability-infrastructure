"""query: operators, typing rules, AND, reserved words, usage errors (README §4)."""

from __future__ import annotations

import json
from typing import Any


def _seed(recdb: Any, *adds: tuple[str, ...]) -> None:
    for add in adds:
        assert recdb("add", *add).returncode == 0


def _titles(proc: Any) -> list[str]:
    return [json.loads(line)["title"] for line in proc.stdout.splitlines()]


def test_query_eq_and_ne(recdb: Any) -> None:
    _seed(recdb, ("title=alpha",), ("title=beta",))
    assert _titles(recdb("query", "title=alpha")) == ["alpha"]
    assert _titles(recdb("query", "title!=alpha")) == ["beta"]


def test_query_numeric_ordering(recdb: Any) -> None:
    _seed(
        recdb,
        ("title=low", "points=1"),
        ("title=mid", "points=5"),
        ("title=high", "points=9"),
    )
    assert _titles(recdb("query", "points>1")) == ["mid", "high"]
    assert _titles(recdb("query", "points>=5")) == ["mid", "high"]
    assert _titles(recdb("query", "points<5")) == ["low"]
    assert _titles(recdb("query", "points<=5")) == ["low", "mid"]


def test_query_string_ordering(recdb: Any) -> None:
    _seed(recdb, ("title=apple",), ("title=banana",), ("title=cherry",))
    assert _titles(recdb("query", "title>=banana")) == ["banana", "cherry"]
    assert _titles(recdb("query", "title<banana")) == ["apple"]


def test_query_substring(recdb: Any) -> None:
    _seed(recdb, ("title=hello world",))
    assert _titles(recdb("query", "title~=hello")) == ["hello world"]
    assert _titles(recdb("query", "title~=world")) == ["hello world"]
    assert _titles(recdb("query", "title~=Hello")) == []
    assert _titles(recdb("query", "title~=oval")) == []


def test_query_missing_field_false_for_every_op(recdb: Any) -> None:
    _seed(recdb, ("title=a",))
    assert _titles(recdb("query", "status=open")) == []
    assert _titles(recdb("query", "status!=open")) == []
    assert _titles(recdb("query", "status~=open")) == []
    assert _titles(recdb("query", "status>=open")) == []


def test_query_null_and_bool_literals(recdb: Any) -> None:
    _seed(recdb, ("title=a", "note=null", "done=true"), ("title=b",))
    assert _titles(recdb("query", "note=null")) == ["a"]
    assert _titles(recdb("query", "note=none")) == []
    assert _titles(recdb("query", "done=true")) == ["a"]
    assert _titles(recdb("query", "done=1")) == []


def test_query_incomparable_types_are_false(recdb: Any) -> None:
    _seed(recdb, ("title=a", "n=3", "note=null"))
    assert _titles(recdb("query", "n=three")) == []
    assert _titles(recdb("query", "n>abc")) == []
    assert _titles(recdb("query", "title=3")) == []
    assert _titles(recdb("query", "note!=x")) == []


def test_query_and_of_terms(recdb: Any) -> None:
    _seed(
        recdb,
        ("title=both", "status=open", "points=2"),
        ("title=one", "status=open", "points=9"),
        ("title=none", "status=closed", "points=2"),
    )
    assert _titles(recdb("query", "status=open points=2")) == ["both"]


def test_query_quoted_value_with_spaces(recdb: Any) -> None:
    _seed(recdb, ("title=two words",), ("title=other",))
    assert _titles(recdb("query", 'title="two words"')) == ["two words"]
    assert _titles(recdb("query", "title='two words'")) == ["two words"]


def test_query_no_match_exit_0(recdb: Any) -> None:
    _seed(recdb, ("title=a",))
    proc = recdb("query", "title=zzz")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_query_or_is_reserved_exit_2(recdb: Any) -> None:
    _seed(recdb, ("title=a",))
    proc = recdb("query", "title=a or title=b")
    assert proc.returncode == 2
    assert "reserved" in proc.stderr


def test_query_parens_reserved_exit_2(recdb: Any) -> None:
    _seed(recdb, ("title=a",))
    proc = recdb("query", "(title=a)")
    assert proc.returncode == 2
    assert "reserved" in proc.stderr


def test_query_usage_errors_exit_2(recdb: Any) -> None:
    _seed(recdb, ("title=a",))
    assert recdb("query", "title~3").returncode == 2  # unknown operator
    assert recdb("query", 'title="open').returncode == 2  # unbalanced quote
    assert recdb("query", "title=").returncode == 2  # missing value
    assert recdb("query", "").returncode == 2  # empty expression
