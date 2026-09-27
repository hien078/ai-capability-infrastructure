"""Resolve a capability to a pinned version + artifact (plan §12, §43)."""

from aci.application.protocols import (
    ArtifactStore,
    CapabilityRepository,
)
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import Capability, CapabilityVersion
from aci.domain.routing.models import ResolvedVersion


class ResolveCapabilityService:
    def __init__(self, capabilities: CapabilityRepository, artifacts: ArtifactStore) -> None:
        self._capabilities = capabilities
        self._artifacts = artifacts

    def get_capability(self, capability_id: str) -> Capability:
        capability = self._capabilities.get_capability(capability_id)
        if capability is None:
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"unknown {capability_id}")
        return capability

    def list_versions(self, capability_id: str) -> list[CapabilityVersion]:
        self.get_capability(capability_id)  # 404 before an empty list
        return self._capabilities.list_versions(capability_id)

    def resolve(self, capability_id: str, version: str) -> ResolvedVersion:
        pinned = self._capabilities.get_version(capability_id, version)
        if pinned is None:
            raise DomainError(
                ErrorCode.CAPABILITY_VERSION_NOT_FOUND,
                f"unknown {capability_id}@{version}",
            )
        return ResolvedVersion(
            version=pinned,
            artifact=self._artifacts.get_artifact(capability_id, version),
        )
