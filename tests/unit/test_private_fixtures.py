"""The private-knowledge fixtures are real fixtures (§34): each must FAIL in
its shipped state and PASS after the known root-cause fix — verified here
mechanically, no network, no DB. Also pins the E2 KNOWLEDGE GATE: the rules
are arbitrary (invented with the fixture, so no model can carry them),
documented ONLY in the private skill, and not derivable from the tests
(digest-pinned, never plaintext), the shipped code (a wrong policy) or the
task prompt.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify_private_fixtures as vpf  # noqa: E402
from private_tasks import (  # noqa: E402
    PRIVATE_INTENDED_SKILLS,
    PRIVATE_TASKS,
)


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


class TestPrivateFixtureVerification:
    def test_every_fixture_has_a_fix(self) -> None:
        for task in PRIVATE_TASKS:
            assert task["name"] in vpf.PRIVATE_FIXES, task["name"]

    def test_fixtures_fail_as_shipped_and_pass_after_the_fix(self) -> None:
        """The §34 discipline, per fixture: shipped state FAILS (the fixture
        is actually buggy), fixed state PASSES (the known root-cause fix is
        a real fix)."""
        for task in PRIVATE_TASKS:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = vpf.materialize(task, Path(tmp))
                code, _ = _pytest(task_dir)
                assert code != 0, f"{task['name']}: shipped state PASSES (not a fixture)"
                vpf.apply_fix(task_dir, vpf.PRIVATE_FIXES[task["name"]])
                code, out = _pytest(task_dir)
                assert code == 0, f"{task['name']}: FIXED state still fails:\n{out[-400:]}"

    def test_verifier_catches_a_fixture_that_passes_as_shipped(self) -> None:
        """A fixture whose shipped state already passes is NOT a fixture —
        verify_all must say so (exit 1), not bless it."""
        _, _, fixed_gate = vpf.PRIVATE_FIXES["private-meridian-release"][0]
        task = {
            "name": "private-broken",
            # Ship the FIXED gate as the "buggy" state: nothing left to fix.
            "files": {
                **{
                    rel: content
                    for rel, content in PRIVATE_TASKS[1]["files"].items()
                    if rel != "release_gate.py"
                },
                "release_gate.py": fixed_gate,
            },
            "prompt": "irrelevant",
        }
        original_tasks, original_fixes = vpf.PRIVATE_TASKS, vpf.PRIVATE_FIXES
        vpf.PRIVATE_TASKS = [task]
        vpf.PRIVATE_FIXES = {**original_fixes, "private-broken": [("release_gate.py", None, "x")]}
        try:
            assert vpf.verify_all(python=sys.executable) == 1
        finally:
            vpf.PRIVATE_TASKS = original_tasks
            vpf.PRIVATE_FIXES = original_fixes


class TestApplyFix:
    def test_whole_file_replacement(self, tmp_path: Path) -> None:
        d = tmp_path / "fx"
        d.mkdir()
        (d / "a.py").write_text("old content\n", encoding="utf-8")
        vpf.apply_fix(d, [("a.py", None, "new content\n")])
        assert (d / "a.py").read_text(encoding="utf-8") == "new content\n"

    def test_splice_with_a_missing_anchor_raises(self, tmp_path: Path) -> None:
        d = tmp_path / "fx"
        d.mkdir()
        (d / "a.py").write_text("keep\n", encoding="utf-8")
        try:
            vpf.apply_fix(d, [("a.py", "absent", "new")])
        except ValueError as exc:
            assert "anchor" in str(exc)
        else:
            raise AssertionError("missing anchor must raise")


class TestPrivateFixtureMetadata:
    def test_intended_skills_are_declared_for_every_fixture(self) -> None:
        assert set(PRIVATE_INTENDED_SKILLS) == {t["name"] for t in PRIVATE_TASKS}

    def test_every_fixture_carries_its_private_skill(self) -> None:
        """The skill is fixture metadata, NEVER a workspace file — the model
        must not see it on disk; arm R serves it through the capability
        plane only."""
        for task in PRIVATE_TASKS:
            assert task["skill_id"] and task["skill"], task["name"]
            assert task["skill_id"] not in task["files"], task["name"]
            assert any(name.startswith("test_") for name in task["files"]), task["name"]

    def test_fixture_paths_are_safe_and_relative(self) -> None:
        for task in PRIVATE_TASKS:
            for rel in task["files"]:
                assert not rel.startswith("/"), f"{task['name']}: absolute path {rel}"
                assert ".." not in rel, f"{task['name']}: traversal in {rel}"


#: The E2 knowledge gate, per fixture: strings that exist ONLY in the
#: private skill (the standard's vocabulary — class names, event-line
#: formats, violation codes). They must NOT appear in the tests (which
#: would turn the test into a copy of the standard) nor in the shipped code
#: (which would leak the vocabulary to a naked model) — and they MUST
#: appear in the skill (the skill is their only source).
PRIVATE_ONLY_MARKERS: dict[str, tuple[str, ...]] = {
    "private-atlas-retry": (
        "AtlasTransientError",
        "AtlasClientError",
        "AtlasDegradedError",
        "AtlasEscalationError",
        "TRANSIENT",
        "CLIENT_FAULT",
        "DEGRADED",
        "ESCALATE",
        "attempt=1 wait=1s",
        "attempt=2 wait=4s",
        "attempt=1 wait=2s",
        "[atlas] degraded",
    ),
    "private-meridian-release": (
        "version-scheme",
        "minor-parity",
        "channel-unknown",
        "promotion-order",
        "approver-count",
        "migration-order",
    ),
}


class TestKnowledgeGate:
    def test_the_rules_live_only_in_the_skill(self) -> None:
        """The private standard's vocabulary appears in the skill and NOWHERE
        else: not in the tests (digest-pinned, never plaintext — the test
        must not become a copy of the standard), not in the shipped code (a
        wrong policy that must not leak the real vocabulary)."""
        for task in PRIVATE_TASKS:
            markers = PRIVATE_ONLY_MARKERS[task["name"]]
            for marker in markers:
                assert marker in task["skill"], f"{task['name']}: skill lacks {marker!r}"
                for rel, content in task["files"].items():
                    assert marker not in content, (
                        f"{task['name']}: {rel} leaks the private marker {marker!r}"
                    )

    def test_task_prompts_do_not_reveal_the_rules(self) -> None:
        """The prompt says the standard exists — never what it says: not the
        skill id, not any private marker."""
        for task in PRIVATE_TASKS:
            prompt = task["prompt"].lower()
            assert task["skill_id"] not in prompt, task["name"]
            assert task["skill_id"].replace("-", " ") not in prompt, task["name"]
            for marker in PRIVATE_ONLY_MARKERS[task["name"]]:
                assert marker.lower() not in prompt, f"{task['name']}: prompt leaks {marker!r}"

    def test_the_rules_are_genuinely_private(self) -> None:
        """The fixture's knowledge did not exist before today: the skill
        names a fictional internal standard (invented with the fixture) —
        pin the fiction so a future edit cannot quietly swap in a real,
        possibly-public convention."""
        for task in PRIVATE_TASKS:
            assert "internal" in task["skill"].lower(), task["name"]
        atlas = PRIVATE_TASKS[0]["skill"]
        assert "Atlas Error Handling Standard" in atlas
        assert "not published" in atlas
        meridian = PRIVATE_TASKS[1]["skill"]
        assert "Meridian Release Gate Standard" in meridian
        assert "not published" in meridian
