"""Mutation-audit killing tests for ``src/aci/runtime/state_manager.py`` (job m6-mutation-audit).

Each test kills a class of surviving mutants from the mutmut before-run
(state_manager: 262 mutants, 208 killed + 1 suspicious, 53 survived). The
behaviors below had NO test in the module's relevant subset:

* ``StateEvent`` is frozen and its ``event_type`` boundary is min_length=1;
* ``create()`` defaults: a root run has ``depth == 0``;
* ``transition()``: ``started_at`` is set on the FIRST RUNNING only (never
  before, never reset on a resumed RUNNING), ``completed_at`` is None until a
  terminal transition;
* ``advance_turn`` and every typed helper bump the version by EXACTLY one
  (§8.5: every mutation is versioned so CAS detects intervening writes);
* ``consume_budget``: wall_time defaults to 0.0 and accumulates, cost rounds
  to 6 decimals, wall time to 3;
* the untested typed helpers actually mutate state: ``set_plan``,
  ``set_workspace``, ``count_recovery``, and ``task.progress`` events
  (replace and append semantics).

``model_dump(mode=...)`` string mutants are equivalent (a python-mode dump
round-trips through ``model_validate`` identically) and are not pinned.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.state import (
    BudgetLedger,
    CapabilityActivation,
    PlanItem,
    TaskState,
)
from aci.domain.runtime.stop_reason import RunStatus
from aci.runtime.state_manager import StateEvent, StateManager


def _create(sm: StateManager, run_id: str = "run-1") -> None:
    sm.create(
        run_id=run_id,
        task=TaskState(task_id="t", objective="fix bug"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
    )


class TestStateEventModel:
    def test_frozen_and_min_length_boundary(self) -> None:
        event = StateEvent(event_type="a")  # min_length=1 boundary
        assert event.event_type == "a"
        assert event.payload == {}
        with pytest.raises(ValidationError):
            event.event_type = "b"  # type: ignore[misc]


class TestCreateDefaults:
    def test_root_run_depth_is_zero(self) -> None:
        sm = StateManager()
        _create(sm)
        assert sm.snapshot("run-1").depth == 0


class TestTransitionTimestamps:
    def test_started_at_only_on_first_running(self) -> None:
        sm = StateManager()
        _create(sm)
        sm.transition("run-1", RunStatus.INITIALIZING)
        sm.transition("run-1", RunStatus.READY)
        assert sm.snapshot("run-1").run.started_at is None
        sm.transition("run-1", RunStatus.RUNNING)
        first = sm.snapshot("run-1").run.started_at
        assert first is not None
        # a resumed RUNNING (pause -> resume) never resets the original start
        sm.transition("run-1", RunStatus.INTERRUPTED)
        sm.transition("run-1", RunStatus.RUNNING)
        assert sm.snapshot("run-1").run.started_at == first

    def test_completed_at_none_until_terminal(self) -> None:
        sm = StateManager()
        _create(sm)
        for status in (RunStatus.INITIALIZING, RunStatus.READY, RunStatus.RUNNING):
            sm.transition("run-1", status)
            assert sm.snapshot("run-1").run.completed_at is None
        sm.transition("run-1", RunStatus.VERIFYING)
        assert sm.snapshot("run-1").run.completed_at is None
        sm.transition("run-1", RunStatus.SUCCEEDED)
        assert sm.snapshot("run-1").run.completed_at is not None


class TestVersioning:
    def test_advance_turn_bumps_version_exactly_one(self) -> None:
        sm = StateManager()
        _create(sm)
        before = sm.snapshot("run-1").run.version
        snap = sm.advance_turn("run-1")
        assert snap.run.version == before + 1
        assert snap.run.current_turn == 1

    def test_typed_helpers_bump_version_exactly_one(self) -> None:
        sm = StateManager()
        _create(sm)
        before = sm.snapshot("run-1").run.version
        snap = sm.set_plan("run-1", [PlanItem(item_id="p1", objective="step")])
        assert snap.run.version == before + 1
        assert [i.item_id for i in snap.plan] == ["p1"]

    def test_set_workspace_sets_the_workspace(self) -> None:
        sm = StateManager()
        _create(sm)
        before = sm.snapshot("run-1").run.version
        snap = sm.set_workspace("run-1", "ws-2")
        assert snap.workspace_id == "ws-2"
        assert snap.run.version == before + 1

    def test_count_recovery_counts_once_per_call(self) -> None:
        sm = StateManager()
        _create(sm)
        before = sm.snapshot("run-1").run.version
        snap = sm.count_recovery("run-1")
        assert snap.budget.consumed_recoveries == 1
        assert sm.count_recovery("run-1").budget.consumed_recoveries == 2
        assert snap.run.version == before + 1

    def test_capability_activation_bumps_version(self) -> None:
        sm = StateManager()
        _create(sm)
        before = sm.snapshot("run-1").run.version
        snap = sm.activate_capability(
            "run-1",
            CapabilityActivation(capability_id="cap-x", version="1", digest="d", activation_id="a"),
        )
        assert snap.run.version == before + 1


class TestBudgetConsumption:
    def test_wall_time_defaults_to_zero_and_accumulates(self) -> None:
        sm = StateManager()
        _create(sm)
        bare = sm.consume_budget("run-1")
        assert bare.budget.consumed_wall_time_seconds == 0.0
        snap = sm.consume_budget("run-1", wall_time_seconds=1.23456)
        assert snap.budget.consumed_wall_time_seconds == 1.235  # rounded to 3 decimals
        assert snap.budget.consumed_cost_usd == 0.0

    def test_cost_rounds_to_six_decimals(self) -> None:
        sm = StateManager()
        _create(sm)
        snap = sm.consume_budget("run-1", cost_usd=0.123456789)
        assert snap.budget.consumed_cost_usd == 0.123457


class TestTaskProgressEvents:
    def test_append_semantics(self) -> None:
        sm = StateManager()
        _create(sm)
        version = sm.snapshot("run-1").run.version
        snap = sm.commit(
            "run-1",
            expected_version=version,
            events=[StateEvent(event_type="task.progress", payload={"append": ["step-1"]})],
        )
        assert snap.task.progress == ["step-1"]
        version = snap.run.version
        snap = sm.commit(
            "run-1",
            expected_version=version,
            events=[StateEvent(event_type="task.progress", payload={"append": ["step-2"]})],
        )
        assert snap.task.progress == ["step-1", "step-2"]

    def test_progress_replaces(self) -> None:
        sm = StateManager()
        _create(sm)
        version = sm.snapshot("run-1").run.version
        snap = sm.commit(
            "run-1",
            expected_version=version,
            events=[StateEvent(event_type="task.progress", payload={"progress": ["only"]})],
        )
        assert snap.task.progress == ["only"]
