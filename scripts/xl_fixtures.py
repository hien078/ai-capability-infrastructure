"""The XL fixture AGGREGATOR — all 7 fixtures, one materializer (2026-10-03).

The three builders ship ``TASKS`` as Python dicts (``scripts/xl_tasks_{a,b,c}.py``);
the bench (``scripts/xl_bench.py``) consumes ON-DISK fixture dirs. This module
is the one bridge: it imports every builder's TASKS, checks the UNIFIED schema
(one change semantics for all 7 fixtures — job xl-fix), and materializes each
fixture into the exact layout ``xl_bench.load_fixture`` reads::

    <out>/<name>/workspace/<relpath>    the seed (copied into a run)
    <out>/<name>/TASK.md                the task text given to the orchestrator
    <out>/<name>/hidden/<relpath>       the ORIGINAL hidden suite (post-hoc only)
    <out>/<name>/change/CHANGE_NOTE.md   sent as the 35% perturbation message
    <out>/<name>/change/hidden/<relpath>  the extra tests encoding the change
    <out>/<name>/change/invalidates.txt   original tests the change RETIRES
    <out>/<name>/change/patch/<relpath>   the reference's change implementation
    <out>/<name>/change/guards.txt        change tests expected to pass pre-patch
    <out>/<name>/reference/<relpath>     the PRE-change reference solution

Usage::

    .venv/bin/python scripts/xl_fixtures.py --out data/xl-bench/fixtures
    .venv/bin/python scripts/xl_bench.py verify-fixture data/xl-bench/fixtures/<name>

No model round, no product code, no DB — pure file materialization.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from xl_tasks_a import TASKS as TASKS_A  # noqa: E402
from xl_tasks_b import TASKS as TASKS_B  # noqa: E402
from xl_tasks_c import TASKS as TASKS_C  # noqa: E402

#: All 7 fixtures, one per kind: build, ui, bug, feature, refactor, research, project.
TASKS: list[dict[str, Any]] = [*TASKS_A, *TASKS_B, *TASKS_C]

#: The unified change-pack schema (identical for every fixture).
CHANGE_KEYS = {"note", "hidden_tests", "invalidates", "patch", "guards"}


def check_schema(tasks: list[dict[str, Any]]) -> None:
    """The UNIFIED schema, enforced before anything is written: one change
    semantics for all 7 fixtures — every pack carries a patch (the
    reference's own change implementation) and declares its guards."""
    names = [task["name"] for task in tasks]
    assert len(names) == len(set(names)) == 7, names
    for task in tasks:
        assert set(task) == {
            "name",
            "kind",
            "seed",
            "task",
            "hidden_tests",
            "change",
            "reference",
            "expected_hours",
        }, task["name"]
        assert set(task["change"]) == CHANGE_KEYS, (task["name"], sorted(task["change"]))
        assert task["change"]["patch"], f"{task['name']}: no change patch"
        assert task["change"]["invalidates"], f"{task['name']}: no invalidates"
        known = set(task["seed"]) | set(task["reference"])
        for rel in task["change"]["patch"]:
            assert rel in known, f"{task['name']}: patch targets an unknown file {rel}"
        for rel in task["change"]["invalidates"]:
            assert rel.rsplit("::", 1)[0] in task["hidden_tests"], (
                f"{task['name']}: invalidates a non-original test {rel}"
            )


def write_all(tasks: list[dict[str, Any]], out_root: Path) -> list[Path]:
    """Materialize every fixture under ``out_root`` (the xl_bench layout)."""
    from xl_tasks_c import write_fixture  # noqa: PLC0415 — the shared materializer

    check_schema(tasks)
    out_root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for task in tasks:
        written.append(write_fixture(task, out_root / task["name"]))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        default=str(_HERE.parent / "data/xl-bench/fixtures"),
        help="output root (default: <repo>/data/xl-bench/fixtures)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="only run the unified schema check, write nothing",
    )
    args = parser.parse_args(argv)
    if args.check_only:
        check_schema(TASKS)
        print(f"schema OK: {[task['name'] for task in TASKS]}")
        return 0
    written = write_all(TASKS, Path(args.out))
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
