"""Minimal-sufficient bundle composer (plan §19; ADR-008).

Optimizes for minimal sufficient capability context, not maximum count: takes
the resolver's selection in rank order, enforces the client's item and
context-token budgets, and pins every item to an exact version + digest
(§52: version pinning, digest pinning). A zero-item bundle is a normal,
successful result (§19.1, §2.7) — the composer never pads.

Context cost (§19.1) is charged on what the client actually loads: the byte
size of the skill's entry file, read from the artifact manifest through a
``PayloadSizeSource`` (sizes only — never content, so the ADR-008/009
prompt-injection boundary holds). When no size is known the composer falls
back to the trusted routing summary's length and records that per item in the
composition trace. Version history (route_runs.composer_version):
``1`` charged every item ``len(summary)/4`` — a ≤2000-char summary, so the
token budget effectively never bound; ``2`` charges real entry-file size.

Oversized candidates (cost > remaining budget) are handled by the
``oversized_policy`` constructor option: ``stop`` (default, the shipped
rule) ends composition at the first one; ``skip`` excludes only that
candidate and keeps composing later candidates that fit. Both record the
per-item ``excluded_reason``; the trace carries the policy that ran.
"""

from datetime import datetime
from math import ceil
from uuid import uuid4

from aci.application.protocols import PayloadSizeSource
from aci.domain.capability.models import (
    BundleBudget,
    BundleItem,
    CapabilityBundle,
    RouteCapabilitiesCommand,
)
from aci.domain.routing.models import (
    ComposedItemCost,
    CompositionOversizedPolicy,
    CompositionResult,
    CompositionStopReason,
    CompositionTrace,
    ResolutionResult,
    TokenEstimateSource,
)

DEFAULT_ITEM_TOKENS = 1000  # fallback estimate when no size and no trusted doc text exist
CHARS_PER_TOKEN = 4  # rough deterministic chars(bytes)-per-token estimate

IMPLEMENTATION = "minimal-bundle-composer"
VERSION = "2"


def estimated_tokens(document_text: str) -> int:
    """Fallback context-cost estimate from the trusted routing document."""
    if not document_text:
        return DEFAULT_ITEM_TOKENS
    return max(1, ceil(len(document_text) / CHARS_PER_TOKEN))


def tokens_for_bytes(size_bytes: int) -> int:
    """Context-cost estimate for a loaded payload of ``size_bytes`` bytes.

    Bytes ≥ characters for UTF-8, so this never under-charges vs. chars/4.
    """
    return max(1, ceil(size_bytes / CHARS_PER_TOKEN))


def item_cost(document_text: str, size_bytes: int | None) -> tuple[int, TokenEstimateSource]:
    """(estimated tokens, source): real entry size when known, else fallback."""
    if size_bytes is not None:
        return tokens_for_bytes(size_bytes), "artifact_entry"
    if document_text:
        return estimated_tokens(document_text), "routing_summary"
    return DEFAULT_ITEM_TOKENS, "default"


class MinimalBundleComposer:
    """Implements the BundleComposer protocol (§44).

    ``payload_sizes`` is optional so pure unit wiring still works; without it
    every item is costed by the summary fallback (visible in the trace).

    ``oversized_policy`` (ADR-008) selects what happens when a candidate's
    cost does not fit the REMAINING context budget:

    - ``stop`` (default — the shipped behaviour): composition ends at the
      first oversized candidate; every later candidate is excluded too,
      even ones that would have fit the remaining budget.
    - ``skip``: only the oversized candidate is excluded (recorded per item
      as ``excluded_reason``); later candidates that fit are still included.

    ``max_items`` and the rank order are unchanged in both policies — the
    composer takes the resolver's selection in rank order and never pads.
    """

    implementation = IMPLEMENTATION
    version = VERSION

    def __init__(
        self,
        payload_sizes: PayloadSizeSource | None = None,
        *,
        oversized_policy: CompositionOversizedPolicy = "stop",
    ) -> None:
        self._payload_sizes = payload_sizes
        self._oversized_policy = oversized_policy

    def compose(
        self,
        resolution: ResolutionResult,
        command: RouteCapabilitiesCommand,
        *,
        route_run_id: str,
        now: datetime,
    ) -> CompositionResult:
        sizes: dict[tuple[str, str], int] = {}
        if self._payload_sizes is not None and resolution.selected:
            sizes = self._payload_sizes.entry_sizes(
                [(r.candidate.capability_id, r.candidate.version) for r in resolution.selected]
            )

        items: list[BundleItem] = []
        costs: list[ComposedItemCost] = []
        spent = 0
        stop_reason: CompositionStopReason | None = None
        skipped_for_budget = False
        for resolved in resolution.selected:
            key = (resolved.candidate.capability_id, resolved.candidate.version)
            cost, source = item_cost(resolved.document_text, sizes.get(key))
            excluded_reason: CompositionStopReason | None = None
            if self._oversized_policy == "stop" and stop_reason is not None:
                # Composition already ended: every later candidate is
                # excluded, with the budget that ended it as the reason.
                excluded_reason = stop_reason
            elif len(items) >= command.max_items:
                if stop_reason is None:
                    stop_reason = "max_items"  # budget: max items reached
                excluded_reason = "max_items"
            elif spent + cost > command.max_context_tokens:
                excluded_reason = "max_context_tokens"  # budget: context cost exceeded
                if self._oversized_policy == "stop":
                    stop_reason = "max_context_tokens"
                else:
                    skipped_for_budget = True
            costs.append(
                ComposedItemCost(
                    capability_id=key[0],
                    version=key[1],
                    estimated_tokens=cost,
                    estimate_source=source,
                    included=excluded_reason is None,
                    excluded_reason=excluded_reason,
                )
            )
            if excluded_reason is not None:
                continue
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

        # In skip mode the context budget never ends composition — but if it
        # excluded anything, the trace must say so (None would read as
        # "every resolved item fit").
        if stop_reason is None and skipped_for_budget:
            stop_reason = "max_context_tokens"

        bundle = CapabilityBundle(
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
        trace = CompositionTrace(
            implementation=self.implementation,
            version=self.version,
            max_items=command.max_items,
            max_context_tokens=command.max_context_tokens,
            spent_tokens=spent,
            stop_reason=stop_reason,
            oversized_policy=self._oversized_policy,
            items=costs,
        )
        return CompositionResult(bundle=bundle, trace=trace)
