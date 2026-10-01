"""AgentRunService RAM lifecycle (§41.1): the ONE shared EventBus per process
frees a finished run's history once the run is terminal — store or no store —
and a run recovered from the store after a restart (migration 0018) can be
revised (and cancelled) exactly like one still in RAM.

Deterministic — a scripted model gateway, no network; workspace runs use tmp
dirs and the interpreter running the tests.
"""

import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from aci.application.protocols import AgentRunStore
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import ClarificationRequest, FinalCandidate, ToolCallBatchAction
from aci.domain.runtime.persistence import (
    AgentRunCheckpointRecord,
    AgentRunEventRecord,
    AgentRunRecord,
)
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.subtask import AcceptanceCriterion, RunResult, SubtaskContract
from aci.domain.runtime.tools import ToolCall
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.event_bus import RUN_CANCELLED, EventBus
from aci.runtime.model_gateway import FakeModelGateway
from aci.runtime.profiles import runtime_spec_for
from aci.runtime.run_controller import CANCELLED_WHILE_PAUSED, SUPERSEDED_BY_REVISION


class _Factory:
    """ModelGatewayFactory returning one fixed collaborator (deterministic)."""

    def __init__(self, obj: object) -> None:
        self._obj = obj

    def build(self) -> object:
        return self._obj


class _NullHandler:
    def handle_request(self, request: object, snapshot: object) -> list[object]:
        return []


class _MemoryStore:
    """In-memory AgentRunStore double: records runs + events, never fails."""

    def __init__(self) -> None:
        self.runs: dict[str, AgentRunRecord] = {}
        self.events: dict[str, list[AgentRunEventRecord]] = {}

    def record_run(self, record: AgentRunRecord) -> None:
        self.runs[record.run_id] = record

    def record_events(self, events: list[AgentRunEventRecord]) -> None:
        for event in events:
            self.events.setdefault(event.run_id, []).append(event)

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    def list_recent(self, limit: int = 50) -> list[AgentRunRecord]:
        return list(self.runs.values())[:limit]


class _ExplodingStore(_MemoryStore):
    """The telemetry-failure path: every write raises (§50 — never the run)."""

    def record_run(self, record: AgentRunRecord) -> None:
        raise RuntimeError("disk on fire")

    def record_events(self, events: list[AgentRunEventRecord]) -> None:
        raise RuntimeError("disk on fire")


def _actions() -> list[Any]:
    """Read a file, then propose completion grounded in what was read."""
    return [
        ToolCallBatchAction(
            calls=[ToolCall(call_id="c1", tool_id="read_file", arguments={"path": "notes.txt"})]
        ),
        FinalCandidate(summary="done", claims=["notes.txt: cause X"]),
    ]


def _service(
    actions: list[Any],
    tmp_path: Path,
    *,
    bus: EventBus,
    store: AgentRunStore | None = None,
    process_prefixes: list[str] | None = None,
) -> AgentRunService:
    return AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _Factory(FakeModelGateway(actions))),
        tool_executor_factory=cast(ModelGatewayFactory, _Factory(_NullHandler())),
        capability_runtime_factory=cast(ModelGatewayFactory, _Factory(_NullHandler())),
        context_engine_factory=cast(
            ModelGatewayFactory, _Factory(ContextEngine(ContextBudget(total_tokens=60_000)))
        ),
        workspace_root=tmp_path / "sources",
        runs_root=tmp_path / "runs",
        process_prefixes=[sys.executable] if process_prefixes is None else process_prefixes,
        event_bus=bus,
        run_store=store,
    )


def _workspace(tmp_path: Path) -> None:
    source = tmp_path / "sources" / "proj"
    source.mkdir(parents=True, exist_ok=True)
    (source / "notes.txt").write_text("cause is X\n", encoding="utf-8")


