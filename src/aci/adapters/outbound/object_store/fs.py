"""Content-addressed filesystem object store (plan §40.3).

Keys are SHA-256 hex digests; layout is `<root>/<key[:2]>/<key>`. Writes are
idempotent: identical content is never rewritten, so ingestion can retry.
"""

import re
import tempfile
from pathlib import Path

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
        path = self._path(key)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # write via temp file + atomic rename so readers never see partial blobs
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(data)
            tmp_name = tmp.name
        Path(tmp_name).replace(path)

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.exists():
            return None
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()
