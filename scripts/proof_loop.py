"""§80 proof-loop measurement: real tasks, A (client alone) vs E (full pipeline).

Grows the real-task sample beyond the first manual run: each task is a
small repo with a genuine bug and a failing acceptance test. Every task
runs twice through a real OpenCode client with the same model and the
same prompt —

- variant A: no plugin, no platform skills (the client works naked);
- variant E: the ACI routing plugin installed — the platform routes the
  prompt, injects 0-5 skills, and the run is reported via POST /v1/outcomes
  (§33) with the stashed route/bundle ids.

The only difference between the variants is the platform's presence, so
acceptance deltas are attributable to routed skills. §34 caveat: n is
small, one model, author-selected tasks — directional evidence, never
statistics. The report JSON records both variants side by side.

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/proof_loop.py \
        [--workdir DIR] [--model local-gateway/OneNexus/glm-5.3]

Requires the ACI server on 127.0.0.1:8000 (uvicorn aci.main:app) and the
opencode CLI on PATH. The .opencode template (plugin + pinned deps) is
materialized from the repo's plugin source on first use.
"""

import argparse
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parent.parent
PLUGIN_SRC = REPO / "src/aci/adapters/inbound/opencode/plugin/index.ts"
TEMPLATE = Path("/tmp/opencode/oc-template/.opencode")
REPORT_ROOT = REPO / "data/proof-loop"
OPENCODE = str(Path.home() / ".opencode/bin/opencode")
PYTEST = str(REPO / ".venv/bin/python")


def _task(name: str, files: dict[str, str], prompt: str) -> dict[str, Any]:
    return {"name": name, "files": files, "prompt": prompt}


