"""H-bench runner — the ADR-014 benchmark arena for harness mechanism changes.

Harness quality is measured SEPARATELY from model quality (harness.md §42):
same model, same fixtures, same tools, same budget, same SYSTEM PROMPT —
different harness mechanism. This runner executes the pack and records §44
metrics.

Arms (the A/B the §80 verdict asked for — "does the harness change
outcomes?", not "does the model?"):

  K — HarnessKernel: the real AgentRunService — verifier-GATED completion
      (INV-08: a claim never self-succeeds), authority preflight, guardrails,
      recovery, context engine.
  N — naive loop: the SAME model + the SAME workspace tools + the SAME
      system prompt (the kernel's own `_system_prompt` over the same
      contract/spec/grants) through a plain ReAct loop — no verification
      gate (the model's "done" IS the result; acceptance is verified
      POST-HOC), no recovery, append-only context.

The delta K−N is what the kernel adds. The headline metric is FALSE
SUCCESS (§44): N reports "done" the verifier refutes; K can only report
`succeeded` when the verification command actually passed. K additionally
reports its verification rounds / fails and repair turns (from the event
bus), so the mechanism's cost is visible next to its gain.

Each case runs `--repeat` times (default 3), in parallel; the aggregate
carries mean ± std. §34 caveat applies to everything here: small n, one
model, author-built fixtures — directional only.

Fixtures: the 8 verified §80 multi/long fixtures (each fails as shipped,
passes after the root-cause fix — `verify_multi_fixtures.py`), labeled with
the Appendix D case each most directly exercises. H-cases needing
DEDICATED fixtures (H005 context flood, H006 approval, H007 injected
transient tool failure, H011 crash/resume) are PENDING — extend FIXTURES
when those land; H008 (false completion) is measured implicitly on every
fixture through the false-success metric.

Usage:
    ACI_AGENT_MODEL_API_KEY=... .venv/bin/python scripts/run_hbench.py \
        [--base-url http://localhost:20128/v1] [--model OneNexus/glm-5.3] \
        [--cases multi-config-precedence,...] [--arms K,N] \
        [--repeat 3] [--parallel 4] [--max-turns 12]

Writes a JSON report to data/hbench/ (gitignored) and prints the table.
"""

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "src"))

from proof_loop import LONG_TASKS, MULTI_TASKS  # noqa: E402

from aci.application.run_agent_task import (  # noqa: E402
    AgentRunService,
    new_run_id,
)
from aci.domain.runtime.actions import FinalCandidate, ToolCallBatchAction  # noqa: E402
from aci.domain.runtime.authority import (  # noqa: E402
    ExecutionEnvelope,
    FilesystemScope,
    GrantEnvelope,
    ProcessScope,
)
from aci.domain.runtime.events import EventEnvelope  # noqa: E402
from aci.domain.runtime.state import BudgetLedger  # noqa: E402
from aci.domain.runtime.subtask import (  # noqa: E402
    AcceptanceCriterion,
    SubtaskContract,
)
from aci.runtime.context_engine import AssembledContext, ContextBudget, ContextEngine  # noqa: E402
from aci.runtime.event_bus import (  # noqa: E402
    RECOVERY_ACTION,
    VERIFICATION_COMPLETED,
    VERIFICATION_STARTED,
    EventBus,
)
from aci.runtime.model_gateway import (  # noqa: E402
    ModelMessage,
    ModelRequest,
    OpenAICompatGateway,
)
from aci.runtime.profiles import runtime_spec_for  # noqa: E402
from aci.runtime.run_controller import (  # noqa: E402
    _system_prompt,
    task_state_from,
    turn_budget_note,
)
from aci.runtime.state_manager import StateManager  # noqa: E402
from aci.runtime.workspace import WorkspaceManager  # noqa: E402
from aci.runtime.workspace_tools import (  # noqa: E402
    WorkspaceToolDispatcher,
    provision_workspace,
    standard_tool_specs,
)

