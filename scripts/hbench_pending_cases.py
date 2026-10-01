"""H-bench PENDING-case fixtures — the dedicated instruments for H005/H006/H007/H011.

``scripts/run_hbench.py`` declares these Appendix D cases PENDING
(``PENDING_H_CASES``): they need DEDICATED fixtures exercising KERNEL
mechanisms the §80 debugging fixtures never touch. The kernel features now
exist (ADR-014 amendments 13/14: approval pause + durable resume,
tool-failure recovery, the context budget), so this module is the instrument
for them — one fixture spec per case, each documented with what PASS means in
KERNEL terms (events / stop reasons / budget ledgers), mapped to its H-id.
H008 (false completion) stays implicit on every fixture through the
false-success metric; it needs no fixture of its own.

The cases (Appendix D of docs/plans/harness.md; the pack descriptions live in
``src/aci/evaluation/harness_cases.py``)::

    H005 oversized tool output    -> h005-context-flood          (context)
    H006 permission denied        -> h006-approval-gate          (authority)
    H007 tool transient failure   -> h007-transient-read-failure (recovery)
    H011 checkpoint crash/resume  -> h011-crash-resume           (durability)

What a fixture is
-----------------
The same shape the H-bench runner materializes for the §80 pack
(``name`` / ``files`` / ``prompt``) plus the knobs the case's mechanism needs:

``context_budget_tokens`` (H005)
    The run's ContextEngine budget. The workspace ships ~126KB of generated
    logs; every ``read_file`` returns ~48KB numbered, which INV-10 caps at the
    12'000-char inline budget (head + truncation marker + tail). The task
    needs only the NEWEST read (each file's FINAL marker lives in its tail,
    visible through the truncation), so it stays answerable when the older
    read groups are dropped: a 4'000-token budget forces the kernel to DROP
    whole earlier turn groups (INV-09) while the run still completes green —
    and a model that floods all three files passes too, because the drop
    keeps the peak within budget either way.
``approval_required_tools`` (H006/H011)
    The §13.6 overlay: the fix needs ``edit_file``, whose calls pause the run
    BEFORE the gated effect executes.
``transient_read_failures`` (H007)
    How many read dispatches raise a transient infrastructure error before
    one succeeds — ``TransientReadFailureInjector`` below.
``crash_after_turn`` (H011)
    The turn the run is PAUSED at when the process dies. Honest scope: the
    kernel's periodic in-run checkpoints are RAM-only by design (ADR-014
    amendment 14 — only PAUSE checkpoints are durable), so a pause checkpoint
    is the one crash a restart can actually resume from; the fixture's crash
    lands there (a worker dying while it waits for a human).

PASS, in kernel terms (what tests/unit/test_hbench_pending_cases.py asserts —
never model terms)
-----------------------------------------------------------------------
H005
    Run SUCCEEDED (verifier-gated); EVERY ``context.assembled`` event's
    ``total_tokens`` <= the case budget; >= 1 event with ``dropped_turns``
    >= 1 (the flood turns were dropped and RECORDED); the tool result the
    model saw was INV-10-bounded (truncation marker, ~12KB inline) — the
    126KB never reached the model whole.
H006
    The run pauses BEFORE the gated effect: status ``interrupted_approval``,
    stop ``AWAITING_APPROVAL``, detail ``APPROVAL_REQUIRED``, ``approval_id``
    ``apr_*``; ``tool.approval.requested`` + ``checkpoint.saved`` (reason
    approval) events; the target file still buggy at the pause.
    ``resume(approve)`` -> ``checkpoint.restored`` + ``run.resumed`` +
    ``tool.approval.decided(approved)`` -> the edit executed EXACTLY once ->
    SUCCEEDED with the verification command passing. Deny:
    ``resume(approve=False)`` -> an ``APPROVAL_REJECTED`` observation, the
    file NEVER modified, and the run can never report SUCCEEDED without the
    effect (INV-08).
H007
    SUCCEEDED; the read dispatched ``failures + 1`` times (the injector's
    counter proves the retry); ``recovery.action`` events ``RETRY_SAME`` xN
    with component ``tool_runtime`` / failure class ``TRANSIENT_TOOL`` /
    attempts 1..N (bounded: N <= the per-class retry bound 2);
    ``tool.execution.failed`` xN (INV-15 — every failed attempt is
    telemetry); the model saw ONE successful result for its one call.
H011
    Segment 1 pauses at ``crash_after_turn`` (``checkpoint.saved`` reason
    approval; run row + checkpoint DURABLE in the store, the checkpoint
    payload JSON-round-tripped — the process boundary). The fresh segment (a
    NEW service over the same store, NO RAM) resumes: ``checkpoint.restored``
    + ``run.resumed``; the pending edit executes EXACTLY once and the
    pre-pause read is NOT re-executed (no duplicated side effect); budget
    continuity — ``usage.turns`` is CUMULATIVE across the boundary
    (pre-pause turns + post-resume turns, never reset); SUCCEEDED with the
    verification command passing.

Determinism
-----------
Everything here is offline and deterministic: the workspaces are generated
text, the "model" is a script (``FakeModelGateway``), processes run through
``NoSandbox`` (the explicit opt-out — these are scripted tests, never
model-written code; the real runner sandboxes with bwrap), and the only
double is the run store (``MemoryRunStore`` — an in-memory AgentRunStore
whose checkpoint payloads make a REAL JSON round-trip, i.e. what a dead
process leaves behind). No network, no DB, no timing dependence beyond the
kernel's own retry backoff (H007 sleeps 1s + 2s of real ``time.sleep``
through the service; the backoff curve itself is unit-pinned in
test_tool_recovery.py).

Wiring (the later integration step — NOT applied here)
-------------------------------------------------------
``run_hbench.py`` would import this module and extend its fixture pack with
``pending_fixtures()``; the per-case knobs reach the service through the
runner's own seams (the ``_kernel_service`` context budget, per-run
``approval_required_tools``, a dispatcher patch for the injector, and a
two-service driver for H011). The exact minimal diff is recorded in this
job's result file; this module must never import ``run_hbench`` (the runner
is edited on another machine).
"""

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aci.application.protocols import AgentRunStore
from aci.application.run_agent_task import AgentRunService, new_run_id
from aci.domain.runtime.actions import FinalCandidate, ToolCallBatchAction
from aci.domain.runtime.authority import ExecutionEnvelope
from aci.domain.runtime.persistence import (
    AgentRunCheckpointRecord,
    AgentRunEventRecord,
    AgentRunRecord,
)
from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.subtask import AcceptanceCriterion, RunResult, SubtaskContract
from aci.domain.runtime.tools import ToolCall, ToolSpec
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.event_bus import EventBus, EventEnvelope
from aci.runtime.model_gateway import FakeModelGateway
from aci.runtime.profiles import runtime_spec_for
from aci.runtime.protocols import ToolDispatchResult
from aci.runtime.sandbox import NoSandbox
from aci.runtime.workspace_tools import WorkspaceToolDispatcher

