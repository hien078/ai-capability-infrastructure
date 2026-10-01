"""Deterministic H-bench PENDING-case tests (H005/H006/H007/H011).

Each fixture from scripts/hbench_pending_cases.py is driven through the REAL
AgentRunService (the same service composition run_hbench's arm K builds: real
workspace tool runtime + guardrails + per-run working copy) with a scripted
FakeModelGateway — no network, no DB, NoSandbox processes (the explicit
opt-out: these are scripted tests, never model-written code; the real runner
sandboxes with bwrap). The assertions are the KERNEL mechanisms the cases
exist to measure — events, stop reasons, budget ledgers — never the model's
intelligence:

  H005  the context flood stays within the budget, with the dropped turns
        RECORDED, and INV-10 bounds every oversized read;
  H006  the approval gate pauses BEFORE the gated effect; resume(approve)
        executes it exactly once; deny never writes and never succeeds;
  H007  the injected transient read failures are retried with backoff
        (bounded) and the run still completes verified;
  H011  a run crashed while paused resumes from the DURABLE checkpoint in a
        FRESH process with budget continuity and no duplicated effect.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import hbench_pending_cases as hpc  # noqa: E402

from aci.domain.capability.errors import DomainError, ErrorCode  # noqa: E402
from aci.domain.runtime.state import BudgetLedger  # noqa: E402
from aci.domain.runtime.stop_reason import RunStatus, StopReason  # noqa: E402
from aci.runtime.event_bus import (  # noqa: E402
    CHECKPOINT_RESTORED,
    CHECKPOINT_SAVED,
    CONTEXT_ASSEMBLED,
    RECOVERY_ACTION,
    RUN_PAUSED,
    RUN_RESUMED,
    TOOL_APPROVAL_DECIDED,
    TOOL_APPROVAL_REQUESTED,
    TOOL_EXECUTION_COMPLETED,
    TOOL_EXECUTION_FAILED,
)
from aci.runtime.sandbox import NoSandbox  # noqa: E402
from aci.runtime.workspace import LocalWorkspace  # noqa: E402

#: The INV-10 inline cap (+ the status line and the truncation marker) — the
#: bound under which every oversized read the model sees must stay.
_MAX_INLINE = 12_200


class TestFixturePack:
    """The instrument's own structure: four cases, one per PENDING H-id,
    runner-compatible fixture dicts, and the knobs each mechanism needs."""

    def test_the_four_pending_cases_exist(self) -> None:
        assert [c.case_id for c in hpc.PENDING_CASES] == ["H005", "H006", "H007", "H011"]

    def test_fixture_dicts_are_runner_compatible(self) -> None:
        """The dicts materialize exactly like a §80 fixture entry (name /
        files / prompt) and carry the h_ref + knobs the wiring reads."""
        for fixture in hpc.pending_fixtures():
            assert fixture["name"] and fixture["files"] and fixture["prompt"]
            assert fixture["h_ref"] == fixture["case_id"]
            assert set(fixture) == {
                "name",
                "files",
                "prompt",
                "case_id",
                "h_ref",
                "context_budget_tokens",
                "approval_required_tools",
                "transient_read_failures",
                "crash_after_turn",
            }

    def test_each_case_declares_its_mechanism_knobs(self) -> None:
        by_id = {c.case_id: c for c in hpc.PENDING_CASES}
        assert by_id["H005"].context_budget_tokens is not None
        assert by_id["H006"].approval_required_tools == ("edit_file",)
        assert by_id["H007"].transient_read_failures >= 1
        assert by_id["H011"].crash_after_turn is not None
        assert by_id["H011"].approval_required_tools == ("edit_file",)

    def test_every_case_documents_its_pass_criteria(self) -> None:
        """PASS is defined in KERNEL terms on the case itself — the spec is
        the instrument's documentation, not just data."""
        for case in hpc.PENDING_CASES:
            assert len(case.pass_criteria) >= 3, case.case_id
            assert all(case.pass_criteria), case.case_id

    def test_case_by_id_fails_loudly_on_a_typo(self) -> None:
        with pytest.raises(KeyError):
            hpc.case_by_id("H004")  # a §80-covered case, not a PENDING one


class TestFixtureIntegrity:
    def test_every_case_fails_as_shipped(self, tmp_path: Path) -> None:
        """The §80 discipline, automated: no fixture is accidentally green
        before the agent works — a fixture that passed as shipped would
        measure nothing. ("Passes after the fix" is what each mechanism
        test's SUCCEEDED proves: the kernel's verifier ran this same command
        in the run's working copy and gated the completion on it.)"""
        for case in hpc.PENDING_CASES:
            workspace = hpc.materialize(case, tmp_path / "sources")
            result = LocalWorkspace(workspace, sandbox=NoSandbox()).execute(
                hpc.VERIFICATION, 120_000
            )
            assert result.exit_code != 0, f"{case.name} must fail as shipped"


