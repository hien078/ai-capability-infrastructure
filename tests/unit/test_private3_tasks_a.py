"""The private3 dense-corpus fixtures (builder "a") are real fixtures
(section 34): each must FAIL in its shipped state and PASS after the
known root-cause fix - verified here mechanically through the same
verifier path the private-fixture tests use (materialize into a temp
dir, real pytest, no network, no DB, no model).

The DENSE-CORPUS properties are pinned on top of the E2C knowledge
gate:

- CONFUSION MATRIX: a family is 5 near-identical standards that
  differ only in WHICH team/service they apply to; applying sibling
  variant i's fix to variant j's workspace must FAIL j's tests for
  every i != j (the rules genuinely differ), while the own-variant
  fix passes (the pair test below).
- KNOWLEDGE GATE: the rules are invented with the fixture (no model
  can carry them), documented ONLY in the variant's private skill, and
  not derivable from the tests (digest-pinned over a LARGE output
  space), the shipped code (the same plausible-but-wrong generic
  convention in every variant of a family) or the task prompt.
- INDIRECTNESS: every private3 prompt names the SERVICE the code
  belongs to and says an internal policy exists - never the
  standard, its team, or any rule. Generic kind words ("rollout",
  "money") are deliberately allowed: they are shared by every
  variant of the family, so they cannot disambiguate a variant -
  the spec's own example prompt uses them.
- SCOPE-ONLY DESCRIPTIONS: the 5 descriptions of a family are
  identical apart from the team and service names - that is the
  confusable list a model reads.
"""

import os
import re
import subprocess
import sys
import tempfile
from itertools import combinations
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify_private_fixtures as vpf  # noqa: E402
from private3_tasks_a import FIXES, TASKS  # noqa: E402

from aci.providers.skills.parser import parse_skill_md  # noqa: E402

#: The two families this builder owns: family -> the 5 fixture names.
FAMILY_MEMBERS: dict[str, list[str]] = {
    "rollout-bucketing": [
        "private3-tessera-rollout",
        "private3-kiln-rollout",
        "private3-sundial-rollout",
        "private3-thistle-rollout",
        "private3-obsidian-rollout",
    ],
    "money-rounding": [
        "private3-basalt-money",
        "private3-taffeta-money",
        "private3-juniper-money",
        "private3-anvil-money",
        "private3-lantern-money",
    ],
}

#: The fictional owning teams per family - pinned so a future edit
#: cannot quietly swap in a real, possibly-public convention.
FAMILY_TEAMS: dict[str, set[str]] = {
    "rollout-bucketing": {"Tessera", "Kiln", "Sundial", "Thistle", "Obsidian"},
    "money-rounding": {"Basalt", "Taffeta", "Juniper", "Anvil", "Lantern"},
}

_DESC_RE = re.compile(
    r"^(?P<kind>.+ standard) for the (?P<team>.+) platform services "
    r"\((?P<svc0>.+), (?P<svc1>.+)\)\.$"
)
_HEX64 = re.compile(r"\b[0-9a-f]{64}\b")


def _pytest(task_dir: Path) -> tuple[int, str]:
    # -x: stop at the first failing test - the shipped and cross-applied
    # states are EXPECTED to fail, one mismatch is the proof; the fixed
    # state passes everything so -x never triggers there.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
        cwd=task_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _by_family() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for task in TASKS:
        grouped.setdefault(task["family"], []).append(task)
    return grouped


def _sources(task: dict) -> dict[str, str]:
    return {rel: content for rel, content in task["files"].items() if not rel.startswith("test_")}


def _test_file(task: dict) -> tuple[str, str]:
    tests = [(rel, content) for rel, content in task["files"].items() if rel.startswith("test_")]
    assert len(tests) == 1, (task["name"], tests)
    return tests[0]


def _description(task: dict) -> str:
    return parse_skill_md(task["skill"]).description.strip()


def _services(task: dict) -> tuple[str, str]:
    match = _DESC_RE.match(_description(task))
    assert match, f"{task['name']}: unexpected description shape: {_description(task)!r}"
    return match.group("svc0"), match.group("svc1")