REPORT_ROOT = REPO_ROOT / "data" / "hbench"
VERIFICATION = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
#: Deployment process ceiling (INV-02) — IDENTICAL in both arms so the A/B
#: measures harness mechanisms, not sandboxing. sys.executable covers the
#: verification command; the literal "python -m pytest" forms are what
#: models naturally type (the §80 proof loop used the same ceiling).
PROCESS_PREFIXES = [sys.executable, "python -m pytest", "python3 -m pytest"]

#: fixture name → (Appendix D case it most directly exercises)
H_REFS: dict[str, str] = {
    "multi-config-precedence": "H003",  # obvious shared-state root cause
    "multi-cache-invalidation": "H004",  # hidden dependency two layers down
    "multi-pipeline-ordering": "H018",  # seam bug: reproduce before claiming
    "multi-error-translation": "H002",  # cross-tier refactor
    "multi-event-aliasing": "H002",  # cross-module aliasing refactor
    "long-order-pipeline": "H013",  # 4-6 module mini-app, context pressure
    "long-auth-session": "H013",
    "long-notify-fanout": "H013",
}
PENDING_H_CASES = ["H005", "H006", "H007", "H008*", "H011"]  # *implicit via false-success


def _all_fixtures() -> list[dict[str, Any]]:
    return [*MULTI_TASKS, *LONG_TASKS]


