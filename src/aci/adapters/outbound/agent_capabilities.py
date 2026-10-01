"""Registry-backed ACIClient for the HarnessKernel (harness.md §11; ADR-014).

The kernel's CapabilityRuntime reaches the capability plane through the
ACIClient port (``search`` / ``resolve``). This adapter answers from the ONE
registry with the SAME §14 routing use case ``POST /v1/routes`` runs — never
a parallel selection path:

- ``search`` turns the normalized need (objective + constraints; never the
  conversation, never the model's free-text ``reason`` — §11.3) into a
  ``RouteCapabilitiesCommand`` restricted to kind ``skill`` (the only kind
  with a loadable instruction payload). Eligibility runs before retrieval
  (ADR-009), so only active production releases that pass the live policy
  come back; an empty bundle is a valid, empty result (ADR-008).
- ``resolve`` reads the pinned version's entry file (``SKILL.md``) from the
  content-addressed object store exactly like the OpenCode catalog / MCP
  skills extension, and returns the bytes with the digest OF THE BYTES READ.
  CapabilityRuntime compares it with the selection's digest (the artifact
  manifest's entry hash), so a tampered blob fails activation (§26.3).

Defense in depth for activation: one client is built PER RUN, and
``resolve`` serves only (capability_id, version) pairs this run's ``search``
selected — and only while that exact version is still the active production
release of a skill (revocation between search and resolve is honored, §46).
A forged or stale selection can never load a non-production, quarantined or
ineligible capability.

Telemetry decision: ``search`` reuses ``RouteCapabilitiesService.route()``
unchanged, so every kernel capability request persists a ``route_runs`` row +
bundle exactly like ``/v1/routes`` (client_type ``harness-kernel``, protocol
``harness``). One telemetry stream for every selection ACI makes (§36) keeps
kernel selections measurable/evaluable with the existing tooling, and the
stored ``task_text`` is only the normalized need.
"""

import hashlib
from dataclasses import dataclass
from math import ceil
from uuid import uuid4

from aci.application.protocols import (
    ArtifactStore,
    CapabilityRepository,
    ObjectStore,
    ReleaseRepository,
)
from aci.application.route_capabilities import RouteCapabilitiesService
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import (
    ArtifactFile,
    BundleItem,
    CapabilityArtifact,
    RouteCapabilitiesCommand,
    SkillSpec,
    TaskContext,
)
from aci.domain.policy.models import ClientDescriptor, ProtocolDescriptor, RequestContext
from aci.domain.runtime.actions import CapabilityRequest
from aci.providers.skills.package import package_digest
from aci.routing.composer import CHARS_PER_TOKEN
from aci.runtime.capability_runtime import ACISelection

CLIENT_TYPE = "harness-kernel"
PROTOCOL_TYPE = "harness"
#: The kernel is a service-side principal with no tenant scope: only
#: public-scope capabilities are eligible (least privilege, §15 scope rule).
DEFAULT_PRINCIPAL = "harness-kernel"
_MAX_TASK_TEXT = 8000  # RouteCapabilitiesCommand.task_text bound


@dataclass
class RegistrySelection:
    """One routed, pinned skill (satisfies the ACISelection protocol, §11.4)."""

    capability_id: str
    version: str
    payload_ref: str
    digest: str
    estimated_context_tokens: int
    route_run_id: str
    bundle_id: str


def normalized_need(request: CapabilityRequest) -> str:
    """§11.3 — the routing text is the objective + declared constraints only."""
    parts = [request.objective.strip() or request.objective]
    parts.extend(c.strip() for c in request.constraints if c.strip())
    return "\n".join(parts)[:_MAX_TASK_TEXT]


def _entry_path(version_spec: object) -> str:
    return version_spec.entrypoint if isinstance(version_spec, SkillSpec) else "SKILL.md"


def _entry_file(artifact: CapabilityArtifact, path: str) -> ArtifactFile | None:
    return next((f for f in artifact.files if f.path == path), None)


