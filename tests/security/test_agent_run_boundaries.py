"""§61 security cases for the agent-run wire surface (ADR-014, harness.md
§12/§16/§19/§29): ``POST /v1/agent-runs`` with workspace tools — the first
surface where a model-driven run touches server files and processes.

Load-bearing claims, enforced as tests:

- A workspace is NAMED, never addressed: the request carries the name of a
  subdirectory of the server's workspace root; a path (relative, absolute,
  nested, empty) is refused before anything is provisioned.
- Authority is a ceiling the client cannot widen (INV-02): a verification
  command or command prefix outside the server's process ceiling is refused
  at the edge — before the model is invoked, before anything runs.
- Every side effect goes through the tool path (INV-06) and stays inside the
  run's own copy of the workspace (§16.2): traversal/absolute paths, writes
  outside ``write_scopes`` and argv outside the process scope are denied
  observations, never effects; without a process scope ``run_command`` is
  not even advertised.
- Workspace processes never inherit the server environment (§16.4).
- The wire carries workspace-relative refs and typed codes — never the
  server's absolute paths, never raw exception text.
- Completion is evidence-gated at the wire (INV-08): a claimed change with
  no observed effect is VERIFICATION_FAILED.
- A revision continues the SAME lineage: it works on a copy of the previous
  run's directory and can neither switch workspace nor widen grants.
- After a restart (a run known only to the durable store, migration 0018)
  the server-side working-copy path stays server-side: no GET/revise/cancel
  response — success or error — ever carries it.

Deterministic: scripted model, temp directories, no network, no live DB.
"""

import json
import shutil
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest import agent_runs as rest_agent_runs
from aci.adapters.inbound.rest.errors import register_error_handlers
from aci.adapters.inbound.rest.wiring import Container, Settings
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import FinalCandidate, ToolCall, ToolCallBatchAction
from aci.domain.runtime.persistence import AgentRunEventRecord, AgentRunRecord
from aci.runtime.model_gateway import FakeModelGateway
from tests.sandbox_support import available_sandbox

PY = sys.executable
WORKSPACE = "proj"
README = "# demo project\n"
APP = "print('hello')\n"
GREET = "def greet():\n    return 'hi'\n"
SECRET = "sk-secret-sentinel"
#: Three identical candidates: the kernel grants up to two repair turns after
#: a failed verification, and a scripted gateway must never run dry.
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
        # The runs root also holds the server's start-manifest dir (the
        # change read model) — infrastructure, never a run dir.
        return sorted(p for p in self.runs_root.iterdir() if p.name != ".aci-run-manifests")

    def run_dir(self, run_id: str) -> Path:
        path = self.runs_root / run_id
        assert path.is_dir(), f"no run dir for {run_id}; runs root holds {self.run_dirs()}"
        return path


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    return Roots(tmp_path)


class _GatewayFactory:
    def __init__(self, gateway: FakeModelGateway) -> None:
        self.gateway = gateway

    def build(self) -> object:
        return self.gateway


class _NoWorkspaceToolFactory:
    """Tool executor for runs WITHOUT a workspace; a workspace run must get the
    standard workspace tools from the service, never from this seam."""

    def build(self) -> object:
        from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime

        class _NoDispatch:
            def dispatch(self, tool: object, args: object, envelope: object) -> object:
                raise DomainError(ErrorCode.TOOL_EXECUTION_FAILED, "no dispatcher")

        return ToolRuntime(ToolRegistry(), dispatcher=cast("Any", _NoDispatch()))


class _NullCapabilityFactory:
    def build(self) -> object:
        class _None:
            def handle_request(self, request: object, snapshot: object) -> list[object]:
                return []

        return _None()


class _ContextFactory:
    def build(self) -> object:
        from aci.runtime.context_engine import ContextBudget, ContextEngine

        return ContextEngine(ContextBudget(total_tokens=60_000))


