"""import/export: CSV/JSONL upsert, cell typing, export shapes (README §4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _records(proc: Any) -> list[dict]:
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines()]


def test_import_csv_basic(recdb: Any, tmp_path: Path) -> None:
    source = tmp_path / "in.csv"
    source.write_text("title,points\nalpha,3\nbeta,9\n", encoding="utf-8")
    proc = recdb("import", "--file", str(source))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "2 imported, 0 updated"
    records = _records(recdb("list"))
    assert [r["title"] for r in records] == ["alpha", "beta"]
    assert records[0]["points"] == 3


def test_import_csv_upsert_and_explicit_ids(recdb: Any, tmp_path: Path) -> None:
    rid = json.loads(recdb("add", "id=1", "title=old", "keep=yes").stdout)
    source = tmp_path / "in.csv"
    source.write_text("id,title\n1,new\nxyz,thing\n", encoding="utf-8")
    proc = recdb("import", "--file", str(source))
    assert proc.stdout.strip() == "1 imported, 1 updated"
    record = json.loads(recdb("get", str(rid)).stdout)
    assert record["title"] == "new"
    assert record["keep"] == "yes"  # fields absent from the row are kept
    assert json.loads(recdb("get", "xyz").stdout)["title"] == "thing"


def test_import_csv_auto_id(recdb: Any, tmp_path: Path) -> None:
    source = tmp_path / "in.csv"
    source.write_text("title\na\nb\n", encoding="utf-8")
    proc = recdb("import", "--file", str(source))
    assert proc.returncode == 0, proc.stderr
    assert len(_records(recdb("list"))) == 2


def test_import_csv_cell_typing(recdb: Any, tmp_path: Path) -> None:
    source = tmp_path / "in.csv"
    source.write_text(
        'n,f,yes,no,nothing,text,empty\n3,3.5,true,false,null,hello,\n"a,b","line\nbreak"\n',
        encoding="utf-8",
    )
    recdb("import", "--file", str(source))
    record = _records(recdb("list"))[0]
    assert record["n"] == 3
    assert record["f"] == 3.5
    assert record["yes"] is True
    assert record["no"] is False
    assert record["nothing"] is None
    assert record["text"] == "hello"
    assert record["empty"] == ""
    second = _records(recdb("list"))[1]
    assert second["n"] == "a,b"  # quoted cells keep their commas/newlines
    assert second["f"] == "line\nbreak"


def test_import_missing_file_exit_2(recdb: Any, tmp_path: Path) -> None:
    proc = recdb("import", "--file", str(tmp_path / "nope.csv"))
    assert proc.returncode == 2
    assert "cannot read" in proc.stderr


def test_import_jsonl_and_upsert(recdb: Any, tmp_path: Path) -> None:
    source = tmp_path / "in.jsonl"
    source.write_text(
        json.dumps({"id": "k1", "title": "a"}) + "\n" + json.dumps({"id": "k2", "n": 2}) + "\n",
        encoding="utf-8",
    )
    proc = recdb("import", "--file", str(source), "--format", "jsonl")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "2 imported, 0 updated"
    source.write_text(json.dumps({"id": "k1", "title": "z"}) + "\n", encoding="utf-8")
    proc = recdb("import", "--file", str(source), "--format", "jsonl")
    assert proc.stdout.strip() == "0 imported, 1 updated"
    assert json.loads(recdb("get", "k1").stdout)["title"] == "z"
    assert json.loads(recdb("get", "k2").stdout)["n"] == 2


def test_import_jsonl_non_object_exit_2(recdb: Any, tmp_path: Path) -> None:
    source = tmp_path / "in.jsonl"
    source.write_text("[1, 2]\n", encoding="utf-8")
    proc = recdb("import", "--file", str(source), "--format", "jsonl")
    assert proc.returncode == 2
    assert recdb("list").stdout == ""  # all-or-nothing: nothing was saved


def test_export_jsonl(recdb: Any, tmp_path: Path) -> None:
    recdb("add", "title=a", "points=1")
    recdb("add", "title=b", "points=2")
    out = tmp_path / "out.jsonl"
    proc = recdb("export", "--file", str(out))
    assert proc.returncode == 0, proc.stderr
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["title"] for r in records] == ["a", "b"]


def test_export_csv_columns_sorted(recdb: Any, tmp_path: Path) -> None:
    recdb("add", "title=a", "zebra=z")
    recdb("add", "title=b", "alpha=1")
    out = tmp_path / "out.csv"
    proc = recdb("export", "--file", str(out), "--format", "csv")
    assert proc.returncode == 0, proc.stderr
    lines = out.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    assert header == sorted(header)
    assert set(header) >= {"id", "title", "zebra", "alpha"}


def test_export_csv_values(recdb: Any, tmp_path: Path) -> None:
    recdb("add", "title=a", "extra=x", "flag=true", "note=null", "n=4")
    recdb("add", "title=b")
    out = tmp_path / "out.csv"
    recdb("export", "--file", str(out), "--format", "csv")
    lines = out.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    rows = [dict(zip(header, row.split(","), strict=True)) for row in lines[1:]]
    assert rows[0]["flag"] == "true"
    assert rows[0]["note"] == "null"
    assert rows[0]["n"] == "4"
    assert rows[0]["extra"] == "x"
    assert rows[1]["extra"] == ""  # missing field -> empty cell


def test_export_where_filter(recdb: Any, tmp_path: Path) -> None:
    recdb("add", "title=a", "points=1")
    recdb("add", "title=b", "points=9")
    out = tmp_path / "out.jsonl"
    recdb("export", "--file", str(out), "--where", "points>5")
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["title"] for r in records] == ["b"]


def test_export_missing_parent_dir_exit_2(recdb: Any, tmp_path: Path) -> None:
    proc = recdb("export", "--file", str(tmp_path / "nosuchdir" / "out.jsonl"))
    assert proc.returncode == 2
    assert "cannot write" in proc.stderr


def test_export_import_roundtrip(recdb: Any, tmp_path: Path, run_dir: Path) -> None:
    recdb("add", "title=a", "points=1", "flag=true")
    recdb("add", 'id="fixed"', "title=b")
    out = tmp_path / "out.jsonl"
    assert recdb("export", "--file", str(out)).returncode == 0
    (run_dir / "recdb.jsonl").unlink()
    proc = recdb("import", "--file", str(out), "--format", "jsonl")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "2 imported, 0 updated"
    records = _records(recdb("list"))
    assert len(records) == 2
    assert json.loads(recdb("get", '"fixed"').stdout)["title"] == "b"
