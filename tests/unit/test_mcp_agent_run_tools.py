"""Unit acceptance for the MCP agent-run tools (ACI_MCP_AGENT_RUNS, ADR-006
§29.2): ``run_agent_task`` / ``get_agent_run`` / ``cancel_agent_run``.

Load-bearing claims:

- The tools are closures over the SAME translation the REST routes use
  (``rest/agent_runs.start_run`` → ``AgentRunService``) — one path, no
  second (weaker) validation copy: the tool fn builds ``AgentRunRequest``
  and the shared translation does the rest.
- Argument bounds mirror ``AgentRunRequest`` so the SDK rejects bad input
  as a ToolError the model can act on (§45 code in the text for DomainErrors).
- ``get_agent_run`` returns the shared read model (``agent_run_response``) —
  whatever fields the server provides flow through; unknown run → the stable
  ROUTE_RUN_NOT_FOUND code, never a guess.

Deterministic: a fake AgentRunService, no DB, no transport.
"""

import asyncio
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.server import MCPServer
from pydantic import ValidationError

from aci.adapters.inbound.mcp.tools import (
    make_cancel_agent_run_tool,
    make_get_agent_run_tool,
    make_run_agent_task_tool,
)
from aci.adapters.inbound.rest.agent_runs import AgentRunResponse
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.subtask import RunResult, RunUsage, SubtaskContract


def _result(run_id: str = "run_unit1", *, status: RunStatus = RunStatus.SUCCEEDED) -> RunResult:
    return RunResult(
        run_id=run_id,
        status=status,
        stop_reason=StopReason.SUCCESS
        if status is RunStatus.SUCCEEDED
        else StopReason.MODEL_FAILURE,
        summary="fixed the off-by-one and verified",
        usage=RunUsage(turns=3, tool_calls=5, wall_time_seconds=1.5),
    )


class FakeService:
    """Duck-typed AgentRunService: records the translation, returns canned."""

    def __init__(self) -> None:
        self.calls: list[tuple[SubtaskContract, RuntimeSpec, dict[str, Any]]] = []
        self.stored: dict[str, RunResult] = {"run_known": _result("run_known")}
        self.cancelled: list[str] = []
        self.error: DomainError | None = None

    def run(self, contract: SubtaskContract, spec: RuntimeSpec, **options: Any) -> RunResult:
        if self.error is not None:
            raise self.error
        self.calls.append((contract, spec, options))
        return _result()

    def get(self, run_id: str) -> RunResult | None:
        return self.stored.get(run_id)

    def cancel(self, run_id: str) -> bool:
        self.cancelled.append(run_id)
        return run_id in self.stored


def test_run_agent_task_translates_like_rest() -> None:
    """The tool fn → start_run → AgentRunService: same contract fields, same
    run options the REST route passes — one translation, asserted field by
    field."""
    service = FakeService()
    tool = make_run_agent_task_tool(service)
    response = tool(
        objective="fix the flaky cache test",
        global_context="repo: demo",
        constraints=["do not touch tests", "keep it minimal"],
        acceptance_criteria=["suite green"],
        max_turns=7,
        workspace="proj",
        verification_command=["python", "-m", "pytest", "-q"],
        write_scopes=["src"],
        command_prefixes=["python -m pytest"],
        approval_required_tools=["run_command"],
        preload_capabilities=True,
    )
    assert isinstance(response, AgentRunResponse)
    assert (response.run_id, response.status) == ("run_unit1", "succeeded")
    assert response.summary == "fixed the off-by-one and verified"
    assert (response.turns, response.tool_calls) == (3, 5)
    assert response.evidence_verdict is None  # no evidence pack on this fake

    assert len(service.calls) == 1
    contract, spec, options = service.calls[0]
    assert contract.objective == "fix the flaky cache test"
    assert contract.global_context == "repo: demo"
    assert contract.constraints == ["do not touch tests", "keep it minimal"]
    assert [c.description for c in contract.acceptance_criteria] == ["suite green"]
    assert contract.requested_profile == "coder"
    assert isinstance(spec, RuntimeSpec)
    assert options == {
        "max_turns": 7,
        "workspace": "proj",
        "verification_command": ["python", "-m", "pytest", "-q"],
        "write_scopes": ["src"],
        "command_prefixes": ["python -m pytest"],
        "approval_required_tools": ["run_command"],
        "preload_capabilities": True,
    }


