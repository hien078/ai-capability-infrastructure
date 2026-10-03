"""xl-bench — the XL-orchestration bench harness (job xl-harness, 2026-10-03).

Measures the founding ADR-014 architecture END-TO-END at orchestration scale:
a client (OpenCode) decomposes a REALLY HARD, LONG task and delegates every
code-writing leaf to the ACI service (POST /v1/agent-runs → the HarnessKernel
executes it), then verifies and re-plans — versus OpenCode doing the whole
task alone. THIS FILE IS THE HARNESS ONLY: it ships NO fixtures and runs NO
measurement (the pre-registered round runs on the Linux host; see
data/xl-bench/PREREGISTERED.md).

Arms (same model OneNexus/glm-5.3, same task text, same wall budget):

* A1 SOLO — OpenCode alone (native loop, may use its own subagents).
* A2 DELEGATE — OpenCode as ORCHESTRATOR: code-writing leaves go to ACI via
  the ``delegate_to_aci`` OpenCode custom tool (scripts/xl_oc_template/,
  bench-side), eager decomposition (A2_PLAYBOOK).
* A3 DELEGATE+DISCIPLINE — A2 plus the orchestration playbook (A3_PLAYBOOK):
  rolling-wave planning, uncertainty-first PROBE leaves, a contract + a
  mechanical check per leaf, no tiny/global-context leaves, plan versions,
  cancel stale leaves on re-plan.

Perturbation: half the runs get the fixture's REQUIREMENT CHANGE at 35% of
the wall budget — the orchestrator process is stopped and the SAME OpenCode
session is resumed with the change note as a new user message (identical
mechanism for every arm).

RECORDED FINDINGS (the kernel is FROZEN — everything here is bench-side):

1. GET /v1/agent-runs does NOT return a leaf's file changes. The response
   projects status/stop_reason/summary/evidence/turns/tool_calls/wall only.
   Bench-side solution: the sidecar diffs the server's per-run working copy
   (<ACI_AGENT_RUNS_ROOT>/<run_id>, kept by the product) against the snapshot
   it wrote, and the tool applies the changed files into the orchestrator
   workspace (full contents, never a patch — no fuzz, idempotent).
2. GET /v1/agent-runs does NOT expose token usage (RunUsage carries
   model_input_tokens/model_output_tokens; the REST projection drops them).
   Bench-side solution: ``kernel_usage()`` reads the durable agent_runs.usage
   projection (§41.1, migration 0016) read-only from the bench DB.
3. The surface has NO principal field (route telemetry says client_type
   harness-kernel; A2A principals do not apply here). Bench-side: the runner
   stamps principal "xl-bench" on its own rows; every delegation carries a
   bench tag so leaves attribute to orchestrator runs.
4. POST /v1/agent-runs is SYNCHRONOUS (returns the terminal RunResult), so
   "poll GET" happens bench-side: the sidecar runs the POST on a worker
   thread and the orchestrator polls the sidecar. A leaf that pauses
   (clarification/approval) cannot wait for a human inside a bench run —
   the sidecar cancels it and reports the pause honestly.

Isolation (rc_bench pattern, ADR-014 amendment 29 — reused, NOT weakened):
measurement runs happen on the LINUX host inside bubblewrap (``_bwrap``
imported from scripts/rc_bench.py: tmpfs over /home, /.snapshots, /tmp,
/var/tmp; docker socket masked; own PID ns). The ACI server and this
sidecar run OUTSIDE the sandbox; the kernel's own workspace processes use
its OS sandbox as usual (bwrap/Seatbelt, fail-closed). Hidden suites and
reference solutions are NEVER inside a run workspace; post-hoc suites run
OUTSIDE the sandbox in a fresh copy. On macOS the round is REFUSED (no
bwrap) — only --smoke runs here, and the smoke is NOT a measurement.

Usage::

    .venv/bin/python scripts/xl_bench.py sidecar --port 8770 \
        --aci http://127.0.0.1:8020 --workspace-root DIR --runs-root DIR
    .venv/bin/python scripts/xl_bench.py verify-fixture data/xl-bench/fixtures/<name>
    .venv/bin/python scripts/xl_bench.py smoke            # Mac wiring smoke (NOT a measurement)
    .venv/bin/python scripts/xl_bench.py round ...        # Linux host only
"""

from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

#: The OpenCode custom-tool template the runner copies into a run's .opencode/.
TEMPLATE = ROOT / "scripts/xl_oc_template"
MODEL = "OneNexus/glm-5.3"
OPENCODE = Path.home() / ".opencode/bin/opencode"
BUN = Path.home() / ".bun/bin/bun"
#: Bench-side leaf budget cap (the job's contract; the REST surface allows 200).
MAX_LEAF_TURNS = 60
#: Tool/VCS noise never counted as leaf output or snapshot content.
NOISE_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git", "node_modules"}
)
#: The marker file the sidecar plants in every snapshot so a cancel can find
#: the run id while the (synchronous) POST /v1/agent-runs is still executing.
LEAF_MARKER = ".xl-leaf"
#: Apply/diff safety caps (a leaf that exceeds them is reported, not merged blind).
MAX_APPLY_FILES = 200
MAX_APPLY_BYTES = 32 * 1024 * 1024
MAX_DIFF_CHARS = 24_000

A2_PLAYBOOK = """\
ORCHESTRATION CONTRACT — you are the ORCHESTRATOR, not the coder.

You plan, read, and verify. You do NOT write or edit code files yourself:
every code-writing leaf subtask is executed by the ACI agent service
through three tools:

- delegate_to_aci(objective, verification_command, ...) — snapshots this
  workspace and starts an ACI agent run on the snapshot. Returns a leaf_id
  immediately; the leaf runs asynchronously.
- delegate_to_aci_check(leaf_id) — polls a leaf. When it is terminal its
  changed files are MERGED into this workspace and you get the status, the
  verification evidence, the changed files, and a diff.
- delegate_to_aci_cancel(leaf_id) — cancels a leaf; its partial changes are
  NOT merged.

Rules:
1. EAGER DECOMPOSITION: before any delegation, write out the whole task as
   a tree of self-contained leaves.
2. A leaf sees ONLY the snapshot taken at delegation time. Its objective
   must be a complete contract: what to change, in which files, the required
   behavior, and the mechanical check (verification_command, e.g.
   ["python","-m","pytest","-q"]) that proves it. Never refer to "the plan
   above" — restate everything the leaf needs.
3. Delegate in dependency order; poll each leaf with delegate_to_aci_check
   until it is terminal before building on it.
4. Verify every merged leaf yourself (read the diff, run the check) before
   depending on it. If a leaf failed or was cancelled, re-delegate a
   corrected leaf.
5. Do not delegate planning, reading, or verification — do those yourself
   with your own tools. Only code-writing leaves go to ACI.
"""