def _post_hoc(run_dir: Path) -> int:
    """Run the verification command in a run dir AFTER the run — the SAME
    yardstick for both arms (§44 outcome), independent of the arm's own
    completion semantics."""
    import subprocess

    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",  # the §80 bytecode-cache trap
    }
    try:
        proc = subprocess.run(VERIFICATION, cwd=run_dir, env=env, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        return 124
    return int(proc.returncode)


def _contract_spec(fixture: dict[str, Any]) -> tuple[SubtaskContract, Any]:
    """The SAME contract + spec both arms run under (§42 fairness: the
    objective, acceptance criteria and profile are held constant; only the
    harness mechanism differs)."""
    contract = SubtaskContract(
        task_id=new_run_id(),
        objective=fixture["prompt"],
        global_context="",
        constraints=[],
        acceptance_criteria=[
            AcceptanceCriterion(criterion_id="ac-1", description="the whole test suite passes")
        ],
        requested_profile="coder",
        budget=None,
        created_at=datetime.now(UTC),
    )
    return contract, runtime_spec_for("coder")


def _grants() -> GrantEnvelope:
    return GrantEnvelope(
        filesystem=FilesystemScope(read=["."], write=["."]),
        process=ProcessScope(allowed_prefixes=PROCESS_PREFIXES),
    )


# ---------------------------------------------------------------------------
# Arm K — the real HarnessKernel, in-process (the same service the REST
# wiring builds; no HTTP, no DB — the kernel is sync and self-contained).
# ---------------------------------------------------------------------------


class _Factory:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def build(self) -> object:
        return self._obj


class _NullHandler:
    def handle_request(self, request: object, snapshot: object) -> list[object]:
        return []


class _NoItemsContextEngine(ContextEngine):
    """Ablation `items`: ContextEngine items OFF — build() contributes
    nothing (the objective/authority still reach the model via
    _system_prompt; what disappears is the task item's acceptance line,
    plan items and every registered context item)."""

    def build(  # type: ignore[override]
        self, snapshot: Any, *, turn: int
    ) -> AssembledContext:
        return AssembledContext(items=[], dropped_item_ids=[])


ABLATIONS = ("items", "progress", "continue")


def apply_ablations(ablations: list[str]) -> None:
    """Process-wide mechanism switches for the ablation arms (one invocation
    = one ablation set; the report records which were active). `items` is
    handled by the context factory in _kernel_service; `progress` and
    `continue` patch the RunController module (the measurement script IS
    the experiment harness — these are experiment switches, not product
    code)."""
    import aci.runtime.run_controller as rc

    _orig_append = rc.HarnessKernel._append
    for ab in ablations:
        if ab == "progress":
            rc._progress_summary = lambda snapshot: ""  # type: ignore[assignment]
        elif ab == "continue":
            # The honest no-nudge shape: NO user message after a no-action
            # turn (exactly arm N's behavior). Patching the constant to ""
            # alone would append an EMPTY user message — a wire oddity N
            # never has, which collapses the loop for the wrong reason
            # (measured: 0.21 tests_pass, 38 model recoveries, runs dying at
            # turn ~4 — an artifact, not an ablation).
            rc._CONTINUE_PROMPT = ""  # type: ignore[assignment]

            def _append_no_empty_user(
                self: object,
                r: object,
                turn: int,
                *,
                assistant: str,
                user: str | None,
                _orig: Any = _orig_append,
            ) -> None:
                _orig(
                    self,  # type: ignore[arg-type]
                    r,
                    turn,
                    assistant=assistant,
                    user=user if user else None,
                )

            rc.HarnessKernel._append = _append_no_empty_user  # type: ignore[assignment]


def _kernel_service(
    gateway: OpenAICompatGateway,
    sources: Path,
    runs: Path,
    bus: EventBus,
    *,
    ablations: list[str] | None = None,
) -> AgentRunService:
    ablations = ablations or []
    context_engine = (
        _NoItemsContextEngine(ContextBudget(total_tokens=60_000))
        if "items" in ablations
        else ContextEngine(ContextBudget(total_tokens=60_000))
    )
    return AgentRunService(
        model_gateway_factory=_Factory(gateway),  # type: ignore[arg-type]
        tool_executor_factory=_Factory(_NullHandler()),  # type: ignore[arg-type]
        capability_runtime_factory=_Factory(_NullHandler()),  # type: ignore[arg-type]
        context_engine_factory=_Factory(context_engine),  # type: ignore[arg-type]
        workspace_root=sources,
        runs_root=runs,
        process_prefixes=PROCESS_PREFIXES,
        command_timeout_seconds=120.0,
        verification_timeout_seconds=300.0,
        event_bus=bus,
    )


def run_kernel_arm(
    fixture: dict[str, Any],
    contract: SubtaskContract,
    spec: Any,
    gateway: OpenAICompatGateway,
    sources: Path,
    runs: Path,
    *,
    max_turns: int,
    ablations: list[str] | None = None,
    trace_dir: Path | None = None,
) -> dict[str, Any]:
    bus = EventBus()
    # The service discards a run's bus history once the run is terminal, so
    # collect events through a sink — reading bus.history() afterwards would
    # silently yield [] (empty trace, all mechanism counts 0).
    events: list[EventEnvelope] = []
    bus.subscribe(events.append)
    service = _kernel_service(gateway, sources, runs, bus, ablations=ablations)
    started = time.monotonic()
    result = service.run(
        contract,
        spec,
        max_turns=max_turns,
        workspace=fixture["name"],
        verification_command=VERIFICATION,
    )
    wall = time.monotonic() - started
    history = [e for e in events if e.run_id == result.run_id]
    if not history:
        raise RuntimeError(f"no events captured for {result.run_id} — trace would be empty")
    if trace_dir is not None:
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / f"trace-{fixture['name']}-{result.run_id}.json").write_text(
            json.dumps(
                [{"event": e.event_type, "turn": e.turn_id, "payload": e.payload} for e in history],
                indent=2,
            ),
            encoding="utf-8",
        )
    counts = mechanism_counts(history)
    # SAME yardstick as arm N: did the tests pass in the run dir at the end,
    # regardless of whether the model proposed completion? A LIMIT_TURNS run
    # where the fix landed but was never proposed counts here (and NOT in
    # `accepted`, which is the verifier-gated completion metric).
    post_hoc_exit = _post_hoc(runs / result.run_id)
    return {
        "run_id": result.run_id,
        "arm": "K",
        "status": result.status.value,
        "stop_reason": result.stop_reason.value if result.stop_reason else None,
        "summary": result.summary[:400],
        "evidence_verdict": result.evidence.verification_verdict
        if result.evidence is not None
        else None,
        "checks": list(result.evidence.checks) if result.evidence is not None else [],
        "turns": result.usage.turns,
        "tool_calls": result.usage.tool_calls,
        "wall_seconds": round(wall, 1),
        # Verifier-gated: `succeeded` IS the acceptance (INV-08) — a false
        # success is structurally impossible; anything else is recorded
        # honestly as not-accepted.
        "accepted": result.status.value == "succeeded",
        "false_success": False,
        # Same yardstick as N: tests pass in the run dir at the end.
        "tests_pass_at_end": post_hoc_exit == 0,
        "post_hoc_exit": post_hoc_exit,
        # The mechanism's visible cost (§44): how many verification rounds
        # the run needed, how many FAILED (→ repair feedback), how many
        # recovery/repair turns were taken.
        **counts,
    }


