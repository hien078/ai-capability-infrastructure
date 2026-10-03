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

## Import / export

`jotbase.import_export` moves notes between installs as one JSON
document — `{"format": 1, "notes": [note dicts]}`:

```
export_notes(service) / export_to_path(service, path)
import_notes(service, document, replace=False) / import_from_path(...)
```

Import merges: a note whose id already exists is kept (skipped) unless
`replace=True`; the whole document is validated first, so a bad
document imports nothing.

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
