"""E2C arm tests (job e2c-measure) — the --set private2 integration pins.

The E2C measurement replicates E2B's F-vs-Bp question on 7 NEW private
fixtures (four parallel builders, assembled by scripts/private2_tasks.py).
These pins keep the integration honest WITHOUT a DB, network or model:

* --set private2 selects exactly the 7 builder fixtures — disjoint from
  every existing set, whose packs stay byte-identical (the default is still
  the verified eight).
* Arms R/F/Bp/Bq are valid on private2 (they need a per-case private skill,
  which every private2 fixture carries) and still refused on every other
  non-private set.
* The intended-skill map and the directness axis (named vs indirect) are
  wired into the runner's records and report.
* verify_private_fixtures.py covers private2 through the same fail-as-shipped
  / pass-when-fixed core (the heavy per-fixture verification is pinned by the
  builders' own tests); e2b_setup_registry.py selects the right set and keeps
  refusing the operational databases.
"""

import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import e2b_setup_registry  # noqa: E402
import run_hbench  # noqa: E402
import verify_private_fixtures as vpf  # noqa: E402
from private2_tasks import (  # noqa: E402
    PRIVATE2_DIRECTNESS,
    PRIVATE2_FIXES,
    PRIVATE2_INTENDED_SKILLS,
    PRIVATE2_TASKS,
)

#: The 7 E2C fixtures (builder order a, b, c, d) with their axes.
EXPECTED: dict[str, tuple[str, str]] = {
    # name: (skill_id, directness)
    "private2-cairn-money": ("cairn-money-standard", "named"),
    "private2-drawbridge-rollout": ("drawbridge-rollout-standard", "indirect"),
    "private2-palisade-redaction": ("palisade-log-hygiene", "named"),
    "private2-cairn-sunset": ("cairn-api-sunset-policy", "indirect"),
    "private2-vellum-order-id": ("vellum-order-id-standard", "named"),
    "private2-queue-consumer-retry": ("ferry-consumer-retry-policy", "indirect"),
    "private2-infra-config": ("harbor-config-resolution", "indirect"),
}

#: A zero-job round: --cases names no real fixture, so main() validates
#: everything (arms, set, flags) and writes a report WITHOUT any model call,
#: DB connection or sandbox dependency.
ZERO_JOB = ["--cases", "no-such-fixture", "--sandbox", "none"]


class TestPrivate2FixtureSet:
    """--set private2: the 7 builder fixtures, and NOTHING else moves."""

    def test_private2_set_is_the_seven_builder_fixtures(self) -> None:
        private2 = run_hbench._fixture_set("private2")
        assert [f["name"] for f in private2] == list(EXPECTED)
        assert len(private2) == 7

    def test_private2_set_is_disjoint_from_every_other_set(self) -> None:
        private2 = {f["name"] for f in run_hbench._fixture_set("private2")}
        for other in ("verified", "domain", "private", "horizon"):
            assert not private2 & {f["name"] for f in run_hbench._fixture_set(other)}, other

    def test_default_set_is_still_the_verified_eight(self) -> None:
        """Adding 'private2' must not change the default pack."""
        assert [f["name"] for f in run_hbench._fixture_set("verified")] == [
            f["name"] for f in run_hbench._all_fixtures()
        ]

    def test_every_private2_fixture_carries_the_e2c_axes(self) -> None:
        for fixture in run_hbench._fixture_set("private2"):
            assert fixture["skill_id"] and fixture["skill"], fixture["name"]
            # The private skill is metadata, NEVER a workspace file.
            assert fixture["skill_id"] not in fixture["files"], fixture["name"]
            assert any(name.startswith("test_") for name in fixture["files"]), fixture["name"]
            assert fixture["directness"] in ("named", "indirect"), fixture["name"]
            assert fixture["markers"], fixture["name"]

    def test_directness_is_three_named_four_indirect(self) -> None:
        """The E2C secondary axis: enough indirect prompts to answer
        "does routing survive a prompt that never names the standard?"."""
        direct = [f["directness"] for f in run_hbench._fixture_set("private2")]
        assert direct.count("named") == 3
        assert direct.count("indirect") == 4

    def test_intended_skill_map_covers_all_seven(self) -> None:
        for fixture in run_hbench._fixture_set("private2"):
            assert run_hbench._intended_skill(fixture["name"]) == fixture["skill_id"]
        assert run_hbench._intended_skill("private2-cairn-money") == "cairn-money-standard"
        # The existing sets' lookups are unchanged.
        assert run_hbench._intended_skill("private-atlas-retry") == "atlas-error-standard"
        assert run_hbench._intended_skill("domain-brand-palette") == "brand-guidelines"
        assert run_hbench._intended_skill("multi-config-precedence") == ""

    def test_private2_prompts_never_contain_the_skill_id(self) -> None:
        """The skill ID (the routing key) appears in NO prompt — named or
        indirect: the round measures retrieval, never prompt-echo."""
        for fixture in run_hbench._fixture_set("private2"):
            prompt = fixture["prompt"].lower()
            assert fixture["skill_id"] not in prompt, fixture["name"]

    def test_directness_is_honored_by_the_prompts(self) -> None:
        """The E2C axis, as the builders defined it: a NAMED prompt names
        the standard's proper name (the fiction's first word — "the Cairn
        money standard", "the Palisade log redaction rules", "the Vellum
        order identifier standard"); an INDIRECT prompt says only "our
        internal policy" and contains NEITHER the proper name NOR the
        standard's title words."""
        for fixture in run_hbench._fixture_set("private2"):
            prompt = fixture["prompt"].lower()
            proper = fixture["skill_id"].split("-")[0]
            if fixture["directness"] == "named":
                assert proper in prompt, fixture["name"]
            else:
                assert proper not in prompt, fixture["name"]
                assert fixture["skill_id"].replace("-", " ") not in prompt, fixture["name"]


