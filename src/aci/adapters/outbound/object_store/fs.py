"""Content-addressed filesystem object store (plan §40.3).

Keys are SHA-256 hex digests; layout is `<root>/<key[:2]>/<key>`. Writes are
idempotent: identical content is never rewritten, so ingestion can retry.
`put` enforces key == sha256(data) and repairs a corrupt object in place.
"""

import hashlib
import os
import re
import tempfile
from pathlib import Path

from aci.domain.capability.errors import DomainError, ErrorCode

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class FsObjectStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not _HEX64.match(key):
            raise ValueError(f"object key must be a sha256 hex digest: {key!r}")
        return self._root / key[:2] / key

    def put(self, key: str, data: bytes) -> None:
        """Store `data` under `key`; raise ARTIFACT_INTEGRITY_ERROR unless
        sha256(data) == key. An existing object with the right digest is a
        no-op; a corrupt one is atomically replaced with the verified data."""
        path = self._path(key)
        if hashlib.sha256(data).hexdigest() != key:
            raise DomainError(
                ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                f"object content does not match its key {key}",
            )
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == key:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # write via temp file + fsync + atomic rename so readers never see
        # partial blobs and a power loss never leaves a 0-byte object at a
        # digest key (content-addressed corruption would be permanent).
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name
        Path(tmp_name).replace(path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def get(self, key: str) -> bytes | None:
        """Raw stored bytes, unverified: every reader re-checks the digest
        itself, and ingestion's repair path must see corrupt bytes to fix them."""
        path = self._path(key)
        if not path.exists():
            return None
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()
