"""XL orchestration-bench fixtures, builder "b" (job xl-fixtures-b, 2026-10-03).

Three XL fixtures for the delegation bench (ADR-014 client-side DAG
question: does OpenCode-as-orchestrator delegating leaves to ACI's
agent-runs beat OpenCode solo, on really hard long tasks, and what does
a mid-run requirement change cost). Each fixture is a medium codebase a
real agent needs hours to work through, graded ONLY by hidden
acceptance tests that live outside the workspace:

- ``xl-ledger-bugfix`` (kind: bug) — a 2k-line bookkeeping package with
  FIVE planted, interacting bugs (parser off-by-one that drops every
  import's last row; business-day grouping that ignores the timezone;
  a manual entry that forgets to invalidate the report cache; a
  check-then-act id allocation that collides with the sync hook; an
  account statement sorted by day instead of by timestamp). The task
  text is a user bug report — symptoms, never causes.
- ``xl-notes-search`` (kind: feature) — add ranked full-text search
  with pagination to a note service, end to end: query model, a
  persisted inverted index, scoring, an HTTP endpoint, a CLI command
  and docs — with hard backward-compat pins (old data dirs load and
  search with zero migration, notes.json's format never changes, the old
  endpoints' shapes are frozen).
- ``xl-jsondb-sqlite`` (kind: refactor) — replace a homegrown JSON-file
  document store's internals with a sqlite3 engine behind the SAME
  public API (a characterization suite pins the observable behavior:
  ids, ordering, copies, error types, datetime round-trips), migrate
  legacy data dirs transparently, and add four new requirements
  (cross-table transactions, count(), delete_where(), crash-safe
  writes). The app layer on top must keep working UNCHANGED.

Schema (per task dict):

    name            "xl-<slug>"
    kind            bug | feature | refactor (of build|ui|bug|feature|refactor|research|project)
    seed            {relpath: content}  the shipped workspace (the agent sees this)
    task            str  the ticket text the agent gets
    hidden_tests    {relpath: content}  the ORIGINAL acceptance suite (post-hoc only)
    change          {note: str, hidden_tests: {...}, patch: {...}, invalidates: [ids],
                    guards: [ids]}
                    the REQUIREMENT-CHANGE pack: the note the run is resumed
                    with, the extra hidden tests that encode it, `patch` (an
                    extension of the job schema) = the reference's own
                    implementation of the change, the original test ids
                    the change invalidates (node ids RELATIVE TO THE HIDDEN
                    ROOT, e.g. "test_import_parser.py::test_unknown_dialect"),
                    and `guards` = the change tests that pin UNCHANGED
                    behavior (the comma dialect stays the default, the
                    summary flags stay unchanged) — the ONLY change tests
                    allowed to pass pre-patch; every other change test must
                    BITE on the unpatched reference (the unified
                    verify_fixture semantics, job xl-fix 2026-10-03).
    reference       {relpath: content}  the known-good solution, an overlay
                      OVER THE SEED (unchanged files are not repeated)
    expected_hours  float

Fixture data lives as real reviewable files (loaded at import):

    scripts/xl_seeds/<name>/        the shipped workspace
    scripts/xl_hidden/<name>/       the original hidden suite
    scripts/xl_reference/<name>/    the reference overlay
    scripts/xl_change/<name>/patch/  the change patch (overlay over the reference)
    scripts/xl_change/<name>/hidden/ the change's extra hidden tests

Verification obligations (pinned by tests/unit/test_xl_tasks_b.py, all
mechanical — no model, no DB, no network): every fixture FAILS as
shipped (the hidden suite over the seed), PASSES with the reference
(the same suite over seed+reference), the POST-CHANGE suite (original
minus invalidated plus the change's tests) passes over
seed+reference+patch, the change's tests BITE on the unpatched
reference (every test that is not a declared guard), each invalidated
test FAILS on the patched reference, the hidden tests are unreachable
from the seed, the task text reveals no hidden test, and every path is
safe and relative.

§34 caveats apply to any bench round on this set: author-built
fixtures, deterministic hidden suites (no model judge), one author's
notion of "hours". The seeds are stdlib-only (no network at run time);
the hidden suites need only pytest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


def _load_tree(rel: str) -> dict[str, str]:
    """Load a fixture data directory into {relpath: content}.

    Skips the local noise (dotfiles, ``__pycache__``, bytecode) so a
    stray editor or Finder artifact can never end up in a workspace.
    """
    root = _HERE / rel
    if not root.is_dir():
        return {}
    tree: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.suffix == ".pyc":
            continue
        if "__pycache__" in path.parts:
            continue
        tree[str(path.relative_to(root)).replace("\\", "/")] = path.read_text(encoding="utf-8")
    return tree


LEDGER_TASK = """\
From: Dana Okonkwo (ops) — to the dev list — priority HIGH
Subject: ledger 1.4 — five wrong numbers and one crash before month-end

