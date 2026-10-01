"""Real-Seatbelt process sandbox boundaries (macOS counterpart of the bwrap
security tests, §16.5). These drive REAL sandbox-exec and SKIP anywhere it is
unusable (non-macOS host, missing binary); the Mac CI/dev host runs them.

The Seatbelt delta vs bwrap is documented in SeatbeltSandbox: no PID/mount
namespace (host paths are VISIBLE read-only), so the pinned boundaries here
are the ones that matter — writes, network, environment, rlimits.
"""

import json
import os
import sys
from pathlib import Path

import pytest

from aci.runtime.protocols import ProcessResult
from aci.runtime.sandbox import SeatbeltSandbox
from aci.runtime.workspace import LocalWorkspace

PY = sys.executable


def seatbelt_or_skip() -> SeatbeltSandbox:
    sandbox = SeatbeltSandbox()
    reason = sandbox.unavailable_reason()
    if reason is not None:
        pytest.skip(f"seatbelt sandbox unusable on this host: {reason}")
    return sandbox


@pytest.fixture
def sandbox() -> SeatbeltSandbox:
    return seatbelt_or_skip()


@pytest.fixture
def ws(tmp_path: Path, sandbox: SeatbeltSandbox) -> LocalWorkspace:
    return LocalWorkspace(tmp_path / "runs" / "run_a", sandbox=sandbox)


def _py(ws: LocalWorkspace, code: str, timeout_ms: int = 30_000) -> ProcessResult:
    return ws.execute([PY, "-c", code], timeout_ms=timeout_ms)


# -- network --------------------------------------------------------------------


def test_network_is_unreachable(ws: LocalWorkspace) -> None:
    result = _py(
        ws,
        "import socket\n"
        "for host, port in (('127.0.0.1', 5432), ('1.1.1.1', 53)):\n"
        "    try:\n"
        "        socket.create_connection((host, port), timeout=3).close()\n"
        "        print('CONNECTED', host)\n"
        "    except OSError as e:\n"
        "        print('BLOCKED', host, type(e).__name__)\n",
    )
    assert result.exit_code == 0, result.stderr
    assert "CONNECTED" not in result.stdout
    assert result.stdout.count("BLOCKED") == 2


# -- filesystem write boundary ------------------------------------------------------


def test_writes_land_only_in_the_workspace(tmp_path: Path, ws: LocalWorkspace) -> None:
    """Seatbelt has no mount namespace: the process's cwd IS the run dir
    (HOME is the real run dir — there is no /workspace mount on macOS). Writes
    land via relative paths; absolute host paths outside the workspace are
    denied."""
    target_outside = tmp_path / "escaped.txt"
    result = _py(
        ws,
        "open('inside.txt', 'w').write('relative')\nopen('abs.txt', 'w').write('absolute')\n",
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
            json.dumps([str(target_outside), "/tmp/aci-escape.txt", "/etc/aci-escape.txt"]),
        ],
        timeout_ms=30_000,
    )
    assert escape.exit_code == 0, escape.stderr
    assert "WROTE" not in escape.stdout
    assert not target_outside.exists()
    assert not os.path.exists("/tmp/aci-escape.txt")
    assert not os.path.exists("/etc/aci-escape.txt")
    # Inside the workspace: relative paths land in the run dir.
    assert (ws.root / "inside.txt").read_text(encoding="utf-8") == "relative"
    assert (ws.root / "abs.txt").read_text(encoding="utf-8") == "absolute"


# -- filesystem read boundary ------------------------------------------------------


