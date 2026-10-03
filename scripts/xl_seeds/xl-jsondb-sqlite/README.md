# flatvault + blog

A tiny document store (``flatvault``) with a small blog app (``app``)
on top.

```
flatvault/
  queries.py     the where() DSL: ==, !=, >, >=, <, <=, in_, contains, And, Or
  serialize.py   JSON with datetime markers ({"$datetime": iso})
  tables.py      Table — one JSON file per table: {"rows": [...], "next_id": n}
  database.py    Database — a directory of tables
app/
  models.py      Post / Comment dataclasses
  repository.py  PostRepository / CommentRepository (the only table-name code)
  service.py     BlogService
  cli.py         the `blog` command line
```

## The store API (pinned)

```python
from flatvault import Database, where

db = Database(Path("data"))
posts = db.table("posts")
post_id = posts.insert({"slug": "hello", "title": "Hello", "published": False})
posts.get(post_id)  # the row dict (a copy) or None
posts.find(where("published") == True)  # matching rows, insertion order
posts.update(post_id, {"published": True})  # merged row or None
posts.delete(post_id)  # True when it was there
posts.all()  # every row, insertion order
db.drop_table("posts")  # TableError when unknown
```

Rules:

* ``insert`` assigns the id (an int, starting at 1, never reused after a
  delete) and rejects a row that carries one.
* ``get``/``find``/``all`` return copies — mutating them changes nothing.
* ``find``/``all`` keep insertion order.
* ``update`` refuses to change ``id``; returns the merged row or None.
* a missing field never matches a query — not even ``!=``; comparisons
  that would raise ``TypeError`` are a non-match.
* ``datetime`` values round-trip through save/load.
* a corrupt file raises on load; a missing file is an empty table.

## The blog app

```bash
blog --root data post add --title "Hello" --tag intro
blog --root data post publish hello
blog --root data comment add hello --author Ada --body "First!"
blog --root data post list          # published only
blog --root data post list --all
blog --root data stats
```

Slugs are derived from the title and uniquified (``hello``, ``hello-2``).

## The blog HTTP API

| Method | Path | Notes |
|---|---|---|
| GET | `/posts` | `{"posts": [post], "count": n}` — `?published=true&limit=` |
| GET | `/posts/{slug}` | `{"post": post, "comments": [comment]}` or 404 |
| POST | `/posts` | body `{"title", "body", "tags"}` → 201 post |
| POST | `/posts/{slug}/comments` | body `{"author", "body"}` → 201 comment |

Timestamps on the wire are ISO strings; in the store they are datetimes.

## Known limits (why a refactor is coming)

The JSON engine rewrites a whole file per save, scans every row per
query, has no transactions across tables, and a crash mid-save can
truncate a file. The sqlite refactor replaces the internals — the API
above stays.
