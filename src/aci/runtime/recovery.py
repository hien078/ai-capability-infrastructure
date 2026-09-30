"""RecoveryManager (harness.md §18): failure-classified, bounded recovery.

No blind retry of uncertain non-idempotent effects (§18.3); recovery itself
consumes budget (§18.4) — the caller enforces max_recoveries.
"""

from aci.domain.runtime.failures import FailureClass, FailureEnvelope
from aci.domain.runtime.stop_reason import StopReason
from aci.domain.runtime.tools import IdempotencyClass


class RecoveryAction:
    """§18.2 recovery actions as frozen data."""

    _VALID = frozenset(
        {
            "RETRY_SAME",
            "RETRY_BACKOFF",
            "REPAIR_INPUT",
            "REPAIR_OUTPUT",
            "REPLAN",
            "COMPACT_CONTEXT",
            "REQUEST_APPROVAL",
            "REQUEST_ALTERNATE_CAPABILITY",
            "SWITCH_MODEL",
            "RESTORE_CHECKPOINT",
            "RETURN_PARTIAL",
            "ESCALATE",
            "FAIL",
        }
    )

    __slots__ = ("action", "reason")

    def __init__(self, action: str, reason: str) -> None:
        if action not in self._VALID:
            raise ValueError(f"unknown recovery action: {action}")
        self.action = action
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover
        return f"RecoveryAction({self.action})"

    @property
    def is_terminal(self) -> bool:
        return self.action in ("FAIL", "RETURN_PARTIAL", "ESCALATE")


# §18.3 default policy matrix: failure class → default action.
_DEFAULT_MATRIX: dict[FailureClass, RecoveryAction] = {
    FailureClass.TRANSIENT_MODEL: RecoveryAction("RETRY_BACKOFF", "transient model error"),
    FailureClass.RATE_LIMITED: RecoveryAction("RETRY_BACKOFF", "respect provider hints"),
    FailureClass.MODEL_MALFORMED_OUTPUT: RecoveryAction("REPAIR_OUTPUT", "schema-focused repair"),
    FailureClass.TRANSIENT_TOOL: RecoveryAction("RETRY_SAME", "read-only transient"),
    FailureClass.TOOL_INVALID_ARGUMENT: RecoveryAction(
        "REPAIR_INPUT", "return validation feedback"
    ),
    FailureClass.TOOL_TIMEOUT: RecoveryAction("RETRY_SAME", "tool-specific bounded retry"),
    FailureClass.TOOL_EXECUTION_FAILED: RecoveryAction("RETRY_SAME", "classify root cause"),
    FailureClass.TOOL_SIDE_EFFECT_UNCERTAIN: RecoveryAction(
        "REPLAN", "never blind-retry uncertain effects"
    ),
    FailureClass.AUTHORITY_BLOCKED: RecoveryAction("REPLAN", "replan within grants"),
    FailureClass.APPROVAL_REJECTED: RecoveryAction("REPLAN", "never circumvent"),
    FailureClass.WORKSPACE_UNAVAILABLE: RecoveryAction("RETRY_BACKOFF", "bounded reconnect"),
    FailureClass.SANDBOX_DENIED: RecoveryAction("REPLAN", "remain least privilege"),
    FailureClass.CAPABILITY_NOT_FOUND: RecoveryAction(
        "REQUEST_ALTERNATE_CAPABILITY", "broaden once"
    ),
    FailureClass.CAPABILITY_LOAD_FAILED: RecoveryAction(
        "REQUEST_ALTERNATE_CAPABILITY", "validate manifest"
    ),
    FailureClass.CAPABILITY_INSUFFICIENT: RecoveryAction(
        "REQUEST_ALTERNATE_CAPABILITY", "include evidence in ACI request"
    ),
    FailureClass.CAPABILITY_CONFLICT: RecoveryAction("REPLAN", "composer conflict resolution"),
    FailureClass.CONTEXT_OVERFLOW: RecoveryAction("COMPACT_CONTEXT", "preserve pinned items"),
    FailureClass.CONTEXT_CORRUPTION: RecoveryAction("RESTORE_CHECKPOINT", "last good state"),
    FailureClass.VERIFICATION_FAILED: RecoveryAction("REPLAN", "evidence-driven repair"),
    FailureClass.RESULT_CONTRACT_FAILED: RecoveryAction(
        "REPAIR_OUTPUT", "structure before semantics"
    ),
    FailureClass.BUDGET_EXHAUSTED: RecoveryAction("RETURN_PARTIAL", "partial/resume per policy"),
    FailureClass.CANCELLED: RecoveryAction("FAIL", "terminal"),
    FailureClass.FATAL: RecoveryAction("FAIL", "preserve diagnostic state"),
}


