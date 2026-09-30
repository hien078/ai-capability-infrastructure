"""AgentRunService (harness.md §0.4 surface B, §29A): POST /v1/agent-runs use case.

Capability Intelligence picks WHAT (the bundle); HarnessKernel executes HOW
one delegated objective (§0.1). The service composes one kernel per run from
an AgentProfile and, when the client names a workspace (§16), provisions a
per-run working copy whose grants never exceed the server ceiling (INV-02).
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import (
    ExecutionEnvelope,
    FilesystemScope,
    GrantEnvelope,
    ProcessScope,
    prefix_within_prefixes,
)
from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.subtask import RunResult, SubtaskContract
from aci.runtime.cancellation import CancelToken
from aci.runtime.run_controller import (
    CapabilityHandler,
    ContextAssembler,
    HarnessKernel,
    ModelGateway,
    ToolExecutor,
)
from aci.runtime.verification import VerifierCallable
from aci.runtime.workspace import command_within_prefixes


def new_run_id() -> str:
    return f"run_{uuid4().hex[:12]}"


class ModelGatewayFactory(Protocol):
    """Builds one kernel collaborator per run (provider adapter or the honest
    null that fails caller-visibly — never a silent default)."""

    def build(self) -> object: ...


@dataclass(frozen=True)
class RunOptions:
    """Client-chosen per-run options: workspace (§16), process scope (§13.4),
    client verifier (§19). Stored so a revision reuses them (§29A)."""

    workspace: str | None = None
    verification_command: tuple[str, ...] | None = None
    write_scopes: tuple[str, ...] | None = None
    command_prefixes: tuple[str, ...] | None = None
    max_turns: int | None = None


@dataclass(frozen=True)
class _RunRecord:
    contract: SubtaskContract
    spec: RuntimeSpec
    options: RunOptions
    run_dir: Path | None


@dataclass(frozen=True)
class _WorkspaceBinding:
    workspace_id: str
    run_dir: Path
    tool_executor: ToolExecutor
    grants: GrantEnvelope
    verifier_check: VerifierCallable | None


def _validate_workspace_name(name: str) -> None:
    """A workspace is named by one path segment under the workspace root."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise DomainError(
            ErrorCode.CLIENT_INCOMPATIBLE,
            f"workspace must be a single directory name, got {name!r}",
        )


def _workspace_scope(scope: str) -> str:
    """Workspace-relative scope ('.' = whole workspace); never absolute or escaping."""
    norm = os.path.normpath(scope.strip()) if scope.strip() else ""
    if not norm or os.path.isabs(norm) or norm == ".." or norm.startswith(".." + os.sep):
        raise DomainError(
            ErrorCode.CLIENT_INCOMPATIBLE,
            f"write_scopes entries must be workspace-relative paths, got {scope!r}",
        )
    return norm