#: The verification command — IDENTICAL to run_hbench.VERIFICATION (the same
#: yardstick the §80 fixtures verify under, and the same acceptance-tied
#: command the kernel's command-evidence checks count). Runs in the run's
#: working copy; the fixture workspaces ship their own ``test_*.py``.
VERIFICATION: list[str] = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]

#: The process ceiling (INV-02) — the interpreter, like run_hbench's
#: ``PROCESS_PREFIXES`` but minimal (the scripted models never run commands).
PROCESS_PREFIXES: list[str] = [sys.executable]

#: The turn ceiling the runner's ``--max-turns`` default uses.
MAX_TURNS = 12


# ---------------------------------------------------------------------------
# The fixture spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingCase:
    """One PENDING H-case fixture: workspace files + task prompt + the
    verification the workspace ships + the harness knobs the case's
    mechanism needs. ``pass_criteria`` is documentation-as-data: what PASS
    means in KERNEL terms (the unit tests assert exactly it)."""

    case_id: str
    name: str
    files: dict[str, str]
    prompt: str
    pass_criteria: tuple[str, ...]
    #: H005 — the run's ContextEngine budget (None = the 60k default).
    context_budget_tokens: int | None = None
    #: H006/H011 — the §13.6 overlay: tool ids whose calls pause the run.
    approval_required_tools: tuple[str, ...] = ()
    #: H007 — transient read dispatch failures before one succeeds.
    transient_read_failures: int = 0
    #: H011 — the turn the run is PAUSED at when the process dies.
    crash_after_turn: int | None = None
    #: The root-cause fix a correct model applies (``edit_file`` old, new) —
    #: None when the task's deliverable is ``answer.txt`` instead.
    fix: tuple[str, str] | None = None
    #: What ``answer.txt`` must hold (the answer-file cases); None on fixes.
    answer: str | None = None

    def as_fixture(self) -> dict[str, Any]:
        """The run_hbench-compatible fixture dict: ``name``/``files``/``prompt``
        materialize exactly like a §80 fixture entry, ``h_ref`` is the
        Appendix D case, and the knob fields are what the wiring reads."""
        return {
            "name": self.name,
            "files": dict(self.files),
            "prompt": self.prompt,
            "case_id": self.case_id,
            "h_ref": self.case_id,
            "context_budget_tokens": self.context_budget_tokens,
            "approval_required_tools": list(self.approval_required_tools),
            "transient_read_failures": self.transient_read_failures,
            "crash_after_turn": self.crash_after_turn,
        }


