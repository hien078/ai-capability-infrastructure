"""Independent adversarial security review of the agent-run surface (job m5).

Attacker model: a client with network access to the port. Every finding is
assessed under BOTH deployment modes — tokens EMPTY (the localhost-only
assumption: any local process is the attacker) and tokens SET (a remote
caller holding the bearer token).

Findings, ranked by severity (each test names the finding it pins):

- ADV-1  The sandbox-unavailable reason interpolated RAW bwrap stderr into a
  caller-visible 403 (``PERMISSION_DENIED`` is a 4xx, so the raw text goes on
  the wire) — and bwrap's stderr names server paths (a failed bind source,
  the probe tempdir). FIXED here: the hint is path-scrubbed.
- ADV-2  The verifier's raw command output is echoed verbatim into the HTTP
  response ``summary`` (``VerificationManager`` repair hints →
  ``RunResult.summary``); only the run dir is scrubbed, so any OTHER server
  path the command prints (e.g. ``sys.prefix``) reaches the client — a
  client-controlled read-back channel (the client picks the
  ``verification_command``). XFAIL(strict): the fix is a wire-surface change
  (decouple the client-facing summary from the model-facing repair hints);
  see the result file.
- ADV-3  An approval binds only the GATED call (call id + operation hash);
  the rest of the paused batch is not hash-bound, so a tampered checkpoint
  can smuggle extra calls past a one-shot approval (authority is still
  evaluated per call — the smuggle is bounded by grants, but the
  "approve = exactly the pending calls once" integrity contract is broken).
  XFAIL(strict): the fix is a checkpoint-schema change (bind every pending
  call); see the result file.
- ADV-4  A workspace NAME that is a symlink escaped the workspace root: the
  per-run copy contained the symlink target's tree, where the model can read
  it (``read_file``) and echo it into the client-visible summary. Requires an
  operator-created (or locally planted) symlink inside
  ``ACI_AGENT_WORKSPACE_ROOT`` — defense in depth. FIXED here: the name must
  resolve inside the root.
- ADV-5  ``command_within_prefixes`` admitted ``command[0] == prefix.strip()``
  — a multi-token prefix also admitted a DIFFERENT program whose NAME is the
  joined prefix string. Latent fail-open in the process-scope check (not
  exploitable as deployed: PATH is absolute-only and never contains the
  workspace). FIXED here: token-aware matching only.
- ADV-6  Unbounded request fields (``global_context``, ``constraints``,
  ``acceptance_criteria``, ``verification_command``, revise
  ``feedback``/``failed_criteria``) — a multi-megabyte field flowed into
  every model request and the durable store (the objective was already
  capped at 8000). FIXED here: Field caps at the edge.

Reviewed-and-holds pins (attack surface examined, the boundary holds — no
finding): bwrap never re-parses command elements as options; ``write_scopes``
traversal is refused at the edge; resume-after-cancel is CHECKPOINT_CONSUMED;
an answer cannot satisfy an approval pause; a client budget cannot raise the
turn ceiling past the request cap.

Deterministic: scripted model, temp directories, no network, no live DB.
"""

import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aci.runtime.sandbox as sandbox_mod
from aci.adapters.inbound.rest import agent_runs as rest_agent_runs
from aci.adapters.inbound.rest.errors import register_error_handlers
from aci.adapters.inbound.rest.wiring import Container, Settings
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.domain.runtime.actions import (
    FinalCandidate,
    ToolCall,
    ToolCallBatchAction,
)
from aci.domain.runtime.authority import ApprovalDecision, FilesystemScope, GrantEnvelope
from aci.domain.runtime.evidence import CheckResult, ResultContract
from aci.domain.runtime.spec import LoopPolicy, RuntimeSpec
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.subtask import SubtaskContract
from aci.domain.runtime.tools import SideEffectReport, ToolAuthority, ToolSpec
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.model_gateway import FakeModelGateway, ModelRequest, ModelResponse, ModelUsage
from aci.runtime.protocols import ToolDispatchResult
from aci.runtime.recovery import RecoveryManager
from aci.runtime.run_controller import HarnessKernel
from aci.runtime.sandbox import BwrapSandbox, NoSandbox, ProcessSandbox
from aci.runtime.state_manager import StateManager
from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime
from aci.runtime.verification import VerificationManager, VerifierCallable
from aci.runtime.workspace import command_within_prefixes
from tests.sandbox_support import available_sandbox

