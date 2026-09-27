"""Unit tests for dependency resolution and bundle composition (plan §§18, 19)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aci.domain.capability.models import (
    BundleItem,
    CapabilityBundle,
    CapabilityRelation,
    RouteCapabilitiesCommand,
    version_matches,
)
from aci.domain.policy.models import EligibleCandidate
from aci.domain.routing.models import (
    RankedCandidate,
    ResolutionResult,
    ResolutionTrace,
    ResolvedItem,
)
from aci.routing.composer import MinimalBundleComposer, estimated_tokens
from aci.routing.dependencies import DefaultDependencyResolver

DIGEST = "sha256:" + "ab" * 32
NOW = datetime(2026, 9, 28, tzinfo=UTC)


def candidate(capability_id: str, version: str = "1.0.0") -> EligibleCandidate:
    return EligibleCandidate(
        capability_id=capability_id,
        version=version,
        digest=DIGEST,
        kind="skill",
        channel="production",
        status="active",
    )


def ranked(capability_id: str, rank: int, *, version: str = "1.0.0") -> RankedCandidate:
    return RankedCandidate(
        candidate=candidate(capability_id, version),
        score=1.0 - 0.1 * rank,
        retrieval_score=1.0 - 0.1 * rank,
        rank=rank,
    )


def relation(
    source: str,
    relation_type: str,
    target: str,
    *,
    source_constraint: str | None = None,
    target_constraint: str | None = None,
) -> CapabilityRelation:
    return CapabilityRelation(
        relation_id=f"rel-{source}-{relation_type}-{target}",
        source_capability_id=source,
        source_version_constraint=source_constraint,
        target_capability_id=target,
        target_version_constraint=target_constraint,
        relation=relation_type,  # type: ignore[arg-type]
    )


class FakeRelations:
    def __init__(self) -> None:
        self._by_source: dict[str, list[CapabilityRelation]] = {}

    def put(self, item: CapabilityRelation) -> None:
        self._by_source.setdefault(item.source_capability_id, []).append(item)

    def list_relations(self, source_capability_id: str) -> list[CapabilityRelation]:
        return self._by_source.get(source_capability_id, [])


class FakeReleases:
    def __init__(self) -> None:
        self._releases: dict[
            tuple[str, str], tuple[str, str]
        ] = {}  # (cap, channel) -> (version, status)

    def set_active(self, capability_id: str, version: str, channel: str = "production") -> None:
        self._releases[(capability_id, channel)] = (version, "active")

    def get_release(self, capability_id: str, channel: str):  # type: ignore[no-untyped-def]
        from aci.domain.capability.models import CapabilityRelease

        entry = self._releases.get((capability_id, channel))
        if entry is None:
            return None
        version, status = entry
        return CapabilityRelease(
            capability_id=capability_id, version=version, channel=channel, status=status
        )


def make_resolver(relations: FakeRelations, releases: FakeReleases) -> DefaultDependencyResolver:
    return DefaultDependencyResolver(
        relations=relations,  # type: ignore[arg-type]
        releases=releases,  # type: ignore[arg-type]
    )


# ---------- version constraints (§18) ----------


def test_version_matches_v1_semantics() -> None:
    assert version_matches("1.0.0", None) is True  # None = any version
    assert version_matches("1.0.0", "1.0.0") is True
    assert version_matches("1.0.0", "2.0.0") is False


# ---------- resolver (§18) ----------


def test_conflict_drops_lower_ranked_either_direction() -> None:
    relations = FakeRelations()
    relations.put(relation("a", "conflicts_with", "b"))
    resolver = make_resolver(relations, FakeReleases())

    result = resolver.resolve([ranked("a", 1), ranked("b", 2)])
    assert [i.candidate.capability_id for i in result.selected] == ["a"]
    assert result.selected[0].role == "primary"
    assert [d.capability_id for d in result.dropped] == ["b"]
    assert result.dropped[0].reason == "CONFLICTS_WITH"

    # Reverse direction: b declares the conflict — b still loses (lower rank).
    relations2 = FakeRelations()
    relations2.put(relation("b", "conflicts_with", "a"))
    result2 = make_resolver(relations2, FakeReleases()).resolve([ranked("a", 1), ranked("b", 2)])
    assert [i.candidate.capability_id for i in result2.selected] == ["a"]


def test_conflict_respects_version_constraints() -> None:
    relations = FakeRelations()
    relations.put(relation("a", "conflicts_with", "b", source_constraint="2.0.0"))
    resolver = make_resolver(relations, FakeReleases())
    # a is 1.0.0, the conflict only applies to a@2.0.0 → both survive.
    result = resolver.resolve([ranked("a", 1), ranked("b", 2)])
    assert len(result.selected) == 2
    assert result.dropped == []


def test_requires_unresolved_drops_dependent() -> None:
    relations = FakeRelations()
    relations.put(relation("a", "requires", "missing"))
    resolver = make_resolver(relations, FakeReleases())
    result = resolver.resolve([ranked("a", 1)])
    assert result.selected == []
    assert result.dropped[0].reason == "REQUIRES_UNRESOLVED"
    assert "missing" in result.dropped[0].detail


def test_requires_active_production_target_keeps_dependent() -> None:
    relations = FakeRelations()
    relations.put(relation("a", "requires", "dep"))
    releases = FakeReleases()
    releases.set_active("dep", "1.0.0")
    result = make_resolver(relations, releases).resolve([ranked("a", 1)])
    assert [i.candidate.capability_id for i in result.selected] == ["a"]


def test_requires_constraint_mismatch_drops_dependent() -> None:
    relations = FakeRelations()
    relations.put(relation("a", "requires", "dep", target_constraint="2.0.0"))
    releases = FakeReleases()
    releases.set_active("dep", "1.0.0")  # production is at 1.0.0, needs 2.0.0
    result = make_resolver(relations, releases).resolve([ranked("a", 1)])
    assert result.selected == []
    assert result.dropped[0].reason == "REQUIRES_UNRESOLVED"


def test_requires_inactive_release_drops_dependent() -> None:
    relations = FakeRelations()
    relations.put(relation("a", "requires", "dep"))
    releases = FakeReleases()
    releases._releases[("dep", "production")] = ("1.0.0", "revoked")
    result = make_resolver(relations, releases).resolve([ranked("a", 1)])
    assert result.selected == []
    assert result.dropped[0].reason == "REQUIRES_UNRESOLVED"


def test_checks_reroles_selected_target_but_never_pulls_new() -> None:
    relations = FakeRelations()
    relations.put(relation("a", "checks", "b"))
    resolver = make_resolver(relations, FakeReleases())
    result = resolver.resolve([ranked("a", 1), ranked("b", 2)])
    roles = {i.candidate.capability_id: i.role for i in result.selected}
    assert roles == {"a": "primary", "b": "check"}
    assert result.selected[1].reason_code == "checks-relation"

    # Target not in the ranked set → nothing added, nothing dropped (§19 minimal).
    relations2 = FakeRelations()
    relations2.put(relation("a", "checks", "elsewhere"))
    result2 = make_resolver(relations2, FakeReleases()).resolve([ranked("a", 1)])
    assert [i.candidate.capability_id for i in result2.selected] == ["a"]
    assert result2.dropped == []


def test_primary_keeps_role_even_when_checked() -> None:
    relations = FakeRelations()
    relations.put(relation("b", "checks", "a"))
    resolver = make_resolver(relations, FakeReleases())
    result = resolver.resolve([ranked("a", 1), ranked("b", 2)])
    roles = {i.candidate.capability_id: i.role for i in result.selected}
    assert roles == {"a": "primary", "b": "support"}


def test_resolver_empty_input_is_valid() -> None:
    result = make_resolver(FakeRelations(), FakeReleases()).resolve([])
    assert result.selected == [] and result.dropped == []
    assert result.trace.input_count == 0
    assert result.trace.selected_count == 0
    assert result.trace.dropped_count == 0


# ---------- composer (§19) ----------


def resolution(*items: tuple[str, str, str]) -> ResolutionResult:
    """items = (capability_id, role, document_text)."""
    selected = [
        ResolvedItem(candidate=candidate(cap), role=role, reason_code="x", document_text=text)  # type: ignore[arg-type]
        for cap, role, text in items
    ]
    return ResolutionResult(
        selected=selected,
        trace=ResolutionTrace(
            input_count=len(selected), selected_count=len(selected), dropped_count=0
        ),
    )


def compose(resolution_result: ResolutionResult, **command: object) -> CapabilityBundle:
    data: dict = {"task_text": "do the thing"}
    data.update(command)
    return MinimalBundleComposer().compose(
        resolution_result,
        RouteCapabilitiesCommand.model_validate(data),
        route_run_id="route_test",
        now=NOW,
    )


def test_compose_empty_bundle_is_valid_success() -> None:
    bundle = compose(resolution())
    assert bundle.items == []
    assert bundle.execution_order == []
    assert bundle.bundle_id.startswith("bun_")
    assert bundle.budget is not None
    assert bundle.budget.max_items == 5
    assert bundle.budget.max_context_tokens == 6000


def test_compose_pins_version_and_digest() -> None:
    bundle = compose(resolution(("cap-a", "primary", "some text")))
    (item,) = bundle.items
    assert item.capability_id == "cap-a"
    assert item.version == "1.0.0"
    assert item.digest == DIGEST
    assert item.load_mode == "lazy"
    assert bundle.execution_order == ["cap-a"]


def test_compose_truncates_to_max_items() -> None:
    bundle = compose(
        resolution(("a", "primary", "t"), ("b", "support", "t"), ("c", "support", "t")),
        max_items=2,
    )
    assert [i.capability_id for i in bundle.items] == ["a", "b"]
    assert bundle.execution_order == ["a", "b"]
    assert bundle.budget is not None and bundle.budget.max_items == 2


def test_compose_enforces_context_budget() -> None:
    # 400 chars → 100 tokens each; budget 150 → only one fits.
    bundle = compose(
        resolution(("a", "primary", "x" * 400), ("b", "support", "y" * 400)),
        max_context_tokens=150,
    )
    assert [i.capability_id for i in bundle.items] == ["a"]
    assert bundle.budget is not None and bundle.budget.max_context_tokens == 150


def test_compose_zero_max_items_is_empty() -> None:
    bundle = compose(resolution(("a", "primary", "t")), max_items=0)
    assert bundle.items == []


def test_bundle_schema_rejects_more_than_five_items() -> None:
    items = [
        BundleItem(capability_id=f"c-{i}", version="1.0.0", digest=DIGEST, kind="skill")
        for i in range(6)
    ]
    with pytest.raises(ValidationError):
        CapabilityBundle(bundle_id="bun_x", route_run_id="route_x", created_at=NOW, items=items)


def test_estimated_tokens_is_deterministic() -> None:
    assert estimated_tokens("") == 1000  # fallback when no trusted doc
    assert estimated_tokens("x" * 400) == 100
    assert estimated_tokens("x" * 401) == 101
    assert estimated_tokens("ab") == 1
