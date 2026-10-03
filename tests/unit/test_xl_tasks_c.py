"""The XL fixtures (builder "c") are real fixtures — verified here
mechanically, no model, no network, no DB (the §34 discipline, applied to
fixture-building itself), through the REAL harness grader
(``xl_bench.hidden_suite_result`` — hidden paths only, agent tests
stripped) against the UNIFIED change semantics (job xl-fix, 2026-10-03):

- FAIL-AS-SHIPPED: the seed alone fails the ORIGINAL hidden suite.
- PASS-WHEN-SOLVED: the PRE-change reference passes the ORIGINAL suite.
- THE BITE: the unpatched reference fails the POST-CHANGE suite (original
  minus invalidates plus the change tests) — every change test that is not
  a declared GUARD has a failing parametrization.
- PASS-WHEN-SOLVED (perturbed): reference + change/patch passes the
  post-change suite.
- THE REVERSAL IS EXACT: the original suite against the PATCHED reference
  fails exactly the invalidated tests.
- The hidden tests are NEVER inside the seed; the task text reveals no
  hidden test; every path is a safe relative path.
- The research answer key is DERIVED from the same world model that renders
  the corpus (corpus and key cannot drift) — proven here by RECOMPUTING the
  CSV answers from the rendered CSV text itself.
- The postbox v1 design (serve embeds the worker, stats cached in-process)
  is what the BRIEF mandates and the original suite pins; the reference IS
  that design (generated from the separated text by anchors) and the change
  patch reverses exactly it — the rework lever the perturbation exploits.

§34 caveat for any round run on these fixtures: author-built, small n,
one model — directional only.
"""

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import xl_bench as xb  # noqa: E402
import xl_tasks_c as xlc  # noqa: E402

PY = sys.executable
RESEARCH = next(t for t in xlc.TASKS if t["kind"] == "research")
POSTBOX = next(t for t in xlc.TASKS if t["kind"] == "project")


# ---------------------------------------------------------------------------
# Materialize + grade — through the REAL harness grader
# (xb.hidden_suite_result: fresh copy, agent tests stripped, hidden tests
# copied in at their relative paths, invalidated originals removed/deselected,
# pytest on the hidden paths only, pass fraction + FAILED ids from -rf).
# ---------------------------------------------------------------------------


def _materialize(task: dict[str, Any], root: Path, *, reference: bool = False) -> Path:
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    for rel, content in task["seed"].items():
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if reference:
        for rel, content in task["reference"].items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    return work


def _apply_patch(task: dict[str, Any], work: Path) -> None:
    """The change patch: a full-file overlay over the reference."""
    for rel, content in task["change"]["patch"].items():
        target = work / rel
        assert target.is_file(), f"patch targets a non-reference file: {rel}"
        target.write_text(content, encoding="utf-8")


def _grade(work: Path, task: dict[str, Any], *, perturbed: bool) -> dict[str, Any]:
    """One hidden-suite grade through the REAL harness mechanics."""
    with tempfile.TemporaryDirectory(prefix="xl-c-hidden-") as hidden_tmp:
        hidden = Path(hidden_tmp) / "hidden"
        for rel, content in task["hidden_tests"].items():
            target = hidden / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        extra = None
        if perturbed:
            extra = Path(hidden_tmp) / "change"
            for rel, content in task["change"]["hidden_tests"].items():
                target = extra / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
        seed = Path(tempfile.mkdtemp(prefix="xl-c-seed-")) / "workspace"
        for rel, content in task["seed"].items():
            target = seed / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        try:
            return xb.hidden_suite_result(
                work,
                hidden,
                extra_hidden=extra,
                invalidated=task["change"]["invalidates"] if perturbed else None,
                seed_dir=seed,
            )
        finally:
            shutil.rmtree(seed.parent, ignore_errors=True)


