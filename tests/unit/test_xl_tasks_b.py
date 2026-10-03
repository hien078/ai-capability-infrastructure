"""The XL fixtures (builder b) are real fixtures, verified mechanically.

Every fixture must FAIL as shipped (the hidden suite over the seed),
PASS with the reference (the same suite over seed+reference), and the
POST-CHANGE suite (original minus invalidated, plus the change's tests)
must pass over seed+reference+patch. The change's tests must BITE on
the unpatched reference, and each invalidated test must FAIL on the
patched reference — otherwise the change pack is decoration. The
hidden tests must be unreachable from the seed, the task text must
reveal nothing, and every path must be safe and relative.

All of this runs the real hidden suites with subprocess pytest in temp
dirs — no DB, no network, no model (§34: the fixtures are author-built;
any bench round on them is directional only).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from xl_tasks_b import TASKS  # noqa: E402

PY = sys.executable
_TEST_DEF = re.compile(r"^def (test_\w+)\(", re.MULTILINE)
_ALLOWED_KINDS = {"build", "ui", "bug", "feature", "refactor", "research", "project"}
#: The bench's wall-budget window (the shared design: 1-3 hours per fixture).
_MIN_HOURS, _MAX_HOURS = 1.0, 3.0
#: Seed size window (the job spec: bug 2-5k lines; feature/refactor 1-5k,
#: accepted down to ~700 for a dense refactor seed).
_MIN_SEED_LINES = {"bug": 2000, "feature": 700, "refactor": 700}
_MAX_SEED_LINES = 5000
#: Partial credit needs enough tests to report a pass fraction.
_MIN_HIDDEN_TESTS = 40
_MIN_CHANGE_TESTS = 4


def _materialize(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _run_pytest(run_root: Path, args: list[str]) -> tuple[int, str]:
    """Run pytest in a materialized run root; returns (rc, combined output)."""
    env = {
        **os.environ,
        "PYTHONPATH": str(run_root / "ws"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "ACI_ROUTER_DISABLED": "1",
    }
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", "-rf", *args],
        cwd=str(run_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _failed_ids(out: str) -> set[str]:
    """FAILED node ids from a -q -rf run, normalized to <file>::<name>."""
    ids: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("FAILED "):
            nodeid = line.split(" ", 2)[1]
            path, _, name = nodeid.partition("::")
            if name:
                ids.add(f"{Path(path).name}::{name.partition('[')[0]}")
    return ids


def _change_ids(task: dict) -> set[str]:
    return {
        f"{Path(rel).name}::{name}"
        for rel, content in task["change"]["hidden_tests"].items()
        for name in _TEST_DEF.findall(content)
    }


def _scenario(task: dict, which: str) -> tuple[int, str]:
    """Materialize one fixture scenario and run its suite.

    seed            ws = seed
    reference       ws = seed + reference overlay
    postchange      ws = seed + reference + patch; original (minus
                    invalidated) + change tests
    change_unpatched ws = seed + reference; the change's tests only
    invalidated     ws = seed + reference + patch; one invalidated test
    """
    with tempfile.TemporaryDirectory(prefix="xl-b-") as tmp:
        run_root = Path(tmp)
        ws, hidden = run_root / "ws", run_root / "hidden"
        _materialize(ws, task["seed"])
        _materialize(hidden, task["hidden_tests"])
        if which in ("reference", "postchange", "invalidated", "change_unpatched"):
            _materialize(ws, task["reference"])
        if which in ("postchange", "invalidated"):
            _materialize(ws, task["change"]["patch"])
        if which == "postchange":
            change_hidden = run_root / "changehidden"
            _materialize(change_hidden, task["change"]["hidden_tests"])
            deselect = [f"--deselect=hidden/{tid}" for tid in task["change"]["invalidates"]]
            return _run_pytest(run_root, ["hidden", "changehidden", *deselect])
        if which == "change_unpatched":
            change_hidden = run_root / "changehidden"
            _materialize(change_hidden, task["change"]["hidden_tests"])
            return _run_pytest(run_root, ["changehidden"])
        if which == "invalidated":
            results = []
            for tid in task["change"]["invalidates"]:
                rc, out = _run_pytest(run_root, [f"hidden/{tid}"])
                results.append((tid, rc, out))
            bad = [(tid, out) for tid, rc, out in results if rc == 0]
            assert not bad, f"invalidated tests still pass on the patch: {bad}"
            return 0, ""
        return _run_pytest(run_root, ["hidden"])


def _test_names(files: dict[str, str]) -> set[str]:
    names: set[str] = set()
    for content in files.values():
        names.update(_TEST_DEF.findall(content))
    return names


def _all_paths(task: dict) -> list[str]:
    return (
        list(task["seed"])
        + list(task["hidden_tests"])
        + list(task["reference"])
        + list(task["change"]["patch"])
        + list(task["change"]["hidden_tests"])
    )


# ------------------------------------------------------------------- schema


class TestSchema:
    def test_the_three_assigned_fixtures_exist(self) -> None:
        assert [t["name"] for t in TASKS] == [
            "xl-ledger-bugfix",
            "xl-notes-search",
            "xl-jsondb-sqlite",
        ]

    def test_every_task_has_exactly_the_schema_keys(self) -> None:
        for task in TASKS:
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
            assert set(task["change"]) == {
                "note",
                "hidden_tests",
                "patch",
                "invalidates",
                "guards",
            }, task["name"]

    def test_guards_are_real_change_tests(self) -> None:
        for task in TASKS:
            for guard in task["change"]["guards"]:
                assert guard in _change_ids(task), (task["name"], guard)

    def test_kinds_names_and_hours(self) -> None:
        for task in TASKS:
            assert task["kind"] in _ALLOWED_KINDS, task["name"]
            assert task["name"].startswith("xl-"), task["name"]
            assert isinstance(task["expected_hours"], float)
            assert _MIN_HOURS <= task["expected_hours"] <= _MAX_HOURS, task["name"]

    def test_every_dictionary_is_non_empty(self) -> None:
        for task in TASKS:
            assert task["seed"], task["name"]
            assert task["hidden_tests"], task["name"]
            assert task["reference"], task["name"]
            assert task["change"]["patch"], task["name"]
            assert task["change"]["hidden_tests"], task["name"]
            assert task["change"]["invalidates"], task["name"]
            assert task["task"].strip(), task["name"]
            assert task["change"]["note"].strip(), task["name"]

    def test_invalidates_ids_point_at_real_hidden_tests(self) -> None:
        for task in TASKS:
            for tid in task["change"]["invalidates"]:
                assert re.match(r"^[\w/]+\.py::test_\w+$", tid), (task["name"], tid)
                file_name, test_name = tid.split("::", 1)
                assert file_name in task["hidden_tests"], (task["name"], tid)
                assert f"def {test_name}(" in task["hidden_tests"][file_name], (
                    task["name"],
                    tid,
                )

    def test_the_patch_only_edits_files_that_exist(self) -> None:
        for task in TASKS:
            known = set(task["seed"]) | set(task["reference"])
            for path in task["change"]["patch"]:
                assert path in known, (task["name"], path)


class TestPathsAndSecrecy:
    def test_every_path_is_safe_and_relative(self) -> None:
        for task in TASKS:
            for path in _all_paths(task):
                assert not path.startswith("/"), (task["name"], path)
                assert "\\" not in path, (task["name"], path)
                assert ".." not in Path(path).parts, (task["name"], path)
                assert not Path(path).is_absolute(), (task["name"], path)
                assert path.strip() == path and path != "", (task["name"], path)

    def test_no_hidden_test_is_a_seed_file(self) -> None:
        for task in TASKS:
            for path in task["hidden_tests"]:
                assert path not in task["seed"], (task["name"], path)
            for path in task["change"]["hidden_tests"]:
                assert path not in task["seed"], (task["name"], path)

    def test_no_hidden_test_name_appears_in_the_seed(self) -> None:
        for task in TASKS:
            names = _test_names(task["hidden_tests"]) | _test_names(task["change"]["hidden_tests"])
            assert names, task["name"]
            for path, content in task["seed"].items():
                for name in names:
                    assert name not in content, (task["name"], path, name)

    def test_the_task_text_reveals_no_hidden_test(self) -> None:
        for task in TASKS:
            names = _test_names(task["hidden_tests"]) | _test_names(task["change"]["hidden_tests"])
            for path in list(task["hidden_tests"]) + list(task["change"]["hidden_tests"]):
                assert Path(path).name not in task["task"], (task["name"], path)
            for name in names:
                assert name not in task["task"], (task["name"], name)
                assert name not in task["change"]["note"], (task["name"], name)


class TestSizes:
    def test_seed_sizes_are_in_the_spec_window(self) -> None:
        for task in TASKS:
            lines = sum(content.count("\n") + 1 for content in task["seed"].values())
            assert lines >= _MIN_SEED_LINES[task["kind"]], (task["name"], lines)
            assert lines <= _MAX_SEED_LINES, (task["name"], lines)

    def test_hidden_suites_are_sized_for_partial_credit(self) -> None:
        for task in TASKS:
            assert len(_test_names(task["hidden_tests"])) >= _MIN_HIDDEN_TESTS, task["name"]
            assert len(_test_names(task["change"]["hidden_tests"])) >= _MIN_CHANGE_TESTS, task[
                "name"
            ]


# ------------------------------------------------------- the fixture matrix


class TestFixtureMatrix:
    def test_fail_as_shipped(self) -> None:
        """The hidden suite over the bare seed must FAIL."""
        for task in TASKS:
            rc, out = _scenario(task, "seed")
            assert rc != 0, f"{task['name']}: shipped state PASSES (not a fixture)"

    def test_pass_with_reference(self) -> None:
        """The hidden suite over seed+reference must PASS completely."""
        for task in TASKS:
            rc, out = _scenario(task, "reference")
            assert rc == 0, f"{task['name']}: reference still fails:\n{out[-800:]}"

    def test_post_change_suite_with_reference_and_patch(self) -> None:
        """Original (minus invalidated) + change tests over seed+reference+patch."""
        for task in TASKS:
            rc, out = _scenario(task, "postchange")
            assert rc == 0, f"{task['name']}: post-change suite fails:\n{out[-800:]}"

    def test_the_change_tests_bite_on_the_unpatched_reference(self) -> None:
        """Without the patch the change's tests must FAIL (the pack is real)."""
        for task in TASKS:
            rc, out = _scenario(task, "change_unpatched")
            assert rc != 0, f"{task['name']}: change tests pass without the patch"

    def test_every_non_guard_change_test_bites(self) -> None:
        """The unified semantics, PER TEST (job xl-fix item 3): every change
        test that is not a declared guard must FAIL on the unpatched
        reference — a change test that passes pre-patch measures nothing
        about the change. (RED when written: the jsondb savepoint tests
        passed pre-patch coincidentally — v1's "nested transaction" entry
        raise satisfied their bare pytest.raises — fixed with match=.) The
        guards (unchanged-behavior pins) pass pre-patch by declaration."""
        for task in TASKS:
            rc, out = _scenario(task, "change_unpatched")
            assert rc != 0, task["name"]
            failed = _failed_ids(out)
            change_ids = _change_ids(task)
            guards = set(task["change"]["guards"])
            not_biting = sorted(change_ids - guards - failed)
            assert not not_biting, (
                f"{task['name']}: change tests that do not bite pre-patch "
                f"and are not guards: {not_biting}\n{out[-800:]}"
            )
            assert guards <= (change_ids - failed), (
                f"{task['name']}: declared guards do not pass pre-patch: {sorted(guards & failed)}"
            )

    def test_invalidated_tests_fail_on_the_patched_reference(self) -> None:
        """Each invalidated test pinned pre-change behavior — it must now fail."""
        for task in TASKS:
            _scenario(task, "invalidated")