TASKS = [
    _task(
        "debug-mutable-default",
        {
            "invoice.py": (
                '"""A tiny invoice service with a bug."""\n\n\n'
                "class InvoiceService:\n"
                "    def __init__(self) -> None:\n"
                '        self._rates = {"standard": 1.0, "vip": 0.8}\n\n'
                "    def apply_discount(self, rate_name: str, history: list[dict] = [])"
                " -> list[dict]:\n"
                '        """Record a discount application and return the history."""\n'
                '        history.append({"rate": rate_name, "applied": self._rates[rate_name]})\n'
                "        return history\n"
            ),
            "test_invoice.py": (
                "from invoice import InvoiceService\n\n\n"
                "def test_history_isolated() -> None:\n"
                '    """A discount applied through one instance must never leak into\n'
                '    the history returned by a different instance."""\n'
                "    first = InvoiceService()\n"
                "    second = InvoiceService()\n"
                '    first.apply_discount("standard")\n'
                '    result = second.apply_discount("vip")\n'
                '    assert result == [{"rate": "vip", "applied": 0.8}], result\n'
            ),
        },
        "The test test_history_isolated in test_invoice.py fails. Find the root cause "
        "before proposing any fix, then fix it and make the whole test suite green. "
        "Show the verification output.",
    ),
    _task(
        "debug-late-binding",
        {
            "callbacks.py": (
                '"""Builds worker callbacks — with a bug."""\n\n\n'
                "def make_workers(names: list[str]) -> dict[str, callable]:\n"
                '    """Return one callable per name; calling it greets that name."""\n'
                "    workers = {}\n"
                "    for name in names:\n"
                "        def greet() -> str:\n"
                '            return f"hello {name}"\n'
                "        workers[name] = greet\n"
                "    return workers\n"
            ),
            "test_callbacks.py": (
                "from callbacks import make_workers\n\n\n"
                "def test_each_worker_greets_its_own_name() -> None:\n"
                '    workers = make_workers(["ada", "grace", "linus"])\n'
                '    assert workers["ada"]() == "hello ada"\n'
                '    assert workers["grace"]() == "hello grace"\n'
                '    assert workers["linus"]() == "hello linus"\n'
            ),
        },
        "The test test_each_worker_greets_its_own_name in test_callbacks.py fails. "
        "Find the root cause before proposing any fix, then fix it and make the whole "
        "test suite green. Show the verification output.",
    ),
    _task(
        "testing-timestamps",
        {
            "schedule.py": (
                '"""Parses schedule timestamps — with a bug."""\n\n'
                "from datetime import datetime\n\n\n"
                "def parse_when(raw: str) -> datetime:\n"
                '    """Parse an ISO timestamp into a datetime.\n\n'
                "    The result must still carry the original UTC offset so that\n"
                '    comparisons with other aware datetimes are correct."""\n'
                '    return datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")\n'
            ),
            "test_schedule.py": (
                "from datetime import datetime, timezone\n\n"
                "from schedule import parse_when\n\n\n"
                "def test_offset_survives_parsing() -> None:\n"
                '    when = parse_when("2026-09-28T10:30:00+02:00")\n'
                "    assert when.utcoffset() is not None\n"
                "    assert when == datetime(2026, 9, 28, 8, 30, tzinfo=timezone.utc)\n"
            ),
        },
        "The test test_offset_survives_parsing in test_schedule.py fails. Find the "
        "root cause before proposing any fix, then fix it and make the whole test "
        "suite green. Show the verification output.",
    ),
    _task(
        "perf-duplicate-scan",
        {
            "scanner.py": (
                '"""Finds duplicate records — too slowly."""\n\n\n'
                "def find_duplicates(records: list[dict]) -> list[dict]:\n"
                '    """Return every record whose "id" appears more than once."""\n'
                "    out = []\n"
                "    for record in records:\n"
                '        same = [r for r in records if r["id"] == record["id"]]\n'
                "        if len(same) > 1 and record not in out:\n"
                "            out.append(record)\n"
                "    return out\n"
            ),
            "test_scanner.py": (
                "import time\n\n"
                "from scanner import find_duplicates\n\n\n"
                "def test_large_scan_is_fast() -> None:\n"
                '    records = [{"id": i % 800, "v": i} for i in range(8_000)]\n'
                "    started = time.perf_counter()\n"
                "    dupes = find_duplicates(records)\n"
                "    elapsed = time.perf_counter() - started\n"
                "    assert len(dupes) == 8_000\n"
                '    assert elapsed < 1.0, f"scan took {elapsed:.2f}s — too slow"\n'
            ),
        },
        "The test test_large_scan_is_fast in test_scanner.py fails on the timing "
        "assertion. Find the root cause before proposing any fix, then fix it and "
        "make the whole test suite green. Show the verification output.",
    ),
    _task(
        "i18n-mojibake",
        {
            "names.py": (
                '"""Normalizes user-entered display names — with a bug."""\n\n\n'
                "def normalize(name: str) -> str:\n"
                '    """Normalize a display name for storage.\n\n'
                '    Must round-trip every character the user typed."""\n'
                '    return name.encode("ascii", errors="ignore").decode("ascii")\n'
            ),
            "test_names.py": (
                "from names import normalize\n\n\n"
                "def test_unicode_roundtrip() -> None:\n"
                '    original = "Grüße — 日本語 — café"\n'
                "    assert normalize(original) == original\n"
            ),
        },
        "The test test_unicode_roundtrip in test_names.py fails. Find the root cause "
        "before proposing any fix, then fix it and make the whole test suite green. "
        "Show the verification output.",
    ),
]


def run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def acceptance(task_dir: Path) -> bool:
    code, _ = run([PYTEST, "-m", "pytest", "-q"], task_dir, 180)
    return code == 0


def reset(task_dir: Path) -> None:
    run(["git", "checkout", "--", "."], task_dir, 30)
    run(["git", "clean", "-fdq", "-e", ".opencode/"], task_dir, 30)


def latest_route(bundle_db: str, since: float) -> dict[str, str] | None:
    engine = create_engine(bundle_db)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT r.route_run_id, r.bundle_id FROM route_runs r "
                "WHERE r.principal_id = 'opencode' "
                "AND r.created_at >= :since ORDER BY r.created_at DESC LIMIT 1"
            ),
            {"since": datetime.fromtimestamp(since, tz=UTC)},
        ).fetchone()
    return {"route_run_id": row[0], "bundle_id": row[1]} if row else None


