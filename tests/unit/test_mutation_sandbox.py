"""Mutation-audit killing tests for ``src/aci/runtime/sandbox.py`` (job m6-mutation-audit).

Each test kills a class of surviving mutants from the mutmut before-run
(sandbox: 238 mutants, 124 killed + 15 suspicious, 99 survived). The
behaviors below had NO test in the module's relevant subset:

* ``minimal_process_env``: the exact §16.4 allowlist (interpreter bin dir
  FIRST, server PATH entries kept, relative entries dropped, TZ passthrough);
* ``ResourceLimits``/``SandboxedCommand`` are frozen; the limit boundary is
  ``> 0`` (1 is valid);
* ``ro_binds``: an EXISTING relative path is still dropped, a valid prefix
  after a dropped one is kept, a symlink binds its realpath alias, and a
  symlink INTO a protected ancestor binds neither;
* ``sandbox_env`` PATH keeps entries under the read-only binds and falls back
  to ``/usr/bin`` when nothing is visible;
* ``build_argv`` carries the profile: /etc binds, the /bin //sbin system
  binds, and the sandbox hostname;
* the fail-closed probe: a working (fake) bwrap probes clean, a failing one
  produces a caller-safe reason, and the probe cache is keyed per profile.

Platform note: the ``--symlink`` branch for ``_SYSTEM_LINKS`` and the real
bwrap behaviors are exercised only where those exist (Linux CI runs
tests/security/test_process_sandbox.py with real bwrap); on this macOS host
the probe is pinned via fake executables.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import aci.runtime.sandbox as sandbox_mod
from aci.runtime.sandbox import (
    BwrapSandbox,
    NoSandbox,
    ResourceLimits,
    SandboxedCommand,
    minimal_process_env,
)


def _has(argv: list[str], *flags: str) -> bool:
    wanted = list(flags)
    return any(argv[i : i + len(wanted)] == wanted for i in range(len(argv) - len(wanted) + 1))


class TestMinimalProcessEnv:
    def test_allowlist_is_exact_and_tz_passes_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", os.pathsep.join(["/custom/bin", "/usr/bin", "rel"]))
        monkeypatch.setenv("TZ", "Europe/Berlin")
        env = minimal_process_env(tmp_path / "home")
        assert env == {
            "PATH": os.pathsep.join([os.path.dirname(sys.executable), "/custom/bin", "/usr/bin"]),
            "HOME": str(tmp_path / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "TZ": "Europe/Berlin",
        }

    def test_no_tz_no_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TZ", raising=False)
        env = minimal_process_env(tmp_path / "home")
        assert "TZ" not in env


class TestFrozenDataclasses:
    def test_resource_limits_frozen_and_boundary(self) -> None:
        limits = ResourceLimits(cpu_seconds=1)  # the boundary: 1 is valid, 0 is not
        assert limits.cpu_seconds == 1
        with pytest.raises(ValueError):
            ResourceLimits(cpu_seconds=0)
        with pytest.raises(AttributeError):
            limits.cpu_seconds = 2  # type: ignore[misc]

    def test_sandboxed_command_frozen(self, tmp_path: Path) -> None:
        command = NoSandbox().prepare(["true"], tmp_path)
        assert isinstance(command, SandboxedCommand)
        with pytest.raises(AttributeError):
            command.argv = []  # type: ignore[misc]


class TestReadOnlyBinds:
    def test_existing_relative_path_is_still_dropped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "rel").mkdir()  # exists, relative — must STILL be dropped
        sb = BwrapSandbox(ro_prefixes=["rel"])
        assert "rel" not in sb.ro_binds()

    def test_valid_prefix_survives_a_dropped_one(self, tmp_path: Path) -> None:
        toolchain = tmp_path / "toolchain"
        toolchain.mkdir()
        # a MISSING first entry is dropped; the valid one after it is kept
        sb = BwrapSandbox(ro_prefixes=[str(tmp_path / "missing"), str(toolchain)])
        assert str(toolchain) in sb.ro_binds()
        # and it is actually bound read-only in the argv
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = sb.build_argv(["true"], ws)
        assert _has(argv, "--ro-bind", str(toolchain), str(toolchain))

    def test_symlink_binds_its_realpath_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)
        sb = BwrapSandbox(ro_prefixes=[str(link)])
        binds = sb.ro_binds()
        assert str(link) in binds
        assert str(target) in binds  # the realpath alias

    def test_symlink_into_protected_ancestor_binds_neither(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        ws = tmp_path / "ws"
        ws.mkdir()
        link = tmp_path / "ancestor-link"
        link.symlink_to(tmp_path)  # an ancestor of the workspace
        sb = BwrapSandbox(ro_prefixes=[str(link)])
        binds = sb.ro_binds()
        assert str(link) not in binds
        assert str(tmp_path) not in binds


class TestSandboxEnvPath:
    def test_entries_under_binds_stay_visible(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        toolchain = tmp_path / "toolchain"
        bind = toolchain / "bin"
        bind.mkdir(parents=True)
        monkeypatch.setenv("PATH", os.pathsep.join([str(bind), "/definitely/not/visible"]))
        sb = BwrapSandbox(ro_prefixes=[str(toolchain)])
        entries = sb.sandbox_env(tmp_path / "ws")["PATH"].split(os.pathsep)
        assert str(bind) in entries
        assert "/definitely/not/visible" not in entries

    def test_fallback_is_usr_bin_when_nothing_visible(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", "relative-entry")
        monkeypatch.setattr(BwrapSandbox, "ro_binds", lambda self, ws=None: [])
        try:
            env = BwrapSandbox().sandbox_env(tmp_path / "ws")
        finally:
            monkeypatch.undo()
        assert env["PATH"] == "/usr/bin"


class TestBuildArgvProfile:
    def test_etc_binds_system_dirs_and_hostname(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BwrapSandbox, "ro_binds", lambda self, ws=None: [])
        ws = tmp_path / "ws"
        ws.mkdir()
        try:
            argv = BwrapSandbox().build_argv(["true"], ws)
        finally:
            monkeypatch.undo()
        assert argv[1:4] == ["--ro-bind", "/usr", "/usr"]
        assert _has(argv, "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache")
        assert _has(argv, "--ro-bind-try", "/etc/localtime", "/etc/localtime")
        assert _has(argv, "--hostname", "sandbox")
        # system dirs that are real directories here (symlinks are a Linux
        # merged-/usr concern — that branch is exercised on Linux CI)
        for link in ("/bin", "/sbin"):
            if os.path.isdir(link) and not os.path.islink(link):
                assert _has(argv, "--ro-bind", link, link)


def _fake_exec(path: Path, body: str) -> str:
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


class TestProbe:
    def test_working_bwrap_probes_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
        bwrap = _fake_exec(tmp_path / "bwrap.sh", "#!/bin/sh\nexit 0\n")
        prlimit = _fake_exec(tmp_path / "prlimit.sh", "#!/bin/sh\nexit 0\n")
        sb = BwrapSandbox(bwrap_path=bwrap, prlimit_path=prlimit)
        assert sb.unavailable_reason() is None

    def test_failing_bwrap_produces_a_caller_safe_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
        bwrap = _fake_exec(
            tmp_path / "bwrap.sh",
            "#!/bin/sh\necho 'setting up uid map: Permission denied' >&2\nexit 1\n",
        )
        prlimit = _fake_exec(tmp_path / "prlimit.sh", "#!/bin/sh\nexit 0\n")
        sb = BwrapSandbox(bwrap_path=bwrap, prlimit_path=prlimit)
        reason = sb.unavailable_reason()
        assert reason is not None
        assert reason.startswith("bwrap probe failed")

    def test_probe_cache_is_keyed_per_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sandbox_mod, "_PROBE_CACHE", {})
        prlimit = _fake_exec(tmp_path / "prlimit.sh", "#!/bin/sh\nexit 0\n")
        ok = _fake_exec(tmp_path / "ok.sh", "#!/bin/sh\nexit 0\n")
        bad = _fake_exec(tmp_path / "bad.sh", "#!/bin/sh\nexit 1\n")
        failing = BwrapSandbox(bwrap_path=bad, prlimit_path=prlimit)
        working = BwrapSandbox(bwrap_path=ok, prlimit_path=prlimit)
        assert failing.unavailable_reason() is not None
        # a DIFFERENT profile must not inherit that failure
        assert working.unavailable_reason() is None
