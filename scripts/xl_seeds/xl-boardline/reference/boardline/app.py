"""The boardline HTTP server, store and page rendering (README.md).

Implements the normative contract: ``make_server(host, port, data_path)``
returns a ready ``ThreadingHTTPServer`` (serving is the caller's job);
``main(argv)`` prints the listening line and serves forever. Routes,
validation, the exact page structure and the accessibility labels are
pinned by README.md §3-§6.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

COLUMNS = ("todo", "doing", "done")
COLUMN_TITLES = {"todo": "To do", "doing": "In progress", "done": "Done"}
MAX_TITLE = 80
MAX_ASSIGNEE = 40
POINTS_MIN = 0
POINTS_MAX = 8
DEFAULT_PORT = 8740


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


# -- store -------------------------------------------------------------------


def load_state(data_path: str) -> dict[str, Any]:
    """Load {"seq": int, "tasks": [...]}; a missing file is an empty board."""
    try:
        text = Path(data_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"seq": 0, "tasks": []}
    state = json.loads(text)
    if not isinstance(state, dict) or "seq" not in state or "tasks" not in state:
        raise ValueError(f"corrupt data file: {data_path}")
    return state


def save_state(data_path: str, state: dict[str, Any]) -> None:
    """Atomically write the state (temp file in the same directory + rename)."""
    target = Path(data_path)
    parent = target.parent if str(target.parent) else Path(".")
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, data_path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# -- validation ----------------------------------------------------------------


def validate_task(
    form: dict[str, list[str]],
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """-> (task fields, errors by field, submitted values for re-render)."""
    errors: dict[str, str] = {}
    values: dict[str, str] = {}

    title = (form.get("title") or [""])[0].strip()
    values["title"] = title
    if not title:
        errors["title"] = "Title is required."
    elif len(title) > MAX_TITLE:
        errors["title"] = f"Title must be at most {MAX_TITLE} characters."

    assignee = (form.get("assignee") or [""])[0].strip()
    values["assignee"] = assignee
    if len(assignee) > MAX_ASSIGNEE:
        errors["assignee"] = f"Assignee must be at most {MAX_ASSIGNEE} characters."

    raw_points = (form.get("points") or [""])[0].strip()
    points = 1
    values["points"] = raw_points
    if raw_points:
        if raw_points.isdigit() and POINTS_MIN <= int(raw_points) <= POINTS_MAX:
            points = int(raw_points)
        else:
            errors["points"] = f"Points must be an integer from {POINTS_MIN} to {POINTS_MAX}."

    column = (form.get("column") or [""])[0].strip() or "todo"
    values["column"] = column
    if column not in COLUMNS:
        errors["column"] = "Column must be one of: todo, doing, done."

    task = {"title": title, "assignee": assignee, "points": points, "column": column}
    return task, errors, values


# -- rendering -----------------------------------------------------------------


def _column_options(current: str) -> str:
    out = []
    for column in COLUMNS:
        selected = " selected" if column == current else ""
        out.append(f'<option value="{column}"{selected}>{COLUMN_TITLES[column]}</option>\n')
    return "".join(out)


def render_card(task: dict[str, Any]) -> str:
    tid = task["id"]
    assignee = task["assignee"] or "Unassigned"
    return (
        f'<article class="task" id="task-{tid}" data-column="{task["column"]}">\n'
        f'<h3 class="task-title">{_esc(task["title"])}</h3>\n'
        f'<p class="task-assignee">{_esc(assignee)}</p>\n'
        f'<p class="task-points"><span class="points">{task["points"]}</span> points</p>\n'
        f'<form class="task-move" method="post" action="/tasks/{tid}/move">\n'
        f'<select id="move-{tid}" name="column" aria-label="Move task to column">\n'
        f"{_column_options(task['column'])}"
        "</select>\n"
        '<button type="submit" class="task-move-submit">Move</button>\n'
        "</form>\n"
        f'<form class="task-delete" method="post" action="/tasks/{tid}/delete">\n'
        '<button type="submit" class="task-delete-submit" aria-label="Delete task">'
        "Delete</button>\n"
        "</form>\n"
        "</article>\n"
    )


def render_page(
    state: dict[str, Any],
    *,
    errors: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
) -> str:
    """Render the board page; on errors, preserve the submitted values."""
    errors = errors or {}
    values = values or {}
    title_value = _esc(values.get("title", ""))
    assignee_value = _esc(values.get("assignee", ""))
    points_value = _esc(values.get("points", "") or "1")
    column_value = values.get("column", "todo")
    parts = [
        "<!DOCTYPE html>\n",
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n',
        "<title>Task board</title>\n</head>\n<body>\n",
        "<h1>Task board</h1>\n",
    ]
    if errors:
        parts.append('<div id="form-errors" role="alert">\n')
        for name in ("title", "assignee", "points", "column"):
            if name in errors:
                parts.append(f'<p class="field-error" id="{name}-error">{_esc(errors[name])}</p>\n')
        parts.append("</div>\n")
    parts.extend(
        [
            '<form id="add-task" method="post" action="/tasks">\n',
            '<label for="task-title">Title</label>\n',
            f'<input id="task-title" name="title" required maxlength="80" value="{title_value}">\n',
            '<label for="task-assignee">Assignee</label>\n',
            f'<input id="task-assignee" name="assignee" maxlength="40" value="{assignee_value}">\n',
            '<label for="task-points">Points</label>\n',
            f'<input id="task-points" name="points" type="number" min="0" max="8" '
            f'value="{points_value}">\n',
            '<label for="task-column">Column</label>\n',
            f'<select id="task-column" name="column">\n{_column_options(column_value)}</select>\n',
            '<button type="submit" id="add-task-submit">Add task</button>\n',
            "</form>\n",
            '<main id="board">\n',
        ]
    )
    for column in COLUMNS:
        parts.append(f'<section id="col-{column}" class="column">\n')
        parts.append(f"<h2>{COLUMN_TITLES[column]}</h2>\n")
        cards = [t for t in state["tasks"] if t["column"] == column]
        if cards:
            for task in cards:
                parts.append(render_card(task))
        else:
            parts.append('<p class="empty">No tasks yet.</p>\n')
        parts.append("</section>\n")
    parts.append("</main>\n</body>\n</html>\n")
    return "".join(parts)


# -- server --------------------------------------------------------------------


class BoardServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer carrying the data path, the state and a lock."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host: str, port: int, data_path: str) -> None:
        self.data_path = data_path
        self.lock = threading.Lock()
        self.state = load_state(data_path)
        super().__init__((host, port), BoardHandler)

    def find_task(self, task_id: str) -> dict[str, Any] | None:
        for task in self.state["tasks"]:
            if task["id"] == task_id:
                return task
        return None


class BoardHandler(BaseHTTPRequestHandler):
    server: BoardServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    # -- plumbing ----------------------------------------------------------

    def _body_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return parse_qs(raw.decode("utf-8"), keep_blank_values=True)

    def _send(self, code: int, body: str, content_type: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, code: int, page: str) -> None:
        self._send(code, page, "text/html; charset=utf-8")

    def _redirect(self, location: str = "/") -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _not_found(self) -> None:
        self._send_html(
            404,
            '<!DOCTYPE html>\n<html lang="en">\n<head><meta charset="utf-8">'
            "<title>Not found</title></head>\n<body><h1>Not found</h1></body>\n</html>\n",
        )

    def _method_not_allowed(self) -> None:
        self._send_html(
            405,
            '<!DOCTYPE html>\n<html lang="en">\n<head><meta charset="utf-8">'
            "<title>Method not allowed</title></head>\n"
            "<body><h1>Method not allowed</h1></body>\n</html>\n",
        )

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send(200, "ok", "text/plain; charset=utf-8")
            return
        if path == "/":
            with self.server.lock:
                page = render_page(self.server.state)
            self._send_html(200, page)
            return
        if path == "/tasks" or re.fullmatch(r"/tasks/[^/]+/(move|delete)", path):
            self._method_not_allowed()
            return
        self._not_found()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/" or path == "/healthz":
            self._method_not_allowed()
            return
        form = self._body_form()
        if path == "/tasks":
            self._create_task(form)
            return
        move = re.fullmatch(r"/tasks/([^/]+)/move", path)
        if move:
            self._move_task(move.group(1), form)
            return
        delete = re.fullmatch(r"/tasks/([^/]+)/delete", path)
        if delete:
            self._delete_task(delete.group(1))
            return
        self._not_found()

    # -- actions -----------------------------------------------------------

    def _create_task(self, form: dict[str, list[str]]) -> None:
        task, errors, values = validate_task(form)
        if errors:
            with self.server.lock:
                page = render_page(self.server.state, errors=errors, values=values)
            self._send_html(422, page)
            return
        with self.server.lock:
            state = self.server.state
            state["seq"] = state.get("seq", 0) + 1
            task["id"] = f"t{state['seq']}"
            state["tasks"].append(task)
            save_state(self.server.data_path, state)
        self._redirect("/")

    def _move_task(self, task_id: str, form: dict[str, list[str]]) -> None:
        column = (form.get("column") or [""])[0].strip()
        with self.server.lock:
            task = self.server.find_task(task_id)
            if task is None:
                self._not_found()
                return
            if column not in COLUMNS:
                errors = {"column": "Column must be one of: todo, doing, done."}
                page = render_page(self.server.state, errors=errors, values={"column": column})
                self._send_html(422, page)
                return
            task["column"] = column
            save_state(self.server.data_path, self.server.state)
        self._redirect("/")

    def _delete_task(self, task_id: str) -> None:
        with self.server.lock:
            tasks = self.server.state["tasks"]
            remaining = [t for t in tasks if t["id"] != task_id]
            if len(remaining) == len(tasks):
                self._not_found()
                return
            self.server.state["tasks"] = remaining
            save_state(self.server.data_path, self.server.state)
        self._redirect("/")


# -- entry points ----------------------------------------------------------------


def make_server(host: str, port: int, data_path: str) -> BoardServer:
    """Create a bound, ready ThreadingHTTPServer (serving is the caller's job)."""
    return BoardServer(host, port, data_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="boardline", description="a team task board web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data", default="boardline.json")
    args = parser.parse_args(argv)
    server = make_server(args.host, args.port, args.data)
    print(f"listening on http://{args.host}:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