A3_PLAYBOOK = (
    A2_PLAYBOOK
    + """\

PLANNING DISCIPLINE (in addition to the contract above):
1. ROLLING WAVE: plan only the next 1-2 levels of the tree. Expand a leaf
   into sub-leaves only when it becomes next. Never plan the whole tree up
   front.
2. UNCERTAINTY FIRST: order leaves so the riskiest, most uncertain work
   comes first, and de-risk it with a cheap read-only PROBE leaf (objective:
   "investigate X, change nothing, report findings in the summary") before
   the implementation leaf that depends on it.
3. Every leaf carries a contract AND a mechanical check — never delegate
   without both.
4. NEVER delegate tiny work (an edit you can specify exactly in one line)
   or global-context work (needs the whole task's history): fold it into
   the next leaf's contract instead.
5. PLAN VERSIONS: prefix every leaf objective with the current plan
   version, e.g. "[plan v2] <contract>". When you re-plan because the plan
   changed, CANCEL every still-running leaf from older plan versions with
   delegate_to_aci_cancel before delegating new ones.
6. Re-plan on evidence: after each leaf check, update the plan from what
   the leaf learned; never delegate from a stale plan.
"""
)

#: The smoke's toy task — a wiring probe, deliberately NOT an XL fixture.
TOY_TASK = {
    "files": {
        "toy_greet.py": (
            '"""A tiny greeting helper — with a bug."""\n\n\n'
            "def greeting(names: list[str]) -> str:\n"
            '    """Return a greeting for the given names."""\n'
            '    return "Hello, !"\n'
        ),
        "test_toy.py": (
            "from toy_greet import greeting\n\n\n"
            "def test_greeting_lists_names() -> None:\n"
            '    assert greeting(["ada", "grace"]) == "Hello, ada, grace!"\n'
        ),
    },
    "prompt": (
        "The test in test_toy.py fails. Fix the root cause and make the suite "
        "green. You MUST delegate the fix to the ACI agent service with the "
        "delegate_to_aci tool (objective = a complete leaf contract, "
        'verification_command ["python","-m","pytest","-q"]) and poll '
        "delegate_to_aci_check until the leaf is terminal — do not edit the "
        "files yourself."
    ),
}


# ---------------------------------------------------------------------------
# ACI REST client (sidecar-side; the orchestrator NEVER talks to the server)
# ---------------------------------------------------------------------------