class AgentRunService:
    """One service per process; one kernel wiring per run (profiles are data)."""

    def __init__(
        self,
        *,
        model_gateway_factory: ModelGatewayFactory,
        tool_executor_factory: ModelGatewayFactory,
        capability_runtime_factory: ModelGatewayFactory,
        context_engine_factory: ModelGatewayFactory,
        workspace_root: str | Path | None = None,
        runs_root: str | Path = "data/agent-runs",
        process_prefixes: Sequence[str] = (),
        command_timeout_seconds: float = 120.0,
        verification_timeout_seconds: float = 300.0,
    ) -> None:
        self._model_factory = model_gateway_factory
        self._tools_factory = tool_executor_factory
        self._capability_factory = capability_runtime_factory
        self._context_factory = context_engine_factory
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self._runs_root = Path(runs_root)
        self._process_prefixes = [p for p in process_prefixes if p.strip()]
        self._command_timeout_ms = int(command_timeout_seconds * 1000)
        self._verification_timeout_ms = int(verification_timeout_seconds * 1000)
        self._cancel_tokens: dict[str, CancelToken] = {}
        self._results: dict[str, RunResult] = {}
        self._records: dict[str, _RunRecord] = {}

    def run(
        self,
        contract: SubtaskContract,
        spec: RuntimeSpec,
        *,
        max_turns: int | None = None,
        workspace: str | None = None,
        verification_command: list[str] | None = None,
        write_scopes: list[str] | None = None,
        command_prefixes: list[str] | None = None,
    ) -> RunResult:
        options = RunOptions(
            workspace=workspace,
            verification_command=(
                tuple(verification_command) if verification_command is not None else None
            ),
            write_scopes=tuple(write_scopes) if write_scopes is not None else None,
            command_prefixes=tuple(command_prefixes) if command_prefixes is not None else None,
            max_turns=max_turns,
        )
        source = self._source_for(workspace) if workspace is not None else None
        return self._start(contract, spec, options, source)

    def get(self, run_id: str) -> RunResult | None:
        """§29 GET run — the last RunResult, or None when unknown."""
        return self._results.get(run_id)

    def contract(self, run_id: str) -> SubtaskContract | None:
        """The contract a known run executed (a revision's parent link lives here)."""
        record = self._records.get(run_id)
        return record.contract if record is not None else None

    def revise(
        self,
        run_id: str,
        *,
        objective: str | None = None,
        failed_criteria: list[str] | None = None,
        feedback: str = "",
        max_turns: int | None = None,
    ) -> RunResult:
        """§29A/§57 delta revision: a new attempt built FROM the previous one —
        same criteria, constraints, budget, profile and options; the previous
        working directory is its workspace source, so the agent continues
        from its own changes. Never a blind restart."""
        record = self._records.get(run_id)
        previous = self._results.get(run_id)
        if record is None or previous is None:
            raise DomainError(ErrorCode.ROUTE_RUN_NOT_FOUND, f"unknown run: {run_id}")
        note = f"Previous attempt {run_id} failed criteria: " + (
            ", ".join(failed_criteria or []) or "none listed"
        )
        if previous.summary:
            note += f"; Previous result: {previous.summary}"
        if feedback:
            note += f"; Feedback: {feedback}"
        base = record.contract
        contract = base.model_copy(
            update={
                "task_id": new_run_id(),
                "parent_task_id": run_id,
                "objective": objective or base.objective,
                "global_context": (base.global_context + "\n" if base.global_context else "")
                + note,
                "created_at": datetime.now(UTC),
            }
        )
        options = (
            record.options if max_turns is None else replace(record.options, max_turns=max_turns)
        )
        spec = record.spec.model_copy(update={"created_at": datetime.now(UTC)})
        return self._start(contract, spec, options, record.run_dir)

    def cancel(self, run_id: str) -> bool:
        token = self._cancel_tokens.get(run_id)
        if token is None:
            return False
        token.cancel()
        return True

    # -- per-run wiring -------------------------------------------------------

    def _source_for(self, name: str) -> Path:
        if self._workspace_root is None:
            raise DomainError(
                ErrorCode.CLIENT_INCOMPATIBLE,
                "this server exposes no workspaces (ACI_AGENT_WORKSPACE_ROOT is unset)",
            )
        _validate_workspace_name(name)
        return self._workspace_root / name

    def _effective_prefixes(self, requested: Sequence[str] | None) -> list[str]:
        """INV-02: the run's process scope is the server ceiling, or a client
        request every entry of which the ceiling already allows."""
        ceiling = list(self._process_prefixes)
        if requested is None:
            return ceiling
        outside = [p for p in requested if not prefix_within_prefixes(p, ceiling)]
        if outside:
            raise DomainError(
                ErrorCode.PERMISSION_DENIED,
                f"command_prefixes outside the server ceiling: {outside[:5]!r}",
            )
        return list(requested)

    def _bind_workspace(self, run_id: str, options: RunOptions, source: Path) -> _WorkspaceBinding:
        """§16: provision <runs_root>/<run_id> from `source`, bind the run's
        grants to the workspace (defense in depth, INV-04) and build the
        tool runtime + optional client verifier over it."""
        from aci.runtime.workspace import WorkspaceManager
        from aci.runtime.workspace_tools import (
            build_workspace_tool_runtime,
            provision_workspace,
            verification_command_check,
        )

        prefixes = self._effective_prefixes(options.command_prefixes)
        verification = options.verification_command
        if verification is not None and not command_within_prefixes(verification, prefixes):
            raise DomainError(
                ErrorCode.PERMISSION_DENIED,
                f"verification_command {list(verification)[:3]!r} is outside the run's "
                "process prefixes",
            )
        write = (
            [_workspace_scope(s) for s in options.write_scopes]
            if options.write_scopes is not None
            else ["."]
        )
        grants = GrantEnvelope(
            filesystem=FilesystemScope(read=["."], write=write),
            process=ProcessScope(allowed_prefixes=prefixes),
        )
        self._runs_root.mkdir(parents=True, exist_ok=True)
        run_dir = provision_workspace(source, self._runs_root, run_id)
        manager = WorkspaceManager()
        # The manager mints the workspace id on create; the bound envelope is
        # keyed by root, so the run id names it here.
        envelope = ExecutionEnvelope(
            run_id=run_id,
            workspace_id=run_id,
            filesystem=grants.filesystem,
            network=grants.network,
            process=grants.process,
        )
        workspace_id = manager.create_local(run_dir, envelope)
        tool_executor = build_workspace_tool_runtime(
            manager,
            workspace_id,
            allow_commands=bool(prefixes),
            command_timeout_ms=self._command_timeout_ms,
        )
        check: VerifierCallable | None = None
        if verification is not None:
            check = verification_command_check(
                manager,
                workspace_id,
                list(verification),
                allowed_prefixes=prefixes,
                timeout_ms=self._verification_timeout_ms,
            )
        return _WorkspaceBinding(
            workspace_id=workspace_id,
            run_dir=run_dir,
            tool_executor=cast(ToolExecutor, tool_executor),
            grants=grants,
            verifier_check=check,
        )

    def _start(
        self,
        contract: SubtaskContract,
        spec: RuntimeSpec,
        options: RunOptions,
        source: Path | None,
    ) -> RunResult:
        from aci.runtime.profiles import verifier_checks
        from aci.runtime.recovery import RecoveryManager
        from aci.runtime.state_manager import StateManager
        from aci.runtime.verification import VerificationManager

        if source is None and options.verification_command is not None:
            raise DomainError(
                ErrorCode.CLIENT_INCOMPATIBLE, "verification_command requires a workspace"
            )
        checks = verifier_checks(spec.profile_id)
        binding = (
            self._bind_workspace(contract.task_id, options, source) if source is not None else None
        )
        if binding is None:
            tool_executor = cast(ToolExecutor, self._tools_factory.build())
            run_spec = spec
        else:
            tool_executor = binding.tool_executor
            run_spec = spec.model_copy(update={"initial_grants": binding.grants})
            if binding.verifier_check is not None:
                checks.append(binding.verifier_check)
        kernel = HarnessKernel(
            state=StateManager(),
            model_gateway=cast(ModelGateway, self._model_factory.build()),
            tool_executor=tool_executor,
            context_engine=cast(ContextAssembler, self._context_factory.build()),
            verifier=VerificationManager(checks),
            recovery=RecoveryManager(),
            capability_runtime=cast(CapabilityHandler, self._capability_factory.build()),
        )
        token = CancelToken(run_id=contract.task_id)
        self._cancel_tokens[contract.task_id] = token
        self._records[contract.task_id] = _RunRecord(
            contract=contract,
            spec=spec,
            options=options,
            run_dir=binding.run_dir if binding is not None else None,
        )
        try:
            if options.max_turns is None:
                result = kernel.run(
                    contract,
                    run_spec,
                    cancel_token=token,
                    workspace_id=binding.workspace_id if binding is not None else None,
                )
            else:
                result = kernel.run(
                    contract,
                    run_spec,
                    cancel_token=token,
                    max_turns=options.max_turns,
                    workspace_id=binding.workspace_id if binding is not None else None,
                )
            self._results[contract.task_id] = result
            return result
        finally:
            self._cancel_tokens.pop(contract.task_id, None)