class TestPrivate2ArmsValid:
    """Arms R/F/Bp/Bq are defined for the private sets (they need the
    per-case private skill) — private2 joins private; every other set is
    still refused loudly BEFORE any run."""

    def test_arm_r_and_f_pass_the_set_check_for_private2(self, tmp_path: Path) -> None:
        """A zero-job round returns 0 — the set check passed (it would
        exit 2 otherwise) and no model/DB was touched."""
        out = tmp_path / "rf.json"
        argv = [
            "--api-key",
            "k",
            "--set",
            "private2",
            "--arms",
            "R,F",
            *ZERO_JOB,
            "--out",
            str(out),
        ]
        assert run_hbench.main(argv) == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["fixture_set"] == "private2"
        assert report["arms"] == ["R", "F"]

    def test_arm_k_runs_a_zero_job_private2_round_with_the_axes(self, tmp_path: Path) -> None:
        """The report carries the private2 axes: the intended-skill map AND
        the directness map (the E2C secondary analysis reads them)."""
        out = tmp_path / "k.json"
        argv = [
            "--api-key",
            "k",
            "--set",
            "private2",
            "--arms",
            "K",
            *ZERO_JOB,
            "--out",
            str(out),
        ]
        assert run_hbench.main(argv) == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["intended_skills"] == PRIVATE2_INTENDED_SKILLS
        assert report["directness"] == PRIVATE2_DIRECTNESS

    def test_arms_bp_bq_pass_the_set_check_for_private2(self, capsys: Any) -> None:
        """Bp/Bq on private2 fail on the MISSING REGISTRY FLAGS (the later
        check), never on the set — proving the set check passed."""
        assert run_hbench.main(["--api-key", "k", "--set", "private2", "--arms", "Bp"]) == 2
        err = capsys.readouterr().err
        assert "need BOTH --registry-db-url and --object-store-root" in err
        assert "need --set private" not in err

    def test_arms_still_refused_outside_the_private_sets(self) -> None:
        for arm in ("R", "F", "Bp", "Bq"):
            assert run_hbench.main(["--api-key", "k", "--set", "verified", "--arms", arm]) == 2
            assert run_hbench.main(["--api-key", "k", "--set", "domain", "--arms", arm]) == 2
            assert run_hbench.main(["--api-key", "k", "--set", "horizon", "--arms", arm]) == 2

    def test_f_doc_path_is_the_skill_id_exactly_as_e2b(self) -> None:
        """F's doc path contract is unchanged: ONE plain repo document at
        docs/standards/<skill_id>.md — parameterized by the case's skill id,
        so private2 needs no new arm code."""
        assert run_hbench.file_arm_docs_path("cairn-money-standard") == (
            "docs/standards/cairn-money-standard.md"
        )

    def test_f_materializes_a_private2_workspace_plus_exactly_one_doc(self, tmp_path: Path) -> None:
        fixture = PRIVATE2_TASKS[0]
        task_dir = run_hbench.materialize_file_arm_source(fixture, tmp_path)
        for rel, content in fixture["files"].items():
            assert (task_dir / rel).read_text(encoding="utf-8") == content
        docs = [p for p in task_dir.rglob("docs/standards/*") if p.is_file()]
        assert [p.relative_to(task_dir).as_posix() for p in docs] == [
            run_hbench.file_arm_docs_path(str(fixture["skill_id"]))
        ]
        assert docs[0].read_text(encoding="utf-8") == fixture["skill"]


