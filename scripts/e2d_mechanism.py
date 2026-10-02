"""E2D-TURNS mechanism analysis — workspaces + traces, per run.

The pre-registered mechanism outcomes (E2D-TURNS-PREREGISTERED.md):
for failing runs, did the model edit the sources at all? time-to-first-source-
edit (turn index) R vs Bp — measured as the turn of the first
``write_file``/``edit_file`` tool call (the trace carries tool_id + turn but
NOT arguments, so this is the first FILE-WRITE call, scratch writes included
— the caveat is pre-registered); extraction/dump scripts written
(model-written files, inspected by hand).

Usage:
    .venv/bin/python scripts/e2d_mechanism.py <work-root> <round.json> [<round2.json> ...]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from private2_tasks import PRIVATE2_TASKS  # noqa: E402

BY_NAME = {t["name"]: t for t in PRIVATE2_TASKS}
WRITE_TOOLS = frozenset({"write_file", "edit_file"})
TOOL_EVENTS = frozenset({"tool.execution.completed", "tool.execution.failed"})


def find_run_dir(work_root: Path, run_id: str) -> tuple[Path | None, Path | None]:
    """(run dir, trace file) for a run id — the stamps are scanned in order."""
    run_dir = trace = None
    for stamp in sorted(work_root.iterdir()):
        if not stamp.is_dir():
            continue
        candidate = stamp / "runs" / run_id
        if candidate.is_dir() and run_dir is None:
            run_dir = candidate
        hits = sorted((stamp / "traces").glob(f"trace-*-{run_id}.json"))
        if hits and trace is None:
            trace = hits[0]
    return run_dir, trace


def trace_facts(trace: Path | None) -> dict:
    """First write/edit call turn + tool-call counts, from the run trace."""
    facts = {
        "first_write_turn": None,
        "write_calls": 0,
        "read_calls": 0,
        "command_calls": 0,
        "tool_calls": 0,
    }
    if trace is None:
        return facts
    events = json.loads(trace.read_text(encoding="utf-8"))
    for event in events:
        if event.get("event") not in TOOL_EVENTS:
            continue
        facts["tool_calls"] += 1
        tool = str(event.get("payload", {}).get("tool_id", ""))
        turn = str(event.get("turn") or "")
        turn_no = int(turn.removeprefix("turn-")) if turn.startswith("turn-") else None
        if tool in WRITE_TOOLS:
            facts["write_calls"] += 1
            if facts["first_write_turn"] is None and turn_no is not None:
                facts["first_write_turn"] = turn_no
        elif tool == "read_file":
            facts["read_calls"] += 1
        elif tool == "run_command":
            facts["command_calls"] += 1
    return facts


#: Run-workspace infrastructure, not model-written analysis scripts — the
#: Seatbelt profile (per-run) and pytest's own cache (the verification
#: command) are excluded from the scratch signal.
INFRA_FILES = frozenset({".aci-sandbox-profile.sb"})


def workspace_facts(run_dir: Path, fixture: dict) -> dict:
    """Sources edited / tests intact / model-written files, from the final
    workspace vs the shipped fixture files."""
    sources_edited, tests_bad, scratch = [], [], []
    for rel, content in fixture["files"].items():
        path = run_dir / rel
        if not path.is_file():
            (tests_bad if rel.startswith("test_") else sources_edited).append(f"MISSING {rel}")
            continue
        same = path.read_text(encoding="utf-8") == content
        if rel.startswith("test_"):
            if not same:
                tests_bad.append(f"MODIFIED {rel}")
        elif not same:
            sources_edited.append(rel)
    for path in sorted(run_dir.rglob("*")):
        rel = str(path.relative_to(run_dir))
        if not path.is_file() or rel in fixture["files"]:
            continue
        if path.name in INFRA_FILES or rel.startswith(".pytest_cache"):
            continue
        scratch.append(rel)
    return {"sources_edited": sources_edited, "tests_bad": tests_bad, "scratch": scratch}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    work_root = Path(argv[0])
    rows: list[dict] = []
    for path in argv[1:]:
        rows += json.loads(Path(path).read_text(encoding="utf-8"))["results"]
    print(
        f"{'fixture':35s} {'arm':3s} {'pass':4s} {'stop':12s} "
        f"{'turns':5s} {'1st-wr':6s} {'wr':3s} {'src-edited':28s} scratch"
    )
    summary: dict[str, list] = {}
    for row in rows:
        if not row.get("run_id"):
            continue
        fixture = BY_NAME.get(row["fixture"])
        if fixture is None:
            continue
        run_dir, trace = find_run_dir(work_root, str(row["run_id"]))
        if run_dir is None:
            print(f"{row['fixture']:35s} {row['arm']:3s} NO WORKSPACE DIR")
            continue
        tf = trace_facts(trace)
        wf = workspace_facts(run_dir, fixture)
        passed = "PASS" if row.get("tests_pass_at_end") else "fail"
        stop = str(row.get("stop_reason") or row.get("status"))
        print(
            f"{row['fixture']:35s} {row['arm']:3s} {passed:4s} {stop:12s} "
            f"{row.get('turns', 0):<5} {str(tf['first_write_turn']):6s} {tf['write_calls']:<3} "
            f"{','.join(wf['sources_edited']) or '-':28s} {','.join(wf['scratch']) or '-'}"
        )
        if wf["tests_bad"]:
            print(f"  !! TESTS TAMPERED: {wf['tests_bad']}")
        key = (row["arm"], passed == "PASS")
        summary.setdefault(key, []).append(
            {
                "first_write_turn": tf["first_write_turn"],
                "sources_edited": bool(wf["sources_edited"]),
                "scratch": bool(wf["scratch"]),
                "stop": stop,
            }
        )
    print("\n=== per-arm aggregates (valid rows) ===")
    for (arm, passed), entries in sorted(summary.items()):
        writes = [e["first_write_turn"] for e in entries if e["first_write_turn"] is not None]
        no_write = sum(1 for e in entries if e["first_write_turn"] is None)
        edited = sum(1 for e in entries if e["sources_edited"])
        scratch = sum(1 for e in entries if e["scratch"])
        label = "PASS" if passed else "fail"
        first = f"{min(writes)}..{max(writes)}" if writes else "-"
        print(
            f"  {arm:3s} {label:4s} n={len(entries)} first-write-turn {first} "
            f"(never-wrote {no_write}) sources-edited {edited}/{len(entries)} "
            f"scratch {scratch}/{len(entries)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
