"""Minimal-sufficient bundle composer (plan §19; ADR-008).

Optimizes for minimal sufficient capability context, not maximum count: takes
the resolver's selection in rank order, enforces the client's item and
context-token budgets, and pins every item to an exact version + digest
(§52: version pinning, digest pinning). A zero-item bundle is a normal,
successful result (§19.1, §2.7) — the composer never pads.
"""

from datetime import datetime
from math import ceil
from uuid import uuid4

from aci.domain.capability.models import (
    BundleBudget,
    BundleItem,
    CapabilityBundle,
    RouteCapabilitiesCommand,
)
from aci.domain.routing.models import ResolutionResult

DEFAULT_ITEM_TOKENS = 1000  # fallback estimate when no trusted doc text exists
CHARS_PER_TOKEN = 4  # rough deterministic estimate for the trusted summary


def estimated_tokens(document_text: str) -> int:
    """Deterministic context-cost estimate from the trusted routing document."""
    if not document_text:
        return DEFAULT_ITEM_TOKENS
    return max(1, ceil(len(document_text) / CHARS_PER_TOKEN))


class MinimalBundleComposer:
    """Implements the BundleComposer protocol (§44)."""

    def compose(
        self,
        resolution: ResolutionResult,
        command: RouteCapabilitiesCommand,
        *,
        route_run_id: str,
        now: datetime,
    ) -> CapabilityBundle:
        items: list[BundleItem] = []
        spent = 0
        for resolved in resolution.selected:
            if len(items) >= command.max_items:
                break  # budget: max items reached
            cost = estimated_tokens(resolved.document_text)
            if spent + cost > command.max_context_tokens:
                break  # budget: context cost exceeded
            items.append(
                BundleItem(
                    capability_id=resolved.candidate.capability_id,
                    version=resolved.candidate.version,
                    digest=resolved.candidate.digest,
                    kind=resolved.candidate.kind,
                    role=resolved.role,
                    load_mode="lazy",
                    reason_code=resolved.reason_code,
                )
            )
            spent += cost

        return CapabilityBundle(
            bundle_id=f"bun_{uuid4().hex}",
            route_run_id=route_run_id,
            created_at=now,
            items=items,
            execution_order=[item.capability_id for item in items],
            budget=BundleBudget(
                max_items=command.max_items,
                max_context_tokens=command.max_context_tokens,
            ),
        )
