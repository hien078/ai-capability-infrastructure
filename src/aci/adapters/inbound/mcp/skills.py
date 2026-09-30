"""MCP Skills extension (SEP-2640, Final) over the canonical registry (§29.1, ADR-006).

Wire shapes verified against the published extension spec (§77):

- ``skills/list`` → ``{resultType, skills, ttlMs, cacheScope}`` where each skill is
  ``{uri, frontmatter, resources: [{uri, digest, size}]}`` — the manifest MUST
  list every file with its sha256 digest and size, and entries are atomic (a
  manifest is never split across pages).
- ``skills/get`` → ``{skill, resultType, ttlMs, cacheScope}`` addressed by the
  skill's entry URI.
- File bytes travel over the core ``resources/read`` on ``skill://`` URIs;
  unknown skill or file → JSON-RPC ``-32602`` (Invalid params).
- The entry file is the canonical ``SKILL.md`` (Agent Skills spec: the skill
  directory name matches the frontmatter name) — no OpenCode-style rename here.

Caching: ``ttl_ms=0`` / ``cache_scope="private"``. Skill *content* is immutable
per version, but the *listing* is revocation-sensitive (§46), so clients must
re-fetch rather than serve it from shared caches.
"""

import hashlib
import re
from typing import Any

import yaml
from mcp.server.context import ServerRequestContext
from mcp.server.extension import Extension, MethodBinding
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_PARAMS,
    CacheableResult,
    PaginatedRequestParams,
    RequestParams,
    ResultType,
)
from pydantic import BaseModel, Field

from aci.application.protocols import (
    ArtifactStore,
    CapabilityRepository,
    ObjectStore,
    ReleaseRepository,
)
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import CapabilityArtifact, CapabilityRelease

EXTENSION_ID = "io.modelcontextprotocol/skills"

# Same shape as the ingestion parser (providers/skills/parser.py), but this one
# preserves *all* frontmatter fields — SEP-2640 requires the raw mapping.
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_ENTRY_URI = re.compile(r"^skill://([^/]+)/SKILL\.md$")
_SAFE_PATH = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*$")

_MEDIA_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".json": "application/json",
    ".txt": "text/plain",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
}


class SkillResourceManifest(BaseModel):
    """One file in a skill manifest: URI + sha256 digest + size (SEP-2640)."""

    uri: str
    digest: str
    size: int


class SkillEntry(BaseModel):
    """A discoverable skill: entry URI, raw frontmatter, full file manifest."""

    uri: str
    frontmatter: dict[str, Any]
    resources: list[SkillResourceManifest]


class SkillsListResult(CacheableResult):
    """``skills/list`` result — one complete page of atomic skill entries."""

    result_type: ResultType = "complete"
    skills: list[SkillEntry] = Field(default_factory=list)


class SkillsGetResult(CacheableResult):
    """``skills/get`` result — one skill entry addressed by its URI."""

    result_type: ResultType = "complete"
    skill: SkillEntry


class SkillsGetRequestParams(RequestParams):
    """``skills/get`` params: the entry URI of a skill from ``skills/list``."""

    uri: str


def _safe_relative(path: str) -> bool:
    """Manifest paths are relative and traversal-free; URIs must be too."""
    if not path or path.startswith("/") or "\\" in path:
        return False
    if ".." in path.split("/"):
        return False
    return bool(_SAFE_PATH.match(path))


def _media_type(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    _, dot, ext = name.rpartition(".")
    if not dot:
        return "application/octet-stream"
    return _MEDIA_TYPES.get(f".{ext.lower()}", "application/octet-stream")


def _parse_frontmatter(data: bytes) -> dict[str, Any] | None:
    """Raw YAML frontmatter mapping, or None if absent/malformed/non-mapping."""
    match = _FRONTMATTER.match(data.decode("utf-8"))
    if match is None:
        return None
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, dict) else None