def _contract() -> SubtaskContract:
    return SubtaskContract(
        task_id=f"run-{uuid.uuid4().hex[:8]}",
        objective="read the note",
        global_context="",
        constraints=[],
        acceptance_criteria=[AcceptanceCriterion(criterion_id="ac-1", description="note read")],
        # researcher: claims grounded in a file the run READ (INV-08) — the
        # coder profile would require observed CHANGES.
        requested_profile="researcher",
        budget=None,
        created_at=datetime.now(UTC),
    )


def _run(service: AgentRunService, **options: Any) -> RunResult:
    return service.run(
        _contract(), runtime_spec_for("researcher"), max_turns=6, workspace="proj", **options
    )


class TestEventHistoryLifecycle:
    def test_history_discarded_once_the_events_are_durable(self, tmp_path: Path) -> None:
        """With a store wired, _persist records the events and then frees the
        shared bus's RAM copy — a long-running server must not accumulate
        every finished run's telemetry forever (one bus per process)."""
        bus = EventBus()
        store = _MemoryStore()
        _workspace(tmp_path)
        result = _run(_service(_actions(), tmp_path, bus=bus, store=cast(AgentRunStore, store)))
        assert result.status.value == "succeeded"
        assert store.events[result.run_id], "events must be durable before the discard"
        assert bus.history(result.run_id) == []  # discarded — no dead weight

    def test_history_discarded_even_when_no_store_is_wired(self, tmp_path: Path) -> None:
        """No store (the run_hbench configuration: its own bus, no run_store):
        the run is terminal, its RAM history is still dead weight on the bus —
        freed all the same (H-bench/tests must not accumulate it forever)."""
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe(lambda e: seen.append(e.event_type))  # sinks still see every event
        _workspace(tmp_path)
        result = _run(_service(_actions(), tmp_path, bus=bus))
        assert result.status.value == "succeeded"
        assert "run.created" in seen
        assert bus.history(result.run_id) == []

    def test_history_discarded_even_when_persistence_fails(self, tmp_path: Path) -> None:
        """The run is terminal either way: a crashing store logs (§50 — the
        result stays caller-visible) and the RAM history is still freed."""
        bus = EventBus()
        _workspace(tmp_path)
        result = _run(
            _service(_actions(), tmp_path, bus=bus, store=cast(AgentRunStore, _ExplodingStore()))
        )
        assert result.status.value == "succeeded"  # telemetry failure never fails the run
        assert bus.history(result.run_id) == []  # freed even on the failure path


