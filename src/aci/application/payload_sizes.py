"""Real payload sizes for the composer's context budget (plan §19.1).

The composer must charge each bundle item what the client actually loads — the
skill's entry file (``SKILL.md`` / ``SkillSpec.entrypoint``), not the ≤2000-char
trusted routing summary. Sizes come exclusively from the immutable artifact
manifest (``ArtifactFile.size_bytes``): this module never touches the object
store, so raw skill bodies stay structurally unreachable by routing
(§16.1/§17.1; ADR-008/009 prompt-injection boundary).
"""

from aci.application.protocols import ArtifactStore, CapabilityRepository
from aci.domain.capability.models import SkillSpec

DEFAULT_ENTRYPOINT = "SKILL.md"


class ArtifactPayloadSizes:
    """Implements ``PayloadSizeSource`` over versions + artifact manifests.

    Two bulk reads per call (versions for the entrypoint path, artifacts for
    the file list) — never per-item roundtrips. Pairs whose version, artifact,
    or entry file is missing are omitted; the composer then falls back to the
    summary estimate and records that in its trace.
    """

    def __init__(self, capabilities: CapabilityRepository, artifacts: ArtifactStore) -> None:
        self._capabilities = capabilities
        self._artifacts = artifacts

    def entry_sizes(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
        if not pairs:
            return {}
        entrypoints = {
            (v.capability_id, v.version): (
                v.spec.entrypoint if isinstance(v.spec, SkillSpec) else DEFAULT_ENTRYPOINT
            )
            for v in self._capabilities.get_versions(pairs)
        }
        sizes: dict[tuple[str, str], int] = {}
        for artifact in self._artifacts.get_artifacts(pairs):
            key = (artifact.capability_id, artifact.version)
            path = entrypoints.get(key)
            if path is None:
                continue
            entry = next((f for f in artifact.files if f.path == path), None)
            if entry is not None:
                sizes[key] = entry.size_bytes
        return sizes
