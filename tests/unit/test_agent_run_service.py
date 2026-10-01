"""AgentRunService RAM lifecycle (§41.1): the ONE shared EventBus per process
frees a finished run's history once the events are durable — and keeps it
when no store is wired (the run_hbench configuration: its own bus, no store).

Deterministic — a scripted model gateway, no network; workspace runs use tmp
dirs and the interpreter running the tests.
"""

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from aci.application.protocols import AgentRunStore
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.domain.runtime.actions import FinalCandidate, ToolCallBatchAction
from aci.domain.runtime.persistence import AgentRunEventRecord, AgentRunRecord
from aci.domain.runtime.subtask import AcceptanceCriterion, RunResult, SubtaskContract
from aci.domain.runtime.tools import ToolCall
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.event_bus import EventBus
from aci.runtime.model_gateway import FakeModelGateway
from aci.runtime.profiles import runtime_spec_for


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
    actions: list[Any], tmp_path: Path, *, bus: EventBus, store: AgentRunStore | None = None
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
        process_prefixes=[sys.executable],
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


def _run(service: AgentRunService) -> RunResult:
    return service.run(_contract(), runtime_spec_for("researcher"), max_turns=6, workspace="proj")


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

    def test_history_kept_when_no_store_is_wired(self, tmp_path: Path) -> None:
        """No store (the run_hbench configuration: its own bus, no run_store)
        → _persist early-returns and the RAM history stays readable."""
        bus = EventBus()
        _workspace(tmp_path)
        result = _run(_service(_actions(), tmp_path, bus=bus))
        assert result.status.value == "succeeded"
        history = bus.history(result.run_id)
        assert history, "RAM history must remain readable with no store"
        assert any(e.event_type == "run.created" for e in history)

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
