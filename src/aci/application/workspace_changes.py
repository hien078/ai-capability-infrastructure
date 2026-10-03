"""Workspace change read model — the shared platform-surface contract
(2026-10-03; xl-harness FINDINGS 1-2): what a delegated run changed on disk.

``GET /v1/agent-runs/{run_id}`` (and the POST response, which returns the
terminal result) project — besides the RunResult — the run's FILE CHANGES:
the per-file sha256 manifest taken when the run's working copy was
provisioned (byte-identical to the source workspace at run start) diffed
against the working copy the product already keeps
(``<runs_root>/<run_id>``).

Design boundaries (load-bearing, pinned in tests/security/):

- The manifest is persisted OUTSIDE the working copy, under
  ``<runs_root>/.aci-run-manifests/<run_id>.json``: the model holds write
  grants over the whole workspace, so a manifest living inside it could be
  rewritten to hide the run's own changes. A sibling of the run dirs is
  invisible to every run (grants bind the run dir only) and shares the
  runs root's lifecycle.
- The manifest is hashes-only, so the FILE LIST (added / modified /
  deleted, sha256 before/after, sizes) is stable after the source workspace
  changes — computing it never reads the source.
- The diff TEXT for modified/deleted files needs the start bytes; those are
  read from the copy source (the client workspace, or the parent run dir
  for a revision) ONLY while the file's current sha256 still matches the
  manifest — proof the source still holds the run's start state, never
  trust — and the hunk is withheld (markered) otherwise. Added files carry
  no before state and always diff fully.
- Noise (caches, VCS metadata, installed environments — the same set
  provisioning refuses to copy) and kernel infrastructure files (the
  Seatbelt profile) are excluded on BOTH sides: never task output.
- Secrets: the run's own scrub (the ``SecretLeakGuard`` patterns, §14) is
  applied to every hunk — redactable patterns get the guard's exact
  ``[REDACTED:<label>]`` replacement; a private key block (never redactable
  per the guard) withholds the whole hunk. The file stays listed with
  hashes only.
- Paths are workspace-relative POSIX, never absolute, never ``..``.
"""

import difflib
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from aci.runtime.guardrails import SecretLeakGuard
from aci.runtime.workspace_tools import PROVISION_IGNORE

#: Kernel infrastructure that appears IN a run workspace (the macOS
#: Seatbelt profile, src/aci/runtime/sandbox.py): never task output,
#: excluded from the manifest and the diff alike. Keep in sync with the
#: sandbox module's profile name.
KERNEL_INFRA_FILES: frozenset[str] = frozenset({".aci-sandbox-profile.sb"})
#: Per-run start manifests live here — a SIBLING of the run dirs under the
#: runs root (see the module docstring for why never inside a run dir).
MANIFEST_DIRNAME = ".aci-run-manifests"
MANIFEST_VERSION = 1
#: The shared read model's diff cap: ``truncated: true`` past it.
MAX_DIFF_BYTES = 200 * 1024

ChangeStatus = Literal["added", "modified", "deleted"]


class FileChange(BaseModel):
    """One changed file — a workspace-relative POSIX path, never absolute."""

    model_config = {"frozen": True}

    path: str
    status: ChangeStatus
    sha256_before: str | None = None
    sha256_after: str | None = None
    size_after: int = Field(default=0, ge=0)


class WorkspaceChanges(BaseModel):
    """The ``changes`` field of the agent-run read model: the file list is
    manifest-stable; ``diff`` is unified text for text files only, capped
    at ``MAX_DIFF_BYTES`` (``truncated`` says which)."""

    model_config = {"frozen": True}

    files: list[FileChange] = Field(default_factory=list)
    diff: str = ""
    truncated: bool = False


@dataclass(frozen=True)
class StartManifest:
    """A run's persisted start state: the copy source path (SERVER-side
    only, re-validated at read time) and the per-file sha256 of the
    working copy exactly as provisioned."""

    source: str | None
    files: dict[str, str]


# -- tree snapshot ------------------------------------------------------------


def _excluded(rel: Path) -> bool:
    """Noise dirs (any depth), installed environments, kernel infra files."""
    return bool(PROVISION_IGNORE & set(rel.parts)) or rel.name in KERNEL_INFRA_FILES


