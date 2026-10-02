"""The E2C private-knowledge fixtures (builder "a") are real fixtures
(§34): each must FAIL in its shipped state and PASS after the known
root-cause fix - verified here mechanically, no network, no DB, no
model (pytest in a temp dir through the same verifier helpers the
private-fixtures tests use). Also pins the E2C KNOWLEDGE GATE: the
rules are invented with the fixture (no model can carry them),
documented ONLY in the fixture's private skill, and not derivable from
the tests (digest-pinned, never plaintext), the shipped code (a
plausible but WRONG convention that must not leak the real vocabulary)
or the task prompt - whose DIRECTNESS is assigned per fixture ("named"
names the standard, "indirect" only says an internal policy exists).
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
from private2_tasks_a import FIXES, TASKS  # noqa: E402

#: The job's assigned fixtures: name -> (directness, skill_id).
ASSIGNED: dict[str, tuple[str, str]] = {
    "private2-cairn-money": ("named", "cairn-money-standard"),
    "private2-drawbridge-rollout": ("indirect", "drawbridge-rollout-standard"),
}


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


class TestFixtureVerification:
    def test_the_assigned_fixtures_are_present(self) -> None:
        assert {t["name"]: (t["directness"], t["skill_id"]) for t in TASKS} == ASSIGNED

    def test_every_fixture_has_a_fix(self) -> None:
        for task in TASKS:
            assert task["name"] in FIXES, task["name"]

    def test_fixes_target_workspace_sources_never_tests(self) -> None:
        for task in TASKS:
            for fix_file, old, _new in FIXES[task["name"]]:
                assert fix_file in task["files"], (task["name"], fix_file)
                assert not fix_file.startswith("test_"), (task["name"], fix_file)
                assert old is None, (task["name"], fix_file)

    def test_fixtures_fail_as_shipped_and_pass_after_the_fix(self) -> None:
        """The §34 discipline, per fixture: shipped state FAILS (the
        fixture is actually buggy), fixed state PASSES (the known
        root-cause fix is a real fix). Runs the real verifier path the
        existing private-fixtures tests use - pytest in a temp dir, NOT
        the model."""
        for task in TASKS:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = vpf.materialize(task, Path(tmp))
                code, _ = _pytest(task_dir)
                assert code != 0, f"{task['name']}: shipped state PASSES (not a fixture)"
                vpf.apply_fix(task_dir, FIXES[task["name"]])
                code, out = _pytest(task_dir)
                assert code == 0, f"{task['name']}: FIXED state still fails:\n{out[-400:]}"


class TestKnowledgeGate:
    def test_the_rules_live_only_in_the_skill(self) -> None:
        """The private standard's vocabulary (its markers) appears in the
        skill and NOWHERE else: not in the tests (digest-pinned, never
        plaintext - the test must not become a copy of the standard),
        not in the shipped code (a wrong policy that must not leak the
        real vocabulary)."""
        for task in TASKS:
            assert 4 <= len(task["markers"]) <= 8, task["name"]
            for marker in task["markers"]:
                assert marker in task["skill"], f"{task['name']}: skill lacks {marker!r}"
                for rel, content in task["files"].items():
                    assert marker not in content, (
                        f"{task['name']}: {rel} leaks the private marker {marker!r}"
                    )

    def test_task_prompts_do_not_reveal_the_rules(self) -> None:
        """No marker - no rule vocabulary - ever reaches the prompt."""
        for task in TASKS:
            prompt = task["prompt"].lower()
            for marker in task["markers"]:
                assert marker.lower() not in prompt, f"{task['name']}: prompt leaks {marker!r}"

    def test_directness_is_assigned_and_honored(self) -> None:
        """named -> the prompt NAMES the standard (a ticket would);
        indirect -> the prompt names NEITHER the standard (skill id,
        spaced skill id) nor any of its distinctive title words (the
        markers) - it only says an internal policy exists."""
        for task in TASKS:
            directness = task["directness"]
            assert directness in ("named", "indirect"), task["name"]
            prompt = task["prompt"].lower()
            skill_id = task["skill_id"].lower()
            if directness == "named":
                assert skill_id.replace("-", " ") in prompt, (
                    f"{task['name']}: a named prompt must name the standard"
                )
            else:
                assert skill_id not in prompt, task["name"]
                assert skill_id.replace("-", " ") not in prompt, task["name"]
                assert "internal" in prompt, task["name"]

    def test_the_rules_are_genuinely_private(self) -> None:
        """The fixture's knowledge did not exist before today: the skill
        names a fictional internal standard (invented with the fixture)
        - pin the fiction so a future edit cannot quietly swap in a
        real, possibly-public convention."""
        for task in TASKS:
            assert "internal" in task["skill"].lower(), task["name"]
            assert "not published" in task["skill"].lower(), task["name"]
        assert "Cairn Money Standard" in TASKS[0]["skill"]
        assert "Drawbridge Rollout Standard" in TASKS[1]["skill"]


class TestFixtureShape:
    def test_names_and_skill_ids_are_well_formed(self) -> None:
        for task in TASKS:
            assert task["name"].startswith("private2-"), task["name"]
            assert task["skill_id"] == task["skill_id"].lower(), task["name"]
            assert " " not in task["skill_id"], task["name"]
            assert task["skill_id"].replace(" ", "") == task["skill_id"]

    def test_the_skill_is_never_a_workspace_file(self) -> None:
        """The skill is fixture metadata, NEVER a workspace file - the
        model must not see it on disk; the measurement arms serve it
        through the capability plane (or write it as a repo doc for arm
        F, which is the arm's own doing, not the fixture's)."""
        for task in TASKS:
            assert task["skill_id"] and task["skill"], task["name"]
            assert task["skill_id"] not in task["files"], task["name"]

    def test_one_test_file_over_a_small_package(self) -> None:
        for task in TASKS:
            tests = [n for n in task["files"] if n.startswith("test_")]
            sources = [n for n in task["files"] if not n.startswith("test_")]
            assert len(tests) == 1, f"{task['name']}: {tests}"
            assert 2 <= len(sources) <= 5, f"{task['name']}: {sources}"

    def test_paths_are_safe_and_relative(self) -> None:
        for task in TASKS:
            for rel in task["files"]:
                assert not rel.startswith("/"), f"{task['name']}: absolute path {rel}"
                assert ".." not in rel, f"{task['name']}: traversal in {rel}"
                assert not Path(rel).is_absolute(), f"{task['name']}: absolute {rel}"

    def test_digest_pins_are_present(self) -> None:
        """The workspace tests pin the standard's decisions as sha256
        DIGESTS over canonical traces - never plaintext expected values
        (the test must not become a copy of the standard), and enough
        of them that every scenario is pinned."""
        hex64 = re.compile(r"\b[0-9a-f]{64}\b")
        for task in TASKS:
            test_files = [n for n in task["files"] if n.startswith("test_")]
            content = task["files"][test_files[0]]
            pins = hex64.findall(content)
            assert len(pins) >= 4, f"{task['name']}: only {len(pins)} digest pins"
            assert "PLACEHOLDER" not in content, task["name"]

    def test_skill_frontmatter_is_valid(self) -> None:
        for task in TASKS:
            skill = task["skill"]
            assert skill.startswith("---\n"), task["name"]
            header, _, body = skill[4:].partition("\n---\n")
            fields: dict[str, str] = {}
            for line in header.splitlines():
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
            assert fields.get("name") == task["skill_id"], task["name"]
            assert fields.get("version") == "1.0.0", task["name"]
            assert fields.get("description"), task["name"]
            assert "\n" not in fields["description"], task["name"]
            assert body.startswith("\n# "), task["name"]

    def test_skill_fits_the_context_budget(self) -> None:
        """The skill body is <= ~1,500 tokens (len/4): the E2C arms
        preload it whole into the model context."""
        for task in TASKS:
            assert len(task["skill"]) // 4 <= 1500, (
                f"{task['name']}: skill is {len(task['skill']) // 4} tokens"
            )
