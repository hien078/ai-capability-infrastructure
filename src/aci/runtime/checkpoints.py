"""CheckpointCoordinator (harness.md §17): safe serializable state only.

INV-13: never checkpoint live sockets, process handles, provider clients, or
non-serializable closures. Checkpoints are versioned; resume validates schema
version, structural round-trip and grant expiry (§17.4 steps 1 and 4).
"""

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from aci.domain.runtime.state import RuntimeStateSnapshot

CHECKPOINT_SCHEMA_VERSION = 1


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


class CheckpointStore:
    """In-memory store (v2 default). A Postgres/CheckpointStore lands with the
    persistence phase; the Protocol shape is what matters now."""

    def __init__(self) -> None:
        self._by_id: dict[str, Checkpoint] = {}
        self._by_run: dict[str, list[str]] = {}

    def save(self, checkpoint: Checkpoint) -> None:
        self._by_id[checkpoint.checkpoint_id] = checkpoint
        ids = self._by_run.setdefault(checkpoint.run_id, [])
        if checkpoint.checkpoint_id not in ids:
            ids.append(checkpoint.checkpoint_id)

    def load(self, checkpoint_id: str) -> Checkpoint | None:
        return self._by_id.get(checkpoint_id)

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


def _assert_serializable(checkpoint: Checkpoint) -> None:
    """INV-13 guard: refuse to checkpoint anything that cannot round-trip
    through strict JSON (no ``default=`` fallback that would stringify a
    live handle)."""
    try:
        payload: dict[str, Any] = checkpoint.model_dump(mode="json")
        json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise CheckpointError(f"non-serializable state: {exc}") from exc
