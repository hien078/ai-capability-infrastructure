"""Store file semantics: JSONL, blank/corrupt lines, duplicates, atomicity (README §5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _write_store(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_store_file_created_and_is_jsonl(recdb: Any, run_dir: Path) -> None:
    recdb("add", "title=a")
    store = run_dir / "recdb.jsonl"
    assert store.is_file()
    lines = store.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["title"] == "a"


def test_missing_store_list_is_empty_exit_0(recdb: Any) -> None:
    proc = recdb("list")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_missing_store_stats_zeroes(recdb: Any) -> None:
    stats = json.loads(recdb("stats").stdout)
    assert stats == {
        "records": 0,
        "fields": {},
        "corrupt_lines": 0,
        "duplicate_ids": 0,
    }


def test_blank_lines_ignored(recdb: Any, run_dir: Path) -> None:
    _write_store(
        run_dir / "recdb.jsonl",
        ["", json.dumps({"id": 1, "title": "a"}), "   ", ""],
    )
    proc = recdb("list")
    assert proc.returncode == 0, proc.stderr
    records = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["title"] for r in records] == ["a"]


def test_corrupt_line_skipped_and_counted(recdb: Any, run_dir: Path) -> None:
    _write_store(
        run_dir / "recdb.jsonl",
        [json.dumps({"id": 1, "title": "a"}), "this is { not json"],
    )
    proc = recdb("list")
    assert proc.returncode == 0, proc.stderr
    assert len(proc.stdout.splitlines()) == 1
    stats = json.loads(recdb("stats").stdout)
    assert stats["records"] == 1
    assert stats["corrupt_lines"] == 1


def test_malformed_lines_are_corrupt(recdb: Any, run_dir: Path) -> None:
    _write_store(run_dir / "recdb.jsonl", ["[1, 2, 3]", json.dumps({"title": "x"})])
    stats = json.loads(recdb("stats").stdout)
    assert stats["corrupt_lines"] == 2


def test_duplicate_id_last_wins(recdb: Any, run_dir: Path) -> None:
    _write_store(
        run_dir / "recdb.jsonl",
        [
            json.dumps({"id": 1, "title": "first"}),
            json.dumps({"id": 1, "title": "second"}),
        ],
    )
    record = json.loads(recdb("get", "1").stdout)
    assert record["title"] == "second"
    stats = json.loads(recdb("stats").stdout)
    assert stats["records"] == 1
    assert stats["duplicate_ids"] == 1


def test_recdb_home_and_db_precedence(recdb: Any, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = recdb("add", "title=a", env={"RECDB_HOME": str(home)})
    assert proc.returncode == 0, proc.stderr
    assert (home / "store.jsonl").is_file()
    explicit = tmp_path / "explicit.jsonl"
    proc = recdb("add", "title=a", db=explicit, env={"RECDB_HOME": str(home)})
    assert proc.returncode == 0, proc.stderr
    assert explicit.is_file()
    # the env store was NOT written again: --db beats RECDB_HOME
    assert len((home / "store.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert len(explicit.read_text(encoding="utf-8").splitlines()) == 1


def test_atomic_write_leaves_no_temp_files(recdb: Any, run_dir: Path) -> None:
    recdb("add", "title=a")
    recdb("add", "title=b")
    assert sorted(p.name for p in run_dir.iterdir()) == ["recdb.jsonl"]


def test_db_in_missing_directory_exit_2(recdb: Any, tmp_path: Path) -> None:
    proc = recdb("add", "title=a", db=tmp_path / "nosuchdir" / "store.jsonl")
    assert proc.returncode == 2
    assert "cannot write store" in proc.stderr
