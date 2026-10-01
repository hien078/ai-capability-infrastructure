"""AgentRunService (harness.md §0.4 surface B, §29A): POST /v1/agent-runs use case.

Capability Intelligence picks WHAT (the bundle); HarnessKernel executes HOW
one delegated objective (§0.1). The service composes one kernel per run from
an AgentProfile and, when the client names a workspace (§16), provisions a
per-run working copy whose grants never exceed the server ceiling (INV-02).
"""

import logging
import os
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from pydantic import ValidationError

from aci.application.protocols import AgentRunStore
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import (
    ApprovalDecision,
    ExecutionEnvelope,
    FilesystemScope,
    GrantEnvelope,
    ProcessScope,
    prefix_within_prefixes,
)
from aci.domain.runtime.persistence import AgentRunCheckpointRecord, AgentRunRecord
from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.stop_reason import RunStatus, is_terminal
from aci.domain.runtime.subtask import RunResult, SubtaskContract
from aci.runtime.cancellation import CancelToken
from aci.runtime.checkpoints import Checkpoint, CheckpointError, validate_for_resume
from aci.runtime.event_bus import EventBus
from aci.runtime.run_controller import (
    CANCELLED_WHILE_PAUSED,
    SUPERSEDED_BY_REVISION,
    CapabilityHandler,
    ContextAssembler,
    HarnessKernel,
    ModelGateway,
    ToolExecutor,
    cancel_paused,
    check_resume_input,
)
from aci.runtime.sandbox import (
    ProcessSandbox,
    build_platform_default_sandbox,
    sandbox_refusal,
)
from aci.runtime.verification import VerifierCallable
from aci.runtime.workspace import command_within_prefixes

log = logging.getLogger(__name__)

