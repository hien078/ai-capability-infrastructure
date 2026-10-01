"""StateManager (harness.md §8): the ONLY mutable runtime-state authority.

INV-01: every other component reads frozen snapshots and commits changes back
through versioned commits (§8.5 compare-and-swap). No hidden competing copies.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from aci.domain.runtime.authority import GrantEnvelope, narrow_grants
from aci.domain.runtime.evidence import EvidenceItem
from aci.domain.runtime.state import (
    BudgetLedger,
    CapabilityActivation,
    PlanItem,
    RunState,
    RuntimeStateSnapshot,
    TaskState,
    TranscriptEntry,
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
            "changed_resources": [],
            "observed_evidence": [],
            "transcript": [],
        }
        return self.snapshot(run_id)

    def restore(self, snapshot: RuntimeStateSnapshot) -> RuntimeStateSnapshot:
        """§17.4 resume: install a checkpointed snapshot AS the run's
        authoritative state — version included, so CAS continuity holds
        across the pause (INV-01): the first write after a resume is
        ``snapshot.run.version + 1``, exactly as if the process had never
        stopped. A run already known here may only be restored from a
        snapshot of its CURRENT version (nothing written since the pause);
        anything else is a conflict, never a silent rollback."""
        run_id = snapshot.run.run_id
        existing = self._runs.get(run_id)
        if existing is not None and existing["run"].version != snapshot.run.version:
            raise StateCommitConflict(run_id, snapshot.run.version, existing["run"].version)
        self._runs[run_id] = {
            "run": snapshot.run,
            "task": snapshot.task,
            "budget": snapshot.budget,
            "grants": snapshot.grants,
            "plan": list(snapshot.plan),
            "capabilities": list(snapshot.active_capabilities),
            "workspace_id": snapshot.workspace_id,
            "depth": snapshot.depth,
            "changed_resources": list(snapshot.changed_resources),
            "observed_evidence": list(snapshot.observed_evidence),
            "transcript": list(snapshot.transcript),
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
            changed_resources=list(record["changed_resources"]),
            observed_evidence=list(record["observed_evidence"]),
            transcript=list(record["transcript"]),
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
        if status is RunStatus.RUNNING:
            # A live run has no stop: re-entering RUNNING (a resumed pause, a
            # recovery) clears the previous stop's reason + detail, so a pause's
            # APPROVAL_REQUIRED / CLARIFICATION_REQUIRED never leaks into the
            # terminal result (2026-10-01 real-model finding).
            update["stop_reason"] = None
            update["detail_code"] = None
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

    # -- typed helpers: the kernel's own writes, versioned like any commit ----

    def advance_turn(self, run_id: str) -> RuntimeStateSnapshot:
        """One model turn: the run's turn counter AND the budget ledger (§7.6)."""
        record = self._runs[run_id]
        run: RunState = record["run"]
        budget: BudgetLedger = record["budget"]
        record["run"] = run.model_copy(
            update={"current_turn": run.current_turn + 1, "version": run.version + 1}
        )
        record["budget"] = budget.model_copy(update={"consumed_turns": budget.consumed_turns + 1})
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
        return self._mutate(
            run_id,
            StateEvent(
                event_type="budget.consumed",
                payload={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "tool_calls": tool_calls,
                    "cost_usd": cost_usd,
                    "wall_time_seconds": wall_time_seconds,
                },
            ),
        )

    def count_recovery(self, run_id: str) -> RuntimeStateSnapshot:
        return self._mutate(run_id, StateEvent(event_type="recovery.counted"))

    def set_plan(self, run_id: str, plan: list[PlanItem]) -> RuntimeStateSnapshot:
        return self._mutate(
            run_id,
            StateEvent(
                event_type="plan.set", payload={"items": [i.model_dump(mode="json") for i in plan]}
            ),
        )

    def activate_capability(
        self, run_id: str, activation: CapabilityActivation
    ) -> RuntimeStateSnapshot:
        return self._mutate(
            run_id,
            StateEvent(
                event_type="capability.activated",
                payload={"activation": activation.model_dump(mode="json")},
            ),
        )

    def extend_grants(self, run_id: str, grants: GrantEnvelope) -> RuntimeStateSnapshot:
        return self._mutate(
            run_id,
            StateEvent(
                event_type="grants.extended", payload={"grants": grants.model_dump(mode="json")}
            ),
        )

    def narrow_grants(self, run_id: str, bound: GrantEnvelope) -> RuntimeStateSnapshot:
        """INV-02: grants := grants ∩ bound (scopes and expiry) — this event
        can only ever REMOVE authority (a resume under a narrowed ceiling)."""
        return self._mutate(
            run_id,
            StateEvent(
                event_type="grants.narrowed", payload={"bound": bound.model_dump(mode="json")}
            ),
        )

    def set_workspace(self, run_id: str, workspace_id: str) -> RuntimeStateSnapshot:
        return self._mutate(
            run_id, StateEvent(event_type="workspace.set", payload={"workspace_id": workspace_id})
        )

    def append_transcript(
        self, run_id: str, entries: list[TranscriptEntry]
    ) -> RuntimeStateSnapshot:
        return self._mutate(run_id, transcript_event(entries))

    def _mutate(self, run_id: str, event: StateEvent) -> RuntimeStateSnapshot:
        """Apply one event and bump the version — every mutation is versioned,
        so a CAS commit detects ANY intervening write (§8.5)."""
        record = self._runs[run_id]
        self._apply(record, event)
        run: RunState = record["run"]
        record["run"] = run.model_copy(update={"version": run.version + 1})
        return self.snapshot(run_id)

    def _apply(self, record: dict[str, Any], event: StateEvent) -> None:
        """Apply one StateEvent to the record (called inside commit)."""
        et, p = event.event_type, event.payload
        if et == "plan.set":
            record["plan"] = [PlanItem.model_validate(i) for i in p["items"]]
        elif et == "capability.activated":
            activation = CapabilityActivation.model_validate(p["activation"])
            record["capabilities"] = [
                a for a in record["capabilities"] if a.capability_id != activation.capability_id
            ] + [activation]
        elif et == "grants.extended":
            record["grants"] = GrantEnvelope.model_validate(p["grants"])
        elif et == "grants.narrowed":
            record["grants"] = narrow_grants(
                record["grants"], GrantEnvelope.model_validate(p["bound"])
            )
        elif et == "workspace.set":
            record["workspace_id"] = p["workspace_id"]
        elif et == "tool.observed":
            merged = [*record["changed_resources"], *p.get("resources", [])]
            record["changed_resources"] = list(dict.fromkeys(merged))
            record["observed_evidence"] = record["observed_evidence"] + [
                EvidenceItem.model_validate(e) for e in p.get("evidence", [])
            ]
        elif et == "transcript.append":
            record["transcript"] = record["transcript"] + [
                TranscriptEntry.model_validate(e) for e in p["entries"]
            ]
        elif et == "budget.consumed":
            budget: BudgetLedger = record["budget"]
            record["budget"] = budget.model_copy(
                update={
                    "consumed_input_tokens": budget.consumed_input_tokens
                    + p.get("input_tokens", 0),
                    "consumed_output_tokens": budget.consumed_output_tokens
                    + p.get("output_tokens", 0),
                    "consumed_tool_calls": budget.consumed_tool_calls + p.get("tool_calls", 0),
                    "consumed_cost_usd": round(
                        budget.consumed_cost_usd + p.get("cost_usd", 0.0), 6
                    ),
                    "consumed_wall_time_seconds": round(
                        budget.consumed_wall_time_seconds + p.get("wall_time_seconds", 0.0), 3
                    ),
                }
            )
        elif et == "recovery.counted":
            ledger: BudgetLedger = record["budget"]
            record["budget"] = ledger.model_copy(
                update={"consumed_recoveries": ledger.consumed_recoveries + 1}
            )
        elif et == "task.progress":
            task: TaskState = record["task"]
            progress = p["progress"] if "progress" in p else [*task.progress, *p["append"]]
            record["task"] = task.model_copy(update={"progress": progress})
        else:
            raise ValueError(f"unknown state event: {et}")


def transcript_event(entries: list[TranscriptEntry]) -> StateEvent:
    return StateEvent(
        event_type="transcript.append",
        payload={"entries": [e.model_dump(mode="json") for e in entries]},
    )
