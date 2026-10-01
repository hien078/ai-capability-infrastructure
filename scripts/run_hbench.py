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
  R — private-skill arm (E2, --set private ONLY): arm K + the case's
      PRIVATE skill preloaded from a LOCAL in-process capability handler
      — no registry, no DB. The private fixtures' required knowledge did
      not exist before today (a fictional internal standard invented with
      the fixture), so no model can carry it: the rules are documented ONLY
      in the skill and pinned in the tests as sha256 digests. R−K on the
      private set isolates what a skill carrying NON-PUBLIC knowledge
      adds — the open axis of ADR-014 amendment 17 (public knowledge was
      proven ungated by the domain round: brand-palette 3/3 naked).
  F — file-in-repo arm (E2B, --set private ONLY): arm K's wiring EXACTLY
      (null capability plane, no preload) with ONE difference — the case's
      skill text sits in the run workspace as a plain repo document
      (``docs/standards/<skill_id>.md``, materialized into the F source
      root before the run). The objective is NOT changed: a real repo does
      not announce its docs, so the model must find (or miss) the file on
      its own. F−K isolates what a passive in-repo document adds over no
      document; R−F isolates push-into-context over discoverable-on-disk
      — together they answer "is ACI's registry+router worth it over
      docs-in-repo?" for non-public knowledge.
  Bp — registry-routed preload arm (E2B, --set private ONLY): arm K + the
      REAL registry capability plane pointed at the EXPERIMENT registry
      copy (``--registry-db-url`` + ``--object-store-root``; the same
      Container composition arms S/P use, fastembed semantics) with the
      kernel's run-start preload ON — the §14 router picks from the
      objective among the real corpus PLUS the two ingested private
      skills (scripts/e2b_setup_registry.py). Per run the record carries
      which skills were preloaded and whether the case's private skill
      was among them, at which rank. Bp−R isolates registry+router over
      the pinned handler; Bp−F isolates routed preload over a plain file.
  Bq — registry-routed default arm (E2B, --set private ONLY): the same
      experiment registry plane with the preload OFF — today's product
      default (request_capability offered only). Bq−K isolates what the
      OFF default actually delivers when the knowledge is non-public.

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
        [--set verified|domain|private|horizon] [--cases multi-config-precedence,...] \
        [--arms K,N,S,P,R,F,Bp,Bq] [--repeat 3] [--parallel 4] [--max-turns 12] \
        [--registry-db-url URL] [--object-store-root DIR]

Fixture sets (--set): 'verified' (default) = the 8 §80 multi/long fixtures;
'domain' = the domain-knowledge fixtures (scripts/domain_tasks.py, verified
by scripts/verify_domain_fixtures.py) — small workspaces whose correct fix
needs knowledge a production skill carries (prompt-injection hardening,
MCP manifest conventions, brand values, design tells). The domain set is
the SKILL axis: run K vs P on it and the report records, per run, whether
the fixture's INTENDED skill (DOMAIN_INTENDED_SKILLS) was preloaded.
'private' = the private-knowledge fixtures (scripts/private_tasks.py,
verified by scripts/verify_private_fixtures.py) — the E2/E2B instrument:
each fixture's rules are a fictional internal standard that did not exist
before today, documented only in the fixture's private SKILL.md and pinned
in the tests as sha256 digests. Arms R/F/Bp/Bq require this set; run
K,R,F,Bp,Bq on it for the E2B measurement (naked vs preloaded-skill vs
file-in-repo vs registry-routed-preload vs registry-routed-default).
'horizon' = the §80 long-horizon fixtures (scripts/horizon_tasks.py,
verified by scripts/verify_horizon_fixtures.py with the STRONGER
red-when-symptom-patched pin) — 8-10 file packages, symptom-only prompts,
the bug 1-2 layers from the symptom; two fixtures carry two independent
bugs. The E1b headroom pool: run K vs N on it where the verified pack
sits at the ceiling (ADR-014 amendment 18). The PENDING H-cases
(scripts/hbench_pending_cases.py) are NOT a set here: their knobs need
scripted drivers (approval overlay, transient-failure injector,
crash/resume), not the real-model K/N contract.

