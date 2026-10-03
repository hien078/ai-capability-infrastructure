"""Hidden acceptance: the app layer keeps working, unchanged.

The blog app (app/) is the refactor's compat proof: it must run
end-to-end with zero edits on any engine behind the same facade.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.cli import main
from app.service import BlogService, PostNotFound
from flatvault import Database


def test_create_post_assigns_slug_and_id(tmp_path):
    service = BlogService(Database(tmp_path))
    post = service.create_post("Hello, World!", body="First")
    assert post.slug == "hello-world"
    assert post.id == 1
    assert post.published is False


def test_slug_uniquification(tmp_path):
    service = BlogService(Database(tmp_path))
    first = service.create_post("Hello")
    second = service.create_post("Hello")
    assert first.slug == "hello"
    assert second.slug == "hello-2"


def test_publish_and_list(tmp_path):
    service = BlogService(Database(tmp_path))
    service.create_post("Draft")
    service.create_post("Published")
    service.publish("published")
    assert [p.slug for p in service.list_posts(published=True)] == ["published"]
    assert len(service.list_posts(published=None)) == 2


def test_publish_unknown_slug_raises(tmp_path):
    service = BlogService(Database(tmp_path))
    with pytest.raises(PostNotFound):
        service.publish("nope")


def test_add_comment_and_get_post(tmp_path):
    service = BlogService(Database(tmp_path))
    service.create_post("Hello")
    comment = service.add_comment("hello", "Ada", "First!")
    assert comment.id == 1
    payload = service.get_post("hello")
    assert payload["post"].slug == "hello"
    assert [c.author for c in payload["comments"]] == ["Ada"]


def test_comment_ordering(tmp_path):
    service = BlogService(Database(tmp_path))
    service.create_post("Hello")
    for author in ("Ada", "Bob", "Cy"):
        service.add_comment("hello", author, "hi")
    assert [c.author for c in service.get_post("hello")["comments"]] == ["Ada", "Bob", "Cy"]


def test_stats(tmp_path):
    service = BlogService(Database(tmp_path))
    service.create_post("One", tags=["intro"])
    service.create_post("Two", tags=["intro", "misc"])
    service.publish("two")
    service.add_comment("one", "Ada", "hi")
    stats = service.stats()
    assert stats["posts"] == 2
    assert stats["published"] == 1
    assert stats["comments"] == 1
    assert stats["tags"] == {"intro": 2, "misc": 1}


def test_cli_end_to_end(tmp_path, capsys):
    root = tmp_path / "data"
    assert main(["--root", str(root), "post", "add", "--title", "Hello", "--tag", "intro"]) == 0
    assert "created hello" in capsys.readouterr().out
    assert main(["--root", str(root), "post", "publish", "hello"]) == 0
    assert (
        main(["--root", str(root), "comment", "add", "hello", "--author", "Ada", "--body", "Hi"])
        == 0
    )
    assert main(["--root", str(root), "post", "list"]) == 0
    assert "hello" in capsys.readouterr().out
    assert main(["--root", str(root), "stats"]) == 0
    out = capsys.readouterr().out
    assert "posts: 1 (1 published)" in out
    assert "comments: 1" in out


def test_posts_survive_a_reopen(tmp_path):
    service = BlogService(Database(tmp_path))
    service.create_post("Hello", body="First")
    reopened = BlogService(Database(tmp_path))
    post = reopened.get_post("hello")["post"]
    assert post.body == "First"
    assert post.created_at.tzinfo is not None


def test_by_tag_and_recent(tmp_path):
    service = BlogService(Database(tmp_path))
    service.create_post("One", tags=["intro"])
    service.create_post("Two", tags=["misc"])
    service.create_post("Three", tags=["intro"])
    assert [p.slug for p in service.posts.by_tag("intro")] == ["three", "one"]
    assert [p.slug for p in service.posts.recent(2)] == ["three", "two"]


def test_archive_groups_published_by_month(tmp_path):
    service = BlogService(Database(tmp_path))
    service.create_post("January")
    service.create_post("February")
    service.publish("february")
    archive = service.archive()
    now = datetime.now(UTC)
    month_key = f"{now.year:04d}-{now.month:02d}"
    assert list(archive) == [month_key]  # only published posts, newest month first
    assert archive[month_key] == ["february"]


def test_export_carries_everything(tmp_path):
    service = BlogService(Database(tmp_path))
    service.create_post("Hello", tags=["intro"])
    service.add_comment("hello", "Ada", "First!")
    dumped = service.export()
    assert len(dumped["posts"]) == 1
    assert dumped["posts"][0]["slug"] == "hello"
    assert dumped["comments"][0]["author"] == "Ada"
    assert isinstance(dumped["posts"][0]["created_at"], datetime)


def test_api_end_to_end(tmp_path):
    import json
    import threading
    import urllib.error
    import urllib.request

    from app.api import make_server

    service = BlogService(Database(tmp_path))
    server, port = make_server(service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        request = urllib.request.Request(
            f"{base}/posts",
            data=json.dumps({"title": "Hello", "tags": ["intro"]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as resp:
            assert resp.status == 201
            created = json.loads(resp.read())
        assert created["slug"] == "hello"
        assert isinstance(created["created_at"], str)  # ISO on the wire

        request = urllib.request.Request(
            f"{base}/posts/hello/comments",
            data=json.dumps({"author": "Ada", "body": "First!"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as resp:
            assert resp.status == 201

        with urllib.request.urlopen(f"{base}/posts") as resp:
            listing = json.loads(resp.read())
        assert listing["count"] == 1
        with urllib.request.urlopen(f"{base}/posts/hello") as resp:
            payload = json.loads(resp.read())
        assert payload["post"]["slug"] == "hello"
        assert [c["author"] for c in payload["comments"]] == ["Ada"]

        try:
            urllib.request.urlopen(f"{base}/posts/missing")
            raise AssertionError("404 expected")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()
