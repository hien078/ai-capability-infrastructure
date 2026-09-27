"""Postgres implementations of the application repository protocols (plan §44).

Sessions are owned here; callers only pass domain models. Published versions
have no update/delete path, and the DB trigger in migration 0002 rejects
UPDATE on capability_versions as a second line of defense.
"""

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import (
    CapabilityArtifactRow,
    CapabilityBindingRow,
    CapabilityReleaseRow,
    CapabilityRow,
    CapabilityVersionRow,
)
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import (
    Capability,
    CapabilityArtifact,
    CapabilityBinding,
    CapabilityRelease,
    CapabilityVersion,
    ReleaseChannel,
)


def _conflict(what: str) -> DomainError:
    return DomainError(ErrorCode.CAPABILITY_ALREADY_EXISTS, f"{what} already exists")


def _capability_of(row: CapabilityRow) -> Capability:
    return Capability.model_validate(
        {
            "id": row.id,
            "kind": row.kind,
            "created_at": row.created_at,
            "owner_scope": row.owner_scope,
        }
    )


def _version_of(row: CapabilityVersionRow) -> CapabilityVersion:
    data: dict[str, Any] = {
        "capability_id": row.capability_id,
        "version": row.version,
        "kind": row.kind,
        "schema_version": row.schema_version,
        "content_digest": row.content_digest,
        "created_at": row.created_at,
        "display_name": row.display_name,
        "description": row.description,
        "facets": row.facets,
        "spec": row.spec,
    }
    return CapabilityVersion.model_validate(data)


def _release_of(row: CapabilityReleaseRow) -> CapabilityRelease:
    return CapabilityRelease.model_validate(
        {
            "capability_id": row.capability_id,
            "version": row.version,
            "channel": row.channel,
            "status": row.status,
            "promoted_at": row.promoted_at,
            "approved_by": row.approved_by,
            "policy_snapshot_id": row.policy_snapshot_id,
        }
    )


def _binding_of(row: CapabilityBindingRow) -> CapabilityBinding:
    return CapabilityBinding.model_validate(
        {
            "binding_id": row.binding_id,
            "capability_id": row.capability_id,
            "version": row.version,
            "binding_type": row.binding_type,
            "visibility_scope": row.visibility_scope,
            "config": row.config,
        }
    )


def _artifact_of(row: CapabilityArtifactRow) -> CapabilityArtifact:
    return CapabilityArtifact.model_validate(
        {
            "capability_id": row.capability_id,
            "version": row.version,
            "package_digest": row.package_digest,
            "manifest": row.manifest,
            "files": row.files,
        }
    )


class SqlAlchemyCapabilityRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create_capability(self, capability: Capability) -> Capability:
        with self._sessions() as session, session.begin():
            session.add(
                CapabilityRow(
                    id=capability.id,
                    kind=capability.kind,
                    created_at=capability.created_at,
                    owner_scope=capability.owner_scope,
                )
            )
            try:
                session.flush()
            except IntegrityError as exc:
                raise _conflict(f"capability {capability.id}") from exc
        return capability

    def get_capability(self, capability_id: str) -> Capability | None:
        with self._sessions() as session:
            row = session.get(CapabilityRow, capability_id)
            return _capability_of(row) if row is not None else None

    def create_version(self, version: CapabilityVersion) -> CapabilityVersion:
        with self._sessions() as session, session.begin():
            if session.get(CapabilityRow, version.capability_id) is None:
                raise DomainError(
                    ErrorCode.CAPABILITY_NOT_FOUND,
                    f"capability {version.capability_id} not found",
                )
            session.add(
                CapabilityVersionRow(
                    capability_id=version.capability_id,
                    version=version.version,
                    kind=version.kind,
                    schema_version=version.schema_version,
                    content_digest=version.content_digest,
                    created_at=version.created_at,
                    display_name=version.display_name,
                    description=version.description,
                    facets=version.facets,
                    spec=version.spec.model_dump(),
                )
            )
            try:
                session.flush()
            except IntegrityError as exc:
                raise _conflict(f"version {version.capability_id}@{version.version}") from exc
        return version

    def get_version(self, capability_id: str, version: str) -> CapabilityVersion | None:
        with self._sessions() as session:
            row = session.get(CapabilityVersionRow, (capability_id, version))
            return _version_of(row) if row is not None else None

    def list_versions(self, capability_id: str) -> list[CapabilityVersion]:
        with self._sessions() as session:
            rows = (
                session.query(CapabilityVersionRow)
                .filter_by(capability_id=capability_id)
                .order_by(CapabilityVersionRow.version)
                .all()
            )
            return [_version_of(r) for r in rows]

    def put_binding(self, binding: CapabilityBinding) -> CapabilityBinding:
        with self._sessions() as session, session.begin():
            if session.get(CapabilityVersionRow, (binding.capability_id, binding.version)) is None:
                raise DomainError(
                    ErrorCode.CAPABILITY_VERSION_NOT_FOUND,
                    f"version {binding.capability_id}@{binding.version} not found",
                )
            row = session.get(CapabilityBindingRow, binding.binding_id)
            if row is None:
                session.add(
                    CapabilityBindingRow(
                        binding_id=binding.binding_id,
                        capability_id=binding.capability_id,
                        version=binding.version,
                        binding_type=binding.binding_type,
                        visibility_scope=binding.visibility_scope,
                        config=binding.config,
                    )
                )
            else:
                row.capability_id = binding.capability_id
                row.version = binding.version
                row.binding_type = binding.binding_type
                row.visibility_scope = binding.visibility_scope
                row.config = binding.config
        return binding

    def get_binding(self, binding_id: str) -> CapabilityBinding | None:
        with self._sessions() as session:
            row = session.get(CapabilityBindingRow, binding_id)
            return _binding_of(row) if row is not None else None


class SqlAlchemyReleaseRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def set_release(self, release: CapabilityRelease) -> CapabilityRelease:
        """Upsert the (capability_id, channel) pointer. Never touches versions."""
        with self._sessions() as session, session.begin():
            if session.get(CapabilityVersionRow, (release.capability_id, release.version)) is None:
                raise DomainError(
                    ErrorCode.CAPABILITY_VERSION_NOT_FOUND,
                    f"version {release.capability_id}@{release.version} not found",
                )
            row = session.get(CapabilityReleaseRow, (release.capability_id, release.channel))
            if row is None:
                session.add(
                    CapabilityReleaseRow(
                        capability_id=release.capability_id,
                        channel=release.channel,
                        version=release.version,
                        status=release.status,
                        promoted_at=release.promoted_at,
                        approved_by=release.approved_by,
                        policy_snapshot_id=release.policy_snapshot_id,
                    )
                )
            else:
                row.version = release.version
                row.status = release.status
                row.promoted_at = release.promoted_at
                row.approved_by = release.approved_by
                row.policy_snapshot_id = release.policy_snapshot_id
        return release

    def get_release(self, capability_id: str, channel: ReleaseChannel) -> CapabilityRelease | None:
        with self._sessions() as session:
            row = session.get(CapabilityReleaseRow, (capability_id, channel))
            return _release_of(row) if row is not None else None

    def list_releases(self, capability_id: str) -> list[CapabilityRelease]:
        with self._sessions() as session:
            rows = (
                session.query(CapabilityReleaseRow)
                .filter_by(capability_id=capability_id)
                .order_by(CapabilityReleaseRow.channel)
                .all()
            )
            return [_release_of(r) for r in rows]


class SqlAlchemyArtifactStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_artifact(self, artifact: CapabilityArtifact) -> CapabilityArtifact:
        with self._sessions() as session, session.begin():
            if (
                session.get(CapabilityVersionRow, (artifact.capability_id, artifact.version))
                is None
            ):
                raise DomainError(
                    ErrorCode.CAPABILITY_VERSION_NOT_FOUND,
                    f"version {artifact.capability_id}@{artifact.version} not found",
                )
            row = session.get(CapabilityArtifactRow, (artifact.capability_id, artifact.version))
            files = [f.model_dump() for f in artifact.files]
            if row is None:
                session.add(
                    CapabilityArtifactRow(
                        capability_id=artifact.capability_id,
                        version=artifact.version,
                        package_digest=artifact.package_digest,
                        manifest=artifact.manifest,
                        files=files,
                    )
                )
            else:
                row.package_digest = artifact.package_digest
                row.manifest = artifact.manifest
                row.files = files
        return artifact

    def get_artifact(self, capability_id: str, version: str) -> CapabilityArtifact | None:
        with self._sessions() as session:
            row = session.get(CapabilityArtifactRow, (capability_id, version))
            return _artifact_of(row) if row is not None else None
