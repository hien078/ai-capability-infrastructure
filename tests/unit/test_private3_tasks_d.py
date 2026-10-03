"""The private3 dense-corpus fixtures (builder "d") are real fixtures
(section 34): each must FAIL in its shipped state and PASS after the
known root-cause fix - verified here mechanically through the real
verifier path (verify_private_fixtures materialize/apply_fix + pytest
in a temp dir), no network, no DB, no model. The DENSE twist is pinned
by the CONFUSION MATRIX: within a family the five variants share file
names and API, and FIX_i applied to workspace_j must FAIL for every
i != j (a sibling standard's rules are plausibly close but wrong) and
pass for i = j.

Also pins the knowledge gate: every standard is a fictional internal
convention invented with the fixture (no model can carry it), documented
ONLY in the variant's private skill, and not derivable from the tests
(digest-pinned over a LARGE output space, never plaintext), the shipped
code (a plausible-but-wrong public convention that must not leak the
private vocabulary) or the prompt - which is INDIRECT: it names the
SERVICE the code belongs to, never the standard, its team or any rule.
"""

import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify_private_fixtures as vpf  # noqa: E402
from private3_tasks_d import FAMILIES, FIXES, TASKS  # noqa: E402

from aci.providers.skills.parser import parse_skill_md  # noqa: E402

#: The workspace modules the contract tests import - purged between
#: in-process probes so one variant's code never leaks into another's.
WORKSPACE_MODULES = (
    "doc_id",
    "doc_registry",
    "service_report",
    "quota_window",
    "limit_headers",
    "limit_response",
)

#: The two families, five variants each.
DOC_ID = [t for t in TASKS if t["family"] == "doc-id"]
RATE_LIMIT = [t for t in TASKS if t["family"] == "rate-limit"]

#: fixture name -> the ONE service the workspace code belongs to (the
#: prompt names it; the module docstrings carry it).
SERVICES: dict[str, str] = {
    "private3-quarry-doc-id": "deed-index",
    "private3-marigold-doc-id": "claim-intake",
    "private3-cobalt-doc-id": "spec-hub",
    "private3-tundra-doc-id": "manifest-board",
    "private3-gossamer-doc-id": "contract-draft",
    "private3-beacon-rate-limit": "edge-gateway",
    "private3-vervain-rate-limit": "query-frontend",
    "private3-basalt-rate-limit": "upload-relay",
    "private3-halcyon-rate-limit": "hook-dispatch",
    "private3-willow-rate-limit": "settle-api",
}

#: fixture name -> the owning team (named ONLY in the skill).
TEAMS: dict[str, str] = {
    "private3-quarry-doc-id": "Quarry",
    "private3-marigold-doc-id": "Marigold",
    "private3-cobalt-doc-id": "Cobalt",
    "private3-tundra-doc-id": "Tundra",
    "private3-gossamer-doc-id": "Gossamer",
    "private3-beacon-rate-limit": "Beacon",
    "private3-vervain-rate-limit": "Vervain",
    "private3-basalt-rate-limit": "Basalt",
    "private3-halcyon-rate-limit": "Halcyon",
    "private3-willow-rate-limit": "Willow",
}

#: The scope words a family description may differ in (team, domain,
#: services) - everything else must be identical across the family.
DESCRIPTION_SCOPE: dict[str, set[str]] = {
    "doc-id": {
        "Quarry",
        "Marigold",
        "Cobalt",
        "Tundra",
        "Gossamer",
        "records",
        "claims",
        "docs",
        "logistics",
        "legal",
        "deed-index",
        "title-vault",
        "claim-intake",
        "referral-desk",
        "spec-hub",
        "drawing-store",
        "manifest-board",
        "waybill-print",
        "contract-draft",
        "clause-library",
    },
    "rate-limit": {
        "Beacon",
        "Vervain",
        "Basalt",
        "Halcyon",
        "Willow",
        "gateway",
        "search",
        "upload",
        "webhook",
        "payments",
        "edge-gateway",
        "status-page",
        "query-frontend",
        "crawler",
        "upload-relay",
        "encode",
        "hook-dispatch",
        "retry-relay",
        "settle-api",
        "payout-queue",
    },
}


