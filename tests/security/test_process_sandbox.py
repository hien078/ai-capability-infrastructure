"""Real-bwrap process sandbox boundaries (§16.5; user decision 2026-10-01).

Every process the kernel starts in a run workspace (model `run_command`,
client `verification_command`) runs inside BwrapSandbox. These tests drive
REAL bubblewrap and SKIP where it is unusable (no binary / unprivileged user
namespaces blocked); CI installs it so they run there. Before the sandbox,
each of these escapes succeeded: workspace code ran as the server user with
the network, the whole filesystem and the server's paths in view.
"""

import json
import os
import socket
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.domain.runtime.actions import FinalCandidate, ToolCallBatchAction
from aci.domain.runtime.subtask import AcceptanceCriterion, SubtaskContract
from aci.domain.runtime.tools import ToolCall
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.model_gateway import FakeModelGateway
from aci.runtime.profiles import runtime_spec_for
from aci.runtime.protocols import ProcessResult
from aci.runtime.sandbox import WORKSPACE_MOUNT, BwrapSandbox, ResourceLimits
from aci.runtime.workspace import LocalWorkspace
from tests.sandbox_support import bwrap_or_skip

PY = sys.executable
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def sandbox() -> BwrapSandbox:
    return bwrap_or_skip()


@pytest.fixture
def ws(tmp_path: Path, sandbox: BwrapSandbox) -> LocalWorkspace:
    return LocalWorkspace(tmp_path / "runs" / "run_a", sandbox=sandbox)


def _py(ws: LocalWorkspace, code: str, timeout_ms: int = 30_000) -> ProcessResult:
    return ws.execute([PY, "-c", code], timeout_ms=timeout_ms)


def _probe_paths(ws: LocalWorkspace, paths: list[str]) -> dict[str, str]:
    """{path: 'READ' | error-class} for an open() of each path inside."""
    code = (
        "import json, sys\n"
        "out = {}\n"
        "for p in json.loads(sys.argv[1]):\n"
        "    try:\n"
        "        open(p, 'rb').read(1); out[p] = 'READ'\n"
        "    except OSError as e:\n"
        "        out[p] = type(e).__name__\n"
        "print(json.dumps(out))\n"
    )
    result = ws.execute([PY, "-c", code, json.dumps(paths)], timeout_ms=30_000)
    assert result.exit_code == 0, result.stderr
    parsed: dict[str, str] = json.loads(result.stdout)
    return parsed


# -- network --------------------------------------------------------------------


def test_network_is_unreachable(ws: LocalWorkspace) -> None:
    # A host-side listener on loopback (stands in for the DB / the API): the
    # sandbox's own loopback is a different, empty network namespace.
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        result = _py(
            ws,
            "import socket\n"
            f"for host, port in (('127.0.0.1', {port}), ('1.1.1.1', 53)):\n"
            "    try:\n"
            "        socket.create_connection((host, port), timeout=3).close()\n"
            "        print('CONNECTED', host)\n"
            "    except OSError as e:\n"
            "        print('BLOCKED', host, type(e).__name__)\n",
        )
    finally:
        server.close()
    assert result.exit_code == 0, result.stderr
    assert "CONNECTED" not in result.stdout
    assert result.stdout.count("BLOCKED") == 2


# -- filesystem view --------------------------------------------------------------