# ---------------------------------------------------------------------------
# Deterministic workspace generators
# ---------------------------------------------------------------------------


#: Each log's last line carries a FINAL marker — the value lives in the
#: read's VISIBLE tail (INV-10 keeps head+tail), so the task is answerable
#: by a real model that reads the file, not only by a scripted one.
_H005_MARKERS = {"a": "alpha-4f9", "b": "bravo-2c7", "c": "charlie-9d2"}


def _log_block(tag: str, lines: int = 800) -> str:
    """Deterministic service-log text (~42KB): every 7th line is an ERROR line
    and the LAST line carries the file's FINAL marker.

    ``read_file`` numbers every line (~48KB), which INV-10 caps at the
    12'000-char inline budget (head + truncation marker + tail) — the
    oversized-output flood H005 measures. The marker is in the tail, so it
    stays visible through the truncation; the fixture's own test recomputes
    it from the file, so the expected answer can never drift.
    """
    rows = [
        f"2026-10-02T10:00:{i % 60:02d}Z svc-{tag} "
        f"{'ERROR' if i % 7 == 0 else 'INFO'} event-{i:04d} payload-ok"
        for i in range(lines)
    ]
    rows.append(f"2026-10-02T11:00:00Z svc-{tag} FINAL marker {_H005_MARKERS[tag]}")
    return "\n".join(rows) + "\n"


_H005_TEST = '''from pathlib import Path


def test_answer_carries_the_final_marker() -> None:
    """answer.txt must hold biglog-c.txt's FINAL marker value."""
    final_line = next(
        line
        for line in reversed(Path("biglog-c.txt").read_text(encoding="utf-8").splitlines())
        if "FINAL marker " in line
    )
    marker = final_line.split("FINAL marker ", 1)[1].strip()
    assert Path("answer.txt").read_text(encoding="utf-8").strip() == marker
'''

_H005_FILES = {
    "biglog-a.txt": _log_block("a"),
    "biglog-b.txt": _log_block("b"),
    "biglog-c.txt": _log_block("c"),
    "test_answer.py": _H005_TEST,
}

_H006_COUNTER = '''"""Score helpers — shipped with a bug."""


def add_bonus(points: list[int], bonus: int = 10) -> list[int]:
    """Return a NEW list with the bonus added to every score."""
    points += [bonus]
    return points
'''

_H006_TEST = '''from counter import add_bonus


def test_add_bonus_returns_a_new_list() -> None:
    """add_bonus must never mutate the caller's list."""
    scores = [1, 2, 3]
    result = add_bonus(scores)
    assert scores == [1, 2, 3], "the caller's list was mutated"
    assert result == [11, 12, 13]
'''

_H007_NOTES = "the secret word is pinecone\n"

_H007_TEST = '''from pathlib import Path


def test_answer_carries_the_secret_word() -> None:
    """answer.txt must hold the secret word named in notes.txt."""
    assert Path("answer.txt").read_text(encoding="utf-8").strip() == "pinecone"
'''

_H011_VIP = '''"""Customer tiers — shipped with a bug."""


def is_vip(purchases: int) -> bool:
    """A customer with 5 or more purchases is a VIP."""
    return purchases > 5
'''

_H011_TEST = '''from vip import is_vip


def test_five_purchases_qualify() -> None:
    """The boundary is inclusive: 5 purchases already qualify."""
    assert is_vip(5) is True
    assert is_vip(4) is False
    assert is_vip(6) is True
'''


# ---------------------------------------------------------------------------
# The four cases
# ---------------------------------------------------------------------------

