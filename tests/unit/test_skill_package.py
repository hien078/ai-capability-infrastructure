"""Unit tests for the skill package provider (plan §52 Phase 3)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from aci.adapters.outbound.object_store.fs import FsObjectStore
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import ArtifactFile
from aci.domain.skills.models import SkillMetadata
from aci.providers.skills.canonicalization import (
    canonical_capability_id,
    derive_version,
    transformations_for,
)
from aci.providers.skills.ingestion import SkillIngestionService
from aci.providers.skills.package import build_file_list, hash_bytes, package_digest
from aci.providers.skills.parser import parse_skill_md

SKILL_MD = """---
name: Systematic Debugging
description: Evidence-driven debugging workflow.
version: 1.2.0
license: MIT
provides:
  - root-cause-analysis
---

# Systematic Debugging

Find the root cause before patching.
"""


def write_skill(root: Path, body: str = SKILL_MD, extra: dict[str, bytes] | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(body, encoding="utf-8")
    for rel, data in (extra or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


# ---------- parser ----------


def test_parse_valid_frontmatter() -> None:
    m = parse_skill_md(SKILL_MD)
    assert m.name == "Systematic Debugging"
    assert m.version == "1.2.0"
    assert m.license == "MIT"
    assert m.provides == ["root-cause-analysis"]


def test_parse_rejects_missing_frontmatter() -> None:
    with pytest.raises(DomainError) as exc:
        parse_skill_md("# just markdown\n")
    assert exc.value.code == ErrorCode.SKILL_PACKAGE_INVALID


def test_parse_rejects_missing_required_fields() -> None:
    no_name = SKILL_MD.replace("name: Systematic Debugging\n", "")
    with pytest.raises(DomainError):
        parse_skill_md(no_name)
    no_desc = SKILL_MD.replace("description: Evidence-driven debugging workflow.\n", "")
    with pytest.raises(DomainError):
        parse_skill_md(no_desc)


def test_parse_rejects_bad_yaml_and_types() -> None:
    with pytest.raises(DomainError):
        parse_skill_md("---\nname: [unclosed\n---\nbody\n")
    with pytest.raises(DomainError):
        parse_skill_md("---\nname: X\ndescription: d\nversion: v1\n---\n")
    with pytest.raises(DomainError):
        parse_skill_md("---\n- a\n- b\n---\n")
    with pytest.raises(DomainError):
        parse_skill_md("---\nname: X\ndescription: d\nprovides: not-a-list\n---\n")


# ---------- canonicalization ----------


def test_capability_id_normalization() -> None:
    assert canonical_capability_id("Systematic Debugging") == "systematic-debugging"
    assert canonical_capability_id("  Weird!!  Name__  ") == "weird-name"
    with pytest.raises(DomainError):
        canonical_capability_id("!!!")
    with pytest.raises(DomainError):
        canonical_capability_id("")


def test_derive_version_and_transformations() -> None:
    m = SkillMetadata(name="Systematic Debugging", description="d", version="2.0.0")
    assert derive_version(m) == "2.0.0"
    assert transformations_for(m, "systematic-debugging") == [
        "name-normalized:Systematic Debugging->systematic-debugging"
    ]

    m2 = SkillMetadata(name="Systematic Debugging", description="d")
    assert derive_version(m2) == "0.1.0"
    assert transformations_for(m2, "systematic-debugging") == [
        "name-normalized:Systematic Debugging->systematic-debugging",
        "version-defaulted:0.1.0",
    ]

    m3 = SkillMetadata(name="code-review", description="d", version="1.0.0")
    assert transformations_for(m3, "code-review") == []


# ---------- package ----------


def test_build_file_list_and_digest_determinism(tmp_path: Path) -> None:
    write_skill(
        tmp_path, extra={"references/checklist.md": b"steps\n", "assets/logo.png": b"\x89PNG"}
    )
    files = build_file_list(tmp_path)
    assert [f.path for f in files] == ["SKILL.md", "assets/logo.png", "references/checklist.md"]
    assert files[0].sha256 == hash_bytes((tmp_path / "SKILL.md").read_bytes())
    assert files[0].size_bytes == len((tmp_path / "SKILL.md").read_bytes())

    d1 = package_digest(files)
    d2 = package_digest(build_file_list(tmp_path))
    assert d1 == d2  # same content -> same digest, regardless of walk order
    other = package_digest([ArtifactFile(path="SKILL.md", sha256="0" * 64, size_bytes=1)])
    assert other != d1


def test_build_rejects_missing_skill_md(tmp_path: Path) -> None:
    (tmp_path / "other.md").write_text("no skill here")
    with pytest.raises(DomainError) as exc:
        build_file_list(tmp_path)
    assert exc.value.code == ErrorCode.SKILL_PACKAGE_INVALID


def test_build_rejects_symlink_escape(tmp_path: Path) -> None:
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("outside")
    write_skill(tmp_path)
    (tmp_path / "references").mkdir(exist_ok=True)
    (tmp_path / "references" / "escape.md").symlink_to(secret)
    with pytest.raises(DomainError, match="symlink"):
        build_file_list(tmp_path)


def test_build_rejects_non_directory(tmp_path: Path) -> None:
    with pytest.raises(DomainError):
        build_file_list(tmp_path / "missing")


# ---------- object store ----------


def test_fs_object_store_roundtrip(tmp_path: Path) -> None:
    store = FsObjectStore(tmp_path / "objects")
    data = b"hello blob"
    key = hash_bytes(data)
    assert store.get(key) is None
    assert not store.exists(key)
    store.put(key, data)
    assert store.get(key) == data
    assert store.exists(key)
    store.put(key, data)  # idempotent: content-addressed, never rewritten
    assert store.get(key) == data


def test_fs_object_store_rejects_bad_keys(tmp_path: Path) -> None:
    store = FsObjectStore(tmp_path / "objects")
    with pytest.raises(ValueError):
        store.put("../escape", b"x")
    with pytest.raises(ValueError):
        store.get("nothex")


def test_artifact_file_validation() -> None:
    with pytest.raises(ValidationError):
        ArtifactFile(path="x", sha256="ZZ", size_bytes=1)


# ---------- audit fixes: canonical id, digest separator, TOCTOU, caps ----------


def test_canonicalization_rejects_single_char_name() -> None:
    """A 1-char name cannot form a valid Capability.id (needs 2-64 chars);
    it must raise a clean domain error, never a pydantic ValidationError."""
    for name in ("a", "A", "a-", "-a-"):
        with pytest.raises(DomainError) as exc:
            canonical_capability_id(name)
        assert exc.value.code == ErrorCode.SKILL_PACKAGE_INVALID


def test_package_digest_count_header_kills_filename_ambiguity() -> None:
    """A file named 'x\\tAAA...\\ny' used to hash identically to a two-file
    package; the v1 count header makes every distinct listing distinct."""
    a = "a" * 64
    b = "b" * 64
    two_files = [
        ArtifactFile(path="x", sha256=a, size_bytes=1),
        ArtifactFile(path="y", sha256=b, size_bytes=1),
    ]
    weird_name = [ArtifactFile(path=f"x\t{a}\ny", sha256=b, size_bytes=1)]
    assert package_digest(two_files) != package_digest(weird_name)


def test_build_rejects_too_many_files_early(tmp_path: Path) -> None:
    write_skill(tmp_path)
    for i in range(500):  # 1 SKILL.md + 500 = 501 > MAX_FILES
        (tmp_path / f"f{i}.txt").write_text("x")
    with pytest.raises(DomainError) as exc:
        build_file_list(tmp_path)
    assert exc.value.code == ErrorCode.SKILL_PACKAGE_INVALID


def test_ingest_verifies_hash_before_storing_blob(tmp_path: Path) -> None:
    """A concurrent writer changing a file between hashing and storing must
    fail with ARTIFACT_INTEGRITY_ERROR, never store content under a foreign
    digest key (content-addressed invariant, §39)."""
    src = tmp_path / "skill-src"
    write_skill(src)
    (src / "references").mkdir(exist_ok=True)
    victim = src / "references" / "checklist.md"
    victim.write_text("original steps\n", encoding="utf-8")

    class MutatingStore(FsObjectStore):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.calls = 0

        def put(self, key: str, data: bytes) -> None:
            if self.calls == 0:  # writer strikes while SKILL.md is being stored
                victim.write_text("TAMPERED\n", encoding="utf-8")
            self.calls += 1
            return super().put(key, data)

    svc = SkillIngestionService(
        capabilities=_FakeCaps(),
        releases=_FakeReleases(),
        artifacts=_FakeArtifacts(),
        source_records=_FakeSourceRecords(),
        objects=MutatingStore(tmp_path / "objects"),
    )
    with pytest.raises(DomainError) as exc:
        svc.ingest_local(src)
    assert exc.value.code == ErrorCode.ARTIFACT_INTEGRITY_ERROR
    # nothing persisted: no version, no blob for the tampered file
    original_sha = hash_bytes(b"original steps\n")
    tampered_sha = hash_bytes(b"TAMPERED\n")
    assert svc._capabilities.get_version("systematic-debugging", "1.2.0") is None
    assert svc._objects.get(original_sha) is None
    assert svc._objects.get(tampered_sha) is None


class _FakeCaps:
    def __init__(self) -> None:
        self.caps: dict = {}
        self.versions: dict = {}

    def create_capability(self, c):
        self.caps[c.id] = c
        return c

    def get_capability(self, cid):
        return self.caps.get(cid)

    def create_version(self, v):
        self.versions[(v.capability_id, v.version)] = v
        return v

    def get_version(self, cid, ver):
        return self.versions.get((cid, ver))

    def list_versions(self, cid):
        return [v for (c, _), v in self.versions.items() if c == cid]

    def put_binding(self, b):
        return b

    def get_binding(self, bid):
        return None


class _FakeReleases:
    def __init__(self) -> None:
        self.r: dict = {}

    def set_release(self, r):
        self.r[(r.capability_id, r.channel)] = r
        return r

    def get_release(self, cid, ch):
        return self.r.get((cid, ch))

    def list_releases(self, cid):
        return [v for (c, _), v in self.r.items() if c == cid]


class _FakeArtifacts:
    def __init__(self) -> None:
        self.a: dict = {}

    def put_artifact(self, a):
        self.a[(a.capability_id, a.version)] = a
        return a

    def get_artifact(self, cid, ver):
        return self.a.get((cid, ver))


class _FakeSourceRecords:
    def __init__(self) -> None:
        self.r: list = []

    def add_source_record(self, rec):
        self.r.append(rec)
        return rec

    def list_source_records(self, cid):
        return [x for x in self.r if x.capability_id == cid]
