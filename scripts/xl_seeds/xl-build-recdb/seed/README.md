# recdb — a tiny flat-file record database CLI

You are implementing `recdb`: a small, real command-line tool that stores
records (JSON objects) in a JSON Lines file and lets the user add, query,
update, delete, import and export them. This README is the **complete and
normative** specification — every detail below is intentional. Acceptance
is an automated suite that drives the tool through `python -m recdb ...`
and pins the behaviors in this spec (happy paths, edge cases and error
handling), so follow it exactly where it is exact.

Constraints:

- Python 3.11+, **standard library only** — no third-party dependencies.
- The package must stay importable as `recdb` and runnable as
  `python -m recdb <command> ...`. Keep the shipped layout
  (`recdb/store.py`, `recdb/query.py`, `recdb/cli.py`); the acceptance
  suite only invokes the CLI, so you may refactor internals freely as long
  as the CLI contract below is honored.

## 1. Store location

The store is a single file, resolved in this order:

1. `--db PATH` — a global flag given **before** the command.
2. else the `RECDB_HOME` environment variable, if set: the store is
   `$RECDB_HOME/store.jsonl`.
3. else `./recdb.jsonl` in the current working directory.

Read commands (`list`, `get`, `query`, `stats`, `check`) on a **missing**
store behave as if the store were empty (exit 0). The store file is created
by the first write.

## 2. Records

- A record is a JSON object. Every record has an `id`.
- **Auto ids** (when no `id=` pair is given): `1` plus the largest existing
  integer id in the store, or `1` if there is none. Only integer ids
  participate; explicit string ids do not.
- **Explicit ids**: `id=<value>` in `add`. The id must be a **string or an
  integer** — any other JSON type is a usage error. A duplicate id is a
  usage error.
- Ids given as command arguments (`get`, `update`, `delete`) are parsed
  with the value-typing rules in §3 (bare `1` is the integer 1, bare `abc`
  is the string `"abc"`, quoted `"1"` is the string `"1"`).

## 3. Value typing

`key=value` pairs split on the **first** `=`. The value text is typed:

1. If it starts and ends with a matching single or double quote, the value
   is the string between the quotes (no escape sequences).
2. Else, if it parses as a JSON scalar (integer, float, `true`, `false`,
   `null`), it is that value. (Floats must be finite — `NaN`/`Infinity` are
   not JSON scalars and stay strings.)
3. Else it is the raw string.

Keys must be non-empty and must not contain whitespace. A pair without
`=`, an empty key, or the same key twice in one command is a usage error.

## 4. Commands

All output goes to stdout, all error messages to stderr, one line each.
Every command prints records as **one-line JSON** (JSON Lines).

### add

    recdb add key=value ... [--json]

- At least one pair is required.
- Prints the new record's id (JSON-encoded: `1` or `"abc"`), or with
  `--json` the whole record as one-line JSON.
- Duplicate explicit id → stderr `recdb: duplicate id: <id>`, exit 2.

### get

    recdb get <id>

Prints the record as one-line JSON. Not found → stderr
`recdb: not found: <id>`, exit 3.

### list

    recdb list [--where EXPR] [--sort FIELD[:asc|desc]] [--limit N] [--offset N]

- Prints the records as JSON Lines, **in store order** by default.
- `--sort FIELD` — ascending by default, `:desc` for descending. Records
  **missing** the sort field sort **last** in both directions; ties keep
  store order (the sort is stable). Values of mixed types sort numbers
  (bools excluded) first, then strings, then everything else — each
  group by value.
- `--offset N` skips the first N records (default 0); `--limit N` keeps at
  most N (default: all). Both apply after sorting. N must be a
  non-negative integer.
- `--where EXPR` filters with the query grammar of §5 (a single
  expression).

### query

    recdb query EXPR [--sort ...] [--limit ...] [--offset ...]

- `EXPR` is one or more **terms**; whitespace between terms means AND.
- A term is `field<op><value>` with **no spaces inside** — the value may
  be quoted (then it is one shell word). Operators: `=`, `!=`, `>`, `>=`,
  `<`, `<=`, `~=`.