def _pytest(task_dir: Path) -> tuple[int, str]:
    """The REAL verifier path: pytest in the materialized workspace. The
    inner suite is plugin-free (plain asserts + parametrize), so plugin
    autoload is disabled to keep 20 subprocess runs from dominating the
    unit suite (the repo baseline is ~2s of pytest startup per run on
    this host)."""
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
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


def _suite_passes(task: dict, task_dir: Path) -> bool:
    """The workspace suite's outcome, computed in-process: the contract
    test's ONLY assertions are `sha256(trace) == DIGESTS[group]` per
    group, so `every group's computed digest matches its pin` IS the
    suite's pass/fail - the exact quantity the confusion matrix needs,
    without 50 more pytest subprocesses."""
    test_file = next(rel for rel in task["files"] if rel.startswith("test_"))
    for name in WORKSPACE_MODULES:
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("p3d_probe", task_dir / test_file)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(task_dir))
    try:
        spec.loader.exec_module(mod)
        for group, builder in mod.GROUPS.items():
            text = "\n".join(builder())
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != mod.DIGESTS[group]:
                return False
        return True
    finally:
        sys.path.remove(str(task_dir))
        for name in WORKSPACE_MODULES:
            sys.modules.pop(name, None)


def _skill_title(skill: str) -> str:
    """The standard's title: the first markdown heading of the skill."""
    for line in skill.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    raise AssertionError("skill has no title heading")


def _core_description(description: str, scope: set[str]) -> str:
    """The description minus its scope words - must be identical across
    a family (the descriptions differ ONLY in scope)."""
    tokens = description.replace("(", " ").replace(")", " ").replace(",", " ").split()
    return " ".join(t for t in tokens if t not in scope)


def _frontmatter(skill: str) -> dict[str, str]:
    assert skill.startswith("---\n"), "missing frontmatter"
    end = skill.index("\n---\n", 3)
    fields: dict[str, str] = {}
    for line in skill[3:end].splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


class TestFixtureVerification:
    def test_every_fixture_has_a_fix(self) -> None:
        for task in TASKS:
            assert task["name"] in FIXES, task["name"]
            assert FIXES[task["name"]], task["name"]

    def test_fixes_are_whole_file_replacements_of_shipped_sources(self) -> None:
        """FIXES uses the verify_private_fixtures (file, None, content)
        format: whole-file replacements of SHIPPED files, never a test
        file (the tests are never touched), never a new file."""
        shipped = {t["name"]: set(t["files"]) for t in TASKS}
        for name, steps in FIXES.items():
            for fix_file, old, new in steps:
                assert fix_file in shipped[name], f"{name}: fix adds {fix_file}"
                assert not fix_file.startswith("test_"), f"{name}: fix touches a test"
                assert old is None, f"{name}: {fix_file}: splice fixes are not used here"
                assert isinstance(new, str) and new, name

    def test_fixtures_fail_as_shipped_and_pass_after_the_fix(self) -> None:
        """The section 34 discipline, per fixture: shipped state FAILS
        (the shipped code embodies a plausible-but-wrong convention),
        fixed state PASSES (the known root-cause fix is a real fix) -
        through the real verifier path, never the model."""
        for task in TASKS:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = vpf.materialize(task, Path(tmp))
                code, _ = _pytest(task_dir)
                assert code != 0, f"{task['name']}: shipped state PASSES (not a fixture)"
                vpf.apply_fix(task_dir, FIXES[task["name"]])
                code, out = _pytest(task_dir)
                assert code == 0, f"{task['name']}: FIXED state still fails:\n{out[-400:]}"

    def test_the_confusion_matrix_doc_id(self) -> None:
        """The dense-family proof: applying sibling variant i's FIX to
        workspace j FAILS for every i != j and passes for i = j - the
        five standards are near-identical in kind but mutually wrong.
        Computed in-process (see _suite_passes: the suite outcome IS the
        digest equality), so the 50 cells stay fast."""
        self._confusion_matrix(DOC_ID)

    def test_the_confusion_matrix_rate_limit(self) -> None:
        self._confusion_matrix(RATE_LIMIT)

    @staticmethod
    def _confusion_matrix(members: list[dict]) -> None:
        assert len(members) == 5, "a family has five variants"
        for i, fix_owner in enumerate(members):
            for j, ws in enumerate(members):
                with tempfile.TemporaryDirectory() as tmp:
                    task_dir = vpf.materialize(ws, Path(tmp))
                    vpf.apply_fix(task_dir, FIXES[fix_owner["name"]])
                    ok = _suite_passes(ws, task_dir)
                if i == j:
                    assert ok, f"FIX_{fix_owner['name']} -> ws_{ws['name']}: own fix fails"
                else:
                    assert not ok, (
                        f"FIX_{fix_owner['name']} -> ws_{ws['name']}: a SIBLING's rules"
                        " pass this workspace (the family is not confusable)"
                    )


