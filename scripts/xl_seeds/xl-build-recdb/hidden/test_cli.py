"""CLI contract: exit codes, persistence across processes, --db flag (README §6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def test_no_args_exit_2(recdb: Any) -> None:
    proc = recdb()
    assert proc.returncode == 2
    assert proc.stdout == ""


def test_unknown_command_exit_2(recdb: Any) -> None:
    proc = recdb("frobnicate", "--whatever")
    assert proc.returncode == 2


def test_persistence_across_processes(recdb: Any) -> None:
    first = recdb("add", "title=a")
    assert first.returncode == 0, first.stderr
    rid = json.loads(first.stdout)
    second = recdb("get", str(rid))
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["title"] == "a"


def test_db_flag_before_command(recdb: Any, tmp_path: Path) -> None:
    store = tmp_path / "elsewhere.jsonl"
    proc = recdb("add", "title=a", db=store)
    assert proc.returncode == 0, proc.stderr
    assert store.is_file()
    rid = json.loads(proc.stdout)
    assert json.loads(recdb("get", str(rid), db=store).stdout)["title"] == "a"


def test_stats_and_check_output_shape(recdb: Any) -> None:
    stats = json.loads(recdb("stats").stdout)
    assert set(stats) == {"records", "fields", "corrupt_lines", "duplicate_ids"}
    report = json.loads(recdb("check").stdout)
    assert set(report) == {"corrupt_lines", "duplicate_ids"}
