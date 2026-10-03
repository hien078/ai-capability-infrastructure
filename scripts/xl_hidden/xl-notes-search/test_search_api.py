"""Hidden acceptance: the HTTP API — the new search endpoint and the
pinned old surface.

``GET /notes/search`` returns the page envelope; every pre-existing
endpoint keeps its exact response shape.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime

import pytest
from jotbase.api import make_server
from jotbase.service import NoteService
from jotbase.storage import NoteStore

UTC = UTC
NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def api(tmp_path):
    service = NoteService(NoteStore(tmp_path / "data"))
    service.add("Deploy runbook", body="how we ship", tags=["ops"])
    service.add("Garden notes", body="basil and tomatoes", tags=["garden"])
    server, port = make_server(service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service, f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url) as resp:  # noqa: S310 - test fixture
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(  # noqa: S310 - test fixture
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _delete(url: str) -> tuple[int, dict]:
    request = urllib.request.Request(url, method="DELETE")  # noqa: S310
    try:
        with urllib.request.urlopen(request) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# ------------------------------------------------------------------- search


def test_search_endpoint_returns_the_envelope(api):
    _, base = api
    status, payload = _get(f"{base}/notes/search?q=deploy")
    assert status == 200
    assert set(payload) == {
        "query",
        "page",
        "page_size",
        "total",
        "total_pages",
        "items",
    }
    assert payload["query"] == "deploy"
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "Deploy runbook"


def test_search_endpoint_ranks_title_first(api):
    _, base = api
    service, _ = api
    service.add("zzz body only", body="deploy")
    status, payload = _get(f"{base}/notes/search?q=deploy")
    assert status == 200
    titles = [item["title"] for item in payload["items"]]
    assert titles[0] == "Deploy runbook"


def test_search_endpoint_requires_q(api):
    _, base = api
    status, payload = _get(f"{base}/notes/search")
    assert status == 400
    assert payload["error"] == "invalid_request"
    status, payload = _get(f"{base}/notes/search?q=")
    assert status == 400


def test_search_endpoint_rejects_a_bad_page(api):
    _, base = api
    status, payload = _get(f"{base}/notes/search?q=deploy&page=0")
    assert status == 400
    status, payload = _get(f"{base}/notes/search?q=deploy&page=abc")
    assert status == 400
    status, payload = _get(f"{base}/notes/search?q=deploy&page_size=999")
    assert status == 400


def test_search_endpoint_paginates(api):
    service, base = api
    for i in range(4):
        service.add(f"extra deploy {i}", body="filler")
    status, payload = _get(f"{base}/notes/search?q=deploy&page=2&page_size=2")
    assert status == 200
    assert payload["page"] == 2
    assert payload["page_size"] == 2
    assert payload["total"] == 5
    assert payload["total_pages"] == 3
    assert len(payload["items"]) == 2


# ------------------------------------------------------- the pinned old API


def test_get_notes_shape_is_unchanged(api):
    _, base = api
    status, payload = _get(f"{base}/notes")
    assert status == 200
    assert set(payload) == {"notes", "count"}
    assert payload["count"] == len(payload["notes"]) == 2
    note = payload["notes"][0]
    assert set(note) == {"id", "title", "body", "tags", "created_at", "updated_at"}


def test_get_notes_tag_and_limit_params_still_work(api):
    _, base = api
    status, payload = _get(f"{base}/notes?tag=ops")
    assert status == 200
    assert payload["count"] == 1
    status, payload = _get(f"{base}/notes?limit=1")
    assert status == 200
    assert payload["count"] == 1


def test_get_note_and_404_shapes(api):
    service, base = api
    note_id = service.store.ids()[0]
    status, payload = _get(f"{base}/notes/{note_id}")
    assert status == 200
    assert payload["id"] == note_id
    status, payload = _get(f"{base}/notes/note_missing")
    assert status == 404
    assert payload == {"error": "note_not_found"}


def test_post_and_delete_shapes(api):
    _, base = api
    status, note = _post(f"{base}/notes", {"title": "New", "body": "b", "tags": ["x"]})
    assert status == 201
    assert note["title"] == "New"
    status, payload = _delete(f"{base}/notes/{note['id']}")
    assert status == 200
    assert payload == {"deleted": True}
    status, payload = _delete(f"{base}/notes/{note['id']}")
    assert status == 404


def test_post_bad_note_is_a_400(api):
    _, base = api
    status, payload = _post(f"{base}/notes", {"title": "", "body": "b"})
    assert status == 400
    assert payload["error"] == "invalid_note"
