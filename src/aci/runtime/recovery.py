"""RecoveryManager (harness.md §18): failure-classified, bounded recovery.

No blind retry of uncertain non-idempotent effects (§18.3, §12.6); recovery
itself consumes budget (§18.4) — the caller enforces max_recoveries.
"""

from dataclasses import dataclass

from aci.domain.runtime.failures import FailureClass, FailureEnvelope
from aci.domain.runtime.stop_reason import StopReason
from aci.domain.runtime.tools import (
    MUTATING_CLASSES,
    IdempotencyClass,
    ToolObservation,
    ToolSpec,
)

RECOVERY_ACTIONS = frozenset(
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

#: Recovery actions that re-run the failed operation.
RETRY_ACTIONS = frozenset({"RETRY_SAME", "RETRY_BACKOFF"})

#: Provider-capacity failures: the model endpoint is overloaded or limiting
#: us, not the run going wrong. They are bounded by CONSECUTIVE failures of
#: one model call (a success ends the streak) — a run-wide count let the 3rd
#: sporadic gateway error of a long run's whole life end it (2026-10-03,
#: g-e2d Bp@20: 32/42 rows MODEL_FAILURE at turn 1 under a shared gateway).
PROVIDER_CAPACITY_CLASSES = frozenset({FailureClass.TRANSIENT_MODEL, FailureClass.RATE_LIMITED})


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    """§18.2 recovery actions as frozen data — shared matrix entries can
    never be mutated through a returned decision."""

    action: str
    reason: str

    def __post_init__(self) -> None:
        if self.action not in RECOVERY_ACTIONS:
            raise ValueError(f"unknown recovery action: {self.action}")

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

# §7.5 stop reason for a failure class that ends the run.
_STOP_REASONS: dict[FailureClass, StopReason] = {
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


class RecoveryManager:
    """Classify → look up policy → bound attempts. The caller (RunController)
    applies the action and enforces recovery budget (§18.4)."""

    def __init__(
        self,
        matrix: dict[FailureClass, RecoveryAction] | None = None,
        *,
        max_attempts_total: int = 8,
        max_same_failure_retries: int = 2,
        max_provider_retries: int = 6,
    ) -> None:
        self._matrix = dict(_DEFAULT_MATRIX)
        if matrix:
            self._matrix.update(matrix)
        self._max_attempts = max_attempts_total
        self._max_same = max_same_failure_retries
        self._max_provider = max_provider_retries
        self._attempts = 0
        self._per_class: dict[FailureClass, int] = {}
        self._provider_streak = 0

    def decide(
        self, failure: FailureEnvelope, *, idempotency: IdempotencyClass | None = None
    ) -> RecoveryAction:
        """Terminal when the recovery budget is spent (no infinite loops).
        ``idempotency`` is the failed tool's class when known: RETRY_SAME is
        never issued for a call that may have had a side effect or that is
        not declared idempotent (§12.6, §18.3)."""
        if failure.failure_class in PROVIDER_CAPACITY_CLASSES:
            # One streak = one attempt of the total budget; bounded by the
            # consecutive cap, reset by provider_call_succeeded().
            self._provider_streak += 1
            if self._provider_streak == 1:
                self._attempts += 1
            if self._attempts > self._max_attempts:
                return RecoveryAction("FAIL", "recovery budget exhausted")
            if self._provider_streak > self._max_provider:
                return RecoveryAction("ESCALATE", f"{failure.failure_class} persisted")
            return self._matrix[failure.failure_class]
        self._attempts += 1
        self._per_class[failure.failure_class] = self._per_class.get(failure.failure_class, 0) + 1
        if self._attempts > self._max_attempts:
            return RecoveryAction("FAIL", "recovery budget exhausted")
        if self._per_class[failure.failure_class] > self._max_same:
            return RecoveryAction("ESCALATE", f"{failure.failure_class} repeated")
        action = self._matrix[failure.failure_class]
        if action.action == "RETRY_SAME":
            if failure.side_effect_state == "possible":
                return RecoveryAction("REPLAN", "possible side effect — no blind retry")
            if idempotency is not None and not idempotency_allows_retry(idempotency):
                return RecoveryAction("REPLAN", f"{idempotency} tool — no blind retry")
        return action

    def decide_tool(self, failure: FailureEnvelope, *, tool: ToolSpec | None) -> RecoveryAction:
        """§18.3 for a failed tool call: the class decides, the TOOL decides
        whether a retry may happen at all. Only a retry-safe tool (read-only
        AND declared idempotent, ``tool_retry_safe``) is ever re-executed; a
        mutating / process / unknown tool's retry is converted to REPLAN —
        the failure goes back to the model, never a blind re-run (§12.6)."""
        idempotency: IdempotencyClass = tool.idempotency_class if tool else "UNKNOWN"
        action = self.decide(failure, idempotency=idempotency)
        if action.action in RETRY_ACTIONS and not tool_retry_safe(tool):
            return RecoveryAction("REPLAN", "non-idempotent tool — no automatic retry")
        return action

    def provider_call_succeeded(self) -> None:
        """A model call went through: the provider-capacity streak is over."""
        self._provider_streak = 0

    @property
    def provider_streak(self) -> int:
        """Consecutive provider-capacity failures of the current model call."""
        return self._provider_streak

    @property
    def attempts(self) -> int:
        return self._attempts


def stop_reason_for_failure(failure: FailureEnvelope) -> StopReason:
    """Map a terminal failure to its §7.5 stop reason."""
    return _STOP_REASONS[failure.failure_class]


def idempotency_allows_retry(cls: IdempotencyClass) -> bool:
    """§12.6 — RecoveryManager must never blindly retry NON_IDEMPOTENT calls."""
    return cls in ("IDEMPOTENT", "IDEMPOTENT_WITH_KEY")


#: §18.1 tool-failure classification, keyed by ``ToolObservation.error_class``
#: (a code ToolRuntime set — never parsed from exception text, §28).
#: Only infrastructure failures enter recovery; everything else is a plain
#: observation the model must see and act on:
#:   TOOL_TIMEOUT / TRANSIENT_TOOL       → recovery (retry only if retry-safe)
#:   TOOL_INVALID_ARGUMENT / TOOL_EXECUTION_FAILED → deterministic: feedback
#:       to the model (§18.3 "return schema error to model"); re-running the
#:       same call cannot change the outcome, so it is never retried and
#:       never charged to the recovery budget
#:   TOOL_NOT_FOUND / AUTHORITY_DENIED / AUTHORITY_EXPIRED /
#:   APPROVAL_REQUIRED / GUARDRAIL_BLOCKED → plain observation, never
#:       retried (no circumvention, §18.3)
TOOL_RECOVERABLE_ERRORS: dict[str, FailureClass] = {
    "TOOL_TIMEOUT": FailureClass.TOOL_TIMEOUT,
    "TRANSIENT_TOOL": FailureClass.TRANSIENT_TOOL,
}


def classify_tool_failure(observation: ToolObservation) -> FailureClass | None:
    """The failure class of a tool observation that must go through
    recovery, or None when it is a success or a plain (deterministic /
    denied / blocked) observation."""
    if observation.status == "success" or observation.error_class is None:
        return None
    return TOOL_RECOVERABLE_ERRORS.get(observation.error_class)


def tool_retry_safe(tool: ToolSpec | None) -> bool:
    """A tool may be re-executed automatically only when it cannot have
    changed anything (not a MUTATING class) AND declares idempotency —
    an unregistered tool, a mutating one, or one of UNKNOWN idempotency
    is never auto-retried."""
    return (
        tool is not None
        and tool.side_effect_class not in MUTATING_CLASSES
        and idempotency_allows_retry(tool.idempotency_class)
    )