class TestKnowledgeGate:
    def test_markers_live_only_in_their_own_skill(self) -> None:
        """The standard's distinctive vocabulary appears in its own skill
        and NOWHERE else: not in any sibling skill (the family is
        confusable by SCOPE, not by vocabulary), not in ANY workspace file
        of the set (the shipped code must not leak the private vocabulary),
        not in any prompt."""
        for task in TASKS:
            for marker in task["markers"]:
                assert marker in task["skill"], f"{task['name']}: skill lacks {marker!r}"
                for other in TASKS:
                    if other["name"] == task["name"]:
                        continue
                    assert marker not in other["skill"], (
                        f"{task['name']}: marker {marker!r} leaks into sibling "
                        f"{other['name']}'s skill"
                    )
                    assert marker not in other["prompt"], (
                        f"{task['name']}: marker {marker!r} leaks into {other['name']}'s prompt"
                    )
                    for rel, content in other["files"].items():
                        assert marker not in content, (
                            f"{task['name']}: {other['name']}/{rel} leaks {marker!r}"
                        )
                for rel, content in task["files"].items():
                    assert marker not in content, (
                        f"{task['name']}: {rel} leaks the private marker {marker!r}"
                    )

    def test_prompts_do_not_reveal_the_rules(self) -> None:
        """The prompt says a policy exists - never what it says: not the
        skill id, not its spaced form, not any private marker."""
        for task in TASKS:
            prompt = task["prompt"].lower()
            assert task["skill_id"] not in prompt, task["name"]
            assert task["skill_id"].replace("-", " ") not in prompt, task["name"]
            for marker in task["markers"]:
                assert marker.lower() not in prompt, f"{task['name']}: leaks {marker!r}"

    def test_prompts_are_indirect_and_name_the_service(self) -> None:
        """INDIRECT directness: the prompt names the SERVICE the code
        belongs to (a ticket would) and says an internal policy exists -
        but never the standard's title, its team, or any rule."""
        for task in TASKS:
            assert task["directness"] == "indirect", task["name"]
            prompt = task["prompt"].lower()
            assert "internal policy" in prompt, task["name"]
            assert SERVICES[task["name"]] in prompt, (
                f"{task['name']}: the prompt must name the service"
            )
            assert TEAMS[task["name"]].lower() not in prompt, (
                f"{task['name']}: the prompt names the team"
            )
            title = _skill_title(task["skill"]).lower()
            assert title not in prompt, f"{task['name']}: the prompt names the standard"

    def test_the_code_belongs_to_one_service(self) -> None:
        """Every shipped source file names its service in its docstring -
        the code clearly belongs to ONE service of the owning team."""
        for task in TASKS:
            service = SERVICES[task["name"]]
            for rel, content in task["files"].items():
                if rel.startswith("test_"):
                    continue
                assert service in content, f"{task['name']}: {rel} does not name the service"

    def test_no_file_names_the_team_or_the_standard(self) -> None:
        """The workspace belongs to the service, never to the standard:
        no file carries the team name or the standard's title."""
        for task in TASKS:
            team = TEAMS[task["name"]].lower()
            title = _skill_title(task["skill"]).lower()
            for rel, content in task["files"].items():
                low = content.lower()
                assert team not in low, f"{task['name']}: {rel} names the team"
                assert title not in low, f"{task['name']}: {rel} names the standard"

    def test_the_rules_are_genuinely_private(self) -> None:
        """The fixture's knowledge did not exist before today: each skill
        names a FICTIONAL internal standard invented with the fixture -
        pin the fiction so a future edit cannot quietly swap in a real,
        possibly-public convention."""
        kind_by_family = {
            "doc-id": "Document Identifier Standard",
            "rate-limit": "Rate-Limit Response Standard",
        }
        for task in TASKS:
            skill = task["skill"]
            title = _skill_title(skill)
            assert "internal" in skill.lower(), task["name"]
            assert "not published" in skill.lower(), task["name"]
            assert TEAMS[task["name"]] in title, task["name"]
            assert kind_by_family[task["family"]] in title, task["name"]


