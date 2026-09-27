"""Phase 3 acceptance (plan §52): ingest sample skills, preserve exact source
snapshot, calculate hashes, reject invalid packages, distinguish raw source
from canonical artifact.

Each test ingests under a unique capability id: the suite runs against a live
shared database, so fixed names would collide across runs.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aci.adapters.outbound.object_store.fs import FsObjectStore
from aci.adapters.outbound.postgres.repositories import (
    SqlAlchemyArtifactStore,
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.source_records import (
    SqlAlchemySourceRecordRepository,
)
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.providers.skills.ingestion import SkillIngestionService
from aci.providers.skills.package import build_file_list, package_digest

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def skill_md(name: str, version: str | None = "1.2.0") -> str:
    version_line = f"version: {version}\n" if version else ""
    return f"""---
name: {name}
description: Evidence-driven debugging workflow.
{version_line}license: MIT
provides:
  - root-cause-analysis
---

Find the root cause before patching.
"""


def write_skill(root: Path, name: str, version: str | None = "1.2.0") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(skill_md(name, version), encoding="utf-8")
    (root / "references").mkdir(exist_ok=True)
    (root / "references" / "checklist.md").write_text("steps\n", encoding="utf-8")
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n")


def test_ingest_happy_path(
    ingestion: SkillIngestionService,
    capability_repo: SqlAlchemyCapabilityRepository,
    release_repo: SqlAlchemyReleaseRepository,
    artifact_store: SqlAlchemyArtifactStore,
    source_records: SqlAlchemySourceRecordRepository,
    tmp_path: Path,
) -> None:
    cap = uid("sysdbg")
    src = tmp_path / "skill-src"
    write_skill(src, cap)

    result = ingestion.ingest_local(
        src,
        license_identifier="MIT",
        source_repository="github.com/example/skills",
        commit_sha="abc123",
        now=NOW,
    )

    assert result.capability_id == cap
    assert result.version == "1.2.0"
    assert result.capability_created is True
    assert result.already_ingested is False
    assert result.file_count == 3
    assert result.quarantined_channel == "raw"
    assert result.transformations == []

    # Canonical version: immutable, digest-pinned, spec intact.
    version = capability_repo.get_version(cap, "1.2.0")
    assert version is not None
    expected = package_digest(build_file_list(src))
    assert version.content_digest == f"sha256:{expected}"
    assert version.spec.provides == ["root-cause-analysis"]
    assert version.spec.artifacts == ["SKILL.md", "assets/logo.png", "references/checklist.md"]

    # Canonical artifact: file list + per-file hashes + package digest.
    artifact = artifact_store.get_artifact(cap, "1.2.0")
    assert artifact is not None
    assert artifact.package_digest == version.content_digest
    assert len(artifact.files) == 3
    assert all(f.sha256 and f.size_bytes >= 0 for f in artifact.files)

    # Quarantine: raw channel pointer, not production.
    raw = release_repo.get_release(cap, "raw")
    assert raw is not None and raw.version == "1.2.0" and raw.status == "active"
    assert release_repo.get_release(cap, "production") is None

    # Provenance: raw snapshot digest + origin, distinct record from artifact.
    records = source_records.list_source_records(cap)
    assert len(records) == 1
    rec = records[0]
    assert rec.raw_snapshot_digest == version.content_digest
    assert rec.license_identifier == "MIT"
    assert rec.source_repository == "github.com/example/skills"
    assert rec.commit_sha == "abc123"
    assert rec.ingested_at == NOW
    assert rec.ingestion_tool_version == "0.1.0"
    assert rec.local_transformations == []


def test_ingest_preserves_exact_source_bytes(
    ingestion: SkillIngestionService,
    artifact_store: SqlAlchemyArtifactStore,
    tmp_path: Path,
) -> None:
    cap = uid("sysdbg")
    src = tmp_path / "skill-src"
    write_skill(src, cap)
    ingestion.ingest_local(src, now=NOW)

    artifact = artifact_store.get_artifact(cap, "1.2.0")
    assert artifact is not None
    objects = FsObjectStore(tmp_path / "objects")
    for f in artifact.files:
        original = (src / f.path).read_bytes()
        assert objects.get(f.sha256) == original  # exact snapshot, byte for byte
        assert f.size_bytes == len(original)


def test_reingest_identical_content_is_idempotent(
    ingestion: SkillIngestionService,
    source_records: SqlAlchemySourceRecordRepository,
    tmp_path: Path,
) -> None:
    cap = uid("sysdbg")
    src = tmp_path / "skill-src"
    write_skill(src, cap)
    first = ingestion.ingest_local(src, now=NOW)
    second = ingestion.ingest_local(src, now=NOW)

    assert second.already_ingested is True
    assert second.capability_created is False
    assert second.package_digest == first.package_digest
    assert len(source_records.list_source_records(cap)) == 1


def test_reingest_changed_content_conflicts(
    ingestion: SkillIngestionService,
    tmp_path: Path,
) -> None:
    cap = uid("sysdbg")
    src = tmp_path / "skill-src"
    write_skill(src, cap)
    ingestion.ingest_local(src, now=NOW)

    (src / "references" / "checklist.md").write_text("changed steps\n", encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        ingestion.ingest_local(src, now=NOW)
    assert exc.value.code == ErrorCode.CAPABILITY_ALREADY_EXISTS


def test_ingest_new_version_creates_second_version(
    ingestion: SkillIngestionService,
    capability_repo: SqlAlchemyCapabilityRepository,
    tmp_path: Path,
) -> None:
    cap = uid("sysdbg")
    src = tmp_path / "skill-src"
    write_skill(src, cap)
    ingestion.ingest_local(src, now=NOW)

    write_skill(src, cap, version="1.3.0")
    result = ingestion.ingest_local(src, now=NOW)

    assert result.version == "1.3.0"
    assert result.capability_created is False  # same capability, new version
    assert [v.version for v in capability_repo.list_versions(cap)] == ["1.2.0", "1.3.0"]


def test_invalid_package_rejected_nothing_persisted(
    ingestion: SkillIngestionService,
    capability_repo: SqlAlchemyCapabilityRepository,
    source_records: SqlAlchemySourceRecordRepository,
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(DomainError) as exc:
        ingestion.ingest_local(empty, now=NOW)
    assert exc.value.code == ErrorCode.SKILL_PACKAGE_INVALID

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter here\n", encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        ingestion.ingest_local(bad, now=NOW)
    assert exc.value.code == ErrorCode.SKILL_PACKAGE_INVALID

    assert capability_repo.get_capability("empty") is None
    assert capability_repo.get_capability("bad") is None
    assert source_records.list_source_records("empty") == []


def test_ingest_defaults_version_and_records_transformations(
    ingestion: SkillIngestionService,
    capability_repo: SqlAlchemyCapabilityRepository,
    source_records: SqlAlchemySourceRecordRepository,
    tmp_path: Path,
) -> None:
    suffix = uid("x")
    src = tmp_path / "skill-src"
    write_skill(src, f"Code Review {suffix}", version=None)
    result = ingestion.ingest_local(src, now=NOW)

    expected_id = f"code-review-{suffix}"
    assert result.capability_id == expected_id
    assert result.version == "0.1.0"
    assert result.transformations == [
        f"name-normalized:Code Review {suffix}->{expected_id}",
        "version-defaulted:0.1.0",
    ]
    rec = source_records.list_source_records(expected_id)
    assert len(rec) == 1
    assert rec[0].local_transformations == result.transformations
    assert capability_repo.get_version(expected_id, "0.1.0") is not None
