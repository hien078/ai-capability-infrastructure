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
  S — skills arm: arm K + the REAL registry capability plane — the same
      Container composition the REST server runs (operational DB aci_bench,
      fastembed semantics, one run-scoped RegistryCapabilityClient per run)
      wired as the capability handler, so the model is OFFERED
      request_capability (tool + protocol text). NOTHING else differs from
      K (same fixtures/model/tools/sandbox/max_turns/verification/turn-budget
      note); S−K isolates what registry skills add to the kernel.
  P — preload arm: arm S + the kernel's run-start skill PRELOAD
      (``preload_capabilities=True``) — the §14 router runs on the task
      objective BEFORE turn 1 and the selected skills are in context from
      the FIRST model request on. S measured that the model never asks
      (ZERO capability requests in 24 runs), so P is the arm that actually
      carries skills; NOTHING else differs from S (same registry plane,
      fixtures, model, tools, sandbox, max_turns, verification,
      turn-budget note) — P−S isolates preload-vs-offered.

The delta K−N is what the kernel adds. The headline metric is FALSE
SUCCESS (§44): N reports "done" the verifier refutes; K can only report
`succeeded` when the verification command actually passed. K additionally
reports its verification rounds / fails and repair turns (from the event
bus), so the mechanism's cost is visible next to its gain. S/P report their
capability requests + loaded skill ids + preloaded skill ids + token usage,
so the skill plane's cost is visible next to its outcome.

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
        [--set verified|domain] [--cases multi-config-precedence,...] \
        [--arms K,N,S,P] [--repeat 3] [--parallel 4] [--max-turns 12]

Fixture sets (--set): 'verified' (default) = the 8 §80 multi/long fixtures;
'domain' = the domain-knowledge fixtures (scripts/domain_tasks.py, verified
by scripts/verify_domain_fixtures.py) — small workspaces whose correct fix
needs knowledge a production skill carries (prompt-injection hardening,
MCP manifest conventions, brand values, design tells). The domain set is
the SKILL axis: run K vs P on it and the report records, per run, whether
the fixture's INTENDED skill (DOMAIN_INTENDED_SKILLS) was preloaded.

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

from domain_tasks import DOMAIN_INTENDED_SKILLS, DOMAIN_TASKS  # noqa: E402
from proof_loop import LONG_TASKS, MULTI_TASKS  # noqa: E402

from aci.application.run_agent_task import (  # noqa: E402
    AgentRunService,
    new_run_id,
)
from aci.domain.runtime.actions import (  # noqa: E402
    CapabilityRequest,
    FinalCandidate,
    ToolCallBatchAction,
)
from aci.domain.runtime.authority import (  # noqa: E402
    ExecutionEnvelope,
    FilesystemScope,
    GrantEnvelope,
    ProcessScope,
)
from aci.domain.runtime.events import EventEnvelope  # noqa: E402
from aci.domain.runtime.state import (  # noqa: E402
    BudgetLedger,
    CapabilityActivation,
    RuntimeStateSnapshot,
)
from aci.domain.runtime.subtask import (  # noqa: E402
    AcceptanceCriterion,
    SubtaskContract,
)
from aci.runtime.capability_runtime import ACIClient, CapabilityRuntime  # noqa: E402
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
from aci.runtime.sandbox import (  # noqa: E402
    BwrapSandbox,
    ProcessSandbox,
    build_process_sandbox,
)
from aci.runtime.state_manager import StateManager  # noqa: E402
from aci.runtime.workspace import LocalWorkspace, WorkspaceManager  # noqa: E402
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

#: Arm S registry plane — the OPERATIONAL DB (the real corpus + telemetry;
#: NEVER the dev/test `aci` DB, which accumulates pytest fixtures), the
#: semantic embedder (ACI_EMBEDDER=fastembed semantics) and the repo's
#: content-addressed object store: the same deployment the REST server runs.
ACI_BENCH_DATABASE_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci_bench"
ACI_OBJECT_STORE_ROOT = REPO_ROOT / "data" / "objects"

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


def _fixture_set(name: str) -> list[dict[str, Any]]:
    """The fixture pack for a run. 'verified' = the 8 §80 multi/long
    fixtures (the default — every existing round's pack, unchanged);
    'domain' = the domain-knowledge fixtures (domain_tasks.py)."""
    if name == "domain":
        return list(DOMAIN_TASKS)
    return _all_fixtures()


