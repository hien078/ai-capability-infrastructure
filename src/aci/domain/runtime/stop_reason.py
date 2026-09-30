"""Run lifecycle vocabulary (harness.md §7.3–7.5, §8.11): status, stop reasons.

Every run ends or pauses with a machine-readable stop reason (INV-11).
Pause vs termination is explicit: ``AWAITING_APPROVAL``/``INTERRUPTED`` are
resumable; ``CANCELLED`` is terminal; budget limits may be resumable.
"""

from enum import StrEnum


class RunStatus(StrEnum):
    """Authoritative run lifecycle (§7.3 state machine)."""

    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    WAITING_CAPABILITY = "waiting_capability"
    WAITING_CHILD = "waiting_child"
    INTERRUPTED_APPROVAL = "interrupted_approval"
    INTERRUPTED = "interrupted"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Legal transitions (§7.3). Terminal states have no outgoing edges.
RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.INITIALIZING}),
    RunStatus.INITIALIZING: frozenset({RunStatus.READY, RunStatus.FAILED}),
    RunStatus.READY: frozenset({RunStatus.RUNNING}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_CAPABILITY,
            RunStatus.WAITING_CHILD,
            RunStatus.INTERRUPTED_APPROVAL,
            RunStatus.INTERRUPTED,
            RunStatus.VERIFYING,
            RunStatus.RECOVERING,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.WAITING_CAPABILITY: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.WAITING_CHILD: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.INTERRUPTED_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.INTERRUPTED: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.VERIFYING: frozenset({RunStatus.SUCCEEDED, RunStatus.RECOVERING, RunStatus.FAILED}),
    RunStatus.RECOVERING: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.PARTIAL: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class StopReason(StrEnum):
    """Stable top-level stop reason (§7.5) + optional detail code elsewhere."""

    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    LIMIT_TURNS = "LIMIT_TURNS"
    LIMIT_TOTAL_TOKENS = "LIMIT_TOTAL_TOKENS"
    LIMIT_OUTPUT_TOKENS = "LIMIT_OUTPUT_TOKENS"
    LIMIT_TOOL_CALLS = "LIMIT_TOOL_CALLS"
    LIMIT_WALL_TIME = "LIMIT_WALL_TIME"
    LIMIT_COST = "LIMIT_COST"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    GUARDRAIL_BLOCKED = "GUARDRAIL_BLOCKED"
    AUTHORITY_DENIED = "AUTHORITY_DENIED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    WORKSPACE_FAILURE = "WORKSPACE_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"
    TOOL_FAILURE = "TOOL_FAILURE"
    FATAL_ERROR = "FATAL_ERROR"


# Resumable pause reasons (§7.5): a run paused with one of these may continue
# from a checkpoint after the blocker clears.
RESUMABLE_STOP_REASONS = frozenset({StopReason.INTERRUPTED, StopReason.AWAITING_APPROVAL})


def is_terminal(status: RunStatus) -> bool:
    """True when ``status`` has no legal outgoing transition."""
    return not RUN_TRANSITIONS[status]