class SkillCatalog:
    """Registry/release state → MCP skill entries + canonical file bytes.

    Same projection rules as the OpenCode catalog (§28.2): active production
    ``skill`` releases only, manifest-whitelisted paths, sha256 re-verification
    on every read, revocation drops the skill immediately (§46). Unlike the
    OpenCode namespace this one keeps canonical paths — the entry stays
    ``SKILL.md`` and there is no ``<capability_id>.md`` alias.
    """

    def __init__(
        self,
        releases: ReleaseRepository,
        capabilities: CapabilityRepository,
        artifacts: ArtifactStore,
        objects: ObjectStore,
    ) -> None:
        self._releases = releases
        self._capabilities = capabilities
        self._artifacts = artifacts
        self._objects = objects

    def entries(self) -> list[SkillEntry]:
        """All exposed skills — one atomic page (V1 serves the full set).

        Batch reads (one query per kind, not per release) — the catalog
        page is the hot path for every MCP client listing skills.
        """
        releases = self._releases.list_channel("production", status="active")
        versions = {
            v.capability_id: v
            for v in self._capabilities.get_versions(
                [(r.capability_id, r.version) for r in releases]
            )
        }
        artifacts = {
            a.capability_id: a
            for a in self._artifacts.get_artifacts([(r.capability_id, r.version) for r in releases])
        }
        out: list[SkillEntry] = []
        for release in releases:
            version = versions.get(release.capability_id)
            if version is None or version.kind != "skill":
                continue
            artifact = artifacts.get(release.capability_id)
            if artifact is None:
                continue
            entry = self._entry_of(release, artifact)
            if entry is not None:
                out.append(entry)
        return out

    def entry(self, uri: str) -> SkillEntry | None:
        """Resolve a skill entry URI (``skill://<id>/SKILL.md``) or None."""
        match = _ENTRY_URI.match(uri)
        if match is None:
            return None
        release = self._releases.get_release(match.group(1), "production")
        if release is None or release.status != "active":
            return None
        return self._entry(release)

    def read(self, capability_id: str, file_path: str) -> tuple[bytes, str]:
        """Serve one canonical file; returns (bytes, media_type).

        Raises DomainError CAPABILITY_NOT_FOUND for unknown skill/file and
        ARTIFACT_INTEGRITY_ERROR when a blob is missing or fails its hash.
        """
        loaded = self._load(capability_id)
        if loaded is None:
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"unknown skill {capability_id}")
        artifact = loaded
        if not _safe_relative(file_path):
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"unsafe path {file_path!r}")
        entry = next((f for f in artifact.files if f.path == file_path), None)
        if entry is None:
            raise DomainError(
                ErrorCode.CAPABILITY_NOT_FOUND, f"{capability_id} has no file {file_path}"
            )
        data = self._objects.get(entry.sha256)
        if data is None:
            raise DomainError(
                ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                f"blob {entry.sha256} missing for {capability_id}/{file_path}",
            )
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            raise DomainError(
                ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                f"blob for {capability_id}/{file_path} failed its content hash",
            )
        return data, _media_type(file_path)

    def _load(self, capability_id: str) -> CapabilityArtifact | None:
        """Active production ``skill`` release → its artifact, or None."""
        release = self._releases.get_release(capability_id, "production")
        if release is None or release.status != "active":
            return None
        return self._artifact(release)

    def _artifact(self, release: CapabilityRelease) -> CapabilityArtifact | None:
        """Artifact of a release whose capability is an exposed skill, or None."""
        version = self._capabilities.get_version(release.capability_id, release.version)
        if version is None or version.kind != "skill":
            return None
        return self._artifacts.get_artifact(release.capability_id, release.version)

    def _entry(self, release: CapabilityRelease) -> SkillEntry | None:
        """Build one skill entry, or None when not exposed / frontmatter broken."""
        artifact = self._artifact(release)
        if artifact is None:
            return None
        return self._entry_of(release, artifact)

    def _entry_of(
        self, release: CapabilityRelease, artifact: CapabilityArtifact
    ) -> SkillEntry | None:
        """Entry from an already-loaded artifact (the batch path)."""
        entry_file = next((f for f in artifact.files if f.path == "SKILL.md"), None)
        if entry_file is None:
            return None
        data = self._objects.get(entry_file.sha256)
        if data is None or hashlib.sha256(data).hexdigest() != entry_file.sha256:
            return None
        frontmatter = _parse_frontmatter(data)
        if frontmatter is None:
            return None
        return SkillEntry(
            uri=f"skill://{release.capability_id}/SKILL.md",
            frontmatter=frontmatter,
            resources=[
                SkillResourceManifest(
                    uri=f"skill://{release.capability_id}/{f.path}",
                    digest=f"sha256:{f.sha256}",
                    size=f.size_bytes,
                )
                for f in artifact.files
            ],
        )


class SkillsExtension(Extension):
    """SEP-2640 server surface: ``skills/list`` + ``skills/get`` (ADR-006).

    File bytes are NOT served here — they go through the core
    ``resources/read`` handler registered in ``server.py`` on ``skill://``
    URIs, exactly as the extension spec prescribes.
    """

    identifier = EXTENSION_ID

    def __init__(self, catalog: SkillCatalog) -> None:
        self._catalog = catalog

    def settings(self) -> dict[str, Any]:
        """Advertised under ``capabilities.extensions[<id>]``."""
        return {"directoryRead": False}

    def methods(self) -> tuple[MethodBinding, ...]:
        return (
            MethodBinding(
                method="skills/list",
                params_type=PaginatedRequestParams,
                handler=self._list,
            ),
            MethodBinding(
                method="skills/get",
                params_type=SkillsGetRequestParams,
                handler=self._get,
            ),
        )

    async def _list(
        self, ctx: ServerRequestContext[Any, Any], params: PaginatedRequestParams
    ) -> SkillsListResult:
        # ttl_ms=0/private: the listing is revocation-sensitive (§46) — never
        # cacheable client-side; content itself is immutable per version.
        return SkillsListResult(skills=self._catalog.entries(), ttl_ms=0, cache_scope="private")

    async def _get(
        self, ctx: ServerRequestContext[Any, Any], params: SkillsGetRequestParams
    ) -> SkillsGetResult:
        entry = self._catalog.entry(params.uri)
        if entry is None:
            # Spec: unknown skill (or non-entry URI) → Invalid params (-32602).
            raise MCPError(INVALID_PARAMS, f"unknown skill {params.uri}")
        return SkillsGetResult(skill=entry, ttl_ms=0, cache_scope="private")