PY = sys.executable
WORKSPACE = "proj"
README = "# demo project\n"
APP = "print('hello')\n"
GREET = "def greet():\n    return 'hi'\n"
#: The kernel grants up to two repair turns after a failed verification.
REPAIR_TURNS = 3


# -- fixtures: a workspace root with one project, an empty runs root ---------


class Roots:
    """Server-side directories: ``workspaces/proj`` (the source) + ``runs``."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.workspace_root = tmp_path / "workspaces"
        self.runs_root = tmp_path / "runs"
        self.source = self.workspace_root / WORKSPACE
        (self.source / "src").mkdir(parents=True)
        (self.source / "src" / "app.py").write_text(APP, encoding="utf-8")
        (self.source / "README.md").write_text(README, encoding="utf-8")

    def run_dirs(self) -> list[Path]:
        if not self.runs_root.exists():
            return []
        return sorted(self.runs_root.iterdir())

    def run_dir(self, run_id: str) -> Path:
        path = self.runs_root / run_id
        assert path.is_dir(), f"no run dir for {run_id}; runs root holds {self.run_dirs()}"
        return path


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    return Roots(tmp_path)


class _Factory:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def build(self) -> object:
        return self._obj


class _NullCapabilities:
    def handle_request(self, request: object, snapshot: object) -> list[object]:
        return []


def _service(
    script: list[Any],
    roots: Roots,
    *,
    process_prefixes: list[str] | None = None,
    process_sandbox: ProcessSandbox | None = None,
) -> tuple[AgentRunService, FakeModelGateway]:
    """The service as the server wires it, with an EXPLICIT sandbox (the
    adversarial cases vary it: a failing bwrap probe, the honest opt-out)."""
    gateway = FakeModelGateway(script)
    service = AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _Factory(gateway)),
        tool_executor_factory=cast(ModelGatewayFactory, _Factory(None)),
        capability_runtime_factory=cast(ModelGatewayFactory, _Factory(_NullCapabilities())),
        context_engine_factory=cast(
            ModelGatewayFactory, _Factory(ContextEngine(ContextBudget(total_tokens=60_000)))
        ),
        workspace_root=str(roots.workspace_root),
        runs_root=str(roots.runs_root),
        process_prefixes=[PY] if process_prefixes is None else process_prefixes,
        command_timeout_seconds=30,
        verification_timeout_seconds=30,
        # Real bwrap where usable (CI); the explicit opt-out elsewhere — the
        # sandbox itself is tests/security/test_process_sandbox.py.
        process_sandbox=process_sandbox if process_sandbox is not None else available_sandbox(),
    )
    return service, gateway


def _client(service: AgentRunService) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    container = cast(
        Container,
        type(
            "C",
            (),
            {
                "agent_run_service": service,
                # The exposure gate reads the token off container settings;
                # default = unauthenticated mode (these tests never set one).
                "settings": Settings(agent_runs_token=""),
            },
        )(),
    )
    app.dependency_overrides[rest_agent_runs.get_container] = lambda: container
    app.include_router(rest_agent_runs.router)
    return TestClient(app)


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "objective": "add a greeting module under src/",
        "acceptance_criteria": ["src/greet.py exists"],
        "workspace": WORKSPACE,
        "write_scopes": ["src"],
    }
    body.update(overrides)
    return body


def _write(call_id: str, path: str, content: str = "x = 1\n") -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[
            ToolCall(
                call_id=call_id, tool_id="write_file", arguments={"path": path, "content": content}
            )
        ]
    )


def _done(*changes: str, summary: str = "done") -> FinalCandidate:
    return FinalCandidate(summary=summary, changes=list(changes))


def _done_repeatedly(*changes: str) -> list[FinalCandidate]:
    return [_done(*changes)] * REPAIR_TURNS


def _fake_failing_bwrap(directory: Path, stderr_line: str) -> str:
    """An executable that behaves like a BROKEN bwrap: it prints one line to
    stderr and exits nonzero — never isolating anything."""
    script = directory / "bwrap-broken"
    script.write_text(
        f"#!/bin/sh\necho {shlex.quote(stderr_line)} >&2\nexit 3\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


# -- ADV-1: the sandbox refusal reason must carry no server path -------------
#
# `unavailable_reason()`'s contract says "caller-safe reason (no server
# paths)", and PERMISSION_DENIED is a 4xx — the raw message goes on the wire
# (403 up front when a verification_command is given). bwrap's own stderr can
# name server paths (a failed bind source, the probe tempdir), so the hint it
# interpolated was a server-path leak into the 403 body.


def test_adv1_probe_failure_reason_carries_no_server_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "operator-venv-location"
    monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
    broken = _fake_failing_bwrap(
        tmp_path, f"bwrap: Can't open source file {secret}: No such file or directory"
    )
    sandbox = BwrapSandbox(bwrap_path=broken, prlimit_path=broken)
    reason = sandbox.unavailable_reason()
    assert reason is not None
    assert str(secret) not in reason
    assert str(tmp_path) not in reason


def test_adv1_sandbox_refusal_on_the_wire_carries_no_server_path(
    roots: Roots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 403 a client gets when the sandbox is unusable must not carry the
    server paths a broken bwrap printed — under token mode this is the only
    diagnostic a remote caller ever sees."""
    monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
    secret = tmp_path / "operator-venv-location"
    broken = _fake_failing_bwrap(
        tmp_path, f"bwrap: Can't open source file {secret}: No such file or directory"
    )
    service, gateway = _service(
        [_done("src/app.py")],
        roots,
        process_sandbox=BwrapSandbox(bwrap_path=broken, prlimit_path=broken),
    )
    response = _client(service).post(
        "/v1/agent-runs", json=_body(verification_command=[PY, "-c", "pass"])
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert str(secret) not in response.text
    assert str(tmp_path) not in response.text
    assert str(roots.runs_root) not in response.text
    # Fail closed: nothing was provisioned, the model was never invoked.
    assert roots.run_dirs() == []
    assert gateway.requests == []


# -- ADV-2: the verifier's raw output must not echo server paths to the client
#
# The client picks verification_command; its stdout/stderr (1500 chars) flows
# through CheckResult.detail → repair_hints → RunResult.summary → the HTTP
# response. Only the run dir is scrubbed, so any other server path the
# command prints (sys.prefix — the server's repo/venv location) reaches the
# client. Under the default bwrap sandbox the visible filesystem is /usr +
# the interpreter prefixes + the run's own copy, so the leak is layout
# disclosure; under ACI_AGENT_SANDBOX=none it is a read-back of ANY server
# file the command can read.


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ADV-2: the verifier's raw command output reaches the client via "
        "RunResult.summary with only the run dir scrubbed — sys.prefix (the "
        "server's venv/repo location) leaks. Fix = decouple the client-facing "
        "summary from the model-facing repair hints (the model keeps the full "
        "output as repair feedback; the client gets check names), a wire-surface "
        "change — see the m5 result file."
    ),
)
def test_adv2_failed_verification_summary_does_not_echo_server_paths(roots: Roots) -> None:
    """A failing verification command that prints its interpreter prefix (as
    test runners print absolute paths) must not push that server path into
    the client-facing summary."""
    leaky = [PY, "-c", "import sys; print('PREFIX=' + sys.prefix); sys.exit(1)"]
    script = [_write("c1", "src/greet.py", GREET), *_done_repeatedly("src/greet.py")]
    service, _ = _service(script, roots)
    data = _client(service).post("/v1/agent-runs", json=_body(verification_command=leaky)).json()
    assert data["status"] == "failed"
    assert data["stop_reason"] == "VERIFICATION_FAILED"
    assert "FAIL:verification_command_passes" in data["checks"]
    assert sys.prefix not in data["summary"]
    assert str(Path(sys.prefix).parent) not in data["summary"]


