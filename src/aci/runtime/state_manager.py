"""StateManager (harness.md §8): the ONLY mutable runtime-state authority.

INV-01: every other component reads frozen snapshots and commits changes back
through versioned commits (§8.5 compare-and-swap). No hidden competing copies.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.events import EventEnvelope
from aci.domain.runtime.state import (
    BudgetLedger,
    CapabilityActivation,
    PlanItem,
    RunState,
    RuntimeStateSnapshot,
    TaskState,
)
from aci.domain.runtime.stop_reason import RUN_TRANSITIONS, RunStatus, StopReason


class StateCommitConflict(Exception):
    """CAS failure: someone else advanced the state version (§27.1)."""

    def __init__(self, run_id: str, expected: int, actual: int) -> None:
        super().__init__(f"state version conflict on {run_id}: expected {expected}, have {actual}")
        self.run_id = run_id
        self.expected = expected
        self.actual = actual


class StateEvent(BaseModel):
    """One typed state mutation, applied atomically inside a commit."""

    model_config = {"frozen": True}

    event_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class StateManager:
    """Owns the authoritative RuntimeState per run. All mutation goes through
    :meth:`commit`, which validates lifecycle edges and bumps the version."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[EventEnvelope]] = {}

    def create(
        self,
        *,
        run_id: str,
        task: TaskState,
        budget: BudgetLedger,
        grants: GrantEnvelope,
        parent_run_id: str | None = None,
        workspace_id: str | None = None,
        depth: int = 0,
    ) -> RuntimeStateSnapshot:
        if run_id in self._runs:
            raise ValueError(f"run already exists: {run_id}")
        now = datetime.now(UTC)
        run = RunState(run_id=run_id, parent_run_id=parent_run_id, created_at=now)
        plan: list[PlanItem] = []
        capabilities: list[CapabilityActivation] = []
        self._runs[run_id] = {
            "run": run,
            "task": task,
            "budget": budget,
            "grants": grants,
            "plan": plan,
            "capabilities": capabilities,
            "workspace_id": workspace_id,
            "depth": depth,
        }
        return self.snapshot(run_id)

    def snapshot(self, run_id: str) -> RuntimeStateSnapshot:
        """Frozen read-only view (INV-01). Raises KeyError on unknown run."""
        record = self._runs[run_id]
        return RuntimeStateSnapshot(
            run=record["run"],
            task=record["task"],
            budget=record["budget"],
            grants=record["grants"],
            plan=list(record["plan"]),
            active_capabilities=list(record["capabilities"]),
            workspace_id=record["workspace_id"],
            depth=record["depth"],
        )

    def commit(
        self,
        run_id: str,
        *,
        expected_version: int,
        events: list[StateEvent],
    ) -> RuntimeStateSnapshot:
        """Apply events atomically; CAS on ``expected_version`` (§8.5)."""
        record = self._runs[run_id]
        run: RunState = record["run"]
        if run.version != expected_version:
            raise StateCommitConflict(run_id, expected_version, run.version)
        for event in events:
            self._apply(record, event)
        record["run"] = run.model_copy(
            update={"version": expected_version + 1, "current_turn": run.current_turn}
        )
        return self.snapshot(run_id)

    def transition(
        self,
        run_id: str,
        status: RunStatus,
        *,
        stop_reason: StopReason | None = None,
        detail_code: str | None = None,
    ) -> RuntimeStateSnapshot:
        """Lifecycle move validated against RUN_TRANSITIONS (§7.3)."""
        record = self._runs[run_id]
        run: RunState = record["run"]
        if status not in RUN_TRANSITIONS[run.status]:
            raise ValueError(f"illegal transition {run.status} -> {status} on {run_id}")
        now = datetime.now(UTC)
        update: dict[str, Any] = {
            "status": status,
            "version": run.version + 1,
        }
        if stop_reason is not None:
            update["stop_reason"] = stop_reason
        if detail_code is not None:
            update["detail_code"] = detail_code
        if status is RunStatus.RUNNING and run.started_at is None:
            update["started_at"] = now
        if status in (
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.PARTIAL,
        ):
            update["completed_at"] = now
        record["run"] = run.model_copy(update=update)
        return self.snapshot(run_id)

    # -- typed helpers over commit events ------------------------------------

    def advance_turn(self, run_id: str) -> RuntimeStateSnapshot:
        record = self._runs[run_id]
        run: RunState = record["run"]
        record["run"] = run.model_copy(
            update={"current_turn": run.current_turn + 1, "version": run.version + 1}
        )
        return self.snapshot(run_id)

    def consume_budget(
        self,
        run_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        cost_usd: float = 0.0,
        wall_time_seconds: float = 0.0,
    ) -> RuntimeStateSnapshot:
        record = self._runs[run_id]
        budget: BudgetLedger = record["budget"]
        record["budget"] = budget.model_copy(
            update={
                "consumed_input_tokens": budget.consumed_input_tokens + input_tokens,
                "consumed_output_tokens": budget.consumed_output_tokens + output_tokens,
                "consumed_tool_calls": budget.consumed_tool_calls + tool_calls,
                "consumed_cost_usd": round(budget.consumed_cost_usd + cost_usd, 6),
                "consumed_wall_time_seconds": round(
                    budget.consumed_wall_time_seconds + wall_time_seconds, 3
                ),
            }
        )
        return self.snapshot(run_id)

    def count_recovery(self, run_id: str) -> RuntimeStateSnapshot:
        record = self._runs[run_id]
        budget: BudgetLedger = record["budget"]
        record["budget"] = budget.model_copy(
            update={"consumed_recoveries": budget.consumed_recoveries + 1}
        )
        return self.snapshot(run_id)

    def set_plan(self, run_id: str, plan: list[PlanItem]) -> RuntimeStateSnapshot:
        record = self._runs[run_id]
        record["plan"] = list(plan)
        return self.snapshot(run_id)

    def activate_capability(
        self, run_id: str, activation: CapabilityActivation
    ) -> RuntimeStateSnapshot:
        record = self._runs[run_id]
        record["capabilities"] = [
            a for a in record["capabilities"] if a.capability_id != activation.capability_id
        ] + [activation]
        return self.snapshot(run_id)

    def extend_grants(self, run_id: str, grants: GrantEnvelope) -> RuntimeStateSnapshot:
        record = self._runs[run_id]
        record["grants"] = grants
        return self.snapshot(run_id)

    def set_workspace(self, run_id: str, workspace_id: str) -> RuntimeStateSnapshot:
        record = self._runs[run_id]
        record["workspace_id"] = workspace_id
        return self.snapshot(run_id)

    def events(self, run_id: str) -> list[EventEnvelope]:
        return list(self._events.get(run_id, []))

    def _apply(self, record: dict[str, Any], event: StateEvent) -> None:
        """Apply one StateEvent to the record (called inside commit)."""
        et, p = event.event_type, event.payload
        if et == "plan.set":
            record["plan"] = [PlanItem.model_validate(i) for i in p["items"]]
        elif et == "capability.activated":
            record["capabilities"] = record["capabilities"] + [
                CapabilityActivation.model_validate(p["activation"])
            ]
        elif et == "grants.extended":
            record["grants"] = GrantEnvelope.model_validate(p["grants"])
        elif et == "task.progress":
            task: TaskState = record["task"]
            record["task"] = task.model_copy(update={"progress": p["progress"]})
        else:
            raise ValueError(f"unknown state event: {et}")
