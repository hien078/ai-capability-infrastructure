"""The blog HTTP API (stdlib http.server, no framework).

Endpoints (JSON in, JSON out):

    GET  /posts                 {"posts": [post], "count": n}  ?published=&limit=
    GET  /posts/{slug}           {"post": post, "comments": [comment]}
    POST /posts                  201 {"slug": ...} | 400
    POST /posts/{slug}/comments  201 comment | 404

The API is a thin translation layer over BlogService — it knows
nothing about storage.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.service import BlogError, BlogService, PostNotFound

MAX_BODY_BYTES = 500_000


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
            raise BlogError("request body must be 1..500000 bytes of JSON")
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise BlogError(f"bad JSON body: {exc}") from None
        if not isinstance(data, dict):
            raise BlogError("request body must be a JSON object")
        return data

    def _slug_path(self) -> str | None:
        """The slug when the path is /posts/{slug} or /posts/{slug}/comments."""
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if len(parts) == 2 and parts[0] == "posts":
            return parts[1]
        return None

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        service: BlogService = self.server.service
        path = urlparse(self.path).path
        if path == "/posts":
            query = parse_qs(urlparse(self.path).query)
            published_raw = query.get("published", [None])[0]
            limit_raw = query.get("limit", [None])[0]
            published: bool | None
            if published_raw is None:
                published = None
            else:
                published = published_raw.lower() == "true"
            limit = int(limit_raw) if limit_raw is not None else None
            try:
                posts = service.list_posts(published=published)
            except BlogError as exc:
                self._send_json(400, {"error": "invalid_request", "detail": str(exc)})
                return
            if limit is not None:
                posts = posts[:limit]
            payload = [post.to_json_row() for post in posts]
            self._send_json(200, {"posts": payload, "count": len(payload)})
            return
        slug = self._slug_path()
        if slug is not None:
            try:
                payload = service.get_post(slug)
            except PostNotFound:
                self._send_json(404, {"error": "post_not_found"})
                return
            self._send_json(
                200,
                {
                    "post": payload["post"].to_json_row(),
                    "comments": [comment.to_json_row() for comment in payload["comments"]],
                },
            )
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        service: BlogService = self.server.service
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if parts == ["posts"]:
            try:
                data = self._read_json_body()
                post = service.create_post(
                    title=data.get("title", ""),
                    body=data.get("body", ""),
                    tags=data.get("tags", []),
                )
            except BlogError as exc:
                self._send_json(400, {"error": "invalid_request", "detail": str(exc)})
                return
            self._send_json(201, post.to_json_row())
            return
        if len(parts) == 3 and parts[0] == "posts" and parts[2] == "comments":
            try:
                data = self._read_json_body()
                comment = service.add_comment(
                    parts[1], data.get("author", ""), data.get("body", "")
                )
            except PostNotFound:
                self._send_json(404, {"error": "post_not_found"})
                return
            except BlogError as exc:
                self._send_json(400, {"error": "invalid_request", "detail": str(exc)})
                return
            self._send_json(201, comment.to_json_row())
            return
        self._send_json(404, {"error": "not_found"})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Quiet by default."""


class ApiServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer carrying its service."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: BlogService) -> None:
        super().__init__(address, _ApiHandler)
        self.service = service


def make_server(
    service: BlogService, host: str = "127.0.0.1", port: int = 0
) -> tuple[ApiServer, int]:
    """A ready-to-serve server on an ephemeral port."""
    server = ApiServer((host, port), service)
    return server, server.server_address[1]


def serve(root: Path, host: str = "127.0.0.1", port: int = 7600) -> int:
    """Run the API against a data directory (blocking)."""
    from flatvault import Database

    service = BlogService(Database(Path(root)))
    server, bound = make_server(service, host, port)
    print(f"blog serving {root} on http://{host}:{bound}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