# -- ADV-3: an approval must bind the WHOLE pending batch ---------------------
#
# validate_for_resume binds calls[0] (the gated call) via gated_call_id +
# operation_hash; the trailing calls of the paused batch are unbound. A
# tampered checkpoint (store access) that keeps the gated call intact but
# swaps a trailing call gets it executed by the approved resume — the client
# approved ONE operation, N ran. Authority still evaluates every call, so the
# smuggle is grants-bounded, but the approval-integrity contract ("approve =
# exactly the pending calls once") is broken.


class _ScriptedModel:
    def __init__(self, actions: list[Any]) -> None:
        self._actions = list(actions)
        self.requests: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        assert self._actions, "scripted model exhausted — the kernel should have stopped"
        return ModelResponse(
            action=self._actions.pop(0),
            usage=ModelUsage(input_tokens=100, output_tokens=50, latency_ms=1),
        )


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(self, tool: ToolSpec, args: dict[str, Any], envelope: Any) -> ToolDispatchResult:
        self.calls.append((tool.tool_id, dict(args)))
        changed = [f"file:{args['path']}"] if tool.side_effect_class != "READ_ONLY" else []
        return ToolDispatchResult(
            output=f"{tool.tool_id} ok",
            side_effects=SideEffectReport(
                state="confirmed" if changed else "none", resources_changed=changed
            ),
            duration_ms=1,
        )


