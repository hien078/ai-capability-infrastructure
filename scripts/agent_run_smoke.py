"""First real-objective instrument for POST /v1/agent-runs (harness.md §42):
materialize one §80 proof-loop task as a workspace, run it through the
HarnessKernel, print what the verifier saw and what changed on disk.

Usage (server started with ACI_AGENT_WORKSPACE_ROOT=<workspace-root> and
ACI_AGENT_PROCESS_PREFIXES='["python -m pytest"]'):
    .venv/bin/python scripts/agent_run_smoke.py --task debug-mutable-default
Exit code 0 iff the run status is "succeeded".
"""

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from proof_loop import MULTI_TASKS, TASKS  # noqa: E402

NOISE = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}
ENV_HINT = (
    "ACI_AGENT_MODEL_BASE_URL=<openai-compatible url> ACI_AGENT_MODEL_ID=<model> "
    "ACI_AGENT_MODEL_API_KEY=<key> ACI_AGENT_WORKSPACE_ROOT={root} "
    "ACI_AGENT_PROCESS_PREFIXES='[\"python -m pytest\"]'"
)


def materialize(task: dict[str, Any], root: Path) -> Path:
    target = root / task["name"]
    if target.exists():
        shutil.rmtree(target)
    for rel, content in task["files"].items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return target


def _hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or NOISE & set(path.relative_to(root).parts):
            continue
        out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def diff_dirs(source: Path, run_dir: Path) -> list[str]:
    before, after = _hashes(source), _hashes(run_dir)
    lines = [f"+ {p}" for p in sorted(set(after) - set(before))]
    lines += [f"- {p}" for p in sorted(set(before) - set(after))]
    lines += [f"~ {p}" for p in sorted(set(before) & set(after)) if before[p] != after[p]]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://localhost:8765")
    parser.add_argument("--task", default="debug-mutable-default")
    parser.add_argument("--workspace-root", default="data/agent-workspaces")
    parser.add_argument("--runs-root", default="data/agent-runs")
    parser.add_argument("--profile", default="coder")
    parser.add_argument("--max-turns", type=int, default=25)
    args = parser.parse_args()

    task = next((t for t in [*TASKS, *MULTI_TASKS] if t["name"] == args.task), None)
    if task is None:
        print(f"unknown task {args.task!r}; known: {', '.join(t['name'] for t in TASKS)}")
        return 2
    root = Path(args.workspace_root).resolve()
    source = materialize(task, root)
    print(f"workspace: {source} (server must run with ACI_AGENT_WORKSPACE_ROOT={root})")

    body = {
        "objective": task["prompt"],
        "workspace": task["name"],
        "verification_command": ["python", "-m", "pytest", "-q"],
        "requested_profile": args.profile,
        "max_turns": args.max_turns,
        "acceptance_criteria": ["python -m pytest -q passes"],
    }
    started = time.monotonic()
    try:
        response = httpx.post(f"{args.server}/v1/agent-runs", json=body, timeout=900.0)
    except httpx.HTTPError as exc:
        print(f"request to {args.server} failed: {type(exc).__name__}: {exc}")
        return 1
    wall = time.monotonic() - started
    if response.status_code != 201:
        print(f"HTTP {response.status_code}: {response.text}")
        return 1
    data = response.json()
    for key in ("run_id", "status", "stop_reason", "detail_code", "summary", "turns", "tool_calls"):
        print(f"{key}: {data.get(key)}")
    print(f"wall_time_seconds: {wall:.1f} (server-side {data.get('wall_time_seconds')})")
    print(f"checks: {json.dumps(data.get('checks', []))}")
    print(f"evidence_refs: {json.dumps(data.get('evidence_refs', []))}")
    run_dir = Path(args.runs_root).resolve() / data["run_id"]
    if run_dir.is_dir():
        changed = diff_dirs(source, run_dir)
        print(f"files changed vs source ({run_dir}):")
        print("\n".join(changed) if changed else "  (none)")
    else:
        print(f"run directory not found locally: {run_dir} (server runs elsewhere?)")
    if data.get("stop_reason") == "MODEL_FAILURE" and data.get("turns", 0) <= 1:
        print("model gateway looks unconfigured; start the server with:")
        print("  " + ENV_HINT.format(root=root))
    return 0 if data.get("status") == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
