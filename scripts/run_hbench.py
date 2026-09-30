"""H-bench runner — the ADR-014 benchmark arena for harness mechanism changes.

Harness quality is measured SEPARATELY from model quality (harness.md §42):
same model, same fixtures, same tools, same budget — different harness
mechanism. This runner executes the pack and records §44 metrics.

Arms (the A/B the §80 verdict asked for — "does the harness change
outcomes?", not "does the model?"):

  K — HarnessKernel: the real AgentRunService — verifier-GATED completion
      (INV-08: a claim never self-succeeds), authority preflight, guardrails,
      recovery, context engine.
  N — naive loop: the SAME model + the SAME workspace tools through a plain
      ReAct loop — no verification gate (the model's "done" IS the result;
      acceptance is verified POST-HOC), no recovery, append-only context.

The delta K−N is what the kernel adds. The headline metric is FALSE
SUCCESS (§44): N reports "done" the verifier refutes; K can only report
`succeeded` when the verification command actually passed.

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
        [--cases multi-config-precedence,...] [--arms K,N] [--max-turns 12]

Writes a JSON report to data/hbench/ (gitignored) and prints the table.
§34 caveat applies to everything here: small n, one model, directional only.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "src"))

from proof_loop import LONG_TASKS, MULTI_TASKS  # noqa: E402

from aci.application.run_agent_task import AgentRunService, new_run_id  # noqa: E402
from aci.domain.runtime.actions import FinalCandidate, ToolCallBatchAction  # noqa: E402
from aci.domain.runtime.authority import (  # noqa: E402
    ExecutionEnvelope,
    FilesystemScope,
    GrantEnvelope,
    ProcessScope,
)
from aci.domain.runtime.subtask import AcceptanceCriterion, SubtaskContract  # noqa: E402
from aci.runtime.context_engine import ContextBudget, ContextEngine  # noqa: E402
from aci.runtime.model_gateway import ModelMessage, ModelRequest, OpenAICompatGateway  # noqa: E402
from aci.runtime.profiles import runtime_spec_for  # noqa: E402
from aci.runtime.run_controller import _ACTION_PROTOCOL  # noqa: E402
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

#: fixture name → (Appendix D case it most directly exercises, category)
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


# ---------------------------------------------------------------------------
# Arm K — the real HarnessKernel, in-process (the same service the REST
# wiring builds; no HTTP, no DB — the kernel is sync and self-contained).
# ---------------------------------------------------------------------------


class _Factory:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def build(self) -> object:
        return self._obj


class _NullTools:
    def build(self) -> object:
        class _None:
            def handle_request(self, request: object, snapshot: object) -> list[object]:
                return []

        return _None()


def _kernel_service(gateway: OpenAICompatGateway, sources: Path, runs: Path) -> AgentRunService:
    return AgentRunService(
        model_gateway_factory=_Factory(gateway),  # type: ignore[arg-type]
        tool_executor_factory=_NullTools(),  # type: ignore[arg-type]
        capability_runtime_factory=_NullTools(),  # type: ignore[arg-type]
        context_engine_factory=_Factory(ContextEngine(ContextBudget(total_tokens=60_000))),  # type: ignore[arg-type]
        workspace_root=sources,
        runs_root=runs,
        process_prefixes=PROCESS_PREFIXES,
        command_timeout_seconds=120.0,
        verification_timeout_seconds=300.0,
    )


def run_kernel_arm(
    fixture: dict[str, Any],
    gateway: OpenAICompatGateway,
    sources: Path,
    runs: Path,
    *,
    max_turns: int,
) -> dict[str, Any]:
    service = _kernel_service(gateway, sources, runs)
    contract = SubtaskContract(
        task_id=new_run_id(),
        objective=fixture["prompt"],
        global_context="",
        constraints=[],
        acceptance_criteria=[
            AcceptanceCriterion(criterion_id=f"ac-{i + 1}", description=c)
            for i, c in enumerate(("the whole test suite passes",))
        ],
        requested_profile="coder",
        budget=None,
        created_at=datetime.now(UTC),
    )
    spec = runtime_spec_for("coder")
    started = time.monotonic()
    result = service.run(
        contract,
        spec,
        max_turns=max_turns,
        workspace=fixture["name"],
        verification_command=VERIFICATION,
    )
    wall = time.monotonic() - started
    return {
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
    }


# ---------------------------------------------------------------------------
# Arm N — naive ReAct loop: same model, same tools, same workspace copy,
# same process ceiling; NO verification gate, NO recovery, NO compaction.
# ---------------------------------------------------------------------------


def _observation_text(result: object) -> str:
    """ToolDispatchResult → the text the naive loop feeds back to the model
    (the kernel would wrap it in a ToolObservation; the naive arm is rawer)."""
    r = result  # ToolDispatchResult
    return str(r.output)[:4000] if r.output else "(no output)"  # type: ignore[attr-defined]


def run_naive_arm(
    fixture: dict[str, Any],
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
    grants = GrantEnvelope(
        filesystem=FilesystemScope(read=["."], write=["."]),
        process=ProcessScope(allowed_prefixes=PROCESS_PREFIXES),
    )
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

    # §42 fairness: the SAME action protocol as the kernel — the A/B
    # measures harness mechanisms, not prompt differences.
    messages: list[ModelMessage] = [
        ModelMessage(role="system", content=_ACTION_PROTOCOL),
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
        try:
            response = gateway.invoke(
                ModelRequest(messages=messages, tools=tools, token_limit=8192)
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
        "post_hoc_exit": exec_result.exit_code,
        "post_hoc_timed_out": exec_result.timed_out,
    }


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:20128/v1")
    parser.add_argument("--model", default="OneNexus/glm-5.3")
    parser.add_argument("--api-key", default="", help="defaults to ACI_AGENT_MODEL_API_KEY (env)")
    parser.add_argument(
        "--cases", default="", help="comma-separated fixture names (default: all 8)"
    )
    parser.add_argument("--arms", default="K,N", help="comma subset of K,N")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--out", default="", help="report path (default data/hbench/<ts>.json)")
    args = parser.parse_args(argv)

    import os

    api_key = args.api_key or os.environ.get("ACI_AGENT_MODEL_API_KEY", "")
    if not api_key:
        print("no API key — set ACI_AGENT_MODEL_API_KEY or --api-key", file=sys.stderr)
        return 2
    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
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

    gateway = OpenAICompatGateway(
        base_url=args.base_url,
        api_key=api_key,
        model_id=args.model,
        request_timeout_seconds=300.0,
    )

    results: list[dict[str, Any]] = []
    for fixture in fixtures:
        for arm in arms:
            print(f"[{fixture['name']}] arm {arm} ...", flush=True)
            if arm == "K":
                record = run_kernel_arm(fixture, gateway, sources, runs, max_turns=args.max_turns)
            elif arm == "N":
                record = run_naive_arm(fixture, gateway, sources, runs, max_turns=args.max_turns)
            else:
                print(f"unknown arm {arm!r}", file=sys.stderr)
                return 2
            record["fixture"] = fixture["name"]
            record["h_ref"] = H_REFS.get(fixture["name"], "")
            results.append(record)
            print(
                f"    -> {record['status']} accepted={record['accepted']} "
                f"false_success={record['false_success']} turns={record['turns']} "
                f"tools={record['tool_calls']} wall={record['wall_seconds']}s",
                flush=True,
            )

    def _rate(arm: str, key: str) -> float:
        rows = [r for r in results if r["arm"] == arm]
        return sum(1 for r in rows if r[key]) / len(rows) if rows else 0.0

    report = {
        "stamp": stamp,
        "model": args.model,
        "base_url": args.base_url,
        "arms": arms,
        "max_turns": args.max_turns,
        "verification": VERIFICATION,
        "pending_h_cases": PENDING_H_CASES,
        "aggregate": {
            arm: {
                "n": sum(1 for r in results if r["arm"] == arm),
                "acceptance": _rate(arm, "accepted"),
                "false_success": _rate(arm, "false_success"),
                "mean_turns": _mean(results, arm, "turns"),
                "mean_tool_calls": _mean(results, arm, "tool_calls"),
                "mean_wall_seconds": _mean(results, arm, "wall_seconds"),
            }
            for arm in arms
        },
        "results": results,
        "caveat": (
            "§34: small n, one model, author-built fixtures — directional only. "
            "K acceptance is verifier-GATED (a false success is structurally "
            "impossible); N acceptance is post-hoc, so N.false_success is the "
            "harness-value headline."
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
            f"false_success {agg['false_success']:.2f} "
            f"turns {agg['mean_turns']:.1f} wall {agg['mean_wall_seconds']:.0f}s"
        )
    return 0


def _mean(rows: list[dict[str, Any]], arm: str, key: str) -> float:
    values = [r[key] for r in rows if r["arm"] == arm]
    return sum(values) / len(values) if values else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
