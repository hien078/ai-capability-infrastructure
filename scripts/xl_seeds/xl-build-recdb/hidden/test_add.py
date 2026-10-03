"""add: auto/explicit ids, value typing, quoting, usage errors (README §3-§4)."""

from __future__ import annotations

import json
from typing import Any


def test_add_prints_the_new_id(recdb: Any) -> None:
    proc = recdb("add", "title=x")
    assert proc.returncode == 0, proc.stderr
    json.loads(proc.stdout)  # the id, JSON-encoded


def test_add_auto_id_starts_at_1(recdb: Any) -> None:
    proc = recdb("add", "title=a")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == 1


def test_add_auto_id_increments(recdb: Any) -> None:
    ids = [json.loads(recdb("add", f"title={t}").stdout) for t in ("a", "b", "c")]
    assert ids == [1, 2, 3]


def test_add_record_exact_fields(recdb: Any) -> None:
    rid = json.loads(recdb("add", "title=x").stdout)
    record = json.loads(recdb("get", str(rid)).stdout)
    assert record == {"id": rid, "title": "x"}


def test_add_explicit_ids(recdb: Any) -> None:
    assert json.loads(recdb("add", "id=abc", "title=x").stdout) == "abc"
    assert json.loads(recdb("add", "id=7", "title=x").stdout) == 7


def test_add_duplicate_explicit_id_exit_2(recdb: Any) -> None:
    assert recdb("add", "id=dup", "a=1").returncode == 0
    proc = recdb("add", "id=dup", "b=2")
    assert proc.returncode == 2
    assert "duplicate id" in proc.stderr


def test_add_value_typing(recdb: Any) -> None:
    rid = json.loads(recdb("add", "n=3", "f=3.5", "yes=true", "no=false", "nothing=null").stdout)
    record = json.loads(recdb("get", str(rid)).stdout)
    assert isinstance(record["n"], int) and record["n"] == 3
    assert isinstance(record["f"], float) and record["f"] == 3.5
    assert record["yes"] is True
    assert record["no"] is False
    assert record["nothing"] is None


def test_add_quoted_values_stay_strings(recdb: Any) -> None:
    rid = json.loads(recdb("add", 'n="3"', "m='4'").stdout)
    record = json.loads(recdb("get", str(rid)).stdout)
    assert record["n"] == "3"
    assert record["m"] == "4"


def test_add_value_with_spaces_and_equals(recdb: Any) -> None:
    rid = json.loads(recdb("add", "title=two words", "note=a=b").stdout)
    record = json.loads(recdb("get", str(rid)).stdout)
    assert record["title"] == "two words"
    assert record["note"] == "a=b"


def test_add_unicode_value_roundtrip(recdb: Any) -> None:
    rid = json.loads(recdb("add", "title=naïve-π-λ").stdout)
    record = json.loads(recdb("get", str(rid)).stdout)
    assert record["title"] == "naïve-π-λ"


def test_add_json_flag(recdb: Any) -> None:
    proc = recdb("add", "--json", "title=x", "points=2")
    assert proc.returncode == 0, proc.stderr
    record = json.loads(proc.stdout)
    assert record["title"] == "x"
    assert record["points"] == 2
    assert "id" in record


def test_add_usage_errors_exit_2(recdb: Any) -> None:
    assert recdb("add", "title").returncode == 2  # pair without =
    assert recdb("add", "=value").returncode == 2  # empty key
    assert recdb("add", "a=1", "a=2").returncode == 2  # duplicate field