class AciClient(Protocol):
    """What the sidecar needs from the ACI server (fake in tests)."""

    def post_agent_run(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def cancel_agent_run(self, run_id: str) -> bool: ...


class HttpAciClient:
    """httpx-backed client for POST /v1/agent-runs (+ cancel). The POST is
    SYNCHRONOUS server-side (returns the terminal RunResult) — the sidecar
    always calls it from a worker thread."""

    def __init__(self, base_url: str, token: str = "", timeout: float = 7200.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def post_agent_run(self, body: dict[str, Any]) -> dict[str, Any]:
        import httpx

        response = httpx.post(
            f"{self._base}/v1/agent-runs",
            json=body,
            headers=self._headers(),
            timeout=self._timeout,
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"ACI POST /v1/agent-runs {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    def cancel_agent_run(self, run_id: str) -> bool:
        import httpx

        response = httpx.post(
            f"{self._base}/v1/agent-runs/{run_id}/cancel",
            headers=self._headers(),
            timeout=60.0,
        )
        return response.status_code == 200 and bool(response.json().get("cancelled"))


# ---------------------------------------------------------------------------
# Delegation sidecar (bench-side bridge; runs on the host, outside any sandbox)
# ---------------------------------------------------------------------------


@dataclass
class LeafState:
    """One delegated leaf — sidecar-owned state (never product state)."""

    leaf_id: str
    workspace: str
    objective: str
    tag: str = ""
    run_id: str | None = None
    status: str = "delegated"  # delegated|succeeded|failed|cancelled|error
    cancelled: bool = False
    cancel_requested: bool = False
    diff_done: bool = False
    result: dict[str, Any] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    diff: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    checked_at: float | None = None

    def compact(self) -> dict[str, Any]:
        return {
            "leaf_id": self.leaf_id,
            "run_id": self.run_id,
            "status": self.status,
            "cancelled": self.cancelled,
            "objective": self.objective,
            "tag": self.tag,
            "changed_files": self.changed_files,
            "deleted_files": self.deleted_files,
            "error": self.error,
            "wall_seconds": round((self.finished_at or time.time()) - self.started_at, 1),
        }


def _safe_member(name: str) -> bool:
    """A tar member is safe to extract into the snapshot dir (no escape)."""
    parts = Path(name).parts
    return bool(parts) and not name.startswith(("/", "\\")) and ".." not in parts


def untar_snapshot(data: bytes, target: Path) -> None:
    """Extract a client snapshot (tar.gz) into a fresh ``target`` dir."""
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not (member.isfile() or member.isdir()):
                continue
            if not _safe_member(member.name):
                raise ValueError(f"unsafe snapshot member: {member.name!r}")
            if member.isfile() and member.size > MAX_APPLY_BYTES:
                raise ValueError(f"snapshot member too large: {member.name}")
            tar.extractall(target, filter="data")


def _tree_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if NOISE_DIRS & set(rel.parts) or rel.name == LEAF_MARKER:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def diff_trees(snapshot: Path, run_dir: Path) -> tuple[list[str], list[str], str, dict[str, str]]:
    """Changed/new/deleted files + a unified diff + the apply payload
    ({rel: base64 content} for changed/new) — snapshot vs the server's
    per-run working copy. FINDING #1's bench-side solution."""
    before, after = _tree_hashes(snapshot), _tree_hashes(run_dir)
    changed = sorted(p for p in before.keys() & after.keys() if before[p] != after[p])
    added = sorted(after.keys() - before.keys())
    deleted = sorted(before.keys() - after.keys())
    files: dict[str, str] = {}
    diff_chunks: list[str] = []
    total_bytes = 0
    for rel in [*changed, *added]:
        if len(files) >= MAX_APPLY_FILES or total_bytes >= MAX_APPLY_BYTES:
            raise ValueError("leaf output exceeds the apply cap (too many/large files)")
        content = (run_dir / rel).read_bytes()
        files[rel] = base64.b64encode(content).decode()
        total_bytes += len(content)
        old = (snapshot / rel).read_bytes() if rel in before else b""
        try:
            diff_chunks.append(
                "".join(
                    difflib.unified_diff(
                        old.decode("utf-8").splitlines(keepends=True),
                        content.decode("utf-8").splitlines(keepends=True),
                        fromfile=f"a/{rel}",
                        tofile=f"b/{rel}",
                    )
                )
            )
        except UnicodeDecodeError:
            diff_chunks.append(f"--- a/{rel}\n+++ b/{rel}\n[binary file changed]\n")
    for rel in deleted:
        diff_chunks.append(f"--- a/{rel}\n+++ /dev/null\n[deleted]\n")
    diff = "".join(diff_chunks)
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + f"\n… [diff truncated, {len(diff)} chars total]\n"
    return [*changed, *added], deleted, diff, files


class DelegationSidecar:
    """The bench-side bridge between the sandboxed orchestrator and ACI.

    The orchestrator's custom tool snapshots its workspace and POSTs it
    here; the sidecar unpacks it under the ACI server's workspace root,
    starts the (synchronous) POST /v1/agent-runs on a worker thread, and
    answers polls with the leaf's compact result plus the file changes
    (diffed bench-side from the server's per-run working copy). The ACI
    bearer token lives ONLY here — the orchestrator never sees it.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        runs_root: Path,
        aci: AciClient,
        max_leaf_turns: int = MAX_LEAF_TURNS,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.runs_root = Path(runs_root)
        self.aci = aci
        self.max_leaf_turns = max_leaf_turns
        self._lock = threading.Lock()
        self.leaves: dict[str, LeafState] = {}

    # -- delegation ---------------------------------------------------------

    def handle_delegate(self, body: dict[str, Any]) -> dict[str, Any]:
        objective = str(body.get("objective") or "").strip()
        if not objective or len(objective) > 8000:
            return {"ok": False, "error": "objective must be 1..8000 chars"}
        try:
            snapshot = base64.b64decode(body.get("snapshot") or "", validate=True)
        except ValueError:  # binascii.Error subclasses ValueError
            return {"ok": False, "error": "snapshot must be base64 tar.gz"}
        if not snapshot:
            return {"ok": False, "error": "snapshot must be base64 tar.gz"}
        leaf_id = f"leaf_{secrets.token_hex(6)}"
        workspace = f"xl-{leaf_id}"
        target = self.workspace_root / workspace
        if target.exists():
            return {"ok": False, "error": "workspace name collision (retry)"}
        try:
            untar_snapshot(snapshot, target)
        except (ValueError, tarfile.TarError, OSError) as exc:
            shutil.rmtree(target, ignore_errors=True)
            return {"ok": False, "error": f"bad snapshot: {exc}"}
        # The marker lets a cancel find the run id while the synchronous
        # POST is still executing (the run dir is a copy of the snapshot).
        (target / LEAF_MARKER).write_text(leaf_id, encoding="utf-8")
        leaf = LeafState(
            leaf_id=leaf_id,
            workspace=workspace,
            objective=objective,
            tag=str(body.get("tag") or ""),
        )
        with self._lock:
            self.leaves[leaf_id] = leaf
        aci_body: dict[str, Any] = {
            "objective": objective,
            "constraints": [str(c) for c in body.get("constraints") or []][:100],
            "acceptance_criteria": [str(c) for c in body.get("acceptance_criteria") or []][:100],
            "workspace": workspace,
            "requested_profile": "coder",
            "max_turns": min(
                int(body.get("max_turns") or self.max_leaf_turns), self.max_leaf_turns
            ),
        }
        verification = [str(v) for v in body.get("verification_command") or []][:100]
        if verification:
            aci_body["verification_command"] = verification
        scopes = [str(s) for s in body.get("write_scopes") or []][:100]
        if scopes:
            aci_body["write_scopes"] = scopes
        threading.Thread(target=self._execute_leaf, args=(leaf, aci_body), daemon=True).start()
        return {"ok": True, "leaf_id": leaf_id, "workspace": workspace, "status": "delegated"}

    def _execute_leaf(self, leaf: LeafState, aci_body: dict[str, Any]) -> None:
        try:
            if leaf.cancel_requested:  # cancelled before the POST was issued
                self._finish(leaf, status="cancelled", cancelled=True)
                return
            result = self.aci.post_agent_run(aci_body)
            leaf.run_id = str(result.get("run_id"))
            status = str(result.get("status") or "failed")
            if status in ("interrupted", "interrupted_approval"):
                # A bench leaf cannot wait for a human (FINDING #4): cancel it
                # and report the pause honestly — the orchestrator re-delegates.
                if leaf.run_id:
                    self.aci.cancel_agent_run(leaf.run_id)
                self._finish(leaf, status="cancelled", cancelled=True, result=result)
                return
            if leaf.cancel_requested and status != "cancelled":
                # The cancel raced the terminal POST: the work happened, but the
                # orchestrator asked to drop it — never merge a cancelled leaf.
                if leaf.run_id:
                    self.aci.cancel_agent_run(leaf.run_id)
                self._finish(leaf, status="cancelled", cancelled=True, result=result)
                return
            self._finish(
                leaf,
                status=status,
                cancelled=status == "cancelled",
                result=result,
            )
        except Exception as exc:  # noqa: BLE001 — a crashed leaf is a row, not a bench
            leaf.error = f"{type(exc).__name__}: {exc}"[:500]
            self._finish(leaf, status="error")

    def _finish(
        self,
        leaf: LeafState,
        *,
        status: str,
        cancelled: bool = False,
        result: dict[str, Any] | None = None,
    ) -> None:
        leaf.status = status
        leaf.cancelled = leaf.cancelled or cancelled
        leaf.result = result or {}
        leaf.finished_at = time.time()
        self._maybe_diff(leaf)

    def _maybe_diff(self, leaf: LeafState) -> None:
        """FINDING #1's bench-side solution, computed once the server's
        per-run working copy exists (at the terminal state — or lazily at the
        first check if the copy appeared late)."""
        if leaf.diff_done or leaf.run_id is None:
            return
        run_dir = self.runs_root / leaf.run_id
        if not run_dir.is_dir():
            return
        try:
            changed, deleted, diff, files = diff_trees(
                self.workspace_root / leaf.workspace, run_dir
            )
            leaf.changed_files, leaf.deleted_files, leaf.diff = changed, deleted, diff
            leaf.result = {**leaf.result, "files": files}
        except Exception as exc:  # noqa: BLE001 — report, never crash the bench
            leaf.error = f"diff failed: {exc}"[:500]
        finally:
            leaf.diff_done = True

    # -- poll / cancel ------------------------------------------------------

    def handle_check(self, leaf_id: str) -> dict[str, Any]:
        with self._lock:
            leaf = self.leaves.get(leaf_id)
        if leaf is None:
            return {"ok": False, "error": f"unknown leaf: {leaf_id}"}
        leaf.checked_at = time.time()
        if leaf.status == "delegated":  # still executing on the ACI server
            return {"ok": True, "leaf_id": leaf_id, "status": "running"}
        self._maybe_diff(leaf)  # the run copy may have appeared after _finish
        return {
            "ok": True,
            "leaf_id": leaf_id,
            "run_id": leaf.run_id,
            "status": leaf.status,
            "cancelled": leaf.cancelled,
            "objective": leaf.objective,
            **{
                key: leaf.result.get(key)
                for key in (
                    "stop_reason",
                    "detail_code",
                    "summary",
                    "evidence_verdict",
                    "checks",
                    "evidence_refs",
                    "turns",
                    "tool_calls",
                    "wall_time_seconds",
                )
            },
            "changed_files": leaf.changed_files,
            "deleted_files": leaf.deleted_files,
            "diff": leaf.diff,
            "files": leaf.result.get("files") or {},
            "error": leaf.error,
        }

    def _find_run_by_marker(self, leaf: LeafState) -> str | None:
        """The run id while the synchronous POST is still executing: the
        server's per-run working copy is a copy of this leaf's snapshot, so
        it carries the marker."""
        if not self.runs_root.is_dir():
            return None
        for run_dir in self.runs_root.iterdir():
            marker = run_dir / LEAF_MARKER
            try:
                if marker.is_file() and marker.read_text(encoding="utf-8") == leaf.leaf_id:
                    return run_dir.name
            except OSError:
                continue
        return None

    def handle_cancel(self, leaf_id: str) -> dict[str, Any]:
        with self._lock:
            leaf = self.leaves.get(leaf_id)
        if leaf is None:
            return {"ok": False, "error": f"unknown leaf: {leaf_id}"}
        leaf.cancel_requested = True
        # Bench-side drop: whatever the kernel does with the run, the
        # orchestrator asked to abandon this leaf — it is never merged.
        leaf.cancelled = True
        run_id = leaf.run_id or self._find_run_by_marker(leaf)
        if run_id:
            leaf.run_id = run_id
            try:
                self.aci.cancel_agent_run(run_id)
            except Exception as exc:  # noqa: BLE001 — cancel is best-effort
                return {
                    "ok": True,
                    "leaf_id": leaf_id,
                    "cancelled": True,
                    "run_id": run_id,
                    "note": f"cancel POST failed: {exc}"[:200],
                }
            return {"ok": True, "leaf_id": leaf_id, "cancelled": True, "run_id": run_id}
        return {
            "ok": True,
            "leaf_id": leaf_id,
            "cancelled": True,
            "run_id": None,
            "note": "run not provisioned yet; will be cancelled at start",
        }

    def handle_leaves(self, tag: str = "") -> dict[str, Any]:
        with self._lock:
            leaves = [leaf.compact() for leaf in self.leaves.values()]
        if tag:
            leaves = [leaf for leaf in leaves if tag in leaf["tag"]]
        return {"ok": True, "leaves": leaves}


class _SidecarHandler(BaseHTTPRequestHandler):
    """Thin stdlib HTTP dispatch onto a DelegationSidecar (no framework)."""

    def log_message(self, fmt: str, *args: object) -> None:  # silence
        return

    def _sidecar(self) -> DelegationSidecar:
        return self.server.sidecar  # type: ignore[attr-defined]

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode())

    def do_GET(self) -> None:  # noqa: N802 — stdlib naming
        query = parse_qs(self.path.partition("?")[2])
        if self.path.startswith("/check"):
            self._json(200, self._sidecar().handle_check(query.get("leaf_id", [""])[0]))
        elif self.path.startswith("/leaves"):
            self._json(200, self._sidecar().handle_leaves(query.get("tag", [""])[0]))
        elif self.path.startswith("/healthz"):
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 — stdlib naming
        try:
            if self.path.startswith("/delegate"):
                self._json(200, self._sidecar().handle_delegate(self._body()))
            elif self.path.startswith("/cancel"):
                body = self._body()
                self._json(200, self._sidecar().handle_cancel(str(body.get("leaf_id") or "")))
            else:
                self._json(404, {"ok": False, "error": "not found"})
        except Exception as exc:  # noqa: BLE001 — one bad request never kills the bench
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})


class _SidecarServer(ThreadingHTTPServer):
    def __init__(self, *args: Any, sidecar: DelegationSidecar, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.sidecar = sidecar


def serve_sidecar(sidecar: DelegationSidecar, host: str, port: int) -> _SidecarServer:
    """Start the sidecar HTTP server (accept loop on a daemon thread)."""
    server = _SidecarServer((host, port), _SidecarHandler, sidecar=sidecar)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------------------
# Metrics (all bench-side; no product code touched)
# ---------------------------------------------------------------------------


def parse_opencode_stream(path: Path) -> dict[str, Any]:
    """OpenCode tokens/session/errors from a ``--format=json`` stream: the
    step_finish events carry ``part.tokens`` (input/output/reasoning/cache)."""
    tokens_in = tokens_out = 0
    session: str | None = None
    errors: list[str] = []
    tool_uses = 0
    for line in path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if session is None and event.get("sessionID"):
            session = str(event["sessionID"])
        kind = event.get("type")
        if kind == "step_finish":
            tokens = (event.get("part") or {}).get("tokens") or {}
            tokens_in += int(tokens.get("input") or 0)
            tokens_out += int(tokens.get("output") or 0)
        elif kind == "tool_use":
            tool_uses += 1
        elif kind == "error":
            errors.append(json.dumps(event.get("error") or {})[:300])
    return {
        "session_id": session,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tool_uses": tool_uses,
        "errors": errors,
    }


def _psycopg_url(db_url: str) -> str:
    """SQLAlchemy dialect suffixes (postgresql+psycopg://) are not libpq URIs."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", db_url)


def kernel_usage(db_url: str, run_ids: list[str]) -> dict[str, dict[str, Any]]:
    """FINDING #2's bench-side solution: GET /v1/agent-runs omits token usage
    (RunUsage has model_input_tokens/model_output_tokens; the REST response
    drops them), so read the durable agent_runs.usage projection (§41.1,
    migration 0016) READ-ONLY from the bench DB."""
    import psycopg

    with psycopg.connect(_psycopg_url(db_url), connect_timeout=10) as conn:
        rows = conn.execute(
            "SELECT run_id, usage FROM agent_runs WHERE run_id = ANY(%s)", (run_ids,)
        ).fetchall()
    return {str(run_id): (usage or {}) for run_id, usage in rows}


def shadow_tree(work: Path) -> str:
    """A read-only snapshot of the workspace state as a git tree sha: a
    THROWAWAY index (GIT_INDEX_FILE) + `git add -A` + `git write-tree` —
    HEAD, the real index, and the working tree are never touched."""
    index = work / ".git" / f"xl-shadow-index-{secrets.token_hex(4)}"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    try:
        subprocess.run(
            ["git", "-C", str(work), "add", "-A"], env=env, capture_output=True, check=True
        )
        tree = subprocess.run(
            ["git", "-C", str(work), "write-tree"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return tree.stdout.strip()
    finally:
        index.unlink(missing_ok=True)


def _numstat_added(work: Path, a: str, b: str) -> int:
    out = subprocess.run(
        ["git", "-C", str(work), "diff", "--numstat", a, b],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    total = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].isdigit():
            total += int(parts[0])
    return total


def reverted_lines(work: Path, trees: list[str], final: str) -> dict[str, int]:
    """Re-planning waste proxy (pre-registered): ``lines_written`` = lines
    added across consecutive shadow trees; ``lines_survived`` = lines added
    initial→final; ``lines_reverted`` = written − survived (floor 0). A line
    rewritten counts as written twice — documented proxy, not a claim."""
    written = sum(_numstat_added(work, a, b) for a, b in zip(trees, trees[1:], strict=False))
    survived = _numstat_added(work, trees[0], final) if len(trees) > 1 else 0
    return {
        "lines_written": written,
        "lines_survived": survived,
        "lines_reverted": max(0, written - survived),
    }


def parse_pytest_summary(stdout: str) -> dict[str, int]:
    """Objective grading: pass FRACTION from a pytest -q summary line."""

    def _count(pattern: str) -> int:
        found = re.search(pattern, stdout)
        return int(found.group(1)) if found else 0

    return {
        "passed": _count(r"(\d+) passed"),
        "failed": _count(r"(\d+) failed"),
        "errors": _count(r"(\d+) error"),
    }


def hidden_suite_result(
    work: Path,
    hidden_dir: Path,
    *,
    extra_hidden: Path | None = None,
    invalidated: list[str] | None = None,
    venv_python: Path | None = None,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """Post-hoc grading OUTSIDE the sandbox in a FRESH copy: hidden tests are
    copied in (they are never inside a run workspace), invalidated originals
    are removed for perturbed runs, pytest runs, pass fraction is reported."""
    fresh = work.parent / f"{work.name}-posthoc-{secrets.token_hex(4)}"
    shutil.copytree(work, fresh, ignore=shutil.ignore_patterns(*NOISE_DIRS))
    try:
        for source in [hidden_dir] + ([extra_hidden] if extra_hidden else []):
            for test in source.rglob("*.py"):
                rel = test.relative_to(source)
                (fresh / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(test, fresh / rel)
        for rel in invalidated or []:
            (fresh / rel).unlink(missing_ok=True)
        proc = subprocess.run(
            [
                str(venv_python or sys.executable),
                "-m",
                "pytest",
                "-q",
                "--tb=no",
                "-p",
                "no:cacheprovider",
            ],
            cwd=fresh,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        counts = parse_pytest_summary(proc.stdout + proc.stderr)
        total = counts["passed"] + counts["failed"] + counts["errors"]
        return {
            "pass_fraction": round(counts["passed"] / total, 4) if total else 0.0,
            **counts,
            "exit_code": proc.returncode,
            "summary_tail": (proc.stdout + proc.stderr)[-400:],
        }
    finally:
        shutil.rmtree(fresh, ignore_errors=True)


# ---------------------------------------------------------------------------
# Fixtures (format + validator only — NO fixtures ship with this harness)
# ---------------------------------------------------------------------------


@dataclass
class Fixture:
    """An XL fixture on disk (nothing here ever enters a run workspace):

    workspace/   the seed workspace copied into a run
    TASK.md      the task text given to the orchestrator
    hidden/      the ORIGINAL hidden acceptance suite (post-hoc only)
    change/      the REQUIREMENT-CHANGE pack: CHANGE_NOTE.md (sent at 35%),
                 hidden/ (the extra tests encoding the change),
                 invalidates.txt (original hidden files the change retires)
    reference/   the reference solution (applied over the seed proves
                 pass-when-solved for BOTH suites)
    """

    name: str
    dir: Path
    task: str
    change_note: str | None
    invalidated: list[str]

    @property
    def perturbed(self) -> bool:
        return self.change_note is not None


def load_fixture(path: Path) -> Fixture:
    task = (path / "TASK.md").read_text(encoding="utf-8")
    change = path / "change"
    note = (change / "CHANGE_NOTE.md").read_text(encoding="utf-8") if change.is_dir() else None
    invalidated: list[str] = []
    if note is not None and (change / "invalidates.txt").is_file():
        invalidated = [
            line.strip()
            for line in (change / "invalidates.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return Fixture(name=path.name, dir=path, task=task, change_note=note, invalidated=invalidated)


def verify_fixture(path: Path, venv_python: Path | None = None) -> int:
    """A fixture must be FAIL-AS-SHIPPED and PASS-WHEN-SOLVED for BOTH suites
    (the reference proves the latter; it is kept outside the workspace)."""
    fixture = load_fixture(path)
    seed, reference = path / "workspace", path / "reference"
    problems: list[str] = []

    def _case(label: str, *, reference_on: bool, perturbed: bool, expect_pass: bool) -> None:
        tmp = Path(tempfile.mkdtemp(prefix=f"xl-fixture-{fixture.name}-"))
        try:
            work = tmp / "work"
            shutil.copytree(seed, work)
            if reference_on:
                for ref in sorted(reference.rglob("*")):
                    if ref.is_file():
                        rel = ref.relative_to(reference)
                        (work / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(ref, work / rel)
            result = hidden_suite_result(
                work,
                path / "hidden",
                extra_hidden=(path / "change" / "hidden") if perturbed else None,
                invalidated=fixture.invalidated if perturbed else None,
                venv_python=venv_python,
            )
            total = result["passed"] + result["failed"] + result["errors"]
            ok = (result["pass_fraction"] == 1.0) == expect_pass and total > 0
            print(
                f"[{fixture.name}] {label}: fraction={result['pass_fraction']} "
                f"passed={result['passed']} failed={result['failed']} "
                f"errors={result['errors']} -> {'OK' if ok else 'VIOLATION'}"
            )
            if not ok:
                problems.append(label)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    _case(
        "fail-as-shipped (seed, original suite)",
        reference_on=False,
        perturbed=False,
        expect_pass=False,
    )
    _case(
        "pass-when-solved (reference, original suite)",
        reference_on=True,
        perturbed=False,
        expect_pass=True,
    )
    if fixture.perturbed:
        _case(
            "fail-as-shipped (seed, post-change suite)",
            reference_on=False,
            perturbed=True,
            expect_pass=False,
        )
        _case(
            "pass-when-solved (reference, post-change suite)",
            reference_on=True,
            perturbed=True,
            expect_pass=True,
        )
    else:
        print(f"[{fixture.name}] no change/ pack: unperturbed-only fixture")
    if problems:
        print(f"FIXTURE INVALID: {problems}")
        return 1
    print(f"fixture {fixture.name} OK")
    return 0


# ---------------------------------------------------------------------------
# Runner (the pre-registered round — LINUX host only, bwrap isolation)
# ---------------------------------------------------------------------------


def _gateway() -> tuple[str, str]:
    """The local model gateway (base URL + key) from the user's opencode
    config — the key is returned to the caller, NEVER printed."""
    cfg_path = Path.home() / ".config/opencode/opencode.json"
    text = re.sub(r"(?m)^\s*//.*$", "", cfg_path.read_text())
    cfg = json.loads(text)
    opts = cfg["provider"]["local-gateway"]["options"]
    return str(opts["baseURL"]), str(opts["apiKey"])


def ensure_oc_template(cache: Path) -> Path:
    """Install the xl tool template into ``cache`` (bun install there — the
    repo template dir stays clean) and return the ready-to-copy template."""
    if cache.exists():
        shutil.rmtree(cache)
    shutil.copytree(TEMPLATE, cache)
    subprocess.run([str(BUN), "install"], cwd=cache, check=True, capture_output=True)
    return cache


def _write_oc_config(root: Path, base_url: str, api_key: str) -> None:
    """The orchestrator's per-run OpenCode config (rc_bench pattern): the
    model through the local gateway; NO ACI plugin, NO skills catalog — the
    only ACI presence in A2/A3 is the delegate tool."""
    cfg = root / "xdg-config/opencode"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "opencode.json").write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "model": f"local-gateway/{MODEL}",
                "provider": {
                    "local-gateway": {
                        "npm": "@ai-sdk/openai-compatible",
                        "options": {"baseURL": base_url, "apiKey": api_key},
                        "models": {MODEL: {"tool_call": True}},
                    }
                },
            }
        )
    )


def _opencode_env(root: Path, work: Path, sidecar_url: str, tag: str) -> dict[str, str]:
    for sub in ("xdg-data", "xdg-cache", "xdg-state"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return {
        "PATH": (
            f"{Path.home() / '.opencode/bin'}:{Path.home() / '.bun/bin'}"
            ":/usr/local/bin:/usr/bin:/bin"
        ),
        "HOME": str(Path.home()),
        "PWD": str(work),
        "TERM": "dumb",
        "LANG": "C.UTF-8",
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
        # The orchestrator must never be routed by the ACI plugin (mac rule +
        # the bench measures orchestration, not skill routing):
        "ACI_ROUTER_DISABLED": "1",
        "XL_SIDECAR_URL": sidecar_url,
        "XL_RUN_TAG": tag,
    }


def _is_model_failure(stream: dict[str, Any], wall: float) -> bool:
    """rc_bench's invalid-row rule: a run that died on the provider before
    doing work (gateway 5xx / transport / auth, under a minute, no tools)."""
    if wall > 60 or stream.get("tool_uses"):
        return False
    return any(
        re.search(r"provider\.transport|provider\.auth|5\d\d|APIError", error)
        for error in stream.get("errors") or []
    )


def run_one_cell(
    *,
    fixture: Fixture,
    arm: str,
    repeat: int,
    perturbed: bool,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """One orchestrator run: materialize, isolate (bwrap), run (with the
    change@35% pause/resume when perturbed), collect metrics, grade post-hoc."""
    from rc_bench import _bwrap  # noqa: PLC0415 — reused, NOT weakened (amendment 29)

    root = Path(ctx["runs"]) / f"{arm.lower()}-{fixture.name}-{repeat}-{secrets.token_hex(4)}"
    work = root / "work"
    work.mkdir(parents=True)
    shutil.copytree(fixture.dir / "workspace", work)
    tag = f"xl-bench:{arm}:{fixture.name}:{repeat}"
    base_url, api_key = _gateway()
    _write_oc_config(root, base_url, api_key)
    if arm != "A1":
        shutil.copytree(ctx["template"], work / ".opencode")
    env = _opencode_env(root, work, ctx["sidecar_url"], tag)
    prompt = (
        fixture.task
        if arm == "A1"
        else (A3_PLAYBOOK if arm == "A3" else A2_PLAYBOOK) + "\n\nTASK:\n\n" + fixture.task
    )
    # git in the run workspace: the reverted-lines instrument needs trees.
    subprocess.run(["git", "init", "-q"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "user.email=xl@bench",
            "-c",
            "user.name=xl-bench",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
        capture_output=True,
    )
    trees: list[str] = [shadow_tree(work)]
    stop = threading.Event()

    def _sample() -> None:
        while not stop.wait(30.0):
            try:
                trees.append(shadow_tree(work))
            except subprocess.CalledProcessError:
                pass

    sampler = threading.Thread(target=_sample, daemon=True)
    sampler.start()
    streams: list[Path] = []
    started = time.time()

    def _segment(message: str, cap: float, session: str | None) -> bool:
        stream = root / f"stream-{len(streams)}.jsonl"
        streams.append(stream)
        cmd = [str(OPENCODE), "run", "--standalone", "--auto", "--format=json"]
        if session:
            cmd += ["--session", session]
        cmd += [message]
        try:
            with stream.open("w") as out_fh:
                subprocess.run(
                    _bwrap(root, ctx["extra_ro"]) + ["--chdir", str(work), "--"] + cmd,
                    env=env,
                    stdout=out_fh,
                    stderr=subprocess.STDOUT,
                    timeout=cap,
                    check=False,
                )
        except subprocess.TimeoutExpired:
            return True  # the "pause": the session survives on disk
        return False

    budget = float(ctx["wall_budget"])
    timed_out = False
    if perturbed:
        if not fixture.change_note:
            raise SystemExit(f"fixture {fixture.name} has no change/ pack")
        _segment(prompt, budget * 0.35, None)
        session = None
        for stream in streams:
            session = session or parse_opencode_stream(stream)["session_id"]
        if session is None:
            timed_out = True  # no persisted session: the resume cannot happen
        else:
            _segment(fixture.change_note, budget * 0.65, session)
    else:
        timed_out = _segment(prompt, budget, None)
    stop.set()
    sampler.join(timeout=5.0)
    trees.append(shadow_tree(work))
    wall = round(time.time() - started, 1)

    parsed = [parse_opencode_stream(stream) for stream in streams]
    tokens_in = sum(p["tokens_in"] for p in parsed)
    tokens_out = sum(p["tokens_out"] for p in parsed)
    leaves = ctx["sidecar"].handle_leaves(tag)["leaves"]
    run_ids = [leaf["run_id"] for leaf in leaves if leaf["run_id"]]
    usage = kernel_usage(ctx["db_url"], run_ids) if run_ids else {}
    kernel_in = sum(int(u.get("model_input_tokens") or 0) for u in usage.values())
    kernel_out = sum(int(u.get("model_output_tokens") or 0) for u in usage.values())
    grade = hidden_suite_result(
        work,
        fixture.dir / "hidden",
        extra_hidden=(fixture.dir / "change" / "hidden") if perturbed else None,
        invalidated=fixture.invalidated if perturbed else None,
        venv_python=Path(ctx["venv_python"]),
    )
    text = "\n".join(stream.read_text(errors="replace") for stream in streams)
    text = text.replace(str(root), "<RUN>")
    forbidden = sorted({f for f in ctx["forbidden"] if f in text})
    stream0 = parsed[0] if parsed else {}
    total_tests = grade["passed"] + grade["failed"] + grade["errors"]
    invalid_reason = (
        "CONTAMINATION"
        if forbidden
        else "MODEL_FAILURE"
        if _is_model_failure(stream0, wall)
        else "NO_TESTS_RAN"
        if not total_tests
        else ""
    )
    return {
        "arm": arm,
        "fixture": fixture.name,
        "repeat": repeat,
        "perturbed": perturbed,
        "principal": "xl-bench",
        "run_root": str(root),
        "wall_seconds": wall,
        "timed_out": timed_out,
        "oc_tokens_in": tokens_in,
        "oc_tokens_out": tokens_out,
        "kernel_tokens_in": kernel_in,
        "kernel_tokens_out": kernel_out,
        "leaves": len(leaves),
        "leaves_cancelled": sum(1 for leaf in leaves if leaf["cancelled"]),
        "leaf_run_ids": run_ids,
        "leaf_details": leaves,
        **reverted_lines(work, trees, trees[-1]),
        **grade,
        "contamination_hits": forbidden,
        "model_failure": _is_model_failure(stream0, wall),
        "invalid": bool(invalid_reason),
        "invalid_reason": invalid_reason,
        "started_at": started,
    }


def run_round(args: argparse.Namespace) -> int:
    """The pre-registered measurement round. REFUSED off the Linux host: the
    isolation contract (bwrap over /home, /.snapshots, /tmp + own PID ns)
    does not exist on macOS, and an unsandboxed measurement is invalid."""
    if sys.platform != "linux" or not shutil.which("bwrap"):
        print("REFUSED: --round needs the Linux bwrap isolation (rc_bench pattern).")
        print("On this Mac only the smoke is allowed (and the smoke is NOT a measurement).")
        return 2
    fixtures = [load_fixture(Path(p)) for p in args.fixtures.split(",")]
    for fixture in fixtures:
        if not (fixture.dir / "workspace").is_dir() or not (fixture.dir / "hidden").is_dir():
            print(f"REFUSED: fixture {fixture.name} lacks workspace/ or hidden/")
            return 2
    base_url, api_key = _gateway()
    bench = Path(args.bench_root)
    (bench / "workspaces").mkdir(parents=True, exist_ok=True)
    (bench / "aci-runs").mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    server_env = {
        **os.environ,
        "ACI_DATABASE_URL": args.db_url,
        "ACI_EMBEDDER": args.embedder,
        "ACI_OBJECT_STORE_ROOT": str(bench / "objects"),
        "ACI_AGENT_MODEL_BASE_URL": base_url,
        "ACI_AGENT_MODEL_API_KEY": api_key,
        "ACI_AGENT_MODEL_ID": args.model,
        "ACI_AGENT_WORKSPACE_ROOT": str(bench / "workspaces"),
        "ACI_AGENT_RUNS_ROOT": str(bench / "aci-runs"),
        "ACI_AGENT_PROCESS_PREFIXES": json.dumps(args.prefixes),
        "ACI_AGENT_RUNS_TOKEN": token,
    }
    server = subprocess.Popen(
        [
            str(Path(args.venv) / "bin" / "python"),
            "-m",
            "uvicorn",
            "aci.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.aci_port),
        ],
        cwd=ROOT,
        env=server_env,
        stdout=(bench / "aci-server.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    sidecar = DelegationSidecar(
        workspace_root=bench / "workspaces",
        runs_root=bench / "aci-runs",
        aci=HttpAciClient(f"http://127.0.0.1:{args.aci_port}", token=token),
    )
    httpd = serve_sidecar(sidecar, "127.0.0.1", args.sidecar_port)
    sidecar_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"ACI server pid={server.pid} (:{args.aci_port}), sidecar {sidecar_url}")
    try:
        import httpx

        for _ in range(120):
            try:
                ready = httpx.get(
                    f"http://127.0.0.1:{args.aci_port}/ready", timeout=2.0
                ).status_code
                if ready == 200:
                    break
                time.sleep(1.0)
            except httpx.HTTPError:
                time.sleep(1.0)
        else:
            print("ACI server never became ready")
            return 1
        template = ensure_oc_template(bench / "oc-template")
        (bench / "oc-runs").mkdir(parents=True, exist_ok=True)
        ctx = {
            "runs": bench / "oc-runs",
            "template": template,
            "sidecar_url": sidecar_url,
            "sidecar": sidecar,
            "db_url": args.db_url,
            "wall_budget": args.wall_budget,
            "venv_python": Path(args.venv) / "bin" / "python",
            "extra_ro": [
                Path.home() / ".opencode",
                Path.home() / ".bun",
                Path(args.venv) / "bin" / "python",
            ],
            "forbidden": [
                f":{args.aci_port}",
                "5432",
                "psql",
                "docker exec",
                "docker ps",
                "docker run",
                ".snapshots/",
                "Data/Projects",
                "aci_e2b",
                str(bench / "aci-runs"),
                str(bench / "objects"),
            ],
        }
        arms = args.arms.split(",")
        perturbations = (
            [True, False] if args.perturbation == "both" else [args.perturbation == "change"]
        )
        jobs = [
            (fixture, arm, repeat, perturbed)
            for repeat in range(args.repeat)
            for fixture in fixtures
            for arm in arms
            for perturbed in perturbations
        ]
        rows: list[dict[str, Any]] = []
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        for fixture, arm, repeat, perturbed in jobs:
            try:
                row = run_one_cell(
                    fixture=fixture, arm=arm, repeat=repeat, perturbed=perturbed, ctx=ctx
                )
            except Exception as exc:  # noqa: BLE001 — a crashed cell is a row
                row = {
                    "arm": arm,
                    "fixture": fixture.name,
                    "repeat": repeat,
                    "perturbed": perturbed,
                    "invalid": True,
                    "invalid_reason": f"HARNESS_CRASH:{type(exc).__name__}",
                    "error": repr(exc)[:500],
                }
            rows.append(row)
            out.write_text(json.dumps({"model": args.model, "rows": rows}, indent=1))
            print(
                f"[{fixture.name} {arm} #{repeat} "
                f"{'change' if perturbed else 'nochange'}] "
                f"fraction={row.get('pass_fraction')} wall={row.get('wall_seconds')}s "
                f"leaves={row.get('leaves')} cancelled={row.get('leaves_cancelled')} "
                f"invalid={row.get('invalid')}:{row.get('invalid_reason')}",
                flush=True,
            )
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        httpd.shutdown()


# ---------------------------------------------------------------------------
# Wiring smoke (THIS Mac; NOT a measurement)
# ---------------------------------------------------------------------------


def run_smoke(args: argparse.Namespace) -> int:
    """ONE A2 orchestrator session on a TOY task with ONE delegated leaf
    round-trip: create → result → files merged → verified. A wiring check of
    every bench-side seam (server, sidecar, tool, apply, grading) — NOT a
    measurement, and the orchestrator here runs WITHOUT bwrap (macOS has
    none; measurement runs are Linux-only by design)."""
    print("=== xl-bench WIRING SMOKE — NOT A MEASUREMENT (n=1 toy task, no isolation) ===")
    base_url, api_key = _gateway()
    root = Path(args.smoke_root).resolve()
    if root.exists():
        shutil.rmtree(root)
    (root / "workspaces").mkdir(parents=True)
    (root / "runs").mkdir(parents=True)
    # The toy hidden suite (kept OUTSIDE the orchestrator workspace, like a
    # real fixture's): written before the run, used for the post-hoc grade.
    toy_hidden = root / "toy-hidden"
    toy_hidden.mkdir(parents=True)
    (toy_hidden / "test_toy.py").write_text(TOY_TASK["files"]["test_toy.py"], encoding="utf-8")
    token = secrets.token_hex(16)
    server_env = {
        **os.environ,
        "ACI_DATABASE_URL": args.db_url,
        "ACI_EMBEDDER": args.embedder,
        "ACI_OBJECT_STORE_ROOT": args.object_store_root,
        "ACI_AGENT_MODEL_BASE_URL": base_url,
        "ACI_AGENT_MODEL_API_KEY": api_key,
        "ACI_AGENT_MODEL_ID": args.model,
        "ACI_AGENT_WORKSPACE_ROOT": str(root / "workspaces"),
        "ACI_AGENT_RUNS_ROOT": str(root / "runs"),
        "ACI_AGENT_PROCESS_PREFIXES": json.dumps(["python -m pytest"]),
        "ACI_AGENT_RUNS_TOKEN": token,
    }
    server = subprocess.Popen(
        [
            str(Path(args.venv) / "bin" / "python"),
            "-m",
            "uvicorn",
            "aci.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.aci_port),
        ],
        cwd=ROOT,
        env=server_env,
        stdout=(root / "aci-server.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    sidecar = DelegationSidecar(
        workspace_root=root / "workspaces",
        runs_root=root / "runs",
        aci=HttpAciClient(f"http://127.0.0.1:{args.aci_port}", token=token),
    )
    httpd = serve_sidecar(sidecar, "127.0.0.1", args.sidecar_port)
    sidecar_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"ACI server :{args.aci_port} (pid {server.pid}), sidecar {sidecar_url}")
    oc_root: Path | None = None
    try:
        import httpx

        for _ in range(120):
            try:
                ready = httpx.get(
                    f"http://127.0.0.1:{args.aci_port}/ready", timeout=2.0
                ).status_code
                if ready == 200:
                    break
                time.sleep(1.0)
            except httpx.HTTPError:
                time.sleep(1.0)
        else:
            print("ACI server never became ready (see the log under the smoke root)")
            return 1
        oc_root = root / "orchestrator"
        work = oc_root / "work"
        work.mkdir(parents=True)
        for rel, content in TOY_TASK["files"].items():
            (work / rel).write_text(content, encoding="utf-8")
        template = ensure_oc_template(root / "oc-template")
        shutil.copytree(template, work / ".opencode")
        _write_oc_config(oc_root, base_url, api_key)
        env = _opencode_env(oc_root, work, sidecar_url, "xl-bench:smoke:a2")
        stream = oc_root / "stream.jsonl"
        prompt = A2_PLAYBOOK + "\n\nTASK:\n\n" + TOY_TASK["prompt"]
        print("orchestrator: opencode run (UNSANDBOXED on this Mac — the smoke is not a round)")
        started = time.time()
        with stream.open("w") as out_fh:
            subprocess.run(
                [str(OPENCODE), "run", "--standalone", "--auto", "--format=json", prompt],
                env=env,
                cwd=work,
                stdout=out_fh,
                stderr=subprocess.STDOUT,
                timeout=args.wall_budget,
                check=False,
            )
        wall = round(time.time() - started, 1)
        parsed = parse_opencode_stream(stream)
        leaves = sidecar.handle_leaves("xl-bench:smoke:a2")["leaves"]
        print(
            f"orchestrator wall={wall}s tokens_in={parsed['tokens_in']} "
            f"tokens_out={parsed['tokens_out']} tool_uses={parsed['tool_uses']}"
        )
        print(f"leaves: {json.dumps(leaves, indent=1)}")
        # The round-trip evidence: the merged workspace must now pass the toy
        # suite OUTSIDE any sandbox (the leaf's own verification ran inside
        # the kernel's Seatbelt sandbox — the default on this Mac).
        grade = hidden_suite_result(
            work, toy_hidden, venv_python=Path(args.venv) / "bin" / "python"
        )
        print(f"merged-workspace grade: {json.dumps(grade)}")
        usage: dict[str, Any] = {}
        run_ids = [leaf["run_id"] for leaf in leaves if leaf["run_id"]]
        if run_ids:
            usage = kernel_usage(args.db_url, run_ids)  # READ granted (aci_e2b)
        print(f"kernel usage (agent_runs.usage, FINDING #2): {json.dumps(usage)}")
        ok = bool(leaves) and grade["pass_fraction"] == 1.0
        print(
            f"SMOKE {'OK' if ok else 'FAILED'}: leaf round-trip "
            f"{'shown' if leaves else 'MISSING'}, merged workspace "
            f"{'green' if grade['pass_fraction'] == 1.0 else 'RED'}"
        )
        return 0 if ok else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        httpd.shutdown()
        # The orchestrator config holds the gateway key (rc_bench pattern):
        # remove it from the kept smoke root; everything else stays as evidence.
        if oc_root is not None:
            (oc_root / "xdg-config/opencode/opencode.json").unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="command", required=True)

    sidecar_p = subs.add_parser("sidecar", help="run the delegation sidecar")
    sidecar_p.add_argument("--port", type=int, default=8770)
    sidecar_p.add_argument("--aci", default="http://127.0.0.1:8020")
    sidecar_p.add_argument("--aci-token", default="")
    sidecar_p.add_argument("--workspace-root", required=True)
    sidecar_p.add_argument("--runs-root", required=True)

    verify_p = subs.add_parser("verify-fixture", help="fail-as-shipped/pass-when-solved check")
    verify_p.add_argument("fixture")
    verify_p.add_argument("--venv-python", default=str(ROOT / ".venv/bin/python"))

    round_p = subs.add_parser("round", help="the pre-registered round (LINUX host only)")
    round_p.add_argument("--fixtures", required=True, help="comma-separated fixture dirs")
    round_p.add_argument("--arms", default="A1,A2,A3")
    round_p.add_argument("--repeat", type=int, default=3)
    round_p.add_argument("--perturbation", default="both", choices=["both", "change", "nochange"])
    round_p.add_argument("--wall-budget", type=float, default=5400.0)
    round_p.add_argument("--out", default="data/xl-bench/round.json")
    round_p.add_argument("--bench-root", default="/tmp/aci-xl-bench")
    round_p.add_argument("--db-url", required=True)
    round_p.add_argument("--embedder", default="fastembed")
    round_p.add_argument("--model", default=MODEL)
    round_p.add_argument("--venv", default=str(ROOT / ".venv"))
    round_p.add_argument("--aci-port", type=int, default=8020)
    round_p.add_argument("--sidecar-port", type=int, default=8770)
    round_p.add_argument("--prefixes", nargs="+", default=["python -m pytest"])

    smoke_p = subs.add_parser("smoke", help="Mac wiring smoke (NOT a measurement)")
    smoke_p.add_argument("--smoke-root", default="/tmp/aci-xl-smoke")
    smoke_p.add_argument(
        "--db-url", default="postgresql+psycopg://aci:aci@100.126.242.46:5432/aci_e2b"
    )
    smoke_p.add_argument("--embedder", default="fastembed")
    smoke_p.add_argument("--object-store-root", default=str(Path.home() / "aci-mac/objects"))
    smoke_p.add_argument("--model", default=MODEL)
    smoke_p.add_argument("--venv", default=str(ROOT / ".venv"))
    smoke_p.add_argument("--aci-port", type=int, default=8020)
    smoke_p.add_argument("--sidecar-port", type=int, default=8770)
    smoke_p.add_argument("--wall-budget", type=float, default=900.0)

    args = parser.parse_args(argv)
    if args.command == "sidecar":
        sidecar = DelegationSidecar(
            workspace_root=Path(args.workspace_root),
            runs_root=Path(args.runs_root),
            aci=HttpAciClient(args.aci, token=args.aci_token),
        )
        httpd = serve_sidecar(sidecar, "127.0.0.1", args.port)
        print(f"sidecar on 127.0.0.1:{httpd.server_address[1]} (Ctrl-C to stop)")
        try:
            while True:
                time.sleep(3600.0)
        except KeyboardInterrupt:
            httpd.shutdown()
        return 0
    if args.command == "verify-fixture":
        return verify_fixture(Path(args.fixture), Path(args.venv_python))
    if args.command == "round":
        return run_round(args)
    return run_smoke(args)


if __name__ == "__main__":
    raise SystemExit(main())
