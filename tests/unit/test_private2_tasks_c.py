"""Builder C's private2 fixtures are real fixtures (§34): each must FAIL in
its shipped state and PASS after the known root-cause fix — verified here
mechanically through the real verifier path (pytest in a temp dir), no
network, no DB, no model. Also pins the E2C KNOWLEDGE GATE: the rules are
arbitrary (invented with the fixture, so no model can carry them),
documented ONLY in the private skill, and not derivable from the tests
(grouped sha256 digest pins over multi-decision traces, never plaintext),
the shipped code (a plausible-but-wrong public convention) or the task
prompt (assigned directness: named / indirect).
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify_private_fixtures as vpf  # noqa: E402
from private2_tasks_c import FIXES, TASKS  # noqa: E402

from aci.providers.skills.parser import parse_skill_md  # noqa: E402

_HEX64 = re.compile(r"\b[0-9a-f]{64}\b")


def _pytest(task_dir: Path) -> tuple[int, str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=task_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _check_fixture(task: dict) -> tuple[bool, str]:
    """The §34 discipline for ONE fixture: shipped state FAILS (the
    fixture is actually buggy), fixed state PASSES (the known root-cause
    fix is a real fix). Returns (ok, detail)."""
    with tempfile.TemporaryDirectory() as tmp:
        task_dir = vpf.materialize(task, Path(tmp))
        code, _ = _pytest(task_dir)
        if code == 0:
            return False, "shipped state PASSES (not a fixture)"
        vpf.apply_fix(task_dir, FIXES[task["name"]])
        code, out = _pytest(task_dir)
        if code != 0:
            return False, f"FIXED state still fails:\n{out[-400:]}"
    return True, ""


class TestFixtureVerification:
    def test_every_fixture_has_a_fix(self) -> None:
        for task in TASKS:
            assert task["name"] in FIXES, task["name"]

    def test_fixtures_fail_as_shipped_and_pass_after_the_fix(self) -> None:
        for task in TASKS:
            ok, detail = _check_fixture(task)
            assert ok, f"{task['name']}: {detail}"

    def test_the_check_catches_a_fixture_that_passes_as_shipped(self) -> None:
        """A fixture whose shipped state already passes is NOT a fixture —
        _check_fixture must say so, not bless it (ship the FIXED module as
        the "buggy" state: nothing left to fix)."""
        task = next(t for t in TASKS if t["name"] == "private2-vellum-order-id")
        fixed_module = FIXES[task["name"]][0][2]
        pre_fixed = {
            "name": task["name"],
            "files": {
                **{rel: c for rel, c in task["files"].items() if rel != "order_ids.py"},
                "order_ids.py": fixed_module,
            },
        }
        ok, detail = _check_fixture(pre_fixed)
        assert not ok, "a pre-fixed workspace must be caught as not a fixture"
        assert "shipped state PASSES" in detail

    def test_fix_steps_target_workspace_files(self) -> None:
        for task in TASKS:
            for fix_file, old, _ in FIXES[task["name"]]:
                assert fix_file in task["files"], (task["name"], fix_file)
                if old is not None:
                    assert old in task["files"][fix_file], (task["name"], fix_file)


class TestKnowledgeGate:
    def test_markers_live_only_in_the_skill(self) -> None:
        """The private standard's vocabulary appears in the skill and NOWHERE
        else: not in the tests (digest-pinned, never plaintext — the test
        must not become a copy of the standard), not in the shipped code (a
        wrong convention that must not leak the real vocabulary)."""
        for task in TASKS:
            assert 4 <= len(task["markers"]) <= 8, task["name"]
            for marker in task["markers"]:
                assert marker in task["skill"], f"{task['name']}: skill lacks {marker!r}"
                for rel, content in task["files"].items():
                    assert marker not in content, (
                        f"{task['name']}: {rel} leaks the private marker {marker!r}"
                    )

    def test_prompts_do_not_reveal_the_rules(self) -> None:
        """The prompt says a standard exists — never what it says: not the
        skill id, not any private marker."""
        for task in TASKS:
            prompt = task["prompt"].lower()
            assert task["skill_id"] not in prompt, task["name"]
            assert task["skill_id"].replace("-", " ") not in prompt, task["name"]
            for marker in task["markers"]:
                assert marker.lower() not in prompt, f"{task['name']}: prompt leaks {marker!r}"

    def test_the_rules_are_genuinely_private(self) -> None:
        """The fixtures' knowledge did not exist before today: each skill
        names a fictional internal standard (invented with the fixture) —
        pin the fiction so a future edit cannot quietly swap in a real,
        possibly-public convention."""
        by_name = {t["name"]: t for t in TASKS}
        vellum = by_name["private2-vellum-order-id"]["skill"]
        assert "Vellum Order Identifier Standard" in vellum
        assert "not published" in vellum
        ferry = by_name["private2-queue-consumer-retry"]["skill"]
        assert "Ferry Consumer Retry Policy" in ferry
        assert "not published" in ferry
        for task in TASKS:
            assert "internal" in task["skill"].lower(), task["name"]


class TestDirectness:
    def test_directness_is_assigned_per_fixture(self) -> None:
        by_name = {t["name"]: t for t in TASKS}
        assert by_name["private2-vellum-order-id"]["directness"] == "named"
        assert by_name["private2-queue-consumer-retry"]["directness"] == "indirect"
        for task in TASKS:
            assert task["directness"] in ("named", "indirect"), task["name"]

    def test_named_prompt_names_the_standard(self) -> None:
        named = next(t for t in TASKS if t["directness"] == "named")
        assert "vellum" in named["prompt"].lower()

    def test_indirect_prompt_names_neither_the_standard_nor_its_title(self) -> None:
        """Indirect: the prompt says only "our internal policy" — it must not
        name the standard, its platform or its title words."""
        indirect = next(t for t in TASKS if t["directness"] == "indirect")
        prompt = indirect["prompt"].lower()
        title = "ferry consumer retry policy"
        assert title not in prompt
        assert title.replace(" ", "-") not in prompt
        assert "ferry" not in prompt


class TestSkillContract:
    def test_skill_is_never_a_workspace_file(self) -> None:
        """The skill is fixture metadata, NEVER a workspace file — the model
        must not see it on disk; the capability plane serves it."""
        for task in TASKS:
            assert task["skill_id"] and task["skill"], task["name"]
            assert task["skill_id"] not in task["files"], task["name"]
            assert "SKILL.md" not in task["files"], task["name"]
            assert any(rel.startswith("test_") for rel in task["files"]), task["name"]

    def test_skill_frontmatter_parses_through_the_real_parser(self) -> None:
        for task in TASKS:
            meta = parse_skill_md(task["skill"])
            assert meta.name == task["skill_id"], task["name"]
            assert meta.description.strip(), task["name"]
            assert meta.version == "1.0.0", task["name"]

    def test_skill_fits_the_context_budget(self) -> None:
        """≤ ~1,500 tokens estimated as len/4 — the skill must fit a
        preloaded context slice without truncation."""
        for task in TASKS:
            assert len(task["skill"]) <= 6000, task["name"]

    def test_fixture_paths_are_safe_and_relative(self) -> None:
        for task in TASKS:
            for rel in task["files"]:
                assert not rel.startswith("/"), f"{task['name']}: absolute path {rel}"
                assert ".." not in rel, f"{task['name']}: traversal in {rel}"

    def test_tests_pin_grouped_digests_not_plaintext(self) -> None:
        """The expected behavior is pinned as sha256 DIGESTS over grouped
        multi-decision traces (the E2B §1.5 meridian lesson: small digest
        spaces are brute-forceable in principle) — never plaintext rules,
        never readable expected values."""
        for task in TASKS:
            test_files = [c for rel, c in task["files"].items() if rel.startswith("test_")]
            assert len(test_files) == 1, task["name"]
            content = test_files[0]
            assert "hashlib.sha256" in content, task["name"]
            assert "DIGESTS" in content, task["name"]
            assert len(_HEX64.findall(content)) >= 2, task["name"]