def _service(
    script: list[Any],
    roots: Roots,
    *,
    process_prefixes: list[str] | None = None,
    context_factory: object | None = None,
    run_store: object | None = None,
) -> tuple[AgentRunService, FakeModelGateway]:
    """The service as the server wires it: a workspace root, a runs root and
    the process ceiling (``[PY]`` unless a test narrows it)."""
    gateway = FakeModelGateway(script)
    service = AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _GatewayFactory(gateway)),
        tool_executor_factory=cast(ModelGatewayFactory, _NoWorkspaceToolFactory()),
        capability_runtime_factory=cast(ModelGatewayFactory, _NullCapabilityFactory()),
        context_engine_factory=cast(ModelGatewayFactory, context_factory or _ContextFactory()),
        workspace_root=str(roots.workspace_root),
        runs_root=str(roots.runs_root),
        process_prefixes=[PY] if process_prefixes is None else process_prefixes,
        command_timeout_seconds=30,
        verification_timeout_seconds=30,
        run_store=cast("Any", run_store),
        # bwrap where usable; the sandbox itself: tests/security/test_process_sandbox.py
        process_sandbox=available_sandbox(),
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


def _command(call_id: str, argv: list[str]) -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[ToolCall(call_id=call_id, tool_id="run_command", arguments={"command": argv})]
    )


def _done(*changes: str, summary: str = "done") -> FinalCandidate:
    return FinalCandidate(summary=summary, changes=list(changes))


def _done_repeatedly(*changes: str) -> list[FinalCandidate]:
    return [_done(*changes)] * REPAIR_TURNS


def _model_saw(gateway: FakeModelGateway) -> str:
    """Every message the model was ever shown, across all of its requests."""
    return "\n".join(m.content for request in gateway.requests for m in request.messages)


def _tool_results(gateway: FakeModelGateway) -> dict[str, str]:
    """``{tool_call_id: rendered observation}`` as the model saw them (the
    transcript is cumulative, so the last request carries every result)."""
    if not gateway.requests:
        return {}
    return {
        m.tool_call_id: m.content
        for m in gateway.requests[-1].messages
        if m.role == "tool" and m.tool_call_id
    }


def _advertised(gateway: FakeModelGateway) -> set[str]:
    assert gateway.requests, "the model was never invoked"
    return {t.tool_id for t in gateway.requests[0].tools}


# -- 1/2: a workspace is a NAME under the server root, never a path ----------


@pytest.mark.parametrize("name", ["../etc", "/tmp", "a/b", ""])
def test_workspace_is_a_name_never_a_path(roots: Roots, name: str) -> None:
    """A path-shaped workspace is CLIENT_INCOMPATIBLE at the edge: nothing is
    provisioned under the runs root and the model is never invoked."""
    service, gateway = _service([_done("src/app.py")], roots)
    response = _client(service).post("/v1/agent-runs", json=_body(workspace=name))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CLIENT_INCOMPATIBLE"
    assert roots.run_dirs() == []
    assert gateway.requests == []


def test_unknown_workspace_is_404_without_the_server_root_path(roots: Roots) -> None:
    """An unknown name is the typed WORKSPACE_NOT_FOUND — the message never
    reveals where the server keeps its workspaces."""
    service, gateway = _service([_done("src/app.py")], roots)
    response = _client(service).post("/v1/agent-runs", json=_body(workspace="ghost"))
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "WORKSPACE_NOT_FOUND"
    assert str(roots.workspace_root) not in json.dumps(body)
    assert str(roots.tmp) not in json.dumps(body)
    assert roots.run_dirs() == []
    assert gateway.requests == []


# -- 3/4: the client cannot widen the server's process ceiling (INV-02) ------