class TestH005ContextFlood:
    def test_flood_stays_within_the_budget_with_drops_recorded(self, tmp_path: Path) -> None:
        case = hpc.case_by_id("H005")
        assert case.answer is not None and case.context_budget_tokens is not None
        script = [
            hpc.read_call("r1", "biglog-a.txt"),
            hpc.read_call("r2", "biglog-b.txt"),
            hpc.read_call("r3", "biglog-c.txt"),
            hpc.write_call("w1", "answer.txt", case.answer),
            hpc.final(f"counted {case.answer} ERROR lines", changes=["answer.txt"]),
        ]
        run = hpc.ScriptedCaseRun(case, script, tmp_path)
        result = run.run()

        # the run completes VERIFIED: the answer is right and the fixture's
        # own test — which recomputes the count from the logs — passed.
        assert result.status is RunStatus.SUCCEEDED, result
        assert result.stop_reason is StopReason.SUCCESS
        answer = (run.run_dir(result.run_id) / "answer.txt").read_text()
        assert answer.strip() == case.answer

        events = run.events_of(result.run_id)
        # (1) EVERY context assembly stayed within the case budget — the
        #     ~126KB of logs never blew the context.
        assembled = [e for e in events if e.event_type == CONTEXT_ASSEMBLED]
        assert assembled, "the kernel must emit context.assembled per turn"
        for event in assembled:
            assert event.payload["total_tokens"] <= case.context_budget_tokens, event.payload
        # (2) the flood turns were dropped AND RECORDED (INV-09): once newer
        #     turns exist, the oldest read groups fall out of the transcript.
        assert any(e.payload["dropped_turns"] >= 1 for e in assembled), [
            e.payload for e in assembled
        ]
        # (3) INV-10 bounded every read: each flood text that reached the
        #     model is head+tail with a truncation marker, never the ~48KB.
        flooded = [
            m
            for r in run.gateway.requests
            for m in r.messages
            if m.role == "tool" and "svc-" in m.content
        ]
        assert len(flooded) >= 3  # one per read, the kept ones replayed per turn
        for message in flooded:
            assert len(message.content) <= _MAX_INLINE, len(message.content)
            assert "truncated" in message.content
        # (4) the drop is real, not cosmetic: the request right after the
        #     first read carried biglog-a, the FINAL request does not (its
        #     turn group was dropped) — while the newest read survives.
        assert any("svc-a" in m.content for m in run.gateway.requests[1].messages)
        final_messages = run.gateway.requests[-1].messages
        assert not any("svc-a" in m.content for m in final_messages)
        assert not any("svc-b" in m.content for m in final_messages)
        assert any("svc-c" in m.content for m in final_messages)