def _fs_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            tool_id="fs.read",
            version="1",
            input_schema={"properties": {"path": {"type": "string"}}},
            side_effect_class="READ_ONLY",
            idempotency_class="IDEMPOTENT",
            authority_requirements=ToolAuthority(read_path_args=["path"]),
        ),
        ToolSpec(
            tool_id="fs.write",
            version="1",
            input_schema={"properties": {"path": {"type": "string"}}},
            side_effect_class="LOCAL_MUTATION",
            authority_requirements=ToolAuthority(write_path_args=["path"]),
        ),
    ]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ADV-3: the approval binds only the gated call (gated_call_id + "
        "operation_hash); a tampered checkpoint swaps a TRAILING call of the "
        "paused batch and the approved resume executes it. Fix = bind every "
        "pending call (per-call hashes or a batch hash on PendingInterrupt, "
        "CHECKPOINT_SCHEMA_VERSION-aware) — a checkpoint-schema change, see "
        "the m5 result file."
    ),
)
def test_adv3_tampered_trailing_call_of_a_paused_batch_never_executes() -> None:
    run_id = "run-adv3"
    dispatcher = _RecordingDispatcher()
    registry = ToolRegistry()
    for tool in _fs_tools():
        registry.register(tool)
    model = _ScriptedModel(
        [
            ToolCallBatchAction(
                calls=[
                    ToolCall(call_id="w1", tool_id="fs.write", arguments={"path": "src/a.py"}),
                    ToolCall(call_id="r1", tool_id="fs.read", arguments={"path": "README.md"}),
                ]
            )
        ]
    )
    kernel = HarnessKernel(
        state=StateManager(),
        model_gateway=model,
        tool_executor=ToolRuntime(registry, dispatcher),
        context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=VerificationManager(
            [VerifierCallable("always", lambda s, c: CheckResult(name="always", passed=True))]
        ),
        recovery=RecoveryManager(),
        capability_runtime=object(),
        approval_required_tools=("fs.write",),
    )
    spec = RuntimeSpec(
        result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
        loop_policy=LoopPolicy(),
        budget=BudgetLedger(),
        initial_grants=GrantEnvelope(filesystem=FilesystemScope(read=["."], write=["src"])),
        created_at=datetime.now(UTC),
    )
    contract = SubtaskContract(task_id=run_id, objective="edit src", created_at=datetime.now(UTC))
    paused = kernel.run(contract, spec, max_turns=10)
    assert paused.status.value == "interrupted_approval"
    checkpoint = kernel.pending_checkpoint(run_id)
    assert checkpoint is not None and checkpoint.pending is not None
    gated, trailing = checkpoint.pending.calls
    assert gated.call_id == "w1" and trailing.call_id == "r1"

    # Store tampering: the gated call stays byte-identical (the hash binds
    # it); the TRAILING read is swapped for a different path.
    smuggled = ToolCall(call_id="r1", tool_id="fs.read", arguments={"path": "server-secret.txt"})
    tampered = checkpoint.model_copy(
        update={"pending": checkpoint.pending.model_copy(update={"calls": [gated, smuggled]})}
    )
    kernel.resume(
        tampered,
        approval=ApprovalDecision(
            approval_id=paused.approval_id,
            approved=True,
            decided_by="client",
            decided_at=datetime.now(UTC),
        ),
    )
    # The approved gated call ran; the smuggled trailing call did NOT.
    assert ("fs.read", {"path": "server-secret.txt"}) not in dispatcher.calls
    assert ("fs.read", {"path": "README.md"}) in dispatcher.calls