class TestRevisionAfterRestart:
    """Migration 0018: GET already read through to the store after a restart;
    revise must too — and still never exceed the CURRENT server ceiling."""

    def _first_run(self, tmp_path: Path, store: _MemoryStore, **options: Any) -> RunResult:
        _workspace(tmp_path)
        result = _run(
            _service(_actions(), tmp_path, bus=EventBus(), store=cast(AgentRunStore, store)),
            **options,
        )
        assert result.status.value == "succeeded"
        return result

    def _fresh(
        self, tmp_path: Path, store: _MemoryStore, *, process_prefixes: list[str] | None = None
    ) -> AgentRunService:
        """A new process: same store, empty RAM."""
        return _service(
            _actions(),
            tmp_path,
            bus=EventBus(),
            store=cast(AgentRunStore, store),
            process_prefixes=process_prefixes,
        )

    def test_revise_of_a_store_only_run_links_to_its_parent(self, tmp_path: Path) -> None:
        store = _MemoryStore()
        first = self._first_run(tmp_path, store)
        # The previous run's working copy carries its own change: the
        # revision must continue FROM it, not from the source workspace.
        (tmp_path / "runs" / first.run_id / "marker.txt").write_text("v1\n", encoding="utf-8")
        fresh = self._fresh(tmp_path, store)
        assert fresh.get(first.run_id) is not None  # GET already worked pre-0018

        revised = fresh.revise(first.run_id, failed_criteria=["ac-1"], feedback="again")

        assert revised.status.value == "succeeded"
        assert revised.run_id != first.run_id
        contract = fresh.contract(revised.run_id)
        assert contract is not None
        assert contract.parent_task_id == first.run_id
        assert contract.objective == "read the note"  # carried from the stored contract
        assert f"Previous attempt {first.run_id}" in contract.global_context
        assert store.runs[revised.run_id].parent_run_id == first.run_id
        assert (tmp_path / "runs" / revised.run_id / "marker.txt").read_text() == "v1\n"
        # The stored contract of a store-only run is readable too.
        stored_parent = fresh.contract(first.run_id)
        assert stored_parent is not None and stored_parent.task_id == first.run_id

    def test_persisted_revision_state_is_complete(self, tmp_path: Path) -> None:
        store = _MemoryStore()
        first = self._first_run(tmp_path, store, write_scopes=["."])
        row = store.runs[first.run_id]
        assert row.contract is not None and row.contract["task_id"] == first.run_id
        assert row.run_options == {
            "workspace": "proj",
            "verification_command": None,
            "write_scopes": ["."],
            "command_prefixes": None,
            "max_turns": 6,
            # migration 0019 era: client-added approval requirements (none here)
            "approval_required_tools": None,
            # 2026-10-01: per-run skill-preload override (none here)
            "preload_capabilities": None,
        }
        assert row.run_dir == str((tmp_path / "runs" / first.run_id).resolve())
        # Server-side only: no serialization of the record carries it.
        assert "run_dir" not in row.model_dump()
        assert str(tmp_path) not in row.model_dump_json()
        assert str(tmp_path) not in repr(row)

    def test_missing_run_dir_is_a_clean_error(self, tmp_path: Path) -> None:
        store = _MemoryStore()
        first = self._first_run(tmp_path, store)
        shutil.rmtree(tmp_path / "runs" / first.run_id)
        with pytest.raises(DomainError) as excinfo:
            self._fresh(tmp_path, store).revise(first.run_id)
        assert excinfo.value.code is ErrorCode.WORKSPACE_NOT_FOUND
        assert first.run_id in str(excinfo.value)
        assert str(tmp_path) not in str(excinfo.value)  # never the server path

    def test_row_cannot_point_the_revision_outside_the_runs_root(self, tmp_path: Path) -> None:
        store = _MemoryStore()
        first = self._first_run(tmp_path, store)
        elsewhere = tmp_path / "sources" / "proj"  # exists, but is not a run dir
        store.runs[first.run_id] = store.runs[first.run_id].model_copy(
            update={"run_dir": str(elsewhere)}
        )
        with pytest.raises(DomainError) as excinfo:
            self._fresh(tmp_path, store).revise(first.run_id)
        assert excinfo.value.code is ErrorCode.WORKSPACE_NOT_FOUND
        assert not any(p.name.startswith("run_") for p in (tmp_path / "runs").iterdir())

    def test_grants_come_from_the_current_ceiling_not_the_row(self, tmp_path: Path) -> None:
        """INV-02 across a restart: the stored command_prefixes are a request;
        a server whose ceiling has narrowed since refuses the revision."""
        store = _MemoryStore()
        first = self._first_run(tmp_path, store, command_prefixes=[sys.executable])
        with pytest.raises(DomainError) as excinfo:
            self._fresh(tmp_path, store, process_prefixes=[]).revise(first.run_id)
        assert excinfo.value.code is ErrorCode.PERMISSION_DENIED

    def test_tampered_row_cannot_widen_process_authority(self, tmp_path: Path) -> None:
        store = _MemoryStore()
        first = self._first_run(tmp_path, store)
        row = store.runs[first.run_id]
        assert row.run_options is not None
        store.runs[first.run_id] = row.model_copy(
            update={"run_options": {**row.run_options, "command_prefixes": ["/bin/sh"]}}
        )
        with pytest.raises(DomainError) as excinfo:
            self._fresh(tmp_path, store).revise(first.run_id)
        assert excinfo.value.code is ErrorCode.PERMISSION_DENIED

    def test_pre_0018_row_is_readable_but_not_revisable(self, tmp_path: Path) -> None:
        store = _MemoryStore()
        first = self._first_run(tmp_path, store)
        store.runs[first.run_id] = store.runs[first.run_id].model_copy(
            update={"contract": None, "run_options": None, "run_dir": None}
        )
        fresh = self._fresh(tmp_path, store)
        assert fresh.get(first.run_id) is not None
        with pytest.raises(DomainError) as excinfo:
            fresh.revise(first.run_id)
        assert excinfo.value.code is ErrorCode.TASK_TRANSITION_INVALID

    def test_unknown_run_is_still_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(DomainError) as excinfo:
            self._fresh(tmp_path, _MemoryStore()).revise("run_ghost")
        assert excinfo.value.code is ErrorCode.ROUTE_RUN_NOT_FOUND

    def test_cancel_of_a_store_only_run_matches_a_terminal_ram_run(self, tmp_path: Path) -> None:
        """A terminal run has nothing to cancel — the same False whether it
        finished in this process or in a previous one; never an error."""
        store = _MemoryStore()
        _workspace(tmp_path)
        live = _service(_actions(), tmp_path, bus=EventBus(), store=cast(AgentRunStore, store))
        first = _run(live)
        assert live.cancel(first.run_id) is False  # terminal, in RAM
        assert self._fresh(tmp_path, store).cancel(first.run_id) is False  # terminal, store-only