class RegistryCapabilityClient:
    """ACIClient over the live registry + §14 router. Build one per run."""

    def __init__(
        self,
        routes: RouteCapabilitiesService,
        releases: ReleaseRepository,
        capabilities: CapabilityRepository,
        artifacts: ArtifactStore,
        objects: ObjectStore,
        *,
        principal_id: str = DEFAULT_PRINCIPAL,
        max_items: int = 5,
        max_context_tokens: int = 6000,
    ) -> None:
        self._routes = routes
        self._releases = releases
        self._capabilities = capabilities
        self._artifacts = artifacts
        self._objects = objects
        self._principal_id = principal_id
        self._max_items = max_items
        self._max_context_tokens = max_context_tokens
        #: (capability_id, version) pairs this run's search selected — the
        #: only pairs resolve() will ever serve.
        self._issued: set[tuple[str, str]] = set()

    # -- search ----------------------------------------------------------------

    def search(self, request: CapabilityRequest) -> list[ACISelection]:
        if request.desired_kinds and "skill" not in request.desired_kinds:
            return []  # only skills carry a loadable payload; nothing to route
        command = RouteCapabilitiesCommand(
            task_text=normalized_need(request),
            context=TaskContext(),
            max_items=self._max_items,
            max_context_tokens=self._max_context_tokens,
            allowed_kinds=["skill"],
        )
        envelope = RequestContext(
            request_id=f"req_{uuid4().hex}",
            trace_id=f"trc_{uuid4().hex}",
            principal_id=self._principal_id,
            client=ClientDescriptor(type=CLIENT_TYPE),
            protocol=ProtocolDescriptor(type=PROTOCOL_TYPE, version="1"),
        )
        result = self._routes.route(
            command, envelope.to_routing_context(command.context), request=envelope
        )
        selections: list[ACISelection] = []
        for item in result.bundle.items:
            selection = self._select(
                item, route_run_id=result.route_run_id, bundle_id=result.bundle.bundle_id
            )
            if selection is not None:
                self._issued.add((selection.capability_id, selection.version))
                selections.append(selection)
        return selections

    def _select(
        self, item: BundleItem, *, route_run_id: str, bundle_id: str
    ) -> RegistrySelection | None:
        """Pinned bundle item → selection, binding the bundle digest to the
        entry file: content_digest == artifact.package_digest ==
        package_digest(manifest files) ∋ entry sha256."""
        if item.kind != "skill":
            return None
        version = self._capabilities.get_version(item.capability_id, item.version)
        if version is None or version.kind != "skill":
            return None
        artifact = self._artifacts.get_artifact(item.capability_id, item.version)
        if artifact is None:
            return None  # same rule as the catalog: no artifact, not loadable
        if (
            artifact.package_digest != item.digest
            or f"sha256:{package_digest(artifact.files)}" != artifact.package_digest
        ):
            raise DomainError(
                ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                f"artifact manifest for {item.capability_id}@{item.version} "
                "does not match its pinned digest",
            )
        path = _entry_path(version.spec)
        entry = _entry_file(artifact, path)
        if entry is None:
            return None
        return RegistrySelection(
            capability_id=item.capability_id,
            version=item.version,
            payload_ref=f"skill://{item.capability_id}@{item.version}/{path}",
            digest=f"sha256:{entry.sha256}",
            estimated_context_tokens=max(1, ceil(entry.size_bytes / CHARS_PER_TOKEN)),
            route_run_id=route_run_id,
            bundle_id=bundle_id,
        )

    # -- resolve ---------------------------------------------------------------

    def resolve(self, capability_id: str, version: str) -> tuple[bytes, str]:
        """(entry bytes, ``sha256:<hex>`` of those bytes). Verification is the
        caller's job (CapabilityRuntime compares against the selection)."""
        if (capability_id, version) not in self._issued:
            raise DomainError(
                ErrorCode.CAPABILITY_NOT_FOUND,
                f"{capability_id}@{version} was not selected for this run",
            )
        release = self._releases.get_release(capability_id, "production")
        if release is None or release.status != "active" or release.version != version:
            raise DomainError(
                ErrorCode.CAPABILITY_NOT_FOUND,
                f"{capability_id}@{version} is not the active production release",
            )
        pinned = self._capabilities.get_version(capability_id, version)
        if pinned is None or pinned.kind != "skill":
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"unknown skill {capability_id}")
        artifact = self._artifacts.get_artifact(capability_id, version)
        if artifact is None:
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"no artifact for {capability_id}")
        path = _entry_path(pinned.spec)
        entry = _entry_file(artifact, path)
        if entry is None:
            raise DomainError(
                ErrorCode.CAPABILITY_NOT_FOUND, f"{capability_id}@{version} has no file {path}"
            )
        data = self._objects.get(entry.sha256)
        if data is None:
            raise DomainError(
                ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                f"blob {entry.sha256} missing for {capability_id}/{path}",
            )
        return data, f"sha256:{hashlib.sha256(data).hexdigest()}"


class RegistryCapabilityClientFactory:
    """Composition-root handle: one fresh, run-scoped client per kernel run."""

    def __init__(
        self,
        routes: RouteCapabilitiesService,
        releases: ReleaseRepository,
        capabilities: CapabilityRepository,
        artifacts: ArtifactStore,
        objects: ObjectStore,
        *,
        principal_id: str = DEFAULT_PRINCIPAL,
        max_items: int = 5,
        max_context_tokens: int = 6000,
    ) -> None:
        # Bundle budgets default to the /v1/routes request defaults — no
        # kernel-specific tuning without paired DEV_CASES evidence (§34).
        self._deps = (routes, releases, capabilities, artifacts, objects)
        self._principal_id = principal_id
        self._max_items = max_items
        self._max_context_tokens = max_context_tokens

    def __call__(self) -> RegistryCapabilityClient:
        return RegistryCapabilityClient(
            *self._deps,
            principal_id=self._principal_id,
            max_items=self._max_items,
            max_context_tokens=self._max_context_tokens,
        )