class TestH006ApprovalGate:
    def test_gated_edit_pauses_before_the_effect_then_resume_completes(
        self, tmp_path: Path
    ) -> None:
        case = hpc.case_by_id("H006")
        assert case.fix is not None
        old, new = case.fix
        script = [
            hpc.read_call("r1", "counter.py"),
            hpc.edit_call("e1", "counter.py", old, new),
            hpc.final("fixed the mutation at the root cause", changes=["counter.py"]),
        ]
        run = hpc.ScriptedCaseRun(case, script, tmp_path)
        paused = run.run()

        # -- the pause: BEFORE the gated effect, caller-visible, resumable --
        assert paused.status is RunStatus.INTERRUPTED_APPROVAL, paused
        assert paused.stop_reason is StopReason.AWAITING_APPROVAL
        assert paused.detail_code == "APPROVAL_REQUIRED"
        assert paused.approval_id is not None and paused.approval_id.startswith("apr_")
        # the gated call did NOT run: the working copy is still the buggy one
        buggy = (run.run_dir(paused.run_id) / "counter.py").read_text()
        assert "points += [bonus]" in buggy
        events = run.events_of(paused.run_id)
        types = [e.event_type for e in events]
        assert TOOL_APPROVAL_REQUESTED in types
        assert CHECKPOINT_SAVED in types
        assert types[-1] == RUN_PAUSED  # a pause is not terminal
        # telemetry hygiene (§61): the approval event carries tool ids only
        requested = next(e for e in events if e.event_type == TOOL_APPROVAL_REQUESTED)
        assert requested.payload["tool_id"] == "edit_file"
        assert "counter.py" not in str(requested.payload)

        # -- the resume: approve -> the edit executes EXACTLY once -> verified --
        resumed = run.resume(paused.run_id, approval_id=paused.approval_id, approve=True)
        assert resumed.status is RunStatus.SUCCEEDED, resumed
        assert resumed.stop_reason is StopReason.SUCCESS
        assert resumed.detail_code is None  # the pause's detail never leaks
        fixed = (run.run_dir(paused.run_id) / "counter.py").read_text()
        assert "points += [bonus]" not in fixed
        assert "return [p + bonus for p in points]" in fixed
        # exactly ONE edit execution across both segments (no duplicated effect)
        edits = [
            e
            for e in run.events_of(paused.run_id)
            if e.event_type == TOOL_EXECUTION_COMPLETED and e.payload["tool_id"] == "edit_file"
        ]
        assert len(edits) == 1
        # the checkpoint is one-shot: a second resume is refused
        with pytest.raises(DomainError) as excinfo:
            run.resume(paused.run_id, approval_id=paused.approval_id, approve=True)
        assert excinfo.value.code is ErrorCode.CHECKPOINT_CONSUMED

    def test_denied_resume_never_writes_and_can_never_succeed(self, tmp_path: Path) -> None:
        case = hpc.case_by_id("H006")
        assert case.fix is not None
        old, new = case.fix
        script = [
            hpc.read_call("r1", "counter.py"),
            hpc.edit_call("e1", "counter.py", old, new),
            hpc.final("no fix applied: the approval was denied", changes=[]),
        ]
        # max_recoveries=0: the unverifiable candidate ends the run at the
        # FIRST verification failure — the assertion is about the DENIAL,
        # not the repair loop (that path is test_run_resume's job).
        run = hpc.ScriptedCaseRun(
            case, script, tmp_path, spec_budget=BudgetLedger(max_recoveries=0)
        )
        paused = run.run()
        assert paused.status is RunStatus.INTERRUPTED_APPROVAL
        denied = run.resume(paused.run_id, approval_id=paused.approval_id, approve=False)

        # the denial is caller-visible and the effect NEVER happened
        assert denied.status is RunStatus.FAILED, denied
        assert denied.stop_reason is StopReason.VERIFICATION_FAILED
        source = (run.run_dir(paused.run_id) / "counter.py").read_text()
        assert "points += [bonus]" in source  # untouched
        # the model SAW the denial (its next request carries it) — and no
        # edit ever executed, so no SUCCEEDED was ever possible (INV-08).
        last = run.gateway.requests[-1].messages
        denials = [m for m in last if m.role == "tool" and m.tool_call_id == "e1"]
        assert len(denials) == 1
        assert denials[0].content.startswith("status: denied (APPROVAL_REJECTED)")
        assert "DENIED approval" in denials[0].content
        executed = [
            e
            for e in run.events_of(paused.run_id)
            if e.event_type == TOOL_EXECUTION_COMPLETED and e.payload["tool_id"] == "edit_file"
        ]
        assert executed == []


class TestH007TransientReadFailure:
    def test_transient_read_failures_are_retried_with_backoff_and_succeed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        case = hpc.case_by_id("H007")
        assert case.answer is not None
        injector = hpc.TransientReadFailureInjector(
            tool_ids=("read_file",), failures=case.transient_read_failures
        )
        # The injection seam: the service builds the real workspace tool
        # runtime internally, so the DISPATCHER is what a driver can patch
        # (the fixture module documents this as the H007 wiring).
        monkeypatch.setattr(
            "aci.runtime.workspace_tools.WorkspaceToolDispatcher",
            injector.dispatcher_class(),
        )
        script = [
            hpc.read_call("r1", "notes.txt"),
            hpc.write_call("w1", "answer.txt", case.answer),
            hpc.final("read the note and copied the secret word", changes=["answer.txt"]),
        ]
        run = hpc.ScriptedCaseRun(case, script, tmp_path)
        result = run.run()

        assert result.status is RunStatus.SUCCEEDED, result
        # the read dispatched failures+1 times: the injector's counter is the
        # retry proof (2 scripted failures + the successful third dispatch).
        assert injector.attempts["read_file"] == case.transient_read_failures + 1
        events = run.events_of(result.run_id)
        # bounded retries through the RecoveryManager, charged and recorded:
        # RETRY_SAME per failure, component tool_runtime, class TRANSIENT_TOOL,
        # attempts 1..N. (The backoff curve itself is unit-pinned in
        # test_tool_recovery; through the service it is real time.sleep —
        # 1s + 2s of wall time for the default two failures.)
        recoveries = [dict(e.payload) for e in events if e.event_type == RECOVERY_ACTION]
        assert recoveries == [
            {
                "failure_class": "TRANSIENT_TOOL",
                "action": "RETRY_SAME",
                "component": "tool_runtime",
                "tool_id": "read_file",
                "attempt": 1,
            },
            {
                "failure_class": "TRANSIENT_TOOL",
                "action": "RETRY_SAME",
                "component": "tool_runtime",
                "tool_id": "read_file",
                "attempt": 2,
            },
        ]
        # INV-15: every failed attempt is telemetry too
        failed = [e for e in events if e.event_type == TOOL_EXECUTION_FAILED]
        assert len(failed) == case.transient_read_failures
        # the model saw ONE successful result for its one call — the retries
        # happened inside the batch, invisible on the wire.
        results = [
            m.content
            for r in run.gateway.requests
            for m in r.messages
            if m.role == "tool" and m.tool_call_id == "r1"
        ]
        assert results and all(c.startswith("status: success") for c in results)
        # the retries are real executions (§7.6): read attempts + the write
        assert result.usage.tool_calls == case.transient_read_failures + 2
        # and the answer is right — the fixture's own test passed
        assert (run.run_dir(result.run_id) / "answer.txt").read_text().strip() == case.answer