# -- cancel / revise of a PAUSED run (migration 0019 checkpoints) ------------


class _CheckpointStore(_MemoryStore):
    """_MemoryStore + the 0019 methods: pause checkpoints with an atomic
    compare-and-set claim, and appended (resumed / cancelled) segments."""

    def __init__(self) -> None:
        super().__init__()
        self.checkpoints: dict[str, AgentRunCheckpointRecord] = {}

    def append_events(self, events: list[AgentRunEventRecord]) -> None:
        self.record_events(events)

    def record_checkpoint(self, record: AgentRunCheckpointRecord) -> None:
        assert record.run_id in self.runs  # FK: the run row is written first
        self.checkpoints.setdefault(record.checkpoint_id, record)

    def latest_checkpoint(self, run_id: str) -> AgentRunCheckpointRecord | None:
        mine = [c for c in self.checkpoints.values() if c.run_id == run_id]
        return max(mine, key=lambda c: c.created_at) if mine else None

    def consume_checkpoint(self, checkpoint_id: str, *, at: datetime) -> bool:
        record = self.checkpoints.get(checkpoint_id)
        if record is None or record.consumed_at is not None:
            return False
        self.checkpoints[checkpoint_id] = record.model_copy(update={"consumed_at": at})
        return True


def _read() -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[ToolCall(call_id="c1", tool_id="read_file", arguments={"path": "notes.txt"})]
    )


def _done() -> FinalCandidate:
    return FinalCandidate(summary="done", claims=["notes.txt: cause X"])


