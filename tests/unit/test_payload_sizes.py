"""ArtifactPayloadSizes: real entry-file sizes for the composer budget (§19.1).

Sizes come from the artifact manifest only (metadata); the entrypoint path is
honoured; anything unknown is omitted so the composer falls back (traced).
"""

from datetime import UTC, datetime

from aci.application.payload_sizes import ArtifactPayloadSizes
from aci.domain.capability.models import (
    ArtifactFile,
    CapabilityArtifact,
    CapabilityVersion,
    SkillSpec,
    ToolSpec,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)
SHA = "ab" * 32


def version(cid: str, spec: SkillSpec | ToolSpec) -> CapabilityVersion:
    return CapabilityVersion.model_validate(
        {
            "capability_id": cid,
            "version": "1.0.0",
            "kind": spec.kind,
            "content_digest": f"sha256:{SHA}",
            "spec": spec.model_dump(),
            "created_at": NOW,
        }
    )


def artifact(cid: str, files: dict[str, int]) -> CapabilityArtifact:
    return CapabilityArtifact(
        capability_id=cid,
        version="1.0.0",
        package_digest=f"sha256:{SHA}",
        files=[ArtifactFile(path=p, sha256=SHA, size_bytes=n) for p, n in files.items()],
    )


class FakeCapabilities:
    def __init__(self, versions: list[CapabilityVersion]) -> None:
        self._versions = {(v.capability_id, v.version): v for v in versions}
        self.calls = 0

    def get_versions(self, pairs: list[tuple[str, str]]) -> list[CapabilityVersion]:
        self.calls += 1
        return [self._versions[p] for p in pairs if p in self._versions]


class FakeArtifacts:
    def __init__(self, artifacts: list[CapabilityArtifact]) -> None:
        self._artifacts = {(a.capability_id, a.version): a for a in artifacts}
        self.calls = 0

    def get_artifacts(self, pairs: list[tuple[str, str]]) -> list[CapabilityArtifact]:
        self.calls += 1
        return [self._artifacts[p] for p in pairs if p in self._artifacts]


def test_entry_sizes_reads_manifest_entry_and_omits_unknowns() -> None:
    capabilities = FakeCapabilities(
        [
            version("plain", SkillSpec()),
            version("custom", SkillSpec(entrypoint="docs/MAIN.md", artifacts=["docs/MAIN.md"])),
            version("no-entry", SkillSpec()),
            version("no-artifact", SkillSpec()),
            version("tool", ToolSpec()),
        ]
    )
    artifacts = FakeArtifacts(
        [
            artifact("plain", {"SKILL.md": 41_896, "references/big.md": 900_000}),
            artifact("custom", {"SKILL.md": 10, "docs/MAIN.md": 2_000}),
            artifact("no-entry", {"README.md": 50}),
            artifact("tool", {"schema.json": 70}),
        ]
    )
    sizes = ArtifactPayloadSizes(capabilities, artifacts)  # type: ignore[arg-type]
    pairs = [
        (cid, "1.0.0") for cid in ("plain", "custom", "no-entry", "no-artifact", "tool", "unknown")
    ]
    assert sizes.entry_sizes(pairs) == {
        ("plain", "1.0.0"): 41_896,  # entry file only, not supporting files
        ("custom", "1.0.0"): 2_000,  # the declared entrypoint, not SKILL.md
    }
    assert (capabilities.calls, artifacts.calls) == (1, 1)  # bulk reads only


def test_entry_sizes_empty_input_does_no_io() -> None:
    capabilities, artifacts = FakeCapabilities([]), FakeArtifacts([])
    assert ArtifactPayloadSizes(capabilities, artifacts).entry_sizes([]) == {}  # type: ignore[arg-type]
    assert (capabilities.calls, artifacts.calls) == (0, 0)
