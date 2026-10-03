"""Change acceptance: nested transactions (savepoints) + batch_insert.

An inner rollback undoes only the inner scope; an inner commit is still
undone by an outer rollback; ``batch_insert`` inserts many rows in one
save and returns their ids in order.
"""

from __future__ import annotations

import pytest
from flatvault import Database, TableError, where


def test_inner_rollback_undoes_only_the_inner_scope(tmp_path):
    db = Database(tmp_path)
    with db.transaction():
        db.table("posts").insert({"slug": "outer"})
        # match= is load-bearing: v1 raised "nested transaction" AT ENTRY,
        # which a bare pytest.raises(RuntimeError) would swallow — the test
        # must discriminate the inner scope's OWN raise from v1's refusal.
        with pytest.raises(RuntimeError, match="inner boom"):
            with db.transaction():
                db.table("posts").insert({"slug": "inner"})
                raise RuntimeError("inner boom")
        # the inner insert is gone, the outer one survives
    assert [row["slug"] for row in db.table("posts").find()] == ["outer"]


def test_inner_commit_is_undone_by_an_outer_rollback(tmp_path):
    db = Database(tmp_path)
    # match= is load-bearing (see test_inner_rollback above): v1's entry raise
    # must not satisfy this raises — only the outer scope's own raise does.
    with pytest.raises(RuntimeError, match="outer boom"):
        with db.transaction():
            db.table("posts").insert({"slug": "outer"})
            with db.transaction():
                db.table("posts").insert({"slug": "inner"})
            raise RuntimeError("outer boom")
    assert db.table("posts").count() == 0


def test_three_levels_deep(tmp_path):
    db = Database(tmp_path)
    with db.transaction():
        db.table("posts").insert({"slug": "one"})
        with db.transaction():
            db.table("posts").insert({"slug": "two"})
            with pytest.raises(RuntimeError, match="deepest boom"):
                with db.transaction():
                    db.table("posts").insert({"slug": "three"})
                    raise RuntimeError("deepest boom")
        db.table("posts").insert({"slug": "four"})
    assert [row["slug"] for row in db.table("posts").find()] == ["one", "two", "four"]


def test_batch_insert_returns_ids_in_order(tmp_path):
    table = Database(tmp_path).table("posts")
    ids = table.batch_insert([{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])
    assert ids == [1, 2, 3]
    assert [row["slug"] for row in table.find()] == ["a", "b", "c"]


def test_batch_insert_is_atomic_in_a_transaction(tmp_path):
    db = Database(tmp_path)
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.table("posts").batch_insert([{"slug": "a"}, {"slug": "b"}])
            raise RuntimeError("boom")
    assert db.table("posts").count() == 0


def test_batch_insert_validates_before_inserting(tmp_path):
    table = Database(tmp_path).table("posts")
    with pytest.raises(TableError):
        table.batch_insert([{"slug": "a"}, {"id": 99, "slug": "b"}])
    assert table.count() == 0


def test_batch_insert_empty_list(tmp_path):
    table = Database(tmp_path).table("posts")
    assert table.batch_insert([]) == []
    assert table.count() == 0


def test_batch_insert_composes_with_delete_where(tmp_path):
    table = Database(tmp_path).table("posts")
    table.batch_insert([{"slug": "a", "published": True}, {"slug": "b", "published": False}])
    assert table.delete_where(where("published") == True) == 1  # noqa: E712
    assert [row["slug"] for row in table.find()] == ["b"]
