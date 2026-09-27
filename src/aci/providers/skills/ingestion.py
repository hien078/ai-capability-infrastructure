"""Local skill ingestion: snapshot → hash → quarantine → canonical version (plan §§21-23).

Flow per ingest_local():
  1. parse SKILL.md and validate package structure (nothing is persisted on failure),
  2. store every file blob in the content-addressed object store,
  3. create capability + immutable version + artifact,
  4. point the `raw` release channel at it (quarantine; plan §21),
  5. append a provenance source record with the raw snapshot digest.

Raw source and canonical artifact stay distinct records: the source record keeps
the raw snapshot digest + origin fields, the version keeps the canonical package
digest. In V1 no file bytes are transformed, so the two digests coincide; the
records remain separate so future transforms cannot erase lineage (§23).

Re-ingesting identical content is idempotent (already_ingested=True, no new
rows). Re-ingesting changed content under the same version is a conflict —
upstream changes must produce a new version (§38), never mutate a published one.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from aci.application.protocols import (
    ArtifactStore,
    CapabilityRepository,
    ObjectStore,
    ReleaseRepository,
    SourceRecordRepository,
)
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import (
    Capability,
    CapabilityArtifact,
    CapabilityRelease,
    CapabilityVersion,
    SkillSpec,
)
from aci.domain.skills.models import IngestionResult, SourceProvenance
from aci.providers.skills.canonicalization import (
    canonical_capability_id,
    derive_version,
    transformations_for,
)
from aci.providers.skills.package import build_file_list, package_digest
from aci.providers.skills.parser import parse_skill_md

TOOL_VERSION = "0.1.0"


class SkillIngestionService:
    def __init__(
        self,
        capabilities: CapabilityRepository,
        releases: ReleaseRepository,
        artifacts: ArtifactStore,
        source_records: SourceRecordRepository,
        objects: ObjectStore,
    ) -> None:
        self._capabilities = capabilities
        self._releases = releases
        self._artifacts = artifacts
        self._source_records = source_records
        self._objects = objects

    def ingest_local(
        self,
        source: Path,
        *,
        license_identifier: str | None = None,
        source_repository: str | None = None,
        source_url_reference: str | None = None,
        commit_sha: str | None = None,
        source_version: str | None = None,
        derived_from: str | None = None,
        now: datetime | None = None,
    ) -> IngestionResult:
        ingested_at = now or datetime.now(UTC)

        # 1. Validate everything before touching persistence.
        skill_path = source / "SKILL.md"
        if not skill_path.is_file():
            raise DomainError(
                ErrorCode.SKILL_PACKAGE_INVALID,
                f"skill package must contain SKILL.md at the root: {source}",
            )
        metadata = parse_skill_md(skill_path.read_text(encoding="utf-8"))
        files = build_file_list(source)
        digest = package_digest(files)
        capability_id = canonical_capability_id(metadata.name)
        version = derive_version(metadata)
        transformations = transformations_for(metadata, capability_id)

        # 2. Idempotency / conflict on existing version.
        existing = self._capabilities.get_version(capability_id, version)
        if existing is not None:
            if existing.content_digest == f"sha256:{digest}":
                return IngestionResult(
                    capability_id=capability_id,
                    version=version,
                    capability_created=False,
                    already_ingested=True,
                    raw_snapshot_digest=f"sha256:{digest}",
                    package_digest=f"sha256:{digest}",
                    file_count=len(files),
                    transformations=transformations,
                )
            raise DomainError(
                ErrorCode.CAPABILITY_ALREADY_EXISTS,
                f"version {capability_id}@{version} exists with different content; "
                "bump the version instead of mutating a published one",
            )

        # 3. Store blobs first: DB rows reference digests, never the reverse.
        for f in files:
            self._objects.put(f.sha256, (source / f.path).read_bytes())

        # 4. Canonical records.
        capability_created = self._capabilities.get_capability(capability_id) is None
        if capability_created:
            self._capabilities.create_capability(
                Capability(id=capability_id, kind="skill", created_at=ingested_at)
            )
        self._capabilities.create_version(
            CapabilityVersion(
                capability_id=capability_id,
                version=version,
                kind="skill",
                content_digest=f"sha256:{digest}",
                created_at=ingested_at,
                display_name=metadata.name,
                description=metadata.description,
                spec=SkillSpec(
                    entrypoint="SKILL.md",
                    artifacts=[f.path for f in files],
                    provides=list(metadata.provides),
                ),
            )
        )
        self._artifacts.put_artifact(
            CapabilityArtifact(
                capability_id=capability_id,
                version=version,
                package_digest=f"sha256:{digest}",
                manifest={
                    "name": metadata.name,
                    "description": metadata.description,
                    "license": metadata.license,
                    "entrypoint": "SKILL.md",
                },
                files=files,
            )
        )
        # 5. Quarantine: raw channel pointer marks unreviewed content (§21).
        self._releases.set_release(
            CapabilityRelease(
                capability_id=capability_id,
                version=version,
                channel="raw",
                status="active",
                policy_snapshot_id=None,
            )
        )
        # 6. Provenance trail (§23).
        self._source_records.add_source_record(
            SourceProvenance(
                record_id=f"src-{uuid.uuid4().hex[:12]}",
                capability_id=capability_id,
                version=version,
                source_type="local-directory",
                source_repository=source_repository,
                source_path=str(source),
                source_url_reference=source_url_reference,
                commit_sha=commit_sha,
                source_version=source_version,
                raw_snapshot_digest=f"sha256:{digest}",
                license_identifier=license_identifier or metadata.license,
                ingested_at=ingested_at,
                ingestion_tool_version=TOOL_VERSION,
                local_transformations=transformations,
                derived_from=derived_from,
            )
        )
        return IngestionResult(
            capability_id=capability_id,
            version=version,
            capability_created=capability_created,
            already_ingested=False,
            raw_snapshot_digest=f"sha256:{digest}",
            package_digest=f"sha256:{digest}",
            file_count=len(files),
            transformations=transformations,
        )
