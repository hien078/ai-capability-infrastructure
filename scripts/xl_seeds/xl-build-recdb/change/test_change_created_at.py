"""CHANGE v2 — created_at: set at insert, preserved on update/upsert, queryable."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CREATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _first_record(recdb: Any) -> dict:
    proc = recdb("list")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.splitlines()[0])


def test_change_created_at_present_and_formatted(recdb: Any) -> None:
    rid = json.loads(recdb("add", "title=a").stdout)
    record = json.loads(recdb("get", rid).stdout)
    assert CREATED_AT_RE.match(record["created_at"])


def test_change_created_at_is_recent_utc(recdb: Any) -> None:
    rid = json.loads(recdb("add", "title=a").stdout)
    record = json.loads(recdb("get", rid).stdout)
    stamp = datetime.strptime(record["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert abs((datetime.now(UTC) - stamp).total_seconds()) < 300


def test_change_update_preserves_created_at(recdb: Any) -> None:
    rid = json.loads(recdb("add", "title=a").stdout)
    before = json.loads(recdb("get", rid).stdout)["created_at"]
    assert recdb("update", rid, "title=b").returncode == 0
    after = json.loads(recdb("get", rid).stdout)
    assert after["title"] == "b"
    assert after["created_at"] == before


def test_change_import_insert_sets_created_at(recdb: Any, tmp_path: Path) -> None:
    source = tmp_path / "in.csv"
    source.write_text("title\na\n", encoding="utf-8")
    proc = recdb("import", "--file", str(source))
    assert proc.returncode == 0, proc.stderr
    assert CREATED_AT_RE.match(_first_record(recdb)["created_at"])


def test_change_import_upsert_preserves_created_at(recdb: Any, tmp_path: Path) -> None:
    rid = json.loads(recdb("add", "title=old").stdout)
    before = json.loads(recdb("get", rid).stdout)["created_at"]
    source = tmp_path / "in.csv"
    source.write_text(f"id,title\n{rid},new\n", encoding="utf-8")
    proc = recdb("import", "--file", str(source))
    assert proc.stdout.strip() == "0 imported, 1 updated"
    record = json.loads(recdb("get", rid).stdout)
    assert record["title"] == "new"
    assert record["created_at"] == before


def test_change_export_includes_created_at(recdb: Any, tmp_path: Path) -> None:
    recdb("add", "title=a")
    out = tmp_path / "out.jsonl"
    assert recdb("export", "--file", str(out)).returncode == 0
    record = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert CREATED_AT_RE.match(record["created_at"])


def test_change_created_at_queryable(recdb: Any) -> None:
    recdb("add", "title=a")
    recdb("add", "title=b")
    proc = recdb("query", "created_at>=2000-01-01")
    assert proc.returncode == 0, proc.stderr
    assert len(proc.stdout.splitlines()) == 2
    assert recdb("query", "created_at<2000-01-01").stdout == ""
