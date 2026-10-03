"""Change-suite harness for the recdb fixture — identical to the original
suite's: ``XL_WORKSPACE`` points at the fixture workspace, every test runs
``python -m recdb`` as a real subprocess with the workspace on PYTHONPATH
and a fresh run directory as cwd.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

WS = Path(os.environ.get("XL_WORKSPACE", ""))


def pytest_configure(config: Any) -> None:
    if not WS.is_dir():
        raise pytest.UsageError(f"XL_WORKSPACE is not a directory: {WS}")


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """A fresh cwd for CLI calls (the default store lands here)."""
    run = tmp_path / "run"
    run.mkdir()
    return run


@pytest.fixture
def recdb(run_dir: Path) -> Any:
    """Run the CLI: ``recdb(*args, db=..., env=..., cwd=...)`` -> CompletedProcess."""

    def _run(
        *args: Any,
        db: Path | str | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = [sys.executable, "-m", "recdb"]
        if db is not None:
            argv += ["--db", str(db)]
        argv += [str(a) for a in args]
        full_env = {
            **os.environ,
            "PYTHONPATH": str(WS),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if env:
            full_env.update(env)
        return subprocess.run(
            argv,
            cwd=str(cwd or run_dir),
            env=full_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    return _run
