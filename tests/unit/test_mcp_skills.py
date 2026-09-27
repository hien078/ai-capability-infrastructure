"""Phase 12 unit acceptance (§52, §62, ADR-006): MCP Skills extension projection.

SEP-2640 wire shapes over the canonical registry: entry URIs keep canonical
``SKILL.md`` paths (no OpenCode rename), manifests carry per-file sha256 +
size, frontmatter passes through raw, and unknown skill/file map to
``-32602``. Revocation drops a skill immediately (§46) because every read
re-derives from live release state.
"""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams

from aci.adapters.inbound.mcp.skills import (
    EXTENSION_ID,
    SkillCatalog,
    SkillsExtension,
    SkillsGetRequestParams,
)
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
SKILL_BYTES = b"---\nname: x\ndescription: d\nlicense: mit\ncustom_field: keep-me\n---\n\nbody\n"
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


def make_catalog(
    releases: list[CapabilityRelease],
    versions: dict[tuple[str, str], CapabilityVersion],
    artifacts: dict[tuple[str, str], CapabilityArtifact],
    blobs: dict[str, bytes],
) -> SkillCatalog:
    return SkillCatalog(
        FakeReleases(releases),
        FakeCapabilities(versions),
        FakeArtifacts(artifacts),
        FakeObjects(blobs),
    )


def active_production(cid: str, version: str = "1.0.0") -> CapabilityRelease:
    return CapabilityRelease(
        capability_id=cid, version=version, channel="production", status="active"
    )


def full_catalog() -> SkillCatalog:
    return make_catalog(
        releases=[active_production("cap-kept")],
        versions={("cap-kept", "1.0.0"): make_version("cap-kept")},
        artifacts={("cap-kept", "1.0.0"): make_artifact("cap-kept")},
        blobs={SKILL_SHA: SKILL_BYTES, GUIDE_SHA: GUIDE_BYTES},
    )


