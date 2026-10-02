"""The private2 fixture (builder "d") is a real fixture (§34): it must
FAIL in its shipped state and PASS after the known root-cause fix —
verified here mechanically through the real verifier path
(verify_private_fixtures materialize/apply_fix + pytest in a temp dir),
no network, no DB, no model. Also pins the E2C KNOWLEDGE GATE: the rules
are arbitrary (invented with the fixture, so no model can carry them),
documented ONLY in the private skill, and not derivable from the tests
(digest-pinned, never plaintext), the shipped code (a wrong policy) or
the task prompt (INDIRECT directness: no standard name, no title words).
"""

import re
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify_private_fixtures as vpf  # noqa: E402
from private2_tasks_d import FIXES, TASKS  # noqa: E402


class TestPrivate2FixtureVerification:
    def test_every_fixture_has_a_fix(self) -> None:
        for task in TASKS:
            assert task["name"] in FIXES, task["name"]

    def test_fixtures_fail_as_shipped_and_pass_after_the_fix(self) -> None:
        """The §34 discipline, per fixture: shipped state FAILS (the
        fixture is actually buggy — the shipped code embodies the wrong
        public-practice convention), fixed state PASSES (the known
        root-cause fix is a real fix)."""
        for task in TASKS:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = vpf.materialize(task, Path(tmp))
                code, _ = vpf.run_pytest(task_dir, python=sys.executable)
                assert code != 0, f"{task['name']}: shipped state PASSES (not a fixture)"
                vpf.apply_fix(task_dir, FIXES[task["name"]])
                code, out = vpf.run_pytest(task_dir, python=sys.executable)
                assert code == 0, f"{task['name']}: FIXED state still fails:\n{out[-400:]}"

    def test_the_fix_only_touches_shipped_files(self) -> None:
        """The fix replaces whole files that exist in the shipped
        workspace — it can never add a new file the tests do not run
        against."""
        for task in TASKS:
            for fix_file, old, _ in FIXES[task["name"]]:
                assert fix_file in task["files"], f"{task['name']}: fix adds {fix_file}"
                assert old is None, f"{task['name']}: fix must be a whole-file replacement"


class TestPrivate2FixtureMetadata:
    def test_every_fixture_carries_its_private_skill(self) -> None:
        """The skill is fixture metadata, NEVER a workspace file — the
        model must not see it on disk; the arms serve it through the
        capability plane only."""
        for task in TASKS:
            assert task["skill_id"] and task["skill"], task["name"]
            assert task["skill_id"] not in task["files"], task["name"]
            assert any(name.startswith("test_") for name in task["files"]), task["name"]

    def test_directness_is_assigned_and_valid(self) -> None:
        for task in TASKS:
            assert task["directness"] in ("named", "indirect"), task["name"]

    def test_fixture_paths_are_safe_and_relative(self) -> None:
        for task in TASKS:
            for rel in task["files"]:
                assert not rel.startswith("/"), f"{task['name']}: absolute path {rel}"
                assert ".." not in rel, f"{task['name']}: traversal in {rel}"

    def test_skill_frontmatter_is_valid(self) -> None:
        """The private skill is a real SKILL.md: frontmatter with name
        (== skill_id), a one-line description and version 1.0.0."""
        for task in TASKS:
            skill = task["skill"]
            assert skill.startswith("---\n"), task["name"]
            end = skill.index("\n---\n", 3)
            fields: dict[str, str] = {}
            for line in skill[3:end].splitlines():
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
            assert fields["name"] == task["skill_id"], task["name"]
            assert fields["version"] == "1.0.0", task["name"]
            assert fields["description"], task["name"]

    def test_skill_is_small_enough_for_context(self) -> None:
        """The standard must fit a preload budget: <= ~1,500 tokens
        (len // 4 estimate)."""
        for task in TASKS:
            assert len(task["skill"]) // 4 <= 1500, task["name"]

    def test_digest_pins_are_present(self) -> None:
        """The contract test pins sha256 DIGESTS over the report — never
        plaintext expected values: >= 4 distinct 64-hex digests, and the
        digest machinery (sha256 + hexdigest) in the shipped test."""
        for task in TASKS:
            test_file = next(rel for rel in task["files"] if rel.startswith("test_"))
            content = task["files"][test_file]
            assert "sha256" in content and "hexdigest" in content, task["name"]
            digests = re.findall(r'"([0-9a-f]{64})"', content)
            assert len(digests) >= 4, task["name"]
            assert len(set(digests)) == len(digests), task["name"]


class TestKnowledgeGate:
    def test_the_rules_live_only_in_the_skill(self) -> None:
        """The private standard's vocabulary appears in the skill and
        NOWHERE else: not in the tests (digest-pinned, never plaintext —
        the test must not become a copy of the standard), not in the
        shipped code (a wrong policy that must not leak the real
        vocabulary)."""
        for task in TASKS:
            markers = task["markers"]
            assert 4 <= len(markers) <= 8, task["name"]
            for marker in markers:
                assert marker in task["skill"], f"{task['name']}: skill lacks {marker!r}"
                for rel, content in task["files"].items():
                    assert marker not in content, (
                        f"{task['name']}: {rel} leaks the private marker {marker!r}"
                    )

    def test_task_prompts_do_not_reveal_the_rules(self) -> None:
        """The prompt says the policy exists — never what it says: not
        the skill id, not any private marker."""
        for task in TASKS:
            prompt = task["prompt"].lower()
            assert task["skill_id"] not in prompt, task["name"]
            assert task["skill_id"].replace("-", " ") not in prompt, task["name"]
            for marker in task["markers"]:
                assert marker.lower() not in prompt, f"{task['name']}: prompt leaks {marker!r}"

    def test_indirect_prompts_do_not_name_the_standard(self) -> None:
        """INDIRECT directness: the prompt never names the standard or
        uses its title words — only "our internal policy" / "how we do
        this here"."""
        for task in TASKS:
            if task["directness"] != "indirect":
                continue
            prompt = task["prompt"].lower()
            assert "harbor" not in prompt, task["name"]
            assert "resolution standard" not in prompt, task["name"]
            assert "internal policy" in prompt, task["name"]

    def test_the_rules_are_genuinely_private(self) -> None:
        """The fixture's knowledge did not exist before today: the skill
        names a fictional internal standard (invented with the fixture) —
        pin the fiction so a future edit cannot quietly swap in a real,
        possibly-public convention."""
        for task in TASKS:
            assert "internal" in task["skill"].lower(), task["name"]
        skill = TASKS[0]["skill"]
        assert "Harbor Config Resolution Standard" in skill
        assert "not published" in skill
