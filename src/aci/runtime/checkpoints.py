"""CheckpointCoordinator (harness.md §17): safe serializable state only.

INV-13: never checkpoint live sockets, process handles, provider clients, or
non-serializable closures. Checkpoints are versioned; resume validates schema
version, structural round-trip and grant expiry (§17.4 steps 1 and 4).

A PAUSE checkpoint (§13.6 approval interrupt, §7.5 clarification) is
self-contained: besides the full state snapshot it carries the run's
contract + spec + turn ceiling + usage so far, and the pending interrupt
(the unexecuted tool batch, or the open question) — everything
``HarnessKernel.resume`` needs in a fresh process after a restart.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.state import RuntimeStateSnapshot
from aci.domain.runtime.subtask import RunUsage, SubtaskContract
from aci.domain.runtime.tools import ToolCall

CHECKPOINT_SCHEMA_VERSION = 1


def operation_hash(call: ToolCall) -> str:
    """§27.4 — what an approval binds to: the exact tool + arguments of the
    paused call (canonical JSON). A changed operation never matches."""
    canonical = json.dumps(
        {"tool_id": call.tool_id, "arguments": call.arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PendingInterrupt(BaseModel):
    """What a paused run is waiting for (§13.6 / §7.5).

    ``approval``: ``calls`` is the UNEXECUTED rest of the paused tool batch,
    the gated call first (its id is ``gated_call_id``); the calls before it
    already executed and were committed before the pause. ``clarification``:
    the model's open question; the answer is appended to the transcript."""

    model_config = {"frozen": True}

    kind: Literal["approval", "clarification"]
    turn: int = Field(ge=0)
    approval_id: str | None = None
    gated_call_id: str | None = None
    operation_hash: str | None = None
    calls: list[ToolCall] = Field(default_factory=list)
    reason: str = ""
    question: str = ""


class Checkpoint(BaseModel):
    """§17.3 checkpoint content — everything JSON-serializable."""

    model_config = {"frozen": True}

    checkpoint_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    state_version: int = Field(ge=1)
    turn: int = Field(ge=0)
    snapshot: RuntimeStateSnapshot
    pending_approval_id: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    #: Pause checkpoints only (None on periodic ones): the interrupt being
    #: waited on + the run context resume rebuilds the loop from.
    pending: PendingInterrupt | None = None
    contract: SubtaskContract | None = None
    spec: RuntimeSpec | None = None
    max_turns: int | None = Field(default=None, ge=1)
    #: Usage accumulated before the pause — budgets are NOT reset by resume.
    usage: RunUsage | None = None


class CheckpointStore:
    """In-memory store (v2 default). A Postgres/CheckpointStore lands with the
    persistence phase; the Protocol shape is what matters now."""

    def __init__(self) -> None:
        self._by_id: dict[str, Checkpoint] = {}
        self._by_run: dict[str, list[str]] = {}
        self._consumed: set[str] = set()

    def save(self, checkpoint: Checkpoint) -> None:
        self._by_id[checkpoint.checkpoint_id] = checkpoint
        ids = self._by_run.setdefault(checkpoint.run_id, [])
        if checkpoint.checkpoint_id not in ids:
            ids.append(checkpoint.checkpoint_id)

    def load(self, checkpoint_id: str) -> Checkpoint | None:
        return self._by_id.get(checkpoint_id)

    def consume(self, checkpoint_id: str) -> bool:
        """Claim a checkpoint for resume: True exactly once per id (§17.4 —
        a checkpoint is resumed at most once)."""
        if checkpoint_id in self._consumed:
            return False
        self._consumed.add(checkpoint_id)
        return True

    def latest_for_run(self, run_id: str) -> Checkpoint | None:
        """The checkpoint of the most advanced state (version, then turn);
        insertion order only breaks exact ties."""
        ids = self._by_run.get(run_id)
        if not ids:
            return None
        return max(
            (self._by_id[cid] for cid in ids),
            key=lambda cp: (cp.state_version, cp.turn, ids.index(cp.checkpoint_id)),
        )


