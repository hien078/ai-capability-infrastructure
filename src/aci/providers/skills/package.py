"""Skill package building: file hashing, structure validation, package digest (plan §20).

Security rules (plan §25): reject symlinks and anything that resolves outside
the source root; cap file count and size. The package digest is a deterministic
hash over the sorted (path, per-file sha256) listing — same content, same digest.
"""

import hashlib
import os
from pathlib import Path

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import ArtifactFile

MAX_FILES = 500
MAX_FILE_BYTES = 10 * 1024 * 1024


def _invalid(detail: str) -> DomainError:
    return DomainError(ErrorCode.SKILL_PACKAGE_INVALID, detail)


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package_digest(files: list[ArtifactFile]) -> str:
    """Deterministic content digest over the sorted file listing."""
    lines = sorted(f"{f.path}\t{f.sha256}" for f in files)
    return hash_bytes("\n".join(lines).encode("utf-8"))


def build_file_list(source: Path) -> list[ArtifactFile]:
    """Snapshot a local skill package directory into hashed file records."""
    if not source.is_dir():
        raise _invalid(f"source is not a directory: {source}")
    root = source.resolve()
    files: list[ArtifactFile] = []
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if path.is_symlink():
            raise _invalid(f"symlink rejected inside skill package: {rel}")
        if path.is_dir():
            continue
        resolved = path.resolve()
        if os.path.commonpath([str(resolved), str(root)]) != str(root):
            raise _invalid(f"path escapes the skill package root: {rel}")
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise _invalid(f"file exceeds {MAX_FILE_BYTES} bytes: {rel}")
        files.append(
            ArtifactFile(path=rel.as_posix(), sha256=hash_bytes(data), size_bytes=len(data))
        )
    if not any(f.path == "SKILL.md" for f in files):
        raise _invalid("skill package must contain SKILL.md at the root")
    if len(files) > MAX_FILES:
        raise _invalid(f"skill package exceeds {MAX_FILES} files")
    return files