def _assert_case(
    task: dict[str, Any], *, perturbed: bool, reference: bool, expect_pass: bool
) -> dict[str, Any]:
    """One unified verify_fixture case, as a reusable assertion."""
    with tempfile.TemporaryDirectory() as tmp:
        work = _materialize(task, Path(tmp), reference=reference)
        result = _grade(work, task, perturbed=perturbed)
    label = f"{task['name']} reference={reference} perturbed={perturbed}"
    assert result["passed"] + result["failed"] + result["errors"] > 0, (
        f"{label}: zero tests ran (a fixture bug, not a result)"
    )
    if expect_pass:
        assert result["pass_fraction"] == 1.0, (
            f"{label}: expected pass-when-solved, "
            f"got {result['pass_fraction']}:\n{result['summary_tail']}"
        )
    else:
        assert result["pass_fraction"] < 1.0, (
            f"{label}: expected fail, got {result['pass_fraction']} (not a fixture)"
        )
    return result


def _change_ids(task: dict[str, Any]) -> set[str]:
    return {
        f"{Path(rel).name}::{name}"
        for rel, content in task["change"]["hidden_tests"].items()
        for name in re.findall(r"^def (test_\w+)", content, re.M)
    }


def _assert_unified_matrix(task: dict[str, Any]) -> None:
    """The SIX unified verify_fixture cases for one fixture (job xl-fix):
    seed fails both suites; the PRE-change reference passes the original
    suite; THE BITE (the unpatched reference fails the post-change suite,
    every non-guard change test failing); reference+patch passes the
    post-change suite; and the reversal is EXACT (the original suite on the
    patch fails exactly the invalidated tests)."""
    # 1. fail-as-shipped (original suite)
    _assert_case(task, perturbed=False, reference=False, expect_pass=False)
    # 2. pass-when-solved (original suite)
    _assert_case(task, perturbed=False, reference=True, expect_pass=True)
    # 3. fail-as-shipped (post-change suite)
    _assert_case(task, perturbed=True, reference=False, expect_pass=False)
    # 4. THE BITE — every non-guard change test fails on the unpatched reference
    bite = _assert_case(task, perturbed=True, reference=True, expect_pass=False)
    change_ids = _change_ids(task)
    guards = set(task["change"]["guards"])
    assert guards <= change_ids, task["name"]
    not_biting = {
        cid
        for cid in change_ids - guards
        if not any(xb._id_covers(cid, failed) for failed in bite["failed_ids"])
    }
    assert not not_biting, (
        f"{task['name']}: change tests that do not bite pre-patch "
        f"(and are not guards): {sorted(not_biting)}"
    )
    # 5. pass-when-solved (reference + patch, post-change suite)
    with tempfile.TemporaryDirectory() as tmp:
        work = _materialize(task, Path(tmp), reference=True)
        _apply_patch(task, work)
        result = _grade(work, task, perturbed=True)
    assert result["pass_fraction"] == 1.0, (
        f"{task['name']}: reference+patch fails the post-change suite:\n{result['summary_tail']}"
    )
    # 6. the reversal is EXACT — the original suite on the PATCHED reference
    #    fails exactly the invalidated tests (each one, and nothing else)
    with tempfile.TemporaryDirectory() as tmp:
        work = _materialize(task, Path(tmp), reference=True)
        _apply_patch(task, work)
        reversal = _grade(work, task, perturbed=False)
    failed = set(reversal["failed_ids"])
    for failed_id in failed:
        assert any(xb._id_covers(entry, failed_id) for entry in task["change"]["invalidates"]), (
            f"{task['name']}: the reversal over-reaches: {failed_id}"
        )
    for entry in task["change"]["invalidates"]:
        file_name = entry if "::" not in entry else entry.split("::", 1)[0]
        for name in re.findall(r"^def (test_\w+)", task["hidden_tests"][file_name], re.M):
            assert any(xb._id_covers(f"{file_name}::{name}", failed_id) for failed_id in failed), (
                f"{task['name']}: invalidated {file_name}::{name} does not fail on the patch"
            )


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _parse_csv(text: str) -> list[dict[str, str]]:
    lines = [line for line in text.strip().splitlines() if line]
    header = lines[0].split(",")
    return [dict(zip(header, line.split(","), strict=False)) for line in lines[1:]]


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _walk_strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _walk_strings(v)]
    return []


# ---------------------------------------------------------------------------
# The fixture contract (shape, safety, hidden-vs-seed, change pack)
# ---------------------------------------------------------------------------


