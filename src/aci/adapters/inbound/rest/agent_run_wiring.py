"""HarnessKernel REST wiring (ADR-014): factories the AgentRunService composes
one kernel per run from, plus the workspace settings (§16) it provisions
per-run working copies with. The model gateway is a real provider client when
ACI_AGENT_MODEL_BASE_URL is set; otherwise the run fails caller-visibly —
the same honest-null rule as the A2A executor (§56.1).

Capabilities (harness.md §11): when the composition root supplies a
``capability_client_factory`` (the REST Container does — a registry-backed
ACIClient over the §14 router), every run gets a fresh CapabilityRuntime over
a fresh run-scoped client. Without one (unit tests constructing the service
directly) the honest null stays: a capability request loads nothing."""

from collections.abc import Callable
from typing import Any

from aci.application.run_agent_task import AgentRunService
from aci.config import Settings
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import ExecutionEnvelope
from aci.domain.runtime.tools import ToolSpec
from aci.runtime.capability_runtime import ACIClient, CapabilityRuntime
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.model_gateway import OpenAICompatGateway
from aci.runtime.protocols import ToolDispatchResult
from aci.runtime.sandbox import (
    ProcessSandbox,
    ResourceLimits,
    build_process_sandbox,
)
from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime


class _UnconfiguredGateway:
    def invoke(self, request: object) -> object:
        raise DomainError(
            ErrorCode.MODEL_FAILURE,
            "agent-run model gateway unconfigured — set ACI_AGENT_MODEL_BASE_URL",
        )


class _NullDispatcher:
    """The no-workspace path (request without `workspace`): a tool call fails
    closed rather than pretending to execute."""

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        raise DomainError(
            ErrorCode.TOOL_EXECUTION_FAILED,
            f"no workspace dispatcher configured for tool {tool.tool_id}",
        )


class _NullCapabilityRuntime:
    """No capability plane wired: a request honestly loads nothing."""

    #: The kernel does not offer capability_request to the model (dead action).
    advertised = False

    def handle_request(self, request: object, snapshot: object) -> list[object]:  # noqa: ARG002
        return []


def build_sandbox(settings: Settings) -> ProcessSandbox:
    """§16.5 — the process sandbox every run's commands go through, built
    from settings. Logs once at startup when processes are enabled and the
    sandbox is either explicitly off or unusable (then every command is
    refused — fail closed, never a silent unsandboxed run)."""
    import logging

    logger = logging.getLogger("aci.agent_runs")
    mb = 1024 * 1024
    sandbox = build_process_sandbox(
        settings.agent_sandbox,
        limits=ResourceLimits(
            cpu_seconds=settings.agent_sandbox_cpu_seconds,
            address_space_bytes=settings.agent_sandbox_memory_mb * mb,
            file_size_bytes=settings.agent_sandbox_file_size_mb * mb,
            max_processes=settings.agent_sandbox_max_processes,
            open_files=settings.agent_sandbox_open_files,
        ),
        extra_ro_binds=settings.agent_sandbox_ro_binds,
    )
    if not settings.agent_process_prefixes:
        return sandbox  # no process authority: nothing to probe or warn about
    if sandbox.name == "none":
        logger.warning(
            "ACI_AGENT_SANDBOX=none — agent-run commands (run_command, "
            "verification_command) execute UNSANDBOXED as the server user with "
            "full network and filesystem access; use only on a disposable host"
        )
        return sandbox
    reason = sandbox.unavailable_reason()
    if reason is not None:
        logger.warning(
            "agent-run process sandbox UNUSABLE (%s) — every run_command and "
            "verification_command will be REFUSED until bubblewrap works or "
            "ACI_AGENT_SANDBOX=none is set explicitly",
            reason,
        )
    return sandbox


def build_agent_run_service(
    settings: Settings,
    *,
    run_store: object | None = None,
    capability_client_factory: Callable[[], ACIClient] | None = None,
) -> AgentRunService:
    if not settings.agent_runs_token:
        # Honest-default log, once per process: the surface is reachable by
        # anyone with network access to the port and a run executes code
        # on this host (sandboxed per ACI_AGENT_SANDBOX, §16.5).
        import logging

        logging.getLogger("aci.agent_runs").warning(
            "ACI_AGENT_RUNS_TOKEN unset — /v1/agent-runs is UNAUTHENTICATED; "
            "keep the port on localhost or set the token before exposing it"
        )

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
            if capability_client_factory is None:
                return _NullCapabilityRuntime()
            # Per run: the runtime's refresh budget + digest cache and the
            # client's issued-selection allowlist never leak across runs.
            return CapabilityRuntime(capability_client_factory())

    class _ContextFactory:
        def build(self) -> object:
            return ContextEngine(ContextBudget(total_tokens=60_000))

    return AgentRunService(
        model_gateway_factory=_ModelFactory(),
        tool_executor_factory=_ToolFactory(),
        capability_runtime_factory=_CapabilityFactory(),
        context_engine_factory=_ContextFactory(),
        workspace_root=settings.agent_workspace_root or None,
        runs_root=settings.agent_runs_root,
        process_prefixes=settings.agent_process_prefixes,
        command_timeout_seconds=settings.agent_command_timeout_seconds,
        verification_timeout_seconds=settings.agent_verification_timeout_seconds,
        run_store=run_store,  # type: ignore[arg-type]
        process_sandbox=build_sandbox(settings),
    )