class RecoveryManager:
    """Classify → look up policy → bound attempts. The caller (RunController)
    applies the action and enforces recovery budget (§18.4)."""

    def __init__(
        self,
        matrix: dict[FailureClass, RecoveryAction] | None = None,
        *,
        max_attempts_total: int = 8,
        max_same_failure_retries: int = 2,
    ) -> None:
        self._matrix = dict(_DEFAULT_MATRIX)
        if matrix:
            self._matrix.update(matrix)
        self._max_attempts = max_attempts_total
        self._max_same = max_same_failure_retries
        self._attempts = 0
        self._per_class: dict[FailureClass, int] = {}

    def decide(self, failure: FailureEnvelope) -> RecoveryAction:
        """Terminal when the recovery budget is spent (no infinite loops)."""
        self._attempts += 1
        self._per_class[failure.failure_class] = self._per_class.get(failure.failure_class, 0) + 1
        if self._attempts > self._max_attempts:
            return RecoveryAction("FAIL", "recovery budget exhausted")
        if self._per_class[failure.failure_class] > self._max_same:
            return RecoveryAction("ESCALATE", f"{failure.failure_class} repeated")
        action = self._matrix[failure.failure_class]
        if failure.side_effect_state == "possible" and action.action == "RETRY_SAME":
            return RecoveryAction("REPLAN", "possible side effect — no blind retry")
        return action

    @property
    def attempts(self) -> int:
        return self._attempts


def stop_reason_for_failure(failure: FailureEnvelope) -> StopReason:
    """Map a terminal failure to its §7.5 stop reason."""
    mapping: dict[FailureClass, StopReason] = {
        FailureClass.VERIFICATION_FAILED: StopReason.VERIFICATION_FAILED,
        FailureClass.RESULT_CONTRACT_FAILED: StopReason.VERIFICATION_FAILED,
        FailureClass.AUTHORITY_BLOCKED: StopReason.AUTHORITY_DENIED,
        FailureClass.APPROVAL_REJECTED: StopReason.AUTHORITY_DENIED,
        FailureClass.CAPABILITY_NOT_FOUND: StopReason.CAPABILITY_UNAVAILABLE,
        FailureClass.CAPABILITY_LOAD_FAILED: StopReason.CAPABILITY_UNAVAILABLE,
        FailureClass.CAPABILITY_INSUFFICIENT: StopReason.CAPABILITY_UNAVAILABLE,
        FailureClass.CAPABILITY_CONFLICT: StopReason.CAPABILITY_UNAVAILABLE,
        FailureClass.WORKSPACE_UNAVAILABLE: StopReason.WORKSPACE_FAILURE,
        FailureClass.SANDBOX_DENIED: StopReason.WORKSPACE_FAILURE,
        FailureClass.TRANSIENT_MODEL: StopReason.MODEL_FAILURE,
        FailureClass.RATE_LIMITED: StopReason.MODEL_FAILURE,
        FailureClass.MODEL_MALFORMED_OUTPUT: StopReason.MODEL_FAILURE,
        FailureClass.TOOL_TIMEOUT: StopReason.TOOL_FAILURE,
        FailureClass.TOOL_EXECUTION_FAILED: StopReason.TOOL_FAILURE,
        FailureClass.TRANSIENT_TOOL: StopReason.TOOL_FAILURE,
        FailureClass.TOOL_INVALID_ARGUMENT: StopReason.TOOL_FAILURE,
        FailureClass.TOOL_SIDE_EFFECT_UNCERTAIN: StopReason.TOOL_FAILURE,
        FailureClass.CONTEXT_OVERFLOW: StopReason.FATAL_ERROR,
        FailureClass.CONTEXT_CORRUPTION: StopReason.FATAL_ERROR,
        FailureClass.BUDGET_EXHAUSTED: StopReason.LIMIT_COST,
        FailureClass.CANCELLED: StopReason.CANCELLED,
        FailureClass.FATAL: StopReason.FATAL_ERROR,
    }
    return mapping[failure.failure_class]


def idempotency_allows_retry(cls: IdempotencyClass) -> bool:
    """§12.6 — RecoveryManager must never blindly retry NON_IDEMPOTENT calls."""
    return cls in ("IDEMPOTENT", "IDEMPOTENT_WITH_KEY")