class TestFixtureContract:
    def test_two_fixtures_one_per_assigned_kind(self) -> None:
        assert [t["name"] for t in xlc.TASKS] == ["xl-research-cinderpeak", "xl-project-postbox"]
        assert [t["kind"] for t in xlc.TASKS] == ["research", "project"]

    def test_task_shape(self) -> None:
        for task in xlc.TASKS:
            assert task["name"].startswith("xl-"), task["name"]
            assert task["kind"] in (
                "build",
                "ui",
                "bug",
                "feature",
                "refactor",
                "research",
                "project",
            )
            assert isinstance(task["seed"], dict) and task["seed"], task["name"]
            assert isinstance(task["task"], str) and len(task["task"]) > 200, task["name"]
            assert isinstance(task["hidden_tests"], dict) and task["hidden_tests"], task["name"]
            assert isinstance(task["reference"], dict) and task["reference"], task["name"]
            assert isinstance(task["expected_hours"], float), task["name"]
            assert 1.0 <= task["expected_hours"] <= 3.0, task["name"]
            change = task["change"]
            assert isinstance(change["note"], str) and len(change["note"]) > 100, task["name"]
            assert isinstance(change["hidden_tests"], dict) and change["hidden_tests"], task["name"]
            assert isinstance(change["invalidates"], list), task["name"]
            assert isinstance(change["patch"], dict) and change["patch"], task["name"]
            assert isinstance(change["guards"], list), task["name"]

    def test_all_paths_are_safe_and_relative(self) -> None:
        for task in xlc.TASKS:
            for group in ("seed", "hidden_tests", "reference"):
                for rel in task[group]:
                    assert isinstance(rel, str) and rel, (task["name"], rel)
                    assert not rel.startswith("/"), (task["name"], rel)
                    assert not Path(rel).is_absolute(), (task["name"], rel)
                    assert ".." not in rel.split("/"), (task["name"], rel)
                    assert "\\" not in rel, (task["name"], rel)
                    assert not rel.startswith("~"), (task["name"], rel)
            for rel in task["change"]["hidden_tests"]:
                assert not Path(rel).is_absolute(), (task["name"], rel)
                assert ".." not in rel.split("/"), (task["name"], rel)

    def test_hidden_tests_are_absent_from_the_seed(self) -> None:
        """No hidden test file name, test function name, or test body may
        appear anywhere in the seed — the agent must not be able to find
        the acceptance suite from inside the workspace."""
        for task in xlc.TASKS:
            seed_text = "\n".join(task["seed"].values())
            for rel, content in {
                **task["hidden_tests"],
                **task["change"]["hidden_tests"],
            }.items():
                assert rel not in seed_text, (
                    f"{task['name']}: seed leaks the hidden file name {rel}"
                )
                for name in re.findall(r"^def (test_\w+)", content, re.M):
                    assert name not in seed_text, f"{task['name']}: seed leaks {name}"
                for line in content.splitlines():
                    if line.startswith(("import ", "from ")) or line.startswith("def test_"):
                        continue
                    if len(line.strip()) > 40 and line.strip() in seed_text:
                        raise AssertionError(
                            f"{task['name']}: seed leaks a hidden-test line: {line.strip()[:60]}"
                        )

    def test_task_text_reveals_no_hidden_test(self) -> None:
        for task in xlc.TASKS:
            task_text = _collapse(task["task"])
            for rel in {**task["hidden_tests"], **task["change"]["hidden_tests"]}:
                assert rel not in task_text, f"{task['name']}: task text names {rel}"
            for content in {
                **task["hidden_tests"],
                **task["change"]["hidden_tests"],
            }.values():
                for name in re.findall(r"^def (test_\w+)", content, re.M):
                    assert name not in task_text, f"{task['name']}: task text leaks {name}"

    def test_research_task_text_reveals_no_answer(self) -> None:
        """The research answers are the hidden key: no answer value may
        appear in the TASK text (QUESTIONS.md legitimately names the
        questions — that is the assignment, not the key)."""
        task_text = _collapse(RESEARCH["task"])
        for source in (xlc._RESEARCH_KEY, xlc._RESEARCH_CHANGE_KEY):
            for qid, entry in source.items():
                for value in _walk_strings(entry["answer"]):
                    if len(value.strip()) < 3:
                        continue  # a 1–2 char string cannot meaningfully "leak"
                    assert _collapse(value) not in task_text, (
                        f"{qid}: task text leaks the answer {value!r}"
                    )

    def test_change_pack_shape(self) -> None:
        for task in xlc.TASKS:
            original = set(task["hidden_tests"])
            change = set(task["change"]["hidden_tests"])
            assert change and not (change & original), task["name"]
            for rel in task["change"]["invalidates"]:
                assert rel.rsplit("::", 1)[0] in original, (
                    f"{task['name']}: invalidates a non-original test {rel}"
                )
            assert task["change"]["invalidates"], task["name"]
            # the patch is a full-file overlay over the reference
            known = set(task["seed"]) | set(task["reference"])
            for rel in task["change"]["patch"]:
                assert rel in known, f"{task['name']}: patch targets an unknown file {rel}"
            # guards are real change tests
            for guard in task["change"]["guards"]:
                assert guard in _change_ids(task), f"{task['name']}: unknown guard {guard}"

    def test_write_fixture_layout_matches_the_harness_contract(self) -> None:
        """write_fixture materializes exactly the on-disk layout
        scripts/xl_bench.py consumes (workspace/ + TASK.md + hidden/ +
        change/{CHANGE_NOTE.md,hidden/,invalidates.txt,patch/,guards.txt} +
        reference/)."""
        for task in xlc.TASKS:
            with tempfile.TemporaryDirectory() as tmp:
                out = xlc.write_fixture(task, Path(tmp) / task["name"])
                assert (out / "workspace").is_dir(), task["name"]
                assert (out / "TASK.md").read_text(encoding="utf-8") == task["task"]
                assert (out / "hidden").is_dir(), task["name"]
                assert (out / "change" / "CHANGE_NOTE.md").read_text(encoding="utf-8") == (
                    task["change"]["note"]
                )
                assert (out / "change" / "invalidates.txt").read_text(encoding="utf-8").strip()
                assert (out / "change" / "guards.txt").is_file(), task["name"]
                assert (out / "reference").is_dir(), task["name"]
                for rel, content in task["seed"].items():
                    assert (out / "workspace" / rel).read_text(encoding="utf-8") == content
                for rel, content in task["hidden_tests"].items():
                    assert (out / "hidden" / rel).read_text(encoding="utf-8") == content
                for rel, content in task["change"]["hidden_tests"].items():
                    assert (out / "change" / "hidden" / rel).read_text(encoding="utf-8") == content
                for rel, content in task["change"]["patch"].items():
                    assert (out / "change" / "patch" / rel).read_text(encoding="utf-8") == content
                assert (out / "change" / "guards.txt").read_text(encoding="utf-8").split() == (
                    task["change"]["guards"]
                )
                for rel, content in task["reference"].items():
                    assert (out / "reference" / rel).read_text(encoding="utf-8") == content