class TestCancelAndReviseOfAPausedRun:
    """A paused run's ONE checkpoint is claimed by exactly one of resume /
    cancel / revise; cancel of a pause is a real CANCELLED (RAM + store),
    and a revision never leaves its paused parent resumable (one lineage)."""

    def _paused(
        self,
        tmp_path: Path,
        script: list[Any],
        *,
        store: _CheckpointStore | None = None,
        approval: bool = True,
        **options: Any,
    ) -> tuple[AgentRunService, RunResult, FakeModelGateway]:
        _workspace(tmp_path)
        gateway = FakeModelGateway(script)
        service = _service([], tmp_path, bus=EventBus(), store=cast(AgentRunStore, store))
        service._model_factory = _Factory(gateway)  # this test's script, same wiring
        if approval:
            service.require_approval_for(["read_file"])
        paused = _run(service, **options)
        expected = "interrupted_approval" if approval else "interrupted"
        assert paused.status.value == expected, paused
        return service, paused, gateway

    def _fresh(
        self,
        tmp_path: Path,
        store: _CheckpointStore,
        script: list[Any] | None = None,
        *,
        process_prefixes: list[str] | None = None,
    ) -> AgentRunService:
        """A new process: same store, empty RAM."""
        service = _service(
            script if script is not None else [],
            tmp_path,
            bus=EventBus(),
            store=cast(AgentRunStore, store),
            process_prefixes=process_prefixes,
        )
        service.require_approval_for(["read_file"])
        return service

    @staticmethod
    def _assert_cancelled(result: RunResult | None, detail: str) -> None:
        assert result is not None
        assert result.status is RunStatus.CANCELLED
        assert result.stop_reason is StopReason.CANCELLED  # what a live cancel gets
        assert result.detail_code == detail
        assert result.approval_id is None

    # -- cancel ------------------------------------------------------------

    def test_cancel_of_a_paused_run_cancels_it_and_burns_the_pause(self, tmp_path: Path) -> None:
        service, paused, gateway = self._paused(tmp_path, [_read(), _done()])
        calls_before = len(gateway.requests)

        assert service.cancel(paused.run_id) is True
        self._assert_cancelled(service.get(paused.run_id), CANCELLED_WHILE_PAUSED)
        with pytest.raises(DomainError) as excinfo:
            service.resume(paused.run_id, approval_id=paused.approval_id, approve=True)
        assert excinfo.value.code is ErrorCode.CHECKPOINT_CONSUMED
        assert service.cancel(paused.run_id) is False  # terminal now
        assert len(gateway.requests) == calls_before  # nothing pending ever executed

    def test_cancel_of_a_clarification_pause(self, tmp_path: Path) -> None:
        service, paused, _ = self._paused(
            tmp_path, [ClarificationRequest(question="which file?")], approval=False
        )
        assert service.cancel(paused.run_id) is True
        self._assert_cancelled(service.get(paused.run_id), CANCELLED_WHILE_PAUSED)
        with pytest.raises(DomainError) as excinfo:
            service.resume(paused.run_id, answer="notes.txt")
        assert excinfo.value.code is ErrorCode.CHECKPOINT_CONSUMED

    def test_cancel_of_a_paused_run_is_persisted(self, tmp_path: Path) -> None:
        store = _CheckpointStore()
        service, paused, _ = self._paused(tmp_path, [_read()], store=store)
        assert store.runs[paused.run_id].finished_at is None

        assert service.cancel(paused.run_id) is True
        row = store.runs[paused.run_id]
        assert row.status == "cancelled" and row.stop_reason == "CANCELLED"
        assert row.detail_code == CANCELLED_WHILE_PAUSED
        assert row.finished_at is not None
        checkpoint = store.latest_checkpoint(paused.run_id)
        assert checkpoint is not None and checkpoint.consumed_at is not None
        types = [e.event_type for e in store.events[paused.run_id]]
        assert types[-1] == RUN_CANCELLED  # appended after the pre-pause segment
        assert types[0] == "run.created"

    def test_cancel_of_a_store_only_paused_run_after_a_restart(self, tmp_path: Path) -> None:
        store = _CheckpointStore()
        _, paused, _ = self._paused(tmp_path, [_read()], store=store)

        restarted = self._fresh(tmp_path, store)
        assert restarted.cancel(paused.run_id) is True
        self._assert_cancelled(restarted.get(paused.run_id), CANCELLED_WHILE_PAUSED)
        row = store.runs[paused.run_id]
        assert row.status == "cancelled" and row.finished_at is not None
        assert [e.event_type for e in store.events[paused.run_id]][-1] == RUN_CANCELLED
        # Yet another process: the pause is gone for resume and cancel alike.
        another = self._fresh(tmp_path, store, [_read(), _done()])
        with pytest.raises(DomainError) as excinfo:
            another.resume(paused.run_id, approval_id=paused.approval_id, approve=True)
        assert excinfo.value.code is ErrorCode.CHECKPOINT_CONSUMED
        assert another.cancel(paused.run_id) is False
        self._assert_cancelled(another.get(paused.run_id), CANCELLED_WHILE_PAUSED)

    def test_cancel_after_resume_is_false(self, tmp_path: Path) -> None:
        store = _CheckpointStore()
        service, paused, _ = self._paused(tmp_path, [_read(), _done()], store=store)
        resumed = service.resume(paused.run_id, approval_id=paused.approval_id, approve=True)
        assert resumed.status is RunStatus.SUCCEEDED, resumed
        assert service.cancel(paused.run_id) is False
        assert self._fresh(tmp_path, store).cancel(paused.run_id) is False
        assert store.runs[paused.run_id].status == "succeeded"

    def test_resumed_success_row_carries_no_pause_detail(self, tmp_path: Path) -> None:
        """The persisted row follows the RunResult: a SUCCEEDED resume of an
        approval pause is stored without detail_code=APPROVAL_REQUIRED."""
        store = _CheckpointStore()
        service, paused, _ = self._paused(tmp_path, [_read(), _done()], store=store)
        assert store.runs[paused.run_id].detail_code == "APPROVAL_REQUIRED"
        resumed = service.resume(paused.run_id, approval_id=paused.approval_id, approve=True)
        assert resumed.status is RunStatus.SUCCEEDED, resumed
        assert resumed.detail_code is None
        row = store.runs[paused.run_id]
        assert row.status == "succeeded" and row.stop_reason == "SUCCESS"
        assert row.detail_code is None

    def test_a_resume_that_claimed_the_pause_first_wins(self, tmp_path: Path) -> None:
        """Another process claimed the pause (its resume is executing): this
        process's cancel loses the atomic claim — False, nothing written."""
        store = _CheckpointStore()
        service, paused, _ = self._paused(tmp_path, [_read()], store=store)
        stored = store.latest_checkpoint(paused.run_id)
        assert stored is not None
        assert store.consume_checkpoint(stored.checkpoint_id, at=datetime.now(UTC))
        assert service.cancel(paused.run_id) is False  # RAM-known pause, store claim lost
        assert self._fresh(tmp_path, store).cancel(paused.run_id) is False  # store-only
        assert store.runs[paused.run_id].status == "interrupted_approval"  # untouched

    def test_cancel_of_a_live_run_still_signals_its_token(self, tmp_path: Path) -> None:
        service, paused, _ = self._paused(tmp_path, [_read(), _done()])
        assert service.cancel("run_ghost") is False

        def _cancel_mid_run(request: Any) -> Any:
            assert service.cancel(paused.run_id) is True  # the resumed segment is live
            return ToolCallBatchAction(  # the kernel stops before this batch runs
                calls=[ToolCall(call_id="c2", tool_id="read_file", arguments={"path": "notes.txt"})]
            )

        service._model_factory = _Factory(FakeModelGateway([_cancel_mid_run]))
        resumed = service.resume(paused.run_id, approval_id=paused.approval_id, approve=True)
        assert resumed.status is RunStatus.CANCELLED
        assert resumed.detail_code != CANCELLED_WHILE_PAUSED

    # -- revise ------------------------------------------------------------

    def test_revise_of_a_paused_run_leaves_no_resumable_original(self, tmp_path: Path) -> None:
        service, paused, _ = self._paused(
            tmp_path,
            [ClarificationRequest(question="which file?"), _read(), _done()],
            approval=False,
        )
        revised = service.revise(paused.run_id, feedback="just read notes.txt")
        assert revised.status is RunStatus.SUCCEEDED, revised
        contract = service.contract(revised.run_id)
        assert contract is not None and contract.parent_task_id == paused.run_id
        self._assert_cancelled(service.get(paused.run_id), SUPERSEDED_BY_REVISION)
        with pytest.raises(DomainError) as excinfo:
            service.resume(paused.run_id, answer="notes.txt")
        assert excinfo.value.code is ErrorCode.CHECKPOINT_CONSUMED
        assert service.cancel(paused.run_id) is False

    def test_revise_of_a_store_only_paused_run(self, tmp_path: Path) -> None:
        store = _CheckpointStore()
        _, paused, _ = self._paused(
            tmp_path, [ClarificationRequest(question="which file?")], store=store, approval=False
        )
        restarted = self._fresh(tmp_path, store, [_read(), _done()])
        restarted._approval_floor = frozenset()  # this server gates nothing
        revised = restarted.revise(paused.run_id, feedback="just read notes.txt")
        assert revised.status is RunStatus.SUCCEEDED, revised
        assert store.runs[revised.run_id].parent_run_id == paused.run_id
        parent = store.runs[paused.run_id]
        assert parent.status == "cancelled" and parent.detail_code == SUPERSEDED_BY_REVISION
        assert parent.finished_at is not None
        checkpoint = store.latest_checkpoint(paused.run_id)
        assert checkpoint is not None and checkpoint.consumed_at is not None
        with pytest.raises(DomainError) as excinfo:
            self._fresh(tmp_path, store).resume(paused.run_id, answer="notes.txt")
        assert excinfo.value.code is ErrorCode.CHECKPOINT_CONSUMED

    def test_a_refused_revision_does_not_burn_the_pause(self, tmp_path: Path) -> None:
        store = _CheckpointStore()
        _, paused, _ = self._paused(
            tmp_path, [_read()], store=store, command_prefixes=[sys.executable]
        )
        narrowed = self._fresh(tmp_path, store, process_prefixes=[])  # ceiling shrank
        with pytest.raises(DomainError) as excinfo:
            narrowed.revise(paused.run_id)
        assert excinfo.value.code is ErrorCode.PERMISSION_DENIED
        checkpoint = store.latest_checkpoint(paused.run_id)
        assert checkpoint is not None and checkpoint.consumed_at is None
        assert store.runs[paused.run_id].status == "interrupted_approval"
        assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == [paused.run_id]
        resumed = self._fresh(tmp_path, store, [_done()]).resume(
            paused.run_id, approval_id=paused.approval_id, approve=True
        )
        assert resumed.status is RunStatus.SUCCEEDED, resumed

    def test_revise_after_a_concurrent_resume_finished_revises_the_terminal_run(
        self, tmp_path: Path
    ) -> None:
        """This process still holds the pause in RAM; another process resumed
        it to completion. The lost claim + a TERMINAL parent = the revision
        proceeds, built from the terminal result."""
        store = _CheckpointStore()
        service, paused, _ = self._paused(tmp_path, [_read()], store=store)
        other = self._fresh(tmp_path, store, [_done()])
        resumed = other.resume(paused.run_id, approval_id=paused.approval_id, approve=True)
        assert resumed.status is RunStatus.SUCCEEDED, resumed

        service._approval_floor = frozenset()
        service._model_factory = _Factory(FakeModelGateway([_read(), _done()]))
        revised = service.revise(paused.run_id, feedback="again")
        assert revised.status is RunStatus.SUCCEEDED, revised
        assert store.runs[revised.run_id].parent_run_id == paused.run_id
        assert store.runs[paused.run_id].status == "succeeded"  # never re-cancelled

    def test_revise_after_a_lost_claim_of_a_live_parent_is_refused(self, tmp_path: Path) -> None:
        """The pause was claimed elsewhere and the parent is not terminal (a
        resumed segment still executing): 409, nothing created."""
        store = _CheckpointStore()
        _, paused, _ = self._paused(tmp_path, [_read()], store=store)
        stored = store.latest_checkpoint(paused.run_id)
        assert stored is not None
        assert store.consume_checkpoint(stored.checkpoint_id, at=datetime.now(UTC))
        with pytest.raises(DomainError) as excinfo:
            self._fresh(tmp_path, store, [_read(), _done()]).revise(paused.run_id)
        assert excinfo.value.code is ErrorCode.CHECKPOINT_CONSUMED
        assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == [paused.run_id]
        assert len(store.runs) == 1