class TestFixtureVerification:
    def test_every_fixture_has_a_fix(self) -> None:
        assert (
            {t["name"] for t in TASKS}
            == set(FIXES)
            == {name for names in FAMILY_MEMBERS.values() for name in names}
        )

    def test_fixtures_fail_as_shipped_and_pass_after_the_fix(self) -> None:
        """The section 34 discipline, per fixture: the shipped state FAILS
        (the fixture is actually buggy - the shipped code embodies the
        generic convention, not the variant's standard), the fixed state
        PASSES (the known root-cause fix is a real fix). Real pytest in a
        temp dir - never the model."""
        for task in TASKS:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = vpf.materialize(task, Path(tmp))
                code, out = _pytest(task_dir)
                assert code != 0, f"{task['name']}: shipped state PASSES (not a fixture)"
                assert "AssertionError" in out, (
                    f"{task['name']}: shipped state must fail on DIGEST mismatch, "
                    f"not crash:\n{out[-400:]}"
                )
                vpf.apply_fix(task_dir, FIXES[task["name"]])
                code, out = _pytest(task_dir)
                assert code == 0, f"{task['name']}: FIXED state still fails:\n{out[-400:]}"

    def test_confusion_matrix_sibling_rules_fail_every_variant(self) -> None:
        """THE dense-corpus property: within a family, applying sibling
        variant i's fix (i's standard) to variant j's workspace must FAIL
        j's tests for every i != j - the sibling's rules produce different
        digests (a model that loads the wrong sibling skill gets
        plausibly-close-but-failing code). The diagonal (i = j) is the
        pass-when-fixed proof above."""
        for family, members in _by_family().items():
            by_name = {t["name"]: t for t in members}
            for name_i in FAMILY_MEMBERS[family]:
                for name_j in FAMILY_MEMBERS[family]:
                    if name_i == name_j:
                        continue
                    with tempfile.TemporaryDirectory() as tmp:
                        task_dir = vpf.materialize(by_name[name_j], Path(tmp))
                        vpf.apply_fix(task_dir, FIXES[name_i])
                        code, out = _pytest(task_dir)
                        assert code != 0, (
                            f"CONFUSION: fix[{name_i}] PASSES workspace[{name_j}] - "
                            "the variants do not genuinely differ"
                        )
                        assert "AssertionError" in out, (
                            f"fix[{name_i}] on workspace[{name_j}] must fail on DIGEST "
                            f"mismatch, not crash:\n{out[-400:]}"
                        )