# ---------------------------------------------------------------------------
# The research fixture (xl-research-cinderpeak)
# ---------------------------------------------------------------------------


class TestResearchFixture:
    def test_corpus_size_is_in_the_xl_range(self) -> None:
        assert 50 <= len(RESEARCH["seed"]) <= 150, len(RESEARCH["seed"])

    def test_corpus_layout(self) -> None:
        for rel in RESEARCH["seed"]:
            assert (
                rel == "README.md" or rel == "QUESTIONS.md" or (rel.startswith(("docs/", "data/")))
            ), rel
            assert Path(rel).suffix in (".md", ".csv"), rel

    def test_every_key_citation_exists_in_the_corpus(self) -> None:
        for source in (xlc._RESEARCH_KEY, xlc._RESEARCH_CHANGE_KEY):
            for qid, entry in source.items():
                for citation in entry["citations"]:
                    assert citation in RESEARCH["seed"], f"{qid}: missing citation {citation}"

    def test_quotes_are_verbatim_lines_of_cited_files(self) -> None:
        for qid, quote in xlc._RESEARCH_QUOTES.items():
            entry = xlc._RESEARCH_KEY.get(qid) or xlc._RESEARCH_CHANGE_KEY[qid]
            haystacks = [_collapse(RESEARCH["seed"][c]) for c in entry["citations"]]
            assert any(_collapse(quote) in text for text in haystacks), f"{qid}: quote not verbatim"

    def test_csv_answers_are_recomputable_from_the_corpus(self) -> None:
        """The corpus↔key consistency proof: recompute every CSV-derived
        answer from the RENDERED CSV text (not the world model) and demand
        equality with the key."""
        usage_2025 = _parse_csv(RESEARCH["seed"]["data/usage-2025.csv"])
        tenants = {
            row["tenant_id"]: row for row in _parse_csv(RESEARCH["seed"]["data/tenants.csv"])
        }
        latency = _parse_csv(RESEARCH["seed"]["data/latency.csv"])
        adoption = _parse_csv(RESEARCH["seed"]["data/adoption.csv"])
        incidents = _parse_csv(RESEARCH["seed"]["data/incidents.csv"])

        # q03: highest 2025 volume tenant + its tier
        totals: dict[str, int] = {}
        for row in usage_2025:
            totals[row["tenant_id"]] = totals.get(row["tenant_id"], 0) + int(row["events"])
        best = max(totals, key=lambda t: totals[t])
        assert xlc._RESEARCH_KEY["q03"]["answer"] == {
            "tenant_id": best,
            "tier": tenants[best]["tier"],
        }

        # q07: the 2026-H1 p99 spike for a 1.0.x version + the month's incident
        rows = [
            row
            for row in latency
            if row["version"].startswith("1.0.")
            and row["month"].startswith("2026")
            and int(row["month"][-2:]) <= 6
        ]
        spike = max(rows, key=lambda row: int(row["p99_ms"]))
        month_incidents = [
            row["incident"] for row in incidents if row["date"].startswith(spike["month"][:7])
        ]
        assert xlc._RESEARCH_KEY["q07"]["answer"] == {
            "month": spike["month"],
            "p99_ms": int(spike["p99_ms"]),
            "incident": month_incidents[0],
        }
        assert len(month_incidents) == 1

        # q09: the month 1.0.0 installs first exceeded 500 + the total that month
        first = min(
            row["month"]
            for row in adoption
            if row["version"] == "1.0.0" and int(row["installs"]) > 500
        )
        total_that_month = sum(int(row["installs"]) for row in adoption if row["month"] == first)
        assert xlc._RESEARCH_KEY["q09"]["answer"] == {
            "month": first,
            "total_installs": total_that_month,
        }

        # q12: gold tenants + their combined 2025 volume
        gold = sorted(tid for tid, row in tenants.items() if row["tier"] == "gold")
        combined = sum(int(row["events"]) for row in usage_2025 if row["tenant_id"] in gold)
        assert xlc._RESEARCH_KEY["q12"]["answer"] == {
            "gold_tenants": gold,
            "combined_2025_events": combined,
        }

        # q16: component counts per year + each year's highest-severity fix
        counts: dict[str, dict[str, int]] = {}
        for row in incidents:
            year = row["date"][:4]
            counts.setdefault(year, {})
            counts[year][row["component"]] = counts[year].get(row["component"], 0) + 1
        order = {"sev-1": 0, "sev-2": 1, "sev-3": 2, "sev-4": 3}
        hi = {
            year: min(
                (r for r in incidents if r["date"].startswith(year)),
                key=lambda r: order[r["severity"]],
            )
            for year in ("2024", "2025")
        }
        assert xlc._RESEARCH_CHANGE_KEY["q16"]["answer"] == {
            "most_incidents_2024": max(counts["2024"], key=counts["2024"].get),
            "most_incidents_2025": max(counts["2025"], key=counts["2025"].get),
            "highest_severity_2024": hi["2024"]["incident"],
            "fix_2024": hi["2024"]["resolution"],
            "highest_severity_2025": hi["2025"]["incident"],
            "fix_2025": hi["2025"]["resolution"],
        }

    def test_doc_answers_appear_in_their_cited_files(self) -> None:
        """Every STRING leaf of every answer is present (whitespace-normalized)
        in at least one of that answer's cited corpus files — the key is
        derivable from the corpus, never invented."""
        for source in (xlc._RESEARCH_KEY, xlc._RESEARCH_CHANGE_KEY):
            for qid, entry in source.items():
                haystack = _collapse("\n".join(RESEARCH["seed"][c] for c in entry["citations"]))
                for value in _walk_strings(entry["answer"]):
                    assert _collapse(value) in haystack, f"{qid}: {value!r} not in cited docs"

    def test_question_ids_line_up(self) -> None:
        questions = RESEARCH["seed"]["QUESTIONS.md"]
        for qid in xlc._RESEARCH_KEY:
            assert f"## {qid} —" in questions, qid
        for qid in xlc._RESEARCH_CHANGE_KEY:
            assert f"## {qid}" not in questions, f"{qid} must arrive only via the change note"
            assert qid in RESEARCH["change"]["note"], f"{qid} missing from the change note"

    def test_reference_answers_the_original_set_without_quotes(self) -> None:
        """The PRE-change reference: exactly q01–q14 (the original QUESTIONS.md
        set), answers + citations, NO quote fields (the quote requirement
        arrives with the change) — so the change tests bite on it."""
        reference = json.loads(RESEARCH["reference"]["answers.json"])
        entries = {a["id"]: a for a in reference["answers"]}
        assert set(entries) == set(xlc._RESEARCH_KEY), sorted(entries)
        for qid, entry in entries.items():
            assert entry["answer"] == xlc._RESEARCH_KEY[qid]["answer"], qid
            assert set(entry["citations"]) >= set(xlc._RESEARCH_KEY[qid]["citations"]), qid
            assert "quote" not in entry, qid

    def test_the_patch_answers_every_living_question_with_the_quotes(self) -> None:
        """The PATCH (the reference's own audit implementation): every
        question except the WITHDRAWN q07, with the audited quotes."""
        patched = json.loads(RESEARCH["change"]["patch"]["answers.json"])
        entries = {a["id"]: a for a in patched["answers"]}
        assert "q07" not in entries, "the withdrawn q07 must be removed by the patch"
        for source in (xlc._RESEARCH_KEY, xlc._RESEARCH_CHANGE_KEY):
            for qid, entry in source.items():
                if qid == "q07":
                    continue
                assert qid in entries, qid
                assert entries[qid]["answer"] == entry["answer"], qid
                assert set(entries[qid]["citations"]) >= set(entry["citations"]), qid
        for qid in xlc._RESEARCH_QUOTES:
            assert entries[qid].get("quote") == xlc._RESEARCH_QUOTES[qid], qid

    def test_fail_as_shipped_original(self) -> None:
        result = _assert_case(RESEARCH, perturbed=False, reference=False, expect_pass=False)
        assert result["passed"] == 0, "no answers.json ships: every test must fail"

    def test_pass_with_reference_original(self) -> None:
        _assert_case(RESEARCH, perturbed=False, reference=True, expect_pass=True)

    def test_fail_as_shipped_post_change(self) -> None:
        _assert_case(RESEARCH, perturbed=True, reference=False, expect_pass=False)

    def test_the_unified_change_matrix(self) -> None:
        """All six unified cases (incl. the bite and the exact reversal —
        the withdrawn q07 entry is REMOVED by the patch, so the invalidated
        test_q07_latency.py fails on it)."""
        _assert_unified_matrix(RESEARCH)


