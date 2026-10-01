"""Dependency resolution over ranked candidates (plan §18; ADR-009 spirit).

V1 implements only REQUIRES / CONFLICTS_WITH / CHECKS (§18 explicit subset).
Semantics, applied in rank order so results are deterministic:

- CONFLICTS_WITH: when two surviving candidates conflict (either direction,
  constraints permitting), the lower-ranked one is dropped. The reranker's
  ordering is authoritative; resolution never reorders, only drops.
- REQUIRES: a candidate whose required target has no active production
  release (or whose version does not satisfy the constraint) is dropped —
  a bundle must never claim a capability whose dependencies are invalid.
  V1 validates existence; it does not auto-add dependencies to the bundle.
- CHECKS: re-roles an already-selected target as ``check``. It never pulls in
  new candidates (§19: minimal sufficient context, not maximum count).
"""

from aci.application.protocols import RelationRepository, ReleaseRepository
from aci.domain.capability.models import CapabilityRelation, version_matches
from aci.domain.routing.models import (
    RankedCandidate,
    ResolutionDrop,
    ResolutionResult,
    ResolutionTrace,
    ResolvedItem,
)


class _RelationsMemo:
    """Per-resolve memo of ``list_relations``: the conflict check is pairwise,
    so without it one route issued O(candidates²) relation queries (measured:
    930 of 943 SQL statements per /v1/routes — 1 s on localhost, ~16 s over a
    3 ms network). One lookup per distinct capability id; results identical.
    Scoped to ONE resolve() call (the resolver is shared across requests)."""

    def __init__(self, relations: RelationRepository) -> None:
        self._relations = relations
        self._cache: dict[str, list[CapabilityRelation]] = {}

    def list_relations(self, source_capability_id: str) -> list[CapabilityRelation]:
        cached = self._cache.get(source_capability_id)
        if cached is None:
            cached = self._relations.list_relations(source_capability_id)
            self._cache[source_capability_id] = cached
        return cached


class DefaultDependencyResolver:
    """Implements the DependencyResolver protocol (§44)."""

    def __init__(self, relations: RelationRepository, releases: ReleaseRepository) -> None:
        self._relations = relations
        self._releases = releases

    def resolve(self, ranked: list[RankedCandidate]) -> ResolutionResult:
        relations = _RelationsMemo(self._relations)
        dropped: list[ResolutionDrop] = []

        # 1. Conflicts: keep rank order, drop the lower-ranked side.
        survivors: list[RankedCandidate] = []
        for item in ranked:
            conflict = self._conflict_with_any(item, survivors, relations)
            if conflict is not None:
                dropped.append(
                    ResolutionDrop(
                        capability_id=item.candidate.capability_id,
                        version=item.candidate.version,
                        reason="CONFLICTS_WITH",
                        detail=conflict,
                    )
                )
            else:
                survivors.append(item)

        # 2. REQUIRES: every applicable requirement needs an active production
        #    release at a satisfying version, else the dependent is dropped.
        kept: list[RankedCandidate] = []
        for item in survivors:
            unresolved = self._unresolved_requirement(item, relations)
            if unresolved is not None:
                dropped.append(
                    ResolutionDrop(
                        capability_id=item.candidate.capability_id,
                        version=item.candidate.version,
                        reason="REQUIRES_UNRESOLVED",
                        detail=unresolved,
                    )
                )
            else:
                kept.append(item)

        # 3. Roles: rank 1 is primary, others support until a CHECKS relation
        #    re-roles them as check.
        selected: list[ResolvedItem] = [
            ResolvedItem(
                candidate=item.candidate,
                role="primary" if position == 0 else "support",
                reason_code="top-ranked" if position == 0 else "ranked-support",
                document_text=item.document_text,
            )
            for position, item in enumerate(kept)
        ]
        selected = self._apply_checks_roles(selected, relations)

        return ResolutionResult(
            selected=selected,
            dropped=dropped,
            trace=ResolutionTrace(
                input_count=len(ranked),
                selected_count=len(selected),
                dropped_count=len(dropped),
            ),
        )

    def _conflict_with_any(
        self, item: RankedCandidate, survivors: list[RankedCandidate], relations: _RelationsMemo
    ) -> str | None:
        for other in survivors:
            if self._conflicts(other, item, relations) is not None:
                return f"conflicts with higher-ranked {other.candidate.capability_id}"
        return None

    def _conflicts(
        self, a: RankedCandidate, b: RankedCandidate, relations: _RelationsMemo
    ) -> str | None:
        """Conflict between ``a`` (higher ranked) and ``b``, either direction."""
        for relation in relations.list_relations(a.candidate.capability_id):
            if relation.relation != "conflicts_with":
                continue
            if relation.target_capability_id != b.candidate.capability_id:
                continue
            if not version_matches(a.candidate.version, relation.source_version_constraint):
                continue
            if not version_matches(b.candidate.version, relation.target_version_constraint):
                continue
            return f"{a.candidate.capability_id} conflicts_with {b.candidate.capability_id}"
        for relation in relations.list_relations(b.candidate.capability_id):
            if relation.relation != "conflicts_with":
                continue
            if relation.target_capability_id != a.candidate.capability_id:
                continue
            if not version_matches(b.candidate.version, relation.source_version_constraint):
                continue
            if not version_matches(a.candidate.version, relation.target_version_constraint):
                continue
            return f"{b.candidate.capability_id} conflicts_with {a.candidate.capability_id}"
        return None

    def _unresolved_requirement(
        self, item: RankedCandidate, relations: _RelationsMemo
    ) -> str | None:
        for relation in relations.list_relations(item.candidate.capability_id):
            if relation.relation != "requires":
                continue
            if not version_matches(item.candidate.version, relation.source_version_constraint):
                continue  # relation does not apply to this version
            release = self._releases.get_release(relation.target_capability_id, "production")
            if (
                release is None
                or release.status != "active"
                or not version_matches(release.version, relation.target_version_constraint)
            ):
                return (
                    f"requires {relation.target_capability_id}"
                    f"{self._constraint_suffix(relation.target_version_constraint)}"
                    " without an active production release"
                )
        return None

    def _apply_checks_roles(
        self, selected: list[ResolvedItem], relations: _RelationsMemo
    ) -> list[ResolvedItem]:
        by_id = {item.candidate.capability_id: item for item in selected}
        updated = {item.candidate.capability_id: item for item in selected}
        for item in selected:
            for relation in relations.list_relations(item.candidate.capability_id):
                if relation.relation != "checks":
                    continue
                if not version_matches(item.candidate.version, relation.source_version_constraint):
                    continue
                target = by_id.get(relation.target_capability_id)
                if target is None:
                    continue  # never pulls in new candidates (§19)
                if target.role == "primary":
                    continue  # the primary keeps its role
                if not version_matches(
                    target.candidate.version, relation.target_version_constraint
                ):
                    continue
                updated[target.candidate.capability_id] = target.model_copy(
                    update={"role": "check", "reason_code": "checks-relation"}
                )
        # Preserve the original (rank) order.
        return [updated[item.candidate.capability_id] for item in selected]

    @staticmethod
    def _constraint_suffix(constraint: str | None) -> str:
        return f"@{constraint}" if constraint is not None else ""
