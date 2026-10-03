# jotbase

A small note-taking service: JSON storage, a service layer, a stdlib
HTTP API and a CLI.

```
jotbase/
  model.py     Note (title, body, tags, timestamps) + validation
  storage.py   NoteStore — root/notes.json
  service.py   NoteService — CRUD, listing, stats
  api.py       the HTTP API (http.server)
  cli.py       the `jotbase` command line
```

## Data

`<root>/notes.json` is `{"format": 1, "notes": {id: note}}` — a dict
keyed by id in insertion order. A note dict is:

```json
{
  "id": "note_1a2b3c4d5e6f",
  "title": "Deploy runbook",
  "body": "…",
  "tags": ["ops", "deploy"],
  "created_at": "2026-01-05T09:00:00+00:00",
  "updated_at": "2026-01-05T09:00:00+00:00"
}
```

The shape is pinned — it is both the on-disk format and the API
response body.

## Validation

* `title` — non-empty, at most 200 characters.
* `tags` — slugs (`[a-z0-9]+(-[a-z0-9]+)*`), at most 10, no duplicates;
  the service slugifies what you give it (`"Ops Guide"` → `"ops-guide"`).
* timestamps are timezone-aware UTC.

## Ordering

`list_notes` returns notes newest first — `created_at` descending,
tie-broken by `id` ascending. The CLI and the API both show this order.

## Search

`jotbase search` / `GET /notes/search` is ranked full-text search:

* **Query** — `SearchQuery(text, page=1, page_size=20)`; `page >= 1`,
  `page_size` in `1..100`; empty text is an error.
* **Index** — an inverted index at `<root>/index.json` (a separate file;
  `notes.json` is never touched by search), maintained on every
  add/edit/delete, built lazily when missing, rebuilt when corrupt,
  stale-versioned, or out of step with the store's ids.
* **Ranking** — `score = tf * 2.0 (title token) * 1.5 (updated in the
  last 7 days)`; a note matches when it contains at least one query
  token; ties break by `created_at` descending, then id ascending.
* **Page** — `{"query", "page", "page_size", "total", "total_pages",
  "items": [note]}`; a page beyond the end has empty `items` and the
  true `total`.

```
jotbase --root DIR search "deploy runbook" --page 2 --page-size 5 --json
jotbase --root DIR search "tag:ops deploy" --tag runbook
GET /notes/search?q=deploy&page=2&page_size=5     # q required, else 400
GET /notes/search?q=deploy&tags=ops,runbook      # tag filters (AND)
```

**Tag filters** — a ``tag:x`` term in the query text (or the ``tags=``
API parameter / ``--tag`` CLI flag) restricts the results to notes
carrying every given tag; a ``tag:`` term no longer matches body text.
A tag-only query returns the tagged notes newest-first.


## API

| Method | Path | Notes |
|---|---|---|
| GET | `/notes` | `{"notes": [note], "count": n}` — `?tag=` filters, `?limit=` caps |
| GET | `/notes/{id}` | the note, or `404 {"error": "note_not_found"}` |
| POST | `/notes` | body `{"title", "body", "tags"}` → `201` note; bad body/notes → `400` |
| DELETE | `/notes/{id}` | `{"deleted": true}`, or `404` |

Unknown paths → `404 {"error": "not_found"}`. Errors are always JSON
objects with an `error` key.

## CLI

```
jotbase --root DIR add --title "Deploy runbook" --body "…" --tag ops
jotbase --root DIR list --tag ops --limit 10
jotbase --root DIR show note_1a2b3c4d5e6f
jotbase --root DIR delete note_1a2b3c4d5e6f
jotbase --root DIR stats
```

Exit 0 on success, 1 on any jotbase error.
