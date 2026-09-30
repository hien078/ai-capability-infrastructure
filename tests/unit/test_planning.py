"""PlanningStrategy unit tests (§10: propose, never commit)."""

from datetime import UTC, datetime

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.spec import PlanningPolicy
from aci.domain.runtime.state import (
    BudgetLedger,
    RunState,
    RuntimeStateSnapshot,
    TaskState,
)
from aci.domain.runtime.stop_reason import RunStatus
from aci.runtime.planning import PlanningStrategy


def _snapshot(criteria: list[str]) -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="r", status=RunStatus.RUNNING, created_at=datetime.now(UTC)),
        task=TaskState(task_id="t", objective="obj", acceptance_criteria=criteria),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
    )


class TestPlanningStrategy:
    def test_none_mode_empty_plan(self) -> None:
        strategy = PlanningStrategy(PlanningPolicy(mode="none"))
        assert strategy.initial_plan(_snapshot(["a", "b"])) == []

    def test_plan_from_acceptance_criteria(self) -> None:
        strategy = PlanningStrategy(PlanningPolicy(mode="adaptive"))
        plan = strategy.initial_plan(_snapshot(["tests pass", "lint clean"]))
        assert [p.objective for p in plan] == [
            "satisfy: tests pass",
            "satisfy: lint clean",
        ]
        assert plan[0].status == "running"
        assert plan[1].status == "pending"

    def test_falls_back_to_objective(self) -> None:
        strategy = PlanningStrategy(PlanningPolicy(mode="todo"))
        plan = strategy.initial_plan(_snapshot([]))
        assert len(plan) == 1
        assert "obj" in plan[0].objective

    def test_max_plan_items_bounded(self) -> None:
        strategy = PlanningStrategy(PlanningPolicy(mode="structured", max_plan_items=2))
        plan = strategy.initial_plan(_snapshot([f"c{i}" for i in range(10)]))
        assert len(plan) == 2

    def test_mark_done_references_evidence(self) -> None:
        strategy = PlanningStrategy(PlanningPolicy())
        plan = strategy.initial_plan(_snapshot(["a", "b"]))
        done = PlanningStrategy.mark_done(plan, "plan-1", evidence_ref="artifact://t/1")
        assert done[0].status == "done"
        assert done[0].evidence_refs == ["artifact://t/1"]
        assert done[1].status == "pending"
        again = PlanningStrategy.mark_done(done, "plan-1", evidence_ref="artifact://t/2")
        assert again[0].evidence_refs == ["artifact://t/1", "artifact://t/2"]

    def test_should_replan_only_on_material_triggers(self) -> None:
        strategy = PlanningStrategy(PlanningPolicy())
        snap = _snapshot([])
        assert strategy.should_replan(snap, trigger="verification_failure")
        assert strategy.should_replan(snap, trigger="capability_change")
        assert strategy.should_replan(snap, trigger="major_new_evidence")
        assert not strategy.should_replan(snap, trigger="new_turn")

    def test_replans_bounded_by_policy(self) -> None:
        """§6.4/§18.4: max_replans is a hard bound, whatever the trigger."""
        strategy = PlanningStrategy(PlanningPolicy(max_replans=2))
        snap = _snapshot([])
        assert strategy.should_replan(snap, trigger="verification_failure", replans=1)
        assert not strategy.should_replan(snap, trigger="verification_failure", replans=2)
        assert not strategy.should_replan(snap, trigger="capability_change", replans=5)
        never = PlanningStrategy(PlanningPolicy(max_replans=0))
        assert not never.should_replan(snap, trigger="verification_failure")
        none_mode = PlanningStrategy(PlanningPolicy(mode="none"))
        assert not none_mode.should_replan(snap, trigger="verification_failure")
