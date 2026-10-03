# boardline — a team task board web app

You are implementing `boardline`: a small, real web application — a
Kanban-style task board — served by the Python standard library only.
This README is the **complete and normative** specification: the
acceptance harness drives the app over HTTP (it uses
`boardline.app.make_server` and, for the CLI contract,
`python -m boardline`) and asserts on the served HTML — interactions,
validation, state and accessibility labels — so the page structure
section below must be followed exactly.

Constraints:

- Python 3.11+, **standard library only** — no third-party dependencies
  (no Flask/FastAPI/Jinja; `http.server` + `html` + `json` +
  `urllib.parse` are enough).
- The package must stay importable as `boardline`, runnable as
  `python -m boardline`, and `boardline.app.make_server` must exist with
  the signature below (the harness imports it).

## 1. Running

    python -m boardline [--host H] [--port P] [--data PATH]

- Defaults: host `127.0.0.1`, port `8740`, data file `./boardline.json`.
- On startup the server prints exactly one first line to stdout —
  `listening on http://<host>:<port>/` — **flushed before it starts
  serving** — then serves until interrupted.
- `--port 0` asks the OS for a free port (the printed line shows the real
  one).

Module API (used by the harness):

    from boardline.app import make_server
    server = make_server(host, port, data_path)  # -> ThreadingHTTPServer

`make_server` returns a bound, ready `http.server.ThreadingHTTPServer`
(`server.server_address[1]` is the real port); **serving is the caller's
job** (`serve_forever` in a thread, or the main thread). The data file is
created on first write; a missing file on startup means an empty board.

## 2. Data

- JSON file, shape `{"seq": <int>, "tasks": [<task>, ...]}`, written
  **atomically** (temp file in the same directory + rename).
- Task: `{"id": "t<seq>", "title": str, "assignee": str, "points": int,
  "column": "todo"|"doing"|"done"}`. At insert `seq` is incremented and
  the task's id becomes `t<seq>` (`t1`, `t2`, …); `seq` persists across
  restarts.
- `assignee` may be the empty string (rendered as "Unassigned").
- The server must be safe under concurrent requests (a lock around
  load-modify-save is enough).

## 3. Routes

| route                | method | behavior                                        |
|----------------------|--------|-------------------------------------------------|
| `/`                  | GET    | the board page (HTML)                            |
| `/healthz`           | GET    | 200, `text/plain`, body `ok`                    |
| `/tasks`             | POST   | create a task → 303 redirect to `/`             |
| `/tasks/<id>/move`   | POST   | move a task → 303 redirect to `/`               |
| `/tasks/<id>/delete` | POST   | delete a task → 303 redirect to `/`             |

- Success redirects: **303 See Other** with `Location: /`.
- Unknown path → 404 (a minimal HTML page). A known path with the wrong
  method → **405** (e.g. `GET /tasks`, `POST /healthz`).
- Form bodies are `application/x-www-form-urlencoded` (parse the body
  up to `Content-Length`; support repeated field names by taking the
  first value).

## 4. Validation (POST /tasks and move)

- `title`: required; strip surrounding whitespace; 1..80 characters
  after strip; missing/empty → invalid.
- `assignee`: optional; strip; at most 40 characters.
- `points`: optional; **default 1** when absent or empty; must be a
  string of digits representing an integer in **0..8** (no sign, no
  decimals — `9`, `abc`, `3.5`, `-1` are all invalid).
- `column`: optional; **default `todo`**; must be one of `todo`, `doing`,
  `done`.

On invalid input respond **422** with the board page re-rendered so it
contains:

- the error region `<div id="form-errors" role="alert">`, containing one
  `<p class="field-error" id="<name>-error">` per invalid field with a
  human-readable message (any wording),
- the submitted values **preserved** in the form inputs
  (`value="..."` on title/assignee/points, the submitted column
  selected).

Unknown task id on move/delete → **404**. Unknown `column` value on move
→ 422 (same error-region shape).

## 5. Page structure (normative)

Content type `text/html; charset=utf-8`. The harness matches elements by
these exact attributes — follow them literally:

    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <title>Task board</title>
    </head>
    <body>
      <h1>Task board</h1>

      <form id="add-task" method="post" action="/tasks">
        <label for="task-title">Title</label>
        <input id="task-title" name="title" required maxlength="80">
        <label for="task-assignee">Assignee</label>
        <input id="task-assignee" name="assignee" maxlength="40">
        <label for="task-points">Points</label>
        <input id="task-points" name="points" type="number" min="0" max="8" value="1">
        <label for="task-column">Column</label>
        <select id="task-column" name="column">
          <option value="todo">To do</option>
          <option value="doing">In progress</option>
          <option value="done">Done</option>
        </select>
        <button type="submit" id="add-task-submit">Add task</button>
      </form>

      <main id="board">
        <section id="col-todo" class="column">
          <h2>To do</h2>
          ... cards or <p class="empty">No tasks yet.</p> ...
        </section>
        <section id="col-doing" class="column">
          <h2>In progress</h2> ...
        </section>
        <section id="col-done" class="column">
          <h2>Done</h2> ...
        </section>
      </main>
    </body>
    </html>

Each task card (inside its column section):

    <article class="task" id="task-<id>" data-column="<column>">
      <h3 class="task-title"><escaped title></h3>
      <p class="task-assignee"><assignee or "Unassigned"></p>
      <p class="task-points"><span class="points"><points></span> points</p>
      <form class="task-move" method="post" action="/tasks/<id>/move">
        <select id="move-<id>" name="column" aria-label="Move task to column">
          <option value="todo">To do</option>
          <option value="doing">In progress</option>
          <option value="done">Done</option>
        </select>
        <button type="submit" class="task-move-submit">Move</button>
      </form>
      <form class="task-delete" method="post" action="/tasks/<id>/delete">
        <button type="submit" class="task-delete-submit" aria-label="Delete task">
          Delete
        </button>
      </form>
    </article>

- The move `<select>` shows the task's current column as selected.
- An empty column shows exactly `<p class="empty">No tasks yet.</p>`.
- All dynamic text (titles, assignees) is HTML-escaped (`html.escape`).
- The error region (§4) renders directly under `<h1>` when present.

## 6. Accessibility (normative)

- Every form control has an accessible name: text inputs and selects via
  `<label for>`; the per-card move select via its `aria-label`; buttons
  via their text (the delete button via `aria-label`).
- `<html lang="en">`, `<meta charset="utf-8">`, `<title>` present.
- The error region has `role="alert"`.
- Heading hierarchy: one `<h1>`, column `<h2>`s, card `<h3>`s.

## 7. Wireframe (illustrative — §5 is normative)

    +--------------------------------------------------------------------+
    | Task board                                            [h1]         |
    |  Title [______________]  Assignee [________]                      |
    |  Points [__]  Column [To do v]           ( Add task )              |
    +--------------------------------------------------------------------+
    | To do [h2]              | In progress [h2]     | Done [h2]           |
    |-------------------------|----------------------|---------------------|
    |  Fix login bug          |  Write API docs     |  Ship v1            |
    |  ana                    |  bo                  |  Unassigned         |
    |  3 points               |  1 points            |  2 points           |
    |  [To do v][Move]        |  [In progress v][Move]| [Done v][Move]     |
    |  [Delete]               |  [Delete]           |  [Delete]           |
    +--------------------------------------------------------------------+
