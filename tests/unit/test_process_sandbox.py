"""ProcessSandbox unit tests (§16.5; user decision 2026-10-01) — no bwrap needed.

The bwrap argv is built purely, so the profile (namespaces, binds, env,
limits) is pinned here on every host; usability is simulated so the
FAIL-CLOSED paths (workspace, verifier, service, wiring) are pinned too. The
real-bwrap behavior lives in tests/security/test_process_sandbox.py.
"""

import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

import aci.runtime.sandbox as sandbox_mod
from aci.adapters.inbound.rest.agent_run_wiring import build_sandbox
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.config import Settings
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import FinalCandidate, ToolCallBatchAction
from aci.domain.runtime.evidence import CandidateResult
from aci.domain.runtime.subtask import AcceptanceCriterion, SubtaskContract
from aci.domain.runtime.tools import ToolCall
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.model_gateway import FakeModelGateway
from aci.runtime.profiles import runtime_spec_for
from aci.runtime.sandbox import (
    WORKSPACE_MOUNT,
    BwrapSandbox,
    NoSandbox,
    ResourceLimits,
    build_process_sandbox,
)
from aci.runtime.workspace import LocalWorkspace, WorkspaceManager
from aci.runtime.workspace_tools import verification_command_check

PY = sys.executable
REASON = "simulated: bubblewrap is not installed"


