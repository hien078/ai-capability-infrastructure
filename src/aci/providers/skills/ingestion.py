"""Local skill ingestion: snapshot → hash → quarantine → canonical version (plan §§21-23).

Flow per ingest_local():
  1. parse SKILL.md and validate package structure (nothing is persisted on failure),
  2. store every file blob in the content-addressed object store,
  3. create capability + immutable version + artifact,
  4. append a provenance source record with the raw snapshot digest, marked
     `ingestion_status=quarantined` (§22: ingested ≠ trusted; the snapshot is
     stored untrusted and inspectable, never advertised to runtime clients),
  5. canonicalization records its transformations on the same record.

Quarantine is an INGESTION state on the provenance record, not a release
channel (§21): release channels (staging/production) only come into existence
when the ingestion gates pass and an explicit promotion moves the pointer.

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
from typing import Any

from aci.application.protocols import (
    ArtifactStore,
    CapabilityRepository,
    ObjectStore,
    ReleaseRepository,
    SourceRecordRepository,
)
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import (
    ArtifactFile,
    Capability,
    CapabilityArtifact,
    CapabilityVersion,
    IngestionStatus,
    SkillSpec,
)
from aci.domain.skills.models import IngestionResult, SkillMetadata, SourceProvenance
from aci.providers.skills.canonicalization import (
    canonical_capability_id,
    derive_version,
    transformations_for,
)
from aci.providers.skills.package import build_file_list, hash_bytes, package_digest
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
            if existing.content_digest != f"sha256:{digest}":
                raise DomainError(
                    ErrorCode.CAPABILITY_ALREADY_EXISTS,
                    f"version {capability_id}@{version} exists with different content; "
                    "bump the version instead of mutating a published one",
                )
            # Identical content: repair any records a crashed ingest left behind
            # (each write is its own transaction, §31.1), then report idempotency.
            self._ensure_blobs(source, files)
            if self._artifacts.get_artifact(capability_id, version) is None:
                self._artifacts.put_artifact(
                    CapabilityArtifact(
                        capability_id=capability_id,
                        version=version,
                        package_digest=f"sha256:{digest}",
                        manifest=self._manifest(metadata),
                        files=files,
                    )
                )
            if not any(
                r.version == version
                for r in self._source_records.list_source_records(capability_id)
            ):
                self._source_records.add_source_record(
                    self._source_record(
                        capability_id=capability_id,
                        version=version,
                        source=source,
                        digest=digest,
                        metadata=metadata,
                        transformations=transformations,
                        license_identifier=license_identifier,
                        source_repository=source_repository,
                        source_url_reference=source_url_reference,
                        commit_sha=commit_sha,
                        source_version=source_version,
                        derived_from=derived_from,
                        ingested_at=ingested_at,
                    )
                )
            existing_records = self._source_records.list_source_records(capability_id)
            existing_status: IngestionStatus = next(
                (
                    r.ingestion_status
                    for r in existing_records
                    if r.version == version and r.ingestion_status is not None
                ),
                "quarantined",
            )
            return IngestionResult(
                capability_id=capability_id,
                version=version,
                capability_created=False,
                already_ingested=True,
                raw_snapshot_digest=f"sha256:{digest}",
                package_digest=f"sha256:{digest}",
                file_count=len(files),
                ingestion_status=existing_status,
                transformations=transformations,
            )

        # 3. Store blobs first: DB rows reference digests, never the reverse.
        #    Bytes are re-read once and re-verified against the recorded hash so
        #    a concurrent writer can never store content under a foreign digest.
        for f in files:
            data = (source / f.path).read_bytes()
            if hash_bytes(data) != f.sha256:
                raise DomainError(
                    ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                    f"file {f.path} changed while ingesting; content hash mismatch",
                )
            self._objects.put(f.sha256, data)

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
                manifest=self._manifest(metadata),
                files=files,
            )
        )
        # 5. Provenance trail (§23) — the record itself carries the
        #    ingestion state (quarantined): stored, inspectable, untrusted,
        #    and NOT advertised on any release channel until gates pass.
        self._source_records.add_source_record(
            self._source_record(
                capability_id=capability_id,
                version=version,
                source=source,
                digest=digest,
                metadata=metadata,
                transformations=transformations,
                license_identifier=license_identifier,
                source_repository=source_repository,
                source_url_reference=source_url_reference,
                commit_sha=commit_sha,
                source_version=source_version,
                derived_from=derived_from,
                ingested_at=ingested_at,
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
            ingestion_status="quarantined",
            transformations=transformations,
        )

    def _ensure_blobs(self, source: Path, files: list[ArtifactFile]) -> None:
        """Repair blobs after a partial ingest: missing or corrupt content is
        re-stored from the (re-verified) source so the content-addressed
        invariant key == sha256(content) always holds (§39, §60.3)."""
        for f in files:
            stored = self._objects.get(f.sha256)
            if stored is not None and hash_bytes(stored) == f.sha256:
                continue
            data = (source / f.path).read_bytes()
            if hash_bytes(data) != f.sha256:
                raise DomainError(
                    ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                    f"file {f.path} changed while ingesting; content hash mismatch",
                )
            self._objects.put(f.sha256, data)

    @staticmethod
    def _manifest(metadata: SkillMetadata) -> dict[str, Any]:
        return {
            "name": metadata.name,
            "description": metadata.description,
            "license": metadata.license,
            "entrypoint": "SKILL.md",
        }

    @staticmethod
    def _source_record(
        *,
        capability_id: str,
        version: str,
        source: Path,
        digest: str,
        metadata: SkillMetadata,
        transformations: list[str],
        license_identifier: str | None,
        source_repository: str | None,
        source_url_reference: str | None,
        commit_sha: str | None,
        source_version: str | None,
        derived_from: str | None,
        ingested_at: datetime,
    ) -> SourceProvenance:
        return SourceProvenance(
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
