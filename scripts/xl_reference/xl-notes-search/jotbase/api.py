"""The jotbase HTTP API (stdlib http.server, no framework).

Endpoints (JSON in, JSON out):

    GET    /notes            {"notes": [note], "count": n}   ?tag=&limit=
    GET    /notes/{id}       note | 404 {"error": "note_not_found"}
    POST   /notes            201 note | 400 {"error": "invalid_note", ...}
    DELETE /notes/{id}       {"deleted": true} | 404

The response shapes are pinned — clients depend on them.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from jotbase.errors import InvalidNote, NoteNotFound
from jotbase.model import Note
from jotbase.search import DEFAULT_PAGE_SIZE, SearchError, SearchQuery
from jotbase.service import NoteService
from jotbase.storage import NoteStore

MAX_BODY_BYTES = 1_000_000


def _note_dicts(notes: list[Note]) -> list[dict[str, Any]]:
    return [n.to_dict() for n in notes]


class _ApiHandler(BaseHTTPRequestHandler):
    """Routes one request; the service lives on ``self.server.service``."""

    server: ApiServer

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            raise InvalidNote("request body must be 1..1000000 bytes of JSON")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidNote(f"bad JSON body: {exc}") from None
        if not isinstance(data, dict):
            raise InvalidNote("request body must be a JSON object")
        return data

    def _route_note_id(self) -> str | None:
        """The note id when the path is /notes/{id}, else None."""
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if len(parts) == 2 and parts[0] == "notes":
            return parts[1]
        return None

    # ------------------------------------------------------------------ verbs

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        service: NoteService = self.server.service
        path = urlparse(self.path).path
        if path == "/notes":
            query = parse_qs(urlparse(self.path).query)
            tag = query.get("tag", [None])[0]
            limit_raw = query.get("limit", [None])[0]
            limit = int(limit_raw) if limit_raw is not None else None
            try:
                notes = service.list_notes(tag=tag, limit=limit)
            except (InvalidNote, ValueError) as exc:
                self._send_json(400, {"error": "invalid_request", "detail": str(exc)})
                return
            payload = _note_dicts(notes)
            self._send_json(200, {"notes": payload, "count": len(payload)})
            return
        if path == "/notes/search":
            query = parse_qs(urlparse(self.path).query)
            if "q" not in query or not query["q"][0].strip():
                self._send_json(400, {"error": "invalid_request", "detail": "q is required"})
                return
            page_raw = query.get("page", ["1"])[0]
            size_raw = query.get("page_size", [str(DEFAULT_PAGE_SIZE)])[0]
            try:
                page = int(page_raw)
                page_size = int(size_raw)
            except ValueError as exc:
                self._send_json(400, {"error": "invalid_request", "detail": str(exc)})
                return
            try:
                search_query = SearchQuery(text=query["q"][0], page=page, page_size=page_size)
            except SearchError as exc:
                self._send_json(400, {"error": "search_invalid", "detail": str(exc)})
                return
            result = service.search(search_query)
            self._send_json(200, result.to_dict())
            return
        note_id = self._route_note_id()
        if note_id is not None:
            try:
                note = service.get(note_id)
            except NoteNotFound:
                self._send_json(404, {"error": "note_not_found"})
                return
            self._send_json(200, note.to_dict())
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        service: NoteService = self.server.service
        if urlparse(self.path).path != "/notes":
            self._send_json(404, {"error": "not_found"})
            return
        try:
            data = self._read_json_body()
        except InvalidNote as exc:
            self._send_json(400, {"error": "invalid_request", "detail": str(exc)})
            return
        try:
            note = service.add(
                title=data.get("title", ""),
                body=data.get("body", ""),
                tags=data.get("tags", []),
            )
        except InvalidNote as exc:
            self._send_json(400, {"error": "invalid_note", "detail": str(exc)})
            return
        self._send_json(201, note.to_dict())

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib naming
        service: NoteService = self.server.service
        note_id = self._route_note_id()
        if note_id is None:
            self._send_json(404, {"error": "not_found"})
            return
        try:
            service.delete(note_id)
        except NoteNotFound:
            self._send_json(404, {"error": "note_not_found"})
            return
        self._send_json(200, {"deleted": True})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Quiet by default (tests capture stdout/stderr)."""


class ApiServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer carrying its service."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: NoteService) -> None:
        super().__init__(address, _ApiHandler)
        self.service = service


def make_server(
    service: NoteService, host: str = "127.0.0.1", port: int = 0
) -> tuple[ApiServer, int]:
    """A ready-to-serve server on an ephemeral port; returns (server, port)."""
    server = ApiServer((host, port), service)
    return server, server.server_address[1]


def serve(root: Path, host: str = "127.0.0.1", port: int = 7400) -> int:
    """Run the API against a store directory (blocking)."""
    service = NoteService(NoteStore(Path(root)))
    server, bound = make_server(service, host, port)
    print(f"jotbase serving {root} on http://{host}:{bound}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
