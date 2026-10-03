"""The XL-orchestration fixtures (builder a) are real fixtures (§34): each
must FAIL in its shipped state and PASS with the reference solution —
and the REQUIREMENT-CHANGE pack must be honest: the reference + its
change patch passes the post-change suite, while the ORIGINAL suite
against the patched reference fails EXACTLY the listed ``invalidates``
ids (neither over- nor under-specified). All verified mechanically —
pytest in temp dirs through the fixtures' own conftest contract
(``XL_WORKSPACE``), no network, no DB, no model.

Also pins the fixture hygiene the job requires: hidden tests absent
from the seed, the task/change texts reveal no hidden test, safe
relative paths everywhere, and the hidden-suite sizes inside the
job's budget (build 40–80).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from xl_tasks_a import TASKS  # noqa: E402

PYTEST_ARGS = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no"]


def _materialize(files: dict[str, str], root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def _apply_patch(workspace: Path, patch: dict[str, str]) -> None:
    """The change patch is a full-file replacement map over the reference."""
    for rel, content in patch.items():
        target = workspace / rel
        assert target.is_file(), f"patch targets a non-reference file: {rel}"
        target.write_text(content, encoding="utf-8")


def _failed_ids(out: str) -> set[str]:
    """FAILED/ERROR nodeids from a -q run, normalized to <file>::<name>."""
    ids: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        for prefix in ("FAILED ", "ERROR "):
            if line.startswith(prefix):
                nodeid = line[len(prefix) :].split()[0]
                path, _, name = nodeid.partition("::")
                if name:
                    ids.add(f"{Path(path).name}::{name}")
                break
    return ids


def _run(suite_dir: Path, workspace: Path) -> tuple[int, set[str], str]:
    env = {**os.environ, "XL_WORKSPACE": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [*PYTEST_ARGS, str(suite_dir)],
        cwd=str(suite_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, _failed_ids(out), out


def _collect_count(suite_dir: Path, workspace: Path) -> int:
    env = {**os.environ, "XL_WORKSPACE": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(suite_dir),
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(suite_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    match = re.search(r"(\d+) tests? collected", out)
    assert match, f"no collect count in:\n{out[-500:]}"
    return int(match.group(1))


def _test_names(content: str) -> list[str]:
    return re.findall(r"^def (test_\w+)", content, re.MULTILINE)


@pytest.fixture(scope="module")
def arenas(tmp_path_factory: Any) -> dict[str, dict[str, Path]]:
    """Materialize every fixture once: workspaces + suite dirs (module scope).

    The hidden suites never write into the workspaces (recdb runs the CLI
    with cwd=tmp run dirs; boardline writes to tmp data paths), so the
    materialized dirs are reusable across the verification runs below.
    """
    out: dict[str, dict[str, Path]] = {}
    for task in TASKS:
        root = tmp_path_factory.mktemp(task["name"])
        patched = _materialize(task["reference"], root / "patched")
        _apply_patch(patched, task["change"]["patch"])
        out[task["name"]] = {
            "seed": _materialize(task["seed"], root / "seed"),
            "reference": _materialize(task["reference"], root / "reference"),
            "patched": patched,
            "hidden": _materialize(task["hidden_tests"], root / "hidden"),
            "change": _materialize(task["change"]["hidden_tests"], root / "change"),
        }
    return out


class TestFixtureSchema:
    def test_two_fixtures_with_the_assigned_kinds(self) -> None:
        assert [t["name"] for t in TASKS] == ["xl-build-recdb", "xl-boardline"]
        assert [t["kind"] for t in TASKS] == ["build", "ui"]

    def test_required_keys_and_types(self) -> None:
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
            }
            assert set(task["change"]) == {
                "note",
                "hidden_tests",
                "invalidates",
                "patch",
                "guards",
            }
            assert isinstance(task["task"], str) and task["task"]
            assert isinstance(task["change"]["note"], str)
            assert len(task["change"]["note"]) >= 200
            assert isinstance(task["expected_hours"], float)
            assert 1.0 <= task["expected_hours"] <= 3.0

    def test_paths_are_safe_and_relative(self) -> None:
        for task in TASKS:
            blobs = [
                task["seed"],
                task["hidden_tests"],
                task["reference"],
                task["change"]["hidden_tests"],
                task["change"]["patch"],
            ]
            for blob in blobs:
                assert blob
                for rel in blob:
                    assert not rel.startswith("/"), f"{task['name']}: absolute {rel}"
                    assert ".." not in rel, f"{task['name']}: traversal in {rel}"
                    assert "\\" not in rel, f"{task['name']}: backslash in {rel}"
                    assert not Path(rel).is_absolute(), f"{task['name']}: absolute {rel}"

    def test_hidden_tests_absent_from_the_seed(self) -> None:
        """No hidden test file name or test function ships in the seed."""
        for task in TASKS:
            hidden_names = {Path(rel).name for rel in task["hidden_tests"]}
            hidden_names |= {Path(rel).name for rel in task["change"]["hidden_tests"]}
            for rel, content in task["seed"].items():
                assert Path(rel).name not in hidden_names, f"{task['name']}: {rel} is a test"
                for hidden_content in task["hidden_tests"].values():
                    for name in _test_names(hidden_content):
                        assert f"def {name}" not in content, (
                            f"{task['name']}: seed {rel} leaks hidden test {name}"
                        )

    def test_task_and_change_texts_reveal_no_hidden_test(self) -> None:
        for task in TASKS:
            texts = task["task"] + task["change"]["note"]
            for suite in (task["hidden_tests"], task["change"]["hidden_tests"]):
                for rel, content in suite.items():
                    assert Path(rel).name not in texts, f"{task['name']}: text leaks {rel}"
                    for name in _test_names(content):
                        assert name not in texts, f"{task['name']}: text leaks {name}"

    def test_seed_is_not_the_reference(self) -> None:
        for task in TASKS:
            assert task["seed"] != task["reference"], task["name"]

    def test_patch_targets_reference_files(self) -> None:
        for task in TASKS:
            for rel in task["change"]["patch"]:
                assert rel in task["reference"], f"{task['name']}: patch target {rel}"

    def test_invalidates_are_original_test_ids(self) -> None:
        for task in TASKS:
            original: set[str] = set()
            for rel, content in task["hidden_tests"].items():
                if rel.endswith(".py"):
                    original |= {f"{Path(rel).name}::{name}" for name in _test_names(content)}
            assert task["change"]["invalidates"], task["name"]
            assert set(task["change"]["invalidates"]) <= original, task["name"]

    def test_guards_are_real_change_tests(self) -> None:
        """Every declared guard is a real change-test id (the unified
        semantics: guards are the ONLY change tests allowed to pass
        pre-patch)."""
        for task in TASKS:
            change_ids: set[str] = set()
            for rel, content in task["change"]["hidden_tests"].items():
                change_ids |= {f"{Path(rel).name}::{name}" for name in _test_names(content)}
            assert set(task["change"]["guards"]) <= change_ids, task["name"]


class TestFixtureVerification:
    def test_fail_as_shipped(self, arenas: dict[str, dict[str, Path]]) -> None:
        """The shipped seed fails the WHOLE original suite — every collected
        test fails or errors (a fixture test that passes as shipped measures
        nothing)."""
        for task in TASKS:
            ws = arenas[task["name"]]
            returncode, failed, out = _run(ws["hidden"], ws["seed"])
            assert returncode != 0, f"{task['name']}: shipped state PASSES (not a fixture)"
            collected = _collect_count(ws["hidden"], ws["seed"])
            assert len(failed) == collected, (
                f"{task['name']}: {collected - len(failed)}/{collected} tests pass as shipped"
            )

    def test_pass_with_reference(self, arenas: dict[str, dict[str, Path]]) -> None:
        """The reference solution passes the whole original suite."""
        for task in TASKS:
            ws = arenas[task["name"]]
            returncode, _, out = _run(ws["hidden"], ws["reference"])
            assert returncode == 0, f"{task['name']}:\n{out[-2000:]}"

    def test_change_suite_passes_with_patched_reference(
        self, arenas: dict[str, dict[str, Path]]
    ) -> None:
        """reference + change patch passes the whole post-change suite."""
        for task in TASKS:
            ws = arenas[task["name"]]
            returncode, _, out = _run(ws["change"], ws["patched"])
            assert returncode == 0, f"{task['name']} (change):\n{out[-2000:]}"

    def test_invalidates_is_exact(self, arenas: dict[str, dict[str, Path]]) -> None:
        """The original suite against the PATCHED reference fails EXACTLY the
        listed invalidates ids — the change breaks what it claims to break,
        nothing more (the rest of the spec still holds)."""
        for task in TASKS:
            ws = arenas[task["name"]]
            returncode, failed, out = _run(ws["hidden"], ws["patched"])
            expected = set(task["change"]["invalidates"])
            assert returncode != 0, f"{task['name']}: change broke nothing?"
            assert failed == expected, (
                f"{task['name']}: unexpected failures {sorted(failed - expected)}, "
                f"missing {sorted(expected - failed)}\n{out[-1500:]}"
            )


class TestChangeBite:
    def test_change_tests_bite_on_the_unpatched_reference(
        self, arenas: dict[str, dict[str, Path]]
    ) -> None:
        """The UNIFIED change semantics (job xl-fix): every change test that
        is NOT a declared guard FAILS on the unpatched reference (the pack
        is real — a change test that passes pre-patch measures nothing),
        and the guards (unchanged-behavior pins) pass pre-patch."""
        for task in TASKS:
            ws = arenas[task["name"]]
            returncode, failed, out = _run(ws["change"], ws["reference"])
            change_ids: set[str] = set()
            for rel, content in task["change"]["hidden_tests"].items():
                change_ids |= {f"{Path(rel).name}::{name}" for name in _test_names(content)}
            guards = set(task["change"]["guards"])
            assert returncode != 0, f"{task['name']}: change tests pass pre-patch"
            not_biting = sorted(change_ids - guards - failed)
            assert not not_biting, (
                f"{task['name']}: change tests that do not bite pre-patch "
                f"(and are not guards): {not_biting}\n{out[-800:]}"
            )
            assert guards <= (change_ids - failed), (
                f"{task['name']}: declared guards do not pass pre-patch: {sorted(guards & failed)}"
            )


class TestHiddenSuiteSizes:
    def test_suites_are_inside_the_budget(self, arenas: dict[str, dict[str, Path]]) -> None:
        """build: 40-80 hidden tests (the job's budget); ui: a real but small
        suite; every change suite is big enough to be a meaningful perturbation."""
        for task in TASKS:
            ws = arenas[task["name"]]
            change_count = _collect_count(ws["change"], ws["patched"])
            assert change_count >= 8, f"{task['name']}: change suite only {change_count}"
        sizes = {}
        for task in TASKS:
            ws = arenas[task["name"]]
            sizes[task["name"]] = _collect_count(ws["hidden"], ws["reference"])
        assert 40 <= sizes["xl-build-recdb"] <= 80, sizes
        assert 30 <= sizes["xl-boardline"] <= 80, sizes
