"""Pins the fixture verifiers' own hygiene contracts (measurement validity).

``verify_horizon_fixtures`` runs pytest THREE times over the same
re-materialized directory (shipped → fixed → wrong-fix). Without
``PYTHONDONTWRITEBYTECODE`` the shipped-state run leaves ``__pycache__``
behind, and a same-size same-mtime-second fix write makes CPython reuse the
stale ``.pyc`` — the FIXED-state run then silently executes the buggy code.
That trap is recorded as real in AGENTS.md (§80 long round, 2026-09-28) and
the sibling verifiers (multi/private/domain) all set the guard; this pins it
for the horizon verifier — the gate for the E1b headroom pool.
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify_horizon_fixtures as vhf  # noqa: E402


class _Proc:
    returncode = 0
    stdout = ""
    stderr = ""


def test_run_pytest_never_writes_bytecode(monkeypatch, tmp_path: Path) -> None:
    """The verifier's own pytest runs must not leave bytecode in a fixture
    tree it re-verifies — a stale .pyc makes the FIXED-state check measure
    the shipped code."""
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        captured["argv"] = argv
        return _Proc()

    monkeypatch.setattr(vhf.subprocess, "run", fake_run)
    vhf.run_pytest(tmp_path)
    assert captured["cwd"] == tmp_path
    env = captured["env"]
    assert env is not None, "run_pytest must pass an explicit env"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_every_horizon_fixture_has_both_fixes() -> None:
    """The verifier's three-pass gate only works when every fixture pins a
    root-cause fix AND a tempting symptom patch."""
    for task in vhf.HORIZON_TASKS:
        name = task["name"]
        assert name in vhf.FIXTURE_FIXES, f"{name}: no root-cause fix defined"
        assert name in vhf.WRONG_FIXES, f"{name}: no wrong-fix pin defined"
