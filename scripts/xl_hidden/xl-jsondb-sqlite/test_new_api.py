"""Hidden acceptance: the new API surface — count, delete_where, and
crash-safe writes.

``count(where=None)`` and ``delete_where(where)`` are new; a failing
save must never corrupt the data dir.
"""

from __future__ import annotations

import json

from flatvault import Database, where


def seeded(tmp_path):
    db = Database(tmp_path)
    table = db.table("posts")
    table.insert({"slug": "a", "published": True})
    table.insert({"slug": "b", "published": False})
    table.insert({"slug": "c", "published": True})
    return db, table


def test_count_on_an_empty_table(tmp_path):
    assert Database(tmp_path).table("nothing").count() == 0


def test_count_counts_every_row(tmp_path):
    _, table = seeded(tmp_path)
    assert table.count() == 3


def test_count_with_a_query(tmp_path):
    _, table = seeded(tmp_path)
    assert table.count(where("published") == True) == 2  # noqa: E712
    assert table.count(where("published") == False) == 1  # noqa: E712


def test_count_returns_an_int(tmp_path):
    _, table = seeded(tmp_path)
    assert isinstance(table.count(), int)
    assert isinstance(table.count(where("slug") == "a"), int)


def test_delete_where_returns_the_count(tmp_path):
    _, table = seeded(tmp_path)
    assert table.delete_where(where("published") == True) == 2  # noqa: E712
    assert [row["slug"] for row in table.find()] == ["b"]


def test_delete_where_with_no_match(tmp_path):
    _, table = seeded(tmp_path)
    assert table.delete_where(where("slug") == "zzz") == 0
    assert table.count() == 3


def test_delete_where_keeps_the_rest_in_order(tmp_path):
    db = Database(tmp_path)
    table = db.table("posts")
    for slug in ("a", "b", "c", "d"):
        table.insert({"slug": slug, "published": slug in ("a", "c")})
    assert table.delete_where(where("published") == True) == 2  # noqa: E712
    assert [row["slug"] for row in table.find()] == ["b", "d"]


def test_a_failing_save_never_corrupts_the_store(tmp_path, monkeypatch):
    """A crash mid-save must leave the data dir loadable — never truncated."""
    db, table = seeded(tmp_path)
    before = {row["slug"]: row for row in table.find()}

    def boom(*args, **kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(json, "dump", boom)
    try:
        db.save()  # may raise — that IS the scenario
    except OSError:
        pass
    monkeypatch.undo()

    reloaded = Database(tmp_path).table("posts")
    after = {row["slug"]: row for row in reloaded.find()}
    assert after == before
