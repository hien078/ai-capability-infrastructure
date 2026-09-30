"""DelegationManager (harness.md §15): controlled task decomposition.

OFF by default (§0.3). Child authority ⊆ parent (INV-02), child budget carved
from what the parent has LEFT in every dimension (INV-03), child context
projected — never cloned (§15.3).
"""

from datetime import UTC, datetime
from typing import Protocol

from aci.domain.runtime.actions import DelegationRequest
from aci.domain.runtime.authority import GrantEnvelope, intersect_grants
from aci.domain.runtime.state import BudgetLedger, RuntimeStateSnapshot
from aci.domain.runtime.subtask import SubtaskContract

#: Fraction of the parent's cost ceiling a child may spend (§15.5 conservative).
CHILD_COST_SHARE = 0.3
DEFAULT_CHILD_TOOL_CALLS = 20


class DelegationDisabled(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"delegation disabled for {run_id} (§0.3 default)")
        self.run_id = run_id


class DelegationBudgetError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)


class ChildRunner(Protocol):
    """What the runtime needs to actually execute a child run."""

    def run_child(
        self, contract: SubtaskContract, grants: GrantEnvelope, budget: BudgetLedger
    ) -> str: ...


class DelegationManager:
    """Validates + projects a delegation request into a child SubtaskContract.

    The actual child execution goes through ``ChildRunner`` (the kernel wires
    it to a nested HarnessKernel run); this manager only enforces the
    invariants and carves the budget."""

    def __init__(
        self,
        *,
        enabled: bool,
        max_depth: int = 1,
        max_children: int = 3,
        child_budget_reserve: int = 20_000,
    ) -> None:
        self._enabled = enabled
        self._max_depth = max_depth
        self._max_children = max_children
        self._reserve = child_budget_reserve
        self._children: dict[str, list[str]] = {}

    def derive_child(
        self,
        parent: RuntimeStateSnapshot,
        request: DelegationRequest,
        *,
        child_grants: GrantEnvelope,
    ) -> tuple[SubtaskContract, GrantEnvelope, BudgetLedger]:
        """§15.2/§15.3 — validate, project context, carve budget, derive grants."""
        if not self._enabled:
            raise DelegationDisabled(parent.run.run_id)
        if parent.depth >= self._max_depth:
            raise DelegationBudgetError(f"delegation depth {parent.depth} >= max {self._max_depth}")
        children = self._children.setdefault(parent.run.run_id, [])
        if len(children) >= self._max_children:
            raise DelegationBudgetError(
                f"max children {self._max_children} reached for {parent.run.run_id}"
            )
        child_budget = self._carve_budget(parent.budget, request)
        grants = intersect_grants(parent.grants, child_grants)  # INV-02: never wider
        contract = SubtaskContract(
            task_id=f"{parent.run.run_id}-child-{len(children) + 1}",
            parent_task_id=parent.task.task_id,
            objective=request.subtask_objective,
            global_context=parent.task.objective,  # projected summary, NOT full context
            requested_profile="coder",
            budget=child_budget,
            created_at=datetime.now(UTC),
        )
        children.append(contract.task_id)
        return contract, grants, child_budget

    def _carve_budget(self, parent: BudgetLedger, request: DelegationRequest) -> BudgetLedger:
        """INV-03: every child limit is at most what the parent has left."""
        remaining_tokens = (
            parent.max_total_tokens
            - parent.consumed_input_tokens
            - parent.consumed_output_tokens
            - parent.reserved_tokens
        )
        if remaining_tokens < self._reserve:
            raise DelegationBudgetError(
                f"remaining budget {remaining_tokens} below child reserve {self._reserve}"
            )
        remaining = {
            "turns": parent.max_turns - parent.consumed_turns,
            "output tokens": parent.max_output_tokens - parent.consumed_output_tokens,
            "tool calls": parent.max_tool_calls - parent.consumed_tool_calls,
            "wall time": int(parent.max_wall_time_seconds - parent.consumed_wall_time_seconds),
        }
        for name, left in remaining.items():
            if left < 1:
                raise DelegationBudgetError(f"parent has no remaining {name} to delegate")
        carved = min(request.requested_budget_tokens or self._reserve, remaining_tokens)
        remaining_cost = parent.max_cost_usd - parent.consumed_cost_usd - parent.reserved_cost_usd
        return BudgetLedger(
            max_turns=remaining["turns"],
            max_total_tokens=carved,
            max_output_tokens=min(remaining["output tokens"], carved),
            max_tool_calls=min(
                request.requested_budget_tool_calls or DEFAULT_CHILD_TOOL_CALLS,
                remaining["tool calls"],
            ),
            max_wall_time_seconds=remaining["wall time"],
            max_cost_usd=round(
                max(min(parent.max_cost_usd * CHILD_COST_SHARE, remaining_cost), 0.0), 6
            ),
            max_recoveries=max(parent.max_recoveries - parent.consumed_recoveries, 0),
        )

    def record_child_result(self, parent_run_id: str, child_task_id: str) -> None:
        """§15.4 — child results merge as external structured observations;
        the kernel validates the child ResultContract before merging."""
        children = self._children.get(parent_run_id, [])
        if child_task_id not in children:
            raise ValueError(f"unknown child task {child_task_id} for {parent_run_id}")

    def child_count(self, parent_run_id: str) -> int:
        return len(self._children.get(parent_run_id, []))
