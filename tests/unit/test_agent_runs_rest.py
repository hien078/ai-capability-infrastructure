"""REST /v1/agent-runs surface tests (ADR-014): thin edge → AgentRunService →
kernel → RunResult. Deterministic — a scripted model gateway, no network."""

from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest import agent_runs as rest_agent_runs
from aci.adapters.inbound.rest.agent_run_wiring import build_agent_run_service
from aci.adapters.inbound.rest.errors import register_error_handlers
from aci.adapters.inbound.rest.wiring import Container, Settings
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import FinalCandidate
from aci.runtime.model_gateway import FakeModelGateway


class _FakeFactory:
    """ModelGatewayFactory returning a scripted gateway (deterministic)."""

    def __init__(self, gateway: FakeModelGateway) -> None:
        self._gateway = gateway

    def build(self) -> object:
        return self._gateway


class _NullToolFactory:
    def build(self) -> object:
        from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime

        class _NoDispatch:
            def dispatch(self, tool: object, args: object, envelope: object) -> object:
                raise DomainError(ErrorCode.TOOL_EXECUTION_FAILED, "no dispatcher")

        return ToolRuntime(ToolRegistry(), dispatcher=cast("Any", _NoDispatch()))


class _NullCapabilityFactory:
    def build(self) -> object:
        class _None:
            def handle_request(self, request: object, snapshot: object) -> list[object]:
                return []

        return _None()


class _NullContextFactory:
    def build(self) -> object:
        from aci.runtime.context_engine import ContextBudget, ContextEngine

        return ContextEngine(ContextBudget(total_tokens=60_000))


def _service(actions: list[Any]) -> AgentRunService:
    return AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _FakeFactory(FakeModelGateway(actions))),
        tool_executor_factory=cast(ModelGatewayFactory, _NullToolFactory()),
        capability_runtime_factory=cast(ModelGatewayFactory, _NullCapabilityFactory()),
        context_engine_factory=cast(ModelGatewayFactory, _NullContextFactory()),
    )


def _client(service: AgentRunService) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    container = cast(Container, type("C", (), {"agent_run_service": service})())
    app.dependency_overrides[rest_agent_runs.get_container] = lambda: container
    app.include_router(rest_agent_runs.router)
    return TestClient(app)


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "objective": "fix the failing test",
        "acceptance_criteria": ["test passes"],
    }
    body.update(overrides)
    return body


class TestAgentRunsRest:
    def test_run_verified_success(self) -> None:
        service = _service([FinalCandidate(summary="fixed", changes=["src/x.py"])])
        client = _client(service)
        response = client.post("/v1/agent-runs", json=_body())
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "succeeded"
        assert data["stop_reason"] == "SUCCESS"
        assert data["summary"] == "fixed"
        assert data["evidence_verdict"] == "PASS"
        assert data["turns"] == 1

    def test_unknown_profile_rejected(self) -> None:
        service = _service([FinalCandidate(summary="x")])
        client = _client(service)
        response = client.post("/v1/agent-runs", json=_body(requested_profile="wizard"))
        assert response.status_code == 422
        assert "unknown profile" in response.json()["error"]["message"]

    def test_unconfigured_gateway_fails_caller_visibly(self) -> None:
        service = _service([])
        client = _client(service)
        # Script exhaustion → MODEL_FAILURE classified by the kernel → the
        # run result carries the failure caller-visibly (never a fake success).
        response = client.post("/v1/agent-runs", json=_body())
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "failed"
        assert data["stop_reason"] == "MODEL_FAILURE"

    def test_cancel_unknown_run(self) -> None:
        service = _service([FinalCandidate(summary="x")])
        client = _client(service)
        response = client.post("/v1/agent-runs/ghost/cancel")
        assert response.status_code == 200
        assert response.json() == {"cancelled": False}

    def test_get_run_after_completion(self) -> None:
        service = _service([FinalCandidate(summary="done", changes=["a"])])
        client = _client(service)
        created = client.post("/v1/agent-runs", json=_body()).json()
        fetched = client.get(f"/v1/agent-runs/{created['run_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["run_id"] == created["run_id"]
        assert fetched.json()["status"] == "succeeded"

    def test_get_unknown_run_404(self) -> None:
        service = _service([FinalCandidate(summary="x")])
        client = _client(service)
        response = client.get("/v1/agent-runs/ghost")
        assert response.status_code == 404

    def test_revise_links_previous_attempt(self) -> None:
        service = _service(
            [FinalCandidate(summary="bad"), FinalCandidate(summary="fixed", changes=["a"])]
        )
        client = _client(service)
        first = client.post("/v1/agent-runs", json=_body()).json()
        revision = client.post(
            f"/v1/agent-runs/{first['run_id']}/revise",
            json={
                "objective": "fix the failing test",
                "failed_criteria": ["test passes"],
                "feedback": "the summary was empty",
            },
        )
        assert revision.status_code == 201
        assert revision.json()["summary"] == "fixed"
        # The revision carried the failed criteria into the new attempt.
        assert service.get(first["run_id"]) is not None

    def test_wiring_builds_with_unconfigured_settings(self) -> None:
        settings = Settings(agent_model_base_url="", agent_model_id="")
        service = build_agent_run_service(settings)
        assert isinstance(service, AgentRunService)

    def test_real_wiring_context_engine_has_build(self) -> None:
        """The production wiring must satisfy the kernel's context seam: the
        kernel calls ``context_engine.build(snapshot, turn=...)`` — a factory
        that returns anything else crashes every real run at turn 1."""
        from aci.runtime.context_engine import ContextEngine

        settings = Settings(agent_model_base_url="", agent_model_id="")
        service = build_agent_run_service(settings)
        context = service._context_factory.build()  # noqa: SLF001
        assert isinstance(context, ContextEngine)
        assert hasattr(context, "build")