#: Recovery actions that end the run — not a repair/retry that was taken.
_TERMINAL_ACTIONS = frozenset({"FAIL", "RETURN_PARTIAL", "ESCALATE"})
_NO_MECHANISM = {
    "verification_rounds": 0,
    "verification_fails": 0,
    "repairs": 0,
    "model_recoveries": 0,
    "tool_recoveries": 0,
}


def mechanism_counts(history: list[Any]) -> dict[str, int]:
    """§44 — the mechanism's visible cost, from the kernel's own events.

    ``repairs`` = verification failures the kernel turned into a repair
    turn; ``model_recoveries`` = model-call failures it retried or repaired
    (transient/rate-limit/malformed); ``tool_recoveries`` = tool
    infrastructure failures it retried or surfaced (``component ==
    "tool_runtime"``). All come from RECOVERY_ACTION — the event the kernel
    actually emits — and count only non-terminal actions: an ESCALATE/FAIL
    ends the run, it is not a repair that was taken.
    """
    rounds = fails = repairs = model = tool = 0
    for event in history:
        if event.event_type == VERIFICATION_STARTED:
            rounds += 1
        elif event.event_type == VERIFICATION_COMPLETED:
            fails += event.payload.get("verdict") != "PASS"
        elif event.event_type == RECOVERY_ACTION:
            if event.payload.get("action") in _TERMINAL_ACTIONS:
                continue
            if event.payload.get("component") == "tool_runtime":
                tool += 1
            elif event.payload.get("failure_class") == "VERIFICATION_FAILED":
                repairs += 1
            else:
                model += 1
    return {
        "verification_rounds": rounds,
        "verification_fails": fails,
        "repairs": repairs,
        "model_recoveries": model,
        "tool_recoveries": tool,
    }


# ---------------------------------------------------------------------------
# Arm N — naive ReAct loop: same model, same tools, same workspace copy,
# same ceiling, same SYSTEM PROMPT (the kernel's own _system_prompt over
# the same contract/spec/grants); NO verification gate, NO recovery,
# append-only context.
# ---------------------------------------------------------------------------


def _observation_text(result: object) -> str:
    """ToolDispatchResult → the text the naive loop feeds back to the model
    (the kernel would wrap it in a ToolObservation; the naive arm is rawer)."""
    r = result  # ToolDispatchResult
    return str(r.output)[:4000] if r.output else "(no output)"  # type: ignore[attr-defined]