class TestKnowledgeGate:
    def test_markers_live_only_in_their_own_skill(self) -> None:
        """A marker exists ONLY in its own variant's skill: not in any
        sibling's skill (the confusable list must not cross-contaminate),
        not in ANY fixture's workspace files (digest-pinned tests, a
        plausible-but-wrong shipped convention), not in ANY prompt. All
        absence checks are case-insensitive - the strongest gate."""
        for task in TASKS:
            assert 4 <= len(task["markers"]) <= 8, task["name"]
            for marker in task["markers"]:
                assert isinstance(marker, str) and marker, task["name"]
                low = marker.lower()
                assert low in task["skill"].lower(), f"{task['name']}: skill lacks {marker!r}"
                for other in TASKS:
                    if other is task:
                        continue
                    assert low not in other["skill"].lower(), (
                        f"{task['name']}: marker {marker!r} leaks into sibling skill "
                        f"{other['name']}"
                    )
                    assert low not in other["prompt"].lower(), (
                        f"{task['name']}: marker {marker!r} leaks into prompt {other['name']}"
                    )
                    for rel, content in other["files"].items():
                        assert low not in content.lower(), (
                            f"{task['name']}: marker {marker!r} leaks into {other['name']}:{rel}"
                        )
                for rel, content in task["files"].items():
                    assert low not in content.lower(), (
                        f"{task['name']}: {rel} leaks the private marker {marker!r}"
                    )
                assert low not in task["prompt"].lower(), f"{task['name']}: prompt leaks {marker!r}"

    def test_prompts_are_indirect_and_name_the_home_service(self) -> None:
        """Every private3 prompt is INDIRECT: it says an internal policy
        exists and names the SERVICE the code belongs to - never the
        standard (skill id), its team, or any rule vocabulary (markers).
        Generic kind words are allowed by design (see module docstring):
        they are shared by all 5 variants of the family."""
        for task in TASKS:
            prompt = task["prompt"].lower()
            skill_id = task["skill_id"].lower()
            team = task["skill_id"].split("-")[0].lower()
            assert task["directness"] == "indirect", task["name"]
            assert "internal" in prompt, f"{task['name']}: prompt must say a policy exists"
            assert skill_id not in prompt, task["name"]
            assert skill_id.replace("-", " ") not in prompt, task["name"]
            assert team not in prompt, f"{task['name']}: prompt names the team"
            svc0, svc1 = _services(task)
            named = [svc for svc in (svc0, svc1) if svc.lower() in prompt]
            assert len(named) == 1, f"{task['name']}: prompt must name exactly one service"
            # ... and that service is the one the shipped code belongs to
            home = named[0].lower()
            assert any(home in content.lower() for content in _sources(task).values()), (
                f"{task['name']}: shipped code does not belong to {named[0]}"
            )

    def test_the_rules_are_genuinely_private(self) -> None:
        """The knowledge did not exist before 2026-10-03: each skill names a
        FICTIONAL internal standard of a pinned fictional team (so a future
        edit cannot quietly swap in a real, possibly-public convention),
        declares itself internal and not published, and carries a Scope
        line naming the team and its services."""
        for family, members in _by_family().items():
            teams = set()
            for task in members:
                skill = task["skill"]
                match = _DESC_RE.match(_description(task))
                assert match, task["name"]
                team = match.group("team")
                teams.add(team)
                assert f"# {team} " in skill, task["name"]
                assert "Standard (internal)" in skill, task["name"]
                assert "not published anywhere" in skill, task["name"]
                svc0, svc1 = _services(task)
                scope = next(line for line in skill.splitlines() if line.startswith("Scope:"))
                assert team in scope and svc0 in scope and svc1 in scope, task["name"]
            assert teams == FAMILY_TEAMS[family], (family, teams)

    def test_shipped_code_is_the_same_generic_convention_per_family(self) -> None:
        """The density property: every variant of a family ships the SAME
        plausible-but-wrong generic convention (modulo the service the
        code belongs to) - so a model that loads a sibling variant's
        skill gets plausibly-close-but-failing code, and the only true
        difference between workspaces is which standard governs them."""
        for family, members in _by_family().items():
            normalized: dict[str, set[str]] = {}
            for task in members:
                prompt = task["prompt"].lower()
                svc0, svc1 = _services(task)
                home = next(svc for svc in (svc0, svc1) if svc.lower() in prompt)
                for rel, content in _sources(task).items():
                    normalized.setdefault(rel, set()).add(content.replace(home, "<HOME>"))
            for rel, variants in normalized.items():
                assert len(variants) == 1, (
                    f"{family}: shipped {rel} differs between variants - the generic "
                    "convention must be shared"
                )


class TestDescriptions:
    def test_descriptions_differ_only_in_scope(self) -> None:
        """The confusable list: within a family the 5 descriptions are
        IDENTICAL apart from the team and service names (normalized
        equality), and the raw word sets overlap heavily (overlap
        coefficient >= 0.6)."""
        for family, members in _by_family().items():
            parsed = []
            scopes = set()
            for task in members:
                match = _DESC_RE.match(_description(task))
                assert match, f"{task['name']}: {_description(task)!r}"
                parsed.append(match)
                scopes.add((match.group("svc0"), match.group("svc1")))
            assert len({m.group("kind") for m in parsed}) == 1, family
            assert len({m.group("team") for m in parsed}) == len(members), family
            assert len(scopes) == len(members), f"{family}: service scopes repeat"
            normalized = {
                f"{m.group('kind')} for the <TEAM> platform services (<SVC0>, <SVC1>)."
                for m in parsed
            }
            assert len(normalized) == 1, f"{family}: descriptions differ beyond scope"
            for a, b in combinations(parsed, 2):
                words_a = set(re.findall(r"[a-z0-9]+", a.group(0).lower()))
                words_b = set(re.findall(r"[a-z0-9]+", b.group(0).lower()))
                overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
                assert overlap >= 0.6, f"{family}: description overlap too low: {overlap}"


