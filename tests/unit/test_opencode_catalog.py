"""Phase 10 unit acceptance (§52, §62): OpenCode catalog projection.

Catalog format, skill ID mapping (SKILL.md → <capability_id>.md), version
mapping from release pointers, path safety, immutable file serving with
content-hash verification. The projection never duplicates lifecycle state:
everything derives from release/artifact records.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest

from aci.adapters.inbound.opencode.catalog import CatalogProjection
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import (
    ArtifactFile,
    CapabilityArtifact,
    CapabilityRelease,
    CapabilityVersion,
    SkillSpec,
    ToolSpec,
)

NOW = datetime(2026, 9, 28, tzinfo=UTC)
SKILL_BYTES = b"---\nname: x\ndescription: d\n---\n\nbody\n"
GUIDE_BYTES = b"# guide\n"
SKILL_SHA = hashlib.sha256(SKILL_BYTES).hexdigest()
GUIDE_SHA = hashlib.sha256(GUIDE_BYTES).hexdigest()


def make_version(cid: str, *, kind: str = "skill", version: str = "1.0.0") -> CapabilityVersion:
    spec: Any = SkillSpec() if kind == "skill" else ToolSpec(operation="run")
    return CapabilityVersion(
        capability_id=cid,
        version=version,
        kind=kind,  # type: ignore[arg-type]
        content_digest=f"sha256:{'a' * 64}",
        created_at=NOW,
        spec=spec,
    )


def make_artifact(cid: str, version: str = "1.0.0") -> CapabilityArtifact:
    return CapabilityArtifact(
        capability_id=cid,
        version=version,
        package_digest=f"sha256:{'b' * 64}",
        manifest={"entrypoint": "SKILL.md"},
        files=[
            ArtifactFile(path="SKILL.md", size_bytes=len(SKILL_BYTES), sha256=SKILL_SHA),
            ArtifactFile(path="references/guide.md", size_bytes=len(GUIDE_BYTES), sha256=GUIDE_SHA),
        ],
    )


class FakeReleases:
    def __init__(self, releases: list[CapabilityRelease]) -> None:
        self.releases = releases

    def list_channel(self, channel: str, *, status: str | None = None) -> list[Any]:
        return [
            r
            for r in self.releases
            if r.channel == channel and (status is None or r.status == status)
        ]

    def get_release(self, capability_id: str, channel: str) -> Any:
        return next(
            (r for r in self.releases if r.capability_id == capability_id and r.channel == channel),
            None,
        )


class FakeCapabilities:
    def __init__(self, versions: dict[tuple[str, str], CapabilityVersion]) -> None:
        self.versions = versions

    def get_version(self, capability_id: str, version: str) -> CapabilityVersion | None:
        return self.versions.get((capability_id, version))


class FakeArtifacts:
    def __init__(self, artifacts: dict[tuple[str, str], CapabilityArtifact]) -> None:
        self.artifacts = artifacts

    def get_artifact(self, capability_id: str, version: str) -> CapabilityArtifact | None:
        return self.artifacts.get((capability_id, version))


class FakeObjects:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def get(self, key: str) -> bytes | None:
        return self.blobs.get(key)


def make_projection(
    releases: list[CapabilityRelease],
    versions: dict[tuple[str, str], CapabilityVersion],
    artifacts: dict[tuple[str, str], CapabilityArtifact],
    blobs: dict[str, bytes],
) -> CatalogProjection:
    return CatalogProjection(
        FakeReleases(releases),
        FakeCapabilities(versions),
        FakeArtifacts(artifacts),
        FakeObjects(blobs),
    )


def active_production(cid: str, version: str = "1.0.0") -> CapabilityRelease:
    return CapabilityRelease(
        capability_id=cid, version=version, channel="production", status="active"
    )


def test_index_projects_only_active_production_skills() -> None:
    projection = make_projection(
        releases=[
            active_production("cap-kept"),
            CapabilityRelease(
                capability_id="cap-revoked",
                version="1.0.0",
                channel="production",
                status="revoked",
            ),
            CapabilityRelease(
                capability_id="cap-raw",
                version="1.0.0",
                channel="raw",
                status="active",
            ),
            # A tool-kind production release is registry content, but not a
            # skill catalog entry: the catalog exposes skills only (§28.2).
            active_production("cap-tool"),
        ],
        versions={
            ("cap-kept", "1.0.0"): make_version("cap-kept"),
            ("cap-revoked", "1.0.0"): make_version("cap-revoked"),
            ("cap-raw", "1.0.0"): make_version("cap-raw"),
            ("cap-tool", "1.0.0"): make_version("cap-tool", kind="tool"),
        },
        artifacts={
            ("cap-kept", "1.0.0"): make_artifact("cap-kept"),
            ("cap-revoked", "1.0.0"): make_artifact("cap-revoked"),
            ("cap-tool", "1.0.0"): make_artifact("cap-tool"),
        },
        blobs={},
    )

    index = projection.index()
    assert [e["name"] for e in index["skills"]] == ["cap-kept"]


def test_index_entry_maps_version_and_files() -> None:
    projection = make_projection(
        releases=[active_production("cap-kept", "2.3.1")],
        versions={("cap-kept", "2.3.1"): make_version("cap-kept", version="2.3.1")},
        artifacts={("cap-kept", "2.3.1"): make_artifact("cap-kept", "2.3.1")},
        blobs={},
    )
    entry = projection.index()["skills"][0]
    assert entry["name"] == "cap-kept"
    assert entry["version"] == "2.3.1"  # from the release pointer, not hardcoded
    # SKILL.md is renamed to <name>.md so the OpenCode skill ID == capability id.
    assert entry["files"] == ["cap-kept.md", "references/guide.md"]


def test_read_file_serves_entry_and_reference_bytes() -> None:
    projection = make_projection(
        releases=[active_production("cap-kept")],
        versions={("cap-kept", "1.0.0"): make_version("cap-kept")},
        artifacts={("cap-kept", "1.0.0"): make_artifact("cap-kept")},
        blobs={SKILL_SHA: SKILL_BYTES, GUIDE_SHA: GUIDE_BYTES},
    )
    body, media = projection.read_file("cap-kept", "cap-kept.md")
    assert body == SKILL_BYTES
    assert media == "text/markdown"

    guide, guide_media = projection.read_file("cap-kept", "references/guide.md")
    assert guide == GUIDE_BYTES
    assert guide_media == "text/markdown"


def test_read_file_rejects_unsafe_paths() -> None:
    projection = make_projection(
        releases=[active_production("cap-kept")],
        versions={("cap-kept", "1.0.0"): make_version("cap-kept")},
        artifacts={("cap-kept", "1.0.0"): make_artifact("cap-kept")},
        blobs={SKILL_SHA: SKILL_BYTES},
    )
    for bad in ("../cap-kept.md", "/etc/passwd", "a\\b.md", "", "references/../../x.md"):
        with pytest.raises(DomainError) as exc:
            projection.read_file("cap-kept", bad)
        assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND


def test_read_file_unknown_skill_and_unknown_file() -> None:
    projection = make_projection(
        releases=[active_production("cap-kept")],
        versions={("cap-kept", "1.0.0"): make_version("cap-kept")},
        artifacts={("cap-kept", "1.0.0"): make_artifact("cap-kept")},
        blobs={SKILL_SHA: SKILL_BYTES},
    )
    with pytest.raises(DomainError) as exc:
        projection.read_file("cap-nobody", "cap-nobody.md")
    assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND

    with pytest.raises(DomainError) as exc:
        projection.read_file("cap-kept", "nope.md")
    assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND


def test_read_file_enforces_content_hash() -> None:
    tampered = b"---\nname: evil\n---\nchanged body"
    projection = make_projection(
        releases=[active_production("cap-kept")],
        versions={("cap-kept", "1.0.0"): make_version("cap-kept")},
        artifacts={("cap-kept", "1.0.0"): make_artifact("cap-kept")},
        blobs={SKILL_SHA: tampered},  # key says one thing, bytes say another
    )
    with pytest.raises(DomainError) as exc:
        projection.read_file("cap-kept", "cap-kept.md")
    assert exc.value.code == ErrorCode.ARTIFACT_INTEGRITY_ERROR


def test_read_file_missing_blob_is_integrity_error() -> None:
    projection = make_projection(
        releases=[active_production("cap-kept")],
        versions={("cap-kept", "1.0.0"): make_version("cap-kept")},
        artifacts={("cap-kept", "1.0.0"): make_artifact("cap-kept")},
        blobs={},  # manifest references a blob that was never stored
    )
    with pytest.raises(DomainError) as exc:
        projection.read_file("cap-kept", "cap-kept.md")
    assert exc.value.code == ErrorCode.ARTIFACT_INTEGRITY_ERROR


def test_read_file_rejects_non_skill_kind() -> None:
    """§28.2: the catalog serves production *skill* releases only — a
    production tool release is registry content, not catalog content."""
    projection = make_projection(
        releases=[active_production("cap-tool")],
        versions={("cap-tool", "1.0.0"): make_version("cap-tool", kind="tool")},
        artifacts={("cap-tool", "1.0.0"): make_artifact("cap-tool")},
        blobs={SKILL_SHA: SKILL_BYTES},
    )
    with pytest.raises(DomainError) as exc:
        projection.read_file("cap-tool", "cap-tool.md")
    assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND


def test_catalog_alias_shadows_same_named_real_file() -> None:
    """A package shipping a real <capability_id>.md next to SKILL.md must not
    advertise the entry name twice; the alias (entry) wins the catalog name."""
    artifact = CapabilityArtifact(
        capability_id="cap-x",
        version="1.0.0",
        package_digest=f"sha256:{'b' * 64}",
        manifest={"entrypoint": "SKILL.md"},
        files=[
            ArtifactFile(path="SKILL.md", size_bytes=len(SKILL_BYTES), sha256=SKILL_SHA),
            ArtifactFile(path="cap-x.md", size_bytes=3, sha256=GUIDE_SHA),
            ArtifactFile(path="references/guide.md", size_bytes=len(GUIDE_BYTES), sha256=GUIDE_SHA),
        ],
    )
    projection = make_projection(
        releases=[active_production("cap-x")],
        versions={("cap-x", "1.0.0"): make_version("cap-x")},
        artifacts={("cap-x", "1.0.0"): artifact},
        blobs={SKILL_SHA: SKILL_BYTES, GUIDE_SHA: GUIDE_BYTES},
    )

    entry = projection.index()["skills"][0]
    # No duplicate names; the shadowed real file is not advertised.
    assert entry["files"] == ["cap-x.md", "references/guide.md"]
    # The entry alias still serves the canonical SKILL.md bytes.
    body, _ = projection.read_file("cap-x", "cap-x.md")
    assert body == SKILL_BYTES
