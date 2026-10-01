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
    """

    implementation = IMPLEMENTATION
    version = VERSION

    def __init__(self, payload_sizes: PayloadSizeSource | None = None) -> None:
        self._payload_sizes = payload_sizes

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
        for resolved in resolution.selected:
            key = (resolved.candidate.capability_id, resolved.candidate.version)
            cost, source = item_cost(resolved.document_text, sizes.get(key))
            included = False
            if stop_reason is None:
                if len(items) >= command.max_items:
                    stop_reason = "max_items"  # budget: max items reached
                elif spent + cost > command.max_context_tokens:
                    stop_reason = "max_context_tokens"  # budget: context cost exceeded
                else:
                    included = True
            costs.append(
                ComposedItemCost(
                    capability_id=key[0],
                    version=key[1],
                    estimated_tokens=cost,
                    estimate_source=source,
                    included=included,
                )
            )
            if not included:
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
            items=costs,
        )
        return CompositionResult(bundle=bundle, trace=trace)