@pytest.fixture
def unusable_bwrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every BwrapSandbox (and, on macOS, every SeatbeltSandbox) in this
    test probes as unusable (fresh cache)."""
    monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
    monkeypatch.setattr(BwrapSandbox, "_probe", lambda self: REASON)
    if sys.platform == "darwin":
        from aci.runtime.sandbox import SeatbeltSandbox

        monkeypatch.setattr(SeatbeltSandbox, "_probe", lambda self: REASON)


def _sandbox(tmp_path: Path, **kwargs: Any) -> BwrapSandbox:
    prefix = tmp_path / "interp"
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    kwargs.setdefault("ro_prefixes", [str(prefix)])
    return BwrapSandbox(bwrap_path="/opt/x/bwrap", prlimit_path="/opt/x/prlimit", **kwargs)


def _pairs(argv: list[str], flag: str, arity: int) -> list[tuple[str, ...]]:
    return [tuple(argv[i + 1 : i + 1 + arity]) for i, a in enumerate(argv) if a == flag]


# -- the profile ----------------------------------------------------------------


class TestBwrapArgv:
    def test_isolation_flags_and_neutral_workspace_mount(self, tmp_path: Path) -> None:
        ws = tmp_path / "run" / "ws"
        ws.mkdir(parents=True)
        argv = _sandbox(tmp_path).build_argv(["python", "-m", "pytest"], ws)
        head = argv[: argv.index("--")]
        assert argv[0] == "/opt/x/bwrap"
        for flag in ("--unshare-all", "--die-with-parent", "--new-session", "--clearenv"):
            assert flag in head, flag
        assert ("ALL",) in _pairs(head, "--cap-drop", 1)
        assert "--share-net" not in argv
        # Fresh /proc, minimal /dev, tmpfs /tmp, read-only /usr.
        assert ("/proc",) in _pairs(head, "--proc", 1)
        assert ("/dev",) in _pairs(head, "--dev", 1)
        assert ("/tmp",) in _pairs(head, "--tmpfs", 1)
        assert ("/usr", "/usr") in _pairs(head, "--ro-bind", 2)
        # The ONLY read-write bind is the workspace, at the neutral path.
        assert _pairs(head, "--bind", 2) == [(os.path.realpath(ws), WORKSPACE_MOUNT)]
        assert ("/workspace",) in _pairs(head, "--chdir", 1)

    def test_command_runs_under_prlimit_after_the_separator(self, tmp_path: Path) -> None:
        limits = ResourceLimits(
            cpu_seconds=7,
            address_space_bytes=123 * 1024**2,
            file_size_bytes=5 * 1024**2,
            max_processes=33,
            open_files=99,
        )
        argv = _sandbox(tmp_path, limits=limits).build_argv(["pytest", "-q"], tmp_path)
        tail = argv[argv.index("--") + 1 :]
        assert tail == [
            "/opt/x/prlimit",
            "--cpu=7",
            f"--as={123 * 1024**2}",
            f"--fsize={5 * 1024**2}",
            "--nproc=33",
            "--nofile=99",
            "--",
            "pytest",
            "-q",
        ]

    def test_default_limits_are_bounded(self) -> None:
        limits = ResourceLimits()
        assert 0 < limits.cpu_seconds <= 3600
        assert 0 < limits.address_space_bytes <= 16 * 1024**3
        assert 0 < limits.file_size_bytes <= 4 * 1024**3
        assert 0 < limits.max_processes <= 1024
        with pytest.raises(ValueError):
            ResourceLimits(max_processes=0)

    def test_env_is_minimal_and_carries_no_server_secret_or_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACI_AGENT_MODEL_API_KEY", "sk-sandbox-unit-sentinel")
        monkeypatch.setenv("ACI_DATABASE_URL", "postgresql://u:pw-sentinel@db/aci")
        ws = tmp_path / "ws"
        ws.mkdir()
        sb = _sandbox(tmp_path)
        argv = sb.build_argv(["true"], ws)
        assert "sk-sandbox-unit-sentinel" not in " ".join(argv)
        assert "pw-sentinel" not in " ".join(argv)
        env = dict((k, v) for k, v in _pairs(argv, "--setenv", 2))
        assert env == sb.sandbox_env(ws)
        assert set(env) <= {
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONUNBUFFERED",
            "TZ",
        }
        assert env["HOME"] == WORKSPACE_MOUNT
        # No host workspace path leaks into the process environment.
        assert not any(str(ws) in v for v in env.values())

    def test_path_keeps_only_entries_visible_inside(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home_bin = tmp_path / "home" / ".local" / "bin"
        home_bin.mkdir(parents=True)
        monkeypatch.setenv("PATH", os.pathsep.join([str(home_bin), "/usr/bin", "rel"]))
        entries = _sandbox(tmp_path).sandbox_env(tmp_path)["PATH"].split(os.pathsep)
        assert "/usr/bin" in entries
        assert str(home_bin) not in entries
        assert all(os.path.isabs(e) for e in entries)


class TestReadOnlyBinds:
    def test_home_its_ancestors_root_cwd_and_workspace_are_never_bound(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home" / "user"
        repo = tmp_path / "repo"
        ws = repo / "data" / "agent-runs" / "run_1"
        for d in (home, ws):
            d.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.chdir(repo)
        venv = repo / ".venv"
        venv.mkdir()
        sb = BwrapSandbox(
            ro_prefixes=["/", str(home), str(home.parent), str(repo), str(repo / "data")],
            extra_ro_binds=[str(tmp_path), str(venv)],
        )
        # Only the venv survives: it is neither $HOME, an ancestor of a
        # protected dir, nor the server cwd (the repo) or its data/.
        assert sb.ro_binds(ws) == [str(venv)]
        sources = {src for src, _ in _pairs(sb.build_argv(["x"], ws), "--ro-bind", 2)}
        assert sources.isdisjoint({str(home), str(home.parent), str(repo), str(tmp_path), "/"})

    def test_system_covered_missing_and_relative_paths_are_dropped(self, tmp_path: Path) -> None:
        sb = BwrapSandbox(
            ro_prefixes=["/usr/lib", str(tmp_path / "missing"), "relative/dir"],
        )
        assert sb.ro_binds() == []

    def test_default_prefixes_cover_the_running_interpreter(self) -> None:
        prefixes = sandbox_mod.interpreter_prefixes()
        assert sys.prefix in prefixes
        assert sys.base_prefix in prefixes


# -- fail closed ------------------------------------------------------------------


@pytest.mark.usefixtures("unusable_bwrap")
class TestFailClosed:
    def test_workspace_refuses_and_starts_nothing(self, tmp_path: Path) -> None:
        ws = LocalWorkspace(tmp_path / "ws", sandbox=BwrapSandbox())
        with pytest.raises(DomainError) as exc:
            ws.execute([PY, "-c", "open('ran.txt', 'w').write('x')"], timeout_ms=10_000)
        assert exc.value.code is ErrorCode.PERMISSION_DENIED
        assert "sandbox is unavailable" in str(exc.value)
        assert "ACI_AGENT_SANDBOX=none" in str(exc.value)
        assert not (tmp_path / "ws" / "ran.txt").exists()

    def test_verifier_check_fails_visibly_without_executing(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager()
        ws_id = mgr.create_local(tmp_path / "ws", sandbox=BwrapSandbox())
        check = verification_command_check(
            mgr, ws_id, [PY, "-c", "open('ran.txt', 'w')"], allowed_prefixes=[PY]
        )
        result = check.fn(cast(Any, None), CandidateResult(summary="done"))
        assert result.passed is False
        assert result.detail.startswith("refused: process execution refused")
        assert not (tmp_path / "ws" / "ran.txt").exists()

    @pytest.mark.usefixtures("unusable_bwrap")
    def test_service_default_is_the_os_sandbox_and_refuses_a_verified_run_up_front(
        self, tmp_path: Path
    ) -> None:
        """No `process_sandbox` kwarg = the PLATFORM sandbox (bwrap on Linux,
        Seatbelt on macOS — both simulated unusable here): a run whose
        verifier would execute is a caller-visible PERMISSION_DENIED before
        any workspace copy exists."""
        service = _service(tmp_path, [FinalCandidate(summary="done")], sandbox=None)
        with pytest.raises(DomainError) as exc:
            service.run(
                _contract(),
                runtime_spec_for("researcher"),
                max_turns=3,
                workspace="proj",
                verification_command=[PY, "-c", "pass"],
            )
        assert exc.value.code is ErrorCode.PERMISSION_DENIED
        assert "sandbox is unavailable" in str(exc.value)
        assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())

    def test_service_run_command_is_refused_as_an_observation(self, tmp_path: Path) -> None:
        """A run WITHOUT a verification command still gets run_command (the
        ceiling allows it) — each call is refused at execution, nothing runs."""
        command = ToolCallBatchAction(
            calls=[
                ToolCall(
                    call_id="c1",
                    tool_id="run_command",
                    arguments={"command": [PY, "-c", "open('ran.txt', 'w')"]},
                )
            ]
        )
        read = ToolCallBatchAction(
            calls=[ToolCall(call_id="c2", tool_id="read_file", arguments={"path": "notes.txt"})]
        )
        gateway = FakeModelGateway(
            [command, read, FinalCandidate(summary="done", claims=["notes.txt: cause X"])]
        )
        service = _service(tmp_path, gateway=gateway, sandbox=None)
        result = service.run(
            _contract(), runtime_spec_for("researcher"), max_turns=6, workspace="proj"
        )
        run_dir = tmp_path / "runs" / result.run_id
        assert run_dir.is_dir()
        assert not (run_dir / "ran.txt").exists()
        observations = [
            m.content for request in gateway.requests for m in request.messages if m.role == "tool"
        ]
        assert any("sandbox is unavailable" in o for o in observations)

    def test_none_is_an_explicit_opt_out(self, tmp_path: Path) -> None:
        ws = LocalWorkspace(tmp_path / "ws", sandbox=NoSandbox())
        result = ws.execute([PY, "-c", "print('ran')"], timeout_ms=10_000)
        assert result.stdout.strip() == "ran"


def test_missing_bwrap_binary_is_unusable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The REAL probe, with a bwrap path that does not exist."""
    monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
    sb = BwrapSandbox(bwrap_path=str(tmp_path / "no-bwrap"))
    assert sb.unavailable_reason() == "bubblewrap (bwrap) is not installed"
    with pytest.raises(DomainError):
        sb.prepare(["true"], tmp_path)


