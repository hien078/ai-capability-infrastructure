"""The agent-run CHANGE read model against the LIVE dev DB (the shared
platform-surface contract, 2026-10-03; xl-harness FINDINGS 1-2).

A run's ``usage`` + ``changes`` survive the process that produced them:
read back through the durable store (migrations 0016/0018 — the row's
``run_dir``) plus the on-disk start manifest, by a FRESH service — the
restart scenario. Needs live PostgreSQL + ``alembic upgrade head``; skips
otherwise (the conftest ``sessions`` fixture).
"""

import hashlib
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest import agent_runs as rest_agent_runs
from aci.adapters.inbound.rest.errors import register_error_handlers
from aci.adapters.inbound.rest.wiring import Container, Settings
from aci.adapters.outbound.postgres.agent_runs import SqlAlchemyAgentRunRepository
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.application.workspace_changes import manifest_path
from aci.domain.runtime.actions import FinalCandidate, ToolCallBatchAction
from aci.domain.runtime.subtask import AcceptanceCriterion, SubtaskContract
from aci.domain.runtime.tools import ToolCall
from aci.runtime.model_gateway import FakeModelGateway
from aci.runtime.profiles import runtime_spec_for

pytestmark = pytest.mark.integration


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _service(actions: list[Any], store: object, tmp_path: Path) -> AgentRunService:
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
        run_store=cast("Any", store),
    )


def _contract(objective: str = "write the result file") -> SubtaskContract:
    return SubtaskContract(
        task_id=uid("run"),
        objective=objective,
        global_context="",
        constraints=[],
        acceptance_criteria=[
            AcceptanceCriterion(criterion_id="ac-1", description="out.txt exists")
        ],
        requested_profile="coder",
        budget=None,
        created_at=datetime.now(UTC),
    )


def _workspace(tmp_path: Path) -> None:
    source = tmp_path / "sources" / "proj"
    source.mkdir(parents=True, exist_ok=True)
    (source / "notes.txt").write_text("cause is X\n", encoding="utf-8")


def _client(service: AgentRunService) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    container = cast(
        Container,
        type(
            "C",
            (),
            {"agent_run_service": service, "settings": Settings(agent_runs_token="")},
        )(),
    )
    app.dependency_overrides[rest_agent_runs.get_container] = lambda: container
    app.include_router(rest_agent_runs.router)
    return TestClient(app)


def _write(path: str, content: str) -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[
            ToolCall(
                call_id="c1", tool_id="write_file", arguments={"path": path, "content": content}
            )
        ]
    )


class TestChangesAfterRestart:
    def test_changes_and_usage_survive_a_restart_via_the_store(
        self, sessions: Any, tmp_path: Path
    ) -> None:
        """The restart scenario, end to end on the live store: the fresh
        service reads the result (the row) AND recomputes the changes (the
        row's run_dir + the on-disk start manifest) — both on the wire."""
        store = SqlAlchemyAgentRunRepository(sessions)
        _workspace(tmp_path)
        service = _service(
            [
                _write("out.txt", "result\n"),
                FinalCandidate(summary="wrote it", changes=["out.txt"]),
            ],
            store,
            tmp_path,
        )
        result = service.run(_contract(), runtime_spec_for("coder"), max_turns=6, workspace="proj")
        assert result.status.value == "succeeded", result
        run_id = result.run_id
        assert manifest_path(tmp_path / "runs", run_id).is_file()

        # The durable usage projection (§41.1, migration 0016) carries the
        # tokens (xl-harness FINDING 2's bench-side path) — now also on GET.
        row = store.get_run(run_id)
        assert row is not None
        assert row.usage["model_input_tokens"] > 0
        assert row.usage["model_output_tokens"] > 0

        # A FRESH service (same store + dirs, empty RAM) — the "restart".
        fresh = _service([], store, tmp_path)
        changes = fresh.changes(run_id)
        assert changes is not None
        assert [f.path for f in changes.files] == ["out.txt"]
        assert changes.files[0].status == "added"
        assert changes.files[0].sha256_after == hashlib.sha256(b"result\n").hexdigest()
        assert changes.files[0].size_after == len("result\n")

        got = _client(fresh).get(f"/v1/agent-runs/{run_id}").json()
        assert got["status"] == "succeeded"
        assert got["usage"]["model_input_tokens"] == row.usage["model_input_tokens"]
        assert got["usage"]["model_output_tokens"] == row.usage["model_output_tokens"]
        assert got["usage"]["turns"] == row.usage["turns"]
        assert got["usage"]["tool_calls"] == row.usage["tool_calls"]
        assert got["usage"]["wall_seconds"] == row.usage["wall_time_seconds"]
        assert [f["path"] for f in got["changes"]["files"]] == ["out.txt"]
        assert "+result" in got["changes"]["diff"]

    def test_a_modified_file_diffs_from_the_hash_verified_source(
        self, sessions: Any, tmp_path: Path
    ) -> None:
        """A modified file's unified diff comes from the copy source under
        the manifest's hash proof — the real start bytes, not the current
        source (which still holds them here)."""
        store = SqlAlchemyAgentRunRepository(sessions)
        _workspace(tmp_path)
        service = _service(
            [
                _write("notes.txt", "cause is Y\n"),
                FinalCandidate(summary="fixed the note", changes=["notes.txt"]),
            ],
            store,
            tmp_path,
        )
        result = service.run(
            _contract("fix the note"), runtime_spec_for("coder"), max_turns=6, workspace="proj"
        )
        assert result.status.value == "succeeded", result
        changes = service.changes(result.run_id)
        assert changes is not None
        [change] = changes.files
        assert change.path == "notes.txt"
        assert change.status == "modified"
        assert change.sha256_before == hashlib.sha256(b"cause is X\n").hexdigest()
        assert change.sha256_after == hashlib.sha256(b"cause is Y\n").hexdigest()
        assert "-cause is X" in changes.diff
        assert "+cause is Y" in changes.diff
        assert not changes.truncated
