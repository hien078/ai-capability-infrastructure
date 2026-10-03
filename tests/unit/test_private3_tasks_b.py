"""The private3 fixtures (builder "b") are real fixtures (§34): each must
FAIL in its shipped state and PASS after the known root-cause fix -
verified here mechanically through the real verifier path
(verify_private_fixtures materialize/apply_fix + pytest in a temp dir),
no network, no DB, no model. Also pins the E3-dense KNOWLEDGE GATE: the
rules are invented with the fixture (no model can carry them),
documented ONLY in the fixture's private skill, and not derivable from
the tests (digest-pinned, never plaintext), the shipped code (a
plausible but wrong public convention) or the task prompt (INDIRECT:
names the service, never the standard, its team title words or any
rule). The DENSE-CORPUS additions this builder owes the spec: the
per-family CONFUSION MATRIX (a sibling variant's fix must FAIL this
variant's tests - the five variants of a family are near-identical
standards that differ only in their concrete decisions) and
near-identical descriptions within a family (differing only in the
scope words).
"""

import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify_private_fixtures as vpf  # noqa: E402
from private3_tasks_b import FIXES, TASKS  # noqa: E402

#: The job's assigned fixtures: name -> (family, skill_id).
ASSIGNED: dict[str, tuple[str, str]] = {
    "private3-bramble-retire": ("api-version-retirement-headers", "bramble-retirement-headers"),
    "private3-thistle-retire": ("api-version-retirement-headers", "thistle-retirement-headers"),
    "private3-vernier-retire": ("api-version-retirement-headers", "vernier-retirement-headers"),
    "private3-solstice-retire": ("api-version-retirement-headers", "solstice-retirement-headers"),
    "private3-trellis-retire": ("api-version-retirement-headers", "trellis-retirement-headers"),
    "private3-kestrel-redact": ("log-field-redaction", "kestrel-log-redaction"),
    "private3-obsidian-redact": ("log-field-redaction", "obsidian-log-redaction"),
    "private3-mosaic-redact": ("log-field-redaction", "mosaic-log-redaction"),
    "private3-capstan-redact": ("log-field-redaction", "capstan-log-redaction"),
    "private3-parallax-redact": ("log-field-redaction", "parallax-log-redaction"),
}

#: The E2/E2C fixture names this corpus must NOT reuse (the spec's list).
FORBIDDEN_NAMES = (
    "drawbridge",
    "palisade",
    "cairn",
    "vellum",
    "ferry",
    "harbor",
    "atlas",
    "meridian",
)

FAMILIES = sorted({str(task["family"]) for task in TASKS})


def _family(family: str) -> list[dict[str, Any]]:
    return [task for task in TASKS if task["family"] == family]


def _description(task: dict[str, Any]) -> str:
    """The skill's frontmatter description (one line)."""
    skill = str(task["skill"])
    header = skill[4 : skill.index("\n---\n")]
    fields: dict[str, str] = {}
    for line in header.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields["description"]


def _services(task: dict[str, Any]) -> list[str]:
    """The team's service names, from the description's parenthetical."""
    desc = _description(task)
    paren = desc[desc.index("(") + 1 : desc.rindex(")")]
    return [name.strip() for name in paren.split(",")]


class TestFixtureVerification:
    def test_the_ten_assigned_fixtures_are_present(self) -> None:
        assert len(TASKS) == 10
        assert {str(t["name"]): (str(t["family"]), str(t["skill_id"])) for t in TASKS} == ASSIGNED

    def test_every_fixture_has_a_fix(self) -> None:
        for task in TASKS:
            assert str(task["name"]) in FIXES, task["name"]

    def test_fixes_target_workspace_sources_never_tests(self) -> None:
        for task in TASKS:
            for fix_file, old, _new in FIXES[str(task["name"])]:
                assert fix_file in task["files"], (task["name"], fix_file)
                assert not fix_file.startswith("test_"), (task["name"], fix_file)
                assert old is None, (task["name"], fix_file)

    def test_fixtures_fail_as_shipped_and_pass_after_the_fix(self) -> None:
        """The §34 discipline, per fixture: shipped state FAILS (the
        shipped code embodies the wrong public convention), fixed state
        PASSES (the known root-cause fix is a real fix)."""
        for task in TASKS:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = vpf.materialize(task, Path(tmp))
                code, _ = vpf.run_pytest(task_dir, python=sys.executable)
                assert code != 0, f"{task['name']}: shipped state PASSES (not a fixture)"
                vpf.apply_fix(task_dir, FIXES[str(task["name"])])
                code, out = vpf.run_pytest(task_dir, python=sys.executable)
                assert code == 0, f"{task['name']}: FIXED state still fails:\n{out[-400:]}"


