"""HarnessKernel REST wiring (ADR-014): factories the AgentRunService composes
one kernel per run from. The model gateway is a real provider client when
ACI_AGENT_MODEL_BASE_URL is set; otherwise the run fails caller-visibly —
the same honest-null rule as the A2A executor (§56.1)."""

from typing import Any

from aci.application.run_agent_task import AgentRunService
from aci.config import Settings
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import ExecutionEnvelope
from aci.domain.runtime.tools import ToolSpec
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.model_gateway import OpenAICompatGateway
from aci.runtime.protocols import ToolDispatchResult
from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime


class _UnconfiguredGateway:
    def invoke(self, request: object) -> object:
        raise DomainError(
            ErrorCode.MODEL_FAILURE,
            "agent-run model gateway unconfigured — set ACI_AGENT_MODEL_BASE_URL",
        )


class _NullDispatcher:
    """The REST agent-run floor has no workspace yet (H4 is a later phase);
    a tool call fails closed rather than pretending to execute."""

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        raise DomainError(
            ErrorCode.TOOL_EXECUTION_FAILED,
            f"no workspace dispatcher configured for tool {tool.tool_id}",
        )


class _NullCapabilityRuntime:
    def handle_request(self, request: object, snapshot: object) -> list[object]:  # noqa: ARG002
        return []


def build_agent_run_service(settings: Settings) -> AgentRunService:
    class _ModelFactory:
        def build(self) -> object:
            if settings.agent_model_base_url:
                return OpenAICompatGateway(
                    base_url=settings.agent_model_base_url,
                    api_key=settings.agent_model_api_key,
                    model_id=settings.agent_model_id,
                    request_timeout_seconds=settings.agent_model_timeout_seconds,
                )
            return _UnconfiguredGateway()

    class _ToolFactory:
        def build(self) -> object:
            return ToolRuntime(ToolRegistry(), dispatcher=_NullDispatcher())

    class _CapabilityFactory:
        def build(self) -> object:
            return _NullCapabilityRuntime()

    class _ContextFactory:
        def build(self) -> object:
            return ContextEngine(ContextBudget(total_tokens=60_000))

    return AgentRunService(
        model_gateway_factory=_ModelFactory(),
        tool_executor_factory=_ToolFactory(),
        capability_runtime_factory=_CapabilityFactory(),
        context_engine_factory=_ContextFactory(),
    )