H005 = PendingCase(
    case_id="H005",
    name="h005-context-flood",
    files=_H005_FILES,
    prompt=(
        "The biglog-*.txt files hold service logs and each ends with a FINAL "
        "marker line. Read biglog-c.txt and write its FINAL marker value to "
        "answer.txt (just the value)."
    ),
    pass_criteria=(
        "run SUCCEEDED (verifier-gated)",
        "every context.assembled total_tokens <= the case budget",
        ">= 1 context.assembled with dropped_turns >= 1 (the drop is recorded)",
        "each read observation INV-10-bounded (truncation marker, ~12KB inline)",
    ),
    context_budget_tokens=4_000,
    # The task needs only the NEWEST read (biglog-c's marker is in its tail),
    # so it stays answerable when the older read groups are dropped — a real
    # model that reads only c passes; one that floods all three files passes
    # too, because the drop keeps the peak within budget either way.
    answer=_H005_MARKERS["c"],
)

H006 = PendingCase(
    case_id="H006",
    name="h006-approval-gate",
    files={"counter.py": _H006_COUNTER, "test_counter.py": _H006_TEST},
    prompt=(
        "The test test_add_bonus_returns_a_new_list in test_counter.py fails. "
        "Find the root cause, then fix counter.py and make the whole test "
        "suite green."
    ),
    pass_criteria=(
        "pause BEFORE the gated effect: interrupted_approval / AWAITING_APPROVAL",
        "tool.approval.requested + checkpoint.saved events; the file unmodified",
        "resume(approve) executes the edit EXACTLY once -> SUCCEEDED, verified",
        "deny: APPROVAL_REJECTED observation, no write, never SUCCEEDED (INV-08)",
    ),
    approval_required_tools=("edit_file",),
    fix=("    points += [bonus]\n    return points", "    return [p + bonus for p in points]"),
)

H007 = PendingCase(
    case_id="H007",
    name="h007-transient-read-failure",
    files={"notes.txt": _H007_NOTES, "test_answer.py": _H007_TEST},
    prompt="Read notes.txt and write the secret word it names to answer.txt (just the word).",
    pass_criteria=(
        "run SUCCEEDED (verifier-gated)",
        "the read dispatched failures+1 times (the injector counter proves the retry)",
        "recovery.action RETRY_SAME xN (tool_runtime / TRANSIENT_TOOL / attempts 1..N)",
        "tool.execution.failed xN (INV-15); the model saw ONE successful result",
    ),
    transient_read_failures=2,
    answer="pinecone",
)

H011 = PendingCase(
    case_id="H011",
    name="h011-crash-resume",
    files={"vip.py": _H011_VIP, "test_vip.py": _H011_TEST},
    prompt=(
        "The test test_five_purchases_qualify in test_vip.py fails. Find the "
        "root cause, then fix vip.py and make the whole test suite green."
    ),
    pass_criteria=(
        "segment 1 pauses at crash_after_turn; checkpoint durable (JSON boundary)",
        "the FRESH service (no RAM) resumes: checkpoint.restored + run.resumed",
        "the pending edit executes EXACTLY once; the pre-pause read is NOT re-run",
        "budget continuity: usage.turns cumulative across the boundary; SUCCEEDED",
    ),
    approval_required_tools=("edit_file",),
    crash_after_turn=2,
    fix=("    return purchases > 5", "    return purchases >= 5"),
)

PENDING_CASES: list[PendingCase] = [H005, H006, H007, H011]


def case_by_id(case_id: str) -> PendingCase:
    """The case with this H-id (KeyError on a typo — fail loudly, never None)."""
    for case in PENDING_CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(
        f"unknown pending H-case: {case_id!r} (have {[c.case_id for c in PENDING_CASES]})"
    )


def pending_fixtures() -> list[dict[str, Any]]:
    """The runner-compatible fixture pack: one dict per PENDING case, the same
    materialization shape as the §80 ``MULTI_TASKS``/``LONG_TASKS`` entries."""
    return [case.as_fixture() for case in PENDING_CASES]


def materialize(case: PendingCase, sources_root: Path) -> Path:
    """Write the case's workspace files under ``sources_root/<name>/`` — the
    same materialization run_hbench.main performs for its fixture pack."""
    target = sources_root / case.name
    target.mkdir(parents=True, exist_ok=True)
    for rel, content in case.files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Scripted model actions (the deterministic "model")
# ---------------------------------------------------------------------------