def test_adv3_positive_control_the_honest_trailing_call_runs() -> None:
    """Not a finding: the design (ADR-014 amendment 14) executes the whole
    unexecuted rest of the paused batch on approve — the trailing call the
    MODEL emitted in the same message runs. This pins that the xfail above is
    about TAMPERING, not about the trailing call existing."""
    run_id = "run-adv3b"
    dispatcher = _RecordingDispatcher()
    registry = ToolRegistry()
    for tool in _fs_tools():
        registry.register(tool)
    batch = ToolCallBatchAction(
        calls=[
            ToolCall(call_id="w1", tool_id="fs.write", arguments={"path": "src/a.py"}),
            ToolCall(call_id="r1", tool_id="fs.read", arguments={"path": "README.md"}),
        ]
    )
    model = _ScriptedModel([batch, FinalCandidate(summary="done")])
    kernel = HarnessKernel(
        state=StateManager(),
        model_gateway=model,
        tool_executor=ToolRuntime(registry, dispatcher),
        context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=VerificationManager(
            [VerifierCallable("always", lambda s, c: CheckResult(name="always", passed=True))]
        ),
        recovery=RecoveryManager(),
        capability_runtime=object(),
        approval_required_tools=("fs.write",),
    )
    spec = RuntimeSpec(
        result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
        loop_policy=LoopPolicy(),
        budget=BudgetLedger(),
        initial_grants=GrantEnvelope(filesystem=FilesystemScope(read=["."], write=["src"])),
        created_at=datetime.now(UTC),
    )
    contract = SubtaskContract(task_id=run_id, objective="edit src", created_at=datetime.now(UTC))
    paused = kernel.run(contract, spec, max_turns=10)
    checkpoint = kernel.pending_checkpoint(run_id)
    assert checkpoint is not None
    resumed = kernel.resume(
        checkpoint,
        approval=ApprovalDecision(
            approval_id=paused.approval_id,
            approved=True,
            decided_by="client",
            decided_at=datetime.now(UTC),
        ),
    )
    assert resumed.status.value == "succeeded", resumed
    assert dispatcher.calls == [
        ("fs.write", {"path": "src/a.py"}),
        ("fs.read", {"path": "README.md"}),
    ]


# -- ADV-4: a workspace NAME that is a symlink must not escape the root -------


