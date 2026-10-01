"""The domain-knowledge fixtures are real fixtures (§34): each must FAIL in
its shipped state and PASS after the known root-cause fix — verified here
mechanically, no network, no DB. Also pins the verifier's fix-application
semantics (whole-file replacement vs splice) so a fixture author cannot
silently break the round's acceptance signal.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify_domain_fixtures as vdf  # noqa: E402
from domain_tasks import DOMAIN_INTENDED_SKILLS, DOMAIN_TASKS  # noqa: E402


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


class TestDomainFixtureVerification:
    def test_every_fixture_has_a_fix(self) -> None:
        for task in DOMAIN_TASKS:
            assert task["name"] in vdf.DOMAIN_FIXES, task["name"]

    def test_fixtures_fail_as_shipped_and_pass_after_the_fix(self) -> None:
        """The §34 discipline, per fixture: shipped state FAILS (the fixture
        is actually buggy), fixed state PASSES (the known root-cause fix is
        a real fix)."""
        for task in DOMAIN_TASKS:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = vdf.materialize(task, Path(tmp))
                code, _ = _pytest(task_dir)
                assert code != 0, f"{task['name']}: shipped state PASSES (not a fixture)"
                vdf.apply_fix(task_dir, vdf.DOMAIN_FIXES[task["name"]])
                code, out = _pytest(task_dir)
                assert code == 0, f"{task['name']}: FIXED state still fails:\n{out[-400:]}"

    def test_verifier_catches_a_fixture_that_passes_as_shipped(self) -> None:
        """A fixture whose shipped state already passes is NOT a fixture —
        verify_all must say so (exit 1), not bless it."""
        _, _, fixed_agent = vdf.DOMAIN_FIXES["domain-injection-hardening"][0]
        task = {
            "name": "domain-broken",
            # Ship the FIXED agent as the "buggy" state: nothing left to fix.
            "files": {
                **{
                    rel: content
                    for rel, content in DOMAIN_TASKS[0]["files"].items()
                    if rel != "agent.py"
                },
                "agent.py": fixed_agent,
            },
            "prompt": "irrelevant",
        }
        original_tasks, original_fixes = vdf.DOMAIN_TASKS, vdf.DOMAIN_FIXES
        vdf.DOMAIN_TASKS = [task]
        vdf.DOMAIN_FIXES = {**original_fixes, "domain-broken": [("agent.py", None, "x")]}
        try:
            assert vdf.verify_all(python=sys.executable) == 1
        finally:
            vdf.DOMAIN_TASKS = original_tasks
            vdf.DOMAIN_FIXES = original_fixes


class TestApplyFix:
    def test_whole_file_replacement(self, tmp_path: Path) -> None:
        d = tmp_path / "fx"
        d.mkdir()
        (d / "a.py").write_text("old content\n", encoding="utf-8")
        vdf.apply_fix(d, [("a.py", None, "new content\n")])
        assert (d / "a.py").read_text(encoding="utf-8") == "new content\n"

    def test_splice_replaces_the_anchor(self, tmp_path: Path) -> None:
        d = tmp_path / "fx"
        d.mkdir()
        (d / "a.py").write_text("keep\nold\ntail\n", encoding="utf-8")
        vdf.apply_fix(d, [("a.py", "old", "new")])
        assert (d / "a.py").read_text(encoding="utf-8") == "keep\nnew\ntail\n"

    def test_splice_with_a_missing_anchor_raises(self, tmp_path: Path) -> None:
        d = tmp_path / "fx"
        d.mkdir()
        (d / "a.py").write_text("keep\n", encoding="utf-8")
        try:
            vdf.apply_fix(d, [("a.py", "absent", "new")])
        except ValueError as exc:
            assert "anchor" in str(exc)
        else:
            raise AssertionError("missing anchor must raise")


class TestDomainFixtureMetadata:
    def test_intended_skills_are_declared_for_every_fixture(self) -> None:
        assert set(DOMAIN_INTENDED_SKILLS) == {t["name"] for t in DOMAIN_TASKS}

    def test_fixture_paths_are_safe_and_relative(self) -> None:
        for task in DOMAIN_TASKS:
            for rel in task["files"]:
                assert not rel.startswith("/"), f"{task['name']}: absolute path {rel}"
                assert ".." not in rel, f"{task['name']}: traversal in {rel}"
