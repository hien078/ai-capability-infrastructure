"""OpenCode V2 HTTP skill catalog (plan §§28.2, 28.5; ADR-005; Phase 10).

A pure projection: the registry + release pointers stay the only source of
truth. This adapter translates them into OpenCode's native catalog format
(``index.json`` + per-file lazy load) — no skill bytes are transformed and no
lifecycle state is duplicated. Only active production releases of kind
``skill`` are exposed; revocation drops a skill from the catalog because the
projection is rebuilt from live release state on every request (§46).

OpenCode V2 catalog semantics (verified against the V2 docs, §77):

- ``index.json`` is ``{"skills": [{"name", "version", "files"}]}`` served at
  the catalog base URL; files download from ``<base>/<name>/<file>``.
- A downloaded skill directory becomes a *source root*, so the entry file must
  be ``<name>.md`` for the skill ID to equal the capability id — a root
  ``SKILL.md`` would get the literal ID ``SKILL``. The projection therefore
  renames ``SKILL.md`` → ``<capability_id>.md`` in the catalog namespace only;
  the immutable artifact keeps its canonical path.
- ``version`` changes make OpenCode refresh its cache — versions are
  immutable here, so a release pointer move is the only possible change.
"""

import hashlib
import re
from typing import Any

from fastapi import APIRouter, Request, Response

from aci.application.protocols import (
    ArtifactStore,
    CapabilityRepository,
    ObjectStore,
    ReleaseRepository,
)
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import CapabilityArtifact

_SAFE_PATH = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*$")

_MEDIA_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".json": "application/json",
    ".txt": "text/plain",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
}

router = APIRouter(prefix="/opencode/skills", tags=["opencode-catalog"])


def _safe_relative(path: str) -> bool:
    """OpenCode catalog rule: paths must be safe, relative, same-origin."""
    if not path or path.startswith("/") or "\\" in path:
        return False
    if ".." in path.split("/"):
        return False
    return bool(_SAFE_PATH.match(path))


class CatalogProjection:
    """Registry/release state → OpenCode catalog index + immutable file bytes."""

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

    def index(self) -> dict[str, list[dict[str, Any]]]:
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
        entries: list[dict[str, Any]] = []
        for release in releases:
            version = versions.get(release.capability_id)
            if version is None or version.kind != "skill":
                continue
            artifact = artifacts.get(release.capability_id)
            if artifact is None:
                continue
            entries.append(
                {
                    "name": release.capability_id,
                    "version": release.version,
                    "files": self._catalog_files(release.capability_id, artifact),
                }
            )
        return {"skills": entries}

    def read_file(self, skill_id: str, file_path: str) -> tuple[bytes, str]:
        """Serve one immutable file; returns (bytes, media_type)."""
        release = self._releases.get_release(skill_id, "production")
        if release is None or release.status != "active":
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"unknown skill {skill_id}")
        # §28.2: the catalog exposes production *skill* releases only — a
        # production tool/resource release is registry content, not a skill.
        version = self._capabilities.get_version(skill_id, release.version)
        if version is None or version.kind != "skill":
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"unknown skill {skill_id}")
        artifact = self._artifacts.get_artifact(skill_id, release.version)
        if artifact is None:
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"no artifact for {skill_id}")

        # Catalog namespace maps the entry file back to the canonical SKILL.md.
        artifact_path = "SKILL.md" if file_path == f"{skill_id}.md" else file_path
        if not _safe_relative(artifact_path):
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"unsafe catalog path {file_path!r}")
        entry = next((f for f in artifact.files if f.path == artifact_path), None)
        if entry is None:
            raise DomainError(
                ErrorCode.CAPABILITY_NOT_FOUND,
                f"{skill_id} has no file {artifact_path}",
            )

        data = self._objects.get(entry.sha256)
        if data is None:
            raise DomainError(
                ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                f"blob {entry.sha256} missing for {skill_id}/{artifact_path}",
            )
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            raise DomainError(
                ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                f"blob for {skill_id}/{artifact_path} failed its content hash",
            )
        media_type = _media_type(artifact_path)
        return data, media_type

    @staticmethod
    def _catalog_files(capability_id: str, artifact: CapabilityArtifact) -> list[str]:
        """Artifact paths → catalog paths (SKILL.md becomes <capability_id>.md).

        The entry alias wins the ``<name>.md`` catalog name: OpenCode's skill-ID
        contract requires it to be the SKILL.md entry, so a package that also
        ships a real ``<capability_id>.md`` has that file shadowed (never
        advertised twice, never served under the entry name).
        """
        alias = f"{capability_id}.md"
        names: list[str] = []
        for f in artifact.files:
            if f.path == alias:
                continue  # shadowed by the entry alias (skill-ID contract)
            name = alias if f.path == "SKILL.md" else f.path
            if name not in names:
                names.append(name)
        return names


def _media_type(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    _, dot, ext = name.rpartition(".")
    if not dot:
        return "application/octet-stream"
    return _MEDIA_TYPES.get(f".{ext.lower()}", "application/octet-stream")


@router.get("/index.json")
def catalog_index(request: Request) -> dict[str, list[dict[str, Any]]]:
    # app.state.catalog is set by create_app(); importing the wiring here
    # would create a cycle (wiring constructs the projection).
    catalog: CatalogProjection = request.app.state.catalog
    return catalog.index()


@router.get("/{skill_id}/{file_path:path}")
def catalog_file(skill_id: str, file_path: str, request: Request) -> Response:
    catalog: CatalogProjection = request.app.state.catalog
    data, media_type = catalog.read_file(skill_id, file_path)
    return Response(content=data, media_type=media_type)