def read_call(call_id: str, path: str) -> ToolCallBatchAction:
    """Scripted model action: one ``read_file`` call."""
    return ToolCallBatchAction(
        calls=[ToolCall(call_id=call_id, tool_id="read_file", arguments={"path": path})]
    )


def write_call(call_id: str, path: str, content: str) -> ToolCallBatchAction:
    """Scripted model action: one ``write_file`` call."""
    return ToolCallBatchAction(
        calls=[
            ToolCall(
                call_id=call_id, tool_id="write_file", arguments={"path": path, "content": content}
            )
        ]
    )


def edit_call(call_id: str, path: str, old_string: str, new_string: str) -> ToolCallBatchAction:
    """Scripted model action: one ``edit_file`` call (the root-cause fix)."""
    return ToolCallBatchAction(
        calls=[
            ToolCall(
                call_id=call_id,
                tool_id="edit_file",
                arguments={
                    "path": path,
                    "old_string": old_string,
                    "new_string": new_string,
                },
            )
        ]
    )


def final(summary: str, **fields: Any) -> FinalCandidate:
    """Scripted model action: propose completion (the verifier gates it)."""
    return FinalCandidate(summary=summary, **fields)


# ---------------------------------------------------------------------------
# H007 — the deterministic transient-failure injector for read tools
# ---------------------------------------------------------------------------


class TransientReadFailureInjector:
    """H007 knob: deterministic transient failures on READ tools.

    ``dispatcher_class()`` returns a ``WorkspaceToolDispatcher`` subclass
    whose ``dispatch`` raises ``ConnectionResetError`` (a §18.1
    ``TRANSIENT_DISPATCH_ERRORS`` class) for the first ``failures``
    dispatches of each listed read tool — BEFORE the handler runs, so the
    failure surfaces as ``TRANSIENT_TOOL`` (the real dispatcher's own OSError
    handling would otherwise normalize it to a deterministic
    ``TOOL_EXECUTION_FAILED``). The kernel's RecoveryManager then retries the
    retry-safe read (READ_ONLY + IDEMPOTENT) with backoff until one succeeds.

    Wiring — the injection seam: the service builds the real workspace tool
    runtime internally (``build_workspace_tool_runtime``), so the DISPATCHER
    is what a driver can patch. Patch
    ``aci.runtime.workspace_tools.WorkspaceToolDispatcher`` with the
    returned class for the duration of the run (pytest ``monkeypatch`` in the
    unit tests; the runner wiring does the same). ``attempts`` counts every
    dispatch of a listed tool — failures and successes: the retry proof is
    ``attempts[tool] == failures + 1``.
    """

    def __init__(self, *, tool_ids: tuple[str, ...] = ("read_file",), failures: int) -> None:
        if failures < 1:
            raise ValueError("an injector needs at least one scripted failure")
        self.tool_ids = frozenset(tool_ids)
        self.failures = failures
        self.attempts: dict[str, int] = {}

    def _fail_this_one(self, tool_id: str) -> bool:
        """Count one dispatch of a listed tool; True when it should fail
        (the first ``failures``). Unlisted tools are never counted."""
        if tool_id not in self.tool_ids:
            return False
        seen = self.attempts.get(tool_id, 0) + 1
        self.attempts[tool_id] = seen
        return seen <= self.failures

    def dispatcher_class(self) -> type[WorkspaceToolDispatcher]:
        """The patched dispatcher class: the real one, minus its first N
        read dispatches (they raise the scripted transient error instead)."""
        injector = self

        class _FlakyReadDispatcher(WorkspaceToolDispatcher):
            """The real workspace dispatcher with the injector in front."""

            def dispatch(
                self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
            ) -> ToolDispatchResult:
                if injector._fail_this_one(tool.tool_id):
                    raise ConnectionResetError(
                        f"injected transient failure ({injector.failures} scripted)"
                    )
                return super().dispatch(tool, args, envelope)

        return _FlakyReadDispatcher


# ---------------------------------------------------------------------------
# H011 — the durable run store double (the process boundary)
# ---------------------------------------------------------------------------


