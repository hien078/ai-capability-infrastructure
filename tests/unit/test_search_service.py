"""Phase 9 unit acceptance: search (§11.2) + resolve (§12) services."""

from datetime import UTC, datetime
from typing import Any

import pytest

from aci.application.resolve_capability import ResolveCapabilityService
from aci.application.search_capabilities import SearchCapabilitiesService
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import (
    CapabilityArtifact,
    CapabilityVersion,
    SearchCapabilitiesQuery,
    SkillSpec,
)
from aci.domain.policy.models import (
    ClientDescriptor,
    EligibleCandidate,
    RoutingRequestContext,
    ScopeContext,
)
from aci.domain.routing.models import RetrievalResult, RetrievalTrace, ScoredCandidate

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def candidate(cid: str, *, kind: str = "skill", domain: str = "software-engineering") -> Any:
    return EligibleCandidate(
        capability_id=cid,
        version="1.0.0",
        digest=f"sha256:{'b' * 64}",
        kind=kind,  # type: ignore[arg-type]
        facets={"domain": [domain]},
        channel="production",
        status="active",
    )


def version(cid: str, name: str = "", description: str = "") -> CapabilityVersion:
    return CapabilityVersion(
        capability_id=cid,
        version="1.0.0",
        kind="skill",
        content_digest=f"sha256:{'b' * 64}",
        created_at=NOW,
        display_name=name,
        description=description,
        spec=SkillSpec(),
    )


class FakeLoader:
    def __init__(self, candidates: list[Any]) -> None:
        self.candidates = candidates

    def load(self, channel: str = "production", *, status: Any = None) -> list[Any]:
        return list(self.candidates)


class FakeRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query: str, eligible: list[Any], *, limit: int = 30) -> RetrievalResult:
        self.calls.append((query, limit))
        return RetrievalResult(
            candidates=[ScoredCandidate(candidate=c, score=0.9) for c in eligible],
            trace=RetrievalTrace(
                eligible_count=len(eligible),
                indexed_count=0,
                searched_count=len(eligible),
                returned_count=len(eligible),
                limit=limit,
                model_id="fake",
            ),
        )


class FakeCapabilities:
    def __init__(self) -> None:
        self.capabilities: dict[str, Any] = {}
        self.versions: dict[tuple[str, str], CapabilityVersion] = {}

    def get_capability(self, capability_id: str) -> Any:
        return self.capabilities.get(capability_id)

    def get_version(self, capability_id: str, version: str) -> CapabilityVersion | None:
        return self.versions.get((capability_id, version))

    def list_versions(self, capability_id: str) -> list[CapabilityVersion]:
        return [v for (cid, _), v in self.versions.items() if cid == capability_id]


class FakeArtifacts:
    def __init__(self) -> None:
        self.artifacts: dict[tuple[str, str], CapabilityArtifact] = {}

    def get_artifact(self, capability_id: str, version: str) -> CapabilityArtifact | None:
        return self.artifacts.get((capability_id, version))


def ctx() -> RoutingRequestContext:
    return RoutingRequestContext(
        client=ClientDescriptor(type="rest-client"), scope=ScopeContext(principal_id="p-1")
    )


def test_search_filters_kinds_and_domains() -> None:
    loader = FakeLoader(
        [
            candidate("cap-skill", kind="skill"),
            candidate("cap-tool", kind="tool"),
            candidate("cap-data", kind="skill", domain="data-engineering"),
        ]
    )
    retriever = FakeRetriever()
    service = SearchCapabilitiesService(loader, retriever, FakeCapabilities())

    results = service.search(
        SearchCapabilitiesQuery(kinds=["skill"], domains=["software-engineering"]), ctx()
    )
    assert [r.capability_id for r in results] == ["cap-skill"]


def test_search_with_query_uses_retrieval_ranking() -> None:
    loader = FakeLoader([candidate("cap-skill")])
    retriever = FakeRetriever()
    capabilities = FakeCapabilities()
    capabilities.versions[("cap-skill", "1.0.0")] = version(
        "cap-skill", "Debug Skill", "debug python tracebacks"
    )
    service = SearchCapabilitiesService(loader, retriever, capabilities)

    results = service.search(SearchCapabilitiesQuery(query="debug python", limit=5), ctx())
    assert retriever.calls == [("debug python", 5)]
    assert results[0].capability_id == "cap-skill"
    assert results[0].score == 0.9
    # Display fields come from the immutable version, not the annotation.
    assert results[0].display_name == "Debug Skill"
    assert results[0].description == "debug python tracebacks"
    assert results[0].digest == f"sha256:{'b' * 64}"


def test_search_without_query_lists_without_ranking() -> None:
    loader = FakeLoader([candidate("cap-a"), candidate("cap-b")])
    retriever = FakeRetriever()
    service = SearchCapabilitiesService(loader, retriever, FakeCapabilities())

    results = service.search(SearchCapabilitiesQuery(query="", limit=1), ctx())
    assert retriever.calls == []  # no vector search for a browse-style query
    assert [r.capability_id for r in results] == ["cap-a"]
    assert results[0].score == 0.0


def test_resolve_capability_not_found() -> None:
    service = ResolveCapabilityService(FakeCapabilities(), FakeArtifacts())
    with pytest.raises(DomainError) as exc:
        service.get_capability("nope")
    assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND


def test_resolve_version_not_found() -> None:
    capabilities = FakeCapabilities()
    capabilities.capabilities["cap-1"] = object()
    service = ResolveCapabilityService(capabilities, FakeArtifacts())
    with pytest.raises(DomainError) as exc:
        service.resolve("cap-1", "9.9.9")
    assert exc.value.code == ErrorCode.CAPABILITY_VERSION_NOT_FOUND


def test_resolve_returns_pinned_version_and_artifact() -> None:
    capabilities = FakeCapabilities()
    capabilities.capabilities["cap-1"] = object()
    capabilities.versions[("cap-1", "1.0.0")] = version("cap-1")
    artifacts = FakeArtifacts()
    artifacts.artifacts[("cap-1", "1.0.0")] = CapabilityArtifact(
        capability_id="cap-1",
        version="1.0.0",
        package_digest=f"sha256:{'c' * 64}",
        manifest={"files": ["SKILL.md"]},
    )
    service = ResolveCapabilityService(capabilities, artifacts)

    resolved = service.resolve("cap-1", "1.0.0")
    assert resolved.version.version == "1.0.0"
    assert resolved.artifact is not None
    assert resolved.artifact.package_digest == f"sha256:{'c' * 64}"
    assert service.list_versions("cap-1") == [capabilities.versions[("cap-1", "1.0.0")]]
