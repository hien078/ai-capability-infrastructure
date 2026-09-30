"""StateManager unit tests (INV-01: one mutable authority, CAS commits)."""

import pytest

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.state import (
    BudgetLedger,
    CapabilityActivation,
    TaskState,
)
from aci.domain.runtime.stop_reason import RunStatus
from aci.runtime.state_manager import (
    StateCommitConflict,
    StateEvent,
    StateManager,
)


def _create(sm: StateManager, run_id: str = "run-1") -> None:
    sm.create(
        run_id=run_id,
        task=TaskState(task_id="t", objective="fix bug"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
    )


class TestStateManager:
    def test_create_and_snapshot(self) -> None:
        sm = StateManager()
        _create(sm)
        snap = sm.snapshot("run-1")
        assert snap.run.run_id == "run-1"
        assert snap.run.status is RunStatus.CREATED
        assert snap.run.version == 1

    def test_duplicate_run_rejected(self) -> None:
        sm = StateManager()
        _create(sm)
        with pytest.raises(ValueError, match="already exists"):
            _create(sm)

    def test_snapshot_is_frozen(self) -> None:
        sm = StateManager()
        _create(sm)
        snap = sm.snapshot("run-1")
        with pytest.raises(Exception):  # noqa: B017, pytest.raises(Exception)
            snap.run.current_turn = 99  # type: ignore[misc]

    def test_legal_transition_chain(self) -> None:
        sm = StateManager()
        _create(sm)
        sm.transition("run-1", RunStatus.INITIALIZING)
        sm.transition("run-1", RunStatus.READY)
        sm.transition("run-1", RunStatus.RUNNING)
        snap = sm.transition("run-1", RunStatus.VERIFYING)
        assert snap.run.status is RunStatus.VERIFYING
        final = sm.transition("run-1", RunStatus.SUCCEEDED)
        assert final.run.completed_at is not None

    def test_illegal_transition_rejected(self) -> None:
        sm = StateManager()
        _create(sm)
        with pytest.raises(ValueError, match="illegal transition"):
            sm.transition("run-1", RunStatus.SUCCEEDED)

    def test_terminal_is_immutable(self) -> None:
        sm = StateManager()
        _create(sm)
        for status in (RunStatus.INITIALIZING, RunStatus.READY, RunStatus.RUNNING):
            sm.transition("run-1", status)
        sm.transition("run-1", RunStatus.CANCELLED)
        with pytest.raises(ValueError, match="illegal transition"):
            sm.transition("run-1", RunStatus.RUNNING)

    def test_cas_commit_conflict(self) -> None:
        sm = StateManager()
        _create(sm)
        snap = sm.snapshot("run-1")
        sm.commit("run-1", expected_version=snap.run.version, events=[])
        with pytest.raises(StateCommitConflict):
            sm.commit("run-1", expected_version=snap.run.version, events=[])

    def test_commit_applies_events_atomically(self) -> None:
        sm = StateManager()
        _create(sm)
        snap = sm.snapshot("run-1")
        new_snap = sm.commit(
            "run-1",
            expected_version=snap.run.version,
            events=[
                StateEvent(
                    event_type="plan.set",
                    payload={"items": [{"item_id": "p1", "objective": "step"}]},
                )
            ],
        )
        assert new_snap.plan[0].item_id == "p1"
        assert new_snap.run.version == snap.run.version + 1

    def test_unknown_state_event_rejected(self) -> None:
        sm = StateManager()
        _create(sm)
        snap = sm.snapshot("run-1")
        with pytest.raises(ValueError, match="unknown state event"):
            sm.commit(
                "run-1",
                expected_version=snap.run.version,
                events=[StateEvent(event_type="bogus")],
            )

    def test_budget_consumption_accumulates(self) -> None:
        sm = StateManager()
        _create(sm)
        sm.consume_budget("run-1", input_tokens=100, output_tokens=50, cost_usd=0.1)
        snap = sm.consume_budget("run-1", input_tokens=200, tool_calls=3)
        assert snap.budget.consumed_input_tokens == 300
        assert snap.budget.consumed_output_tokens == 50
        assert snap.budget.consumed_tool_calls == 3
        assert snap.budget.consumed_cost_usd == pytest.approx(0.1)

    def test_capability_activation_replaces_same_id(self) -> None:
        sm = StateManager()
        _create(sm)
        a1 = CapabilityActivation(
            capability_id="cap-x", version="1", digest="d1", activation_id="act-1"
        )
        a2 = CapabilityActivation(
            capability_id="cap-x", version="2", digest="d2", activation_id="act-2"
        )
        sm.activate_capability("run-1", a1)
        snap = sm.activate_capability("run-1", a2)
        assert len(snap.active_capabilities) == 1
        assert snap.active_capabilities[0].version == "2"

    def test_grant_extension(self) -> None:
        sm = StateManager()
        _create(sm)
        from aci.domain.runtime.authority import FilesystemScope

        wider = GrantEnvelope(filesystem=FilesystemScope(write=["/repo"]))
        snap = sm.extend_grants("run-1", wider)
        assert snap.grants.filesystem.write == ["/repo"]

    def test_advance_turn(self) -> None:
        sm = StateManager()
        _create(sm)
        assert sm.advance_turn("run-1").run.current_turn == 1
        snap = sm.advance_turn("run-1")
        assert snap.run.current_turn == 2
        # §7.6: the budget ledger counts turns too — budget.max_turns is live.
        assert snap.budget.consumed_turns == 2

    def test_tool_observed_records_confirmed_effects_once(self) -> None:
        sm = StateManager()
        _create(sm)
        version = sm.snapshot("run-1").run.version
        sm.commit(
            "run-1",
            expected_version=version,
            events=[
                StateEvent(event_type="tool.observed", payload={"resources": ["file:a", "file:a"]})
            ],
        )
        snap = sm.commit(
            "run-1",
            expected_version=version + 1,
            events=[
                StateEvent(event_type="tool.observed", payload={"resources": ["file:b", "file:a"]})
            ],
        )
        assert snap.changed_resources == ["file:a", "file:b"]

    def test_started_at_set_on_first_running(self) -> None:
        sm = StateManager()
        _create(sm)
        for status in (RunStatus.INITIALIZING, RunStatus.READY):
            sm.transition("run-1", status)
        snap = sm.transition("run-1", RunStatus.RUNNING)
        assert snap.run.started_at is not None
        assert snap.run.started_at.tzinfo is not None
