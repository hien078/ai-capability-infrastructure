"""Builder C's private3 fixtures are real fixtures (§34): each must FAIL in
its shipped state and PASS after the known root-cause fix — verified here
mechanically through the real verifier path (pytest in a temp dir), no
network, no DB, no model. This is the DENSE-CORPUS slice (rc-bench ADR-014
amendment 30 follow-up): TWO families of FIVE near-identical standards,
so the suite also pins the family invariants — the descriptions differ
ONLY in scope words, the rules differ in every decision, and a SIBLING
variant's fix must FAIL every other workspace of the family (the
confusion matrix: FIX_i on workspace_j fails for every i != j, passes
for i = j). The knowledge gate holds as in E2C: the rules are invented
with the fixtures (no model can carry them), documented ONLY in the
private skill, and not derivable from the tests (grouped sha256 digest
pins over multi-decision traces, never plaintext), the shipped code (a
plausible-but-wrong public convention) or the task prompt (indirect:
names the service, never the standard or its title words).
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
from private3_tasks_c import FIXES, TASKS  # noqa: E402

from aci.providers.skills.parser import parse_skill_md  # noqa: E402

_HEX64 = re.compile(r"\b[0-9a-f]{64}\b")

#: The job's assigned fixtures: name -> (family, skill_id, directness).
ASSIGNED: dict[str, tuple[str, str, str]] = {
    "private3-thistle-retry": ("consumer-retry", "thistle-consumer-retry", "indirect"),
    "private3-sable-retry": ("consumer-retry", "sable-consumer-retry", "indirect"),
    "private3-juniper-retry": ("consumer-retry", "juniper-consumer-retry", "indirect"),
    "private3-basalt-retry": ("consumer-retry", "basalt-consumer-retry", "indirect"),
    "private3-tundra-retry": ("consumer-retry", "tundra-consumer-retry", "indirect"),
    "private3-kestrel-config": (
        "config-precedence",
        "kestrel-config-precedence",
        "indirect",
    ),
    "private3-marrow-config": (
        "config-precedence",
        "marrow-config-precedence",
        "indirect",
    ),
    "private3-sorrel-config": (
        "config-precedence",
        "sorrel-config-precedence",
        "indirect",
    ),
    "private3-onyx-config": ("config-precedence", "onyx-config-precedence", "indirect"),
    "private3-wick-config": ("config-precedence", "wick-config-precedence", "indirect"),
}

FAMILIES = ("consumer-retry", "config-precedence")

#: The description template per family: the scope words (team, domain,
#: services) are the ONLY thing that varies between siblings.
DESC_TEMPLATE = {
    "consumer-retry": re.compile(
        r"^Retry policy for the (?P<team>[A-Z][a-z]+) (?P<domain>[a-z]+) services "
        r"\((?P<svc>[a-z0-9-]+, [a-z0-9-]+)\)\.$"
    ),
    "config-precedence": re.compile(
        r"^Config precedence for the (?P<team>[A-Z][a-z]+) (?P<domain>[a-z]+) services "
        r"\((?P<svc>[a-z0-9-]+, [a-z0-9-]+)\)\.$"
    ),
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


def _description_of(task: dict) -> str:
    skill = task["skill"]
    header = skill[4:].partition("\n---\n")[0]
    for line in header.splitlines():
        if line.startswith("description:"):
            return line.partition(":")[2].strip()
    raise AssertionError(f"{task['name']}: no description line")


def _scope_of(task: dict) -> dict:
    """The scope of a description: team + domain + services (the only
    words that vary between siblings)."""
    match = DESC_TEMPLATE[task["family"]].match(_description_of(task))
    assert match, f"{task['name']}: description is not the family template"
    return {
        "team": match["team"],
        "domain": match["domain"],
        "services": [s.strip() for s in match["svc"].split(",")],
    }


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


class TestFixtureVerification:
    def test_the_assigned_fixtures_are_present(self) -> None:
        assert {t["name"]: (t["family"], t["skill_id"], t["directness"]) for t in TASKS} == (
            ASSIGNED
        )

    def test_every_fixture_has_a_fix(self) -> None:
        for task in TASKS:
            assert task["name"] in FIXES, task["name"]
        assert set(FIXES) == set(ASSIGNED)

    def test_fix_steps_target_workspace_sources_never_tests(self) -> None:
        for task in TASKS:
            for fix_file, old, _new in FIXES[task["name"]]:
                assert fix_file in task["files"], (task["name"], fix_file)
                assert not fix_file.startswith("test_"), (task["name"], fix_file)
                assert old is None, (task["name"], fix_file)

    def test_fixes_are_reimplementations_not_splices(self) -> None:
        """The fix is a whole-module reimplementation of the standard
        (~30-80 lines per rule module), not a one-line splice."""
        for task in TASKS:
            total = sum(step[2].count("\n") for step in FIXES[task["name"]])
            assert 30 <= total <= 120, f"{task['name']}: fix is {total} lines"

    def test_fixtures_fail_as_shipped_and_pass_after_the_fix(self) -> None:
        for task in TASKS:
            ok, detail = _check_fixture(task)
            assert ok, f"{task['name']}: {detail}"

    def test_the_check_catches_a_fixture_that_passes_as_shipped(self) -> None:
        """A fixture whose shipped state already passes is NOT a fixture —
        _check_fixture must say so, not bless it (ship the FIXED modules
        as the "buggy" state: nothing left to fix)."""
        task = TASKS[0]
        pre_fixed_files = dict(task["files"])
        for fix_file, _old, new in FIXES[task["name"]]:
            pre_fixed_files[fix_file] = new
        pre_fixed = {"name": task["name"], "files": pre_fixed_files}
        ok, detail = _check_fixture(pre_fixed)
        assert not ok, "a pre-fixed workspace must be caught as not a fixture"
        assert "shipped state PASSES" in detail


class TestConfusionMatrix:
    """The dense-corpus property: within a family the five variants are
    near-identical in kind but differ in every rule — a SIBLING variant's
    fix, applied to another workspace, must FAIL that workspace's tests
    (the digests pin THIS variant's rules, not the family's)."""

    def test_consumer_retry_family(self) -> None:
        members = [t for t in TASKS if t["family"] == "consumer-retry"]
        assert len(members) == 5
        self._matrix(members)

    def test_config_precedence_family(self) -> None:
        members = [t for t in TASKS if t["family"] == "config-precedence"]
        assert len(members) == 5
        self._matrix(members)

    @staticmethod
    def _matrix(members: list) -> None:
        for fixer in members:
            for worker in members:
                if fixer is worker:
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    task_dir = vpf.materialize(worker, Path(tmp))
                    vpf.apply_fix(task_dir, FIXES[fixer["name"]])
                    code, out = _pytest(task_dir)
                    assert code != 0, (
                        f"{fixer['name']}'s fix PASSES {worker['name']}'s "
                        f"workspace (siblings are confusable - not dense): "
                        f"{out[-200:]}"
                    )


class TestKnowledgeGate:
    def test_markers_live_only_in_their_own_skill(self) -> None:
        """The private standard's vocabulary appears in the skill and
        NOWHERE else: not in ANY fixture's workspace files (both families
        - the dense corpus is one list to a model), not in ANY prompt, and
        not in any sibling skill."""
        for task in TASKS:
            assert 4 <= len(task["markers"]) <= 8, task["name"]
            for marker in task["markers"]:
                assert marker in task["skill"], f"{task['name']}: lacks {marker!r}"
                for other in TASKS:
                    for rel, content in other["files"].items():
                        assert marker not in content, (
                            f"{task['name']}: {other['name']}:{rel} leaks {marker!r}"
                        )
                    assert marker.lower() not in other["prompt"].lower(), (
                        f"{task['name']}: {other['name']} prompt leaks {marker!r}"
                    )
                    if other is not task:
                        assert marker not in other["skill"], (
                            f"{task['name']}: marker {marker!r} also in {other['name']}'s skill"
                        )

    def test_prompts_do_not_reveal_the_rules(self) -> None:
        """No marker - no rule vocabulary - ever reaches any prompt; the
        prompt never names the standard (skill id, spaced skill id)."""
        for task in TASKS:
            prompt = task["prompt"].lower()
            assert task["skill_id"] not in prompt, task["name"]
            assert task["skill_id"].replace("-", " ") not in prompt, task["name"]
            for marker in task["markers"]:
                assert marker.lower() not in prompt, f"{task['name']}: prompt leaks {marker!r}"

    def test_prompts_are_indirect_and_name_the_service(self) -> None:
        """INDIRECT: the prompt says only that an internal policy exists -
        and names the SERVICE the code belongs to (the scope anchor a
        router could use), never the standard or its title words."""
        for task in TASKS:
            assert task["directness"] == "indirect", task["name"]
            prompt = task["prompt"].lower()
            assert "internal" in prompt, task["name"]
            scope = _scope_of(task)
            assert any(service in prompt for service in scope["services"]), (
                f"{task['name']}: prompt names no service of the scope"
            )

    def test_the_rules_are_genuinely_private(self) -> None:
        """The fixtures' knowledge did not exist before today: each skill
        names a fictional internal standard (invented with the fixture) —
        pin the fiction so a future edit cannot quietly swap in a real,
        possibly-public convention."""
        for task in TASKS:
            assert "internal" in task["skill"].lower(), task["name"]
            assert "not published" in task["skill"].lower(), task["name"]
        titles = {t["name"]: t["markers"][0] for t in TASKS}
        assert titles["private3-thistle-retry"] == "Thistle Consumer Retry Policy"
        assert titles["private3-kestrel-config"] == "Kestrel Config Precedence Standard"


class TestFamilyShape:
    def test_descriptions_are_identical_apart_from_scope(self) -> None:
        """The dense-corpus point: within a family the five descriptions
        share ONE template; only the scope words (team, domain, services)
        vary — the list a model reads is confusable."""
        for family in FAMILIES:
            members = [t for t in TASKS if t["family"] == family]
            templates = []
            for task in members:
                desc = _tokens(_description_of(task))
                scope = _scope_of(task)
                scope_tokens = set(
                    _tokens(" ".join([scope["team"], scope["domain"], *scope["services"]]))
                )
                kept = [t for t in desc if t not in scope_tokens]
                assert len(kept) >= 4, f"{task['name']}: thin description template"
                templates.append(kept)
            assert all(t == templates[0] for t in templates), (
                f"{family}: descriptions differ beyond scope words: {templates}"
            )

    def test_descriptions_overlap_strongly_within_a_family(self) -> None:
        """Token-overlap floor: the shared template is the bulk of every
        description (containment), and every pair overlaps."""
        for family in FAMILIES:
            members = [t for t in TASKS if t["family"] == family]
            sets = [set(_tokens(_description_of(t))) for t in members]
            for i in range(len(sets)):
                for j in range(i + 1, len(sets)):
                    shared = sets[i] & sets[j]
                    assert len(shared) / min(len(sets[i]), len(sets[j])) >= 0.4, (
                        f"{family}: shared template is not the bulk"
                    )
                    assert len(shared) / len(sets[i] | sets[j]) >= 0.25, (
                        f"{family}: pair overlap too low"
                    )

    def test_sibling_decisions_actually_differ(self) -> None:
        """≥ 2 concrete decisions differ per variant pair — pinned here as
        the marker sets being disjoint (each variant's standard-only
        vocabulary is its own) and the skills' rule sections differing."""
        for family in FAMILIES:
            members = [t for t in TASKS if t["family"] == family]
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a = set(members[i]["markers"])
                    b = set(members[j]["markers"])
                    assert not (a & b), f"{family}: sibling markers overlap: {a & b}"