class TestConfusionMatrix:
    def test_fix_file_names_are_family_uniform(self) -> None:
        """The confusion matrix is only meaningful because a sibling's fix
        replaces the SAME rule modules this variant ships."""
        for family in FAMILIES:
            names = {
                tuple(sorted(fix_file for fix_file, _old, _new in FIXES[str(t["name"])]))
                for t in _family(family)
            }
            assert len(names) == 1, family

    @pytest.mark.parametrize("family", FAMILIES)
    def test_own_fix_passes_and_every_sibling_fix_fails(self, family: str) -> None:
        """The dense-corpus confusion matrix, per family: FIX_i applied to
        workspace_j PASSES for i = j and FAILS for every i != j - the five
        variants are near-identical standards whose concrete decisions
        are pinned by every digest, so a sibling's rules can never
        satisfy this variant's tests."""
        members = _family(family)
        assert len(members) == 5, family
        for target in members:
            for source in members:
                with tempfile.TemporaryDirectory() as tmp:
                    task_dir = vpf.materialize(target, Path(tmp))
                    vpf.apply_fix(task_dir, FIXES[str(source["name"])])
                    code, out = vpf.run_pytest(task_dir, python=sys.executable)
                if source is target:
                    assert code == 0, f"{target['name']}: OWN fix fails (diagonal):\n{out[-400:]}"
                else:
                    assert code != 0, (
                        f"{target['name']}: sibling fix {source['name']} PASSES - "
                        "the variants are not decision-distinct"
                    )


class TestKnowledgeGate:
    def test_markers_live_only_in_their_own_skill(self) -> None:
        """The private standard's vocabulary (its markers) appears in its
        own skill and NOWHERE else: not in any workspace file of any
        fixture, not in any prompt, and not in any SIBLING skill (the
        dense-corpus requirement - each variant's distinctive vocabulary
        is its own)."""
        for task in TASKS:
            markers = list(task["markers"])
            assert 4 <= len(markers) <= 8, task["name"]
            for marker in markers:
                assert marker in str(task["skill"]), f"{task['name']}: skill lacks {marker!r}"
                for other in TASKS:
                    for rel, content in other["files"].items():
                        assert marker not in content, (
                            f"{task['name']}: {other['name']}/{rel} leaks {marker!r}"
                        )
                    if other is not task:
                        assert marker not in str(other["skill"]), (
                            f"{task['name']}: sibling skill {other['name']} carries {marker!r}"
                        )

    def test_prompts_do_not_reveal_the_rules(self) -> None:
        """No marker - no rule vocabulary - ever reaches a prompt; the
        prompt says only that an internal policy exists."""
        for task in TASKS:
            prompt = str(task["prompt"]).lower()
            skill_id = str(task["skill_id"]).lower()
            assert skill_id not in prompt, task["name"]
            assert skill_id.replace("-", " ") not in prompt, task["name"]
            for marker in task["markers"]:
                assert marker.lower() not in prompt, f"{task['name']}: leaks {marker!r}"
            assert "internal" in prompt, f"{task['name']}: must say a policy exists"

    def test_indirect_prompts_do_not_name_the_standard(self) -> None:
        """INDIRECT directness everywhere: the prompt names the SERVICE,
        never the standard, its team's name or its title words."""
        team_names = {str(t["skill_id"]).split("-")[0] for t in TASKS}
        for task in TASKS:
            assert str(task["directness"]) == "indirect", task["name"]
            prompt = str(task["prompt"]).lower()
            for team in team_names:
                assert team not in prompt, f"{task['name']}: prompt names team {team!r}"
            if str(task["family"]) == "api-version-retirement-headers":
                assert "retirement" not in prompt, task["name"]
            else:
                assert "redaction" not in prompt, task["name"]
            # the prompt names the service the workspace belongs to
            assert _services(task)[0] in str(task["prompt"]), task["name"]

    def test_the_rules_are_genuinely_private(self) -> None:
        """The fixture's knowledge did not exist before today: each skill
        names a fictional internal standard (invented with the fixture) -
        pin the fiction so a future edit cannot quietly swap in a real,
        possibly-public convention."""
        for task in TASKS:
            skill = str(task["skill"]).lower()
            assert "internal" in skill, task["name"]
            assert "not published" in skill, task["name"]
        titles = {
            "private3-bramble-retire": "Bramble Retirement Headers",
            "private3-thistle-retire": "Thistle Retirement Headers",
            "private3-vernier-retire": "Vernier Retirement Headers",
            "private3-solstice-retire": "Solstice Retirement Headers",
            "private3-trellis-retire": "Trellis Retirement Headers",
            "private3-kestrel-redact": "Kestrel Log Redaction",
            "private3-obsidian-redact": "Obsidian Log Redaction",
            "private3-mosaic-redact": "Mosaic Log Redaction",
            "private3-capstan-redact": "Capstan Log Redaction",
            "private3-parallax-redact": "Parallax Log Redaction",
        }
        for name, title in titles.items():
            by_name = {str(t["name"]): t for t in TASKS}
            assert title in str(by_name[name]["skill"]), name


