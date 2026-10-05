#!/usr/bin/env python3
"""Deliver ACI registry skills into a client's NATIVE skill directory.

rc-bench (ADR-014 amendments 30/34) measured that OpenCode's own native
skill selection is excellent — ACI's job for such clients is to be the
governed SOURCE and keep the native directories in sync. This tool pulls
the OpenCode catalog projection of an ACI server over plain HTTP (never
touching a database) and materializes it as local skill dirs:

    <target>/<name>/SKILL.md        <- the catalog's `<name>.md` entry file
    <target>/<name>/<other files>   <- at their catalog paths

The catalog serves the entry as `<name>.md` (OpenCode's HTTP skill-ID
contract, ADR-005); local dirs are source ROOTS, where the entry must be
`SKILL.md` again (verified against the OpenCode V2 docs 2026-10-03,
§77: `~/.config/opencode/skills/<name>/SKILL.md`, `.claude/skills/…`).

Targets (`--client`):
    opencode      ~/.config/opencode/skills/   (--project DIR -> DIR/.opencode/skills/)
    claude-code   ~/.claude/skills/           (--project DIR -> DIR/.claude/skills/)
    goose         ~/.agents/skills/           (--project DIR -> DIR/.agents/skills/)
    antigravity   ~/.gemini/config/skills/    (--project DIR -> DIR/.agents/skills/)
    dir           any --target DIR

Goose 1.52 reads `~/.agents/skills/` and `~/.claude/skills/` (global) and
`.agents/skills/` (project; per its bundled help text) — `~/.agents/skills`
is used so a goose sync never shares a dir/lockfile with claude-code.
Antigravity's global customization root is `~/.gemini/config/`, its
workspace root `.agents/` (bundled agy-customizations docs, 2026-10-05);
skills live at `<root>/skills/<name>/SKILL.md`. NOTE: `--project` for goose
and antigravity resolves to the SAME dir (`DIR/.agents/skills/`) — one
lockfile, one set of managed skills, visible to both clients.

Safety contract (tests/security/test_aci_sync_safety.py pins it):

    * Only ACI-MANAGED skills — those recorded in `<target>/.aci-sync.lock.json`
      — are ever updated or pruned. A dir that is not in the lockfile is
      NEVER touched, and a served skill colliding with one is rejected.
    * Served paths must be relative, POSIX, no `..`/absolute/backslash;
      unsafe skills are rejected whole (never written).
    * Per-skill replace is atomic: bytes land in a temp dir inside the
      target, then one rename swaps it in (the old dir is moved aside and
      deleted only after the swap; a crash leaves a `.aci-sync-old-*` dir,
      cleaned at the next sync start).
    * The lockfile is written via temp + os.replace and refused when it
      is a symlink; a managed dir that became a symlink is replaced, not
      written through.
    * `--dry-run` writes nothing at all (no skill dirs, no lockfile).

What the catalog provides: `GET /opencode/skills/index.json` today serves
`{"skills": [{"name", "version", "files": [path, ...]}]}` — names and
versions only, NO per-file digests. aci_sync therefore records the sha256
it computes from the downloaded bytes into the lockfile (drift/tamper
detection on later syncs) and, if a future index carries per-file
`{"path", "sha256"}` entries, verifies every byte against them before
writing.

Idempotence: a skill whose (version, file list) matches the lockfile is
not re-downloaded (registry versions are immutable, §6) — the second run
is a no-op that does not even rewrite the lockfile. `--verify` forces a
re-download + digest comparison. A skill present in the lockfile but
absent from the index (revoked/removed server-side) is pruned; skills
filtered out by --include/--exclude/--only-ids are left untouched.

Usage:
    ACI_API_TOKEN=<bearer> python scripts/aci_sync.py \\
        --server http://aci.internal:8000 --client opencode [--dry-run]

Exit codes: 0 = clean (possibly a no-op), 1 = errors (rejected skills,
HTTP failures, lockfile problems — the rest of the plan still ran),
2 = bad usage.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

LOCK_NAME = ".aci-sync.lock.json"
LOCK_VERSION = 1
TMP_PREFIX = ".aci-sync-tmp-"
OLD_PREFIX = ".aci-sync-old-"
LOCK_TMP_PREFIX = f".{LOCK_NAME}."
DEFAULT_SERVER = "http://127.0.0.1:8000"
CATALOG_PATH = "/opencode/skills"
DEFAULT_TIMEOUT = 30.0
#: Per-file download cap, enforced while streaming: a hostile or
#: misconfigured server must not be able to buffer gigabytes in memory.
MAX_FILE_BYTES = 64 * 1024 * 1024

#: A skill name is ONE path component (it becomes a directory).
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
#: One component of a served file path (mirrors the server's catalog rule,
#: src/aci/adapters/inbound/opencode/catalog.py `_SAFE_PATH`).
_PART_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
#: OpenCode/Claude native skill-name rule (docs, §77 2026-10-03): lowercase
#: alphanumerics with single hyphens. A name outside it is still synced but
#: will NOT be discovered by the client — we warn.
CLIENT_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: client -> (global default dir, project-relative dir)
CLIENTS: dict[str, tuple[str, str]] = {
    "opencode": ("~/.config/opencode/skills", ".opencode/skills"),
    "claude-code": ("~/.claude/skills", ".claude/skills"),
    "goose": ("~/.agents/skills", ".agents/skills"),
    "antigravity": ("~/.gemini/config/skills", ".agents/skills"),
}

_ACTION_SYMBOL = {
    "create": "+",
    "update": "~",
    "unchanged": "=",
    "prune": "-",
    "skip": ".",
    "reject": "x",
}


class SyncError(Exception):
    """Fatal: the run cannot continue (index, lockfile, target, usage)."""


class SkillRejected(Exception):
    """Per-skill: this skill is not synced; the rest of the plan continues."""


@dataclass
class SyncOptions:
    server: str = DEFAULT_SERVER
    client: str = "opencode"
    project: str | None = None
    target: str | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    only_ids: tuple[str, ...] = ()
    dry_run: bool = False
    verify: bool = False
    force: bool = False
    timeout: float = DEFAULT_TIMEOUT
    #: Bearer token. main() reads ACI_API_TOKEN; callers/tests inject directly.
    #: Never printed or logged (the security suite pins that).
    token: str = ""


@dataclass
class IndexSkill:
    name: str
    version: str
    #: catalog path -> sha256 from the index, None when not provided.
    digests: dict[str, str | None] = field(default_factory=dict)

    @property
    def has_index_digests(self) -> bool:
        return any(d is not None for d in self.digests.values())


@dataclass
class Row:
    name: str
    action: str  # create|update|unchanged|prune|skip|reject
    detail: str = ""


# ---------------------------------------------------------------- paths ----


def _safe_name(name: str) -> bool:
    """A skill name is one safe path component (never `.`, `..`, hidden)."""
    return bool(name) and bool(_NAME_RE.match(name)) and not name.startswith(".")


def _safe_rel(path: str) -> bool:
    """A served/lockfile path: relative, POSIX, no traversal, no empties."""
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        return False
    return all(_PART_RE.match(part) for part in path.split("/"))


def _local_path(name: str, catalog_path: str) -> str:
    """Catalog namespace -> client-native namespace.

    The catalog renamed the entry `SKILL.md` to `<name>.md` (OpenCode's
    HTTP skill-ID contract); a local dir is a source ROOT, where the entry
    must be `SKILL.md` again. Every other file keeps its catalog path.
    """
    return "SKILL.md" if catalog_path == f"{name}.md" else catalog_path


def _resolve_target(opts: SyncOptions) -> Path:
    if opts.client == "dir":
        if opts.project:
            raise SyncError("--client dir does not support --project; pass --target DIR")
        if not opts.target:
            raise SyncError("--client dir requires --target DIR")
        return Path(opts.target).expanduser()
    if opts.client not in CLIENTS:
        raise SyncError(f"unknown client {opts.client!r}")
    if opts.target and opts.project:
        raise SyncError("pass either --target or --project, not both")
    if opts.target:
        return Path(opts.target).expanduser()
    if opts.project:
        return Path(opts.project).expanduser() / CLIENTS[opts.client][1]
    return Path(CLIENTS[opts.client][0]).expanduser()


def _selected(name: str, opts: SyncOptions) -> bool:
    """--only-ids intersects; --include keeps on ANY match; --exclude drops."""
    if opts.only_ids and name not in opts.only_ids:
        return False
    if opts.include and not any(fnmatch.fnmatchcase(name, pat) for pat in opts.include):
        return False
    if opts.exclude and any(fnmatch.fnmatchcase(name, pat) for pat in opts.exclude):
        return False
    return True


# ------------------------------------------------------------------ HTTP ----


def _get_index(client: httpx.Client, base: str) -> dict[str, Any]:
    url = f"{base}/index.json"
    try:
        response = client.get(url)
    except httpx.TransportError as exc:
        raise SyncError(f"cannot reach {url}: {type(exc).__name__}") from exc
    if response.status_code == 401:
        raise SyncError(f"{url}: 401 Unauthorized — set ACI_API_TOKEN (bearer) and retry")
    if response.status_code == 404:
        raise SyncError(f"{url}: 404 — is the --server an ACI instance with the catalog mounted?")
    if 300 <= response.status_code < 400:
        raise SyncError(f"{url}: unexpected redirect ({response.status_code}); refusing to follow")
    if response.status_code != 200:
        raise SyncError(f"{url}: HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SyncError(f"{url}: index is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
        raise SyncError(f'{url}: index is not {{"skills": [...]}} — not an ACI catalog?')
    return payload


def _parse_index(payload: dict[str, Any]) -> tuple[list[IndexSkill], list[tuple[str, str]]]:
    """Validate every entry up front; malformed/unsafe entries become
    (name, reason) rejections instead of aborting the whole sync."""
    skills: list[IndexSkill] = []
    rejected: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _reject(name: Any, reason: str) -> None:
        rejected.append((name if isinstance(name, str) else "<unnamed>", reason))

    for raw in payload["skills"]:
        if not isinstance(raw, dict):
            _reject(raw, "index entry is not an object")
            continue
        name, version = raw.get("name"), raw.get("version")
        if not isinstance(name, str) or not _safe_name(name):
            _reject(name, f"unsafe skill name {name!r}")
            continue
        if name in seen:
            _reject(name, "duplicate index entry")
            continue
        if not isinstance(version, str) or not version:
            _reject(name, "missing/invalid version")
            continue
        seen.add(name)
        try:
            digests = _parse_files(raw.get("files"))
        except SkillRejected as exc:
            _reject(name, str(exc))
            continue
        skills.append(IndexSkill(name=name, version=version, digests=digests))
    return skills, rejected


def _parse_files(raw_files: Any) -> dict[str, str | None]:
    """`files` today is a list of path strings (no digests — see the module
    docstring); a future index may carry `{"path", "sha256"}` objects. Both
    shapes parse; digests are verified against the downloaded bytes."""
    if isinstance(raw_files, str):
        raw_files = [raw_files]  # tolerate a single string
    if not isinstance(raw_files, list):
        raise SkillRejected("'files' is not a list")
    digests: dict[str, str | None] = {}
    for entry in raw_files:
        if isinstance(entry, str):
            path, digest = entry, None
        elif isinstance(entry, dict):
            raw_path = entry.get("path")
            raw_digest = entry.get("sha256")
            if not isinstance(raw_path, str) or (
                raw_digest is not None and not isinstance(raw_digest, str)
            ):
                raise SkillRejected("unsupported file entry shape (want path/sha256)")
            path, digest = raw_path, raw_digest
        else:
            raise SkillRejected("unsupported file entry shape")
        if not _safe_rel(path):
            raise SkillRejected(f"unsafe served path {path!r}")
        if path in digests:
            raise SkillRejected(f"duplicate served path {path!r}")
        digests[path] = digest
    return digests


def _fetch_file(client: httpx.Client, url: str) -> bytes:
    """Stream one file with a hard size cap; per-skill failures reject."""
    try:
        with client.stream("GET", url) as response:
            if 300 <= response.status_code < 400:
                raise SkillRejected(f"unexpected redirect ({response.status_code}) for {url}")
            if response.status_code != 200:
                raise SkillRejected(f"HTTP {response.status_code} for {url}")
            buffer = bytearray()
            for chunk in response.iter_bytes():
                buffer += chunk
                if len(buffer) > MAX_FILE_BYTES:
                    raise SkillRejected(f"file over the {MAX_FILE_BYTES // (1 << 20)} MiB cap")
            return bytes(buffer)
    except httpx.TransportError as exc:
        raise SkillRejected(f"transport error: {type(exc).__name__}") from exc


def _fetch_skill(client: httpx.Client, base: str, skill: IndexSkill) -> dict[str, bytes]:
    """Download every served file -> {local path: bytes}, verifying index
    digests where the catalog provides them."""
    files: dict[str, bytes] = {}
    for catalog_path, digest in skill.digests.items():
        data = _fetch_file(client, f"{base}/{skill.name}/{catalog_path}")
        if digest is not None and hashlib.sha256(data).hexdigest() != digest:
            raise SkillRejected(f"sha256 mismatch for {catalog_path!r} (index digest)")
        local = _local_path(skill.name, catalog_path)
        if local in files:
            raise SkillRejected(f"two served files map to one local path: {local!r}")
        files[local] = data
    return files


# -------------------------------------------------------------- lockfile ----


def _load_lock(target: Path) -> dict[str, Any]:
    path = target / LOCK_NAME
    if path.is_symlink():
        raise SyncError(f"lockfile {path} is a symlink — refusing; remove it and re-sync")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SyncError(f"cannot read lockfile {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != LOCK_VERSION:
        raise SyncError(
            f"lockfile {path} is not aci-sync v{LOCK_VERSION} — inspect/remove it manually"
        )
    skills = data.get("skills")
    if not isinstance(skills, dict):
        raise SyncError(f"lockfile {path}: 'skills' is not a mapping")
    for name, entry in skills.items():
        if not _safe_name(name) or not isinstance(entry, dict):
            raise SyncError(f"lockfile {path}: bad skill entry {name!r}")
        if not isinstance(entry.get("version"), str) or not isinstance(entry.get("files"), dict):
            raise SyncError(f"lockfile {path}: bad entry shape for {name!r}")
        for file_path in entry["files"]:
            if not _safe_rel(file_path):
                raise SyncError(f"lockfile {path}: unsafe path {file_path!r} for {name!r}")
    return data


def _write_lock(target: Path, lock: dict[str, Any]) -> None:
    """Atomic lockfile write: temp file + os.replace (never follows a
    symlink at the destination, never writes through one)."""
    path = target / LOCK_NAME
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise SyncError(f"lockfile path {path} is a symlink/directory — refusing to write it")
    body = json.dumps(lock, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=LOCK_TMP_PREFIX, dir=target)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _dir_matches_lock(skill_dir: Path, files: dict[str, str]) -> bool:
    """True when the on-disk dir is byte-identical to the lockfile entry
    (no extra files, no symlinks). Used to refuse pruning user-modified
    content without --force."""
    if skill_dir.is_symlink() or not skill_dir.is_dir():
        return False
    disk: dict[str, str] = {}
    for root, _dirs, names in os.walk(skill_dir, followlinks=False):
        for file_name in names:
            file_path = Path(root) / file_name
            if file_path.is_symlink():
                return False
            relative = file_path.relative_to(skill_dir).as_posix()
            disk[relative] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return disk == files


# --------------------------------------------------------------- writing ----


def _clean_stale(target: Path) -> int:
    """Remove leftovers from an interrupted run (our reserved namespace:
    `.aci-sync-tmp-*`, `.aci-sync-old-*`, `..aci-sync.lock.json.*`)."""
    removed = 0
    for entry in target.iterdir():
        if not entry.name.startswith((TMP_PREFIX, OLD_PREFIX, LOCK_TMP_PREFIX)):
            continue
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        else:
            shutil.rmtree(entry)
        removed += 1
    return removed


def _replace_dir(target: Path, name: str, tmp: Path) -> None:
    """Atomically move `tmp` onto `<target>/<name>` (same filesystem).

    The existing dir is moved aside first and deleted only after the new
    one is in place; on failure it is moved back. A crash can leave a
    `.aci-sync-old-*` dir (never a half-written skill) — cleaned next run.
    Only ever called for ACI-MANAGED names (present in the lockfile)."""
    final = target / name
    if final.is_symlink() or (final.exists() and not final.is_dir()):
        final.unlink()  # a symlink/file where our managed dir belongs
    if not final.is_dir():
        os.rename(tmp, final)
        return
    old = Path(tempfile.mkdtemp(prefix=f"{OLD_PREFIX}{name}-", dir=target))
    os.rename(final, old)  # replaces the empty mkdtemp dir
    try:
        os.rename(tmp, final)
    except OSError:
        os.rename(old, final)  # roll back
        raise
    shutil.rmtree(old, ignore_errors=True)


def _prune(target: Path, name: str, lock_entry: dict[str, Any], force: bool) -> tuple[bool, str]:
    """Remove one ACI-managed skill dir. Refuses (unless --force) when the
    local content no longer matches the lockfile — probable user edits."""
    skill_dir = target / name
    if skill_dir.is_symlink():
        skill_dir.unlink()
        return True, "removed symlink"
    if not skill_dir.exists():
        return True, "already gone"
    if not skill_dir.is_dir():
        skill_dir.unlink()
        return True, "removed non-dir"
    if not force and not _dir_matches_lock(skill_dir, lock_entry.get("files", {})):
        return False, "local content differs from the lockfile (user edits?) — kept; use --force"
    old = Path(tempfile.mkdtemp(prefix=f"{OLD_PREFIX}{name}-", dir=target))
    os.rename(skill_dir, old)
    shutil.rmtree(old, ignore_errors=True)
    return True, "removed"


# ------------------------------------------------------------------ sync ----


def run_sync(opts: SyncOptions, transport: httpx.BaseTransport | None = None) -> int:
    """One sync run. Returns the process exit code (0 clean / 1 errors).

    Fatal problems (unreachable server, bad lockfile, bad usage) print to
    stderr and return 1 — never a traceback."""
    try:
        return _run_sync(opts, transport)
    except SyncError as exc:
        print(f"aci-sync: error: {exc}", file=sys.stderr)
        return 1


def _run_sync(opts: SyncOptions, transport: httpx.BaseTransport | None) -> int:
    target = _resolve_target(opts)
    server = opts.server.rstrip("/")
    base = f"{server}{CATALOG_PATH}"
    headers = {"Authorization": f"Bearer {opts.token}"} if opts.token else {}
    rows: list[Row] = []
    notes: list[str] = []
    errors = 0

    with httpx.Client(
        headers=headers, timeout=opts.timeout, transport=transport, follow_redirects=False
    ) as client:
        # Fetch the index FIRST: an unreachable/unauthorized server must not
        # touch the filesystem at all (not even create the target dir).
        index_skills, index_rejects = _parse_index(_get_index(client, base))
        if not opts.dry_run:
            target.mkdir(parents=True, exist_ok=True)
            stale = _clean_stale(target)
            if stale:
                notes.append(f"cleaned {stale} leftover temp item(s) from an interrupted run")
        lock = _load_lock(target)
        lock_skills: dict[str, Any] = dict(lock.get("skills", {}))
        if lock and lock.get("server") not in (None, server):
            notes.append(
                f"warning: lockfile was written by server {lock.get('server')!r}, "
                f"syncing from {server!r} — provenance will be overwritten"
            )
        for name, reason in index_rejects:
            rows.append(Row(name, "reject", reason))
            errors += 1
        index_by_name = {skill.name: skill for skill in index_skills}

        for wanted in opts.only_ids:
            if wanted not in index_by_name:
                rows.append(Row(wanted, "reject", "--only-ids skill is not in the index"))
                errors += 1

        selected = [skill for skill in index_skills if _selected(skill.name, opts)]
        selected_names = {skill.name for skill in selected}
        for skill in index_skills:
            if skill.name not in selected_names:
                rows.append(Row(skill.name, "skip", "filtered out"))
        if opts.client in CLIENTS:
            for skill in selected:
                if not CLIENT_NAME_RE.match(skill.name):
                    notes.append(
                        f"warning: {skill.name!r} is outside the client's native name rule "
                        f"({CLIENT_NAME_RE.pattern}); synced, but the client will not discover it"
                    )
        if index_skills:
            if any(skill.has_index_digests for skill in index_skills):
                notes.append("index provides per-file sha256 digests; every byte is verified")
            else:
                notes.append(
                    "index provides no per-file digests; lockfile sha256s are computed "
                    "from the downloaded bytes (registry versions are immutable)"
                )

        # ---- plan: prune (lock minus index), then sync each selected skill
        pruned: set[str] = set()
        for name in sorted(lock_skills):
            if name in index_by_name:
                continue  # revoked check below only for names the index no longer serves
            if opts.dry_run:
                rows.append(Row(name, "prune", "not in the index (revoked/removed)"))
                continue
            try:
                done, detail = _prune(target, name, lock_skills[name], opts.force)
            except OSError as exc:
                done, detail = False, f"prune failed: {type(exc).__name__}"
            rows.append(Row(name, "prune", detail))
            if done:
                pruned.add(name)
            else:
                errors += 1

        new_lock_skills = {name: entry for name, entry in lock_skills.items() if name not in pruned}
        changed = bool(pruned)

        for skill in selected:
            lock_entry = lock_skills.get(skill.name)
            skill_dir = target / skill.name
            if lock_entry is None and (skill_dir.exists() or skill_dir.is_symlink()):
                rows.append(
                    Row(
                        skill.name,
                        "reject",
                        "target dir exists but is not in the lockfile (user content?); refusing",
                    )
                )
                errors += 1
                continue

            local_paths = [_local_path(skill.name, path) for path in skill.digests]
            if not local_paths:
                rows.append(Row(skill.name, "reject", "index entry serves no files"))
                errors += 1
                continue
            if len(set(local_paths)) != len(local_paths):
                rows.append(Row(skill.name, "reject", "two served files map to one local path"))
                errors += 1
                continue

            lock_files: dict[str, str] = dict(lock_entry["files"]) if lock_entry else {}
            same_version = lock_entry is not None and lock_entry["version"] == skill.version
            same_paths = sorted(lock_files) == sorted(local_paths)
            if (
                same_version
                and same_paths
                and skill_dir.is_dir()
                and not skill_dir.is_symlink()
                and not opts.verify
            ):
                rows.append(Row(skill.name, "unchanged", f"v{skill.version}"))
                continue

            try:
                files = _fetch_skill(client, base, skill)
            except SkillRejected as exc:
                rows.append(Row(skill.name, "reject", str(exc)))
                errors += 1
                continue
            if "SKILL.md" not in files:
                notes.append(
                    f"warning: {skill.name} has no entry file (no SKILL.md served) — "
                    "the client will not discover it"
                )

            new_digests = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
            if (
                same_version
                and new_digests == lock_files
                and skill_dir.is_dir()
                and not skill_dir.is_symlink()
                and not opts.verify
            ):
                rows.append(Row(skill.name, "unchanged", f"v{skill.version} (content verified)"))
                continue

            action = "update" if lock_entry is not None else "create"
            if opts.dry_run:
                rows.append(Row(skill.name, action, f"v{skill.version}, {len(files)} file(s)"))
                continue
            tmp = Path(tempfile.mkdtemp(prefix=f"{TMP_PREFIX}{skill.name}-", dir=target))
            try:
                for path, data in files.items():
                    file_path = tmp / path
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_bytes(data)
                _replace_dir(target, skill.name, tmp)
            except OSError as exc:
                shutil.rmtree(tmp, ignore_errors=True)
                rows.append(Row(skill.name, "reject", f"write failed: {type(exc).__name__}"))
                errors += 1
                continue
            new_lock_skills[skill.name] = {"version": skill.version, "files": new_digests}
            changed = True
            rows.append(Row(skill.name, action, f"v{skill.version}, {len(files)} file(s)"))

    # ---- lockfile: written only when something actually changed (or when
    # it does not exist yet / the server moved) — a no-op run leaves every
    # byte alone.
    lock_path = target / LOCK_NAME
    server_changed = bool(lock) and lock.get("server") not in (None, server)
    if not opts.dry_run and (changed or server_changed or not lock_path.is_file()):
        try:
            _write_lock(
                target,
                {
                    "version": LOCK_VERSION,
                    "server": server,
                    "synced_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "skills": new_lock_skills,
                },
            )
        except OSError as exc:
            print(f"aci-sync: error: cannot write lockfile {lock_path}: {exc}", file=sys.stderr)
            return 1

    # ---- report
    counts = Counter(row.action for row in rows)
    print(f"aci-sync: server {server} -> {target} ({len(index_skills)} skill(s) in index)")
    for note in notes:
        print(f"  note: {note}")
    for row in rows:
        print(f"  {_ACTION_SYMBOL.get(row.action, '?')} {row.name}  {row.detail}".rstrip())
    if not rows:
        print("  = nothing to do (no skills in index, none managed)")
    keys = ("create", "update", "unchanged", "prune", "skip", "reject")
    summary = ", ".join(f"{counts.get(key, 0)} {key}" for key in keys)
    print(f"plan: {summary}; lockfile {lock_path}")
    if opts.dry_run:
        print("dry run — nothing written")
    return 1 if errors else 0


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    """CLI entry (also used by tests, which inject a mock transport).
    Reads ACI_API_TOKEN for the bearer."""
    parser = argparse.ArgumentParser(
        prog="aci_sync",
        description="Sync ACI registry skills into a client's native skill directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  ACI_API_TOKEN=<bearer> python scripts/aci_sync.py --client opencode --dry-run\n"
            "  python scripts/aci_sync.py --client claude-code --project ~/myproj\n"
            "  python scripts/aci_sync.py --client goose --server http://aci.internal:8000\n"
            "  python scripts/aci_sync.py --client antigravity --server http://aci.internal:8000\n"
            "  python scripts/aci_sync.py --client dir --target /srv/agent/skills \\\n"
            "      --only-ids debugging\n"
        ),
    )
    parser.add_argument("--server", default=DEFAULT_SERVER, help="ACI base URL")
    parser.add_argument(
        "--client",
        default="opencode",
        choices=[*CLIENTS, "dir"],
        help=(
            "native target layout: opencode (~/.config/opencode/skills), claude-code "
            "(~/.claude/skills), goose (~/.agents/skills), antigravity "
            "(~/.gemini/config/skills), dir (--target)"
        ),
    )
    parser.add_argument(
        "--project",
        help=(
            "sync into the project dir (PROJECT/.opencode/skills, .claude/skills, or "
            ".agents/skills for goose/antigravity) instead of the global dir"
        ),
    )
    parser.add_argument("--target", help="explicit target dir (overrides --project/default)")
    parser.add_argument(
        "--include", action="append", default=[], help="fnmatch glob on names (repeatable)"
    )
    parser.add_argument(
        "--exclude", action="append", default=[], help="fnmatch glob to drop (repeatable)"
    )
    parser.add_argument(
        "--only-ids", action="append", default=[], help="comma-separated skill ids (repeatable)"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    parser.add_argument(
        "--verify", action="store_true", help="re-download and re-hash even 'unchanged' skills"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="prune even when local content drifted from the lockfile",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-request timeout seconds"
    )
    args = parser.parse_args(argv)

    def _ids(values: list[str]) -> tuple[str, ...]:
        out: list[str] = []
        for value in values:
            out.extend(part.strip() for part in value.split(",") if part.strip())
        return tuple(out)

    opts = SyncOptions(
        server=args.server,
        client=args.client,
        project=args.project,
        target=args.target,
        include=tuple(args.include),
        exclude=tuple(args.exclude),
        only_ids=_ids(args.only_ids),
        dry_run=args.dry_run,
        verify=args.verify,
        force=args.force,
        timeout=args.timeout,
        token=os.environ.get("ACI_API_TOKEN", ""),
    )
    return run_sync(opts, transport=transport)


if __name__ == "__main__":
    sys.exit(main())