def _post_hoc(run_dir: Path, sandbox: ProcessSandbox) -> int:
    """Run the verification command in a run dir AFTER the run — the SAME
    yardstick for both arms (§44 outcome), independent of the arm's own
    completion semantics. Model-written code: it runs in the SAME sandbox as
    the arms' own commands (the minimal env keeps PYTHONDONTWRITEBYTECODE=1,
    the §80 bytecode-cache trap)."""
    result = LocalWorkspace(run_dir, sandbox=sandbox).execute(VERIFICATION, 300_000)
    return 124 if result.timed_out else int(result.exit_code)


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
    #: No registry in the bench: capability_request is not offered to arm K
    #: (arm N has no such action either — the protocols stay identical).
    advertised = False

    def handle_request(self, request: object, snapshot: object) -> list[object]:
        return []


# ---------------------------------------------------------------------------
# Arm S — arm K + the REAL registry capability plane. The ONLY difference
# from K is the capability handler: the real CapabilityRuntime over a fresh
# run-scoped RegistryCapabilityClient — the same composition the REST
# Container builds (adapters/inbound/rest/wiring.py), against the
# operational DB, so every capability request runs the SAME §14 router
# POST /v1/routes runs (route_run telemetry included, client_type
# harness-kernel) under the same kernel-side selection policy
# (ACI_AGENT_CAPABILITY_*, via agent_capability_policy(Settings)).
# ---------------------------------------------------------------------------


class _CountingCapabilityRuntime(CapabilityRuntime):
    """The REAL CapabilityRuntime (advertised: a live plane IS offered to
    the model), plus per-run counters for the report — how many capability
    requests the model made and which skills actually loaded."""

    def __init__(self, aci: ACIClient) -> None:
        super().__init__(aci)
        self.requests = 0
        self.loaded: list[str] = []
        self.preloaded: list[str] = []

    def preload(
        self, request: CapabilityRequest, snapshot: RuntimeStateSnapshot
    ) -> list[CapabilityActivation]:
        """The kernel's run-start preload — NOT a model request (not
        counted in ``requests``); its skills are loaded skills all the same."""
        activations = super().preload(request, snapshot)
        ids = [f"{a.capability_id}@{a.version}" for a in activations]
        self.preloaded.extend(ids)
        self.loaded.extend(ids)
        return activations

    def handle_request(
        self, request: CapabilityRequest, snapshot: RuntimeStateSnapshot
    ) -> list[CapabilityActivation]:
        self.requests += 1
        activations = super().handle_request(request, snapshot)
        self.loaded.extend(f"{a.capability_id}@{a.version}" for a in activations)
        return activations


def _skills_capability_runtime(client_factory: Any) -> _CountingCapabilityRuntime:
    """One run-scoped handler: a FRESH registry client per run (the REST
    rule — the client's issued-selection allowlist and the runtime's refresh
    budget / digest cache never leak across runs)."""
    return _CountingCapabilityRuntime(client_factory())