def test_sibling_run_home_and_repo_are_invisible(tmp_path: Path, ws: LocalWorkspace) -> None:
    sibling = tmp_path / "runs" / "run_b"
    sibling.mkdir(parents=True)
    (sibling / "secret.txt").write_text("other run's data", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("server file", encoding="utf-8")
    home = os.path.realpath(os.path.expanduser("~"))
    bound = ws.sandbox.ro_binds(ws.root)  # type: ignore[attr-defined]
    home_entries = [
        os.path.join(home, e)
        for e in sorted(os.listdir(home))[:20]
        if os.path.isfile(os.path.join(home, e))
        and not any(b.startswith(os.path.join(home, e)) for b in bound)
    ]
    paths = [
        str(sibling / "secret.txt"),
        str(outside),
        str(REPO / "pyproject.toml"),
        str(REPO / "src" / "aci" / "config.py"),
        str(REPO / "AGENTS.md"),
        "/etc/passwd",
        "/etc/hostname",
        *home_entries,
    ]
    seen = _probe_paths(ws, paths)
    assert {p: v for p, v in seen.items() if v == "READ"} == {}


def test_writes_land_only_in_the_workspace(tmp_path: Path, ws: LocalWorkspace) -> None:
    target_outside = tmp_path / "escaped.txt"
    venv_target = os.path.join(sys.prefix, "aci-sandbox-escape.txt")
    result = _py(
        ws,
        "open('inside.txt', 'w').write('relative')\n"
        "open('/workspace/abs.txt', 'w').write('absolute')\n"
        "open('/tmp/scratch.txt', 'w').write('tmpfs')\n",
    )
    assert result.exit_code == 0, result.stderr
    escape = ws.execute(
        [
            PY,
            "-c",
            "import json, sys\n"
            "out = {}\n"
            "for p in json.loads(sys.argv[1]):\n"
            "    try:\n"
            "        open(p, 'w').write('x'); out[p] = 'WROTE'\n"
            "    except OSError as e:\n"
            "        out[p] = type(e).__name__\n"
            "print(json.dumps(out))\n",
            json.dumps([str(target_outside), venv_target, "/usr/aci-escape.txt"]),
        ],
        timeout_ms=30_000,
    )
    assert escape.exit_code == 0, escape.stderr
    assert "WROTE" not in escape.stdout
    assert not target_outside.exists()
    assert not os.path.exists(venv_target)
    # Inside the workspace: relative and /workspace paths land in the run dir.
    assert (ws.root / "inside.txt").read_text(encoding="utf-8") == "relative"
    assert (ws.root / "abs.txt").read_text(encoding="utf-8") == "absolute"
    # /tmp is a private tmpfs: nothing reaches the host's /tmp.
    assert not any(p.name == "scratch.txt" for p in (tmp_path / "runs").rglob("*"))


# -- environment / identity -------------------------------------------------------


def test_environment_carries_no_server_secret(
    ws: LocalWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACI_AGENT_MODEL_API_KEY", "sk-sandbox-sentinel-123")
    monkeypatch.setenv("ACI_AGENT_RUNS_TOKEN", "runs-token-sentinel")
    monkeypatch.setenv("ACI_DATABASE_URL", "postgresql://aci:pw-sentinel@localhost/aci")
    result = _py(ws, "import json, os; print(json.dumps(dict(os.environ)))")
    assert result.exit_code == 0, result.stderr
    env: dict[str, str] = json.loads(result.stdout)
    dumped = json.dumps(env)
    for sentinel in ("sk-sandbox-sentinel-123", "runs-token-sentinel", "pw-sentinel"):
        assert sentinel not in dumped
    assert not any(k.startswith("ACI_") for k in env)
    assert env["HOME"] == WORKSPACE_MOUNT
    # /proc/1/environ etc.: a fresh PID namespace — no host process visible.
    pids = _py(ws, "import os; print(sum(e.isdigit() for e in os.listdir('/proc')))")
    assert int(pids.stdout.strip()) <= 4, pids.stdout


# -- the verifier's own command works -----------------------------------------------


def test_python_m_pytest_runs_inside(ws: LocalWorkspace) -> None:
    (ws.root / "test_tiny.py").write_text(
        "import os, socket\n\n"
        "def test_inside_the_sandbox():\n"
        "    assert os.getcwd() == '/workspace'\n\n"
        "def test_arithmetic():\n"
        "    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    for argv in (
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"],  # PATH lookup
    ):
        result = ws.execute(argv, timeout_ms=120_000)
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "2 passed" in result.stdout


# -- resource limits + timeout ------------------------------------------------------


def _marker_alive(marker: str) -> bool:
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                if marker.encode() in fh.read():
                    return True
        except OSError:
            continue
    return False


def test_fork_bomb_is_bounded_and_the_timeout_still_kills_everything(tmp_path: Path) -> None:
    bwrap_or_skip()
    sandbox = BwrapSandbox(limits=ResourceLimits(max_processes=32))
    ws = LocalWorkspace(tmp_path / "ws", sandbox=sandbox)
    marker = f"aci-forkbomb-{uuid.uuid4().hex[:8]}"
    code = (
        "import os, sys, time\n"
        "n = 0\n"
        "try:\n"
        "    while n < 1000:\n"
        "        if os.fork() == 0:\n"
        "            time.sleep(600); os._exit(0)\n"
        "        n += 1\n"
        "except OSError as e:\n"
        "    print('BOUNDED', n, flush=True)\n"
        "time.sleep(600)\n"
    )
    start = time.monotonic()
    result = ws.execute([PY, "-c", code, marker], timeout_ms=4_000)
    elapsed = time.monotonic() - start
    assert result.timed_out
    assert "BOUNDED" in result.stdout
    assert int(result.stdout.split()[1]) < 32
    assert elapsed < 30
    time.sleep(0.5)
    assert not _marker_alive(marker), "sandboxed processes survived the timeout kill"


def test_memory_and_file_size_are_bounded(tmp_path: Path) -> None:
    bwrap_or_skip()
    sandbox = BwrapSandbox(
        limits=ResourceLimits(address_space_bytes=512 * 1024**2, file_size_bytes=1024**2)
    )
    ws = LocalWorkspace(tmp_path / "ws", sandbox=sandbox)
    result = _py(
        ws,
        "try:\n"
        "    b = bytearray(2 * 1024**3); print('ALLOCATED')\n"
        "except MemoryError:\n"
        "    print('MEMORY_BOUNDED')\n"
        "try:\n"
        "    open('big.bin', 'wb').write(b'0' * (4 * 1024**2)); print('WROTE_BIG')\n"
        "except OSError as e:\n"
        "    print('FSIZE_BOUNDED', type(e).__name__)\n",
    )
    assert "MEMORY_BOUNDED" in result.stdout, result.stdout + result.stderr
    assert "FSIZE_BOUNDED" in result.stdout, result.stdout + result.stderr
    assert (ws.root / "big.bin").stat().st_size <= 1024**2


def test_cpu_time_is_bounded(tmp_path: Path) -> None:
    bwrap_or_skip()
    ws = LocalWorkspace(tmp_path / "ws", sandbox=BwrapSandbox(limits=ResourceLimits(cpu_seconds=1)))
    start = time.monotonic()
    result = _py(ws, "while True: pass", timeout_ms=60_000)
    assert not result.timed_out
    assert result.exit_code != 0
    assert time.monotonic() - start < 30


# -- no server path in output -------------------------------------------------------


def test_server_paths_never_appear_in_output(tmp_path: Path, ws: LocalWorkspace) -> None:
    (ws.root / "test_fail.py").write_text(
        "import os\n\ndef test_fails():\n    assert os.getcwd() == 'nowhere'\n", encoding="utf-8"
    )
    (ws.root / "boom.py").write_text("raise RuntimeError(__file__)\n", encoding="utf-8")
    outputs = [
        _py(ws, "import os; print(os.getcwd(), os.environ['HOME'], os.path.abspath('x'))"),
        ws.execute([PY, "boom.py"], timeout_ms=30_000),
        ws.execute([PY, "-m", "pytest", "-p", "no:cacheprovider"], timeout_ms=120_000),
    ]
    text = "\n".join(o.stdout + o.stderr for o in outputs)
    assert WORKSPACE_MOUNT in text
    for server_path in {str(tmp_path), os.path.realpath(tmp_path), str(ws.root)}:
        assert server_path not in text


# -- end to end: both process paths of a real run go through the sandbox ------------


class _Factory:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def build(self) -> object:
        return self._obj


class _NullCapabilities:
    def handle_request(self, request: object, snapshot: object) -> list[object]:
        return []


def test_run_command_and_verification_command_are_both_sandboxed(
    tmp_path: Path, sandbox: BwrapSandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sources" / "proj"
    source.mkdir(parents=True)
    (source / "notes.txt").write_text("cause is X\n", encoding="utf-8")
    # The client's acceptance test PROVES where it ran: neutral cwd, no
    # network, no server secret.
    (source / "test_where.py").write_text(
        "import os, socket\n\n"
        "def test_sandboxed():\n"
        "    assert os.getcwd() == '/workspace'\n"
        "    assert 'ACI_AGENT_MODEL_API_KEY' not in os.environ\n"
        "    try:\n"
        "        socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
        "    except OSError:\n"
        "        return\n"
        "    raise AssertionError('network reachable')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ACI_AGENT_MODEL_API_KEY", "sk-e2e-sentinel")
    run_cmd = ToolCallBatchAction(
        calls=[
            ToolCall(
                call_id="c1",
                tool_id="run_command",
                arguments={"command": [PY, "-c", "import os; print('CWD=' + os.getcwd())"]},
            )
        ]
    )
    read = ToolCallBatchAction(
        calls=[ToolCall(call_id="c2", tool_id="read_file", arguments={"path": "notes.txt"})]
    )
    gateway = FakeModelGateway(
        [run_cmd, read, FinalCandidate(summary="done", claims=["notes.txt: cause X"])]
    )
    service = AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _Factory(gateway)),
        tool_executor_factory=cast(ModelGatewayFactory, _Factory(None)),
        capability_runtime_factory=cast(ModelGatewayFactory, _Factory(_NullCapabilities())),
        context_engine_factory=cast(
            ModelGatewayFactory, _Factory(ContextEngine(ContextBudget(total_tokens=60_000)))
        ),
        workspace_root=tmp_path / "sources",
        runs_root=tmp_path / "runs",
        process_prefixes=[PY],
        process_sandbox=sandbox,
    )
    contract = SubtaskContract(
        task_id=f"run-{uuid.uuid4().hex[:8]}",
        objective="read the note",
        global_context="",
        constraints=[],
        acceptance_criteria=[AcceptanceCriterion(criterion_id="ac-1", description="note read")],
        requested_profile="researcher",
        budget=None,
        created_at=datetime.now(UTC),
    )
    result = service.run(
        contract,
        runtime_spec_for("researcher"),
        max_turns=6,
        workspace="proj",
        verification_command=[PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
    )
    tool_outputs = [
        m.content for request in gateway.requests for m in request.messages if m.role == "tool"
    ]
    assert any(f"CWD={WORKSPACE_MOUNT}" in o for o in tool_outputs), tool_outputs
    assert result.status.value == "succeeded", cast(Any, result.evidence)
    assert result.evidence is not None
    assert "PASS:verification_command_passes" in result.evidence.checks