class MemoryRunStore:
    """In-memory AgentRunStore double for the scripted drivers (the 0016 run
    row + events methods and the 0019 checkpoint methods).

    ``record_checkpoint`` makes the payload cross a REAL JSON boundary
    (``json.dumps`` -> ``json.loads``): what a resume validates is exactly
    what a dead process leaves in a durable store — datetimes as ISO
    strings, no live objects (INV-13). ``append_events`` re-bases ``seq``
    past the stored rows (the protocol's append-after contract) so a resumed
    segment's events never collide with the pre-pause ones.
    """

    def __init__(self) -> None:
        self.runs: dict[str, AgentRunRecord] = {}
        self.events: dict[str, list[AgentRunEventRecord]] = {}
        self.checkpoints: dict[str, AgentRunCheckpointRecord] = {}

    def record_run(self, record: AgentRunRecord) -> None:
        self.runs[record.run_id] = record

    def record_events(self, events: list[AgentRunEventRecord]) -> None:
        for event in events:
            self.events.setdefault(event.run_id, []).append(event)

    def append_events(self, events: list[AgentRunEventRecord]) -> None:
        for event in events:
            stored = self.events.setdefault(event.run_id, [])
            stored.append(event.model_copy(update={"seq": len(stored) + event.seq}))

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    def list_recent(self, limit: int = 50) -> list[AgentRunRecord]:
        return list(self.runs.values())[:limit]

    def record_checkpoint(self, record: AgentRunCheckpointRecord) -> None:
        if record.run_id not in self.runs:
            raise AssertionError("checkpoint recorded before its run row (the store's FK order)")
        payload = json.loads(json.dumps(record.payload))  # the process boundary
        self.checkpoints[record.checkpoint_id] = record.model_copy(update={"payload": payload})

    def latest_checkpoint(self, run_id: str) -> AgentRunCheckpointRecord | None:
        mine = [c for c in self.checkpoints.values() if c.run_id == run_id]
        return max(mine, key=lambda c: c.created_at) if mine else None

    def consume_checkpoint(self, checkpoint_id: str, *, at: datetime) -> bool:
        record = self.checkpoints.get(checkpoint_id)
        if record is None or record.consumed_at is not None:
            return False
        self.checkpoints[checkpoint_id] = record.model_copy(update={"consumed_at": at})
        return True


# ---------------------------------------------------------------------------
# The driver — one scripted run through the REAL AgentRunService
# ---------------------------------------------------------------------------


class _Factory:
    """ModelGatewayFactory returning one fixed collaborator (deterministic)."""

    def __init__(self, obj: object) -> None:
        self._obj = obj

    def build(self) -> object:
        return self._obj


class _NullCapabilities:
    """No registry plane (arm K's null handler): ``advertised = False`` so the
    model is not offered ``request_capability`` at all."""

    advertised = False

    def handle_request(self, request: object, snapshot: object) -> list[object]:
        return []


def contract_spec(
    case: PendingCase, *, budget: BudgetLedger | None = None
) -> tuple[SubtaskContract, RuntimeSpec]:
    """The SAME contract + spec shape run_hbench._contract_spec builds for a
    fixture (§42 fairness: the objective from the fixture, "the whole test
    suite passes" acceptance, the coder profile) — plus an optional budget
    override (the deny-path test pins ``max_recoveries=0``)."""
    contract = SubtaskContract(
        task_id=new_run_id(),
        objective=case.prompt,
        global_context="",
        constraints=[],
        acceptance_criteria=[
            AcceptanceCriterion(criterion_id="ac-1", description="the whole test suite passes")
        ],
        requested_profile="coder",
        budget=None,
        created_at=datetime.now(UTC),
    )
    return contract, runtime_spec_for("coder", budget=budget)