Team — the bookkeeping app (the `ledger` package in this repo) has been
wrong in five different ways since the last release, and I finally have
reproducible reports from the offices. Fix all five. The README in the
repo is the behavior spec — where the code disagrees with it, the README
wins. Do not change the public API or the on-disk format; these are
bugs, not a redesign.

1. IMPORTS ARE ONE SHORT. Every bank export we import ends up missing
   its LAST transaction — we reconcile against the bank's own totals
   and the final row of every file never makes it into the store. It
   happens on every single file, with and without a trailing blank
   line, from every office.

2. TOKYO POSTS ON THE WRONG DAY. The Tokyo office closes their books at
   midnight JST and their evening transactions (early afternoon UTC)
   show up on the PREVIOUS day's summary. New York has the mirror
   problem: their early-evening entries (late night UTC) land on the
   NEXT day. The UTC office is fine. The daily summary must group by
   the business day in the configured timezone, not by the UTC calendar
   date.

3. STALE DASHBOARD. When we record a manual transaction (CLI or
   service), the daily summary does not change — it only picks the
   entry up after a restart. Imports DO show up immediately, so
   whatever imports do to refresh the reports, manual entries are
   missing it.

4. BACKFILL ORDER. We backfill old transactions (recording them out of
   order) and the account statement's running balance is computed in
   the order we TYPED them, not the order they happened — so the
   balance column is wrong mid-day and the overdraft warning points at
   the wrong row. Same-timestamp transactions should tie-break by id.

5. DUPLICATE-ID CRASH. We use the allocate_hook extension point (the
   sync replication prototype) and got a "duplicate transaction id"
   crash when the hook added a transaction while an import was
   running. The hook is documented to be safe for exactly this — ids
   must never collide, whatever the hook does.

Acceptance: the README's documented behaviors all hold — the import
format (every row, quoting rules), business days in the report
timezone, cache freshness after every mutation, statement order
(timestamp, then id), id uniqueness under the hook. The fix is the
deliverable; regression tests welcome.
"""

NOTES_TASK = """\
Feature request — jotbase 1.5: ranked full-text search

jotbase (this repo) is our note service: a JSON store, a service layer,
a small stdlib HTTP API and a CLI (see README for the current surface).
We need ranked full-text search, end to end, WITHOUT breaking any
existing client:

1. QUERY MODEL — a validated SearchQuery (text, page, page_size;
   defaults 20, max 100; page >= 1; empty text rejected).
2. INDEX — an inverted index persisted at <root>/index.json, maintained
   incrementally on add/edit/delete (title + body tokens, casefolded
   alphanumeric). It must NOT change notes.json. Old data dirs (no
   index.json) keep working: build the index lazily on first search. A
   stale, missing or corrupt index is rebuilt, never fatal.
3. RANKING — score = term frequency * 2.0 for title tokens * 1.5 for
   notes updated in the last 7 days; ties: newer created_at first,
   then id ascending. A query matches a note when the note contains at
   least one query token; no match returns an empty page, not an error.
4. PAGINATION — a Page envelope (items, page, page_size, total,
   total_pages); a page beyond the end returns empty items with the
   true total.
5. API — GET /notes/search?q=&page=&page_size= returning the envelope
   with note dicts; missing q -> 400; invalid page/page_size -> 400.
   The existing endpoints (GET /notes, GET /notes/{id}, POST /notes,
   DELETE /notes/{id}) keep their exact response shapes.
6. CLI — `search QUERY [--page N] [--page-size N] [--json]`; the
   existing subcommands and flags unchanged.
7. DOCS — README gains the endpoint, the CLI command and the ranking
   rules.

Backward compatibility is a hard requirement: a data dir written by the
current version must load and search with zero migration; notes.json's
format must not change; list_notes()'s signature must not change.
"""

JSONDB_TASK = """\
Refactor ticket — flatvault: sqlite engine, same behavior + 4 new requirements

flatvault (this repo) is our homegrown document store (a JSON file per
table, a tiny where() DSL) with a small blog app (app/) on top. The
JSON engine is fragile: whole-file rewrite per save, no transactions,
full scans, and a crash mid-save can truncate a file. Replace its
internals with a sqlite3-backed engine (stdlib sqlite3 — no new
dependency).

HARD CONSTRAINTS — a characterization suite pins the current observable
behavior (you will not see it; when in doubt keep behavior identical):

- Public API unchanged: Database(root), db.table(name),
  Table.insert/get/find/update/delete/all, the where()/And()/Or() DSL,
  drop_table, save, close. Same return types, same error types, same
  insertion ordering, ids never reused after a delete.
- app/ keeps working UNCHANGED — if you must edit app/, that is a
  regression.
- Existing JSON data dirs must migrate transparently on first open (ids
  and the next id preserved, datetimes decoded); the original JSON
  files are left in place untouched as the backup; migration happens
  exactly once.