class TestFixtureShape:
    def test_names_and_skill_ids_are_well_formed(self) -> None:
        for task in TASKS:
            name = str(task["name"])
            assert name.startswith("private3-"), name
            assert name == name.lower(), name
            assert re.fullmatch(r"private3-[a-z0-9]+(-[a-z0-9]+)*", name), name
            skill_id = str(task["skill_id"])
            assert skill_id == skill_id.lower(), name
            assert " " not in skill_id, name
            assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", skill_id), name

    def test_the_skill_is_never_a_workspace_file(self) -> None:
        for task in TASKS:
            assert task["skill_id"] and task["skill"], task["name"]
            assert str(task["skill_id"]) not in task["files"], task["name"]

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
        DIGESTS over canonical traces - never plaintext expected values,
        enough of them that every scenario group is pinned, and no
        placeholder ever survives."""
        hex64 = re.compile(r"\b[0-9a-f]{64}\b")
        for task in TASKS:
            test_file = next(rel for rel in task["files"] if rel.startswith("test_"))
            content = task["files"][test_file]
            pins = hex64.findall(content)
            assert len(pins) >= 4, f"{task['name']}: only {len(pins)} digest pins"
            assert len(set(pins)) == len(pins), f"{task['name']}: duplicate digests"
            assert "sha256" in content and "hexdigest" in content, task["name"]
            assert "@@" not in content, f"{task['name']}: placeholder digest left in"
            assert "PLACEHOLDER" not in content, task["name"]

    def test_skill_frontmatter_is_valid(self) -> None:
        for task in TASKS:
            skill = str(task["skill"])
            assert skill.startswith("---\n"), task["name"]
            end = skill.index("\n---\n", 3)
            fields: dict[str, str] = {}
            for line in skill[3:end].splitlines():
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
            assert fields["name"] == str(task["skill_id"]), task["name"]
            assert fields["version"] == "1.0.0", task["name"]
            assert fields["description"], task["name"]
            assert "\n" not in fields["description"], task["name"]
            assert skill[end + 5 :].startswith("\n# "), task["name"]

    def test_skill_fits_the_context_budget(self) -> None:
        """The skill body is <= ~1,500 tokens (len/4): the arms preload it
        whole into the model context."""
        for task in TASKS:
            assert len(str(task["skill"])) // 4 <= 1500, (
                f"{task['name']}: skill is {len(str(task['skill'])) // 4} tokens"
            )


class TestDenseCorpus:
    def test_two_families_of_five_variants(self) -> None:
        assert FAMILIES == ["api-version-retirement-headers", "log-field-redaction"]
        for family in FAMILIES:
            assert len(_family(family)) == 5, family

    def test_descriptions_are_near_identical_within_a_family(self) -> None:
        """The dense-corpus point: the five descriptions of a family share
        one phrasing and differ ONLY in the scope words (team, adjective,
        services) - the list a model reads is confusable by design."""
        for family in FAMILIES:
            descs = [_description(t) for t in _family(family)]
            for desc in descs:
                assert "\n" not in desc and desc.endswith("."), desc
                assert len(desc) <= 95, desc
            sets = [set(re.findall(r"[a-z]+", d.lower())) for d in descs]
            common = set.intersection(*sets)
            assert len(common) >= 6, f"{family}: only {sorted(common)}"
            for a in sets:
                for b in sets:
                    if a is b:
                        continue
                    overlap = len(a & b) / len(a | b)
                    assert overlap >= 0.4, f"{family}: descriptions drifted apart"

    def test_service_names_are_invented_and_unique(self) -> None:
        """Every team owns 2-3 invented service names, unique across the
        whole ten-variant corpus, and none reuses an E2/E2C name."""
        all_services: list[str] = []
        for task in TASKS:
            services = _services(task)
            assert 2 <= len(services) <= 3, task["name"]
            all_services.extend(services)
        assert len(set(all_services)) == len(all_services), "service name reused"
        for task in TASKS:
            for name in [str(task["skill_id"]).split("-")[0], *_services(task)]:
                assert name not in FORBIDDEN_NAMES, f"{task['name']}: reuses {name!r}"

    def test_every_workspace_names_its_service(self) -> None:
        """The code clearly belongs to ONE service of the owning team -
        and never names the standard's title."""
        for task in TASKS:
            service = _services(task)[0]
            sources = [
                content for rel, content in task["files"].items() if not rel.startswith("test_")
            ]
            assert any(service in content for content in sources), (
                f"{task['name']}: no source names the service {service!r}"
            )
            title = str(task["skill"]).split("\n# ", 1)[1].split(" (", 1)[0]
            for content in sources:
                assert title not in content, f"{task['name']}: source names the title"