class ScriptedCaseRun:
    """One scripted run of a PENDING case through the REAL AgentRunService —
    the same service composition run_hbench's arm K builds (real workspace
    tool runtime + guardrails + per-run working copy + the case's context
    budget). The ONLY doubles are the model gateway (a script) and, where a
    case needs one, the store (``MemoryRunStore``).

    Events are collected through a bus SINK: the service frees the bus's own
    history once a segment stops (§41.1), so a sink is the only way a test
    still sees them afterwards. ``run_dir`` is the run's working copy
    (provisioned from ``sources/<case.name>/``).
    """

    def __init__(
        self,
        case: PendingCase,
        script: list[Any],
        tmp_path: Path,
        *,
        store: AgentRunStore | None = None,
        spec_budget: BudgetLedger | None = None,
    ) -> None:
        self.case = case
        self.tmp_path = tmp_path
        self.spec_budget = spec_budget
        materialize(case, tmp_path / "sources")
        self.bus = EventBus()
        self.events: list[EventEnvelope] = []
        self.bus.subscribe(self.events.append)
        self.gateway = FakeModelGateway(list(script))
        self.service = AgentRunService(
            model_gateway_factory=_Factory(self.gateway),
            tool_executor_factory=_Factory(_NullCapabilities()),
            capability_runtime_factory=_Factory(_NullCapabilities()),
            context_engine_factory=_Factory(
                ContextEngine(ContextBudget(total_tokens=case.context_budget_tokens or 60_000))
            ),
            workspace_root=tmp_path / "sources",
            runs_root=tmp_path / "runs",
            process_prefixes=PROCESS_PREFIXES,
            event_bus=self.bus,
            run_store=store,
            # The explicit opt-out: these are SCRIPTED tests, never
            # model-written code. The real runner sandboxes with bwrap
            # (fail closed); NoSandbox keeps these deterministic and
            # CI-green where bubblewrap cannot run (macOS).
            process_sandbox=NoSandbox(),
        )

    @property
    def runs_root(self) -> Path:
        return self.tmp_path / "runs"

    def run_dir(self, run_id: str) -> Path:
        """The run's working copy (``runs_root/<run_id>``, §16.2)."""
        return self.runs_root / run_id

    def run(self, *, max_turns: int = MAX_TURNS, **options: Any) -> RunResult:
        """Start the run: the contract/spec shape of run_hbench._contract_spec
        plus the case's own knobs (workspace, verification command, the §13.6
        approval overlay)."""
        contract, spec = contract_spec(self.case, budget=self.spec_budget)
        return self.service.run(
            contract,
            spec,
            max_turns=max_turns,
            workspace=self.case.name,
            verification_command=list(VERIFICATION),
            approval_required_tools=list(self.case.approval_required_tools) or None,
            **options,
        )

    def resume(self, run_id: str, **options: Any) -> RunResult:
        """Continue a run this service paused (§17.4 — at most once)."""
        return self.service.resume(run_id, **options)

    def events_of(self, run_id: str) -> list[EventEnvelope]:
        """This driver's captured events for one run (both segments of a
        pause/resume share the run id)."""
        return [e for e in self.events if e.run_id == run_id]


def crash_and_resume(
    case: PendingCase,
    tmp_path: Path,
    *,
    script_before_pause: list[Any],
    script_after_restart: list[Any],
    store: MemoryRunStore,
    approve: bool = True,
) -> tuple[RunResult, RunResult, "ScriptedCaseRun"]:
    """H011 — the crash-after-turn-N + resume driver.

    Segment 1 runs the case until the approval pause at ``crash_after_turn``
    (the crash point). The "crash" is process death WHILE PAUSED — the one
    durable crash the kernel has: periodic in-run checkpoints are RAM-only by
    design (ADR-014 amendment 14), a pause checkpoint is the only thing a
    restart can resume from, and a pause is exactly where a real worker dies
    waiting for a human. Segment 1's service (and ALL its RAM state —
    StateManager, checkpoint, results) is then dropped; only ``store``
    survives, and the checkpoint payload inside it crossed a real JSON
    boundary (``MemoryRunStore``).

    The fresh segment — a NEW service over the same store, empty RAM (the
    restart case) — resumes the SAME run id with the approval decision.
    Returns ``(paused, resumed, fresh_driver)``.
    """
    first = ScriptedCaseRun(case, script_before_pause, tmp_path, store=store)
    paused = first.run()
    assert paused.status.value == "interrupted_approval", paused
    if case.crash_after_turn is not None:
        assert paused.usage.turns == case.crash_after_turn, (
            f"{case.case_id}: the crash point is turn {case.crash_after_turn}, "
            f"the run paused at turn {paused.usage.turns}"
        )
    # Process death: nothing of segment 1 exists past this line but the store.
    del first
    fresh = ScriptedCaseRun(case, script_after_restart, tmp_path, store=store)
    resumed = fresh.resume(paused.run_id, approval_id=paused.approval_id, approve=approve)
    return paused, resumed, fresh


__all__ = [
    "MAX_TURNS",
    "PROCESS_PREFIXES",
    "PENDING_CASES",
    "VERIFICATION",
    "MemoryRunStore",
    "PendingCase",
    "ScriptedCaseRun",
    "TransientReadFailureInjector",
    "case_by_id",
    "contract_spec",
    "crash_and_resume",
    "edit_call",
    "final",
    "materialize",
    "pending_fixtures",
    "read_call",
    "write_call",
]