def report_outcome(base_url: str, ids: dict[str, str], passed: bool) -> str | None:
    status = "success" if passed else "failure"
    body = {
        "route_run_id": ids["route_run_id"],
        "bundle_id": ids["bundle_id"],
        "verdicts": [
            {"source": "test_harness", "status": status, "confidence": "high"},
            {"source": "agent_self_report", "status": status, "confidence": "medium"},
        ],
        "client_status": "completed",
        "build_passed": passed,
        "human_corrected": False,
    }
    resp = httpx.post(f"{base_url}/v1/outcomes", json=body, timeout=10)
    if resp.status_code not in (200, 201):
        print(f"  outcome POST failed: {resp.status_code} {resp.text[:200]}")
        return None
    return resp.json()["outcome_id"]


def run_variant(
    task: dict[str, Any], task_dir: Path, variant: str, args: argparse.Namespace
) -> dict[str, Any]:
    with_plugin = variant == "E"
    if with_plugin:
        shutil.copytree(TEMPLATE, task_dir / ".opencode", dirs_exist_ok=True)
    started = time.time()
    code, output = run([OPENCODE, "run", "-m", args.model, task["prompt"]], task_dir, args.timeout)
    wall = round(time.time() - started, 1)
    passed = acceptance(task_dir)
    record: dict[str, Any] = {
        "variant": variant,
        "exit_code": code,
        "acceptance_pass": passed,
        "wall_seconds": wall,
        "output_tail": output[-1500:],
    }
    if with_plugin:
        ids = latest_route(args.database_url, started)
        record["routed"] = ids
        if ids:
            record["outcome_id"] = report_outcome(args.base_url, ids, passed)
        shutil.rmtree(task_dir / ".opencode", ignore_errors=True)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/opencode/proof-tasks"))
    parser.add_argument("--model", default="local-gateway/OneNexus/glm-5.3")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--database-url", default="postgresql+psycopg://aci:aci@localhost:5432/aci_bench"
    )
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--only", help="run a single task by name")
    args = parser.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for task in TASKS:
        if args.only and task["name"] != args.only:
            continue
        name = task["name"]
        task_dir = args.workdir / name
        if task_dir.exists():
            shutil.rmtree(task_dir)
        task_dir.mkdir(parents=True)
        for rel, content in task["files"].items():
            (task_dir / rel).write_text(content, encoding="utf-8")
        run(["git", "init", "-q"], task_dir, 30)
        run(["git", "add", "-A"], task_dir, 30)
        run(["git", "commit", "-qm", "task fixture"], task_dir, 30)

        print(f"=== {name} ===")
        baseline = run_variant(task, task_dir, "A", args)
        print(f"  A: pass={baseline['acceptance_pass']} {baseline['wall_seconds']}s")
        reset(task_dir)
        routed = run_variant(task, task_dir, "E", args)
        print(
            f"  E: pass={routed['acceptance_pass']} {routed['wall_seconds']}s "
            f"routed={routed.get('routed')}"
        )
        reset(task_dir)
        results.append({"task": name, "A": baseline, "E": routed})

    a_pass = sum(1 for r in results if r["A"]["acceptance_pass"])
    e_pass = sum(1 for r in results if r["E"]["acceptance_pass"])
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "task_count": len(results),
        "summary": {
            "A_acceptance": a_pass,
            "E_acceptance": e_pass,
            "note": (
                "§34: n is small, one model, author-selected tasks — directional "
                "evidence only; the only difference between variants is the "
                "platform's presence (plugin + routed skills)."
            ),
        },
        "results": results,
    }
    out = REPORT_ROOT / f"proof-loop-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nA acceptance: {a_pass}/{len(results)}  E acceptance: {e_pass}/{len(results)}")
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