def test_run_agent_task_defaults_match_the_rest_request() -> None:
    service = FakeService()
    response = make_run_agent_task_tool(service)(objective="do the thing")
    assert isinstance(response, AgentRunResponse)
    contract, _spec, options = service.calls[0]
    assert contract.constraints == []
    assert contract.acceptance_criteria == []
    assert options["workspace"] is None
    assert options["verification_command"] is None
    assert options["write_scopes"] is None
    assert options["max_turns"] is None


def test_run_agent_task_domain_error_carries_the_stable_code() -> None:
    service = FakeService()
    service.error = DomainError(ErrorCode.MODEL_FAILURE, "gateway unconfigured")
    with pytest.raises(ToolError) as exc:
        make_run_agent_task_tool(service)(objective="x")
    assert "MODEL_FAILURE" in str(exc.value)


def test_get_agent_run_returns_the_shared_read_model() -> None:
    tool = make_get_agent_run_tool(FakeService())
    response = tool("run_known")
    assert isinstance(response, AgentRunResponse)
    assert (response.run_id, response.status, response.summary) == (
        "run_known",
        "succeeded",
        "fixed the off-by-one and verified",
    )


def test_get_agent_run_unknown_is_the_stable_not_found_code() -> None:
    with pytest.raises(ToolError) as exc:
        make_get_agent_run_tool(FakeService())("run_nope")
    assert "ROUTE_RUN_NOT_FOUND" in str(exc.value)


def test_cancel_agent_run_reports_the_service_answer() -> None:
    service = FakeService()
    assert make_cancel_agent_run_tool(service)("run_known") == {"cancelled": True}
    assert make_cancel_agent_run_tool(service)("run_nope") == {"cancelled": False}
    assert service.cancelled == ["run_known", "run_nope"]


def test_cancel_agent_run_domain_error_carries_the_stable_code() -> None:
    class Failing(FakeService):
        def cancel(self, run_id: str) -> bool:
            raise DomainError(ErrorCode.TASK_TRANSITION_INVALID, "terminal run")

    with pytest.raises(ToolError) as exc:
        make_cancel_agent_run_tool(Failing())("run_x")
    assert "TASK_TRANSITION_INVALID" in str(exc.value)


# -- the SDK's argument validation (the wire behavior a client sees) ------------


def _server_with_agent_tools(service: FakeService) -> MCPServer[Any]:
    server: MCPServer[Any] = MCPServer(name="aci-test")
    server.add_tool(make_run_agent_task_tool(service), name="run_agent_task")
    return server


@pytest.mark.parametrize(
    "arguments",
    [
        {},  # objective missing
        {"objective": ""},  # objective empty
        {"objective": "x", "max_turns": 0},  # below the REST bound (ge=1)
        {"objective": "x", "max_turns": 201},  # above the REST bound (le=200)
        {"objective": "x", "verification_command": []},  # min_length=1
    ],
)
def test_sdk_rejects_out_of_bounds_arguments_as_tool_errors(arguments: dict[str, Any]) -> None:
    """The signature mirrors AgentRunRequest's bounds, so the SDK's argument
    validation rejects bad input as a ToolError — the model can self-correct
    (§45), the protocol layer stays clean."""
    server = _server_with_agent_tools(FakeService())
    with pytest.raises((ToolError, ValidationError)):
        asyncio.run(server.call_tool("run_agent_task", arguments))


def test_sdk_calls_the_tool_through_the_shared_translation() -> None:
    service = FakeService()
    server = _server_with_agent_tools(service)
    result = asyncio.run(
        server.call_tool(
            "run_agent_task",
            {"objective": "fix the bug", "constraints": ["stay minimal"], "max_turns": 4},
        )
    )
    payload: dict[str, Any] = result.structured_content  # type: ignore[union-attr]
    assert payload["run_id"] == "run_unit1"
    assert payload["status"] == "succeeded"
    contract, _spec, options = service.calls[0]
    assert contract.objective == "fix the bug"
    assert options["max_turns"] == 4