class TestFixtureMetadata:
    def test_task_shape(self) -> None:
        """The module contract: name/family/files/prompt/skill_id/skill/
        directness/markers, indirect prompts, 4-8 distinctive string
        markers, kebab-case unique skill ids, unique fixture names."""
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
            assert task["directness"] == "indirect", task["name"]
            assert 4 <= len(task["markers"]) <= 8, task["name"]
            assert all(isinstance(m, str) and m for m in task["markers"]), task["name"]
            assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", task["skill_id"]), task["skill_id"]
            names.add(task["name"])
            skill_ids.add(task["skill_id"])
        assert len(names) == len(TASKS) and len(skill_ids) == len(TASKS)

    def test_the_two_families_have_five_variants_each(self) -> None:
        assert len(DOC_ID) == 5 and len(RATE_LIMIT) == 5
        assert {t["family"] for t in TASKS} == {"doc-id", "rate-limit"}
        for task in TASKS:
            assert FAMILIES[task["name"]] == task["family"], task["name"]
        for family in (DOC_ID, RATE_LIMIT):
            kinds = {t["skill_id"].rsplit("-", 2)[-2] for t in family}
            assert len(kinds) == 1, f"a family is ONE kind of standard: {kinds}"

    def test_one_test_file_over_a_small_package(self) -> None:
        for task in TASKS:
            tests = [rel for rel in task["files"] if rel.startswith("test_")]
            sources = [rel for rel in task["files"] if not rel.startswith("test_")]
            assert len(tests) == 1, f"{task['name']}: {tests}"
            assert 2 <= len(sources) <= 5, f"{task['name']}: {sources}"

    def test_family_members_share_file_names_and_api(self) -> None:
        """The dense-family precondition for the confusion matrix: every
        variant of a family ships the SAME file names (FIX_i must slot
        into workspace_j) and the SAME PUBLIC function names (the
        contract tests must be able to call FIX_i's code) - only the
        private helpers may differ."""
        for family in (DOC_ID, RATE_LIMIT):
            file_sets = {tuple(sorted(t["files"])) for t in family}
            assert len(file_sets) == 1, f"family file names differ: {file_sets}"
            for rel in family[0]["files"]:
                if rel.startswith("test_"):
                    continue
                api = set()
                for task in family:
                    defs = re.findall(r"^def ([a-z][a-z_]*)\(", task["files"][rel], re.MULTILINE)
                    api.add(frozenset(d for d in defs if not d.startswith("_")))
                assert len(api) == 1, f"{rel}: the family API must be identical: {api}"
                assert all(api), f"{rel}: no public API found"

    def test_skill_is_never_a_workspace_file(self) -> None:
        for task in TASKS:
            assert task["skill_id"] and task["skill"], task["name"]
            assert task["skill_id"] not in task["files"], task["name"]

    def test_fixture_paths_are_safe_and_relative(self) -> None:
        for task in TASKS:
            for rel in task["files"]:
                assert not rel.startswith("/"), f"{task['name']}: absolute path {rel}"
                assert ".." not in rel, f"{task['name']}: traversal in {rel}"
                assert not Path(rel).is_absolute(), f"{task['name']}: absolute {rel}"

    def test_digest_pins_are_present(self) -> None:
        """The tests pin behavior as sha256 DIGESTS over the canonical
        trace - never plaintext expected values: >= 4 distinct 64-hex
        pins per test file, the pinning machinery visible, no
        placeholders left."""
        hex64 = re.compile(r"\b[0-9a-f]{64}\b")
        for task in TASKS:
            test_file = next(rel for rel in task["files"] if rel.startswith("test_"))
            content = task["files"][test_file]
            pins = hex64.findall(content)
            assert len(pins) >= 4, f"{task['name']}: only {len(pins)} digest pins"
            assert len(set(pins)) == len(pins), f"{task['name']}: duplicate digest pins"
            assert "hashlib.sha256" in content and "hexdigest" in content, task["name"]
            assert "PLACEHOLDER" not in content and "@@" not in content, task["name"]

    def test_skill_frontmatter_is_valid(self) -> None:
        """The skill text is a real SKILL.md: the repo's own parser
        accepts it, the name is the skill id, the version is 1.0.0, the
        description is one line."""
        for task in TASKS:
            fields = _frontmatter(task["skill"])
            assert fields.get("name") == task["skill_id"], task["name"]
            assert fields.get("version") == "1.0.0", task["name"]
            assert fields.get("description"), task["name"]
            assert "\n" not in fields["description"], task["name"]
            meta = parse_skill_md(task["skill"])
            assert meta.name == task["skill_id"], task["name"]
            assert meta.version == "1.0.0", task["name"]
            assert meta.description.strip(), task["name"]

    def test_skill_fits_the_context_budget(self) -> None:
        """The standard must fit a preload: <= ~1,500 tokens estimated as
        len/4 (the same estimate the kernel's capability plane uses)."""
        for task in TASKS:
            assert len(task["skill"]) // 4 <= 1500, (
                f"{task['name']}: skill is {len(task['skill']) // 4} tokens"
            )

    def test_descriptions_within_a_family_differ_only_in_scope(self) -> None:
        """The dense-corpus point: the five descriptions of a family are
        near-identical apart from the scope words (team, domain,
        services) - the list a model reads is confusable. Pinned as: the
        scope-stripped cores are IDENTICAL (the load-bearing check) and
        the pairwise token overlap stays high."""
        for family in (DOC_ID, RATE_LIMIT):
            name = family[0]["family"]
            scope = DESCRIPTION_SCOPE[name]
            cores = set()
            for task in family:
                description = _frontmatter(task["skill"])["description"]
                cores.add(_core_description(description, scope))
                tokens = set(description.replace("(", " ").replace(")", " ").split())
                for other in family:
                    other_tokens = set(
                        _frontmatter(other["skill"])["description"]
                        .replace("(", " ")
                        .replace(")", " ")
                        .split()
                    )
                    overlap = len(tokens & other_tokens) / min(len(tokens), len(other_tokens))
                    assert overlap >= 0.5, f"{name}: descriptions drift apart"
            assert len(cores) == 1, f"{name}: descriptions differ beyond scope: {cores}"

    def test_no_fake_pii_or_secrets_in_the_fixtures(self) -> None:
        """None of the fixtures carries fake PII or secrets as test input
        (no credentials, no card numbers, no personal data) - so this
        module needs NO gitleaks allowlist entry."""
        forbidden = (
            "password",
            "secret",
            "token",
            "api_key",
            "apikey",
            "credential",
            "card",
            "@example",
        )
        for task in TASKS:
            for rel, content in task["files"].items():
                low = content.lower()
                for word in forbidden:
                    assert word not in low, f"{task['name']}: {rel} carries {word!r}"
