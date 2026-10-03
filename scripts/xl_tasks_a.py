"""XL-orchestration bench fixtures — builder "a": a BUILD fixture and a UI fixture (2026-10-03).

The XL bench (docs/plans + the shared design, 2026-10-03) asks whether
client-side decomposition + delegation to ACI's HarnessKernel beats
OpenCode doing REALLY HARD, LONG tasks alone (build software, fix hard
bugs, research, project-scale work, UI), and what re-planning costs
when requirements change mid-run. This module is builder "a"'s share of
the fixture set: the two fixtures the job assigned — one "build" and
one "ui". No runner changes, no product code, no model round.

Each fixture is a real §34 fixture, verified mechanically by
tests/unit/test_xl_tasks_a.py (fail-as-shipped, pass-with-reference,
post-change pass, invalidates-exact — pytest in temp dirs, never a
model):

- xl-build-recdb (kind "build", ~79 hidden tests + 20 change tests)
      implement a small but real piece of software from a written spec:
      ``recdb``, a flat-file record database CLI — JSONL storage with
      atomic writes and corruption tolerance, a query expression
      language (operators, typing rules, reserved words), CSV/JSONL
      import/export with upsert, and an exact CLI exit-code contract.
      Empty-ish seed: the README spec + a package skeleton whose every
      command raises NotImplementedError.
- xl-boardline (kind "ui", ~47 hidden tests + 12 change tests)
      build a small web app from a spec + wireframe: ``boardline``, a
      team task board served by the Python stdlib — routes, server-side
      validation with an error region and preserved values, a JSON file
      store with atomic writes and restart persistence, and a pinned
      accessible page structure. Graded by a STDLIB harness (see below).

REQUIREMENT-CHANGE packs (the bench's mid-run perturbation, delivered
as a new user message at 35% of the wall budget): each fixture carries
``change = {"note", "hidden_tests", "invalidates", "patch", "guards"}``.
``note`` is the change ticket; ``hidden_tests`` encode the change;
``invalidates`` lists the ORIGINAL test ids the change legitimately REVERSES
(the post-change grade removes them; each one FAILS on the patched
reference — the reversal is real); ``patch`` is a FULL-FILE replacement map
{relpath: content} that updates the REFERENCE solution to the changed
requirements — applying it to a copy of the reference must pass the change
suite, and running the ORIGINAL suite against the patched reference must
fail EXACTLY ``invalidates`` (the list is neither over- nor under-specified;
the unit test pins this); ``guards`` lists the change tests that pin
UNCHANGED behavior (explicit ids stay opaque, the comma dialect stays the
default, the add form survives the search form, ...) — they are EXPECTED to
pass pre-patch; every OTHER change test must BITE on the unpatched
reference (the unified verify_fixture semantics, job xl-fix 2026-10-03).

- recdb change: string ids ``r0001`` + ``created_at`` on every record +
  ``or`` in the query language (REVERSES the integer auto ids and the
  reserved-word ``or`` — invalidates 4 original tests; 17/20 change tests
  bite pre-patch, 3 are unchanged-behavior guards).
- boardline change: points scale 0..8 -> 0..13 + title search (REVERSES the
  0..8 bound — invalidates 2 original tests; 8/12 change tests bite
  pre-patch, 4 are guards).

WHY A STDLIB HARNESS FOR THE UI FIXTURE (not Playwright): Playwright is
not in the repo lock (requirements-lock.txt) and is not vendorable as
an offline wheelhouse with browser binaries (~150 MB) — and the bench
runs on the Linux host inside bubblewrap with no network. The job
sanctions the fallback: the hidden suite drives the app over real HTTP
(``http.client`` against an in-process ``boardline.app.make_server`` on
an ephemeral port, plus one ``python -m boardline`` subprocess for the
CLI contract) and asserts on the served DOM through a small
``html.parser``-based checker — interactions, validation, state and
accessibility labels.

Content lives as real files under ``scripts/xl_seeds/<name>/`` (the job
sanctions a seed directory) and is loaded into the dict schema at
import: ``seed/`` (the workspace the agent gets), ``reference/`` (the
solved workspace, kept OUTSIDE the run), ``hidden/`` (the original
acceptance suite, kept OUTSIDE the workspace), ``change/`` (the change
suite), ``patch/`` (the reference patch). Hidden tests run as
``XL_WORKSPACE=<workspace> python -m pytest <suite dir>`` — each
suite's conftest.py resolves the workspace from that variable.

§34 caveat applies to any round on this set: author-built fixtures,
one model family — directional only.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

_SEEDS = Path(__file__).resolve().parent / "xl_seeds"

TASK_RECDB = (
    "Build `recdb` — a tiny flat-file record database CLI — to the spec in "
    "README.md, which is complete and normative: read it first. The repo ships "
    "only the spec and a package skeleton: every command is unimplemented. "
    "Implement the whole tool: the JSONL storage layer (atomic writes, "
    "corruption tolerance, duplicate handling), the query expression language "
    "(operators, typing rules, reserved words), CSV/JSONL import and export "
    "with upsert, and the CLI with its exact exit-code contract. Acceptance is "
    "an automated suite that drives the CLI end-to-end (`python -m recdb ...`) "
    "and pins the spec's behaviors — happy paths, edge cases and error "
    "handling — so follow the spec exactly where it is exact (exit codes, "
    "output shapes, typing rules, sort semantics). Python 3.11+ standard "
    "library only; keep the package importable as `recdb` and runnable as "
    "`python -m recdb`."
)

CHANGE_NOTE_RECDB = (
    "Requirement change (v2), effective immediately: (1) Auto-generated record "
    "ids are no longer integers — they are zero-padded strings: `r0001`, "
    "`r0002`, … (at least 4 digits; past 9999 they grow, e.g. `r10000`). "
    "Explicit `id=` values are unchanged (any string or integer is still "
    "allowed). Every command treats ids as opaque values. (2) Every record now "
    "carries `created_at`: the insert time as ISO 8601 UTC with second "
    "precision, e.g. `2026-10-03T09:00:00Z`. It is set on insert (`add`, and "
    "`import` of a NEW record), PRESERVED on `update` and on `import` upsert "
    "of an existing record, exported like any other field, and queryable like "
    "any other field. (3) The query language now supports `or` between terms, "
    "with LOWER precedence than the implicit and: `a=1 b=2 or c=3` means "
    "(a=1 AND b=2) OR c=3. Everything else in the spec is unchanged."
)

INVALIDATES_RECDB = [
    "test_add.py::test_add_auto_id_starts_at_1",
    "test_add.py::test_add_auto_id_increments",
    "test_add.py::test_add_record_exact_fields",
    "test_query.py::test_query_or_is_reserved_exit_2",
]

#: recdb change tests that pin UNCHANGED behavior (expected to pass pre-patch):
#: explicit ids stay opaque values, get/update/delete work with whatever add
#: returns, and a dangling operator stays a usage error.
GUARDS_RECDB = [
    "test_change_ids.py::test_change_explicit_ids_unchanged",
    "test_change_ids.py::test_change_get_update_delete_by_new_id",
    "test_change_or.py::test_change_dangling_or_exit_2",
]

TASK_BOARDLINE = (
    "Build `boardline` — a small team task-board web app — to the spec in "
    "README.md, which is complete and normative: read it first, including the "
    "wireframe and the exact page structure. The repo ships the spec and a "
    "package skeleton; implement the whole app: a standard-library HTTP server "
    "(no third-party dependencies), the JSON file store with atomic writes, the "
    "routes (GET /, POST /tasks, move, delete, /healthz), server-side "
    "validation with the error region and preserved values, and the exact "
    "accessibility structure the spec pins (labels, aria-labels, roles, heading "
    "hierarchy). Acceptance is an automated harness that drives the app over "
    "HTTP — it imports `boardline.app.make_server` and also runs the CLI — and "
    "asserts on the served HTML: interactions, validation, state and "
    "accessibility labels. The page structure section is normative: match "
    "those ids, classes and attributes exactly. Python 3.11+ standard library "
    "only."
)

CHANGE_NOTE_BOARDLINE = (
    "Requirement change (v2), effective immediately: (1) The points scale is "
    "extended: valid points are now integers 0..13 (was 0..8) — the add form's "
    "points input must allow up to 13. (2) The board gains title search: "
    "`GET /?q=<text>` shows only the tasks whose title contains the text "
    "(case-insensitive). The page gains a search form — "
    '`<form id="search" method="get" action="/">` with '
    '`<label for="search-box">Search</label>` and '
    '`<input id="search-box" name="q" type="search">` — above the '
    "columns, and when a query is active the search box shows the submitted "
    "text. Everything else in the spec is unchanged."
)

INVALIDATES_BOARDLINE = [
    "test_dom_structure.py::test_points_input_bounds",
    "test_validation.py::test_points_rejected_422",
]

#: boardline change tests that pin UNCHANGED behavior (expected to pass
#: pre-patch): 14 stays invalid, an empty query shows the whole board, and
#: the search form is ADDED next to the add form (which keeps working).
GUARDS_BOARDLINE = [
    "test_change_points13.py::test_change_points_14_rejected",
    "test_change_search.py::test_change_search_empty_shows_all",
    "test_change_search.py::test_change_search_keeps_add_form",
]


def _load_dir(root: Path) -> dict[str, str]:
    """Every file under root, as {relative posix path: content}."""
    if not root.is_dir():
        raise FileNotFoundError(f"missing fixture directory: {root}")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    if not files:
        raise FileNotFoundError(f"empty fixture directory: {root}")
    return files


def _fixture(
    name: str,
    kind: str,
    task: str,
    change_note: str,
    invalidates: list[str],
    expected_hours: float,
    guards: list[str],
) -> dict[str, Any]:
    """Assemble one fixture from scripts/xl_seeds/<name>/ into the dict schema."""
    base = _SEEDS / name
    return {
        "name": name,
        "kind": kind,
        "seed": _load_dir(base / "seed"),
        "task": task,
        "hidden_tests": _load_dir(base / "hidden"),
        "change": {
            "note": change_note,
            "hidden_tests": _load_dir(base / "change"),
            "invalidates": list(invalidates),
            # Full-file replacement map applied to a copy of the reference:
            # the post-change reference must pass the change suite.
            "patch": _load_dir(base / "patch"),
            # Change tests that pin UNCHANGED behavior (pass pre-patch);
            # every other change test must bite on the unpatched reference.
            "guards": list(guards),
        },
        "reference": _load_dir(base / "reference"),
        "expected_hours": expected_hours,
    }


TASKS: list[dict[str, Any]] = [
    _fixture(
        "xl-build-recdb",
        "build",
        TASK_RECDB,
        CHANGE_NOTE_RECDB,
        INVALIDATES_RECDB,
        2.5,
        GUARDS_RECDB,
    ),
    _fixture(
        "xl-boardline",
        "ui",
        TASK_BOARDLINE,
        CHANGE_NOTE_BOARDLINE,
        INVALIDATES_BOARDLINE,
        2.0,
        GUARDS_BOARDLINE,
    ),
]
