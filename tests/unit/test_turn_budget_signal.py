"""Turn-budget signal (H-bench multi-config-precedence finding): near the turn
limit the kernel pins a system note telling the model how many turns remain.

Pinned here: the note appears at <= TURN_BUDGET_NOTE_THRESHOLD turns left
(the current turn included) and never before; it binds to whichever ceiling
is tighter (``max_turns`` or the budget ledger); it is a seed message counted
in the context token estimate; and it changes nothing about acceptance —
completion stays verifier-gated (INV-08).
"""

from datetime import UTC, datetime
from typing import Any

from aci.domain.runtime.actions import ContinueAction, FinalCandidate
from aci.domain.runtime.evidence import CheckResult, ResultContract
from aci.domain.runtime.spec import LoopPolicy, RuntimeSpec
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.subtask import SubtaskContract
from aci.runtime.context_engine import ContextBudget, ContextEngine, estimate_tokens
from aci.runtime.event_bus import CONTEXT_ASSEMBLED, EventBus
from aci.runtime.model_gateway import FakeModelGateway, ModelRequest
from aci.runtime.recovery import RecoveryManager
from aci.runtime.run_controller import (
    TURN_BUDGET_NOTE,
    TURN_BUDGET_NOTE_THRESHOLD,
    HarnessKernel,
    turn_budget_note,
)
from aci.runtime.state_manager import StateManager
from aci.runtime.verification import VerificationManager, VerifierCallable

RUN_ID = "run-turn-budget"
MARKER = "Turn budget:"


class _NoTools:
    def execute_batch(self, calls: object, *, snapshot: object, envelope: object) -> list[Any]:
        raise AssertionError("no tools in these runs")

    def available_tools(self) -> list[Any]:
        return []


def _kernel(
    script: list[Any], *, passed: bool = True
) -> tuple[HarnessKernel, FakeModelGateway, EventBus]:
    model = FakeModelGateway(script)
    bus = EventBus()
    kernel = HarnessKernel(
        state=StateManager(),
        model_gateway=model,
        tool_executor=_NoTools(),
        context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=VerificationManager(
            [VerifierCallable("tests", lambda s, c: CheckResult(name="tests", passed=passed))]
        ),
        recovery=RecoveryManager(),
        capability_runtime=object(),  # type: ignore[arg-type]
        event_bus=bus,
        sleep=lambda _s: None,
    )
    return kernel, model, bus


def _contract() -> SubtaskContract:
    return SubtaskContract(task_id=RUN_ID, objective="fix it", created_at=datetime.now(UTC))


def _spec(**budget: Any) -> RuntimeSpec:
    return RuntimeSpec(
        result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
        loop_policy=LoopPolicy(**budget),
        budget=BudgetLedger(**budget),
        created_at=datetime.now(UTC),
    )


def _notes(request: ModelRequest) -> list[str]:
    return [m.content for m in request.messages if MARKER in m.content]


class TestTurnBudgetNoteText:
    def test_threshold_is_two_turns_including_the_current_one(self) -> None:
        assert TURN_BUDGET_NOTE_THRESHOLD == 2
        assert turn_budget_note(3) == ""
        assert turn_budget_note(40) == ""
        assert turn_budget_note(2) == (
            "Turn budget: 2 turn(s) left including this one. If your verification "
            "command already passed after your last change, propose completion now."
        )
        assert turn_budget_note(1) == TURN_BUDGET_NOTE.format(turns_left=1)

    def test_wording_never_bypasses_the_verifier(self) -> None:
        """It nudges the model to PROPOSE (→ verifier) — it never claims
        success or tells the model to skip verification."""
        text = turn_budget_note(1).lower()
        assert "propose completion" in text
        for forbidden in ("skip", "accepted", "succeeded", "without verif"):
            assert forbidden not in text


class TestTurnBudgetNoteInKernel:
    def test_note_appears_only_in_the_last_two_turns(self) -> None:
        kernel, model, _ = _kernel([ContinueAction() for _ in range(10)])
        result = kernel.run(_contract(), _spec(), max_turns=5)
        assert result.stop_reason is StopReason.LIMIT_TURNS
        assert len(model.requests) == 5
        assert [_notes(r) for r in model.requests[:3]] == [[], [], []]
        assert _notes(model.requests[3]) == [turn_budget_note(2)]
        assert _notes(model.requests[4]) == [turn_budget_note(1)]

    def test_note_is_a_system_seed_message_before_the_objective(self) -> None:
        kernel, model, _ = _kernel([ContinueAction() for _ in range(3)])
        kernel.run(_contract(), _spec(), max_turns=3)
        messages = model.requests[-1].messages
        index = next(i for i, m in enumerate(messages) if MARKER in m.content)
        assert messages[index].role == "system"
        objective = next(i for i, m in enumerate(messages) if m.content == "fix it")
        assert index < objective
        assert all(m.role == "system" for m in messages[:index])

    def test_budget_ledger_turn_ceiling_also_triggers_the_note(self) -> None:
        """The binding ceiling may be the ledger's max_turns, not the
        run()'s max_turns argument — the note counts against the tighter."""
        kernel, model, _ = _kernel([ContinueAction() for _ in range(10)])
        result = kernel.run(_contract(), _spec(max_turns=4), max_turns=40)
        assert result.stop_reason is StopReason.LIMIT_TURNS
        assert len(model.requests) == 4
        assert [len(_notes(r)) for r in model.requests] == [0, 0, 1, 1]
        assert _notes(model.requests[-1]) == [turn_budget_note(1)]

    def test_note_is_counted_in_the_context_token_estimate(self) -> None:
        kernel, model, bus = _kernel([ContinueAction() for _ in range(3)])
        kernel.run(_contract(), _spec(), max_turns=3)
        assembled = [e for e in bus.history(RUN_ID) if e.event_type == CONTEXT_ASSEMBLED]
        assert len(assembled) == len(model.requests) == 3
        for event, request in zip(assembled, model.requests, strict=True):
            assert event.payload["dropped_turns"] == 0
            assert event.payload["total_tokens"] == sum(
                estimate_tokens(m.content) for m in request.messages
            )
        # The last turn carries the note; its estimate includes it.
        assert _notes(model.requests[-1])

    def test_note_is_deterministic_across_identical_runs(self) -> None:
        texts = []
        for _ in range(2):
            kernel, model, _ = _kernel([ContinueAction() for _ in range(4)])
            kernel.run(_contract(), _spec(), max_turns=4)
            texts.append([_notes(r) for r in model.requests])
        assert texts[0] == texts[1]

    def test_completion_stays_verifier_gated_under_the_note(self) -> None:
        """INV-08: proposing completion on the noted final turn with a
        failing verifier is NOT a success."""
        kernel, model, _ = _kernel([ContinueAction(), FinalCandidate(summary="done")], passed=False)
        result = kernel.run(_contract(), _spec(), max_turns=2)
        assert _notes(model.requests[-1]) == [turn_budget_note(1)]
        assert result.status is not RunStatus.SUCCEEDED
