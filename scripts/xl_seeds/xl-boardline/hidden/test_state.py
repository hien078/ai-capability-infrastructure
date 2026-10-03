"""State: the JSON file, atomic writes, restarts, escaping (README §2)."""

from __future__ import annotations

import http.client
import json
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from conftest import parse_html


def _serve(data_path: Path) -> tuple[Any, threading.Thread]:
    from boardline.app import make_server

    srv = make_server("127.0.0.1", 0, str(data_path))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread


def _stop(srv: Any, thread: threading.Thread) -> None:
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=10)


def _get(srv: Any, path: str) -> str:
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=10)
    conn.request("GET", path)
    body = conn.getresponse().read().decode("utf-8")
    conn.close()
    return body


def _post(srv: Any, path: str, form: dict[str, str]) -> int:
    host, port = srv.server_address[:2]
    body = urllib.parse.urlencode(form).encode("utf-8")
    conn = http.client.HTTPConnection(host, port, timeout=10)
    conn.request(
        "POST", path, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    status = conn.getresponse().status
    conn.close()
    return status


def test_data_file_written(add: Any, data_path: Path) -> None:
    add("Fix login bug", assignee="ana", points="3")
    assert data_path.is_file()
    state = json.loads(data_path.read_text(encoding="utf-8"))
    assert state["seq"] == 1
    assert len(state["tasks"]) == 1
    task = state["tasks"][0]
    assert task["id"] == "t1"
    assert task["title"] == "Fix login bug"
    assert task["assignee"] == "ana"
    assert task["points"] == 3
    assert task["column"] == "todo"


def test_restart_preserves_tasks(add: Any, data_path: Path) -> None:
    add("Fix login bug")
    add("Write docs", column="doing")
    srv, thread = _serve(data_path)
    try:
        dom = parse_html(_get(srv, "/"))
        assert dom.find("article", {"id": "task-t1"}) is not None
        second = dom.find("article", {"id": "task-t2"})
        assert second is not None
        assert second.attrs.get("data-column") == "doing"
    finally:
        _stop(srv, thread)


def test_seq_continues_after_restart(add: Any, data_path: Path) -> None:
    add("first")
    srv, thread = _serve(data_path)
    try:
        assert _post(srv, "/tasks", {"title": "second"}) == 303
        dom = parse_html(_get(srv, "/"))
        assert dom.find("article", {"id": "task-t2"}) is not None
    finally:
        _stop(srv, thread)


def test_atomic_write_leaves_no_temp_files(add: Any, tmp_path: Path) -> None:
    add("Fix login bug")
    add("Write docs")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["boardline.json"]


def test_title_escaped_in_html(add: Any, page: Any) -> None:
    add("<script>alert('x')</script>")
    board = page()
    heading = board.find("h3", {"class": "task-title"})
    assert heading is not None
    assert heading.text_content() == "<script>alert('x')</script>"
    assert board.find("script") is None  # escaped, never a real node
