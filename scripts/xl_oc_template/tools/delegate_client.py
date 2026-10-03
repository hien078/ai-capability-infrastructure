#!/usr/bin/env python3
"""Bench-side OpenCode custom-tool client for xl-bench delegation (job xl-harness).

This is NOT product code — it is the bench's bridge between the OpenCode
ORCHESTRATOR (which runs inside the run sandbox) and the xl-bench delegation
SIDECAR (which runs on the host next to the ACI server). The OpenCode custom
tool ``delegate_to_aci.ts`` (same directory) is a thin wrapper that spawns
this script with a subcommand and a JSON payload on stdin; this script does
the real work with the Python STDLIB ONLY (it must run on the bare host
python3 inside the run sandbox — no repo, no venv, no network beyond the
sidecar):

* ``delegate`` — snapshot the orchestrator workspace (tar.gz, junk excluded),
  POST it to the sidecar ``/delegate``; the sidecar unpacks it into the ACI
  server's workspace root, starts ``POST /v1/agent-runs`` on it, and returns a
  ``leaf_id`` immediately (the leaf runs asynchronously).
* ``check`` — poll the sidecar ``/check`` for a leaf. When the leaf is
  terminal the sidecar returns the compact run result plus the leaf's changed
  files (it diffs the server's per-run working copy against the snapshot —
  the REST API cannot return file changes, a recorded FINDING). THIS script
  then APPLIES the changed files into the orchestrator workspace (full
  contents, never a patch — no fuzz) and prints the result + diff for the
  orchestrator. A CANCELLED leaf is reported but NOT applied.
* ``cancel`` — ask the sidecar to cancel a leaf (``POST /v1/agent-runs/{id}/
  cancel`` under the hood). Partial changes of a cancelled leaf are never
  merged.

Environment: ``XL_SIDECAR_URL`` (required) — the sidecar base URL;
``XL_RUN_TAG`` (optional) — the bench run tag stamped on every delegation so
the runner can attribute leaves to orchestrator runs.

Why the tool applies (not the orchestrator model): applying is mechanical
work — letting the model re-type patches wastes orchestrator turns/tokens and
invites transcription errors; the deterministic apply is idempotent and
verifiable, and the orchestrator still SEES the diff + changed-file list so
it can verify and re-plan.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

#: Workspace entries never snapshotted (junk, VCS, deps — the leaf needs none).
EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
}
#: Hard cap on one snapshot (defensive; seed workspaces are far smaller).
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
#: Hard cap on the printed diff so one huge leaf cannot eat the context.
MAX_DIFF_CHARS = 12_000


def _sidecar() -> str:
    url = os.environ.get("XL_SIDECAR_URL", "").rstrip("/")
    if not url:
        return json.dumps({"ok": False, "error": "XL_SIDECAR_URL is not set"})
    return url


def _post(url: str, path: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        f"{url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _call(req, timeout)


def _get(url: str, path: str, timeout: float) -> dict:
    return _call(urllib.request.Request(f"{url}{path}", method="GET"), timeout)


def _call(req: urllib.request.Request, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode()[:2000]
        except Exception:  # noqa: BLE001 - best-effort error text
            body = f"HTTP {exc.code}"
        return {"ok": False, "error": f"sidecar HTTP {exc.code}: {body}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"sidecar unreachable: {exc.reason}"}


def snapshot_tar(workspace: Path) -> bytes:
    """tar.gz of every regular file under ``workspace`` (junk excluded)."""
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(workspace.rglob("*")):
            rel = path.relative_to(workspace)
            if EXCLUDE_DIRS & set(rel.parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            info = tar.gettarinfo(str(path), arcname=rel.as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with path.open("rb") as fh:
                tar.addfile(info, fh)
    data = buf.getvalue()
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise SystemExit(
            json.dumps({"ok": False, "error": f"workspace snapshot too large: {len(data)} bytes"})
        )
    return data


def _safe_rel(rel: str) -> PurePosixPath | None:
    """A workspace-relative path the apply step may write; None if unsafe."""
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        return None
    return p


def apply_result(workspace: Path, result: dict) -> list[str]:
    """Apply a terminal leaf's changed files into the orchestrator workspace.

    Full contents (base64) for changed/new files, unlink for deleted ones.
    Returns the list of problems (empty = clean apply)."""
    problems: list[str] = []
    for rel, content_b64 in (result.get("files") or {}).items():
        p = _safe_rel(rel)
        if p is None:
            problems.append(f"unsafe path skipped: {rel}")
            continue
        target = workspace / Path(*p.parts)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(content_b64))
        except OSError as exc:
            problems.append(f"could not write {rel}: {exc}")
    for rel in result.get("deleted_files") or []:
        p = _safe_rel(rel)
        if p is None:
            problems.append(f"unsafe delete skipped: {rel}")
            continue
        target = workspace / Path(*p.parts)
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            problems.append(f"could not delete {rel}: {exc}")
    return problems


def cmd_delegate(workspace: Path, args: dict) -> dict:
    url = _sidecar()
    if not url.startswith("http"):
        return {"ok": False, "error": url}
    try:
        snap = snapshot_tar(workspace)
    except SystemExit as exc:  # the too-large guard above
        return json.loads(str(exc))
    payload = {
        "objective": args["objective"],
        "constraints": args.get("constraints") or [],
        "acceptance_criteria": args.get("acceptance_criteria") or [],
        "verification_command": args.get("verification_command") or None,
        "write_scopes": args.get("write_scopes") or None,
        "max_turns": args.get("max_turns") or 60,
        "snapshot": base64.b64encode(snap).decode(),
        "tag": os.environ.get("XL_RUN_TAG", ""),
    }
    out = _post(url, "/delegate", payload, timeout=120.0)
    if out.get("ok"):
        out["applied"] = False
    return out


def cmd_check(workspace: Path, args: dict) -> dict:
    url = _sidecar()
    if not url.startswith("http"):
        return {"ok": False, "error": url}
    leaf = args["leaf_id"]
    out = _get(url, f"/check?leaf_id={leaf}", timeout=120.0)
    if not out.get("ok"):
        return out
    if out.get("status") in ("running", "delegated"):
        return out
    # Terminal: apply the leaf's files (a cancelled leaf is never applied).
    if out.get("cancelled"):
        out["applied"] = False
        return out
    problems = apply_result(workspace, out)
    out["applied"] = not problems
    out["apply_problems"] = problems
    diff = out.get("diff") or ""
    if len(diff) > MAX_DIFF_CHARS:
        out["diff"] = diff[:MAX_DIFF_CHARS] + f"\n… [diff truncated, {len(diff)} chars total]"
    return out


def cmd_cancel(args: dict) -> dict:
    url = _sidecar()
    if not url.startswith("http"):
        return {"ok": False, "error": url}
    return _post(url, "/cancel", {"leaf_id": args["leaf_id"]}, timeout=60.0)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("delegate", "check", "cancel"):
        print(json.dumps({"ok": False, "error": f"usage: {argv[0]} delegate|check|cancel"}))
        return 2
    args = json.loads(sys.stdin.read() or "{}")
    workspace = Path(args.get("workspace") or ".")
    if argv[1] == "delegate":
        out = cmd_delegate(workspace, args)
    elif argv[1] == "check":
        out = cmd_check(workspace, args)
    else:
        out = cmd_cancel(args)
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
