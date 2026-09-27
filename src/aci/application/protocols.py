"""Application-layer repository protocols (plan §44).

Implementations live in adapters (e.g. Postgres) and own their sessions:
no SQLAlchemy/FastAPI/protocol types may appear in this module.
"""

from typing import Protocol

from aci.domain.capability.models import (
    Capability,
    CapabilityArtifact,
    CapabilityBinding,
    CapabilityRelease,
    CapabilityVersion,
    ReleaseChannel,
)
from aci.domain.skills.models import SourceProvenance


class CapabilityRepository(Protocol):
    """One authoritative registry: identities + immutable versions + bindings."""

    def create_capability(self, capability: Capability) -> Capability: ...
    def get_capability(self, capability_id: str) -> Capability | None: ...
    def create_version(self, version: CapabilityVersion) -> CapabilityVersion: ...
    def get_version(self, capability_id: str, version: str) -> CapabilityVersion | None: ...
    def list_versions(self, capability_id: str) -> list[CapabilityVersion]: ...
    def put_binding(self, binding: CapabilityBinding) -> CapabilityBinding: ...
    def get_binding(self, binding_id: str) -> CapabilityBinding | None: ...


class ReleaseRepository(Protocol):
    """Mutable channel/state pointers over immutable versions (ADR-002)."""

    def set_release(self, release: CapabilityRelease) -> CapabilityRelease: ...
    def get_release(
        self, capability_id: str, channel: ReleaseChannel
    ) -> CapabilityRelease | None: ...
    def list_releases(self, capability_id: str) -> list[CapabilityRelease]: ...


class ArtifactStore(Protocol):
    """Immutable content-package metadata, keyed by exact version."""

    def put_artifact(self, artifact: CapabilityArtifact) -> CapabilityArtifact: ...
    def get_artifact(self, capability_id: str, version: str) -> CapabilityArtifact | None: ...


class ObjectStore(Protocol):
    """Content-addressed blob storage. Keys are SHA-256 hex digests."""

    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def exists(self, key: str) -> bool: ...


class SourceRecordRepository(Protocol):
    """Provenance trail for ingested sources (plan §23)."""

    def add_source_record(self, record: SourceProvenance) -> SourceProvenance: ...
    def list_source_records(self, capability_id: str) -> list[SourceProvenance]: ...