#: Resumable pause statuses (§7.5): the run row is written at the pause and
#: a checkpoint row (migration 0019) holds what a resume continues from.
_PAUSED = frozenset({RunStatus.INTERRUPTED, RunStatus.INTERRUPTED_APPROVAL})


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
    #: §13.6 client-added approval requirements (tool ids). Narrowing only:
    #: unioned with the server's own list, never able to remove from it.
    approval_required_tools: tuple[str, ...] | None = None
    #: Per-run override of the service's skill-preload default (None = the
    #: server setting ``ACI_AGENT_CAPABILITY_PRELOAD``). Grants nothing: it
    #: only decides whether routed skill TEXT is loaded before turn 1.
    preload_capabilities: bool | None = None

    def to_json(self) -> dict[str, object]:
        """The persisted form (migration 0018) — client REQUESTS, never grants."""
        return {
            "workspace": self.workspace,
            "verification_command": _list_or_none(self.verification_command),
            "write_scopes": _list_or_none(self.write_scopes),
            "command_prefixes": _list_or_none(self.command_prefixes),
            "max_turns": self.max_turns,
            "approval_required_tools": _list_or_none(self.approval_required_tools),
            "preload_capabilities": self.preload_capabilities,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "RunOptions":
        max_turns = data.get("max_turns")
        workspace = data.get("workspace")
        preload = data.get("preload_capabilities")  # absent on pre-preload rows
        if max_turns is not None and not isinstance(max_turns, int):
            raise ValueError("max_turns must be an int")
        if preload is not None and not isinstance(preload, bool):
            raise ValueError("preload_capabilities must be a bool")
        if workspace is not None and not isinstance(workspace, str):
            raise ValueError("workspace must be a string")
        return cls(
            workspace=workspace,
            verification_command=_str_tuple(data.get("verification_command")),
            write_scopes=_str_tuple(data.get("write_scopes")),
            command_prefixes=_str_tuple(data.get("command_prefixes")),
            max_turns=max_turns,
            approval_required_tools=_str_tuple(data.get("approval_required_tools")),
            preload_capabilities=preload,
        )


def _list_or_none(values: tuple[str, ...] | None) -> list[str] | None:
    return list(values) if values is not None else None


def _str_tuple(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("expected a list of strings")
    return tuple(value)


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


def _result_of(record: AgentRunRecord) -> RunResult:
    """The RunResult a persisted row projects (§29 GET read-through). The row's
    server-side fields (run_dir, options, contract) are never part of it."""
    return RunResult.model_validate(
        {
            "run_id": record.run_id,
            "status": record.status,
            "stop_reason": record.stop_reason,
            "detail_code": record.detail_code,
            "summary": record.summary,
            "artifacts": record.artifacts,
            "evidence": record.evidence,
            "usage": record.usage,
            "trace_ref": record.trace_ref,
            "spec": record.spec or None,
        }
    )


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
        event_bus: EventBus | None = None,
        run_store: AgentRunStore | None = None,
        process_sandbox: ProcessSandbox | None = None,
        approval_required_tools: Iterable[str] = (),
        preload_capabilities: bool = False,
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
        self._event_bus = event_bus if event_bus is not None else EventBus()
        self._run_store = run_store
        # §16.5: every workspace process (run_command + verification) runs in
        # this sandbox. Omitted = the SAFE default: the OS sandbox for this
        # platform (bwrap on Linux, Seatbelt on macOS), probed lazily on the
        # first command (runs that never execute never probe), and FAIL CLOSED
        # (commands refused) where it is unusable. Opt-out is explicit only:
        # pass NoSandbox().
        self._process_sandbox: ProcessSandbox = (
            process_sandbox if process_sandbox is not None else build_platform_default_sandbox()
        )
        self._cancel_tokens: dict[str, CancelToken] = {}
        self._results: dict[str, RunResult] = {}
        self._records: dict[str, _RunRecord] = {}
        #: §13.6 server approval floor: tool ids whose calls ALWAYS pause the
        #: run for a client decision. A request can add to it, never remove.
        self._approval_floor: frozenset[str] = frozenset()
        self.require_approval_for(approval_required_tools)
        #: Latest pause checkpoint per run (RAM — the honest-null; durable in
        #: the store when one is wired, migration 0019), resume claims, and
        #: which checkpoints the store holds (claimed there, atomically).
        self._checkpoints: dict[str, Checkpoint] = {}
        self._consumed: set[str] = set()
        self._durable_checkpoints: set[str] = set()
        self._claim_lock = threading.Lock()
        #: Default for the kernel's run-start skill preload (a request's
        #: ``preload_capabilities`` overrides it per run). Only a FRESH run
        #: (first run or revision) preloads; a resume never does.
        self._preload_capabilities = preload_capabilities

    def require_approval_for(self, tool_ids: Iterable[str]) -> None:
        """Add server-side approval requirements (``ACI_AGENT_APPROVAL_
        REQUIRED_TOOLS``). Monotonic: there is no way to remove one."""
        self._approval_floor = self._approval_floor | {t.strip() for t in tool_ids if t.strip()}

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
        approval_required_tools: list[str] | None = None,
        preload_capabilities: bool | None = None,
    ) -> RunResult:
        options = RunOptions(
            workspace=workspace,
            verification_command=(
                tuple(verification_command) if verification_command is not None else None
            ),
            write_scopes=tuple(write_scopes) if write_scopes is not None else None,
            command_prefixes=tuple(command_prefixes) if command_prefixes is not None else None,
            max_turns=max_turns,
            approval_required_tools=(
                tuple(approval_required_tools) if approval_required_tools is not None else None
            ),
            preload_capabilities=preload_capabilities,
        )
        source = self._source_for(workspace) if workspace is not None else None
        return self._start(contract, spec, options, source)

    def get(self, run_id: str) -> RunResult | None:
        """§29 GET run — the last RunResult, or None when unknown.

        RAM first (the live process), then the durable store (read-through:
        a run from a PREVIOUS process is still readable after a restart —
        the row is a projection of the frozen terminal state, INV-01)."""
        result = self._results.get(run_id)
        if result is not None:
            return result
        if self._run_store is None:
            return None
        record = self._run_store.get_run(run_id)
        if record is None:
            return None
        result = _result_of(record)
        if result.status is RunStatus.INTERRUPTED_APPROVAL:
            # The approval id lives on the pause checkpoint row (0019).
            try:
                checkpoint = self._run_store.latest_checkpoint(run_id)
            except Exception:  # noqa: BLE001 — a read-through extra, never a failure
                log.warning("agent run %s: checkpoint lookup failed", run_id, exc_info=True)
                checkpoint = None
            if checkpoint is not None and checkpoint.consumed_at is None:
                result = result.model_copy(update={"approval_id": checkpoint.approval_id})
        return result

    def contract(self, run_id: str) -> SubtaskContract | None:
        """The contract a known run executed (a revision's parent link lives
        here) — RAM first, then the durable store (migration 0018)."""
        record = self._records.get(run_id)
        if record is not None:
            return record.contract
        if self._run_store is None:
            return None
        stored = self._run_store.get_run(run_id)
        if stored is None or stored.contract is None:
            return None
        try:
            return SubtaskContract.model_validate(stored.contract)
        except ValidationError:
            return None

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
        from its own changes. Never a blind restart.

        The previous run may come from THIS process (RAM) or from a previous
        one (the durable store, migration 0018 — the restart case). Either
        way every grant is re-derived from the CURRENT server ceiling
        (INV-02); the stored options are requests, never authority.

        A PAUSED previous run (interrupted_approval / interrupted) is never
        left resumable next to its revision (one lineage, not two): after
        the revision's own refusals (missing working copy, ceiling, sandbox
        — they never burn the pause) and BEFORE its working copy is made,
        the revision claims the parent's pause checkpoint with the same
        atomic claim a resume/cancel takes, and the parent moves to
        CANCELLED (detail_code SUPERSEDED_BY_REVISION, row + RUN_CANCELLED
        persisted). A later resume of the parent is CHECKPOINT_CONSUMED.
        If that claim is lost (a resume or cancel took the pause first),
        the revision proceeds only when the parent is TERMINAL by now — it
        is then revised from that terminal result; otherwise (the parent is
        live again, e.g. a resumed segment still executing or paused anew)
        it is refused with CHECKPOINT_CONSUMED (409) and nothing is created.
        A paused parent whose checkpoint is missing or unusable cannot be
        resumed by anyone, so it is revised as before (nothing to claim).
        Revision of a terminal run is unchanged."""
        record, previous = self._revision_base(run_id)
        supersede = self._pause_superseder(run_id) if previous.status in _PAUSED else None
        try:
            return self._revise_from(
                run_id,
                record,
                previous,
                objective=objective,
                failed_criteria=failed_criteria,
                feedback=feedback,
                max_turns=max_turns,
                before_start=supersede,
            )
        except _PauseLost:
            pass
        terminal = self._terminal_base(run_id)
        if terminal is None:
            raise DomainError(
                ErrorCode.CHECKPOINT_CONSUMED,
                f"run {run_id}: its pause was resumed or cancelled concurrently and the run "
                "is not terminal yet — revise it once it has finished",
            )
        record, previous = terminal
        return self._revise_from(
            run_id,
            record,
            previous,
            objective=objective,
            failed_criteria=failed_criteria,
            feedback=feedback,
            max_turns=max_turns,
            before_start=None,
        )

    def _revise_from(
        self,
        run_id: str,
        record: _RunRecord,
        previous: RunResult,
        *,
        objective: str | None,
        failed_criteria: list[str] | None,
        feedback: str,
        max_turns: int | None,
        before_start: Callable[[], None] | None,
    ) -> RunResult:
        source = self._stored_run_dir(run_id, record, action="revised")
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
        return self._start(contract, spec, options, source, before_start=before_start)

    def cancel(self, run_id: str) -> bool:
        """POST cancel. True when the run was cancelled (or signalled), else
        False — never an error.

        - A LIVE segment of this process (a first run or a resume still
          executing): its cancel token is signalled → True; the kernel
          stops it CANCELLED between steps (unchanged behavior).
        - A PAUSED run (interrupted_approval / interrupted — in RAM, or
          known only to the store after a restart): its pause checkpoint is
          CLAIMED atomically (the same one-shot claim a resume takes, the
          store's compare-and-set when durable), then the run moves to
          CANCELLED with stop_reason CANCELLED (what a cancelled live run
          gets) and detail_code CANCELLED_WHILE_PAUSED, the row is
          re-persisted with finished_at and RUN_CANCELLED is appended → True.
          A later resume is CHECKPOINT_CONSUMED. If a resume (or another
          cancel/revision) claimed the pause first it wins → False.
        - A terminal run (this process or the store), an unknown id, or a
          paused run whose checkpoint is unusable → False."""
        token = self._cancel_tokens.get(run_id)
        if token is not None:
            token.cancel()
            return True
        if not self._is_paused(run_id):
            return False
        try:
            record, checkpoint = self._pause_base(run_id)
        except DomainError:
            return False
        # Refusals above are reads only; the claim is the one write that
        # decides between this cancel and a racing resume/revision.
        try:
            self._claim(checkpoint)
        except DomainError:
            return False
        self._finish_paused(record, checkpoint, detail_code=CANCELLED_WHILE_PAUSED)
        return True

    def resume(
        self,
        run_id: str,
        *,
        approval_id: str | None = None,
        approve: bool | None = None,
        answer: str | None = None,
    ) -> RunResult:
        """§13.6/§17.4 — continue a PAUSED run (same run id, same state, the
        REMAINING budget) from its checkpoint, at most once.

        ``approval_id`` + ``approve`` answer an INTERRUPTED_APPROVAL pause
        (approved → exactly the pending calls run once; denied → the model
        sees a denial); ``answer`` answers a clarification. The run may be
        live in RAM or known only to the store (the restart case). The run
        continues in its OWN working copy (re-bound, never re-copied); grants
        are recomputed from the CURRENT ceiling and can only narrow the
        checkpointed ones (INV-02). Refusals happen before anything runs:
        unknown run → ROUTE_RUN_NOT_FOUND; not paused → RUN_NOT_RESUMABLE;
        already resumed → CHECKPOINT_CONSUMED; approval id mismatch →
        APPROVAL_REPLAY_INVALID; vanished working copy → WORKSPACE_NOT_FOUND."""
        record, checkpoint = self._resume_base(run_id)
        approval: ApprovalDecision | None = None
        if approval_id is not None or approve is not None:
            if approval_id is None or approve is None:
                raise DomainError(
                    ErrorCode.CLIENT_INCOMPATIBLE,
                    "an approval decision needs both approval_id and approve",
                )
            approval = ApprovalDecision(
                approval_id=approval_id,
                approved=approve,
                decided_by="agent-runs-client",
                decided_at=datetime.now(UTC),
            )
        if checkpoint.pending is None:  # validate_for_resume guarantees it
            raise DomainError(ErrorCode.RUN_NOT_RESUMABLE, "checkpoint has no pending state")
        check_resume_input(checkpoint, checkpoint.pending, approval, answer)
        if checkpoint.checkpoint_id in self._consumed:
            raise _consumed_error(run_id)
        run_dir = self._stored_run_dir(run_id, record, action="resumed")
        checks = self._verifier_checks(record.spec, record.options)
        # Re-bind BEFORE claiming: a refusal here (sandbox, ceiling) must not
        # burn the run's one resume.
        binding = (
            self._bind_workspace(run_id, record.options, run_dir, existing_dir=run_dir)
            if run_dir is not None
            else None
        )
        kernel, run_spec = self._build_kernel(record.spec, record.options, binding, checks)
        self._claim(checkpoint)
        grants = binding.grants if binding is not None else run_spec.initial_grants
        self._records[run_id] = record

        def _go(token: CancelToken) -> RunResult:
            return kernel.resume(
                checkpoint,
                approval=approval,
                answer=answer,
                cancel_token=token,
                grants=grants,
                workspace_id=binding.workspace_id if binding is not None else None,
            )

        return self._drive(run_id, kernel, record, _go, resumed=True)

    # -- resume of a paused run (migration 0019) --------------------------------

    def _resume_base(self, run_id: str) -> tuple[_RunRecord, Checkpoint]:
        """The paused run's record + its latest pause checkpoint: RAM first,
        else the store. Raises the 404/409 refusals of ``resume``."""
        record = self._records.get(run_id)
        checkpoint = self._checkpoints.get(run_id)
        if record is None or checkpoint is None:
            stored = self._run_store.get_run(run_id) if self._run_store is not None else None
            if record is None:
                if stored is None:
                    raise DomainError(ErrorCode.ROUTE_RUN_NOT_FOUND, f"unknown run: {run_id}")
                record = self._record_from_store(stored)
            if checkpoint is None and self._run_store is not None:
                checkpoint = self._stored_checkpoint(run_id)
        if checkpoint is None:
            raise DomainError(
                ErrorCode.RUN_NOT_RESUMABLE,
                f"run {run_id} is not paused at a resumable checkpoint",
            )
        try:
            return record, validate_for_resume(checkpoint)
        except CheckpointError as exc:
            raise DomainError(
                ErrorCode.RUN_NOT_RESUMABLE, f"run {run_id} has an unusable checkpoint"
            ) from exc

    def _stored_checkpoint(self, run_id: str) -> Checkpoint | None:
        if self._run_store is None:
            return None
        stored = self._run_store.latest_checkpoint(run_id)
        if stored is None:
            return None
        if stored.consumed_at is not None:
            raise _consumed_error(run_id)
        try:
            checkpoint = Checkpoint.model_validate(stored.payload)
        except ValidationError as exc:
            raise DomainError(
                ErrorCode.RUN_NOT_RESUMABLE, f"run {run_id} has an unreadable checkpoint"
            ) from exc
        self._durable_checkpoints.add(checkpoint.checkpoint_id)
        return checkpoint

    def _claim(self, checkpoint: Checkpoint) -> None:
        """At most one resume per checkpoint: this process's claim under a
        lock, plus the store's atomic compare-and-set when the checkpoint is
        durable (another process — or a racing request — loses)."""
        with self._claim_lock:
            if checkpoint.checkpoint_id in self._consumed:
                raise _consumed_error(checkpoint.run_id)
            durable = checkpoint.checkpoint_id in self._durable_checkpoints
            if self._run_store is not None and durable:
                at = datetime.now(UTC)
                if not self._run_store.consume_checkpoint(checkpoint.checkpoint_id, at=at):
                    raise _consumed_error(checkpoint.run_id)
            self._consumed.add(checkpoint.checkpoint_id)

    # -- cancel / revision of a PAUSED run --------------------------------------

    def _is_paused(self, run_id: str) -> bool:
        """The run's CURRENT status (this process's result, else the stored
        row) is a resumable pause. A live segment is handled by its token
        before this is asked."""
        result = self._results.get(run_id)
        if result is not None:
            return result.status in _PAUSED
        if self._run_store is None:
            return False
        stored = self._run_store.get_run(run_id)
        return stored is not None and stored.status in {s.value for s in _PAUSED}

    def _pause_base(self, run_id: str) -> tuple[_RunRecord, Checkpoint]:
        """The paused run's record + its UNCLAIMED, paused pause checkpoint —
        reads only, so a refusal never burns the pause. Raises the resume
        refusals (ROUTE_RUN_NOT_FOUND / RUN_NOT_RESUMABLE / CHECKPOINT_
        CONSUMED); the claim itself is the caller's."""
        record, checkpoint = self._resume_base(run_id)
        if checkpoint.checkpoint_id in self._consumed:
            raise _consumed_error(run_id)
        if checkpoint.snapshot.run.status not in _PAUSED:
            raise DomainError(
                ErrorCode.RUN_NOT_RESUMABLE, f"run {run_id} is not paused at its checkpoint"
            )
        return record, checkpoint

    def _finish_paused(
        self, record: _RunRecord, checkpoint: Checkpoint, *, detail_code: str
    ) -> RunResult:
        """A CLAIMED pause → CANCELLED (StateManager-made, INV-01), RAM
        result, the row re-persisted (finished_at) + RUN_CANCELLED appended
        after the stored events."""
        from aci.runtime.state_manager import StateManager

        run_id = checkpoint.run_id
        try:
            result = cancel_paused(
                checkpoint,
                state=StateManager(),
                event_bus=self._event_bus,
                detail_code=detail_code,
            )
            self._results[run_id] = result
            self._records[run_id] = record
            self._persist(record, result, resumed=True)
        finally:
            self._event_bus.discard(run_id)
        return result

    def _pause_superseder(self, run_id: str) -> Callable[[], None] | None:
        """For revise of a PAUSED run: the step (run right before the
        revision's working copy is made) that claims the parent's pause and
        cancels the parent — raising _PauseLost when the pause is already
        claimed. None when the checkpoint is missing/unusable: nobody can
        resume the parent, so there is nothing to claim."""
        try:
            record, checkpoint = self._pause_base(run_id)
        except DomainError as exc:
            if exc.code is ErrorCode.CHECKPOINT_CONSUMED:

                def _lost() -> None:
                    raise _PauseLost(run_id)

                return _lost
            log.warning("agent run %s: paused without a usable checkpoint (%s)", run_id, exc.code)
            return None

        def _supersede() -> None:
            try:
                self._claim(checkpoint)
            except DomainError as exc:
                raise _PauseLost(run_id) from exc
            self._finish_paused(record, checkpoint, detail_code=SUPERSEDED_BY_REVISION)

        return _supersede

    def _terminal_base(self, run_id: str) -> tuple[_RunRecord, RunResult] | None:
        """The run's record + result when it is TERMINAL now (this process's
        result, else the stored row — another process may have finished
        it), else None (live, paused, unknown)."""
        if run_id in self._cancel_tokens:
            return None
        record = self._records.get(run_id)
        result = self._results.get(run_id)
        if record is not None and result is not None and is_terminal(result.status):
            return record, result
        if self._run_store is None:
            return None
        stored = self._run_store.get_run(run_id)
        if stored is None:
            return None
        try:
            stored_result = _result_of(stored)
        except ValidationError:
            return None
        if not is_terminal(stored_result.status):
            return None
        return self._record_from_store(stored), stored_result

    # -- revision of a recovered run -------------------------------------------

    def _revision_base(self, run_id: str) -> tuple[_RunRecord, RunResult]:
        """The previous attempt's record + terminal result: RAM, else the store."""
        record = self._records.get(run_id)
        previous = self._results.get(run_id)
        if record is not None and previous is not None:
            return record, previous
        stored = self._run_store.get_run(run_id) if self._run_store is not None else None
        if stored is None:
            raise DomainError(ErrorCode.ROUTE_RUN_NOT_FOUND, f"unknown run: {run_id}")
        return self._record_from_store(stored), _result_of(stored)

    def _record_from_store(self, stored: AgentRunRecord) -> _RunRecord:
        """Rebuild contract/spec/options from a persisted row. Authority fields
        of the spec (initial grants, delegation) come from the CURRENT profile,
        never from the row (INV-02); workspace grants are recomputed by
        _bind_workspace from the current ceiling anyway."""
        from aci.runtime.profiles import runtime_spec_for

        if stored.contract is None or stored.run_options is None:
            raise DomainError(
                ErrorCode.TASK_TRANSITION_INVALID,
                f"run {stored.run_id} was recorded without revision state "
                "(before migration 0018); start a new run instead",
            )
        try:
            contract = SubtaskContract.model_validate(stored.contract)
            options = RunOptions.from_json(stored.run_options)
            row_spec = RuntimeSpec.model_validate(stored.spec)
            current = runtime_spec_for(row_spec.profile_id, budget=row_spec.budget)
        except (ValidationError, ValueError, KeyError) as exc:
            raise DomainError(
                ErrorCode.TASK_TRANSITION_INVALID,
                f"run {stored.run_id} has unreadable revision state; start a new run instead",
            ) from exc
        spec = row_spec.model_copy(
            update={
                "initial_grants": current.initial_grants,
                "delegation_policy": current.delegation_policy,
            }
        )
        return _RunRecord(
            contract=contract,
            spec=spec,
            options=options,
            run_dir=Path(stored.run_dir) if stored.run_dir else None,
        )

    def _stored_run_dir(self, run_id: str, record: _RunRecord, *, action: str) -> Path | None:
        """The run's working copy: the copy source of a revision, or the
        directory a resumed run continues IN.

        It must still exist AND sit under this server's runs root — a row is
        never trusted to point the revision at an arbitrary server path. The
        error never names the path (server-side only)."""
        if record.options.workspace is None and record.run_dir is None:
            return None  # a no-workspace run revises/resumes without a workspace
        gone = DomainError(
            ErrorCode.WORKSPACE_NOT_FOUND,
            f"the working copy of run {run_id} no longer exists on this server; "
            f"it cannot be {action} — start a new run instead",
        )
        if record.run_dir is None:
            raise gone
        runs_root = self._runs_root.resolve()
        run_dir = record.run_dir.resolve()
        if run_dir == runs_root or not run_dir.is_relative_to(runs_root) or not run_dir.is_dir():
            raise gone
        return run_dir

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

    def _bind_workspace(
        self,
        run_id: str,
        options: RunOptions,
        source: Path,
        *,
        existing_dir: Path | None = None,
        before_provision: Callable[[], None] | None = None,
    ) -> _WorkspaceBinding:
        """§16: provision <runs_root>/<run_id> from `source`, bind the run's
        grants to the workspace (defense in depth, INV-04) and build the
        tool runtime + optional client verifier over it. ``existing_dir``
        (a RESUMED run, already validated under the runs root) re-binds the
        run's own working copy instead of provisioning a new one.
        ``before_provision`` runs after every refusal above and before the
        working copy is made (a revision claims its paused parent there)."""
        from aci.runtime.guardrails import (
            GuardrailManager,
            PathTraversalGuard,
            SecretLeakGuard,
            ShellInjectionGuard,
        )
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
        if verification is not None:
            # §16.5 fail closed, caller-visible: a run whose verifier WILL
            # execute a command is refused up front (403 with the reason)
            # when the sandbox cannot isolate it — before any provisioning.
            # The model's run_command calls are refused at execution instead.
            sandbox_problem = self._process_sandbox.unavailable_reason()
            if sandbox_problem is not None:
                raise DomainError(ErrorCode.PERMISSION_DENIED, sandbox_refusal(sandbox_problem))
        write = (
            [_workspace_scope(s) for s in options.write_scopes]
            if options.write_scopes is not None
            else ["."]
        )
        grants = GrantEnvelope(
            filesystem=FilesystemScope(read=["."], write=write),
            process=ProcessScope(allowed_prefixes=prefixes),
        )
        if before_provision is not None:
            before_provision()
        self._runs_root.mkdir(parents=True, exist_ok=True)
        run_dir = (
            existing_dir
            if existing_dir is not None
            else provision_workspace(source, self._runs_root, run_id)
        )
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
        workspace_id = manager.create_local(run_dir, envelope, sandbox=self._process_sandbox)
        tool_executor = build_workspace_tool_runtime(
            manager,
            workspace_id,
            allow_commands=bool(prefixes),
            command_timeout_ms=self._command_timeout_ms,
            # Second defense line (§14/§25): authority + workspace path
            # checks are the FIRST; these guards catch what a well-formed
            # but malicious call would do — shell metacharacters, `..`
            # escapes, secrets leaking back through observations.
            guardrails=GuardrailManager(
                pre_guardrails=[ShellInjectionGuard(), PathTraversalGuard()],
                post_guardrails=[SecretLeakGuard()],
            ),
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
        *,
        before_start: Callable[[], None] | None = None,
    ) -> RunResult:
        """``before_start`` runs after the run's refusals and before anything
        is created (no working copy, no record) — see revise."""
        if source is None and options.verification_command is not None:
            raise DomainError(
                ErrorCode.CLIENT_INCOMPATIBLE, "verification_command requires a workspace"
            )
        checks = self._verifier_checks(spec, options)
        if source is not None:
            binding: _WorkspaceBinding | None = self._bind_workspace(
                contract.task_id, options, source, before_provision=before_start
            )
        else:
            binding = None
            if before_start is not None:
                before_start()
        kernel, run_spec = self._build_kernel(spec, options, binding, checks)
        record = _RunRecord(
            contract=contract,
            spec=spec,
            options=options,
            run_dir=binding.run_dir if binding is not None else None,
        )
        self._records[contract.task_id] = record
        workspace_id = binding.workspace_id if binding is not None else None
        preload = (
            options.preload_capabilities
            if options.preload_capabilities is not None
            else self._preload_capabilities
        )

        def _go(token: CancelToken) -> RunResult:
            if options.max_turns is None:
                return kernel.run(
                    contract,
                    run_spec,
                    cancel_token=token,
                    workspace_id=workspace_id,
                    preload_capabilities=preload,
                )
            return kernel.run(
                contract,
                run_spec,
                cancel_token=token,
                max_turns=options.max_turns,
                workspace_id=workspace_id,
                preload_capabilities=preload,
            )

        return self._drive(contract.task_id, kernel, record, _go, resumed=False)

    def _verifier_checks(self, spec: RuntimeSpec, options: RunOptions) -> list[VerifierCallable]:
        """Command-evidence checks count only acceptance-tied commands (the
        client's verification_command); with none they fail closed."""
        from aci.runtime.profiles import verifier_checks

        return verifier_checks(
            spec.profile_id,
            acceptance_commands=(
                [options.verification_command] if options.verification_command else []
            ),
        )

    def _approval_tools(self, options: RunOptions) -> frozenset[str]:
        """§13.6: server floor ∪ client additions — a request can add an
        approval requirement, never remove one the server set."""
        requested = {t.strip() for t in options.approval_required_tools or () if t.strip()}
        return self._approval_floor | requested

    def _build_kernel(
        self,
        spec: RuntimeSpec,
        options: RunOptions,
        binding: _WorkspaceBinding | None,
        checks: list[VerifierCallable],
    ) -> tuple[HarnessKernel, RuntimeSpec]:
        """One kernel wiring per run (or resumed segment); profiles are data."""
        from aci.runtime.recovery import RecoveryManager
        from aci.runtime.state_manager import StateManager
        from aci.runtime.verification import VerificationManager

        if binding is None:
            tool_executor = cast(ToolExecutor, self._tools_factory.build())
            run_spec = spec
        else:
            tool_executor = binding.tool_executor
            run_spec = spec.model_copy(update={"initial_grants": binding.grants})
            if binding.verifier_check is not None:
                checks = [*checks, binding.verifier_check]
        kernel = HarnessKernel(
            state=StateManager(),
            model_gateway=cast(ModelGateway, self._model_factory.build()),
            tool_executor=tool_executor,
            context_engine=cast(ContextAssembler, self._context_factory.build()),
            verifier=VerificationManager(checks),
            recovery=RecoveryManager(),
            capability_runtime=cast(CapabilityHandler, self._capability_factory.build()),
            event_bus=self._event_bus,
            approval_required_tools=self._approval_tools(options),
        )
        return kernel, run_spec

    def _drive(
        self,
        run_id: str,
        kernel: HarnessKernel,
        record: _RunRecord,
        go: Callable[[CancelToken], RunResult],
        *,
        resumed: bool,
    ) -> RunResult:
        """Run (or resume) one segment of a run: cancel token, RAM result,
        the pause checkpoint if the segment paused, persistence."""
        token = CancelToken(run_id=run_id)
        self._cancel_tokens[run_id] = token
        try:
            result = go(token)
            self._results[run_id] = result
            checkpoint = kernel.pending_checkpoint(run_id)
            if checkpoint is not None:
                # A consumed checkpoint stays recorded until a newer pause
                # replaces it — a second resume is CHECKPOINT_CONSUMED.
                self._checkpoints[run_id] = checkpoint
            self._persist(record, result, checkpoint=checkpoint, resumed=resumed)
            return result
        finally:
            self._cancel_tokens.pop(run_id, None)
            # The segment is over (stopped, paused, or the kernel raised): its
            # RAM event history is dead weight on the ONE shared bus — free it
            # whether or not a store is wired, and whether or not persistence
            # succeeded. _persist has already read it when a store exists.
            self._event_bus.discard(run_id)

    def _persist(
        self,
        record: _RunRecord,
        result: RunResult,
        *,
        checkpoint: Checkpoint | None = None,
        resumed: bool = False,
    ) -> None:
        """§41.1: project the frozen stop state (terminal, or a resumable
        pause) + the event history into the durable store. Honest-null: no
        store wired = RAM-only (the pre-0016 behavior). A persistence failure
        NEVER fails the run — the result is already caller-visible; the store
        is telemetry, not a dependency (§50) — EXCEPT that an unpersisted
        pause checkpoint cannot be resumed after a restart (it still can in
        this process). A RESUMED segment's events are appended after the
        stored ones, never replacing them. The caller (_drive) discards the
        bus history afterwards in every case."""
        if self._run_store is None:
            return
        from aci.domain.runtime.persistence import AgentRunEventRecord

        contract, options = record.contract, record.options
        try:
            self._run_store.record_run(
                AgentRunRecord(
                    run_id=result.run_id,
                    parent_run_id=contract.parent_task_id,
                    profile_id=contract.requested_profile,
                    objective=contract.objective,
                    workspace=options.workspace,
                    status=result.status.value,
                    stop_reason=result.stop_reason.value if result.stop_reason else None,
                    detail_code=result.detail_code,
                    summary=result.summary,
                    artifacts=list(result.artifacts),
                    trace_ref=result.trace_ref,
                    evidence=result.evidence.model_dump(mode="json") if result.evidence else None,
                    usage=result.usage.model_dump(mode="json"),
                    spec=record.spec.model_dump(mode="json"),
                    verification_command=list(options.verification_command)
                    if options.verification_command is not None
                    else None,
                    # Migration 0018: what a revision after a restart needs.
                    contract=contract.model_dump(mode="json"),
                    run_options=options.to_json(),
                    run_dir=str(record.run_dir.resolve()) if record.run_dir is not None else None,
                    created_at=contract.created_at,
                    finished_at=None if result.status in _PAUSED else datetime.now(UTC),
                )
            )
            if checkpoint is not None and checkpoint.pending is not None:
                # Migration 0019: after the run row (FK), before the events.
                self._run_store.record_checkpoint(
                    AgentRunCheckpointRecord(
                        checkpoint_id=checkpoint.checkpoint_id,
                        run_id=checkpoint.run_id,
                        kind=checkpoint.pending.kind,
                        approval_id=checkpoint.pending.approval_id,
                        payload=checkpoint.model_dump(mode="json"),
                        created_at=checkpoint.created_at,
                    )
                )
                self._durable_checkpoints.add(checkpoint.checkpoint_id)
            history = self._event_bus.history(result.run_id)
            write_events = (
                self._run_store.append_events if resumed else self._run_store.record_events
            )
            write_events(
                [
                    AgentRunEventRecord(
                        event_id=e.event_id,
                        run_id=e.run_id,
                        seq=seq,
                        event_type=e.event_type,
                        turn_id=e.turn_id,
                        payload=dict(e.payload),
                        recorded_at=e.timestamp,
                    )
                    for seq, e in enumerate(history)
                ]
            )
        except Exception:  # noqa: BLE003 — telemetry must never kill a finished run
            log.warning(
                "agent run %s persistence FAILED (result stays caller-visible)",
                result.run_id,
                exc_info=True,
            )


class _PauseLost(Exception):
    """revise of a paused run lost the race for the parent's pause (a
    resume/cancel claimed it first) — raised before anything was created."""


def _consumed_error(run_id: str) -> DomainError:
    return DomainError(
        ErrorCode.CHECKPOINT_CONSUMED,
        f"run {run_id}: this pause was already resumed (a checkpoint resumes at most once)",
    )