class TestPrivate2Verifier:
    """verify_private_fixtures.py covers the private2 set through the same
    fail-as-shipped / pass-when-fixed core (the per-fixture heavy
    verification is pinned by the builders' own tests)."""

    def test_every_private2_fixture_has_a_whole_file_fix(self) -> None:
        for task in PRIVATE2_TASKS:
            assert task["name"] in PRIVATE2_FIXES, task["name"]
            for fix_file, old, _new in PRIVATE2_FIXES[task["name"]]:
                assert fix_file in task["files"], (task["name"], fix_file)
                assert not fix_file.startswith("test_"), (task["name"], fix_file)
                assert old is None, (task["name"], fix_file)

    def test_the_aggregator_merges_all_four_builders(self) -> None:
        import private2_tasks_a  # noqa: PLC0415
        import private2_tasks_b  # noqa: PLC0415
        import private2_tasks_c  # noqa: PLC0415
        import private2_tasks_d  # noqa: PLC0415

        assert [f["name"] for f in PRIVATE2_TASKS] == [
            *[f["name"] for f in private2_tasks_a.TASKS],
            *[f["name"] for f in private2_tasks_b.TASKS],
            *[f["name"] for f in private2_tasks_c.TASKS],
            *[f["name"] for f in private2_tasks_d.TASKS],
        ]
        for fixes in (
            private2_tasks_a.FIXES,
            private2_tasks_b.FIXES,
            private2_tasks_c.FIXES,
            private2_tasks_d.FIXES,
        ):
            for name, steps in fixes.items():
                assert PRIVATE2_FIXES[name] == steps, name

    def test_verify_fixtures_covers_private2(self) -> None:
        """The --set private2 path verifies the 7 fixtures against the
        merged fixes (verify_all's default stays the 2 E2B fixtures)."""
        import private2_tasks  # noqa: PLC0415

        assert vpf.verify_fixtures is not None
        # The default set is unchanged: verify_all reads the module-level
        # E2B names (the existing test monkeypatches them).
        assert [t["name"] for t in vpf.PRIVATE_TASKS] == [
            "private-atlas-retry",
            "private-meridian-release",
        ]
        assert set(private2_tasks.PRIVATE2_FIXES) == set(EXPECTED)


class TestSetupScriptPrivate2:
    """e2b_setup_registry.py ingests the private2 skills through the same
    real gates (the E2C grant: aci_e2b ONLY) and still refuses the
    operational databases for BOTH sets."""

    def test_selected_tasks_per_set(self) -> None:
        assert [t["name"] for t in e2b_setup_registry.selected_tasks("private")] == [
            t["name"] for t in e2b_setup_registry.PRIVATE_TASKS
        ]
        assert [t["name"] for t in e2b_setup_registry.selected_tasks("private2")] == [
            t["name"] for t in PRIVATE2_TASKS
        ]

    def test_source_labels_exist_for_both_sets(self) -> None:
        assert set(e2b_setup_registry._SOURCE_LABELS) == {"private", "private2"}
        repo, ref = e2b_setup_registry._SOURCE_LABELS["private"]
        assert repo == "aci/e2b-private-standards" and "e2b experiment" in ref
        repo2, ref2 = e2b_setup_registry._SOURCE_LABELS["private2"]
        assert repo2 == "aci/e2c-private-standards" and "e2c experiment" in ref2

    def test_refuses_operational_databases_regardless_of_set(self) -> None:
        for set_name in ("private", "private2"):
            assert (
                e2b_setup_registry.main(
                    [
                        "--database-url",
                        "postgresql+psycopg://aci:aci@localhost:5432/aci_bench",
                        "--object-store-root",
                        "/tmp/objects",
                        "--set",
                        set_name,
                    ]
                )
                == 2
            )

    def test_allows_the_experiment_copy_for_both_sets(self) -> None:
        for _set_name in ("private", "private2"):
            assert (
                e2b_setup_registry.refuse_operational_database(
                    "postgresql+psycopg://aci:aci@100.126.242.46:5432/aci_e2b"
                )
                is None
            )