def run_naive_arm(
    fixture: dict[str, Any],
    contract: SubtaskContract,
    spec: Any,
    gateway: OpenAICompatGateway,
    sources: Path,
    runs: Path,
    *,
    max_turns: int,
) -> dict[str, Any]:
    run_id = new_run_id()
    source = sources / fixture["name"]
    run_dir = provision_workspace(source, runs, run_id)
    manager = WorkspaceManager()
    grants = _grants()
    envelope = ExecutionEnvelope(
        run_id=run_id,
        workspace_id=run_id,
        filesystem=grants.filesystem,
        network=grants.network,
        process=grants.process,
    )
    workspace_id = manager.create_local(run_dir, envelope)
    dispatcher = WorkspaceToolDispatcher(manager, workspace_id)
    tools = standard_tool_specs(allow_commands=True, command_timeout_ms=120_000)
    by_id = {t.tool_id: t for t in tools}

    # §42 fairness: the SAME system prompt the kernel would build for this
    # contract/spec/grants — identity, objective, authority summary, action
    # protocol. The ONLY difference between the arms is the mechanism.
    state = StateManager()
    snapshot = state.create(
        run_id=run_id, task=task_state_from(contract), budget=BudgetLedger(), grants=grants
    )
    messages: list[ModelMessage] = [
        ModelMessage(role="system", content=_system_prompt(snapshot, spec)),
        ModelMessage(role="user", content=fixture["prompt"]),
    ]
    turns = 0
    tool_calls = 0
    claimed_done = False
    claim_text = ""
    error: str | None = None
    started = time.monotonic()
    while turns < max_turns:
        turns += 1
        # §42 fairness: the kernel's turn-budget note — the SAME text at the
        # SAME threshold (turn_budget_note is the kernel's own helper), as a
        # system message after the system prompt, for this request only
        # (the kernel re-assembles its seed per turn; it is never persisted).
        request_messages = messages
        note = turn_budget_note(max_turns - turns + 1)
        if note:
            request_messages = [
                messages[0],
                ModelMessage(role="system", content=note),
                *messages[1:],
            ]
        try:
            response = gateway.invoke(
                ModelRequest(messages=request_messages, tools=tools, token_limit=8192)
            )
        except Exception as exc:  # noqa: BLE003 — the naive arm has no recovery
            error = f"model error: {type(exc).__name__}"
            break
        action = response.action
        if isinstance(action, ToolCallBatchAction):
            messages.append(
                ModelMessage(
                    role="assistant",
                    content=response.raw_text or "",
                    tool_calls=list(action.calls),
                )
            )
            for call in action.calls:
                tool_calls += 1
                tool = by_id.get(call.tool_id)
                if tool is None:
                    observation = f"status: error\nunknown tool {call.tool_id!r}"
                else:
                    try:
                        observation = _observation_text(
                            dispatcher.dispatch(tool, call.arguments, envelope)
                        )
                    except Exception as exc:  # noqa: BLE003 — observation, never a crash
                        observation = f"status: error\n{type(exc).__name__}: {exc}"
                messages.append(
                    ModelMessage(role="tool", content=observation, tool_call_id=call.call_id)
                )
            continue
        if isinstance(action, FinalCandidate):
            claimed_done = True
            claim_text = action.summary or response.raw_text or ""
            messages.append(ModelMessage(role="assistant", content=claim_text))
            break
        # ContinueAction / anything else: record the raw text and keep going.
        messages.append(ModelMessage(role="assistant", content=response.raw_text or "(empty)"))
    wall = time.monotonic() - started

    # Post-hoc acceptance — the thing the kernel GATES but the naive loop
    # merely checks afterwards. This is where false successes live.
    exec_result = manager.execute(workspace_id, VERIFICATION, 300_000)
    accepted = bool(exec_result.exit_code == 0 and not exec_result.timed_out)
    return {
        "run_id": run_id,
        "arm": "N",
        "status": "claimed_done" if claimed_done else ("error" if error else "turn_limit"),
        "stop_reason": error or ("claimed_done" if claimed_done else "MAX_TURNS"),
        "summary": (claim_text or error or "")[:400],
        "evidence_verdict": None,
        "checks": [],
        "turns": turns,
        "tool_calls": tool_calls,
        "wall_seconds": round(wall, 1),
        "accepted": accepted,
        # §44 headline: the model CLAIMED completion and the verifier refutes it.
        "false_success": bool(claimed_done and not accepted),
        "tests_pass_at_end": accepted,
        "post_hoc_exit": exec_result.exit_code,
        "post_hoc_timed_out": exec_result.timed_out,
        **_NO_MECHANISM,
    }


# ---------------------------------------------------------------------------


