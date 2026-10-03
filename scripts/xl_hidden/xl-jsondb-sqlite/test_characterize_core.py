"""Hidden characterization: the core table API.

These pin the CURRENT observable behavior — they pass on the JSON
engine and must keep passing on any refactor: ids, ordering, copies,
error types, return shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from flatvault import Database, TableError, where

UTC = UTC


def test_insert_assigns_sequential_ids(tmp_path):
    table = Database(tmp_path).table("people")
    assert [table.insert({"name": "a"}), table.insert({"name": "b"})] == [1, 2]


def test_insert_rejects_a_row_with_id(tmp_path):
    table = Database(tmp_path).table("people")
    with pytest.raises(TableError):
        table.insert({"id": 5, "name": "a"})


def test_insert_rejects_a_non_dict(tmp_path):
    table = Database(tmp_path).table("people")
    with pytest.raises(TableError):
        table.insert(["not", "a", "dict"])


def test_get_returns_a_copy(tmp_path):
    table = Database(tmp_path).table("people")
    row_id = table.insert({"name": "ada"})
    row = table.get(row_id)
    assert row is not None
    row["name"] = "mutated"
    assert table.get(row_id)["name"] == "ada"


def test_get_missing_returns_none(tmp_path):
    assert Database(tmp_path).table("people").get(99) is None


def test_find_keeps_insertion_order(tmp_path):
    table = Database(tmp_path).table("people")
    for name in ("c", "a", "b"):
        table.insert({"name": name})
    assert [row["name"] for row in table.find()] == ["c", "a", "b"]


def test_find_with_a_query(tmp_path):
    table = Database(tmp_path).table("people")
    table.insert({"name": "ada", "age": 36})
    table.insert({"name": "bob", "age": 9})
    assert [row["name"] for row in table.find(where("age") > 18)] == ["ada"]


def test_update_merges_and_returns_the_row(tmp_path):
    table = Database(tmp_path).table("people")
    row_id = table.insert({"name": "ada", "age": 36})
    updated = table.update(row_id, {"age": 37})
    assert updated is not None
    assert updated["age"] == 37
    assert updated["name"] == "ada"
    assert table.get(row_id)["age"] == 37


def test_update_missing_returns_none(tmp_path):
    assert Database(tmp_path).table("people").update(99, {"age": 1}) is None


def test_update_refuses_the_id(tmp_path):
    table = Database(tmp_path).table("people")
    row_id = table.insert({"name": "ada"})
    with pytest.raises(TableError):
        table.update(row_id, {"id": 42})


def test_delete_returns_true_then_false(tmp_path):
    table = Database(tmp_path).table("people")
    row_id = table.insert({"name": "ada"})
    assert table.delete(row_id) is True
    assert table.delete(row_id) is False
    assert table.get(row_id) is None


def test_ids_are_never_reused_after_delete(tmp_path):
    table = Database(tmp_path).table("people")
    table.insert({"name": "a"})
    table.insert({"name": "b"})
    table.insert({"name": "c"})
    assert table.delete(3) is True
    assert table.insert({"name": "d"}) == 4


def test_all_returns_copies(tmp_path):
    table = Database(tmp_path).table("people")
    table.insert({"name": "ada"})
    rows = table.all()
    rows[0]["name"] = "mutated"
    assert table.all()[0]["name"] == "ada"


def test_two_tables_are_isolated(tmp_path):
    db = Database(tmp_path)
    people, places = db.table("people"), db.table("places")
    people.insert({"name": "ada"})
    places.insert({"city": "Kyoto"})
    assert len(people.all()) == 1
    assert len(places.all()) == 1
    assert people.all()[0].get("city") is None


def test_drop_table_removes_then_errors(tmp_path):
    db = Database(tmp_path)
    db.table("people").insert({"name": "ada"})
    db.drop_table("people")
    with pytest.raises(TableError):
        db.drop_table("people")
    assert db.table("people").all() == []


def test_save_and_reload_roundtrip(tmp_path):
    db = Database(tmp_path)
    db.table("people").insert({"name": "ada", "created": datetime(2026, 1, 5, tzinfo=UTC)})
    reloaded = Database(tmp_path).table("people")
    rows = reloaded.find()
    assert len(rows) == 1
    assert rows[0]["name"] == "ada"
    assert rows[0]["created"] == datetime(2026, 1, 5, tzinfo=UTC)


def test_len_tracks_rows(tmp_path):
    table = Database(tmp_path).table("people")
    assert len(table) == 0
    table.insert({"name": "a"})
    assert len(table) == 1
