"""capctl candidate-store durability (the §80 human-gate audit record).

``_save_candidate`` was a plain ``write_text``: a crash (or disk-full) mid-write
truncates the candidate JSON — the append-only record of who approved/rejected
what. The repo already fixed this exact class for the worker queue state
(``aci_worker_queue.QueueState.save``: temp + fsync + atomic rename + dir
fsync, "a crash mid-write can never leave a truncated/corrupt state file").
These tests pin the same durability for the governance audit trail, plus the
read-modify-write lock around a lifecycle transition (two concurrent capctl
decisions on one candidate otherwise lose one to last-write-wins).
"""

import fcntl
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import capctl  # noqa: E402

from aci.domain.acquisition.models import CandidateProposal, CandidateRecord  # noqa: E402


def _record(candidate_id: str = "cand-1", *, status: str = "proposed") -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id,
        proposal=CandidateProposal(
            candidate_id=candidate_id,
            source_repo="https://github.com/example/skills",
            source_path="skills/debugging",
            observed_revision="abc123def456",
            reason="fills the measured debugging gap",
            discovery_confidence=0.8,
            proposed_at=datetime(2026, 10, 1, tzinfo=UTC),
        ),
        status=status,  # type: ignore[arg-type]
        decided_by="scout",
    )


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(capctl, "CANDIDATE_ROOT", tmp_path)
    return tmp_path


class TestSaveCandidateDurability:
    def test_crash_mid_save_never_truncates_the_audit_record(
        self, store: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The record on disk is always a COMPLETE decision — the old one or
        the new one, never a half-written file. Simulated crash: the atomic
        rename dies; on the old plain-write code the target had already been
        truncated/clobbered by the time anything could fail."""
        capctl._save_candidate(_record())  # seed a complete record
        target = store / "cand-1.json"
        before = target.read_text(encoding="utf-8")

        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("crash before the rename")

        monkeypatch.setattr("os.replace", boom)
        with pytest.raises(OSError, match="crash before the rename"):
            capctl._save_candidate(_record(status="source_approved"))
        # The OLD complete decision survives byte-for-byte and still parses.
        assert target.read_text(encoding="utf-8") == before
        CandidateRecord.model_validate_json(target.read_text(encoding="utf-8"))
        # No temp litter: the store holds exactly the record file.
        assert [p.name for p in sorted(store.iterdir())] == ["cand-1.json"]

    def test_save_then_load_round_trips(self, store: Path) -> None:
        record = _record(status="source_approved")
        capctl._save_candidate(record)
        loaded = capctl._load_candidate("cand-1")
        assert loaded == record

    def test_save_is_a_durable_write_not_a_plain_write(
        self, store: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pin the mechanism (not just the crash outcome): the bytes are fsync'd
        in a temp file and moved into place with os.replace — the same pattern
        as QueueState.save."""
        saved: dict[str, object] = {}
        real_replace = capctl.os.replace
        real_fsync = capctl.os.fsync

        def spy_replace(src: object, dst: object) -> None:
            saved["src"] = src
            saved["dst"] = dst
            real_replace(src, dst)  # type: ignore[arg-type]

        def spy_fsync(fd: object) -> None:
            saved["fsync"] = True
            real_fsync(fd)  # type: ignore[arg-type]

        monkeypatch.setattr("os.replace", spy_replace)
        monkeypatch.setattr("os.fsync", spy_fsync)
        capctl._save_candidate(_record())
        assert Path(str(saved["src"])).name == "cand-1.json.tmp"
        assert Path(str(saved["dst"])).name == "cand-1.json"
        assert saved.get("fsync") is True


class TestTransitionLock:
    def test_transition_takes_an_exclusive_lock(self, store: Path) -> None:
        """While one capctl process is mid-transition, a second one must not
        interleave its own load→advance→save (last-write-wins would silently
        drop one human decision from the audit trail)."""
        capctl._save_candidate(_record())
        lock_path = store / "cand-1.json.lock"
        with capctl._candidate_lock("cand-1"):
            assert lock_path.exists()
            with open(lock_path, "w", encoding="utf-8") as fh:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Released afterwards: a fresh non-blocking acquisition succeeds.
        with open(lock_path, "w", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_transition_through_the_lock_lands(
        self, store: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lock wraps the whole read-modify-write: a transition run under
        it still lands (no deadlock on the lock the transition itself holds)."""
        capctl._save_candidate(_record())
        args = argparse_namespace(by="alice", reason=None, candidate_id="cand-1")
        code = capctl._transition(args, "source_approved")
        assert code == 0
        loaded = capctl._load_candidate("cand-1")
        assert loaded.status == "source_approved"
        assert loaded.decided_by == "alice"


def argparse_namespace(**kwargs: object) -> "capctl.argparse.Namespace":
    import argparse

    return argparse.Namespace(**kwargs)  # type: ignore[arg-type]