# -- settings / wiring ------------------------------------------------------------


class TestWiring:
    def test_sandbox_setting_rejects_unknown_values(self) -> None:
        with pytest.raises(ValidationError):
            Settings(agent_sandbox=cast(Any, "docker"))
        with pytest.raises(ValidationError):
            Settings(agent_sandbox_max_processes=0)
        with pytest.raises(ValueError):
            build_process_sandbox("docker")

    def test_default_setting_is_the_platform_sandbox_with_limits_from_settings(self) -> None:
        settings = Settings(
            agent_sandbox_cpu_seconds=11,
            agent_sandbox_memory_mb=512,
            agent_sandbox_file_size_mb=64,
            agent_sandbox_max_processes=40,
            agent_sandbox_open_files=128,
        )
        assert settings.agent_sandbox == sandbox_mod.PLATFORM_SANDBOX_KIND
        sb = build_sandbox(settings)
        if sys.platform == "darwin":
            from aci.runtime.sandbox import SeatbeltSandbox

            assert isinstance(sb, SeatbeltSandbox)
        else:
            assert isinstance(sb, BwrapSandbox)
        assert sb.limits == ResourceLimits(
            cpu_seconds=11,
            address_space_bytes=512 * 1024**2,
            file_size_bytes=64 * 1024**2,
            max_processes=40,
            open_files=128,
        )

    @pytest.mark.usefixtures("unusable_bwrap")
    def test_unusable_sandbox_logs_a_startup_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="aci.agent_runs"):
            sb = build_sandbox(Settings(agent_process_prefixes=[PY]))
        assert sb.name == sandbox_mod.PLATFORM_SANDBOX_KIND
        assert any("UNUSABLE" in r.getMessage() for r in caplog.records)
        assert any(REASON in r.getMessage() for r in caplog.records)

    def test_opt_out_logs_a_loud_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="aci.agent_runs"):
            sb = build_sandbox(Settings(agent_sandbox="none", agent_process_prefixes=[PY]))
        assert isinstance(sb, NoSandbox)
        assert any("UNSANDBOXED" in r.getMessage() for r in caplog.records)

    @pytest.mark.usefixtures("unusable_bwrap")
    def test_no_process_authority_means_no_probe_and_no_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="aci.agent_runs"):
            build_sandbox(Settings(agent_process_prefixes=[]))
        assert not any("sandbox" in r.getMessage().lower() for r in caplog.records)
        assert sandbox_mod._PROBE_CACHE == {}

    def test_probe_runs_once_per_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
        calls: list[int] = []

        def probe(self: BwrapSandbox) -> None:
            calls.append(1)
            return None

        monkeypatch.setattr(BwrapSandbox, "_probe", probe)
        for _ in range(3):
            assert BwrapSandbox().unavailable_reason() is None
        assert len(calls) == 1