class CheckpointError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CheckpointCoordinator:
    """Decides when checkpointing is safe (§17.1) and validates on resume."""

    def __init__(self, store: CheckpointStore) -> None:
        self._store = store

    def save(
        self,
        snapshot: RuntimeStateSnapshot,
        *,
        pending_approval_id: str | None = None,
        artifact_refs: list[str] | None = None,
        checkpoint_id: str,
    ) -> Checkpoint:
        checkpoint = Checkpoint(
            checkpoint_id=checkpoint_id,
            run_id=snapshot.run.run_id,
            state_version=snapshot.run.version,
            turn=snapshot.run.current_turn,
            snapshot=snapshot,
            pending_approval_id=pending_approval_id,
            artifact_refs=artifact_refs or [],
        )
        _assert_serializable(checkpoint)
        self._store.save(checkpoint)
        return checkpoint

    def record(self, checkpoint: Checkpoint) -> Checkpoint:
        """Store a checkpoint the kernel built itself (a PAUSE checkpoint
        carries the pending interrupt + run context, not only the snapshot)."""
        _assert_serializable(checkpoint)
        self._store.save(checkpoint)
        return checkpoint

    def consume(self, checkpoint_id: str) -> bool:
        """Claim a checkpoint for resume — True exactly once (§17.4)."""
        return self._store.consume(checkpoint_id)

    def restore(self, checkpoint_id: str, *, now: datetime | None = None) -> Checkpoint:
        """§17.4 resume validation: schema version, structural round-trip from
        the serialized form, grants not expired. Returns the round-tripped
        checkpoint — what a persisted store would hand back."""
        checkpoint = self._store.load(checkpoint_id)
        if checkpoint is None:
            raise CheckpointError(f"checkpoint not found: {checkpoint_id}")
        if checkpoint.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointError(
                f"checkpoint schema {checkpoint.schema_version} != "
                f"{CHECKPOINT_SCHEMA_VERSION} — migration required"
            )
        try:
            restored = Checkpoint.model_validate(checkpoint.model_dump(mode="json"))
        except ValidationError as exc:
            raise CheckpointError(f"checkpoint corrupt: {exc}") from exc
        expires_at = restored.snapshot.grants.expires_at
        if expires_at is not None:
            moment = now or datetime.now(UTC)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if moment >= expires_at:
                raise CheckpointError(
                    f"checkpoint {checkpoint_id}: grants expired at {expires_at.isoformat()}"
                )
        return restored

    def latest(self, run_id: str) -> Checkpoint | None:
        return self._store.latest_for_run(run_id)


def validate_for_resume(checkpoint: Checkpoint) -> Checkpoint:
    """§17.4 steps 1–2 for a PAUSE checkpoint: schema version, strict JSON
    round-trip, and the self-contained resume context. Grant expiry is NOT
    a refusal here — expired grants stay expired and the resumed run ends
    AUTHORITY_EXPIRED on its next tool batch (never renewed by a resume)."""
    if checkpoint.schema_version != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointError(
            f"checkpoint schema {checkpoint.schema_version} != "
            f"{CHECKPOINT_SCHEMA_VERSION} — migration required"
        )
    _assert_serializable(checkpoint)
    try:
        restored = Checkpoint.model_validate(checkpoint.model_dump(mode="json"))
    except ValidationError as exc:
        raise CheckpointError(f"checkpoint corrupt: {exc}") from exc
    if restored.pending is None or restored.contract is None or restored.spec is None:
        raise CheckpointError(f"checkpoint {checkpoint.checkpoint_id} is not a pause checkpoint")
    if restored.snapshot.run.run_id != restored.run_id:
        raise CheckpointError(f"checkpoint {checkpoint.checkpoint_id} names another run")
    pending = restored.pending
    if pending.kind == "approval":
        gated = pending.calls[0] if pending.calls else None
        if (
            gated is None
            or not pending.approval_id
            or gated.call_id != pending.gated_call_id
            or operation_hash(gated) != pending.operation_hash
        ):
            raise CheckpointError(
                f"checkpoint {checkpoint.checkpoint_id}: pending operation does not match "
                "its approval binding (§27.4)"
            )
    return restored


def _assert_serializable(checkpoint: Checkpoint) -> None:
    """INV-13 guard: refuse to checkpoint anything that cannot round-trip
    through strict JSON (no ``default=`` fallback that would stringify a
    live handle)."""
    try:
        payload: dict[str, Any] = checkpoint.model_dump(mode="json")
        json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise CheckpointError(f"non-serializable state: {exc}") from exc