Writes a JSON report to data/hbench/ (gitignored) and prints the table.
"""

import argparse
import hashlib
import json
import os
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
from horizon_tasks import HORIZON_TASKS  # noqa: E402
from private_tasks import PRIVATE_INTENDED_SKILLS, PRIVATE_TASKS  # noqa: E402
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
    PLATFORM_SANDBOX_KIND,
    ProcessSandbox,
    build_platform_default_sandbox,
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


def work_root() -> Path:
    """Where run workspaces (sources/ + runs/) are materialized. bwrap mounts
    each workspace at /workspace, so on Linux nothing above it is visible and
    the report dir is fine. macOS Seatbelt has NO mount namespace: a
    workspace under the repo sees the repo's pyproject.toml / .git as
    PARENT config (pytest rootdir discovery, git discovery) — measured: N on
    verified x flash fell to 1/8 because pytest crashed on the parent config.
    So on macOS the workspaces live under a neutral root outside any repo
    (ACI_HBENCH_WORK_ROOT overrides on any platform)."""
    override = os.environ.get("ACI_HBENCH_WORK_ROOT")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path("/tmp/aci-hbench")  # nosec B108 — neutral, not a secret store
    return REPORT_ROOT


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

#: The databases an EXPERIMENT arm (Bp/Bq) must NEVER point at: `aci` is the
#: dev/test DB (pytest fixtures accumulate there — once polluted, they ROUTED),
#: `aci_bench` is the operational corpus + telemetry. Both live on the Linux
#: box; an experiment copy (e.g. aci_e2b) is the only acceptable target.
OPERATIONAL_DATABASES = frozenset({"aci", "aci_bench"})


def database_name_from_url(url: str) -> str:
    """The database name from a SQLAlchemy URL (the last path segment)."""
    from urllib.parse import urlparse

    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def refuse_operational_database(url: str) -> str | None:
    """None when an experiment arm may use ``url``; otherwise the refusal
    reason (the runner exits 2 on it — fail closed, never a warning)."""
    name = database_name_from_url(url)
    if name in OPERATIONAL_DATABASES:
        return (
            f"refusing to point an experiment arm at the database {name!r} — "
            "aci/aci_bench are the dev-test and OPERATIONAL databases; use a "
            "disposable experiment copy (e.g. aci_e2b)"
        )
    return None


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
    # §80 horizon fixtures (scripts/horizon_tasks.py) — the long-horizon
    # exploration pack: 8-10 file packages, symptom-only prompts, the bug
    # 1-2 layers from the symptom (two fixtures carry two independent bugs).
    "horizon-ledger-reversal": "H013",
    "horizon-scheduler-order": "H013",
    "horizon-catalog-order": "H013",
    "horizon-settings-migration": "H013",
    "horizon-cache-tz": "H013",
}
PENDING_H_CASES = ["H005", "H006", "H007", "H008*", "H011"]  # *implicit via false-success


def _all_fixtures() -> list[dict[str, Any]]:
    return [*MULTI_TASKS, *LONG_TASKS]


def _fixture_set(name: str) -> list[dict[str, Any]]:
    """The fixture pack for a run. 'verified' = the 8 §80 multi/long
    fixtures (the default — every existing round's pack, unchanged);
    'domain' = the domain-knowledge fixtures (domain_tasks.py);
    'private' = the private-knowledge fixtures (private_tasks.py, the E2
    instrument — arm R's private-skill plane); 'horizon' = the §80
    long-horizon fixtures (horizon_tasks.py — 8-10 file packages,
    symptom-only prompts, verified by verify_horizon_fixtures.py with the
    STRONGER red-when-symptom-patched pin). The PENDING H-cases
    (hbench_pending_cases.py) are deliberately NOT a set here: their
    knobs (approval overlay, transient-failure injector, crash/resume
    driver) need scripted drivers, not the real-model K/N contract."""
    if name == "domain":
        return list(DOMAIN_TASKS)
    if name == "private":
        return list(PRIVATE_TASKS)
    if name == "horizon":
        return list(HORIZON_TASKS)
    return _all_fixtures()


def _intended_skill(fixture_name: str) -> str:
    """The skill whose knowledge a fixture's fix needs — the domain set's
    production skill or the private set's local one ("" elsewhere)."""
    return DOMAIN_INTENDED_SKILLS.get(fixture_name) or PRIVATE_INTENDED_SKILLS.get(fixture_name, "")


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
        #: The run-scoped client (kept public for the registry arms' evidence
        #: read-out: its ``decisions`` carry the kernel's kept/dropped ranks).
        self.client = aci

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


def build_experiment_capability_container(database_url: str, object_store_root: str) -> Any:
    """Arms Bp/Bq ONLY: the SAME registry + §14 router composition as
    build_capability_container (the REST deployment shape, the semantic
    embedder, the same Settings-default selection policy) pointed at the
    EXPERIMENT registry copy — a disposable database + its object-store
    copy, NEVER the operational aci_bench or the dev/test aci (main()
    refuses those names before this is called)."""
    from aci.adapters.inbound.rest.wiring import Container
    from aci.config import Settings

    return Container(
        Settings(
            database_url=database_url,
            object_store_root=object_store_root,
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
        process_sandbox=sandbox if sandbox is not None else build_platform_default_sandbox(),
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
    sandbox = sandbox if sandbox is not None else build_platform_default_sandbox()
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


# ---------------------------------------------------------------------------
# Arm R — private-skill arm (E2, --set private ONLY): arm K + the case's
# private skill preloaded from a LOCAL in-process capability handler.
# NO registry, NO DB: the handler is the same counting CapabilityRuntime
# over a fake ACIClient that serves exactly the fixture's private SKILL.md
# text with its real sha256 digest — the knowledge exists nowhere else,
# which is what makes R−K the E2 measurement (a skill carrying non-public
# knowledge vs the same harness without it).
# ---------------------------------------------------------------------------


class _PrivateSkillSelection:
    """One selection of the case's private skill (arm R)."""

    def __init__(
        self, skill_id: str, version: str, digest: str, estimated_context_tokens: int
    ) -> None:
        self.capability_id = skill_id
        self.version = version
        self.payload_ref = f"skill://{skill_id}@{version}/SKILL.md"
        self.digest = digest
        self.estimated_context_tokens = estimated_context_tokens


class _PrivateSkillACIClient:
    """Arm R's capability plane: LOCAL and in-process — no registry, no DB.
    Serves exactly the case's private skill text with its real sha256
    digest (the same ACIClient contract the registry client implements;
    search returns the one skill for any need — there is nothing to
    route)."""

    VERSION = "1.0.0"

    def __init__(self, *, skill_id: str, skill_text: str) -> None:
        self._skill_id = skill_id
        self._payload = skill_text.encode("utf-8")
        self._digest = hashlib.sha256(self._payload).hexdigest()
        self.searches = 0

    def search(self, request: object) -> list[object]:  # noqa: ARG002
        self.searches += 1
        return [
            _PrivateSkillSelection(
                self._skill_id,
                self.VERSION,
                self._digest,
                max(1, len(self._payload) // 4),
            )
        ]

    def resolve(self, capability_id: str, version: str) -> tuple[bytes, str]:
        if capability_id != self._skill_id or version != self.VERSION:
            raise KeyError(f"unknown capability {capability_id}@{version}")
        return self._payload, self._digest


def run_private_arm(
    fixture: dict[str, Any],
    contract: SubtaskContract,
    spec: Any,
    gateway: OpenAICompatGateway,
    sources: Path,
    runs: Path,
    *,
    max_turns: int,
    ablations: list[str] | None = None,
    sandbox: ProcessSandbox | None = None,
    trace_dir: Path | None = None,
) -> dict[str, Any]:
    """Arm R = arm K + the case's PRIVATE skill preloaded from a LOCAL
    in-process handler (no registry, no DB). The handler is the same
    counting CapabilityRuntime arm S/P wire, over a fake ACIClient serving
    exactly the fixture's skill text with its real sha256 digest; the
    kernel's run-start preload (``preload_capabilities=True``) puts it in
    context from the FIRST model request on. Everything else is arm K's
    path unchanged (fixtures, tools, sandbox, max_turns, verification,
    turn-budget note, ablations plumbing) — R−K on --set private isolates
    the private-skill contribution."""
    runtime = _skills_capability_runtime(
        lambda: _PrivateSkillACIClient(
            skill_id=str(fixture["skill_id"]), skill_text=str(fixture["skill"])
        )
    )
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
        arm="R",
        capability_factory=_Factory(runtime),
        preload_capabilities=True,
    )
    record["capability_requests"] = runtime.requests
    record["skills_loaded"] = list(runtime.loaded)
    record["skills_preloaded"] = list(runtime.preloaded)
    return record


# ---------------------------------------------------------------------------
# Arm F — file-in-repo (E2B, --set private ONLY): arm K's wiring EXACTLY, but
# the case's skill text sits in the run workspace as a plain repo document.
# NO capability plane, NO preload, NO hint in the objective — the model must
# find (or miss) the file on its own, exactly as it would in a real repo.
# ---------------------------------------------------------------------------


def file_arm_docs_path(skill_id: str) -> str:
    """Where arm F writes the case's skill text: ONE plain repo document."""
    return f"docs/standards/{skill_id}.md"


def materialize_file_arm_source(fixture: dict[str, Any], target: Path) -> Path:
    """Arm F's source root: the fixture's files byte-identical to every other
    arm's PLUS the case's skill text as one plain document at
    ``docs/standards/<skill_id>.md``. The objective is NOT changed — a real
    repo does not announce its docs, so nothing tells the model the file
    exists. Returns the fixture's source directory."""
    task_dir = target / str(fixture["name"])
    task_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in fixture["files"].items():
        path = task_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    docs = task_dir / file_arm_docs_path(str(fixture["skill_id"]))
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text(str(fixture["skill"]), encoding="utf-8")
    return task_dir


def run_file_arm(
    fixture: dict[str, Any],
    contract: SubtaskContract,
    spec: Any,
    gateway: OpenAICompatGateway,
    runs: Path,
    *,
    max_turns: int,
    sources_f: Path,
    ablations: list[str] | None = None,
    sandbox: ProcessSandbox | None = None,
    trace_dir: Path | None = None,
) -> dict[str, Any]:
    """Arm F = arm K's wiring EXACTLY (null capability plane, no preload,
    same fixtures/tools/sandbox/max_turns/verification/turn-budget note)
    with ONE difference: the run's workspace source is the F root
    (``materialize_file_arm_source``), so the case's skill text is present
    in the run workspace as a plain document at
    ``docs/standards/<skill_id>.md``. F−K isolates what a passive in-repo
    document adds over no document at all."""
    record = run_kernel_arm(
        fixture,
        contract,
        spec,
        gateway,
        sources_f,
        runs,
        max_turns=max_turns,
        ablations=ablations,
        trace_dir=trace_dir,
        sandbox=sandbox,
        arm="F",
    )
    record["docs_file"] = file_arm_docs_path(str(fixture["skill_id"]))
    return record


# ---------------------------------------------------------------------------
# Arms Bp/Bq — registry-routed (E2B, --set private ONLY): arm K + the REAL
# registry capability plane pointed at the EXPERIMENT registry copy (the
# same Container composition arms S/P use), preload ON (Bp) / OFF (Bq).
# ---------------------------------------------------------------------------


def private_skill_evidence(client: Any, skill_id: str) -> dict[str, Any]:
    """Per-run router evidence for the case's private skill, read from the
    run-scoped registry client's selection decisions: ``kept`` entries are
    what the kernel activated (in rank order), ``dropped`` entries were
    routed but narrowed out (reason code). Absent from both = never routed.
    ``private_skill_rank`` is the 1-based position among the kept entries of
    the decision that selected it (the preload decision for Bp; whichever
    model request loaded it for Bq)."""
    decisions: list[dict[str, Any]] = []
    selected = False
    rank: int | None = None
    drop_reason: str | None = None
    for decision in getattr(client, "decisions", []):
        kept = [
            {
                "capability_id": e.capability_id,
                "version": e.version,
                "score": e.score,
                "reason": e.reason,
            }
            for e in decision.kept
        ]
        dropped = [
            {
                "capability_id": e.capability_id,
                "version": e.version,
                "score": e.score,
                "reason": e.reason,
            }
            for e in decision.dropped
        ]
        decisions.append(
            {
                "kept": kept,
                "dropped": dropped,
                "scores_available": decision.scores_available,
            }
        )
        if not selected:
            for position, entry in enumerate(decision.kept, start=1):
                if entry.capability_id == skill_id:
                    selected = True
                    rank = position
        if drop_reason is None:
            drop_reason = next(
                (e.reason for e in decision.dropped if e.capability_id == skill_id), None
            )
    return {
        "private_skill_selected": selected,
        "private_skill_rank": rank,
        "private_skill_drop_reason": drop_reason,
        "capability_decisions": decisions,
    }


def run_registry_arm(
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
    arm: str = "Bp",
    preload_capabilities: bool = False,
) -> dict[str, Any]:
    """Arms Bp/Bq = arm K + the REAL registry capability plane pointed at
    the EXPERIMENT registry copy (``container`` = the experiment Container
    built by build_experiment_capability_container): the same run_kernel_arm
    path arms S/P use — one run-scoped RegistryCapabilityClient per run, the
    model OFFERED request_capability — with the kernel's run-start preload ON
    (Bp) or OFF (Bq, today's product default). Beyond arm S/P's counters the
    record carries the case's private-skill routing evidence
    (``private_skill_evidence``): selected + rank among the kept, or the
    narrowing's drop reason when routed but not kept."""
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
    record.update(private_skill_evidence(runtime.client, str(fixture["skill_id"])))
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
        run_dir,
        envelope,
        sandbox=sandbox if sandbox is not None else build_platform_default_sandbox(),
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
    sources_f: Path | None = None,
    registry_container: Any = None,
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
    elif arm == "R":
        # Arm R = arm K + the case's private skill preloaded from a LOCAL
        # in-process handler (no registry, no DB — container stays None).
        record = run_private_arm(
            fixture,
            contract,
            spec,
            gateway,
            sources,
            runs,
            max_turns=max_turns,
            ablations=ablations,
            sandbox=sandbox,
            trace_dir=trace_dir,
        )
    elif arm == "F":
        # Arm F = arm K's wiring exactly, but the workspace source is the F
        # root (fixture files + the skill text as a plain repo document).
        if sources_f is None:
            raise ValueError("arm F needs its materialized source root (sources_f)")
        record = run_file_arm(
            fixture,
            contract,
            spec,
            gateway,
            runs,
            max_turns=max_turns,
            sources_f=sources_f,
            ablations=ablations,
            sandbox=sandbox,
            trace_dir=trace_dir,
        )
    elif arm in ("Bp", "Bq"):
        # Arms Bp/Bq = arm K + the REAL registry plane pointed at the
        # EXPERIMENT registry copy; Bp preloads at run start, Bq (today's
        # product default) offers request_capability only.
        record = run_registry_arm(
            fixture,
            contract,
            spec,
            gateway,
            sources,
            runs,
            max_turns=max_turns,
            container=registry_container,
            sandbox=sandbox,
            trace_dir=trace_dir,
            arm=arm,
            preload_capabilities=arm == "Bp",
        )
    else:
        record = run_naive_arm(
            fixture, contract, spec, gateway, sources, runs, max_turns=max_turns, sandbox=sandbox
        )
    record["fixture"] = fixture["name"]
    record["h_ref"] = H_REFS.get(fixture["name"], "")
    # The skill axis: which skill this fixture's fix needs — the domain
    # set's production skill, the private set's local one, or "" (the
    # verified pack has no intended skill).
    record["intended_skill"] = _intended_skill(fixture["name"])
    return record


def _sandbox_kind(arg: str) -> str:
    """A --sandbox value → the sandbox kind ('' = the platform OS sandbox:
    bwrap on Linux — every existing round's profile; seatbelt on macOS)."""
    return arg or PLATFORM_SANDBOX_KIND


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
        choices=["verified", "domain", "private", "horizon"],
        help=(
            "fixture set: 'verified' = the 8 §80 multi/long fixtures (default), "
            "'domain' = the domain-knowledge fixtures (domain_tasks.py), "
            "'private' = the private-knowledge fixtures (private_tasks.py, the "
            "E2/E2B instrument — arms R/F/Bp/Bq's set), "
            "'horizon' = the §80 long-horizon fixtures (horizon_tasks.py — "
            "verified fail-as-shipped/pass-when-fixed/red-when-symptom-patched)"
        ),
    )
    parser.add_argument(
        "--arms",
        default="K,N",
        help="comma subset of K,N,S,P,R,F,Bp,Bq (Bp/Bq: experiment registry arms)",
    )
    parser.add_argument("--repeat", type=int, default=3, help="runs per case per arm (default 3)")
    parser.add_argument("--parallel", type=int, default=4, help="concurrent runs (default 4)")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument(
        "--registry-db-url",
        default="",
        help=(
            "EXPERIMENT registry database for arms Bp/Bq ONLY (a disposable copy "
            "like aci_e2b — NEVER aci/aci_bench, refused; required with Bp/Bq)"
        ),
    )
    parser.add_argument(
        "--object-store-root",
        default="",
        help=(
            "object-store root matching --registry-db-url for arms Bp/Bq ONLY "
            "(the experiment copy's blobs; required with Bp/Bq)"
        ),
    )
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
        choices=["bwrap", "seatbelt", "none"],
        default=PLATFORM_SANDBOX_KIND,
        help=(
            "process sandbox for BOTH arms (§16.5; default = this platform's OS "
            "sandbox: bwrap on Linux, seatbelt on macOS — the run refuses to start "
            "when it is unusable; `none` = explicit opt-out)"
        ),
    )
    parser.add_argument("--out", default="", help="report path (default data/hbench/<ts>.json)")
    args = parser.parse_args(argv)

    import os

    api_key = args.api_key or os.environ.get("ACI_AGENT_MODEL_API_KEY", "")
    if not api_key:
        print("no API key — set ACI_AGENT_MODEL_API_KEY or --api-key", file=sys.stderr)
        return 2
    arms = []
    for raw in args.arms.split(","):
        label = raw.strip().upper()
        if not label:
            continue
        # Canonical labels: the experiment arms are Bp/Bq (mixed case reads
        # better in the report than BP/BQ); everything else is uppercase.
        arms.append("Bp" if label == "BP" else "Bq" if label == "BQ" else label)
    unknown_arms = [a for a in arms if a not in ("K", "N", "S", "P", "R", "F", "Bp", "Bq")]
    if unknown_arms:
        print(f"unknown arms {unknown_arms!r} — pick from K,N,S,P,R,F,Bp,Bq", file=sys.stderr)
        return 2
    if "R" in arms and args.set != "private":
        # Arm R preloads the case's private skill (fixture['skill']) — the
        # private set is the only one that carries one.
        print("arm R needs --set private (the private-skill fixtures)", file=sys.stderr)
        return 2
    if "F" in arms and args.set != "private":
        # Arm F writes the case's skill text (fixture['skill']) into the
        # workspace — only the private set carries one.
        print("arm F needs --set private (the private-skill fixtures)", file=sys.stderr)
        return 2
    if "Bp" in arms or "Bq" in arms:
        if args.set != "private":
            print(
                "arms Bp/Bq need --set private (they route the private-skill fixtures)",
                file=sys.stderr,
            )
            return 2
        if not args.registry_db_url or not args.object_store_root:
            print(
                "arms Bp/Bq need BOTH --registry-db-url and --object-store-root "
                "(the EXPERIMENT registry copy + its blobs — never a guess)",
                file=sys.stderr,
            )
            return 2
        refusal = refuse_operational_database(args.registry_db_url)
        if refusal is not None:
            print(refusal, file=sys.stderr)
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
    # Arms Bp/Bq registry plane: the EXPERIMENT copy (a SEPARATE Container —
    # both can coexist; S/P keep aci_bench untouched).
    registry_container = None
    if "Bp" in arms or "Bq" in arms:
        registry_container = build_experiment_capability_container(
            args.registry_db_url, args.object_store_root
        )
        print(
            "arm Bp/Bq: EXPERIMENT registry capability plane wired "
            f"(database {database_name_from_url(args.registry_db_url)!r}, "
            "fastembed, one run-scoped client per run)",
            flush=True,
        )
    # ONE sandbox instance for the whole pack: both arms (and both post-hoc
    # yardsticks) execute model-written code under the identical profile.
    # '' (the default) = the PLATFORM OS sandbox (bwrap on Linux — every
    # existing round's profile; seatbelt on macOS) — an explicit --sandbox
    # value overrides, and an unusable sandbox still fails closed below.
    sandbox_kind = _sandbox_kind(args.sandbox)
    sandbox = build_process_sandbox(sandbox_kind)
    unusable = sandbox.unavailable_reason()
    if unusable is not None:
        print(f"sandbox unusable: {unusable} — fix it or pass --sandbox none", file=sys.stderr)
        return 2
    if sandbox_kind == "none":
        print("SANDBOX OFF (--sandbox none): model-written code runs as you", flush=True)
    fixtures = _fixture_set(args.set)
    if args.cases:
        wanted = {c.strip() for c in args.cases.split(",")}
        fixtures = [f for f in fixtures if f["name"] in wanted]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = work_root() / stamp
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
    # Arm F's source root: the fixture files + the case's skill text as ONE
    # plain repo document (materialized once, before the pool starts — the
    # per-run workspace copies come from provision_workspace as usual).
    sources_f: Path | None = None
    if "F" in arms:
        sources_f = root / "sources-f"
        for fixture in fixtures:
            materialize_file_arm_source(fixture, sources_f)

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
                sources_f=sources_f,
                registry_container=registry_container,
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
                    "intended_skill": _intended_skill(fixture["name"]),
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
                    # The experiment arms' honest zeros (a crashed run routed
                    # nothing, preloaded nothing, wrote no docs file).
                    "skills_preloaded": [],
                    "private_skill_selected": False,
                    "private_skill_rank": None,
                    "private_skill_drop_reason": None,
                    "capability_decisions": [],
                    "docs_file": None,
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
        "sandbox": sandbox_kind,
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
            "N shares K's prompt base minus the capability protocol. "
            "E2B arms (--set private): F = K's wiring + the case's skill text "
            "as a plain repo document (objective unchanged); Bp = K + the "
            "EXPERIMENT registry plane with the preload ON; Bq = the same "
            "plane with the preload OFF (today's product default). This "
            "round carries the turn-budget note and the "
            f"{args.sandbox} sandbox — NOT comparable to rounds <= 3."
        ),
    }
    if container is not None:
        report["arm_s_registry"] = {
            "database": "aci_bench (operational)",
            "embedder": "fastembed",
            "object_store_root": str(ACI_OBJECT_STORE_ROOT),
            "selection_policy": "Settings defaults (agent_capability_policy)",
        }
    if registry_container is not None:
        report["arm_bp_bq_registry"] = {
            "database": database_name_from_url(args.registry_db_url),
            "embedder": "fastembed",
            "object_store_root": str(Path(args.object_store_root)),
            "selection_policy": "Settings defaults (agent_capability_policy)",
            "note": (
                "EXPERIMENT copy (disposable) — the private skills were ingested "
                "by scripts/e2b_setup_registry.py; S/P above stay on aci_bench"
            ),
        }
    if sources_f is not None:
        report["arm_f_docs"] = {
            "path": "docs/standards/<skill_id>.md (in the run workspace)",
            "objective_changed": False,
        }
    if args.set == "domain":
        # The skill axis: which production skill each fixture's fix needs —
        # the report answers "was the INTENDED skill preloaded?" per run.
        report["intended_skills"] = dict(DOMAIN_INTENDED_SKILLS)
    elif args.set == "private":
        # The E2 skill axis: which PRIVATE skill each fixture's fix needs —
        # documented only in the skill, pinned in the tests as digests.
        report["intended_skills"] = dict(PRIVATE_INTENDED_SKILLS)
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