def test_adv4_symlinked_workspace_name_cannot_escape_the_root(roots: Roots) -> None:
    """A symlinked name under the workspace root pointed at a server
    directory OUTSIDE it: the run's working copy used to contain the target
    tree, where the model can read it and echo it into the client-visible
    summary. The name must be refused — no copy, no run, no path on the wire."""
    outside = roots.tmp / "outside-secret"
    outside.mkdir()
    (outside / "SECRET.md").write_text("server secret\n", encoding="utf-8")
    (roots.workspace_root / "escape").symlink_to(outside)

    service, gateway = _service([_done("src/app.py")], roots)
    response = _client(service).post("/v1/agent-runs", json=_body(workspace="escape"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"
    assert str(outside) not in response.text
    assert str(roots.workspace_root) not in response.text
    assert roots.run_dirs() == []
    assert gateway.requests == []
    leaked = list(roots.runs_root.rglob("SECRET.md")) if roots.runs_root.exists() else []
    assert leaked == []


def test_adv4_symlinked_workspace_name_within_the_root_still_works(roots: Roots) -> None:
    """Positive control: the fix must not forbid deployment convenience
    links — a name that resolves INSIDE the workspace root provisions as
    before (the boundary is escape, not symlinks)."""
    (roots.workspace_root / "real").mkdir()
    (roots.workspace_root / "real" / "src").mkdir()
    (roots.workspace_root / "real" / "src" / "app.py").write_text(APP, encoding="utf-8")
    (roots.workspace_root / "link").symlink_to(roots.workspace_root / "real")

    service, _ = _service([_write("c1", "src/greet.py", GREET), _done("src/greet.py")], roots)
    data = _client(service).post("/v1/agent-runs", json=_body(workspace="link")).json()
    assert data["status"] == "succeeded", data
    run_dir = roots.run_dir(data["run_id"])
    assert (run_dir / "src" / "greet.py").read_text(encoding="utf-8") == GREET


# -- ADV-5: the process scope must be token-aware ONLY -------------------------


def test_adv5_multitoken_prefix_never_admits_the_joined_program_name() -> None:
    """``command[0] == prefix.strip()`` admitted a program whose NAME is the
    joined prefix string — a different program than the token sequence the
    prefix declares. The scope check must be token-aware only."""
    assert not command_within_prefixes(["python -m pytest", "--evil"], ["python -m pytest"])
    # Positive controls: the token sequence itself still matches, and the
    # classic fail-closed shapes still hold.
    assert command_within_prefixes(["python", "-m", "pytest", "-q"], ["python -m pytest"])
    assert not command_within_prefixes(["python", "-c", "import os"], ["python -m pytest"])
    assert not command_within_prefixes(["pytest-evil"], ["pytest"])
    assert not command_within_prefixes(["pytest"], [])


def test_adv5_verification_command_cannot_smuggle_the_joined_name(roots: Roots) -> None:
    """End to end: a verification_command whose argv[0] is the joined
    prefix string is outside the run's process scope — refused at the edge
    (403), nothing provisioned, the model never invoked."""
    joined = "python -m pytest"
    service, gateway = _service([_done("src/app.py")], roots)
    response = _client(service).post(
        "/v1/agent-runs",
        json=_body(command_prefixes=[joined], verification_command=[joined, "-q"]),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert roots.run_dirs() == []
    assert gateway.requests == []


# -- ADV-6: request fields are bounded at the edge ----------------------------


def test_adv6_huge_global_context_is_refused_before_anything_runs(roots: Roots) -> None:
    """global_context had NO length cap (the objective did): a
    multi-megabyte field flowed into every model request and the durable
    store. Refused at the edge now: 422, nothing provisioned."""
    service, gateway = _service([_done("src/app.py")], roots)
    response = _client(service).post("/v1/agent-runs", json=_body(global_context="x" * 300_000))
    assert response.status_code == 422
    assert roots.run_dirs() == []
    assert gateway.requests == []


def test_adv6_huge_revise_feedback_is_refused_at_the_edge(roots: Roots) -> None:
    service, _ = _service([_write("c1", "src/greet.py", GREET), _done("src/greet.py")], roots)
    client = _client(service)
    first = client.post("/v1/agent-runs", json=_body()).json()
    assert first["status"] == "succeeded", first
    response = client.post(
        f"/v1/agent-runs/{first['run_id']}/revise", json={"feedback": "x" * 300_000}
    )
    assert response.status_code == 422
    assert roots.run_dirs() == [roots.run_dir(first["run_id"])]


@pytest.mark.parametrize(
    "field",
    [
        {"constraints": ["x" * 100_000]},
        {"acceptance_criteria": ["ok"] * 10_000},
        {"verification_command": [PY] + ["-v"] * 10_000},
        {"command_prefixes": [PY, "x" * 100_000]},
        {"write_scopes": ["src"] * 10_000},
    ],
)
def test_adv6_unbounded_list_and_item_fields_are_refused(
    roots: Roots, field: dict[str, Any]
) -> None:
    """Every list-shaped authority/context field is capped (items and length):
    a huge one is 422 at the edge, before any workspace copy exists."""
    service, gateway = _service([_done("src/app.py")], roots)
    response = _client(service).post("/v1/agent-runs", json=_body(**field))
    assert response.status_code == 422
    assert roots.run_dirs() == []
    assert gateway.requests == []


# -- reviewed-and-holds pins (no finding; the boundary holds) -----------------


def test_pin_bwrap_never_reparses_command_elements_as_its_own_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sandbox argv construction: everything after bwrap's `--` (and after
    prlimit's own `--`) is exec'd verbatim — a command element that LOOKS
    like a bwrap option can never become one (no argv injection)."""
    monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
    prefix = tmp_path / "interp"
    (prefix / "bin").mkdir(parents=True)
    sandbox = BwrapSandbox(
        bwrap_path="/opt/x/bwrap", prlimit_path="/opt/x/prlimit", ro_prefixes=[str(prefix)]
    )
    hostile = ["--ro-bind", "/etc", "/etc", "--unshare-all", "sh", "-c", "id"]
    argv = sandbox.build_argv(hostile, tmp_path)
    separators = [i for i, a in enumerate(argv) if a == "--"]
    assert len(separators) == 2  # bwrap's, then prlimit's
    assert argv[separators[1] + 1 :] == hostile  # exec'd verbatim, never re-parsed


def test_pin_write_scopes_traversal_is_refused_at_the_edge(roots: Roots) -> None:
    """Reviewed (grant widening via request fields): a write_scopes entry
    that escapes the workspace is CLIENT_INCOMPATIBLE before anything is
    provisioned — the model never gets a chance to write through it."""
    service, gateway = _service([_done("src/app.py")], roots)
    response = _client(service).post(
        "/v1/agent-runs", json=_body(write_scopes=["../outside", "src/../../etc"])
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CLIENT_INCOMPATIBLE"
    assert roots.run_dirs() == []
    assert gateway.requests == []


def test_pin_resume_after_cancel_is_checkpoint_consumed(roots: Roots) -> None:
    """Reviewed (approval replay): cancel of a paused run CLAIMS the pause —
    a later resume (even with the right approval id) is CHECKPOINT_CONSUMED,
    never a second execution."""
    service, _ = _service([_write("c1", "src/greet.py", GREET), _done("src/greet.py")], roots)
    client = _client(service)
    paused = client.post(
        "/v1/agent-runs", json=_body(approval_required_tools=["write_file"])
    ).json()
    assert paused["status"] == "interrupted_approval"
    assert client.post(f"/v1/agent-runs/{paused['run_id']}/cancel").json() == {"cancelled": True}
    resumed = client.post(
        f"/v1/agent-runs/{paused['run_id']}/resume",
        json={"approval_id": paused["approval_id"], "approve": True},
    )
    assert resumed.status_code == 409
    assert resumed.json()["error"]["code"] == "CHECKPOINT_CONSUMED"
    assert not (roots.run_dir(paused["run_id"]) / "src" / "greet.py").exists()


def test_pin_an_answer_cannot_satisfy_an_approval_pause(roots: Roots) -> None:
    """Reviewed (approval forgery): a clarification answer never satisfies
    an approval pause — the refusal is typed and does not burn the pause."""
    service, _ = _service([_write("c1", "src/greet.py", GREET), _done("src/greet.py")], roots)
    client = _client(service)
    paused = client.post(
        "/v1/agent-runs", json=_body(approval_required_tools=["write_file"])
    ).json()
    wrong = client.post(f"/v1/agent-runs/{paused['run_id']}/resume", json={"answer": "just do it"})
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "CLIENT_INCOMPATIBLE"
    # The refusal did not burn the one resume.
    ok = client.post(
        f"/v1/agent-runs/{paused['run_id']}/resume",
        json={"approval_id": paused["approval_id"], "approve": True},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "succeeded", ok.json()


def test_pin_a_client_budget_cannot_raise_the_turn_ceiling(roots: Roots) -> None:
    """Reviewed (grant widening via request fields): the budget is
    client-owned, but a huge budget.max_turns cannot bypass the run-level
    turn cap (the request cap is <= 200; the default is 40) — the run still
    stops at LIMIT_TURNS."""
    from aci.domain.runtime.actions import ContinueAction

    model = FakeModelGateway([ContinueAction()] * 60)
    service = AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _Factory(model)),
        tool_executor_factory=cast(ModelGatewayFactory, _Factory(None)),
        capability_runtime_factory=cast(ModelGatewayFactory, _Factory(_NullCapabilities())),
        context_engine_factory=cast(
            ModelGatewayFactory, _Factory(ContextEngine(ContextBudget(total_tokens=60_000)))
        ),
        workspace_root=str(roots.workspace_root),
        runs_root=str(roots.runs_root),
        process_prefixes=[PY],
        process_sandbox=NoSandbox(),
    )
    data = (
        _client(service)
        .post(
            "/v1/agent-runs",
            json=_body(budget={"max_turns": 10**9, "max_total_tokens": 10**12}),
        )
        .json()
    )
    assert data["status"] == "failed"
    assert data["stop_reason"] == "LIMIT_TURNS"
    assert data["turns"] <= 200