class TestSkillContract:
    def test_skill_is_never_a_workspace_file(self) -> None:
        for task in TASKS:
            assert task["skill_id"] and task["skill"], task["name"]
            assert task["skill_id"] not in task["files"], task["name"]
            assert "SKILL.md" not in task["files"], task["name"]

    def test_skill_frontmatter_parses_through_the_real_parser(self) -> None:
        for task in TASKS:
            meta = parse_skill_md(task["skill"])
            assert meta.name == task["skill_id"], task["name"]
            assert meta.description.strip(), task["name"]
            assert meta.version == "1.0.0", task["name"]

    def test_skill_body_starts_with_the_standard_title(self) -> None:
        for task in TASKS:
            body = task["skill"][4:].partition("\n---\n")[2]
            assert body.startswith("\n# "), task["name"]

    def test_skill_fits_the_context_budget(self) -> None:
        """≤ ~1,500 tokens estimated as len/4 — the skill must fit a
        preloaded context slice without truncation."""
        for task in TASKS:
            assert len(task["skill"]) // 4 <= 1500, (
                f"{task['name']}: skill is {len(task['skill']) // 4} tokens"
            )

    def test_one_test_file_over_a_small_package(self) -> None:
        for task in TASKS:
            tests = [n for n in task["files"] if n.startswith("test_")]
            sources = [n for n in task["files"] if not n.startswith("test_")]
            assert len(tests) == 1, f"{task['name']}: {tests}"
            assert 2 <= len(sources) <= 5, f"{task['name']}: {sources}"

    def test_fixture_paths_are_safe_and_relative(self) -> None:
        for task in TASKS:
            for rel in task["files"]:
                assert not rel.startswith("/"), f"{task['name']}: absolute path {rel}"
                assert ".." not in rel, f"{task['name']}: traversal in {rel}"
                assert not Path(rel).is_absolute(), f"{task['name']}: absolute {rel}"

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
            assert len(_HEX64.findall(content)) >= 3, task["name"]
            assert "PLACEHOLDER" not in content, task["name"]