class TestH011CrashResume:
    def test_crash_while_paused_resumes_from_the_durable_checkpoint(self, tmp_path: Path) -> None:
        case = hpc.case_by_id("H011")
        assert case.fix is not None and case.crash_after_turn is not None
        old, new = case.fix
        store = hpc.MemoryRunStore()
        paused, resumed, fresh = hpc.crash_and_resume(
            case,
            tmp_path,
            script_before_pause=[
                hpc.read_call("r1", "vip.py"),
                hpc.edit_call("e1", "vip.py", old, new),
            ],
            script_after_restart=[hpc.final("fixed the inclusive boundary", changes=["vip.py"])],
            store=store,
        )

        # -- segment 1 paused at the crash point, DURABLY --
        assert paused.status is RunStatus.INTERRUPTED_APPROVAL
        assert paused.stop_reason is StopReason.AWAITING_APPROVAL
        assert paused.usage.turns == case.crash_after_turn  # the crash point
        checkpoint = store.latest_checkpoint(paused.run_id)
        assert checkpoint is not None
        assert checkpoint.kind == "approval"
        assert checkpoint.approval_id == paused.approval_id
        # the checkpoint payload crossed the process boundary (plain JSON,
        # no live objects) and the resume already CLAIMED it (one-shot)
        assert isinstance(checkpoint.payload, dict)
        assert checkpoint.consumed_at is not None

        # -- the fresh segment completed the SAME run, verified --
        assert resumed.run_id == paused.run_id  # the SAME run, not a revision
        assert resumed.status is RunStatus.SUCCEEDED, resumed
        assert resumed.stop_reason is StopReason.SUCCESS
        assert resumed.detail_code is None
        # budget continuity: turns are CUMULATIVE across the process
        # boundary (2 pre-pause + 1 post-resume), never reset
        assert resumed.usage.turns == case.crash_after_turn + 1
        assert resumed.usage.tool_calls == 2  # the read + the edit, once each
        # the fix landed exactly once
        fixed = (fresh.run_dir(paused.run_id) / "vip.py").read_text()
        assert "return purchases >= 5" in fixed
        # no duplicated side effect across the boundary: the read ran ONCE
        # (segment 1) and the gated edit ONCE (segment 2) — the store holds
        # both segments' events, the resumed ones appended after the pause's.
        rows = store.events[paused.run_id]
        completed = [e.payload["tool_id"] for e in rows if e.event_type == TOOL_EXECUTION_COMPLETED]
        assert completed.count("read_file") == 1
        assert completed.count("edit_file") == 1
        # the restart path in the fresh segment's events: restored ->
        # resumed -> decided (the approval) -> ... -> completed
        types = [e.event_type for e in fresh.events_of(paused.run_id)]
        assert types.index(CHECKPOINT_RESTORED) < types.index(RUN_RESUMED)
        assert types.index(RUN_RESUMED) < types.index(TOOL_APPROVAL_DECIDED)
        decided = next(
            e for e in fresh.events_of(paused.run_id) if e.event_type == TOOL_APPROVAL_DECIDED
        )
        assert decided.payload["approved"] is True
        # the durable row follows the resumed result (the pause's detail is gone)
        row = store.runs[paused.run_id]
        assert row.status == "succeeded"
        assert row.stop_reason == "SUCCESS"
        assert row.detail_code is None
        # the checkpoint is one-shot ACROSS the boundary too
        with pytest.raises(DomainError) as excinfo:
            fresh.resume(paused.run_id, approval_id=paused.approval_id, approve=True)
        assert excinfo.value.code is ErrorCode.CHECKPOINT_CONSUMED
