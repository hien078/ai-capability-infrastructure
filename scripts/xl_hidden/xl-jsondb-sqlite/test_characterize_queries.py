"""Hidden characterization: the where() DSL.

Missing fields never match, type-mismatched comparisons are a
non-match, ``contains`` is a substring test on strings — the DSL is
pure and pinned.
"""

from __future__ import annotations

import pytest
from flatvault.errors import QueryError
from flatvault.queries import And, Or, match, where

DOC = {"name": "ada", "age": 36, "city": "Kyoto", "tags": ["a", "b"], "note": "the age is 36"}


def test_eq_and_ne():
    assert match(where("age") == 36, DOC)
    assert not match(where("age") == 35, DOC)
    assert match(where("age") != 35, DOC)
    assert not match(where("age") != 36, DOC)


def test_ordering_operators():
    assert match(where("age") > 18, DOC)
    assert not match(where("age") > 36, DOC)
    assert match(where("age") >= 36, DOC)
    assert match(where("age") < 37, DOC)
    assert match(where("age") <= 36, DOC)
    assert not match(where("age") < 36, DOC)


def test_missing_field_never_matches():
    assert not match(where("nope") == 1, DOC)
    assert not match(where("nope") != 1, DOC)
    assert not match(where("nope") > 1, DOC)
    assert not match(where("nope").in_([1, 2]), DOC)
    assert not match(where("nope").contains("x"), DOC)


def test_type_mismatch_is_a_non_match():
    assert not match(where("age") > "z", DOC)
    assert not match(where("name") < 99, DOC)


def test_in_():
    assert match(where("age").in_([18, 36]), DOC)
    assert not match(where("age").in_([18]), DOC)


def test_contains_is_substring_on_strings():
    assert match(where("name").contains("da"), DOC)
    assert not match(where("name").contains("zz"), DOC)
    assert not match(where("age").contains("3"), DOC)  # not a string field


def test_and_or():
    assert match(And(where("age") > 18, where("city") == "Kyoto"), DOC)
    assert not match(And(where("age") > 18, where("city") == "Osaka"), DOC)
    assert match(Or(where("city") == "Osaka", where("age") > 18), DOC)
    assert not match(Or(where("city") == "Osaka", where("age") < 18), DOC)


def test_match_rejects_a_non_query():
    with pytest.raises(QueryError):
        match("not a query", DOC)


def test_where_rejects_an_empty_field():
    with pytest.raises(QueryError):
        where("")


def test_queries_compose_through_tables(tmp_path):
    from flatvault import Database

    table = Database(tmp_path).table("people")
    table.insert({"name": "ada", "age": 36})
    table.insert({"name": "bob", "age": 9})
    table.insert({"name": "cay", "age": 64})
    query = And(where("age") > 18, where("name").contains("a"))
    assert [row["name"] for row in table.find(query)] == ["ada", "cay"]