class TestFixtureShape:
    def test_task_shape(self) -> None:
        names: set[str] = set()
        skill_ids: set[str] = set()
        for task in TASKS:
            assert set(task) == {
                "name",
                "family",
                "files",
                "prompt",
                "skill_id",
                "skill",
                "directness",
                "markers",
            }
            assert task["name"].startswith("private3-"), task["name"]
            assert task["family"] in FAMILY_MEMBERS, task["name"]
            assert task["name"] in FAMILY_MEMBERS[task["family"]], task["name"]
            assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", task["skill_id"]), task["skill_id"]
            assert task["skill_id"] == task["skill_id"].lower(), task["name"]
            names.add(task["name"])
            skill_ids.add(task["skill_id"])
        assert len(names) == len(TASKS) == 10
        assert len(skill_ids) == len(TASKS)
        for family, members in FAMILY_MEMBERS.items():
            assert len(members) == 5, family

    def test_skill_is_never_a_workspace_file(self) -> None:
        for task in TASKS:
            assert task["skill_id"] and task["skill"], task["name"]
            assert task["skill_id"] not in task["files"], task["name"]

    def test_one_test_file_over_a_small_package(self) -> None:
        for task in TASKS:
            _test_file(task)
            sources = _sources(task)
            assert 2 <= len(sources) <= 5, f"{task['name']}: {sorted(sources)}"

    def test_fixture_paths_are_safe_and_relative(self) -> None:
        for task in TASKS:
            for rel in task["files"]:
                assert not rel.startswith("/"), f"{task['name']}: absolute path {rel}"
                assert ".." not in rel, f"{task['name']}: traversal in {rel}"
                assert not Path(rel).is_absolute(), f"{task['name']}: absolute {rel}"

    def test_digest_pins_are_present(self) -> None:
        """The workspace tests pin the standard's decisions as sha256
        DIGESTS over canonical traces - never plaintext expected values -
        and enough distinct ones that every scenario is pinned."""
        for task in TASKS:
            _rel, content = _test_file(task)
            pins = _HEX64.findall(content)
            assert len(pins) >= 4, f"{task['name']}: only {len(pins)} digest pins"
            assert len(set(pins)) == len(pins), f"{task['name']}: duplicate digest pins"
            assert "hashlib.sha256" in content, task["name"]
            assert "PLACEHOLDER" not in content, task["name"]

    def test_skill_frontmatter_is_valid(self) -> None:
        """The skill text is a real SKILL.md: the repo's own parser accepts
        it, the name is the skill id, the version is pinned 1.0.0, the
        description is one line."""
        for task in TASKS:
            meta = parse_skill_md(task["skill"])
            assert meta.name == task["skill_id"], task["name"]
            assert meta.version == "1.0.0", task["name"]
            assert meta.description.strip(), task["name"]
            assert "\n" not in meta.description, task["name"]

    def test_skill_fits_the_context_budget(self) -> None:
        """The standard must fit a preload: <= 1,500 tokens estimated as
        len/4 (the same estimate the kernel's capability plane uses)."""
        for task in TASKS:
            estimated = len(task["skill"]) / 4
            assert estimated <= 1500, f"{task['name']}: skill is {estimated:.0f} tokens"

    def test_fixes_are_whole_file_replacements_of_shipped_sources(self) -> None:
        """FIXES uses the verify_private_fixtures format: (file, None,
        content) whole-file replacements of shipped SOURCE files - never
        the tests, never a file the workspace does not ship."""
        shipped = {t["name"]: set(t["files"]) for t in TASKS}
        for name, steps in FIXES.items():
            assert name in shipped, name
            for fix_file, old, new in steps:
                assert fix_file in shipped[name], f"{name}: fix touches unshipped {fix_file}"
                assert not fix_file.startswith("test_"), f"{name}: fix touches the tests"
                assert old is None, f"{name}: {fix_file}: splice fixes are not used here"
                assert isinstance(new, str) and new, name