def snapshot_tree(root: Path) -> dict[str, str]:
    """Per-file sha256 of the tree under ``root``: workspace-relative POSIX
    paths; noise dirs, kernel infrastructure files and symlinks excluded
    (a symlink is not content; provisioning already dropped escaping ones)."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if _excluded(rel):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# -- manifest persistence -----------------------------------------------------


def manifest_path(runs_root: Path, run_id: str) -> Path:
    return Path(runs_root) / MANIFEST_DIRNAME / f"{run_id}.json"


def write_start_manifest(runs_root: Path, run_id: str, *, source: Path, run_dir: Path) -> None:
    """Persist the run-start manifest: hashes of the freshly provisioned
    working copy plus the copy source path. Atomic (temp + rename) so a
    crash never leaves a half-written manifest behind."""
    payload = json.dumps(
        {
            "version": MANIFEST_VERSION,
            "source": str(source.resolve()),
            "files": snapshot_tree(run_dir),
        }
    )
    target = manifest_path(runs_root, run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f"{target.name}.tmp"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, target)


def read_start_manifest(runs_root: Path, run_id: str) -> StartManifest | None:
    """The run's start manifest, or None when none was written (a run from
    before this change, or the write failed) or it is unreadable — changes
    are then honestly absent, never guessed."""
    try:
        raw = json.loads(manifest_path(runs_root, run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != MANIFEST_VERSION:
        return None
    source = raw.get("source")
    files = raw.get("files")
    if source is not None and not isinstance(source, str):
        return None
    if not isinstance(files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in files.items()
    ):
        return None
    return StartManifest(source=source, files=dict(files))


# -- the diff -----------------------------------------------------------------


def _safe_rel(rel: str) -> bool:
    """A manifest key must stay a plain workspace-relative POSIX path —
    defense in depth before it is ever joined onto a directory."""
    if not rel or rel.startswith("/") or "\\" in rel or "\x00" in rel:
        return False
    return all(part not in ("", ".", "..") for part in rel.split("/"))


def _start_bytes(source: Path | None, rel: str, sha: str) -> bytes | None:
    """The file's START bytes from the copy source — only while the source
    still holds exactly them (its current sha256 matches the manifest):
    proof, not trust. None = the start state is not recoverable there."""
    if source is None or not _safe_rel(rel):
        return None
    try:
        data = (source / rel).read_bytes()
    except OSError:
        return None
    return data if hashlib.sha256(data).hexdigest() == sha else None


def redact(text: str) -> str:
    """The run's own scrub (``SecretLeakGuard`` patterns, §14) applied to
    diff text: redactable patterns get the guard's exact replacement; a
    NON-redactable one (a private key block — the guard BLOCKS those
    outright) returns ``""`` so the caller withholds the whole hunk."""
    for label, pattern, redactable in SecretLeakGuard.PATTERNS:
        if not redactable and pattern.search(text):
            return ""
        if redactable:
            text = pattern.sub(f"[REDACTED:{label}]", text)
    return text


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _hunk(rel: str, before: bytes | None, after: bytes) -> str:
    """One file's unified-diff hunk — or its honest marker when the start
    bytes are gone (source changed since run start) or the file is binary."""
    if before is None:
        return (
            f"--- a/{rel}\n+++ b/{rel}\n"
            "[diff unavailable: the source workspace changed since run start]\n"
        )
    old_text, new_text = _decode(before), _decode(after)
    if old_text is None or new_text is None:
        return f"--- a/{rel}\n+++ b/{rel}\n[binary file changed]\n"
    hunk = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
    scrubbed = redact(hunk)
    if not scrubbed:
        return f"--- a/{rel}\n+++ b/{rel}\n[diff withheld: secret pattern (private key block)]\n"
    return scrubbed


def compute_changes(
    manifest: dict[str, str],
    run_dir: Path,
    *,
    source: Path | None = None,
    max_diff_bytes: int = MAX_DIFF_BYTES,
) -> WorkspaceChanges:
    """Diff a run's working copy against its start manifest → the shared
    read model. The FILE LIST is manifest-stable (never reads the source);
    diff TEXT for modified/deleted files uses source bytes only under the
    manifest's hash proof (see the module docstring). Never mutates
    anything — a pure read of the working copy and, hash-verified, the
    copy source."""
    after = snapshot_tree(run_dir)
    status_of: dict[str, ChangeStatus] = {}
    for rel in set(after) - set(manifest):
        status_of[rel] = "added"
    for rel in set(manifest) - set(after):
        status_of[rel] = "deleted"
    for rel in set(manifest) & set(after):
        if manifest[rel] != after[rel]:
            status_of[rel] = "modified"

    files: list[FileChange] = []
    diff_parts: list[str] = []
    truncated = False
    total = 0
    for rel in sorted(status_of):
        status = status_of[rel]
        sha_after = after.get(rel)
        size_after = 0
        if sha_after is not None and _safe_rel(rel):
            try:
                size_after = (run_dir / rel).stat().st_size
            except OSError:
                size_after = 0
        files.append(
            FileChange(
                path=rel,
                status=status,
                sha256_before=manifest.get(rel),
                sha256_after=sha_after,
                size_after=size_after,
            )
        )
        if truncated:
            continue
        if status == "added":
            before: bytes | None = b""
        else:  # modified / deleted: start bytes under hash proof only
            before = _start_bytes(source, rel, manifest[rel])
        after_bytes = b"" if status == "deleted" else _read_after(run_dir, rel)
        if after_bytes is None:
            continue  # unreadable now: listed, no hunk
        hunk = _hunk(rel, before, after_bytes)
        if total + len(hunk.encode("utf-8")) > max_diff_bytes:
            truncated = True
            continue
        diff_parts.append(hunk)
        total += len(hunk.encode("utf-8"))
    diff = "".join(diff_parts)
    if truncated:
        diff += f"\n… [diff truncated at {max_diff_bytes} bytes]\n"
    return WorkspaceChanges(files=files, diff=diff, truncated=truncated)


def _read_after(run_dir: Path, rel: str) -> bytes | None:
    if not _safe_rel(rel):
        return None
    try:
        return (run_dir / rel).read_bytes()
    except OSError:
        return None