def build_capability_container() -> Any:
    """The SAME registry + §14 router composition the REST server runs
    (adapters/inbound/rest/wiring.Container) — ONE per process, exactly the
    deployment shape the REST server serves concurrent requests with —
    pointed at the OPERATIONAL DB (aci_bench: the real corpus + telemetry)
    with the semantic embedder and the repo's object store. The kernel-side
    selection policy comes from the same Settings defaults the REST
    Container applies (agent_capability_policy)."""
    from aci.adapters.inbound.rest.wiring import Container
    from aci.config import Settings

    return Container(
        Settings(
            database_url=ACI_BENCH_DATABASE_URL,
            object_store_root=str(ACI_OBJECT_STORE_ROOT),
            embedder="fastembed",
        )
    )


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
    sandbox: ProcessSandbox | None = None,
    capability_factory: object | None = None,
    preload_capabilities: bool = False,
) -> AgentRunService:
    """One kernel service per run. ``capability_factory`` is arm S's real
    registry plane; the default (None) keeps arm K on the null handler —
    byte-identical to the pre-S runner. ``preload_capabilities`` turns on
    the kernel's run-start skill preload (default OFF = K/N/S unchanged;
    with the null handler it is a no-op — nothing is advertised)."""
    ablations = ablations or []
    context_engine = (
        _NoItemsContextEngine(ContextBudget(total_tokens=60_000))
        if "items" in ablations
        else ContextEngine(ContextBudget(total_tokens=60_000))
    )
    capability_runtime_factory = (
        capability_factory if capability_factory is not None else _Factory(_NullHandler())
    )
    return AgentRunService(
        model_gateway_factory=_Factory(gateway),  # type: ignore[arg-type]
        tool_executor_factory=_Factory(_NullHandler()),  # type: ignore[arg-type]
        capability_runtime_factory=capability_runtime_factory,  # type: ignore[arg-type]
        context_engine_factory=_Factory(context_engine),  # type: ignore[arg-type]
        workspace_root=sources,
        runs_root=runs,
        process_prefixes=PROCESS_PREFIXES,
        command_timeout_seconds=120.0,
        verification_timeout_seconds=300.0,
        event_bus=bus,
        process_sandbox=sandbox if sandbox is not None else BwrapSandbox(),
        preload_capabilities=preload_capabilities,
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
    sandbox: ProcessSandbox | None = None,
    arm: str = "K",
    capability_factory: object | None = None,
    preload_capabilities: bool = False,
) -> dict[str, Any]:
    sandbox = sandbox if sandbox is not None else BwrapSandbox()
    bus = EventBus()
    # The service discards a run's bus history once the run is terminal, so
    # collect events through a sink — reading bus.history() afterwards would
    # silently yield [] (empty trace, all mechanism counts 0).
    events: list[EventEnvelope] = []
    bus.subscribe(events.append)
    service = _kernel_service(
        gateway,
        sources,
        runs,
        bus,
        ablations=ablations,
        sandbox=sandbox,
        capability_factory=capability_factory,
        preload_capabilities=preload_capabilities,
    )
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
    post_hoc_exit = _post_hoc(runs / result.run_id, sandbox)
    return {
        "run_id": result.run_id,
        "arm": arm,
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
        # §44 cost next to outcome: what the run consumed (tokens) and —
        # arms S/P only, overwritten by run_skills_arm — what the capability
        # plane added (requests + loaded/preloaded skill ids). K's null plane
        # is the honest zero.
        "tokens_in": result.usage.model_input_tokens,
        "tokens_out": result.usage.model_output_tokens,
        "capability_requests": 0,
        "skills_loaded": [],
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


def run_skills_arm(
    fixture: dict[str, Any],
    contract: SubtaskContract,
    spec: Any,
    gateway: OpenAICompatGateway,
    sources: Path,
    runs: Path,
    *,
    max_turns: int,
    container: Any,
    sandbox: ProcessSandbox | None = None,
    trace_dir: Path | None = None,
    arm: str = "S",
    preload_capabilities: bool = False,
) -> dict[str, Any]:
    """Arm S = arm K + the REAL registry capability plane, and NOTHING else
    different: the same run_kernel_arm path (fixtures, tools, sandbox,
    max_turns, verification, turn-budget note) with the one substitution —
    the capability handler is the real CapabilityRuntime over a fresh
    run-scoped RegistryCapabilityClient from the REST Container
    composition, so the model is OFFERED request_capability (tool + protocol
    text). The counters record what the plane actually did.
    ``preload_capabilities`` (default OFF — arm S unchanged) adds the
    kernel's run-start preload: preloaded skills count in ``skills_loaded``
    and ``skills_preloaded`` but NOT in ``capability_requests`` (those stay
    model-initiated). Arm P (``arm="P"``) is this function with the preload
    ON — the ONLY difference from S."""
    runtime = _skills_capability_runtime(container.agent_capability_clients)
    record = run_kernel_arm(
        fixture,
        contract,
        spec,
        gateway,
        sources,
        runs,
        max_turns=max_turns,
        trace_dir=trace_dir,
        sandbox=sandbox,
        arm=arm,
        capability_factory=_Factory(runtime),
        preload_capabilities=preload_capabilities,
    )
    record["capability_requests"] = runtime.requests
    record["skills_loaded"] = list(runtime.loaded)
    record["skills_preloaded"] = list(runtime.preloaded)
    return record


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
    sandbox: ProcessSandbox | None = None,
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
    # §42 fairness: the SAME process sandbox as arm K (one instance per pack).
    workspace_id = manager.create_local(
        run_dir, envelope, sandbox=sandbox if sandbox is not None else BwrapSandbox()
    )
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
    tokens_in = 0
    tokens_out = 0
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
        # Telemetry only (§44 cost next to outcome) — no behavior change.
        tokens_in += response.usage.input_tokens
        tokens_out += response.usage.output_tokens
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
        # §44 cost next to outcome — the naive loop has no RunResult, so
        # usage is accumulated from the gateway responses; no capability
        # plane exists in this arm (honest zeros).
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "capability_requests": 0,
        "skills_loaded": [],
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
    sandbox: ProcessSandbox | None = None,
    container: Any = None,
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
            sandbox=sandbox,
        )
    elif arm == "S":
        record = run_skills_arm(
            fixture,
            contract,
            spec,
            gateway,
            sources,
            runs,
            max_turns=max_turns,
            container=container,
            sandbox=sandbox,
            trace_dir=trace_dir,
        )
    elif arm == "P":
        # Arm P = arm S with the run-start preload ON — the ONLY difference.
        record = run_skills_arm(
            fixture,
            contract,
            spec,
            gateway,
            sources,
            runs,
            max_turns=max_turns,
            container=container,
            sandbox=sandbox,
            trace_dir=trace_dir,
            arm="P",
            preload_capabilities=True,
        )
    else:
        record = run_naive_arm(
            fixture, contract, spec, gateway, sources, runs, max_turns=max_turns, sandbox=sandbox
        )
    record["fixture"] = fixture["name"]
    record["h_ref"] = H_REFS.get(fixture["name"], "")
    # The domain set's skill axis: which production skill this fixture's
    # fix needs ("" on the verified pack — no intended skill there).
    record["intended_skill"] = DOMAIN_INTENDED_SKILLS.get(fixture["name"], "")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:20128/v1")
    parser.add_argument("--model", default="OneNexus/glm-5.3")
    parser.add_argument("--api-key", default="", help="defaults to ACI_AGENT_MODEL_API_KEY (env)")
    parser.add_argument(
        "--cases",
        default="",
        help="comma-separated fixture names (default: the whole selected set)",
    )
    parser.add_argument(
        "--set",
        default="verified",
        choices=["verified", "domain"],
        help=(
            "fixture set: 'verified' = the 8 §80 multi/long fixtures (default), "
            "'domain' = the domain-knowledge fixtures (domain_tasks.py)"
        ),
    )
    parser.add_argument("--arms", default="K,N", help="comma subset of K,N,S,P")
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
    parser.add_argument(
        "--sandbox",
        choices=["bwrap", "none"],
        default="bwrap",
        help=(
            "process sandbox for BOTH arms (§16.5; default bwrap — the run "
            "refuses to start when bwrap is unusable; `none` = explicit opt-out)"
        ),
    )
    parser.add_argument("--out", default="", help="report path (default data/hbench/<ts>.json)")
    args = parser.parse_args(argv)

    import os

    api_key = args.api_key or os.environ.get("ACI_AGENT_MODEL_API_KEY", "")
    if not api_key:
        print("no API key — set ACI_AGENT_MODEL_API_KEY or --api-key", file=sys.stderr)
        return 2
    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
    unknown_arms = [a for a in arms if a not in ("K", "N", "S", "P")]
    if unknown_arms:
        print(f"unknown arms {unknown_arms!r} — pick from K,N,S,P", file=sys.stderr)
        return 2
    ablations = [a.strip() for a in args.ablate.split(",") if a.strip()]
    unknown = [a for a in ablations if a not in ABLATIONS]
    if unknown:
        print(f"unknown ablations {unknown!r} — pick from {ABLATIONS}", file=sys.stderr)
        return 2
    if ablations:
        apply_ablations(ablations)
        print(f"ABLATIONS ACTIVE: {ablations} (process-wide)", flush=True)
    # Arm S/P registry plane: ONE Container per process (the REST deployment
    # shape) — built only when a skills arm is requested, so K/N runs stay
    # DB-free.
    container = None
    if "S" in arms or "P" in arms:
        container = build_capability_container()
        print(
            "arm S/P: registry capability plane wired (operational DB aci_bench, "
            "fastembed, one run-scoped client per run)",
            flush=True,
        )
    # ONE sandbox instance for the whole pack: both arms (and both post-hoc
    # yardsticks) execute model-written code under the identical profile.
    sandbox = build_process_sandbox(args.sandbox)
    unusable = sandbox.unavailable_reason()
    if unusable is not None:
        print(f"sandbox unusable: {unusable} — fix it or pass --sandbox none", file=sys.stderr)
        return 2
    if args.sandbox == "none":
        print("SANDBOX OFF (--sandbox none): model-written code runs as you", flush=True)
    fixtures = _fixture_set(args.set)
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
                sandbox=sandbox,
                container=container,
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
                    "intended_skill": DOMAIN_INTENDED_SKILLS.get(fixture["name"], ""),
                    "status": "crashed",
                    "stop_reason": type(exc).__name__,
                    "accepted": False,
                    "false_success": False,
                    "tests_pass_at_end": False,
                    "turns": 0,
                    "tool_calls": 0,
                    "wall_seconds": 0.0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "capability_requests": 0,
                    "skills_loaded": [],
                    **_NO_MECHANISM,
                }
            record["repeat"] = repeat
            results.append(record)
            print(
                f"[{fixture['name']} #{repeat}] arm {arm} -> {record['status']} "
                f"accepted={record['accepted']} false_success={record['false_success']} "
                f"turns={record['turns']} tools={record['tool_calls']} "
                f"wall={record['wall_seconds']}s "
                f"tokens={record.get('tokens_in', 0)}/{record.get('tokens_out', 0)} "
                f"caps={record.get('capability_requests', 0)} "
                f"skills={','.join(record.get('skills_loaded', [])) or '-'} "
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
        tokens_in = [float(r.get("tokens_in", 0)) for r in rows]
        tokens_out = [float(r.get("tokens_out", 0)) for r in rows]
        skills_loaded = [len(r.get("skills_loaded", ())) for r in rows]
        skills_preloaded = [len(r.get("skills_preloaded", ())) for r in rows]
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
            # §44 cost next to outcome, for EVERY arm (S/P's plane is the
            # interesting one; K/N carry the honest zeros).
            "tokens_in_mean": statistics.mean(tokens_in) if rows else 0.0,
            "tokens_out_mean": statistics.mean(tokens_out) if rows else 0.0,
            "skills_loaded_mean": statistics.mean(skills_loaded) if rows else 0.0,
            "skills_preloaded_mean": statistics.mean(skills_preloaded) if rows else 0.0,
            "capability_requests_total": sum(int(r.get("capability_requests", 0)) for r in rows),
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
        "fixture_set": args.set,
        "repeat": args.repeat,
        "max_turns": args.max_turns,
        "ablations": ablations,
        "sandbox": args.sandbox,
        "verification": VERIFICATION,
        "pending_h_cases": PENDING_H_CASES,
        "aggregate": {arm: _agg(arm) for arm in arms},
        "results": results,
        "caveat": (
            "§34: small n, one model, author-built fixtures — directional only. "
            "K acceptance is verifier-GATED (a false success is structurally "
            "impossible); N acceptance is post-hoc, so N.false_success is the "
            "harness-value headline. All arms share the system prompt "
            "(_system_prompt over the same contract/spec/grants), tools and "
            "process ceiling. S differs from K ONLY by the capability handler "
            "(the real registry plane: request_capability offered + its "
            "protocol text), so S-K isolates the registry-skill contribution; "
            "P = S + the run-start preload (skills routed on the objective "
            "and loaded BEFORE turn 1), so P-S isolates preload-vs-offered; "
            "N shares K's prompt base minus the capability protocol. This "
            "round carries the turn-budget note and the bwrap sandbox — NOT "
            "comparable to rounds <= 3."
        ),
    }
    if container is not None:
        report["arm_s_registry"] = {
            "database": "aci_bench (operational)",
            "embedder": "fastembed",
            "object_store_root": str(ACI_OBJECT_STORE_ROOT),
            "selection_policy": "Settings defaults (agent_capability_policy)",
        }
    if args.set == "domain":
        # The skill axis: which production skill each fixture's fix needs —
        # the report answers "was the INTENDED skill preloaded?" per run.
        report["intended_skills"] = dict(DOMAIN_INTENDED_SKILLS)
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
            f"tokens_in {agg['tokens_in_mean']:.0f} "
            f"tokens_out {agg['tokens_out_mean']:.0f} "
            f"skills_loaded {agg['skills_loaded_mean']:.2f} "
            f"skills_preloaded {agg['skills_preloaded_mean']:.2f} "
            f"(capability requests {agg['capability_requests_total']}) "
            f"verify_rounds {agg['verification_rounds_mean']:.1f} "
            f"(fails {agg['verification_fails_total']}, repairs {agg['repairs_total']}, "
            f"model recoveries {agg['model_recoveries_total']}, "
            f"tool recoveries {agg['tool_recoveries_total']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
