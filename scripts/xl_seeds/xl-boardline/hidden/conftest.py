"""Hidden acceptance harness for the boardline fixture — stdlib only.

WHY a stdlib harness (not Playwright): the bench runs on the Linux host
inside bubblewrap with NO network, and Playwright is neither in the repo
lock (requirements-lock.txt) nor vendorable as an offline wheelhouse
with browser binaries (~150 MB). The job sanctions the fallback: this
harness drives the app over real HTTP (``http.client`` against an
in-process ``boardline.app.make_server`` on an ephemeral port) and
asserts on the served DOM through a small ``html.parser``-based checker
— interactions, validation, state and accessibility labels.

``XL_WORKSPACE`` must point at the fixture workspace (the directory
that contains the ``boardline``/ package); it is put on ``sys.path`` so
the tests can import ``boardline.app``.
"""

from __future__ import annotations

import html.parser
import http.client
import os
import sys
import threading
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

WS = Path(os.environ.get("XL_WORKSPACE", ""))


def pytest_configure(config: Any) -> None:
    if not WS.is_dir():
        raise pytest.UsageError(f"XL_WORKSPACE is not a directory: {WS}")
    sys.path.insert(0, str(WS))


# -- minimal DOM -----------------------------------------------------------------

_VOID_TAGS = {"meta", "input", "br", "hr", "img", "link", "source"}


class Node:
    """A DOM node: tag, attributes, children, own text pieces."""

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag = tag
        self.attrs = {key: value or "" for key, value in attrs}
        self.children: list[Node] = []
        self.text: list[str] = []

    def iter_nodes(self) -> Any:
        yield self
        for child in self.children:
            yield from child.iter_nodes()

    def find_all(self, tag: str | None = None, attrs: dict[str, str] | None = None) -> list[Node]:
        """All descendant nodes matching the tag and every given attribute."""
        wanted = attrs or {}
        out: list[Node] = []
        for node in self.iter_nodes():
            if tag is not None and node.tag != tag:
                continue
            if all(node.attrs.get(key) == value for key, value in wanted.items()):
                out.append(node)
        return out

    def find(self, tag: str | None = None, attrs: dict[str, str] | None = None) -> Node | None:
        found = self.find_all(tag, attrs)
        return found[0] if found else None

    def text_content(self) -> str:
        return "".join(self.text) + "".join(c.text_content() for c in self.children)


class _DomParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [])
        self.stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for position in range(len(self.stack) - 1, 0, -1):
            if self.stack[position].tag == tag:
                del self.stack[position:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].text.append(data)


def parse_html(text: str) -> Node:
    parser = _DomParser()
    parser.feed(text)
    parser.close()
    return parser.root


# -- fixtures --------------------------------------------------------------------


@pytest.fixture
def data_path(tmp_path: Path) -> Path:
    return tmp_path / "boardline.json"


@pytest.fixture
def server(data_path: Path) -> Any:
    """A running boardline server on an ephemeral port (in-process)."""
    from boardline.app import make_server

    srv = make_server("127.0.0.1", 0, str(data_path))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=10)


@pytest.fixture
def client(server: Any) -> Any:
    """request(method, path, form=None) -> SimpleNamespace(status, body, location, ...)."""

    host, port = server.server_address[:2]

    def _request(
        method: str,
        path: str,
        form: dict[str, str] | None = None,
        follow: bool = False,
    ) -> SimpleNamespace:
        conn = http.client.HTTPConnection(host, port, timeout=10)
        body = None
        headers = {}
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        result = SimpleNamespace(
            status=response.status,
            body=response.read().decode("utf-8"),
            location=response.getheader("Location"),
            content_type=response.getheader("Content-Type") or "",
        )
        conn.close()
        if follow and result.status in (301, 302, 303, 307, 308) and result.location:
            return _request("GET", result.location)
        return result

    return _request


@pytest.fixture
def page(client: Any) -> Any:
    """GET / and parse the board page DOM."""

    def _page(path: str = "/") -> Node:
        result = client("GET", path)
        assert result.status == 200, f"GET {path} -> {result.status}"
        return parse_html(result.body)

    return _page


@pytest.fixture
def add(client: Any) -> Any:
    """add(title, **fields) -> the new task id (t<n>); asserts the redirect."""

    def _add(title: str, **fields: str) -> str:
        form = {"title": title, **fields}
        result = client("POST", "/tasks", form=form)
        assert result.status == 303, f"POST /tasks {form} -> {result.status}\n{result.body}"
        assert result.location == "/"
        board = parse_html(client("GET", "/").body)
        for card in board.find_all("article", {"class": "task"}):
            heading = card.find("h3", {"class": "task-title"})
            if heading is not None and heading.text_content() == title:
                return card.attrs["id"].removeprefix("task-")
        pytest.fail(f"card for {title!r} not found after add")

    return _add
