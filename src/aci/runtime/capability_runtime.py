"""CapabilityRuntime (harness.md §11): the ACI ↔ harness boundary.

ACI selects capabilities (Capability Intelligence); this runtime loads/binds/
activates them (INV-05). A manifest may DECLARE required permissions but can
never grant them (§11.6) — AuthorityManager evaluates. Versions are pinned
immutable per run (§11.7); feedback carries outcomes, never subjective
ratings (§34).
"""

import hashlib
from datetime import UTC, datetime
from typing import Protocol

from aci.domain.runtime.actions import CapabilityRequest
from aci.domain.runtime.state import CapabilityActivation, RuntimeStateSnapshot
from aci.runtime.context_engine import estimate_tokens, render_capability

_CHARS_PER_TOKEN = 4  # matches context_engine.estimate_tokens
#: Hard ceiling on one capability's instructions in context. Capability items
#: are a PROTECTED context kind (never dropped, §9.7), so an unbounded skill
#: would blow the budget: 4k tokens x the default max_loaded (8) stays well
#: inside the REST kernel's 60k context budget alongside task + transcript.
MAX_INSTRUCTION_TOKENS = 4_000
#: Floor for the estimate-derived cap so a low/absent estimate never
#: truncates an ordinary skill to nothing.
MIN_INSTRUCTION_TOKENS = 512
#: The selection's estimate is derived from the manifest entry size; 2x
#: slack absorbs multi-byte UTF-8 and rounding, while a selection that
#: understates its payload cannot smuggle an arbitrarily large one in.
_ESTIMATE_SLACK = 2
TRUNCATION_MARKER = "[... skill instructions truncated by the harness: {kept} of {total} chars ...]"


def instruction_limit_chars(
    estimated_context_tokens: int, max_tokens: int = MAX_INSTRUCTION_TOKENS
) -> int:
    """Per-capability instruction cap in chars: the selection's own estimate
    (with slack, floored) but never above the hard ``max_tokens`` ceiling."""
    derived = max(MIN_INSTRUCTION_TOKENS, _ESTIMATE_SLACK * max(0, estimated_context_tokens))
    return min(max_tokens, derived) * _CHARS_PER_TOKEN


def bound_instructions(payload: bytes, limit_chars: int) -> str:
    """Decode a VERIFIED payload (UTF-8, errors replaced) and bound it to
    ``limit_chars`` — marker included — with an explicit truncation marker."""
    text = payload.decode("utf-8", errors="replace")
    if len(text) <= limit_chars:
        return text
    probe = TRUNCATION_MARKER.format(kept=limit_chars, total=len(text))
    kept = max(0, limit_chars - len(probe) - 1)
    marker = TRUNCATION_MARKER.format(kept=kept, total=len(text))
    return f"{text[:kept]}\n{marker}"


class CapabilitySearchError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ACISelection(Protocol):
    """What the ACI capability plane returns for a search (§11.4)."""

    capability_id: str
    version: str
    payload_ref: str
    digest: str
    estimated_context_tokens: int


class ACIClient(Protocol):
    """§11.2 — search/resolve/feedback against the ACI capability plane."""

    def search(self, request: CapabilityRequest) -> list[ACISelection]: ...

    def resolve(self, capability_id: str, version: str) -> tuple[bytes, str]:
        """Return (payload bytes, sha256 digest) for the pinned version."""


class CapabilityFeedback(Protocol):
    def report(
        self,
        *,
        capability_id: str,
        version: str,
        outcome: str,
        failure_class: str | None,
        evidence_refs: list[str],
    ) -> None: ...


class CapabilityRuntime:
    """Loads capabilities from ACI with digest verification (§49.2 activation
    flow: resolve → validate hash → authority check happens upstream → load →
    ACTIVE). Cache keyed by (capability_id, version) — idempotent."""

    def __init__(
        self,
        aci: ACIClient,
        *,
        feedback: CapabilityFeedback | None = None,
        max_loaded: int = 8,
        max_refreshes: int = 2,
        max_instruction_tokens: int = MAX_INSTRUCTION_TOKENS,
    ) -> None:
        self._aci = aci
        self._max_instruction_tokens = max_instruction_tokens
        self._feedback = feedback
        self._max_loaded = max_loaded
        self._max_refreshes = max_refreshes
        self._cache: dict[tuple[str, str], tuple[bytes, str]] = {}
        self._refreshes: dict[str, int] = {}

    def search(self, request: CapabilityRequest) -> list[ACISelection]:
        """§11.3 — send normalized need, never full conversation history."""
        return self._aci.search(request)

    def activate(
        self,
        selection: ACISelection,
        *,
        run_id: str,
    ) -> tuple[CapabilityActivation, bytes]:
        """Resolve + verify digest + build the activation record (§11.5).

        Raises on digest mismatch — a tampered payload must never reach model
        context (§26.3 supply-chain rules). Only AFTER verification are the
        bytes decoded into ``instructions`` (bounded, see
        ``bound_instructions``); ``context_tokens`` is the estimate of the
        block ContextEngine actually renders, so the budget stays honest."""
        key = (selection.capability_id, selection.version)
        if key not in self._cache:
            self._cache[key] = self._aci.resolve(*key)
        payload, digest = self._cache[key]
        if digest != selection.digest:
            # A pinned version has ONE digest: a cache hit is verified too.
            raise CapabilitySearchError(
                f"digest mismatch for {selection.capability_id}@{selection.version}: "
                f"expected {selection.digest}, got {digest}"
            )
        activation = CapabilityActivation(
            capability_id=selection.capability_id,
            version=selection.version,
            digest=digest,
            activation_id=f"act-{hashlib.sha256(f'{run_id}:{key}'.encode()).hexdigest()[:12]}",
            status="ACTIVE",
            activated_at=datetime.now(UTC),
            instructions=bound_instructions(
                payload,
                instruction_limit_chars(
                    selection.estimated_context_tokens, self._max_instruction_tokens
                ),
            ),
        )
        activation = activation.model_copy(
            update={"context_tokens": estimate_tokens(render_capability(activation))}
        )
        return activation, payload

    def handle_request(
        self,
        request: CapabilityRequest,
        snapshot: RuntimeStateSnapshot,
    ) -> list[CapabilityActivation]:
        """§25.2 mid-run acquisition — bounded by max_loaded + max_refreshes."""
        run_id = snapshot.run.run_id
        self._refreshes[run_id] = self._refreshes.get(run_id, 0) + 1
        if self._refreshes[run_id] > self._max_refreshes:
            raise CapabilitySearchError(
                f"capability refresh budget exhausted for {run_id} (max {self._max_refreshes})"
            )
        if len(snapshot.active_capabilities) >= self._max_loaded:
            raise CapabilitySearchError(
                f"max loaded capabilities {self._max_loaded} reached for {run_id}"
            )
        selections = self.search(request)
        activations: list[CapabilityActivation] = []
        for selection in selections:
            if len(activations) + len(snapshot.active_capabilities) >= self._max_loaded:
                break
            activation, _ = self.activate(selection, run_id=run_id)
            activations.append(activation)
        return activations

    def report_outcome(
        self,
        *,
        capability_id: str,
        version: str,
        outcome: str,
        failure_class: str | None = None,
        evidence_refs: list[str] | None = None,
    ) -> None:
        """§11.8 — outcomes, not opaque ratings; never mutates the live version."""
        if self._feedback is not None:
            self._feedback.report(
                capability_id=capability_id,
                version=version,
                outcome=outcome,
                failure_class=failure_class,
                evidence_refs=evidence_refs or [],
            )