class TestHbenchSameSandbox:
    def test_both_arms_and_the_post_hoc_yardstick_use_the_given_sandbox(
        self, tmp_path: Path, unusable_bwrap: None
    ) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import run_hbench

        from aci.runtime.event_bus import EventBus

        sb = NoSandbox()
        service = run_hbench._kernel_service(
            cast(Any, None), tmp_path, tmp_path / "runs", EventBus(), sandbox=sb
        )
        assert service._process_sandbox is sb
        # The post-hoc yardstick runs model-written code: same sandbox,
        # fail closed (the old code ran it directly as the operator).
        (tmp_path / "rd").mkdir()
        with pytest.raises(DomainError):
            run_hbench._post_hoc(tmp_path / "rd", BwrapSandbox())


# -- helpers ----------------------------------------------------------------------


class _Factory:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def build(self) -> object:
        return self._obj


class _NullCapabilities:
    def handle_request(self, request: object, snapshot: object) -> list[object]:
        return []


def _service(
    tmp_path: Path,
    actions: list[Any] | None = None,
    *,
    gateway: FakeModelGateway | None = None,
    sandbox: object | None,
) -> AgentRunService:
    source = tmp_path / "sources" / "proj"
    source.mkdir(parents=True, exist_ok=True)
    (source / "notes.txt").write_text("cause is X\n", encoding="utf-8")
    gw = gateway if gateway is not None else FakeModelGateway(actions or [])
    return AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _Factory(gw)),
        tool_executor_factory=cast(ModelGatewayFactory, _Factory(None)),
        capability_runtime_factory=cast(ModelGatewayFactory, _Factory(_NullCapabilities())),
        context_engine_factory=cast(
            ModelGatewayFactory, _Factory(ContextEngine(ContextBudget(total_tokens=60_000)))
        ),
        workspace_root=tmp_path / "sources",
        runs_root=tmp_path / "runs",
        process_prefixes=[PY],
        process_sandbox=cast(Any, sandbox),
    )


def _contract() -> SubtaskContract:
    return SubtaskContract(
        task_id=f"run-{uuid.uuid4().hex[:8]}",
        objective="read the note",
        global_context="",
        constraints=[],
        acceptance_criteria=[AcceptanceCriterion(criterion_id="ac-1", description="note read")],
        requested_profile="researcher",
        budget=None,
        created_at=datetime.now(UTC),
    )
