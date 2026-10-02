"""E2D-TURNS instrument pins — the analysis + mechanism scripts.

Pure-function pins over synthetic rows/workspaces/traces (no model, no DB,
no baseline files): the FIFO cell caps, the valid-row exclusion, the pooled
counters, the trace fact extraction (turn parsing, first write call) and
the workspace fact extraction (sources edited vs tests intact vs
model-written scratch).
"""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from e2c_analyze import passes_fails  # noqa: E402
from e2d_mechanism import trace_facts, workspace_facts  # noqa: E402
from e2d_turns_analyze import E2D_CAPS, load_cells, pooled, rate, valid  # noqa: E402
from private2_tasks import PRIVATE2_TASKS  # noqa: E402


def _row(fixture: str, arm: str, *, passes: bool, stop: str = "LIMIT_TURNS", n: int = 1) -> dict:
    return {
        "fixture": fixture,
        "arm": arm,
        "tests_pass_at_end": passes,
        "stop_reason": stop,
        "turns": 12,
        "accepted": False,
        "false_success": False,
        "tokens_in": 1000,
        "tokens_out": 100,
        "repeat": n,
    }


def test_valid_excludes_model_failure_and_crashed() -> None:
    assert valid(_row("f", "R", passes=True))
    assert not valid(_row("f", "R", passes=True, stop="MODEL_FAILURE"))
    crashed = _row("f", "R", passes=True)
    crashed["stop_reason"] = "RuntimeError"
    assert crashed["stop_reason"] == "RuntimeError"
    crashed["status"] = "crashed"
    assert not valid(crashed)


def test_load_cells_caps_fifo_and_skips_invalid(tmp_path: Path) -> None:
    # 8 valid rows for one cell (cap 6 -> FIFO keeps the FIRST 6) + 2 invalid.
    rows = [_row("f", "R", passes=bool(i % 2)) for i in range(8)]
    rows += [_row("f", "R", passes=True, stop="MODEL_FAILURE") for _ in range(2)]
    path = tmp_path / "round.json"
    path.write_text(json.dumps({"results": rows}), encoding="utf-8")
    cells = load_cells([path], {"R": 6})
    kept = cells[("f", "R")]
    assert len(kept) == 6
    assert E2D_CAPS == {"K": 2, "R": 6, "Bp": 6}
    # FIFO: the first 6 rows alternate fail/pass starting with a fail (i%2).
    assert [r["tests_pass_at_end"] for r in kept] == [False, True, False, True, False, True]


def test_pooled_and_rate_over_fixtures() -> None:
    cells = {
        ("a", "R"): [_row("a", "R", passes=True), _row("a", "R", passes=False)],
        ("b", "R"): [_row("b", "R", passes=True)],
    }
    assert pooled(cells, "R", ["a", "b"]) == (2, 1)
    assert pooled(cells, "R", ["a"]) == (1, 1)
    assert pooled(cells, "R", ["zzz"]) == (0, 0)
    mean_in, n = rate(cells, "R", ["a", "b"], "tokens_in")
    assert mean_in == 1000.0
    assert n == 3


def test_e2c_passes_fails_excludes_crashed_rows() -> None:
    # The runner's crashed rows carry status='crashed' + stop_reason=<exc
    # type name> — passes_fails must exclude them on EITHER signal (red
    # first: the stop_reason-only check missed the exception-name case).
    crashed = _row("f", "R", passes=True)
    crashed["status"] = "crashed"
    crashed["stop_reason"] = "RuntimeError"
    rows = [_row("f", "R", passes=True), _row("f", "R", passes=False), crashed]
    assert passes_fails(rows) == (1, 1)


def test_trace_facts_first_write_turn_and_counts(tmp_path: Path) -> None:
    events = [
        {"event": "run.started", "turn": None, "payload": {}},
        {
            "event": "tool.execution.completed",
            "turn": "turn-1",
            "payload": {"tool_id": "read_file", "status": "success"},
        },
        {
            "event": "tool.execution.completed",
            "turn": "turn-2",
            "payload": {"tool_id": "write_file", "status": "success"},
        },
        {
            "event": "tool.execution.failed",
            "turn": "turn-4",
            "payload": {"tool_id": "edit_file", "status": "error"},
        },
        {
            "event": "tool.execution.completed",
            "turn": "turn-5",
            "payload": {"tool_id": "run_command", "status": "success"},
        },
    ]
    trace = tmp_path / "trace-x.json"
    trace.write_text(json.dumps(events), encoding="utf-8")
    facts = trace_facts(trace)
    assert facts["first_write_turn"] == 2
    assert facts["write_calls"] == 2
    assert facts["read_calls"] == 1
    assert facts["command_calls"] == 1
    assert facts["tool_calls"] == 4
    # No trace -> honest zeros (the run still counts, nothing is invented).
    assert trace_facts(None)["first_write_turn"] is None


def test_workspace_facts_sources_vs_tests_vs_scratch(tmp_path: Path) -> None:
    fixture = PRIVATE2_TASKS[0]
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir()
    for rel, content in fixture["files"].items():
        (run_dir / rel).write_text(content, encoding="utf-8")
    # The model edited ONE source file, left the rest + tests intact, and
    # wrote a scratch dump script.
    source_files = [rel for rel in fixture["files"] if not rel.startswith("test_")]
    victim = run_dir / source_files[0]
    victim.write_text("# tampered\n", encoding="utf-8")
    (run_dir / "zz_dump_test.py").write_text("print('dump')\n", encoding="utf-8")
    facts = workspace_facts(run_dir, fixture)
    assert facts["sources_edited"] == [source_files[0]]
    assert facts["tests_bad"] == []
    assert facts["scratch"] == ["zz_dump_test.py"]
    # A modified TEST file is flagged (tamper), never silently accepted.
    test_file = run_dir / next(rel for rel in fixture["files"] if rel.startswith("test_"))
    test_file.write_text("# tampered\n", encoding="utf-8")
    facts = workspace_facts(run_dir, fixture)
    assert facts["tests_bad"] == [f"MODIFIED {test_file.name}"]


def test_valid_excludes_registry_outage_rows() -> None:
    """A Bp/Bq row the runner marked invalid (registry unreachable) is not a
    result — the E2D-turns Bp@20 round was 42 such rows."""
    assert valid({"stop_reason": "LIMIT_TURNS", "status": "failed"})
    assert not valid(
        {"stop_reason": "LIMIT_TURNS", "status": "failed", "invalid_reason": "REGISTRY_UNAVAILABLE"}
    )
