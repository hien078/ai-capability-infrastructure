"""AgentRunService (harness.md §0.4 surface B): POST /v1/agent-runs use case.

Capability Intelligence picks WHAT (the bundle); HarnessKernel executes HOW
one delegated objective (§0.1). This service composes the kernel per request
from an AgentProfile — the kernel itself is stateless wiring; all run state
lives in the StateManager the service owns per run.
"""

from typing import Protocol, cast

from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.subtask import RunResult, SubtaskContract
from aci.runtime.cancellation import CancelToken
from aci.runtime.run_controller import HarnessKernel, ToolExecutor


class ModelGatewayFactory(Protocol):
    """Builds the model gateway for one run (provider adapter or the honest
    null that fails caller-visibly — never a silent default)."""

    def build(self) -> object: ...


class AgentRunService:
    """One service per process; one kernel wiring per run (profiles are data)."""

    def __init__(
        self,
        *,
        model_gateway_factory: ModelGatewayFactory,
        tool_executor_factory: ModelGatewayFactory,
        capability_runtime_factory: ModelGatewayFactory,
        context_engine_factory: ModelGatewayFactory,
    ) -> None:
        self._model_factory = model_gateway_factory
        self._tools_factory = tool_executor_factory
        self._capability_factory = capability_runtime_factory
        self._context_factory = context_engine_factory
        self._cancel_tokens: dict[str, CancelToken] = {}
        self._results: dict[str, RunResult] = {}

    def run(
        self,
        contract: SubtaskContract,
        spec: RuntimeSpec,
        *,
        max_turns: int | None = None,
    ) -> RunResult:
        from aci.runtime.profiles import verifier_checks
        from aci.runtime.recovery import RecoveryManager
        from aci.runtime.state_manager import StateManager
        from aci.runtime.verification import VerificationManager

        kernel = HarnessKernel(
            state=StateManager(),
            model_gateway=self._model_factory.build(),
            tool_executor=cast(ToolExecutor, self._tools_factory.build()),
            context_engine=self._context_factory.build(),
            verifier=VerificationManager(verifier_checks(spec.profile_id)),
            recovery=RecoveryManager(),
            capability_runtime=self._capability_factory.build(),
        )
        token = CancelToken(run_id=contract.task_id)
        self._cancel_tokens[contract.task_id] = token
        try:
            if max_turns is None:
                result = kernel.run(contract, spec, cancel_token=token)
            else:
                result = kernel.run(contract, spec, cancel_token=token, max_turns=max_turns)
            self._results[contract.task_id] = result
            return result
        finally:
            self._cancel_tokens.pop(contract.task_id, None)

    def get(self, run_id: str) -> RunResult | None:
        """§29.2 GET run — the last RunResult, or None when unknown."""
        return self._results.get(run_id)

    def revise(
        self,
        run_id: str,
        contract: SubtaskContract,
        spec: RuntimeSpec,
        *,
        failed_criteria: list[str] | None = None,
        feedback: str = "",
        max_turns: int | None = None,
    ) -> RunResult:
        """§29.6/§57 revision: a new attempt LINKED to the previous run —
        the agent receives delta feedback, never a blind restart."""
        if run_id not in self._results:
            from aci.domain.capability.errors import DomainError, ErrorCode

            raise DomainError(ErrorCode.ROUTE_RUN_NOT_FOUND, f"unknown run: {run_id}")
        previous = self._results[run_id]
        revision = contract.model_copy(
            update={
                "parent_task_id": previous.run_id,
                "global_context": (
                    (contract.global_context + "\n" if contract.global_context else "")
                    + f"Previous attempt {previous.run_id} failed criteria: "
                    + ", ".join(failed_criteria or [])
                    + (f". Feedback: {feedback}" if feedback else "")
                ),
            }
        )
        return self.run(revision, spec, max_turns=max_turns)

    def cancel(self, run_id: str) -> bool:
        token = self._cancel_tokens.get(run_id)
        if token is None:
            return False
        token.cancel()
        return True