def test_verification_command_outside_ceiling_is_refused_before_anything_runs(
    roots: Roots,
) -> None:
    """The verifier runs the command with the SERVER's authority, so an argv
    outside the ceiling is PERMISSION_DENIED at the edge: it never executes,
    the model never runs, nothing is provisioned."""
    sentinel = roots.tmp / "verification-ran"
    service, gateway = _service([_write("c1", "src/greet.py"), _done("src/greet.py")], roots)
    response = _client(service).post(
        "/v1/agent-runs",
        json=_body(verification_command=["/bin/sh", "-c", f"touch {sentinel}"]),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert not sentinel.exists()
    assert gateway.requests == []
    assert roots.run_dirs() == []


@pytest.mark.parametrize(
    ("ceiling", "requested"),
    [
        ([PY], ["/bin/sh"]),  # a different program
        ([], [PY]),  # anything at all when the server allows nothing
        ([PY], [PY, "/bin/sh"]),  # one allowed prefix does not smuggle another
    ],
)
def test_client_cannot_widen_its_own_process_authority(
    roots: Roots, ceiling: list[str], requested: list[str]
) -> None:
    """Requested command prefixes must fit inside the server ceiling; a wider
    request is refused whole, before the model is invoked."""
    service, gateway = _service([_done("src/app.py")], roots, process_prefixes=ceiling)
    response = _client(service).post("/v1/agent-runs", json=_body(command_prefixes=requested))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert gateway.requests == []
    assert roots.run_dirs() == []


# -- 5/6: file effects stay inside the run's own copy of the workspace -------


def test_tool_path_escape_never_writes_outside_the_run_dir(roots: Roots) -> None:
    """Traversal and absolute paths from the model are denied observations —
    no file appears anywhere, and the run cannot succeed on a claim about it."""
    absolute = roots.tmp / "abs-escape.txt"
    escape = ToolCallBatchAction(
        calls=[
            ToolCall(
                call_id="rel",
                tool_id="write_file",
                arguments={"path": "../escape.txt", "content": "pwned"},
            ),
            ToolCall(
                call_id="abs",
                tool_id="write_file",
                arguments={"path": str(absolute), "content": "pwned"},
            ),
        ]
    )
    service, gateway = _service([escape, *_done_repeatedly("../escape.txt", str(absolute))], roots)
    body = _body()
    del body["write_scopes"]  # the DEFAULT authority must already hold the line
    data = _client(service).post("/v1/agent-runs", json=body).json()
    assert data["status"] != "succeeded"
    assert not absolute.exists()
    assert list(roots.tmp.rglob("escape.txt")) == []
    assert list(roots.tmp.rglob("abs-escape.txt")) == []
    results = _tool_results(gateway)
    # Either defense line may fire first (guardrails run before the
    # authority preflight): `..` → GUARDRAIL_BLOCKED, the absolute path →
    # GUARDRAIL_BLOCKED or AUTHORITY_DENIED. Both mean: no effect, the
    # model sees a refusal observation, the run cannot succeed on it.
    assert results["rel"].startswith(("status: denied", "status: blocked"))
    assert results["abs"].startswith(("status: denied", "status: blocked"))


def test_write_scopes_bound_writes_to_the_granted_prefix(roots: Roots) -> None:
    """``write_scopes=["src"]``: a write outside is denied and the file is
    unchanged; a write inside lands — in the run's copy, never in the source."""
    batch = ToolCallBatchAction(
        calls=[
            ToolCall(
                call_id="outside",
                tool_id="write_file",
                arguments={"path": "README.md", "content": "pwned"},
            ),
            ToolCall(
                call_id="inside",
                tool_id="write_file",
                arguments={"path": "src/greet.py", "content": GREET},
            ),
        ]
    )
    service, gateway = _service([batch, _done("src/greet.py")], roots)
    data = _client(service).post("/v1/agent-runs", json=_body(write_scopes=["src"])).json()
    assert data["status"] == "succeeded", data
    run_dir = roots.run_dir(data["run_id"])
    assert (run_dir / "README.md").read_text(encoding="utf-8") == README
    assert (run_dir / "src" / "greet.py").read_text(encoding="utf-8") == GREET
    # §16.2: runs work on their own copy — the source workspace is never touched.
    assert (roots.source / "README.md").read_text(encoding="utf-8") == README
    assert not (roots.source / "src" / "greet.py").exists()
    results = _tool_results(gateway)
    assert results["outside"].startswith("status: denied")
    assert results["inside"].startswith("status: success")


# -- 7: process effects need a process scope --------------------------------


def test_run_command_outside_process_ceiling_is_a_denied_observation(roots: Roots) -> None:
    sentinel = roots.tmp / "pwned"
    script = [
        _command("c1", ["/bin/sh", "-c", f"touch {sentinel}"]),
        *_done_repeatedly("src/app.py"),
    ]
    service, gateway = _service(script, roots)
    data = _client(service).post("/v1/agent-runs", json=_body(command_prefixes=[PY])).json()
    assert not sentinel.exists()
    assert data["status"] != "succeeded"
    assert _tool_results(gateway)["c1"].startswith("status: denied")


def test_run_command_is_not_advertised_without_a_process_scope(roots: Roots) -> None:
    """No ``command_prefixes`` on an empty server ceiling: ``run_command`` is
    absent from the tools the model is offered, and calling it anyway is an
    unknown-tool observation that executes nothing."""
    sentinel = roots.tmp / "pwned"
    script = [
        _command("c1", [PY, "-c", f"open({str(sentinel)!r}, 'w').close()"]),
        *_done_repeatedly("src/app.py"),
    ]
    service, gateway = _service(script, roots, process_prefixes=[])
    _client(service).post("/v1/agent-runs", json=_body())
    assert not sentinel.exists()
    advertised = _advertised(gateway)
    assert "run_command" not in advertised
    assert {"read_file", "list_dir", "write_file", "edit_file"} <= advertised
    result = _tool_results(gateway)["c1"]
    assert result.startswith("status: error") or result.startswith("status: denied")


# -- 8: workspace processes never see the server environment (§16.4) --------


def test_workspace_processes_never_see_the_server_environment(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACI_AGENT_MODEL_API_KEY", SECRET)
    probe = [PY, "-c", "import os; print(os.environ.get('ACI_AGENT_MODEL_API_KEY'))"]
    service, gateway = _service([_command("c1", probe), *_done_repeatedly("src/app.py")], roots)
    response = _client(service).post("/v1/agent-runs", json=_body(command_prefixes=[PY]))
    assert response.status_code == 201
    result = _tool_results(gateway)["c1"]
    assert result.startswith("status: success")  # the probe DID run ...
    assert "None" in result  # ... and saw no key
    assert SECRET not in result
    assert SECRET not in _model_saw(gateway)
    assert SECRET not in response.text


# -- 9: response hygiene — relative refs, typed codes, no server paths -------


def test_wire_carries_relative_refs_never_server_paths(roots: Roots) -> None:
    cwd_probe = [PY, "-c", "import os; print(os.getcwd())"]
    script = [_write("c1", "src/greet.py", GREET), _command("c2", cwd_probe), _done("src/greet.py")]
    service, _ = _service(script, roots)
    response = _client(service).post(
        "/v1/agent-runs", json=_body(command_prefixes=[PY], verification_command=cwd_probe)
    )
    data = response.json()
    assert data["status"] == "succeeded", data
    assert "PASS:verification_command_passes" in data["checks"]
    assert "file://src/greet.py" in data["evidence_refs"]
    for ref in data["evidence_refs"]:
        scheme, _, rest = ref.partition("://")
        assert scheme and not rest.startswith("/"), ref
    text = json.dumps(data)
    assert str(roots.runs_root) not in text
    assert str(roots.workspace_root) not in text
    assert str(roots.tmp) not in text


def test_failed_verification_output_never_carries_the_run_dir_path(roots: Roots) -> None:
    """A failing verification command that prints its cwd (as test runners
    do) must not push the server's absolute run directory into ``summary``."""
    leaky = [PY, "-c", "import os, sys; print('rootdir:', os.getcwd()); sys.exit(1)"]
    script = [_write("c1", "src/greet.py", GREET), *_done_repeatedly("src/greet.py")]
    service, _ = _service(script, roots)
    data = _client(service).post("/v1/agent-runs", json=_body(verification_command=leaky)).json()
    assert data["status"] == "failed"
    assert data["stop_reason"] == "VERIFICATION_FAILED"
    assert "FAIL:verification_command_passes" in data["checks"]
    text = json.dumps(data)
    assert str(roots.runs_root) not in text
    assert str(roots.tmp) not in text


def test_manager_crash_yields_exception_type_never_its_text(roots: Roots) -> None:
    secret_path = roots.tmp / "secret-store"

    class _ExplodingContext:
        def build(self, snapshot: object, *, turn: int) -> object:
            raise RuntimeError(f"context store unavailable at {secret_path}")

    class _ExplodingFactory:
        def build(self) -> object:
            return _ExplodingContext()

    service, _ = _service([_done("src/app.py")], roots, context_factory=_ExplodingFactory())
    response = _client(service).post("/v1/agent-runs", json=_body())
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "failed"
    assert data["stop_reason"] == "FATAL_ERROR"
    assert data["detail_code"] == "RuntimeError"
    assert "context store unavailable" not in response.text
    assert str(secret_path) not in response.text


# -- 10: completion is evidence-gated at the wire (INV-08) ------------------


def test_completion_is_evidence_gated_at_the_wire(roots: Roots) -> None:
    """The model claims a change it never made through a tool: the verifier,
    not the model, decides — VERIFICATION_FAILED with the failing check named."""
    service, gateway = _service(_done_repeatedly("src/x.py"), roots)
    data = _client(service).post("/v1/agent-runs", json=_body()).json()
    assert data["status"] == "failed"
    assert data["stop_reason"] == "VERIFICATION_FAILED"
    assert data["evidence_verdict"] == "FAIL"
    assert "FAIL:claimed_changes_observed" in data["checks"]
    assert data["tool_calls"] == 0
    assert list(roots.tmp.rglob("x.py")) == []
    # The kernel asked for repairs; every repeated claim was refused the same way.
    assert len(gateway.requests) == REPAIR_TURNS


# -- 11: revision = same lineage, same workspace, same grants ---------------


def test_revision_continues_in_a_copy_of_the_previous_run_dir(roots: Roots) -> None:
    script = [
        _write("c1", "src/greet.py", GREET),
        _done("src/greet.py"),
        _write("c2", "src/greet_test.py", "from greet import greet\n"),
        _done("src/greet_test.py"),
    ]
    service, _ = _service(script, roots)
    client = _client(service)
    first = client.post("/v1/agent-runs", json=_body()).json()
    assert first["status"] == "succeeded", first
    first_dir = roots.run_dir(first["run_id"])
    revision = client.post(
        f"/v1/agent-runs/{first['run_id']}/revise",
        json={
            "objective": "add a test for the greeting module",
            "failed_criteria": ["src/greet_test.py exists"],
            "feedback": "the module has no test",
        },
    ).json()
    assert revision["status"] == "succeeded", revision
    assert revision["run_id"] != first["run_id"]
    revision_dir = roots.run_dir(revision["run_id"])
    assert revision_dir != first_dir
    # Lineage: the revision started from the previous run's files, not the source.
    assert (revision_dir / "src" / "greet.py").read_text(encoding="utf-8") == GREET
    assert (revision_dir / "src" / "greet_test.py").exists()
    # The previous run dir is a copy source, never mutated by its successor.
    assert not (first_dir / "src" / "greet_test.py").exists()
    assert not (roots.source / "src" / "greet.py").exists()


def test_revision_cannot_switch_workspace_or_widen_grants(roots: Roots) -> None:
    """The revise body carries no workspace/scope/prefix fields; smuggling them
    in must not move the revision to another workspace, widen its write scope
    or admit a process the first run was not granted."""
    other = roots.workspace_root / "other"
    other.mkdir()
    (other / "MARKER").write_text("other workspace\n", encoding="utf-8")
    sentinel = roots.tmp / "pwned"
    smuggle = ToolCallBatchAction(
        calls=[
            ToolCall(
                call_id="r1",
                tool_id="write_file",
                arguments={"path": "README.md", "content": "pwned"},
            ),
            ToolCall(
                call_id="r2",
                tool_id="run_command",
                arguments={"command": ["/bin/sh", "-c", f"touch {sentinel}"]},
            ),
        ]
    )
    script = [
        _write("c1", "src/greet.py", GREET),
        _done("src/greet.py"),
        smuggle,
        *_done_repeatedly("README.md"),
    ]
    service, gateway = _service(script, roots)
    client = _client(service)
    first = client.post("/v1/agent-runs", json=_body()).json()
    assert first["status"] == "succeeded", first
    first_dir = roots.run_dir(first["run_id"])

    response = client.post(
        f"/v1/agent-runs/{first['run_id']}/revise",
        json={
            "objective": "now take over",
            "failed_criteria": ["x"],
            "feedback": "y",
            "workspace": "other",
            "write_scopes": ["."],
            "command_prefixes": ["/bin/sh"],
            "verification_command": ["/bin/sh", "-c", f"touch {sentinel}"],
        },
    )
    assert not sentinel.exists()
    assert (other / "MARKER").read_text(encoding="utf-8") == "other workspace\n"
    if response.status_code == 422:
        # Rejecting the unknown fields outright also holds the boundary.
        assert roots.run_dirs() == [first_dir]
        return
    assert response.status_code == 201
    data = response.json()
    assert data["status"] != "succeeded"
    revision_dir = roots.run_dir(data["run_id"])
    assert not (revision_dir / "MARKER").exists()  # not the other workspace
    assert (revision_dir / "src" / "greet.py").read_text(encoding="utf-8") == GREET
    assert (revision_dir / "README.md").read_text(encoding="utf-8") == README
    results = _tool_results(gateway)
    assert results["r1"].startswith("status: denied")
    assert results["r2"].startswith("status: denied") or results["r2"].startswith("status: error")


# -- 12: after a restart the run dir stays server-side (migration 0018) -----


class _MemoryStore:
    """In-memory AgentRunStore: the durable store a restarted process reads."""

    def __init__(self) -> None:
        self.runs: dict[str, AgentRunRecord] = {}

    def record_run(self, record: AgentRunRecord) -> None:
        self.runs[record.run_id] = record

    def record_events(self, events: list[AgentRunEventRecord]) -> None:
        return None

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    def list_recent(self, limit: int = 50) -> list[AgentRunRecord]:
        return list(self.runs.values())[:limit]


def _assert_no_server_paths(roots: Roots, text: str) -> None:
    assert str(roots.runs_root) not in text
    assert str(roots.runs_root.resolve()) not in text
    assert str(roots.tmp) not in text
    assert str(roots.tmp.resolve()) not in text


def test_restarted_server_never_returns_the_stored_run_dir(roots: Roots) -> None:
    """The store holds the absolute working-copy path (the revision's copy
    source); GET, revise and cancel of a store-only run never put it on the
    wire — and the revision really continues from that copy."""
    store = _MemoryStore()
    first_service, _ = _service(
        [_write("c1", "src/greet.py", GREET), _done("src/greet.py")], roots, run_store=store
    )
    first = _client(first_service).post("/v1/agent-runs", json=_body()).json()
    assert first["status"] == "succeeded", first
    stored = store.runs[first["run_id"]]
    assert stored.run_dir is not None and str(roots.runs_root.resolve()) in stored.run_dir

    restarted, _ = _service(
        [
            _write("c2", "src/greet_test.py", "from greet import greet\n"),
            _done("src/greet_test.py"),
        ],
        roots,
        run_store=store,
    )
    client = _client(restarted)
    got = client.get(f"/v1/agent-runs/{first['run_id']}")
    assert got.status_code == 200
    _assert_no_server_paths(roots, got.text)
    revised = client.post(
        f"/v1/agent-runs/{first['run_id']}/revise",
        json={"failed_criteria": ["src/greet_test.py exists"], "feedback": "add a test"},
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["status"] == "succeeded", revised.json()
    _assert_no_server_paths(roots, revised.text)
    revision_dir = roots.run_dir(revised.json()["run_id"])
    assert (revision_dir / "src" / "greet.py").read_text(encoding="utf-8") == GREET
    cancelled = client.post(f"/v1/agent-runs/{first['run_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json() == {"cancelled": False}


def test_vanished_run_dir_error_never_names_the_path(roots: Roots) -> None:
    store = _MemoryStore()
    first_service, _ = _service(
        [_write("c1", "src/greet.py", GREET), _done("src/greet.py")], roots, run_store=store
    )
    first = _client(first_service).post("/v1/agent-runs", json=_body()).json()
    shutil.rmtree(roots.run_dir(first["run_id"]))

    restarted, gateway = _service([_done("src/greet.py")], roots, run_store=store)
    response = _client(restarted).post(
        f"/v1/agent-runs/{first['run_id']}/revise", json={"feedback": "again"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"
    _assert_no_server_paths(roots, response.text)
    assert gateway.requests == []  # refused before the model is invoked


# -- 13: approval interrupts + checkpoint resume (migration 0019) ------------
#
# - a client can ADD approval requirements, never remove the server's;
# - an approval is one-shot and never widens grants: a call outside the
#   write scope is DENIED (never even paused for), approved or not;
# - resume continues the SAME run in its OWN working copy (also after a
#   restart, from the store), at most once, and no response carries a path.


class _CheckpointStore(_MemoryStore):
    """_MemoryStore + the 0019 checkpoint methods (atomic claim)."""

    def __init__(self) -> None:
        super().__init__()
        self.checkpoints: dict[str, Any] = {}
        self.appended: list[AgentRunEventRecord] = []

    def append_events(self, events: list[AgentRunEventRecord]) -> None:
        self.appended.extend(events)

    def record_checkpoint(self, record: Any) -> None:
        assert record.run_id in self.runs  # FK: the run row is written first
        self.checkpoints.setdefault(record.checkpoint_id, record)

    def latest_checkpoint(self, run_id: str) -> Any:
        mine = [c for c in self.checkpoints.values() if c.run_id == run_id]
        return max(mine, key=lambda c: c.created_at) if mine else None

    def consume_checkpoint(self, checkpoint_id: str, *, at: Any) -> bool:
        record = self.checkpoints.get(checkpoint_id)
        if record is None or record.consumed_at is not None:
            return False
        self.checkpoints[checkpoint_id] = record.model_copy(update={"consumed_at": at})
        return True


def test_client_cannot_remove_server_approval_requirements(roots: Roots) -> None:
    service, _ = _service([_write("c1", "src/greet.py", GREET), _done("src/greet.py")], roots)
    service.require_approval_for(["write_file"])  # the server floor
    response = _client(service).post(
        "/v1/agent-runs", json=_body(approval_required_tools=[])
    )  # an empty request list removes nothing
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "interrupted_approval"
    assert body["stop_reason"] == "AWAITING_APPROVAL"
    assert body["approval_id"]
    assert not (roots.run_dir(body["run_id"]) / "src" / "greet.py").exists()


def test_approval_pauses_then_resume_executes_once_without_leaking_paths(
    roots: Roots,
) -> None:
    service, gateway = _service([_write("c1", "src/greet.py", GREET), _done("src/greet.py")], roots)
    client = _client(service)
    paused = client.post("/v1/agent-runs", json=_body(approval_required_tools=["write_file"]))
    assert paused.status_code == 201, paused.text
    run_id, approval_id = paused.json()["run_id"], paused.json()["approval_id"]
    assert paused.json()["status"] == "interrupted_approval"
    run_dir = roots.run_dir(run_id)
    assert not (run_dir / "src" / "greet.py").exists()
    assert client.get(f"/v1/agent-runs/{run_id}").json()["approval_id"] == approval_id

    resumed = client.post(
        f"/v1/agent-runs/{run_id}/resume", json={"approval_id": approval_id, "approve": True}
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run_id"] == run_id
    assert resumed.json()["status"] == "succeeded", resumed.json()
    assert resumed.json()["approval_id"] is None
    _assert_no_server_paths(roots, resumed.text)
    assert (run_dir / "src" / "greet.py").read_text(encoding="utf-8") == GREET
    assert roots.run_dirs() == [run_dir]  # the SAME working copy — never re-copied
    assert _tool_results(gateway)["c1"].startswith("status: success")

    again = client.post(
        f"/v1/agent-runs/{run_id}/resume", json={"approval_id": approval_id, "approve": True}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CHECKPOINT_CONSUMED"


def test_approval_cannot_widen_the_write_scope(roots: Roots) -> None:
    """write_file needs approval AND the call is outside write_scopes: it is
    DENIED by authority — no pause, no approval id, no effect."""
    service, gateway = _service([_write("c1", "README.md", "pwned\n"), _done("README.md")], roots)
    response = _client(service).post(
        "/v1/agent-runs", json=_body(approval_required_tools=["write_file"])
    )
    body = response.json()
    assert body["status"] != "interrupted_approval"
    assert body["approval_id"] is None
    assert (roots.run_dir(body["run_id"]) / "README.md").read_text(encoding="utf-8") == README
    assert _tool_results(gateway)["c1"].startswith("status: denied")


def test_denied_approval_is_an_observation_and_the_run_continues(roots: Roots) -> None:
    service, gateway = _service(
        [_write("c1", "src/greet.py", GREET), _done(summary="could not write")], roots
    )
    client = _client(service)
    paused = client.post(
        "/v1/agent-runs", json=_body(approval_required_tools=["write_file"])
    ).json()
    resumed = client.post(
        f"/v1/agent-runs/{paused['run_id']}/resume",
        json={"approval_id": paused["approval_id"], "approve": False},
    )
    assert resumed.status_code == 200, resumed.text
    assert not (roots.run_dir(paused["run_id"]) / "src" / "greet.py").exists()
    assert "DENIED approval" in _tool_results(gateway)["c1"]


def test_resume_refusals_are_typed_and_pathless(roots: Roots) -> None:
    service, _ = _service([_write("c1", "src/greet.py", GREET), _done("src/greet.py")] * 2, roots)
    client = _client(service)
    assert client.post("/v1/agent-runs/run_nope/resume", json={"answer": "x"}).status_code == 404
    paused = client.post(
        "/v1/agent-runs", json=_body(approval_required_tools=["write_file"])
    ).json()
    run_id = paused["run_id"]
    wrong = client.post(
        f"/v1/agent-runs/{run_id}/resume", json={"approval_id": "apr_forged", "approve": True}
    )
    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "APPROVAL_REPLAY_INVALID"
    both = client.post(
        f"/v1/agent-runs/{run_id}/resume",
        json={"approval_id": paused["approval_id"], "approve": True, "answer": "x"},
    )
    assert both.status_code == 422
    # The refusals above did not burn the one resume.
    ok = client.post(
        f"/v1/agent-runs/{run_id}/resume",
        json={"approval_id": paused["approval_id"], "approve": True},
    )
    assert ok.status_code == 200, ok.text
    finished = client.post("/v1/agent-runs", json=_body()).json()
    assert finished["status"] == "succeeded", finished
    never_paused = client.post(f"/v1/agent-runs/{finished['run_id']}/resume", json={"answer": "x"})
    assert never_paused.status_code == 409
    assert never_paused.json()["error"]["code"] == "RUN_NOT_RESUMABLE"
    for response in (wrong, both, never_paused):
        _assert_no_server_paths(roots, response.text)


def test_resume_after_restart_continues_the_same_run_from_the_store(roots: Roots) -> None:
    store = _CheckpointStore()
    first_service, _ = _service([_write("c1", "src/greet.py", GREET)], roots, run_store=store)
    paused = (
        _client(first_service)
        .post("/v1/agent-runs", json=_body(approval_required_tools=["write_file"]))
        .json()
    )
    assert paused["status"] == "interrupted_approval"
    assert store.runs[paused["run_id"]].status == "interrupted_approval"
    stored = store.latest_checkpoint(paused["run_id"])
    assert stored is not None and stored.approval_id == paused["approval_id"]

    # A FRESH process: empty RAM, same store, same server-side directories.
    restarted, gateway = _service([_done("src/greet.py")], roots, run_store=store)
    client = _client(restarted)
    got = client.get(f"/v1/agent-runs/{paused['run_id']}")
    assert got.json()["approval_id"] == paused["approval_id"]
    resumed = client.post(
        f"/v1/agent-runs/{paused['run_id']}/resume",
        json={"approval_id": paused["approval_id"], "approve": True},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "succeeded", resumed.json()
    _assert_no_server_paths(roots, resumed.text)
    run_dir = roots.run_dir(paused["run_id"])
    assert (run_dir / "src" / "greet.py").read_text(encoding="utf-8") == GREET
    assert roots.run_dirs() == [run_dir]
    assert store.runs[paused["run_id"]].status == "succeeded"
    assert store.appended  # the resumed segment's events were APPENDED
    # The model saw its own pre-restart turn (the transcript was restored).
    first_request = gateway.requests[0].messages
    assert any(m.role == "assistant" and m.tool_calls for m in first_request)
    assert any(m.tool_call_id == "c1" for m in first_request)
    second = _client(_service([], roots, run_store=store)[0]).post(
        f"/v1/agent-runs/{paused['run_id']}/resume",
        json={"approval_id": paused["approval_id"], "approve": True},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CHECKPOINT_CONSUMED"