def test_entries_project_only_active_production_skills() -> None:
    catalog = make_catalog(
        releases=[
            active_production("cap-kept"),
            CapabilityRelease(
                capability_id="cap-revoked", version="1.0.0", channel="production", status="revoked"
            ),
            CapabilityRelease(
                capability_id="cap-raw", version="1.0.0", channel="raw", status="active"
            ),
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
        blobs={SKILL_SHA: SKILL_BYTES},
    )
    assert [e.uri for e in catalog.entries()] == ["skill://cap-kept/SKILL.md"]


def test_entry_manifest_is_uri_frontmatter_and_digest_per_file() -> None:
    entry = full_catalog().entries()[0]
    assert entry.uri == "skill://cap-kept/SKILL.md"
    # Frontmatter passes through raw — unknown fields survive (SEP-2640).
    assert entry.frontmatter["name"] == "x"
    assert entry.frontmatter["custom_field"] == "keep-me"
    by_uri = {r.uri: r for r in entry.resources}
    assert set(by_uri) == {
        "skill://cap-kept/SKILL.md",
        "skill://cap-kept/references/guide.md",
    }
    assert by_uri["skill://cap-kept/SKILL.md"].digest == f"sha256:{SKILL_SHA}"
    assert by_uri["skill://cap-kept/SKILL.md"].size == len(SKILL_BYTES)
    assert by_uri["skill://cap-kept/references/guide.md"].size == len(GUIDE_BYTES)


def test_entry_uri_resolution_rules() -> None:
    catalog = full_catalog()
    assert catalog.entry("skill://cap-kept/SKILL.md") is not None
    # Supporting files are not skill entries; unknown shapes/cids resolve None.
    assert catalog.entry("skill://cap-kept/references/guide.md") is None
    assert catalog.entry("skill://cap-nobody/SKILL.md") is None
    assert catalog.entry("file://cap-kept/SKILL.md") is None
    assert catalog.entry("skill://cap-kept/other.md") is None


def test_entry_skips_broken_frontmatter_and_missing_entry_blob() -> None:
    bad_fm = make_catalog(
        releases=[active_production("cap-bad")],
        versions={("cap-bad", "1.0.0"): make_version("cap-bad")},
        artifacts={("cap-bad", "1.0.0"): make_artifact("cap-bad")},
        blobs={SKILL_SHA: b"no frontmatter here"},
    )
    assert bad_fm.entries() == []
    assert bad_fm.entry("skill://cap-bad/SKILL.md") is None

    missing_blob = make_catalog(
        releases=[active_production("cap-missing")],
        versions={("cap-missing", "1.0.0"): make_version("cap-missing")},
        artifacts={("cap-missing", "1.0.0"): make_artifact("cap-missing")},
        blobs={},
    )
    assert missing_blob.entries() == []


def test_read_serves_canonical_paths() -> None:
    catalog = full_catalog()
    body, media = catalog.read("cap-kept", "SKILL.md")
    assert body == SKILL_BYTES
    assert media == "text/markdown"
    guide, _ = catalog.read("cap-kept", "references/guide.md")
    assert guide == GUIDE_BYTES


def test_read_rejects_unsafe_and_unknown_paths() -> None:
    catalog = full_catalog()
    for bad in ("../SKILL.md", "/etc/passwd", "a\\b.md", "", "references/../../x.md"):
        with pytest.raises(DomainError) as exc:
            catalog.read("cap-kept", bad)
        assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND
    with pytest.raises(DomainError) as exc:
        catalog.read("cap-kept", "nope.md")
    assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND
    with pytest.raises(DomainError) as exc:
        catalog.read("cap-nobody", "SKILL.md")
    assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND


def test_read_enforces_content_hash_and_missing_blob() -> None:
    tampered = make_catalog(
        releases=[active_production("cap-kept")],
        versions={("cap-kept", "1.0.0"): make_version("cap-kept")},
        artifacts={("cap-kept", "1.0.0"): make_artifact("cap-kept")},
        blobs={SKILL_SHA: b"---\nname: evil\n---\ntampered"},
    )
    with pytest.raises(DomainError) as exc:
        tampered.read("cap-kept", "SKILL.md")
    assert exc.value.code == ErrorCode.ARTIFACT_INTEGRITY_ERROR

    missing = make_catalog(
        releases=[active_production("cap-kept")],
        versions={("cap-kept", "1.0.0"): make_version("cap-kept")},
        artifacts={("cap-kept", "1.0.0"): make_artifact("cap-kept")},
        blobs={},
    )
    with pytest.raises(DomainError) as exc:
        missing.read("cap-kept", "SKILL.md")
    assert exc.value.code == ErrorCode.ARTIFACT_INTEGRITY_ERROR


def test_read_rejects_non_skill_kind() -> None:
    catalog = make_catalog(
        releases=[active_production("cap-tool")],
        versions={("cap-tool", "1.0.0"): make_version("cap-tool", kind="tool")},
        artifacts={("cap-tool", "1.0.0"): make_artifact("cap-tool")},
        blobs={SKILL_SHA: SKILL_BYTES},
    )
    with pytest.raises(DomainError) as exc:
        catalog.read("cap-tool", "SKILL.md")
    assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND


def test_extension_identifier_and_settings() -> None:
    extension = SkillsExtension(full_catalog())
    assert extension.identifier == EXTENSION_ID == "io.modelcontextprotocol/skills"
    assert extension.settings() == {"directoryRead": False}


def test_extension_methods_bind_list_and_get() -> None:
    extension = SkillsExtension(full_catalog())
    bindings = {b.method: b for b in extension.methods()}
    assert set(bindings) == {"skills/list", "skills/get"}
    assert bindings["skills/list"].params_type is PaginatedRequestParams
    assert bindings["skills/get"].params_type is SkillsGetRequestParams


def test_extension_list_returns_atomic_entries_uncached() -> None:
    extension = SkillsExtension(full_catalog())
    result = asyncio.run(
        extension._list(None, PaginatedRequestParams())  # type: ignore[arg-type]
    )
    assert [e.uri for e in result.skills] == ["skill://cap-kept/SKILL.md"]
    # §46: the listing is revocation-sensitive — never client-cacheable.
    assert result.ttl_ms == 0
    assert result.cache_scope == "private"
    assert result.result_type == "complete"


def test_extension_get_resolves_entry_and_rejects_unknown() -> None:
    extension = SkillsExtension(full_catalog())
    result = asyncio.run(
        extension._get(None, SkillsGetRequestParams(uri="skill://cap-kept/SKILL.md"))  # type: ignore[arg-type]
    )
    assert result.skill.uri == "skill://cap-kept/SKILL.md"
    assert result.ttl_ms == 0
    assert result.cache_scope == "private"

    with pytest.raises(MCPError) as exc:
        asyncio.run(
            extension._get(  # type: ignore[arg-type]
                None, SkillsGetRequestParams(uri="skill://cap-nobody/SKILL.md")
            )
        )
    assert exc.value.code == INVALID_PARAMS
