"""Hidden acceptance: transactions — the new requirement.

``Database.transaction()`` is a context manager: atomic across tables,
rollback on any exception, commit on success. (v1: nesting raises —
the change pack will replace that with savepoints.)
"""

from __future__ import annotations

import pytest
from flatvault import Database, where


def test_transaction_commits_on_success(tmp_path):
    db = Database(tmp_path)
    with db.transaction():
        db.table("posts").insert({"slug": "a"})
        db.table("comments").insert({"post_id": 1, "author": "Ada"})
    assert db.table("posts").count() == 1
    assert db.table("comments").count() == 1


def test_transaction_rolls_back_on_exception(tmp_path):
    db = Database(tmp_path)
    with pytest.raises(RuntimeError, match="boom"):
        with db.transaction():
            db.table("posts").insert({"slug": "a"})
            raise RuntimeError("boom")
    assert db.table("posts").count() == 0


def test_transaction_is_atomic_across_tables(tmp_path):
    db = Database(tmp_path)
    db.table("posts").insert({"slug": "keep"})
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.table("posts").insert({"slug": "transient"})
            db.table("comments").insert({"post_id": 1, "author": "Ada"})
            raise RuntimeError("boom")
    assert [row["slug"] for row in db.table("posts").find()] == ["keep"]
    assert db.table("comments").count() == 0


def test_rollback_leaves_a_usable_store(tmp_path):
    db = Database(tmp_path)
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.table("posts").insert({"slug": "a"})
            raise RuntimeError("boom")
    db.table("posts").insert({"slug": "b"})
    assert [row["slug"] for row in db.table("posts").find()] == ["b"]


def test_nested_transaction_raises(tmp_path):
    """v1: nesting is a programming error — RuntimeError, never data loss."""
    db = Database(tmp_path)
    with pytest.raises(RuntimeError, match="nested"):
        with db.transaction():
            db.table("posts").insert({"slug": "a"})
            with db.transaction():
                db.table("posts").insert({"slug": "b"})
    # the outer transaction was rolled back by the exception propagation
    assert db.table("posts").count() == 0


def test_count_sees_uncommitted_rows(tmp_path):
    db = Database(tmp_path)
    with db.transaction():
        db.table("posts").insert({"slug": "a"})
        db.table("posts").insert({"slug": "b"})
        assert db.table("posts").count() == 2
    assert db.table("posts").count() == 2


def test_transaction_with_delete_where(tmp_path):
    db = Database(tmp_path)
    db.table("posts").insert({"slug": "a", "published": True})
    db.table("posts").insert({"slug": "b", "published": False})
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.table("posts").delete_where(where("published") == True)  # noqa: E712
            raise RuntimeError("boom")
    assert db.table("posts").count() == 2
