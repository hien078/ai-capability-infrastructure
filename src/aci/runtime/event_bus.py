"""EventBus (harness.md §20.7, §21): typed events for every external effect.

INV-15: tool execution, approval, capability load, delegation, checkpoint,
model call, recovery action, and verification outcome MUST emit telemetry.
The bus is sync, in-memory by default; sinks (log, DB, tracing) subscribe as
callables and must never raise back into the run loop.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from aci.domain.runtime.events import EventEnvelope

log = logging.getLogger(__name__)

# §21.2 core event taxonomy — the types the kernel emits.
RUN_CREATED = "run.created"
RUN_STARTED = "run.started"
RUN_PAUSED = "run.paused"
RUN_RESUMED = "run.resumed"
RUN_CANCELLED = "run.cancelled"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"

TURN_STARTED = "turn.started"
TURN_COMPLETED = "turn.completed"

MODEL_REQUEST_STARTED = "model.request.started"
MODEL_REQUEST_COMPLETED = "model.request.completed"
MODEL_REQUEST_FAILED = "model.request.failed"

CONTEXT_ASSEMBLED = "context.assembled"
CONTEXT_COMPACTED = "context.compacted"
CONTEXT_OFFLOADED = "context.offloaded"

CAPABILITY_SEARCH_STARTED = "capability.search.started"
CAPABILITY_SEARCH_COMPLETED = "capability.search.completed"
CAPABILITY_LOADED = "capability.loaded"
CAPABILITY_UNLOADED = "capability.unloaded"
CAPABILITY_FEEDBACK = "capability.feedback"

TOOL_REQUESTED = "tool.requested"
TOOL_AUTHORITY_EVALUATED = "tool.authority.evaluated"
TOOL_APPROVAL_REQUESTED = "tool.approval.requested"
TOOL_EXECUTION_STARTED = "tool.execution.started"
TOOL_EXECUTION_COMPLETED = "tool.execution.completed"
TOOL_EXECUTION_FAILED = "tool.execution.failed"

WORKSPACE_CREATED = "workspace.created"
WORKSPACE_SNAPSHOT = "workspace.snapshot"
WORKSPACE_TERMINATED = "workspace.terminated"

DELEGATION_STARTED = "delegation.started"
DELEGATION_COMPLETED = "delegation.completed"
DELEGATION_FAILED = "delegation.failed"

CHECKPOINT_SAVED = "checkpoint.saved"
CHECKPOINT_RESTORED = "checkpoint.restored"

RECOVERY_STARTED = "recovery.started"
RECOVERY_ACTION = "recovery.action"
RECOVERY_COMPLETED = "recovery.completed"

VERIFICATION_STARTED = "verification.started"
VERIFICATION_CHECK = "verification.check"
VERIFICATION_COMPLETED = "verification.completed"

ALL_EVENT_TYPES = frozenset(
    {
        RUN_CREATED,
        RUN_STARTED,
        RUN_PAUSED,
        RUN_RESUMED,
        RUN_CANCELLED,
        RUN_COMPLETED,
        RUN_FAILED,
        TURN_STARTED,
        TURN_COMPLETED,
        MODEL_REQUEST_STARTED,
        MODEL_REQUEST_COMPLETED,
        MODEL_REQUEST_FAILED,
        CONTEXT_ASSEMBLED,
        CONTEXT_COMPACTED,
        CONTEXT_OFFLOADED,
        CAPABILITY_SEARCH_STARTED,
        CAPABILITY_SEARCH_COMPLETED,
        CAPABILITY_LOADED,
        CAPABILITY_UNLOADED,
        CAPABILITY_FEEDBACK,
        TOOL_REQUESTED,
        TOOL_AUTHORITY_EVALUATED,
        TOOL_APPROVAL_REQUESTED,
        TOOL_EXECUTION_STARTED,
        TOOL_EXECUTION_COMPLETED,
        TOOL_EXECUTION_FAILED,
        WORKSPACE_CREATED,
        WORKSPACE_SNAPSHOT,
        WORKSPACE_TERMINATED,
        DELEGATION_STARTED,
        DELEGATION_COMPLETED,
        DELEGATION_FAILED,
        CHECKPOINT_SAVED,
        CHECKPOINT_RESTORED,
        RECOVERY_STARTED,
        RECOVERY_ACTION,
        RECOVERY_COMPLETED,
        VERIFICATION_STARTED,
        VERIFICATION_CHECK,
        VERIFICATION_COMPLETED,
    }
)


class EventBus:
    """Sync in-process bus. Sinks are callables ``(EventEnvelope) -> None``;
    a crashing sink is logged and dropped — telemetry must never kill a run."""

    def __init__(self) -> None:
        self._sinks: list[Callable[[EventEnvelope], None]] = []
        self._history: dict[str, list[EventEnvelope]] = {}

    def subscribe(self, sink: Callable[[EventEnvelope], None]) -> None:
        self._sinks.append(sink)

    def emit(
        self,
        event_type: str,
        *,
        run_id: str,
        payload: dict[str, object] | None = None,
        parent_run_id: str | None = None,
        turn_id: str | None = None,
        correlation_id: str | None = None,
    ) -> EventEnvelope:
        envelope = EventEnvelope(
            event_id=f"evt_{uuid4().hex[:12]}",
            event_type=event_type,
            timestamp=datetime.now(UTC),
            run_id=run_id,
            parent_run_id=parent_run_id,
            turn_id=turn_id,
            correlation_id=correlation_id,
            payload=payload or {},
        )
        self._history.setdefault(run_id, []).append(envelope)
        for sink in self._sinks:
            try:
                sink(envelope)
            except Exception:  # noqa: BLE001 — a bad sink never breaks the run
                log.exception("event sink crashed on %s", event_type)
        return envelope

    def history(self, run_id: str) -> list[EventEnvelope]:
        return list(self._history.get(run_id, []))


class NullEventBus(EventBus):
    """Honest default when no sink is wired: events are recorded in-memory
    (replay/debug path, §55) but nothing is shipped anywhere."""
