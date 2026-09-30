"""PlanningStrategy (harness.md §10): interchangeable planning modes.

The planner PROPOSES plan items; StateManager commits them (§10.4). Planning
is adaptive — skipped for trivial tasks; replan only on materially new
information (verification failure / capability change), never every turn,
and at most ``max_replans`` times (§6.4).
"""

from aci.domain.runtime.spec import PlanningPolicy
from aci.domain.runtime.state import PlanItem, RuntimeStateSnapshot

REPLAN_TRIGGERS = frozenset({"verification_failure", "capability_change", "major_new_evidence"})


class PlanningStrategy:
    """v2 deterministic planner: derives plan items from the objective's
    acceptance criteria (§10.3). No LLM in the default strategy."""

    def __init__(self, policy: PlanningPolicy) -> None:
        self._policy = policy

    def initial_plan(self, snapshot: RuntimeStateSnapshot) -> list[PlanItem]:
        if self._policy.mode == "none":
            return []
        criteria = snapshot.task.acceptance_criteria or [snapshot.task.objective]
        items = [
            PlanItem(
                item_id=f"plan-{i + 1}",
                objective=f"satisfy: {criterion}",
                status="pending",
            )
            for i, criterion in enumerate(criteria[: self._policy.max_plan_items])
        ]
        if items:
            items[0] = items[0].model_copy(update={"status": "running"})
        return items

    def should_replan(
        self, snapshot: RuntimeStateSnapshot, *, trigger: str, replans: int = 0
    ) -> bool:
        """§6.4 refresh_on triggers, bounded by ``max_replans``; ``replans`` is
        how many replans the run has already committed (the caller keeps that
        count in authoritative state, INV-01)."""
        if self._policy.mode == "none" or replans >= self._policy.max_replans:
            return False
        return trigger in REPLAN_TRIGGERS

    @staticmethod
    def mark_done(plan: list[PlanItem], item_id: str, *, evidence_ref: str) -> list[PlanItem]:
        """§10.4 — 'done' references the evidence that closed it."""
        return [
            item.model_copy(
                update={"status": "done", "evidence_refs": [*item.evidence_refs, evidence_ref]}
            )
            if item.item_id == item_id
            else item
            for item in plan
        ]
