"""FsObjectStore content-addressed invariant: key == sha256(content)."""

import hashlib
import os
from pathlib import Path

import pytest

from aci.adapters.outbound.object_store.fs import FsObjectStore
from aci.domain.capability.errors import DomainError, ErrorCode


def _key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def test_put_rejects_mismatched_data_and_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    store = FsObjectStore(root)
    key = _key(b"real content")
    with pytest.raises(DomainError) as exc:
        store.put(key, b"forged content")
    assert exc.value.code == ErrorCode.ARTIFACT_INTEGRITY_ERROR
    assert str(root) not in str(exc.value)  # no server paths in messages
    assert not store.exists(key)
    assert _files(root) == []


def test_put_repairs_corrupt_existing_object(tmp_path: Path) -> None:
    store = FsObjectStore(tmp_path / "objects")
    data = b"good bytes\n"
    key = _key(data)
    path = tmp_path / "objects" / key[:2] / key
    path.parent.mkdir(parents=True)
    path.write_bytes(b"poisoned\n")
    store.put(key, data)
    assert store.get(key) == data
    assert _files(tmp_path / "objects") == [path]  # no stray temp files


def test_put_repairs_zero_byte_object(tmp_path: Path) -> None:
    store = FsObjectStore(tmp_path / "objects")
    data = b"payload"
    key = _key(data)
    path = tmp_path / "objects" / key[:2] / key
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    store.put(key, data)
    assert path.read_bytes() == data


def test_idempotent_put_is_a_no_op(tmp_path: Path) -> None:
    store = FsObjectStore(tmp_path / "objects")
    data = b"same bytes"
    key = _key(data)
    store.put(key, data)
    path = tmp_path / "objects" / key[:2] / key
    before = os.stat(path)
    store.put(key, data)
    after = os.stat(path)
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    assert store.get(key) == data


def test_get_returns_raw_bytes_for_caller_verification(tmp_path: Path) -> None:
    """get() is unverified by design: readers re-hash, and ingestion's
    repair path needs to observe the corrupt bytes to replace them."""
    store = FsObjectStore(tmp_path / "objects")
    data = b"original"
    key = _key(data)
    store.put(key, data)
    (tmp_path / "objects" / key[:2] / key).write_bytes(b"tampered")
    stored = store.get(key)
    assert stored == b"tampered"
    assert _key(stored) != key


def test_bad_keys_still_raise_value_error(tmp_path: Path) -> None:
    store = FsObjectStore(tmp_path / "objects")
    with pytest.raises(ValueError):
        store.put("../escape", b"x")
    with pytest.raises(ValueError):
        store.put(_key(b"x").upper(), b"x")