def test_hidden_trees_are_unreadable_but_the_workspace_is(tmp_path: Path) -> None:
    """A secret OUTSIDE the workspace in a hidden tree cannot be read or
    listed; the workspace (inside that same tree) and the interpreter can."""
    secret_dir = tmp_path / "operator-home"
    secret_dir.mkdir()
    (secret_dir / "api-key.json").write_text('{"apiKey": "sk-not-real"}', encoding="utf-8")
    sandbox = SeatbeltSandbox(extra_hidden_paths=[str(tmp_path)])
    if sandbox.unavailable_reason() is not None:
        pytest.skip("seatbelt sandbox unusable on this host")
    ws = LocalWorkspace(tmp_path / "runs" / "run_a", sandbox=sandbox)
    (ws.root / "own.txt").write_text("mine", encoding="utf-8")
    result = _py(
        ws,
        "import json, os, sys\n"
        "out = {'own': open('own.txt').read(), 'listing': len(os.listdir('.'))}\n"
        f"for name, fn in [('read', lambda: open({str(secret_dir / 'api-key.json')!r}).read()),\n"
        f"                 ('list', lambda: os.listdir({str(secret_dir)!r}))]:\n"
        "    try:\n"
        "        fn(); out[name] = 'READ'\n"
        "    except Exception as e:\n"
        "        out[name] = type(e).__name__\n"
        "print(json.dumps(out))\n",
    )
    assert result.exit_code == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["own"] == "mine" and out["listing"] >= 1
    assert out["read"] == "PermissionError"
    assert out["list"] == "PermissionError"
    assert "sk-not-real" not in result.stdout


def test_server_home_listing_is_denied(ws: LocalWorkspace) -> None:
    """The server user's $HOME (where API keys and other repos live) cannot
    be listed or read from inside the sandbox."""
    home = str(Path.home())
    result = _py(
        ws,
        "import os, sys\n"
        "try:\n"
        f"    os.listdir({home!r}); print('LISTED')\n"
        "except PermissionError:\n"
        "    print('DENIED')\n",
    )
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "DENIED"


# -- environment / identity -------------------------------------------------------


