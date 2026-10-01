"""Agent run persistence (migration 0016, §41.1): a finished run + its event
history survive the process that produced them.

The row is a projection of the frozen terminal RunResult (INV-01: the store
is never a source of truth for a live run); events are the EventBus history
flattened (INV-15 made real). A persistence failure NEVER fails the run —
the result is already terminal and caller-visible.
"""

import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from aci.adapters.outbound.postgres.agent_runs import SqlAlchemyAgentRunRepository
from aci.application.protocols import AgentRunStore
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import FinalCandidate, ToolCallBatchAction
from aci.domain.runtime.subtask import AcceptanceCriterion, SubtaskContract
from aci.domain.runtime.tools import ToolCall
from aci.runtime.model_gateway import FakeModelGateway
from aci.runtime.profiles import runtime_spec_for

pytestmark = pytest.mark.integration

from datetime import UTC, datetime  # noqa: E402


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _service(actions: list[Any], store: AgentRunStore, tmp_path: Path) -> AgentRunService:
    class _Factory:
        def __init__(self, obj: object) -> None:
            self._obj = obj

        def build(self) -> object:
            return self._obj

    class _Null:
        def handle_request(self, request: object, snapshot: object) -> list[object]:
            return []

    from aci.runtime.context_engine import ContextBudget, ContextEngine

    return AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _Factory(FakeModelGateway(actions))),
        tool_executor_factory=cast(ModelGatewayFactory, _Factory(_Null())),
        capability_runtime_factory=cast(ModelGatewayFactory, _Factory(_Null())),
        context_engine_factory=cast(
            ModelGatewayFactory, _Factory(ContextEngine(ContextBudget(total_tokens=60_000)))
        ),
        workspace_root=tmp_path / "sources",
        runs_root=tmp_path / "runs",
        process_prefixes=[sys.executable],
        run_store=store,
    )


def _contract(objective: str = "fix the failing test") -> SubtaskContract:
    return SubtaskContract(
        task_id=uid("run"),
        objective=objective,
        global_context="",
        constraints=[],
        acceptance_criteria=[AcceptanceCriterion(criterion_id="ac-1", description="suite passes")],
        # researcher: claims grounded in a file the run READ (INV-08) — the
        # coder profile would require observed CHANGES.
        requested_profile="researcher",
        budget=None,
        created_at=datetime.now(UTC),
    )


def _workspace(tmp_path: Path) -> None:
    source = tmp_path / "sources" / "proj"
    source.mkdir(parents=True, exist_ok=True)
    (source / "notes.txt").write_text("cause is X\n", encoding="utf-8")


class TestAgentRunPersistence:
    def test_run_and_events_survive_the_process(self, sessions: Any, tmp_path: Path) -> None:
        """A finished run + its telemetry are readable from a FRESH service
        over the same store — the restart scenario, made testable."""
        store = SqlAlchemyAgentRunRepository(sessions)
        actions = [
            ToolCallBatchAction(
                calls=[ToolCall(call_id="c1", tool_id="read_file", arguments={"path": "notes.txt"})]
            ),
            FinalCandidate(
                summary="done",
                claims=["notes.txt: cause X"],
                # migration 0017: an artifact the run claims to have produced —
                # the researcher verifier does not check artifacts, so this
                # exercises the persistence path, not the verification one.
                artifacts=["out.txt"],
            ),
        ]
        _workspace(tmp_path)
        service = _service(actions, store, tmp_path)
        result = service.run(
            _contract(),
            runtime_spec_for("researcher"),
            max_turns=6,
            workspace="proj",
        )
        assert result.status.value == "succeeded"
        assert result.artifacts == ["out.txt"]  # the fixture really carries artifacts
        run_id = result.run_id

        # A FRESH service (same store, empty RAM) — the "restart".
        fresh = _service([], store, tmp_path)
        revived = fresh.get(run_id)
        assert revived is not None
        assert revived.run_id == run_id
        assert revived.status.value == "succeeded"
        assert revived.summary == "done"
        # migration 0017: artifacts/trace_ref round-trip through the fresh
        # service — the row is the projection of the WHOLE frozen terminal
        # state (INV-01), so no RunResult field is lost on read-back.
        assert revived.artifacts == ["out.txt"]
        assert revived.trace_ref is None  # nothing sets trace_ref on this path — None-stable

        # The row is the projection of the frozen terminal state.
        record = store.get_run(run_id)
        assert record is not None
        assert record.status == "succeeded"
        assert record.profile_id == "researcher"
        assert record.objective == "fix the failing test"
        assert record.workspace == "proj"
        assert record.artifacts == ["out.txt"]
        assert record.trace_ref is None
        assert record.usage["turns"] >= 1
        assert record.spec  # §52 reproducibility: the spec is pinned
        assert record.finished_at is not None

        # The event history is persisted and ordered (INV-15 made real).
        from sqlalchemy import select

        from aci.adapters.outbound.postgres.orm import AgentRunEventRow

        with sessions() as session:
            rows = session.scalars(
                select(AgentRunEventRow)
                .where(AgentRunEventRow.run_id == run_id)
                .order_by(AgentRunEventRow.seq)
            ).all()
        assert rows, "no events persisted"
        assert [r.seq for r in rows] == list(range(len(rows)))
        types = [r.event_type for r in rows]
        assert "run.created" in types
        assert "verification.started" in types

    def test_get_unknown_run_is_none(self, sessions: Any) -> None:
        store = SqlAlchemyAgentRunRepository(sessions)
        assert store.get_run("run-does-not-exist") is None

    def test_persistence_failure_never_fails_the_run(self, sessions: Any, tmp_path: Path) -> None:
        """The store is telemetry, not a dependency (§50): a crashing store
        logs and the terminal result still reaches the caller."""

        class _ExplodingStore:
            def record_run(self, record: object) -> None:
                raise RuntimeError("disk on fire")

            def record_events(self, events: list[object]) -> None:
                raise RuntimeError("disk on fire")

            def get_run(self, run_id: str) -> object:
                return None

            def list_recent(self, limit: int = 50) -> list[object]:
                return []

        actions = [
            ToolCallBatchAction(
                calls=[ToolCall(call_id="c1", tool_id="read_file", arguments={"path": "notes.txt"})]
            ),
            FinalCandidate(summary="done", claims=["notes.txt: cause X"]),
        ]
        _workspace(tmp_path)
        service = _service(actions, cast(AgentRunStore, _ExplodingStore()), tmp_path)
        result = service.run(
            _contract(), runtime_spec_for("researcher"), max_turns=6, workspace="proj"
        )
        # The run completed normally; only the telemetry was lost.
        assert result.status.value == "succeeded"

    def test_list_recent_is_newest_first(self, sessions: Any, tmp_path: Path) -> None:
        store = SqlAlchemyAgentRunRepository(sessions)
        actions = [FinalCandidate(summary=f"done {i}") for i in range(3)]
        _workspace(tmp_path)
        service = _service(actions, store, tmp_path)
        ids = []
        for i in range(3):
            result = service.run(
                _contract(f"objective {i}"),
                runtime_spec_for("researcher"),
                max_turns=6,
                workspace="proj",
            )
            ids.append(result.run_id)
        recent = store.list_recent(limit=10)
        assert [r.run_id for r in recent[:3]] == list(reversed(ids))


