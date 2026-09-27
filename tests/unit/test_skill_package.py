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
    key = "ab" * 32
    data = b"hello blob"
    assert store.get(key) is None
    assert not store.exists(key)
    store.put(key, data)
    assert store.get(key) == data
    assert store.exists(key)
    store.put(key, b"other")  # idempotent: content-addressed, never rewritten
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