NEW REQUIREMENTS:

1. Database.transaction() — a context manager, atomic across tables,
   rollback on any exception, commit on success.
2. Table.count(where=None) — the number of matching rows (int).
3. Table.delete_where(where) -> int — deletes the matching rows,
   returns how many.
4. Crash-safe writes — a failing save must never leave the data dir
   corrupt (no partial or truncated stores).

The README documents the current API; the store's rules there are the
contract.
"""

LEDGER_CHANGE_NOTE = """\
Requirement change (arrives mid-run): the bank is switching export
dialects. `parse_export(text, dialect="semicolon")` must support the
new format: semicolon-delimited fields, `#` comment lines (skipped),
and DD.MM.YYYY dates (recorded at midnight UTC); the comma dialect
stays the default and unchanged. Also: `summary` gains `--since DAY`
(summarize every business day from that day forward, one line per day,
ascending — only days with transactions appear) and `--tz ZONE`
(override the business timezone for the invocation; an unknown zone is
a clean error, exit 1). The service gains the matching
`summaries_since(since)`.
"""

NOTES_CHANGE_NOTE = """\
Requirement change (arrives mid-run): the query language gains tag
filters. A `tag:x` term in the query text filters results to notes
tagged `x` (slugified, case-insensitive — `tag:Ops-Guide` filters to
the `ops-guide` tag); plain terms still rank as before; a tag-only
query returns the tagged notes newest-first. A `tag:` term NO LONGER
matches body text. The API gains `tags=` (a comma list, AND semantics)
on /notes/search; the CLI gains `--tag` (repeatable).
"""

JSONDB_CHANGE_NOTE = """\
Requirement change (arrives mid-run): nested transactions must WORK —
via savepoints. An inner rollback undoes only the inner scope; an inner
commit is still undone by an outer rollback; no more RuntimeError.
Also add `Table.batch_insert(docs) -> list[int]` — insert many rows in
order in one save, returning their ids (validated up front: a row with
an id or a non-dict row raises before anything is inserted).
"""

TASK_TEXTS: dict[str, str] = {
    "xl-ledger-bugfix": LEDGER_TASK,
    "xl-notes-search": NOTES_TASK,
    "xl-jsondb-sqlite": JSONDB_TASK,
}

CHANGE_NOTES: dict[str, str] = {
    "xl-ledger-bugfix": LEDGER_CHANGE_NOTE,
    "xl-notes-search": NOTES_CHANGE_NOTE,
    "xl-jsondb-sqlite": JSONDB_CHANGE_NOTE,
}


#: Change tests that pin UNCHANGED behavior — the ONLY change tests allowed
#: to pass pre-patch (every other change test must BITE on the unpatched
#: reference). The ledger change explicitly keeps the comma dialect the
#: default and the summary flags unchanged; its three guard tests pin that.
#: notes-search and jsondb have NO guards: every change test bites.
GUARDS: dict[str, list[str]] = {
    "xl-ledger-bugfix": [
        "test_change_dialect.py::test_comma_dialect_is_still_the_default",
        "test_change_dialect.py::test_unknown_dialect_is_still_rejected",
        "test_change_since_and_tz.py::test_cli_summary_without_flags_is_unchanged",
    ],
    "xl-notes-search": [],
    "xl-jsondb-sqlite": [],
}


def _fixture(
    name: str,
    kind: str,
    expected_hours: float,
    invalidates: list[str],
) -> dict[str, Any]:
    """Assemble one fixture dict from the data directories."""
    return {
        "name": name,
        "kind": kind,
        "seed": _load_tree(f"xl_seeds/{name}"),
        "task": TASK_TEXTS[name],
        "hidden_tests": _load_tree(f"xl_hidden/{name}"),
        "change": {
            "note": CHANGE_NOTES[name],
            "hidden_tests": _load_tree(f"xl_change/{name}/hidden"),
            "patch": _load_tree(f"xl_change/{name}/patch"),
            "invalidates": invalidates,
            "guards": list(GUARDS[name]),
        },
        "reference": _load_tree(f"xl_reference/{name}"),
        "expected_hours": expected_hours,
    }


TASKS: list[dict[str, Any]] = [
    _fixture(
        "xl-ledger-bugfix",
        "bug",
        3.0,
        [
            "test_import_parser.py::test_parse_export_rejects_an_unknown_dialect",
            "test_cli.py::test_cli_summary_has_no_timezone_flag",
        ],
    ),
    _fixture(
        "xl-notes-search",
        "feature",
        3.0,
        ["test_search_ranking.py::test_tag_token_is_plain_text"],
    ),
    _fixture(
        "xl-jsondb-sqlite",
        "refactor",
        2.5,
        ["test_transactions.py::test_nested_transaction_raises"],
    ),
]

__all__ = ["TASKS"]
