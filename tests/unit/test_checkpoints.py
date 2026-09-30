"""CheckpointCoordinator tests (harness.md §17, INV-13): only serializable
state, exact round-trip of the CURRENT RuntimeStateSnapshot, newest by
version, resume validation."""

from datetime import UTC, datetime, timedelta

import pytest

from aci.domain.runtime.authority import FilesystemScope, GrantEnvelope
from aci.domain.runtime.evidence import EvidenceItem, EvidenceKind
from aci.domain.runtime.state import (
    BudgetLedger,
    CapabilityActivation,
    PlanItem,
    RunState,
    RuntimeStateSnapshot,
    TaskState,
    TranscriptEntry,
)
from aci.domain.runtime.stop_reason import RunStatus
from aci.domain.runtime.tools import ToolCall
from aci.runtime.checkpoints import (
    CHECKPOINT_SCHEMA_VERSION,
    Checkpoint,
    CheckpointCoordinator,
    CheckpointError,
    CheckpointStore,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _snapshot(
    *, version: int = 3, turn: int = 2, expires_at: datetime | None = None
) -> RuntimeStateSnapshot:
    """A snapshot with EVERY field populated — the round-trip must be exact."""
    return RuntimeStateSnapshot(
        run=RunState(
            run_id="run-1",
            status=RunStatus.RUNNING,
            version=version,
            current_turn=turn,
            created_at=NOW,
            started_at=NOW + timedelta(seconds=1),
        ),
        task=TaskState(
            task_id="t",
            objective="fix the bug",
            constraints=["no network"],
            acceptance_criteria=["tests pass"],
            progress=["turn 1: 1 tool observation(s)"],
            unresolved_questions=["which python?"],
        ),
        budget=BudgetLedger(consumed_turns=turn, consumed_input_tokens=120, consumed_cost_usd=0.01),
        grants=GrantEnvelope(
            filesystem=FilesystemScope(read=["."], write=["out"]), expires_at=expires_at
        ),
        plan=[PlanItem(item_id="p1", objective="x", status="running", evidence_refs=["e"])],
        active_capabilities=[
            CapabilityActivation(
                capability_id="cap",
                version="1.0.0",
                digest="d",
                activation_id="a",
                activated_at=NOW,
            )
        ],
        workspace_id="ws-1",
        depth=1,
        changed_resources=["file:out/app.py"],
        observed_evidence=[
            EvidenceItem(kind=EvidenceKind.FILE_STATE, ref="file://out/app.py", sha256="ab")
        ],
        transcript=[
            TranscriptEntry(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        call_id="c1",
                        tool_id="write_file",
                        arguments={"path": "out/app.py", "n": 1, "nested": {"k": [1, 2.5]}},
                    )
                ],
                turn=1,
            ),
            TranscriptEntry(role="tool", tool_call_id="c1", content="wrote out/app.py", turn=1),
        ],
    )


class TestRoundTrip:
    def test_snapshot_survives_json_round_trip_exactly(self) -> None:
        snapshot = _snapshot(expires_at=NOW + timedelta(hours=1))
        assert RuntimeStateSnapshot.model_validate(snapshot.model_dump(mode="json")) == snapshot

    def test_save_and_restore_reproduce_the_snapshot(self) -> None:
        coord = CheckpointCoordinator(CheckpointStore())
        snapshot = _snapshot()
        saved = coord.save(
            snapshot, checkpoint_id="cp-1", pending_approval_id="apr-1", artifact_refs=["a://1"]
        )
        assert saved.state_version == 3 and saved.turn == 2
        assert saved.schema_version == CHECKPOINT_SCHEMA_VERSION
        restored = coord.restore("cp-1", now=NOW)
        assert restored.snapshot == snapshot
        assert restored.pending_approval_id == "apr-1"
        assert restored.artifact_refs == ["a://1"]
        assert restored == saved

    def test_restore_returns_the_serialized_form_not_the_live_object(self) -> None:
        coord = CheckpointCoordinator(CheckpointStore())
        saved = coord.save(_snapshot(), checkpoint_id="cp-1")
        restored = coord.restore("cp-1", now=NOW)
        assert restored is not saved
        assert restored.snapshot is not saved.snapshot


