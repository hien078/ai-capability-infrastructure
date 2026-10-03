"""The XL fixture AGGREGATOR (scripts/xl_fixtures.py) — all 7 fixtures, one
materializer, one schema. Cheap checks only (no hidden-suite pytest runs —
those live in the builders' own tests and in `xl_bench.py verify-fixture`,
which the operator runs on the materialized dirs): the unified change-pack
schema, the on-disk layout the harness consumes, and the load_fixture
round-trip.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import xl_bench as xb  # noqa: E402
import xl_fixtures as xlf  # noqa: E402

EXPECTED = [
    "xl-build-recdb",
    "xl-boardline",
    "xl-ledger-bugfix",
    "xl-notes-search",
    "xl-jsondb-sqlite",
    "xl-research-cinderpeak",
    "xl-project-postbox",
]
EXPECTED_KINDS = ["build", "ui", "bug", "feature", "refactor", "research", "project"]


def _test_ids(files: dict[str, str]) -> set[str]:
    return {
        f"{Path(rel).name}::{name}"
        for rel, content in files.items()
        for name in re.findall(r"^def (test_\w+)", content, re.M)
    }


class TestUnifiedSchema:
    def test_all_seven_fixtures_one_per_kind(self) -> None:
        assert [t["name"] for t in xlf.TASKS] == EXPECTED
        assert [t["kind"] for t in xlf.TASKS] == EXPECTED_KINDS

    def test_every_change_pack_carries_a_patch_and_declares_its_guards(self) -> None:
        """The unified semantics: one change pack schema for all 7 —
        note + hidden_tests + invalidates + patch + guards."""
        for task in xlf.TASKS:
            assert set(task["change"]) == xlf.CHANGE_KEYS, task["name"]
            assert task["change"]["patch"], task["name"]
            assert task["change"]["invalidates"], task["name"]
            assert isinstance(task["change"]["guards"], list), task["name"]

    def test_patch_targets_known_files(self) -> None:
        for task in xlf.TASKS:
            known = set(task["seed"]) | set(task["reference"])
            for rel in task["change"]["patch"]:
                assert rel in known, (task["name"], rel)

    def test_invalidates_are_original_tests(self) -> None:
        for task in xlf.TASKS:
            for entry in task["change"]["invalidates"]:
                file_name = entry.rsplit("::", 1)[0]
                assert file_name in task["hidden_tests"], (task["name"], entry)
                if "::" in entry:
                    name = entry.split("::", 1)[1]
                    assert f"def {name}(" in task["hidden_tests"][file_name], (
                        task["name"],
                        entry,
                    )


class TestMaterialization:
    def test_write_all_produces_the_harness_layout(self, tmp_path: Path) -> None:
        """write_all materializes exactly what xl_bench.load_fixture reads —
        and the loaded Fixture carries the invalidates + guards."""
        written = xlf.write_all(xlf.TASKS, tmp_path / "fixtures")
        assert [p.name for p in written] == EXPECTED
        for task in xlf.TASKS:
            fixture = xb.load_fixture(tmp_path / "fixtures" / task["name"])
            assert fixture.name == task["name"]
            assert fixture.perturbed
            assert fixture.change_note == task["change"]["note"]
            assert fixture.invalidated == task["change"]["invalidates"]
            assert fixture.guards == task["change"]["guards"]
            assert (fixture.dir / "workspace").is_dir()
            assert (fixture.dir / "hidden").is_dir()
            assert (fixture.dir / "change" / "hidden").is_dir()
            assert (fixture.dir / "change" / "patch").is_dir()
            assert (fixture.dir / "reference").is_dir()
            assert (fixture.dir / "TASK.md").read_text(encoding="utf-8") == task["task"]

    def test_guards_are_real_change_tests(self) -> None:
        for task in xlf.TASKS:
            change_ids = _test_ids(task["change"]["hidden_tests"])
            for guard in task["change"]["guards"]:
                assert guard in change_ids, (task["name"], guard)

    def test_check_only_writes_nothing(self, tmp_path: Path) -> None:
        assert xlf.main(["--check-only", "--out", str(tmp_path / "nope")]) == 0
        assert not (tmp_path / "nope").exists()