def test_environment_carries_no_server_secret(
    ws: LocalWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACI_AGENT_MODEL_API_KEY", "sk-seatbelt-sentinel-123")
    monkeypatch.setenv("ACI_AGENT_RUNS_TOKEN", "runs-token-sentinel")
    monkeypatch.setenv("ACI_DATABASE_URL", "postgresql://aci:pw-sentinel@localhost/aci")
    result = _py(ws, "import json, os; print(json.dumps(dict(os.environ)))")
    assert result.exit_code == 0, result.stderr
    env: dict[str, str] = json.loads(result.stdout)
    dumped = json.dumps(env)
    for sentinel in ("sk-seatbelt-sentinel-123", "runs-token-sentinel", "pw-sentinel"):
        assert sentinel not in dumped
    assert not any(k.startswith("ACI_") for k in env)
    assert env["HOME"] == os.path.realpath(ws.root)
    assert env["TMPDIR"] == os.path.join(os.path.realpath(ws.root), "tmp")


def test_home_writes_land_in_the_workspace(ws: LocalWorkspace) -> None:
    """m9 job verification: HOME is the REAL workspace path (the bwrap
    /workspace alias dangled on macOS — no mount namespace), so a tool
    writing to ~ lands in the run's own workspace, never in the operator's
    home."""
    result = _py(ws, "import pathlib\n(pathlib.Path.home() / 'x').write_text('1')\n")
    assert result.exit_code == 0, result.stderr
    assert (ws.root / "x").read_text(encoding="utf-8") == "1"


def test_tmpdir_is_writable_and_lands_in_the_workspace(ws: LocalWorkspace) -> None:
    """m9 job verification: TMPDIR points at the per-command tmp dir under
    the workspace (the profile's writable pair) — tempfile works inside the
    sandbox, and the file lands in the run's workspace, never in the host
    /tmp (which the profile denies)."""
    result = _py(
        ws,
        "import os, tempfile\n"
        "fd, path = tempfile.mkstemp()\n"
        "os.write(fd, b'x')\n"
        "os.close(fd)\n"
        "print(path)\n",
    )
    assert result.exit_code == 0, result.stderr
    created = result.stdout.strip().splitlines()[-1]
    real = os.path.realpath(ws.root)
    assert created.startswith(os.path.join(real, "tmp") + os.sep)
    assert (ws.root / "tmp").is_dir()


# -- the verifier's own command works -----------------------------------------------


def test_python_m_pytest_runs_inside(ws: LocalWorkspace) -> None:
    (ws.root / "test_tiny.py").write_text(
        "import os\n\n"
        "def test_inside_the_sandbox():\n"
        "    assert os.path.realpath(os.environ['HOME']) == os.path.realpath(os.getcwd())\n\n"
        "def test_arithmetic():\n"
        "    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    # The PATH-lookup arm only applies where a PATH python has pytest (the
    # CI venv); a bare macOS host python does not, so probe first.
    argvs: list[list[str]] = [[PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"]]
    import shutil

    if shutil.which("python3") and shutil.which("pytest") is not None:
        argvs.append(["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    for argv in argvs:
        result = ws.execute(argv, timeout_ms=120_000)
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "2 passed" in result.stdout


def test_python3_is_the_servers_interpreter(ws: LocalWorkspace) -> None:
    """`python3` inside the sandbox must resolve to the server's interpreter
    (the one with pytest), not macOS's /usr/bin/python3 Xcode stub."""
    result = ws.execute(["python3", "-m", "pytest", "--version"], timeout_ms=60_000)
    assert result.exit_code == 0, result.stdout + result.stderr


def test_commands_can_fork_on_a_busy_host(ws: LocalWorkspace) -> None:
    """RLIMIT_NPROC is per USER on macOS: a pipeline (forks) must still run
    when the user already owns many processes (a dev Mac always does)."""
    result = ws.execute(["/bin/sh", "-c", "echo forked | cat; ls | wc -l"], timeout_ms=30_000)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "forked" in result.stdout


def test_parent_repository_and_config_are_not_the_workspaces(tmp_path: Path) -> None:
    """No mount namespace on macOS: a workspace nested in a host repo must not
    discover that repo (git) — GIT_CEILING_DIRECTORIES stops at the workspace."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not installed")
    host_repo = tmp_path / "host-repo"
    host_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(host_repo)], check=True)
    sandbox = seatbelt_or_skip()
    ws = LocalWorkspace(host_repo / "runs" / "run_a", sandbox=sandbox)
    result = ws.execute(["git", "rev-parse", "--show-toplevel"], timeout_ms=30_000)
    assert result.exit_code != 0
    assert str(host_repo) not in result.stdout


def test_a_run_cannot_read_a_sibling_runs_workspace(tmp_path: Path) -> None:
    """A shared work root (H-bench /tmp/aci-hbench) hidden via
    extra_hidden_paths: each run reads its own workspace only. E2B measured
    the failure: K runs found arm F's standard doc in a sibling workspace."""
    from aci.runtime.sandbox import build_process_sandbox

    root = tmp_path / "work-root"
    sibling = root / "runs" / "run_f"
    (sibling / "docs").mkdir(parents=True)
    (sibling / "docs" / "standard.md").write_text("PRIVATE-STANDARD", encoding="utf-8")
    sandbox = build_process_sandbox("seatbelt", extra_hidden_paths=[str(root)])
    if sandbox.unavailable_reason() is not None:
        pytest.skip("seatbelt sandbox unusable on this host")
    ws = LocalWorkspace(root / "runs" / "run_k", sandbox=sandbox)
    (ws.root / "own.txt").write_text("mine", encoding="utf-8")
    result = _py(
        ws,
        "import os\n"
        "print(open('own.txt').read())\n"
        "try:\n"
        f"    print(open({str(sibling / 'docs' / 'standard.md')!r}).read())\n"
        "except PermissionError:\n"
        "    print('DENIED')\n",
    )
    assert result.exit_code == 0, result.stderr
    assert "mine" in result.stdout
    assert "DENIED" in result.stdout
    assert "PRIVATE-STANDARD" not in result.stdout


# -- resource limits ----------------------------------------------------------------


def test_file_size_is_bounded(tmp_path: Path) -> None:
    """RLIMIT_AS cannot be set on macOS (jetsam owns memory policy), so the
    address-space knob is NOT applied — the file-size rlimit is the one
    that must demonstrably bite here."""
    sandbox = seatbelt_or_skip()
    ws = LocalWorkspace(tmp_path / "ws", sandbox=sandbox)
    result = ws.execute(
        # fsize limit is 1 GiB by default: a 2 GiB write must die.
        [PY, "-c", "open('big.bin', 'wb').write(b'x' * (2 * 1024**3)); print('WROTE_BIG')"],
        timeout_ms=120_000,
    )
    assert result.exit_code != 0
    assert "WROTE_BIG" not in result.stdout


def test_process_count_is_bounded(tmp_path: Path) -> None:
    sandbox = seatbelt_or_skip()
    ws = LocalWorkspace(tmp_path / "ws", sandbox=sandbox)
    result = ws.execute(
        [PY, "-c", "import subprocess as s; [s.Popen(['true']) for _ in range(400)]"],
        timeout_ms=60_000,
    )
    assert result.exit_code != 0


# -- server paths never leak -------------------------------------------------------


def test_server_paths_never_appear_in_output(tmp_path: Path, ws: LocalWorkspace) -> None:
    """Seatbelt has no mount namespace: the process's cwd IS the host run
    dir (and HOME is that run dir). What must never leak are the
    SERVER's own paths — the repo, $HOME — into the model's view."""
    (ws.root / "test_fails.py").write_text(
        "import os\n\ndef test_fails():\n    assert os.getcwd() == 'nowhere'\n", encoding="utf-8"
    )
    result = ws.execute(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_fails.py"],
        timeout_ms=120_000,
    )
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert os.path.realpath(os.path.expanduser("~")) not in combined
    assert os.getcwd() not in combined  # the server cwd (the repo)


def test_run_command_and_verification_command_are_both_sandboxed(
    tmp_path: Path, ws: LocalWorkspace
) -> None:
    """Both process authorities go through the SAME sandbox: neither can write
    outside the workspace."""
    for code in ("open('a.txt', 'w')", "open('/tmp/b.txt', 'w')"):
        result = ws.execute([PY, "-c", code], timeout_ms=30_000)
        if code.startswith("open('/tmp"):
            assert result.exit_code != 0
            assert not os.path.exists("/tmp/b.txt")
        else:
            assert result.exit_code == 0, result.stderr
            assert (ws.root / "a.txt").exists()
    assert not os.path.exists("/tmp/b.txt")


def test_prepare_writes_profile_only_into_workspace(
    tmp_path: Path, sandbox: SeatbeltSandbox
) -> None:
    """The SBPL profile file (the sandbox's own config) lands in the
    workspace — never anywhere else on the host."""
    ws = tmp_path / "ws"
    ws.mkdir()
    sandbox.prepare(["true"], ws)
    profile = ws / ".aci-sandbox-profile.sb"
    assert profile.is_file()
    assert "(deny default)" in profile.read_text(encoding="utf-8")
    assert not (tmp_path / ".aci-sandbox-profile.sb").exists()


def test_probe_refuses_on_non_darwin(tmp_path: Path) -> None:
    """Fail closed: on a non-macOS host with sandbox-exec present (Linux CI
    images sometimes ship a stub), the probe still refuses."""
    if sys.platform == "darwin":
        pytest.skip("this host IS darwin")
    sandbox = SeatbeltSandbox()
    reason = sandbox.unavailable_reason()
    if reason is None:
        pytest.fail("seatbelt probe succeeded on a non-darwin host — fail-closed broken")
    from aci.domain.capability.errors import DomainError

    with pytest.raises(DomainError) as exc:
        sandbox.prepare(["true"], tmp_path)
    assert "sandbox is unavailable" in str(exc.value)