- The value is typed with the rules of §3 (quoted → string; bare → JSON
  scalar or string).
- A **missing field** makes the term false for **every** operator, `!=`
  included — never an error.
- Comparison rules: `=`/`!=` accept two numbers (bools excluded), two
  strings, two bools, or two nulls; `>`/`>=`/`<`/`<=` accept two numbers
  (numeric) or two strings (lexicographic); `~=` accepts two strings and
  is a case-sensitive **substring** test. A record value that is null, or
  a type pair the operator does not accept, makes the term **false** —
  never an error. (`true`/`false` literals therefore match only JSON
  booleans; `null` matches only JSON null.)
- `or` (as a term separator) and parentheses are **reserved** in this
  version: using either is a usage error (exit 2).
- Usage errors → stderr `recdb: usage: <message>`, exit 2. No matches →
  empty output, exit 0.

### update

    recdb update <id> key=value ... [--unset FIELD] ...

- Sets the pairs (typing rules of §3) and removes each `--unset` field.
- `id` cannot be set or unset → usage error.
- Prints the updated record as one-line JSON. Unknown id → exit 3.

### delete

    recdb delete <id>

Silent success (exit 0, no output). Unknown id → stderr, exit 3.

### import

    recdb import --file PATH [--format csv|jsonl]

- Default format `csv`. The first row is the **header**. An `id` column is
  optional: a row whose id already exists **updates** that record's
  fields (fields absent from the row are kept); a row whose id does not
  exist is inserted with that id; a row without an id is inserted with an
  auto id.
- CSV cell typing: `true`/`false` → boolean, `null` → null, JSON integer →
  int, JSON float → float, anything else → string. An **empty cell is the
  empty string** (use a `null` cell for null).
- `jsonl` format: each line a JSON object, same upsert-by-id semantics; a
  line that is not a JSON object → usage error (exit 2).
- Prints `<N> imported, <M> updated` (e.g. `3 imported, 1 updated`).
- Import is **all-or-nothing**: an invalid row aborts with exit 2 and
  saves nothing.
- Unreadable file → stderr, exit 2.

### export

    recdb export --file PATH [--format csv|jsonl] [--where EXPR]

- Default format `jsonl`: one record per line.
- `csv`: the columns are the **sorted union** of field names over the
  (filtered) records; a record missing a field gets an empty cell; booleans
  render as `true`/`false`, null as `null`.
- `--where EXPR` filters with the query grammar of §5.
- The parent directory must exist (else exit 2). Existing files are
  overwritten.

### stats

    recdb stats

Prints one-line JSON:

    {"records": <N>, "fields": {<field>: <count>, ...}, "corrupt_lines": <K>, "duplicate_ids": <D>}

`fields` counts, per **non-id** field, how many records carry it.
`corrupt_lines` is the number of unparseable lines; `duplicate_ids` the
number of ids that appear in more than one line.

### check

    recdb check

Prints one-line JSON `{"corrupt_lines": [<line numbers>], "duplicate_ids":
[<ids>]}` (line numbers 1-based, ids in first-appearance order). Exit 0 if
both lists are empty, else exit 4.

## 5. Store file semantics

- UTF-8 JSON Lines: one record per line. **Blank lines are ignored.**
- A line that is **not valid JSON, is not a JSON object, or has no `id`**
  counts as **corrupt**: it is ignored by every read command (`list`,
  `get`, `query`, `export`, `stats`) — corrupt lines never crash the
  tool; `stats`/`check` count them.
- A **duplicate id** in the file: the **last** line wins on read;
  `stats`/`check` report it.
- Writes are **atomic**: write a temp file in the same directory, then
  rename over the store. If the store cannot be written (e.g. its
  parent directory does not exist), the command fails with exit 2.

## 6. Exit codes

| code | meaning                          |
|------|----------------------------------|
| 0    | success                          |
| 2    | usage / validation error         |
| 3    | not found                        |
| 4    | store corrupt (`check`)          |
| 1    | anything else (unexpected)      |

No args, an unknown command, or a malformed command line → usage error,
exit 2.
