"""Eligibility engine (plan §15; ADR-009; crawl.md §26-27 canary).

Pure rule evaluation over annotated candidates — no DB, no LLM. Runs BEFORE
retrieval and rerank; the reranker can never rescue an excluded candidate.
Every exclusion carries a stable ErrorCode reason for route traces (§14, §45).

Canary (crawl.md §27): a release in status `canary` routes only its
`canary_percent` share of eligible traffic. The split is DETERMINISTIC —
hash(request_id, capability_id) — so the same request always reaches the
same decision (no flapping), different requests spread evenly, and the
canary population is reproducible for telemetry comparison.
"""

import hashlib

from aci.domain.capability.errors import ErrorCode
from aci.domain.capability.models import CapabilityKind
from aci.domain.policy.models import (
    EligibilityDecision,
    EligibleCandidate,
    Exclusion,
    PolicyRules,
    RoutingRequestContext,
)


def _canary_selected(request_id: str, capability_id: str, percent: int) -> bool:
    """Deterministic canary split: hash(request, capability) % 100 < percent.

    Same request → same decision, always (§27 reproducible canary
    population); different requests spread ~evenly across the range.
    """
    digest = hashlib.sha256(f"{request_id}:{capability_id}".encode()).hexdigest()
    return int(digest[:8], 16) % 100 < percent


class DefaultEligibilityPolicy:
    """Implements the EligibilityPolicy protocol (plan §44)."""

    def filter(
        self,
        candidates: list[EligibleCandidate],
        context: RoutingRequestContext,
        rules: PolicyRules,
        *,
        allowed_kinds: list[CapabilityKind],
    ) -> EligibilityDecision:
        kept: list[EligibleCandidate] = []
        excluded: list[Exclusion] = []
        allowed = set(allowed_kinds)

        for candidate in candidates:
            reason = self._first_failure(candidate, context, rules, allowed)
            if reason is None:
                kept.append(candidate)
            else:
                code, detail = reason
                excluded.append(
                    Exclusion(
                        capability_id=candidate.capability_id,
                        version=candidate.version,
                        reason=code.value,
                        detail=detail,
                    )
                )
        return EligibilityDecision(kept=kept, excluded=excluded)

    def _first_failure(
        self,
        candidate: EligibleCandidate,
        context: RoutingRequestContext,
        rules: PolicyRules,
        allowed_kinds: set[CapabilityKind],
    ) -> tuple[ErrorCode, str] | None:
        # 1. Lifecycle: active releases route fully; canary releases route
        #    their deterministic percentage share (§27); everything else
        #    is excluded as before.
        if candidate.status == "canary":
            percent = candidate.canary_percent if candidate.canary_percent is not None else 100
            if not _canary_selected(context.request_id, candidate.capability_id, percent):
                return (
                    ErrorCode.CAPABILITY_NOT_ELIGIBLE,
                    f"canary {percent}%: request not in the canary population",
                )
        elif candidate.status != "active":
            return (
                ErrorCode.CAPABILITY_REVOKED
                if candidate.status == "revoked"
                else ErrorCode.CAPABILITY_NOT_ELIGIBLE,
                f"release status={candidate.status}",
            )
        if candidate.channel != rules.required_channel:
            return (
                ErrorCode.CAPABILITY_NOT_ELIGIBLE,
                f"channel={candidate.channel} != required={rules.required_channel}",
            )

        # 2. Explicit deny rule (§15).
        if candidate.capability_id in rules.denied_capability_ids:
            return (ErrorCode.POLICY_DENIED, "explicit deny rule")

        # 3. Kind filter from the route command.
        if candidate.kind not in allowed_kinds:
            return (ErrorCode.CAPABILITY_NOT_ELIGIBLE, f"kind={candidate.kind} not allowed")

        # 4. Trust tier (§15).
        if not rules.trust_ok(candidate.trust_tier):
            return (
                ErrorCode.CAPABILITY_NOT_ELIGIBLE,
                f"trust_tier={candidate.trust_tier} < min={rules.min_trust_tier}",
            )

        # 5. License restriction (defense-in-depth; promotion gates first, §24).
        #    Deliberately fail-closed on EVERY channel, not just production:
        #    §15 lists "license restriction" as an unqualified exclusion reason.
        #    Non-production content is reviewed through the control plane, not
        #    by routing around the license gate.
        if candidate.license_blocked:
            return (ErrorCode.POLICY_DENIED, "license blocks redistribution")

        # 6. Scope authorization (§27).
        scope_error = self._scope_failure(candidate, context)
        if scope_error is not None:
            return scope_error

        # 7. Client compatibility (§48): a filter, never a preference.
        compat_error = self._compatibility_failure(candidate, context)
        if compat_error is not None:
            return compat_error

        return None

    def _scope_failure(
        self, candidate: EligibleCandidate, context: RoutingRequestContext
    ) -> tuple[ErrorCode, str] | None:
        scope = candidate.owner_scope
        if scope == "global":
            return None
        # Fail closed: a scoped candidate without its scope id must never match
        # a request that also lacks one (None == None would be fail-open, §27).
        if candidate.owner_scope_id is None:
            return (ErrorCode.PERMISSION_DENIED, f"{scope} scope missing owner_scope_id")
        if scope == "organization":
            if context.scope.organization_id == candidate.owner_scope_id:
                return None
            return (ErrorCode.PERMISSION_DENIED, "organization scope mismatch")
        if scope == "workspace":
            if context.scope.workspace_id == candidate.owner_scope_id:
                return None
            return (ErrorCode.PERMISSION_DENIED, "workspace scope mismatch")
        # private
        if context.scope.principal_id == candidate.owner_scope_id:
            return None
        return (ErrorCode.PERMISSION_DENIED, "private scope mismatch")

    def _compatibility_failure(
        self, candidate: EligibleCandidate, context: RoutingRequestContext
    ) -> tuple[ErrorCode, str] | None:
        compat = candidate.compatibility
        if compat is None:
            return None
        if (
            compat.supported_clients is not None
            and context.client.type not in compat.supported_clients
        ):
            return (
                ErrorCode.CLIENT_INCOMPATIBLE,
                f"client type {context.client.type!r} not in supported_clients",
            )
        missing = [
            f for f in compat.minimum_client_features if f not in context.client.supported_features
        ]
        if missing:
            return (
                ErrorCode.CLIENT_INCOMPATIBLE,
                f"missing client features: {missing}",
            )
        # §15: unsupported language/framework excludes before rerank. Only a
        # declared incompatibility excludes — an undeclared task language stays
        # eligible (the facet filter narrows further down the pipeline).
        if compat.supported_languages and context.task.language is not None:
            if context.task.language not in compat.supported_languages:
                return (
                    ErrorCode.CAPABILITY_NOT_ELIGIBLE,
                    f"language {context.task.language!r} not in supported_languages",
                )
        if compat.supported_frameworks and context.task.frameworks:
            if not set(context.task.frameworks) & set(compat.supported_frameworks):
                return (
                    ErrorCode.CAPABILITY_NOT_ELIGIBLE,
                    f"frameworks {context.task.frameworks} disjoint with supported_frameworks",
                )
        return None
