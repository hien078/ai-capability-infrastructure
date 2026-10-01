"""SeatbeltSandbox unit tests (macOS sandbox-exec; §16.5 platform counterpart).

The SBPL profile and the argv are built PURELY, so the contract is pinned here
on every host (Linux CI included — no macOS needed): deny-default, the
workspace as the ONLY writable subpath, no network, cleared env, ulimit
wrapper. Usability is simulated for the FAIL-CLOSED paths; the real
sandbox-exec behavior lives in tests/security/test_seatbelt_sandbox.py
(macOS-only, skips elsewhere).
"""

import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

import aci.runtime.sandbox as sandbox_mod
from aci.adapters.inbound.rest.agent_run_wiring import build_sandbox
from aci.config import Settings
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.runtime.sandbox import (
    WORKSPACE_MOUNT,
    NoSandbox,
    ResourceLimits,
    SeatbeltSandbox,
    build_platform_default_sandbox,
    build_process_sandbox,
)
from aci.runtime.workspace import LocalWorkspace

REASON = "simulated: sandbox-exec is not installed"
PROFILE_NAME = ".aci-sandbox-profile.sb"


@pytest.fixture
def unusable_seatbelt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every SeatbeltSandbox in this test probes as unusable (fresh cache)."""
    monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
    monkeypatch.setattr(SeatbeltSandbox, "_probe", lambda self: REASON)


def _sandbox(tmp_path: Path, **kwargs: Any) -> SeatbeltSandbox:
    prefix = tmp_path / "interp"
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    kwargs.setdefault("ro_prefixes", [str(prefix)])
    return SeatbeltSandbox(sandbox_exec_path="/usr/bin/sandbox-exec", **kwargs)


class TestProfile:
    def test_deny_default_with_workspace_as_the_only_write_subpath(self, tmp_path: Path) -> None:
        ws = tmp_path / "run" / "ws"
        ws.mkdir(parents=True)
        profile = _sandbox(tmp_path).profile(ws)
        assert "(deny default)" in profile
        assert "(deny network*)" in profile
        real = os.path.realpath(ws)
        assert f'(allow file-write* (subpath "{real}"))' in profile
        assert f'(allow file-write* (subpath "{real}/tmp"))' in profile
        # Writable SUBPATHS: exactly the workspace pair. The only other
        # file-write allows are the four fixed character devices.
        subpaths = profile.count("(allow file-write* (subpath")
        assert subpaths == 2
        for dev in ("/dev/null", "/dev/urandom", "/dev/random", "/dev/zero"):
            assert f'(allow file-write* (literal "{dev}"))' in profile
        assert profile.count("(allow file-write*") == 6

    def test_reads_are_allowed_system_wide(self, tmp_path: Path) -> None:
        profile = _sandbox(tmp_path).profile(tmp_path)
        assert "(allow file-read*)" in profile
        assert "(allow process-exec)" in profile
        assert "(allow process-fork)" in profile

    def test_user_trees_and_home_contents_are_hidden(self, tmp_path: Path) -> None:
        """File CONTENTS under /Users, /Volumes, the per-user temp tree,
        root's home and the server's $HOME are denied (API keys, ssh keys,
        other repos) — the bwrap "no $HOME/repo/data" property."""
        profile = _sandbox(tmp_path).profile(tmp_path)
        deny = next(
            line for line in profile.splitlines() if line.startswith("(deny file-read-data")
        )
        for root in (*sandbox_mod.HIDDEN_READ_ROOTS, str(Path.home())):
            assert f'(subpath "{os.path.normpath(root)}")' in deny

    def test_workspace_and_interpreter_are_reallowed_after_the_deny(self, tmp_path: Path) -> None:
        """SBPL: the last matching rule wins — the re-allow (workspace +
        interpreter trees) must come AFTER the hidden-root deny."""
        ws = tmp_path / "ws"
        ws.mkdir()
        lines = _sandbox(tmp_path).profile(ws).splitlines()
        deny_at = next(i for i, ln in enumerate(lines) if ln.startswith("(deny file-read-data"))
        allow_at = next(i for i, ln in enumerate(lines) if ln.startswith("(allow file-read-data"))
        assert allow_at > deny_at
        assert f'(subpath "{os.path.realpath(ws)}")' in lines[allow_at]
        assert f'(subpath "{os.path.realpath(tmp_path / "interp")}")' in lines[allow_at]

    @pytest.mark.parametrize("ancestor", ["/", "/Users", "HOME"])
    def test_a_prefix_that_contains_a_hidden_root_is_never_reallowed(
        self, tmp_path: Path, ancestor: str
    ) -> None:
        """Re-allowing `/`, `/Users` or `$HOME` as an "interpreter prefix"
        would re-open the hidden trees — such prefixes are dropped."""
        path = str(Path.home()) if ancestor == "HOME" else ancestor
        sandbox = _sandbox(tmp_path, ro_prefixes=[path], extra_ro_binds=[path])
        lines = sandbox.profile(tmp_path).splitlines()
        allow = next(ln for ln in lines if ln.startswith("(allow file-read-data"))
        assert f'(subpath "{os.path.normpath(path)}")' not in allow
        assert f'(subpath "{os.path.realpath(path)}")' not in allow

    def test_quote_in_workspace_path_is_refused_at_prepare(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `"` in the workspace path cannot be expressed in SBPL — prepare
        refuses (fail closed) rather than widening the write allowlist."""
        monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
        monkeypatch.setattr(SeatbeltSandbox, "_probe", lambda self: None)
        sb = _sandbox(tmp_path)
        quoted = tmp_path / 'we"ird'
        with pytest.raises(DomainError) as exc:
            sb.prepare(["true"], quoted)
        assert exc.value.code is ErrorCode.PERMISSION_DENIED
        assert "quote" in str(exc.value)