def _read_then_done() -> list[Any]:
    return [
        ToolCallBatchAction(
            calls=[ToolCall(call_id="c1", tool_id="read_file", arguments={"path": "notes.txt"})]
        ),
        FinalCandidate(summary="done", claims=["notes.txt: cause X"]),
    ]


class TestRevisionAfterRestart:
    """Migration 0018: the store carries the contract, the client options and
    the server-side working-copy path, so a FRESH process can revise a run it
    never executed — GET and revise agree after a restart."""

    def test_revise_of_a_store_only_run(self, sessions: Any, tmp_path: Path) -> None:
        store = SqlAlchemyAgentRunRepository(sessions)
        _workspace(tmp_path)
        first = _service(_read_then_done(), store, tmp_path).run(
            _contract(), runtime_spec_for("researcher"), max_turns=6, workspace="proj"
        )
        assert first.status.value == "succeeded"
        row = store.get_run(first.run_id)
        assert row is not None
        assert row.contract is not None and row.contract["task_id"] == first.run_id
        assert row.run_options is not None and row.run_options["workspace"] == "proj"
        assert row.run_dir == str((tmp_path / "runs" / first.run_id).resolve())

        fresh = _service(_read_then_done(), store, tmp_path)  # the "restart"
        revised = fresh.revise(first.run_id, failed_criteria=["ac-1"], feedback="again")

        assert revised.status.value == "succeeded"
        child = store.get_run(revised.run_id)
        assert child is not None
        assert child.parent_run_id == first.run_id
        assert child.contract is not None and child.contract["parent_task_id"] == first.run_id
        assert child.objective == "fix the failing test"
        assert (tmp_path / "runs" / revised.run_id / "notes.txt").is_file()

    def test_revise_with_a_vanished_run_dir_is_a_clean_error(
        self, sessions: Any, tmp_path: Path
    ) -> None:
        store = SqlAlchemyAgentRunRepository(sessions)
        _workspace(tmp_path)
        first = _service(_read_then_done(), store, tmp_path).run(
            _contract(), runtime_spec_for("researcher"), max_turns=6, workspace="proj"
        )
        shutil.rmtree(tmp_path / "runs" / first.run_id)
        with pytest.raises(DomainError) as excinfo:
            _service([], store, tmp_path).revise(first.run_id)
        assert excinfo.value.code is ErrorCode.WORKSPACE_NOT_FOUND
        assert str(tmp_path) not in str(excinfo.value)

    def test_cancel_of_a_store_only_run_is_false_not_an_error(
        self, sessions: Any, tmp_path: Path
    ) -> None:
        store = SqlAlchemyAgentRunRepository(sessions)
        _workspace(tmp_path)
        first = _service(_read_then_done(), store, tmp_path).run(
            _contract(), runtime_spec_for("researcher"), max_turns=6, workspace="proj"
        )
        assert _service([], store, tmp_path).cancel(first.run_id) is False