class TestSerializableOnly:
    def test_live_object_in_state_is_refused(self) -> None:
        """INV-13: a handle smuggled into tool arguments never reaches the store."""

        class Handle:
            pass

        snapshot = _snapshot().model_copy(
            update={
                "transcript": [
                    TranscriptEntry(
                        role="assistant",
                        tool_calls=[ToolCall(call_id="c", tool_id="t", arguments={"h": Handle()})],
                    )
                ]
            }
        )
        store = CheckpointStore()
        with pytest.raises(CheckpointError, match="non-serializable"):
            CheckpointCoordinator(store).save(snapshot, checkpoint_id="cp-bad")
        assert store.load("cp-bad") is None

    def test_restore_missing_raises(self) -> None:
        coord = CheckpointCoordinator(CheckpointStore())
        with pytest.raises(CheckpointError, match="not found"):
            coord.restore("nope")

    def test_schema_version_mismatch_refused(self) -> None:
        store = CheckpointStore()
        stale = Checkpoint(
            checkpoint_id="old",
            run_id="run-1",
            schema_version=CHECKPOINT_SCHEMA_VERSION + 1,
            state_version=1,
            turn=0,
            snapshot=_snapshot(),
        )
        store.save(stale)
        with pytest.raises(CheckpointError, match="migration required"):
            CheckpointCoordinator(store).restore("old")


class TestResumeValidation:
    def test_expired_grants_refuse_resume(self) -> None:
        """§17.4 step 4: a checkpoint whose grants have lapsed is not resumable."""
        coord = CheckpointCoordinator(CheckpointStore())
        coord.save(_snapshot(expires_at=NOW + timedelta(minutes=5)), checkpoint_id="cp-1")
        assert coord.restore("cp-1", now=NOW).run_id == "run-1"
        with pytest.raises(CheckpointError, match="grants expired"):
            coord.restore("cp-1", now=NOW + timedelta(minutes=6))

    def test_no_expiry_always_resumable(self) -> None:
        coord = CheckpointCoordinator(CheckpointStore())
        coord.save(_snapshot(expires_at=None), checkpoint_id="cp-1")
        assert coord.restore("cp-1", now=NOW + timedelta(days=365)).run_id == "run-1"


class TestLatest:
    def test_latest_is_the_most_advanced_state_not_the_last_saved(self) -> None:
        coord = CheckpointCoordinator(CheckpointStore())
        coord.save(_snapshot(version=9, turn=4), checkpoint_id="newer")
        coord.save(_snapshot(version=2, turn=1), checkpoint_id="older")
        latest = coord.latest("run-1")
        assert latest is not None and latest.checkpoint_id == "newer"

    def test_ties_prefer_the_most_recently_saved(self) -> None:
        coord = CheckpointCoordinator(CheckpointStore())
        coord.save(_snapshot(), checkpoint_id="cp-1")
        coord.save(_snapshot(), checkpoint_id="cp-2")
        latest = coord.latest("run-1")
        assert latest is not None and latest.checkpoint_id == "cp-2"

    def test_resaving_an_id_does_not_duplicate_it(self) -> None:
        store = CheckpointStore()
        coord = CheckpointCoordinator(store)
        coord.save(_snapshot(version=1, turn=0), checkpoint_id="cp-1")
        coord.save(_snapshot(version=5, turn=3), checkpoint_id="cp-1")
        assert store._by_run["run-1"] == ["cp-1"]
        latest = coord.latest("run-1")
        assert latest is not None and latest.state_version == 5

    def test_unknown_run_has_no_latest(self) -> None:
        assert CheckpointCoordinator(CheckpointStore()).latest("ghost") is None