class TestArgv:
    def test_env_is_cleared_and_minimal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACI_AGENT_MODEL_API_KEY", "sk-seatbelt-unit-sentinel")
        monkeypatch.setenv("ACI_DATABASE_URL", "postgresql://u:pw-sentinel@db/aci")
        ws = tmp_path / "ws"
        ws.mkdir()
        sb = _sandbox(tmp_path)
        argv = sb.build_argv(["python", "-m", "pytest"], ws)
        assert argv[0] == "/usr/bin/env"
        assert argv[1] == "-i"
        assert "sk-seatbelt-unit-sentinel" not in " ".join(argv)
        assert "pw-sentinel" not in " ".join(argv)
        # env -i assignments sit between -i and sandbox-exec.
        i = argv.index(sb._sandbox_exec)
        env_pairs = dict(p.split("=", 1) for p in argv[2:i])
        assert env_pairs == sb.sandbox_env(ws)
        assert env_pairs["HOME"] == WORKSPACE_MOUNT
        assert set(env_pairs) <= {
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONUNBUFFERED",
            "TZ",
        }

    def test_command_runs_under_ulimit_wrapper(self, tmp_path: Path) -> None:
        limits = ResourceLimits(
            cpu_seconds=7,
            address_space_bytes=123 * 1024**2,
            file_size_bytes=5 * 1024**2,
            max_processes=33,
            open_files=99,
        )
        argv = _sandbox(tmp_path, limits=limits).build_argv(["pytest", "-q"], tmp_path)
        # The wrapper: sh -c 'ulimit ...; exec <command>' — NO -v (RLIMIT_AS
        # cannot be set on macOS; memory is bounded by the timeout instead).
        sh_at = argv.index("/bin/sh")
        assert argv[sh_at + 1] == "-c"
        script = argv[sh_at + 2]
        assert script.startswith("ulimit -t 7 ")
        assert " -v " not in script
        assert f"-f {5 * 1024**2 // 1024} " in script
        assert "-u 33 " in script
        assert "-n 99; " in script
        assert "exec 'pytest' '-q'" in script

    def test_profile_file_is_written_into_the_workspace(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        sb = _sandbox(tmp_path)
        argv = sb.build_argv(["true"], ws)
        # -f <workspace>/.aci-sandbox-profile.sb — the one writable place.
        f_at = argv.index("-f")
        assert argv[f_at + 1].endswith(PROFILE_NAME)
        assert os.path.realpath(ws) in argv[f_at + 1]

    def test_path_reduced_to_system_and_exec_prefixes(self, tmp_path: Path) -> None:
        prefix = tmp_path / "toolchain"
        (prefix / "bin").mkdir(parents=True)
        sb = _sandbox(tmp_path, ro_prefixes=[str(prefix)])
        entries = sb.sandbox_env()["PATH"].split(os.pathsep)
        assert str(prefix / "bin") in entries
        assert all(os.path.isabs(e) for e in entries)
        assert all(e not in entries for e in [os.path.expanduser("~") + "/.local/bin"])


class TestFailClosed:
    @pytest.mark.usefixtures("unusable_seatbelt")
    def test_workspace_refuses_and_starts_nothing(self, tmp_path: Path) -> None:
        ws = LocalWorkspace(tmp_path / "ws", sandbox=SeatbeltSandbox())
        with pytest.raises(DomainError) as exc:
            ws.execute([sys.executable, "-c", "open('ran.txt', 'w').write('x')"], timeout_ms=10_000)
        assert exc.value.code is ErrorCode.PERMISSION_DENIED
        assert "sandbox is unavailable" in str(exc.value)
        assert "ACI_AGENT_SANDBOX=none" in str(exc.value)
        assert not (tmp_path / "ws" / "ran.txt").exists()

    def test_non_darwin_host_is_unusable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The REAL probe on a non-macOS host: refused, never a silent run.
        (Where sandbox-exec exists but the host is Linux, the platform check
        is the reason; where the binary is missing, that is the reason.)"""
        monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
        sb = SeatbeltSandbox(sandbox_exec_path="/usr/bin/sandbox-exec")
        reason = sb.unavailable_reason()
        if sys.platform == "darwin":
            pytest.skip("this host IS darwin — see the security tests")
        assert reason is not None
        if os.access("/usr/bin/sandbox-exec", os.X_OK):
            assert "macOS-only" in reason
        else:
            assert "not installed" in reason

    def test_missing_sandbox_exec_is_unusable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
        sb = SeatbeltSandbox(sandbox_exec_path=str(tmp_path / "no-sandbox-exec"))
        assert sb.unavailable_reason() == "sandbox-exec (Seatbelt) is not installed"
        with pytest.raises(DomainError):
            sb.prepare(["true"], tmp_path)


class TestSettings:
    def test_seatbelt_kind_builds_with_limits(self) -> None:
        limits = ResourceLimits(cpu_seconds=9, max_processes=40)
        sb = build_process_sandbox("seatbelt", limits=limits)
        assert isinstance(sb, SeatbeltSandbox)
        assert sb.limits == limits

    def test_unknown_kind_still_raises(self) -> None:
        with pytest.raises(ValueError):
            build_process_sandbox(cast(Any, "docker"))

    def test_settings_accept_seatbelt_and_default_follows_platform(self) -> None:
        settings = Settings(agent_sandbox="seatbelt")
        assert settings.agent_sandbox == "seatbelt"
        sb = build_sandbox(settings)
        assert isinstance(sb, SeatbeltSandbox)
        with pytest.raises(ValidationError):
            Settings(agent_sandbox=cast(Any, "docker"))
        # The settings default and the platform default agree.
        assert Settings().agent_sandbox == sandbox_mod.PLATFORM_SANDBOX_KIND

    def test_platform_default_is_the_os_sandbox(self) -> None:
        sb = build_platform_default_sandbox()
        if sys.platform == "darwin":
            assert isinstance(sb, SeatbeltSandbox)
        else:
            assert isinstance(sb, sandbox_mod.BwrapSandbox)

    def test_opt_out_is_explicit_none(self) -> None:
        assert isinstance(build_process_sandbox(cast(Any, "none")), NoSandbox)
