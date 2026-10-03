"""Change acceptance: tag filters.

A ``tag:x`` term filters to notes tagged ``x`` (combined with plain
tokens; a tag-only query returns the tagged notes newest-first); the
API gains ``tags=`` and the CLI gains ``--tag``. A ``tag:`` term no
longer matches body text.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime

import pytest
from jotbase.api import make_server
from jotbase.cli import main
from jotbase.service import NoteService
from jotbase.storage import NoteStore

UTC = UTC


@pytest.fixture
def clock(monkeypatch):
    holder = {"now": datetime(2026, 1, 1, 12, 0, tzinfo=UTC)}

    def fake_now():
        return holder["now"]

    monkeypatch.setattr("jotbase.service._utcnow", fake_now)
    return holder


def test_tag_filter_matches_tagged_notes(tmp_path, clock):
    service = NoteService(NoteStore(tmp_path))
    clock["now"] = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    tagged = service.add("Quiet title", body="nothing relevant here", tags=["deploy"])
    clock["now"] = datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    service.add("Loud deploy", body="deploy deploy deploy", tags=["other"])
    result = service.search("tag:deploy")
    assert result.total == 1
    assert result.items[0].id == tagged.id


def test_tag_filter_no_longer_matches_body_text(tmp_path):
    """The body text `tag:foo` is NOT a tag — the inverse of the v1 rule."""
    service = NoteService(NoteStore(tmp_path))
    service.add("Conventions", body="the tag:foo convention", tags=["deploy"])
    result = service.search("tag:foo")
    assert result.total == 0
    assert result.items == []


def test_tag_filter_combines_with_plain_tokens(tmp_path, clock):
    service = NoteService(NoteStore(tmp_path))
    clock["now"] = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    hit = service.add("Runbook", body="deploy procedure", tags=["ops"])
    clock["now"] = datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    service.add("Other", body="deploy procedure", tags=["garden"])
    result = service.search("tag:ops deploy")
    assert result.total == 1
    assert result.items[0].id == hit.id


def test_tag_only_query_is_newest_first(tmp_path, clock):
    service = NoteService(NoteStore(tmp_path))
    clock["now"] = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    older = service.add("Older", body="words", tags=["ops"])
    clock["now"] = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    newer = service.add("Newer", body="other words", tags=["ops"])
    result = service.search("tag:ops")
    assert [n.id for n in result.items] == [newer.id, older.id]


def test_tag_filter_is_slugified_and_case_insensitive(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    note = service.add("Title", body="the guide", tags=["ops", "ops-guide"])
    assert service.search("tag:OPS-GUIDE").items[0].id == note.id  # slugified, case-insensitive
    assert service.search("tag:Ops Guide").items[0].id == note.id  # "Guide" stays a plain token
    assert service.search("tag:ops").total == 1


def test_explicit_tags_argument_filters(tmp_path):
    service = NoteService(NoteStore(tmp_path))
    hit = service.add("Deploy", body="procedure", tags=["ops"])
    service.add("Deploy", body="procedure", tags=["garden"])
    result = service.search("deploy", tags=["ops"])
    assert result.total == 1
    assert result.items[0].id == hit.id


def test_api_tags_parameter_filters(tmp_path):
    service = NoteService(NoteStore(tmp_path / "data"))
    hit = service.add("Deploy", body="procedure", tags=["ops"])
    service.add("Deploy", body="procedure", tags=["garden"])
    server, port = make_server(service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{port}/notes/search?q=deploy&tags=ops"
        with urllib.request.urlopen(url) as resp:  # noqa: S310
            payload = json.loads(resp.read())
        assert payload["total"] == 1
        assert payload["items"][0]["id"] == hit.id
    finally:
        server.shutdown()
        server.server_close()


def test_cli_tag_flag_filters(tmp_path, capsys):
    root = tmp_path / "data"
    service = NoteService(NoteStore(root))
    hit = service.add("Deploy", body="procedure", tags=["ops"])
    service.add("Deploy", body="procedure", tags=["garden"])
    rc = main(["--root", str(root), "search", "deploy", "--tag", "ops"])
    assert rc == 0
    out = capsys.readouterr().out
    assert hit.id in out
    assert "1 of 1 results" in out
