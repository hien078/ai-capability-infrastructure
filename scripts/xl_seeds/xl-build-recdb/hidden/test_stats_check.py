"""stats/check: counters and the integrity report (README §4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def test_stats_counts_records(recdb: Any) -> None:
    for title in ("a", "b", "c"):
        recdb("add", f"title={title}")
    stats = json.loads(recdb("stats").stdout)
    assert stats["records"] == 3
    assert stats["fields"]["title"] == 3


def test_stats_fields_exclude_id(recdb: Any) -> None:
    recdb("add", "title=x", "points=2")
    stats = json.loads(recdb("stats").stdout)
    assert stats["fields"]["title"] == 1
    assert stats["fields"]["points"] == 1
    assert "id" not in stats["fields"]


def test_stats_corrupt_and_duplicates(recdb: Any, run_dir: Path) -> None:
    store = run_dir / "recdb.jsonl"
    store.write_text(
        "not json\n"
        + json.dumps({"id": 1, "title": "a"})
        + "\n"
        + json.dumps({"id": 1, "title": "b"})
        + "\n",
        encoding="utf-8",
    )
    stats = json.loads(recdb("stats").stdout)
    assert stats["records"] == 1
    assert stats["corrupt_lines"] == 1
    assert stats["duplicate_ids"] == 1


def test_check_clean_exit_0(recdb: Any) -> None:
    recdb("add", "title=a")
    proc = recdb("check")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report == {"corrupt_lines": [], "duplicate_ids": []}


def test_check_corrupt_exit_4_with_line_numbers(recdb: Any, run_dir: Path) -> None:
    store = run_dir / "recdb.jsonl"
    store.write_text(
        json.dumps({"id": 1, "title": "a"}) + "\nnot json\nalso not json\n",
        encoding="utf-8",
    )
    proc = recdb("check")
    assert proc.returncode == 4
    report = json.loads(proc.stdout)
    assert report["corrupt_lines"] == [2, 3]


def test_check_duplicates_exit_4(recdb: Any, run_dir: Path) -> None:
    store = run_dir / "recdb.jsonl"
    store.write_text(
        json.dumps({"id": "dup", "title": "a"}) + "\n" + json.dumps({"id": "dup", "title": "b"}),
        encoding="utf-8",
    )
    proc = recdb("check")
    assert proc.returncode == 4
    report = json.loads(proc.stdout)
    assert report["duplicate_ids"] == ["dup"]