def _run_one(
    fixture: dict[str, Any],
    arm: str,
    base_url: str,
    model: str,
    api_key: str,
    sources: Path,
    runs: Path,
    *,
    max_turns: int,
    ablations: list[str] | None = None,
    trace_dir: Path | None = None,
) -> dict[str, Any]:
    contract, spec = _contract_spec(fixture)
    gateway = OpenAICompatGateway(
        base_url=base_url, api_key=api_key, model_id=model, request_timeout_seconds=300.0
    )
    if arm == "K":
        record = run_kernel_arm(
            fixture,
            contract,
            spec,
            gateway,
            sources,
            runs,
            max_turns=max_turns,
            ablations=ablations,
            trace_dir=trace_dir,
        )
    else:
        record = run_naive_arm(fixture, contract, spec, gateway, sources, runs, max_turns=max_turns)
    record["fixture"] = fixture["name"]
    record["h_ref"] = H_REFS.get(fixture["name"], "")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:20128/v1")
    parser.add_argument("--model", default="OneNexus/glm-5.3")
    parser.add_argument("--api-key", default="", help="defaults to ACI_AGENT_MODEL_API_KEY (env)")
    parser.add_argument(
        "--cases", default="", help="comma-separated fixture names (default: all 8)"
    )
    parser.add_argument("--arms", default="K,N", help="comma subset of K,N")
    parser.add_argument("--repeat", type=int, default=3, help="runs per case per arm (default 3)")
    parser.add_argument("--parallel", type=int, default=4, help="concurrent runs (default 4)")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument(
        "--ablate",
        default="",
        help=(
            "comma subset of ABLATIONS (items,progress,continue) — turn K "
            "mechanisms OFF one at a time to attribute the edge; process-wide"
        ),
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="dump every K run's event history to <report>/traces/",
    )
    parser.add_argument("--out", default="", help="report path (default data/hbench/<ts>.json)")
    args = parser.parse_args(argv)

    import os

    api_key = args.api_key or os.environ.get("ACI_AGENT_MODEL_API_KEY", "")
    if not api_key:
        print("no API key — set ACI_AGENT_MODEL_API_KEY or --api-key", file=sys.stderr)
        return 2
    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
    ablations = [a.strip() for a in args.ablate.split(",") if a.strip()]
    unknown = [a for a in ablations if a not in ABLATIONS]
    if unknown:
        print(f"unknown ablations {unknown!r} — pick from {ABLATIONS}", file=sys.stderr)
        return 2
    if ablations:
        apply_ablations(ablations)
        print(f"ABLATIONS ACTIVE: {ablations} (process-wide)", flush=True)
    fixtures = _all_fixtures()
    if args.cases:
        wanted = {c.strip() for c in args.cases.split(",")}
        fixtures = [f for f in fixtures if f["name"] in wanted]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = REPORT_ROOT / stamp
    sources = root / "sources"
    runs = root / "runs"
    sources.mkdir(parents=True, exist_ok=True)
    for fixture in fixtures:
        target = sources / fixture["name"]
        target.mkdir(parents=True, exist_ok=True)
        for rel, content in fixture["files"].items():
            path = target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    jobs = [
        (fixture, arm, repeat)
        for repeat in range(1, args.repeat + 1)
        for fixture in fixtures
        for arm in arms
    ]
    trace_dir = root / "traces" if args.trace else None
    print(f"{len(jobs)} runs ({len(fixtures)} cases x {arms} x {args.repeat})", flush=True)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = {
            pool.submit(
                _run_one,
                fixture,
                arm,
                args.base_url,
                args.model,
                api_key,
                sources,
                runs,
                max_turns=args.max_turns,
                ablations=ablations,
                trace_dir=trace_dir,
            ): (fixture, arm, repeat)
            for fixture, arm, repeat in jobs
        }
        for future in futures:
            fixture, arm, repeat = futures[future]
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE003 — one crashed run never kills the pack
                record = {
                    "run_id": "",
                    "arm": arm,
                    "fixture": fixture["name"],
                    "h_ref": H_REFS.get(fixture["name"], ""),
                    "status": "crashed",
                    "stop_reason": type(exc).__name__,
                    "accepted": False,
                    "false_success": False,
                    "tests_pass_at_end": False,
                    "turns": 0,
                    "tool_calls": 0,
                    "wall_seconds": 0.0,
                    **_NO_MECHANISM,
                }
            record["repeat"] = repeat
            results.append(record)
            print(
                f"[{fixture['name']} #{repeat}] arm {arm} -> {record['status']} "
                f"accepted={record['accepted']} false_success={record['false_success']} "
                f"turns={record['turns']} tools={record['tool_calls']} "
                f"wall={record['wall_seconds']}s "
                f"verify={record['verification_rounds']}rounds/"
                f"{record['verification_fails']}fails/{record['repairs']}repairs/"
                f"{record['model_recoveries']}model-recoveries/"
                f"{record['tool_recoveries']}tool-recoveries",
                flush=True,
            )

    def _agg(arm: str) -> dict[str, Any]:
        rows = [r for r in results if r["arm"] == arm]
        accepted = [1.0 if r["accepted"] else 0.0 for r in rows]
        false_success = [1.0 if r["false_success"] else 0.0 for r in rows]
        tests_pass = [1.0 if r.get("tests_pass_at_end") else 0.0 for r in rows]
        return {
            "n": len(rows),
            "acceptance": sum(accepted) / len(accepted) if accepted else 0.0,
            "tests_pass_at_end": sum(tests_pass) / len(tests_pass) if tests_pass else 0.0,
            "false_success": sum(false_success) / len(false_success) if false_success else 0.0,
            "turns_mean": statistics.mean(r["turns"] for r in rows) if rows else 0.0,
            "turns_stdev": statistics.stdev([r["turns"] for r in rows]) if len(rows) > 1 else 0.0,
            "wall_mean": statistics.mean(r["wall_seconds"] for r in rows) if rows else 0.0,
            "wall_stdev": statistics.stdev([r["wall_seconds"] for r in rows])
            if len(rows) > 1
            else 0.0,
            "verification_rounds_mean": statistics.mean(r["verification_rounds"] for r in rows)
            if rows
            else 0.0,
            "verification_fails_total": sum(r["verification_fails"] for r in rows),
            "repairs_total": sum(r["repairs"] for r in rows),
            "model_recoveries_total": sum(r["model_recoveries"] for r in rows),
            "tool_recoveries_total": sum(r["tool_recoveries"] for r in rows),
        }

    report = {
        "stamp": stamp,
        "model": args.model,
        "base_url": args.base_url,
        "arms": arms,
        "repeat": args.repeat,
        "max_turns": args.max_turns,
        "ablations": ablations,
        "verification": VERIFICATION,
        "pending_h_cases": PENDING_H_CASES,
        "aggregate": {arm: _agg(arm) for arm in arms},
        "results": results,
        "caveat": (
            "§34: small n, one model, author-built fixtures — directional only. "
            "K acceptance is verifier-GATED (a false success is structurally "
            "impossible); N acceptance is post-hoc, so N.false_success is the "
            "harness-value headline. Both arms share the system prompt "
            "(_system_prompt over the same contract/spec/grants), tools and "
            "process ceiling — only the mechanism differs."
        ),
    }
    out = Path(args.out) if args.out else REPORT_ROOT / f"hbench-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nreport: {out}")
    for arm in arms:
        agg = report["aggregate"][arm]
        print(
            f"arm {arm}: acceptance {agg['acceptance']:.2f} "
            f"tests_pass_at_end {agg['tests_pass_at_end']:.2f} "
            f"false_success {agg['false_success']:.2f} "
            f"turns {agg['turns_mean']:.1f}±{agg['turns_stdev']:.1f} "
            f"wall {agg['wall_mean']:.0f}±{agg['wall_stdev']:.0f}s "
            f"verify_rounds {agg['verification_rounds_mean']:.1f} "
            f"(fails {agg['verification_fails_total']}, repairs {agg['repairs_total']}, "
            f"model recoveries {agg['model_recoveries_total']}, "
            f"tool recoveries {agg['tool_recoveries_total']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
