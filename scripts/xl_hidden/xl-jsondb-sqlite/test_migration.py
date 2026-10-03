"""Hidden acceptance: legacy data dirs keep working.

A v1 directory (JSON files, no sqlite) opens with zero ceremony: the
data is there, ids and the next id are preserved, datetimes decode, and
the original JSON files are left in place untouched.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from flatvault import Database

UTC = UTC


def write_v1_dir(root: Path) -> None:
    """A v1 data dir exactly as the JSON engine wrote it (datetime markers)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "posts.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "id": 1,
                        "slug": "hello",
                        "title": "Hello",
                        "body": "First post",
                        "published": False,
                        "tags": ["intro"],
                        "created_at": {"$datetime": "2026-01-05T09:00:00+00:00"},
                    },
                    {
                        "id": 2,
                        "slug": "second",
                        "title": "Second",
                        "body": "More",
                        "published": True,
                        "tags": [],
                        "created_at": {"$datetime": "2026-01-06T09:00:00+00:00"},
                    },
                ],
                "next_id": 3,
            }
        ),
        encoding="utf-8",
    )
    (root / "comments.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "id": 1,
                        "post_id": 1,
                        "author": "Ada",
                        "body": "First!",
                        "created_at": {"$datetime": "2026-01-05T10:00:00+00:00"},
                    }
                ],
                "next_id": 2,
            }
        ),
        encoding="utf-8",
    )


def test_v1_dir_opens_and_reads(tmp_path):
    write_v1_dir(tmp_path)
    db = Database(tmp_path)
    posts = db.table("posts").find()
    assert [row["id"] for row in posts] == [1, 2]
    assert posts[0]["slug"] == "hello"
    assert len(db.table("comments").find()) == 1


def test_v1_ids_and_next_id_are_preserved(tmp_path):
    write_v1_dir(tmp_path)
    db = Database(tmp_path)
    assert db.table("posts").insert({"slug": "third", "title": "Third"}) == 3
    assert db.table("comments").insert({"post_id": 1, "author": "Bob"}) == 2


def test_v1_datetimes_decode(tmp_path):
    write_v1_dir(tmp_path)
    row = Database(tmp_path).table("posts").get(1)
    assert row is not None
    assert row["created_at"] == datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def test_v1_files_are_left_in_place(tmp_path):
    write_v1_dir(tmp_path)
    before = {path.name: path.read_bytes() for path in sorted(tmp_path.glob("*.json"))}
    Database(tmp_path)  # open (and migrate, if the engine does)
    Database(tmp_path)  # and again
    after = {path.name: path.read_bytes() for path in sorted(tmp_path.glob("*.json"))}
    assert before == after
    assert set(before) == {"posts.json", "comments.json"}


def test_second_open_reads_the_same_data(tmp_path):
    write_v1_dir(tmp_path)
    first = Database(tmp_path).table("posts").find()
    second = Database(tmp_path).table("posts").find()
    assert [row["slug"] for row in first] == [row["slug"] for row in second]


def test_a_fresh_dir_starts_empty(tmp_path):
    db = Database(tmp_path)
    assert db.table("anything").find() == []
    assert db.table("anything").insert({"x": 1}) == 1
