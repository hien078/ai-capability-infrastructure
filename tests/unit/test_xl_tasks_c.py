"""The XL fixtures (builder "c") are real fixtures — verified here
mechanically, no model, no network, no DB (the §34 discipline, applied to
fixture-building itself), against the contract scripts/xl_bench.py (the
xl-harness job) enforces on disk:

- FAIL-AS-SHIPPED: the seed alone fails the ORIGINAL hidden suite.
- PASS-WHEN-SOLVED: the reference (kept outside the workspace) passes BOTH
  the original suite AND the post-change suite (original minus invalidated
  plus the change tests) — the xl_bench.verify_fixture requirement that
  forces the reference to be the FINAL post-change state.
- The hidden tests are NEVER inside the seed; the task text reveals no
  hidden test; every path is a safe relative path.
- The research answer key is DERIVED from the same world model that renders
  the corpus (corpus and key cannot drift) — proven here by RECOMPUTING the
  CSV answers from the rendered CSV text itself.
- The postbox ORIGINAL suite is ARCHITECTURE-NEUTRAL where the requirement
  change bites: an embedded-worker + cached-stats implementation passes it
  (proven here by patching the reference into exactly that naive design),
  and the CHANGE pack is what punishes that design — that asymmetry is the
  fixture's whole point (the perturbation must be able to arrive mid-run
  and cost re-planning, so the original suite must not have forced the
  right architecture already).

§34 caveat for any round run on these fixtures: author-built, small n,
one model — directional only.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import xl_tasks_c as xlc  # noqa: E402

PY = sys.executable
NOISE = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git", "node_modules")
RESEARCH = next(t for t in xlc.TASKS if t["kind"] == "research")
POSTBOX = next(t for t in xlc.TASKS if t["kind"] == "project")


# ---------------------------------------------------------------------------
# Materialize + grade — a faithful mirror of xl_bench.hidden_suite_result
# (the consumer's exact mechanics: fresh copy, hidden tests copied in at
# their relative paths, invalidated originals deleted for perturbed runs,
# pytest -q, pass fraction from the summary line).
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


def _grade(work: Path, task: dict[str, Any], *, perturbed: bool) -> dict[str, Any]:
    fresh = work.parent / f"{work.name}-posthoc-{task['name'][:12]}"
    shutil.copytree(work, fresh, ignore=shutil.ignore_patterns(*NOISE))
    try:
        sources = [task["hidden_tests"]]
        if perturbed:
            sources.append(task["change"]["hidden_tests"])
        for source in sources:
            for rel, content in source.items():
                (fresh / rel).parent.mkdir(parents=True, exist_ok=True)
                (fresh / rel).write_text(content, encoding="utf-8")
        if perturbed:
            for rel in task["change"]["invalidates"]:
                (fresh / rel).unlink(missing_ok=True)
        proc = subprocess.run(
            [PY, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider"],
            cwd=fresh,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        out = proc.stdout + proc.stderr

        def _count(pattern: str) -> int:
            found = re.search(pattern, out)
            return int(found.group(1)) if found else 0

        passed = _count(r"(\d+) passed")
        failed = _count(r"(\d+) failed")
        errors = _count(r"(\d+) error")
        total = passed + failed + errors
        return {
            "fraction": round(passed / total, 4) if total else 0.0,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "total": total,
            "failed_names": sorted(re.findall(r"^FAILED (\S+)", out, re.M)),
            "tail": out[-400:],
        }
    finally:
        shutil.rmtree(fresh, ignore_errors=True)


def _assert_case(
    task: dict[str, Any], *, perturbed: bool, reference: bool, expect_pass: bool
) -> dict[str, Any]:
    """One xl_bench.verify_fixture case, as a reusable assertion."""
    with tempfile.TemporaryDirectory() as tmp:
        work = _materialize(task, Path(tmp), reference=reference)
        result = _grade(work, task, perturbed=perturbed)
    label = f"{task['name']} reference={reference} perturbed={perturbed}"
    assert result["total"] > 0, f"{label}: zero tests ran (a fixture bug, not a result)"
    if expect_pass:
        assert result["fraction"] == 1.0, (
            f"{label}: expected pass-when-solved, got {result['fraction']}:\n{result['tail']}"
        )
    else:
        assert result["fraction"] < 1.0, (
            f"{label}: expected fail-as-shipped, got {result['fraction']} (not a fixture)"
        )
    return result


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
                assert rel in original, f"{task['name']}: invalidates a non-original test {rel}"
            assert task["change"]["invalidates"], task["name"]

    def test_write_fixture_layout_matches_the_harness_contract(self) -> None:
        """write_fixture materializes exactly the on-disk layout
        scripts/xl_bench.py consumes (workspace/ + TASK.md + hidden/ +
        change/{CHANGE_NOTE.md,hidden/,invalidates.txt} + reference/)."""
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
                assert (out / "reference").is_dir(), task["name"]
                for rel, content in task["seed"].items():
                    assert (out / "workspace" / rel).read_text(encoding="utf-8") == content
                for rel, content in task["hidden_tests"].items():
                    assert (out / "hidden" / rel).read_text(encoding="utf-8") == content
                for rel, content in task["change"]["hidden_tests"].items():
                    assert (out / "change" / "hidden" / rel).read_text(encoding="utf-8") == content
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

    def test_reference_answers_every_question_with_the_audited_quotes(self) -> None:
        reference = json.loads(RESEARCH["reference"]["answers.json"])
        entries = {a["id"]: a for a in reference["answers"]}
        for source in (xlc._RESEARCH_KEY, xlc._RESEARCH_CHANGE_KEY):
            for qid, entry in source.items():
                assert qid in entries, qid
                assert entries[qid]["answer"] == entry["answer"], qid
                assert set(entries[qid]["citations"]) >= set(entry["citations"]), qid
        for qid in xlc._RESEARCH_QUOTES:
            assert entries[qid].get("quote") == xlc._RESEARCH_QUOTES[qid], qid

    def test_fail_as_shipped_original(self) -> None:
        result = _assert_case(RESEARCH, perturbed=False, reference=False, expect_pass=False)
        assert result["failed"] + result["errors"] == result["total"]

    def test_pass_with_reference_original(self) -> None:
        _assert_case(RESEARCH, perturbed=False, reference=True, expect_pass=True)

    def test_fail_as_shipped_post_change(self) -> None:
        _assert_case(RESEARCH, perturbed=True, reference=False, expect_pass=False)

    def test_pass_with_reference_post_change(self) -> None:
        _assert_case(RESEARCH, perturbed=True, reference=True, expect_pass=True)


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
        assert result["passed"] == 0, f"tests passing on the bare brief: {result['failed_names']}"

    def test_pass_with_reference_original(self) -> None:
        _assert_case(POSTBOX, perturbed=False, reference=True, expect_pass=True)

    def test_fail_as_shipped_post_change(self) -> None:
        _assert_case(POSTBOX, perturbed=True, reference=False, expect_pass=False)

    def test_pass_with_reference_post_change(self) -> None:
        _assert_case(POSTBOX, perturbed=True, reference=True, expect_pass=True)


#: The naive early-architecture patches: applied to the reference they
#: produce exactly the wrong-but-plausible design the change punishes —
#: serve embeds a worker thread, stats come from an in-memory cache warmed
#: at startup and updated only by this process's writes.
_NAIVE_PATCHES: list[tuple[str, str, str]] = [
    (
        "postbox/store.py",
        '''    def stats(self) -> dict[str, int]:
        """Counts per status, computed from the store on EVERY call (R7) —
        never cached, so a separately-running worker's writes are visible."""
        counts = {status: 0 for status in STATUSES}
        for message in self.all_messages():
            status = message.get("status")
            if status in counts:
                counts[status] += 1
        return counts''',
        """    def stats(self) -> dict[str, int]:
        counts = {status: 0 for status in STATUSES}
        for status in self._status.values():
            if status in counts:
                counts[status] += 1
        return counts""",
    ),
    (
        "postbox/store.py",
        """        self.messages.mkdir(parents=True, exist_ok=True)
        self.idempotency.mkdir(parents=True, exist_ok=True)""",
        """        self.messages.mkdir(parents=True, exist_ok=True)
        self.idempotency.mkdir(parents=True, exist_ok=True)
        self._status = {m["id"]: m.get("status") for m in self.all_messages()}""",
    ),
    (
        "postbox/store.py",
        """    def put(self, message: dict[str, Any]) -> None:
        self.write_atomic(self._path(message["id"]), message)""",
        """    def put(self, message: dict[str, Any]) -> None:
        self.write_atomic(self._path(message["id"]), message)
        self._status[message["id"]] = message.get("status")""",
    ),
    (
        "postbox/server.py",
        """def serve(store: Store, port: int) -> None:
    httpd = create_server(store, port)""",
        """def serve(store: Store, port: int) -> None:
    import threading

    from postbox.config import load_config
    from postbox.delivery import run_worker

    threading.Thread(
        target=run_worker, args=(store, load_config(store.home)), daemon=True
    ).start()
    httpd = create_server(store, port)""",
    ),
]


class TestPostboxDesignNeutrality:
    def test_the_original_suite_is_architecture_neutral_and_the_change_bites(self) -> None:
        """The fixture's core claim, proven mechanically: the NAIVE design
        (embedded worker + cached stats) passes the ORIGINAL suite untouched
        (so the change can still arrive mid-run and cost re-planning), and
        the CHANGE pack is what punishes it — exactly the separation tests,
        nothing else. If this test fails after editing the reference or the
        suites, the neutrality contract broke: fix the fixture, not the
        assertion."""
        with tempfile.TemporaryDirectory() as tmp:
            work = _materialize(POSTBOX, Path(tmp), reference=True)
            for rel, old, new in _NAIVE_PATCHES:
                path = work / rel
                text = path.read_text(encoding="utf-8")
                assert old in text, f"naive patch anchor missing in {rel}"
                path.write_text(text.replace(old, new), encoding="utf-8")

            original = _grade(work, POSTBOX, perturbed=False)
            assert original["total"] > 0
            assert original["fraction"] == 1.0, (
                "the ORIGINAL suite punishes the naive design — neutrality broke:\n"
                f"{original['failed_names']}\n{original['tail']}"
            )

            post_change = _grade(work, POSTBOX, perturbed=True)
            punished = set(post_change["failed_names"])
            expected = {
                "test_change_separation.py::test_serve_alone_does_not_deliver",
                "test_change_separation.py::test_stats_reflect_worker_deliveries",
            }
            change_names = {
                "test_change_separation.py::" + name
                for name in re.findall(
                    r"^def (test_\w+)",
                    POSTBOX["change"]["hidden_tests"]["test_change_separation.py"],
                    re.M,
                )
            }
            # the naive design MUST fail the two core separation tests...
            assert expected <= punished, (
                "the change no longer punishes the naive design:\n"
                f"{post_change['failed_names']}\n{post_change['tail']}"
            )
            # ...and must fail ONLY change tests (never an original one)
            assert punished <= change_names, (
                f"an ORIGINAL test punishes the naive design: {punished - change_names}"
            )
