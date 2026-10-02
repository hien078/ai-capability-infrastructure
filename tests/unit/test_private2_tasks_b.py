"""The private2 fixtures (builder b) are real fixtures (section 34): each
must FAIL in its shipped state and PASS after the known root-cause fix -
verified here mechanically through the same verifier path the existing
private-fixture tests use (materialize into a temp dir, real pytest, no
network, no DB, no model). Also pins the E2C KNOWLEDGE GATE: the rules
are arbitrary (invented with the fixture, so no model can carry them),
documented ONLY in the private skill, and not derivable from the tests
(digest-pinned over a LARGE output space - the E2B section 1.5 lesson),
the shipped code (a plausible-but-wrong public-practice policy) or the
task prompt. Directness is pinned per fixture: the named prompt names
the standard and its owning team; the indirect prompt names neither the
standard nor its title words.
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
from private2_tasks_b import FIXES, TASKS  # noqa: E402

from aci.providers.skills.parser import parse_skill_md  # noqa: E402


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


def _verify_pair(task: dict, fix: list) -> list[str]:
    """The section 34 discipline for one fixture: the shipped state must
    FAIL (the fixture is actually buggy) and the fixed state must PASS
    (the known root-cause fix is a real fix). Returns the problems."""
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        task_dir = vpf.materialize(task, Path(tmp))
        code, _ = _pytest(task_dir)
        if code == 0:
            problems.append(f"{task['name']}: shipped state PASSES (not a fixture)")
            return problems
        vpf.apply_fix(task_dir, fix)
        code, out = _pytest(task_dir)
        if code != 0:
            problems.append(f"{task['name']}: FIXED state still fails:\n{out[-400:]}")
    return problems


def _by_name(name: str) -> dict:
    return next(t for t in TASKS if t["name"] == name)


class TestFixtureVerification:
    def test_every_fixture_has_a_fix(self) -> None:
        for task in TASKS:
            assert task["name"] in FIXES, task["name"]

    def test_fixtures_fail_as_shipped_and_pass_after_the_fix(self) -> None:
        """The section 34 discipline, per fixture: shipped state FAILS (the
        fixture is actually buggy), fixed state PASSES (the known
        root-cause fix is a real fix) - through the real verifier path
        (materialize + pytest in a temp dir), never the model."""
        for task in TASKS:
            problems = _verify_pair(task, FIXES[task["name"]])
            assert problems == [], f"{task['name']}: {problems}"

    def test_a_fixture_that_passes_as_shipped_is_not_a_fixture(self) -> None:
        """A fixture whose shipped state already passes is NOT a fixture -
        the discipline must flag it, not bless it. Pinned by shipping the
        FIXED filter as the 'buggy' state: nothing left to fix."""
        palisade = _by_name("private2-palisade-redaction")
        fixed = {rel: new for rel, _old, new in FIXES[palisade["name"]]}
        fake = {
            "name": "private2-not-a-fixture",
            # the shipped workspace with the FIXED sources: nothing left to fix
            "files": {**palisade["files"], **fixed},
            "prompt": "irrelevant",
        }
        assert _verify_pair(fake, [("log_filter.py", None, "x")]) == [
            "private2-not-a-fixture: shipped state PASSES (not a fixture)"
        ]


class TestKnowledgeGate:
    def test_markers_live_only_in_the_skill(self) -> None:
        """The private standard's vocabulary appears in the skill and
        NOWHERE else: not in the tests (digest-pinned, never plaintext -
        the test must not become a copy of the standard), not in the
        shipped code (a plausible-but-wrong policy that must not leak the
        real vocabulary)."""
        for task in TASKS:
            for marker in task["markers"]:
                assert marker in task["skill"], f"{task['name']}: skill lacks {marker!r}"
                for rel, content in task["files"].items():
                    assert marker not in content, (
                        f"{task['name']}: {rel} leaks the private marker {marker!r}"
                    )

    def test_prompts_do_not_reveal_the_rules(self) -> None:
        """The prompt says a standard exists - never what it says: not the
        skill id, not any private marker."""
        for task in TASKS:
            prompt = task["prompt"].lower()
            assert task["skill_id"] not in prompt, task["name"]
            assert task["skill_id"].replace("-", " ") not in prompt, task["name"]
            for marker in task["markers"]:
                assert marker.lower() not in prompt, f"{task['name']}: leaks {marker!r}"

    def test_directness_is_pinned(self) -> None:
        """NAMED: the prompt names the standard and its owning team.
        INDIRECT: the prompt names neither the standard nor any of its
        title words - only 'our internal policy'."""
        named = _by_name("private2-palisade-redaction")
        assert named["directness"] == "named"
        assert "palisade" in named["prompt"].lower()
        assert "security team" in named["prompt"].lower()

        indirect = _by_name("private2-cairn-sunset")
        assert indirect["directness"] == "indirect"
        prompt = indirect["prompt"].lower()
        for word in ("cairn", "sunset", "rules"):  # the standard's title words
            assert word not in prompt, f"indirect prompt names the standard: {word!r}"
        assert "internal policy" in prompt  # it DOES say a policy exists

    def test_the_rules_are_genuinely_private(self) -> None:
        """The fixture's knowledge did not exist before today: the skill
        names a fictional internal standard (invented with the fixture) -
        pin the fiction so a future edit cannot quietly swap in a real,
        possibly-public convention."""
        palisade = _by_name("private2-palisade-redaction")["skill"]
        assert "Palisade Log Redaction Rules" in palisade
        assert "internal" in palisade.lower()
        assert "not published" in palisade
        cairn = _by_name("private2-cairn-sunset")["skill"]
        assert "Cairn Sunset Rules" in cairn
        assert "internal" in cairn.lower()
        assert "not published" in cairn


class TestFixtureMetadata:
    def test_task_shape(self) -> None:
        """The E2C module contract: name/files/prompt/skill_id/skill/
        directness/markers, directness from the assigned vocabulary,
        4-8 distinctive string markers per fixture."""
        names = set()
        skill_ids = set()
        for task in TASKS:
            assert set(task) == {
                "name",
                "files",
                "prompt",
                "skill_id",
                "skill",
                "directness",
                "markers",
            }
            assert task["name"].startswith("private2-"), task["name"]
            assert task["directness"] in ("named", "indirect")
            assert 4 <= len(task["markers"]) <= 8, task["name"]
            assert all(isinstance(m, str) and m for m in task["markers"]), task["name"]
            assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", task["skill_id"]), task["skill_id"]
            names.add(task["name"])
            skill_ids.add(task["skill_id"])
        assert len(names) == len(TASKS) and len(skill_ids) == len(TASKS)

    def test_skill_is_never_a_workspace_file(self) -> None:
        """The skill is fixture metadata, NEVER a workspace file - the model
        must not see it on disk; the capability plane serves it. Each
        fixture ships exactly ONE test file."""
        for task in TASKS:
            assert task["skill_id"] and task["skill"], task["name"]
            assert task["skill_id"] not in task["files"], task["name"]
            tests = [rel for rel in task["files"] if rel.startswith("test_")]
            assert len(tests) == 1, f"{task['name']}: {tests}"

    def test_fixture_paths_are_safe_and_relative(self) -> None:
        for task in TASKS:
            for rel in task["files"]:
                assert not rel.startswith("/"), f"{task['name']}: absolute path {rel}"
                assert ".." not in rel, f"{task['name']}: traversal in {rel}"

    def test_digest_pins_are_present(self) -> None:
        """The tests pin behavior as sha256 DIGESTS (never plaintext): at
        least 5 distinct 64-hex pins per test file, over the canonical
        rendering, with the pinning mechanism visible in the file."""
        for task in TASKS:
            test_file = next(rel for rel in task["files"] if rel.startswith("test_"))
            content = task["files"][test_file]
            pins = re.findall(r'"([0-9a-f]{64})"', content)
            assert len(pins) >= 5, f"{task['name']}: only {len(pins)} digest pins"
            assert len(set(pins)) == len(pins), f"{task['name']}: duplicate digest pins"
            assert "hashlib.sha256" in content, task["name"]
            assert "strict=True" in content, f"{task['name']}: pins not zip-strict"

    def test_skill_frontmatter_is_valid(self) -> None:
        """The skill text is a real SKILL.md: the repo's own parser accepts
        it, the name is the skill id, the version is pinned 1.0.0."""
        for task in TASKS:
            meta = parse_skill_md(task["skill"])
            assert meta.name == task["skill_id"], task["name"]
            assert meta.version == "1.0.0", task["name"]
            assert meta.description.strip(), task["name"]

    def test_skill_fits_the_context_budget(self) -> None:
        """The standard must fit a preload: <= 1,500 tokens estimated as
        len/4 (the same estimate the kernel's capability plane uses)."""
        for task in TASKS:
            estimated = len(task["skill"]) / 4
            assert estimated <= 1500, f"{task['name']}: skill is {estimated:.0f} tokens"

    def test_fixes_are_whole_file_replacements_of_shipped_files(self) -> None:
        """FIXES uses the verify_private_fixtures.PRIVATE_FIXES format:
        (file, None, content) whole-file replacements of shipped files."""
        shipped = {t["name"]: set(t["files"]) for t in TASKS}
        for name, steps in FIXES.items():
            assert name in shipped, name
            for fix_file, old, new in steps:
                assert fix_file in shipped[name], f"{name}: fix touches unshipped {fix_file}"
                assert old is None, f"{name}: {fix_file}: splice fixes are not used here"
                assert isinstance(new, str) and new, name