# ---------------------------------------------------------------------------
# The project fixture (xl-project-postbox)
# ---------------------------------------------------------------------------


class TestPostboxFixture:
    def test_seed_is_the_brief_only(self) -> None:
        assert set(POSTBOX["seed"]) == {"BRIEF.md"}, set(POSTBOX["seed"])
        brief = POSTBOX["seed"]["BRIEF.md"]
        for req in range(1, 28):
            assert f"R{req}." in brief, f"the brief lost requirement R{req}"

    def test_hidden_suite_is_sized_for_partial_credit(self) -> None:
        assert len(POSTBOX["hidden_tests"]) >= 8, sorted(POSTBOX["hidden_tests"])
        total = 0
        for content in POSTBOX["hidden_tests"].values():
            total += len(re.findall(r"^def test_", content, re.M))
        assert total >= 40, total
        change_total = len(
            re.findall(
                r"^def test_",
                POSTBOX["change"]["hidden_tests"]["test_change_separation.py"],
                re.M,
            )
        )
        assert change_total >= 5, change_total

    def test_reference_is_a_stdlib_only_package(self) -> None:
        assert set(POSTBOX["reference"]).issuperset(
            {
                "postbox/__init__.py",
                "postbox/__main__.py",
                "postbox/config.py",
                "postbox/store.py",
                "postbox/service.py",
                "postbox/delivery.py",
                "postbox/server.py",
                "README.md",
                "docs/API.md",
                "docs/OPERATIONS.md",
            }
        )
        for rel, content in POSTBOX["reference"].items():
            if not rel.endswith(".py"):
                continue
            for module in re.findall(
                r"^(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", content, re.M
            ):
                root_module = module.split(".")[0]
                if root_module == "postbox":
                    continue  # the package's own modules
                assert root_module in sys.stdlib_module_names, f"{rel}: third-party import {module}"

    def test_fail_as_shipped_original(self) -> None:
        result = _assert_case(POSTBOX, perturbed=False, reference=False, expect_pass=False)
        assert result["passed"] == 0, f"tests passing on the bare brief: {result['failed_ids']}"

    def test_pass_with_reference_original(self) -> None:
        _assert_case(POSTBOX, perturbed=False, reference=True, expect_pass=True)

    def test_fail_as_shipped_post_change(self) -> None:
        _assert_case(POSTBOX, perturbed=True, reference=False, expect_pass=False)

    def test_the_unified_change_matrix(self) -> None:
        """All six unified cases: the v1 single-process reference passes the
        original suite (embeds tests included), the change BITES via the two
        separation tests (the guards are the four the embedded worker
        satisfies), reference+patch passes the post-change suite, and the
        reversal is exact (test_serve_embeds.py fails on the separated patch
        and nothing else does)."""
        _assert_unified_matrix(POSTBOX)

    def test_the_brief_mandates_the_v1_single_process_model(self) -> None:
        """The rework lever, stated in the SEED: the v1 brief mandates the
        embedded worker (the change reverses a BRIEFED behavior, not just a
        design choice), and the original suite pins it."""
        brief = POSTBOX["seed"]["BRIEF.md"]
        assert "starts the delivery worker IN-PROCESS" in brief
        assert "ONE command, one process" in brief
        assert "test_serve_embeds.py" in POSTBOX["hidden_tests"]
        embeds = POSTBOX["hidden_tests"]["test_serve_embeds.py"]
        assert "test_serve_delivers_without_worker" in embeds
        assert "test_stats_refresh_without_worker" in embeds

    def test_the_reference_is_the_v1_design_and_the_patch_reverses_it(self) -> None:
        """The PRE-change reference is the naive single-process design
        (serve embeds the worker thread, stats cached in-process); the patch
        reverses exactly those two files to the separated design."""
        reference = POSTBOX["reference"]
        assert "run_worker" in reference["postbox/server.py"], "serve must embed the worker"
        assert "self._status" in reference["postbox/store.py"], "stats must be cached"
        patch = POSTBOX["change"]["patch"]
        assert "run_worker" not in patch["postbox/server.py"], "serve must be API only"
        assert "self._status" not in patch["postbox/store.py"], "stats must scan per request"
        # the patch is a full-file overlay over the reference's own files
        for rel in patch:
            assert rel in reference, f"patch targets a non-reference file: {rel}"


#: The v1 single-process design is GENERATED (xl_tasks_c._postbox_naive_overlays)
#: from the separated reference text by exact string anchors — the same
#: anchors this file used to patch in the old neutrality proof. If the
#: separated reference is ever edited without updating the anchors, the
#: fixture build breaks loudly (by design: it forces a conscious
#: re-verification of the v1↔separated pair).


class TestPostboxNaiveAnchors:
    def test_the_naive_overlays_are_generated_from_live_anchors(self) -> None:
        """The PRE-change reference (the v1 design) and the CHANGE PATCH (the
        separated design) are the same two files with exactly the naive bits
        reversed — generated, not hand-copied, so they cannot drift."""
        overlays = xlc._postbox_naive_overlays()
        assert "run_worker" in overlays["postbox/server.py"], "serve must embed the worker"
        assert "self._status" in overlays["postbox/store.py"], "stats must be cached"
        for rel in ("postbox/store.py", "postbox/server.py"):
            assert overlays[rel] != POSTBOX["change"]["patch"][rel], (
                f"{rel}: the v1 design and the patch must differ"
            )
            assert overlays[rel] == POSTBOX["reference"][rel], (
                f"{rel}: the overlays ARE the shipped reference"
            )
