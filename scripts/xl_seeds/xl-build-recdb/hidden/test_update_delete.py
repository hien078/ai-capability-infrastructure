"""update/delete: field mutation, id immutability, not-found (README §4)."""

from __future__ import annotations

import json
from typing import Any


def test_update_sets_fields(recdb: Any) -> None:
    rid = json.loads(recdb("add", "a=1").stdout)
    proc = recdb("update", str(rid), "b=2")
    assert proc.returncode == 0, proc.stderr
    record = json.loads(recdb("get", str(rid)).stdout)
    assert record["a"] == 1
    assert record["b"] == 2


def test_update_prints_updated_record(recdb: Any) -> None:
    rid = json.loads(recdb("add", "a=1").stdout)
    proc = recdb("update", str(rid), "a=9")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["a"] == 9


def test_update_unset(recdb: Any) -> None:
    rid = json.loads(recdb("add", "a=1", "b=2").stdout)
    proc = recdb("update", str(rid), "--unset", "b")
    assert proc.returncode == 0, proc.stderr
    record = json.loads(proc.stdout)
    assert "b" not in record
    assert record["a"] == 1
    # unsetting a field the record does not have is silent
    assert recdb("update", str(rid), "--unset", "nosuch").returncode == 0


def test_update_typing(recdb: Any) -> None:
    rid = json.loads(recdb("add", "n=1").stdout)
    recdb("update", str(rid), "n=3.5", "flag=true")
    record = json.loads(recdb("get", str(rid)).stdout)
    assert record["n"] == 3.5
    assert record["flag"] is True


def test_update_unknown_id_exit_3(recdb: Any) -> None:
    proc = recdb("update", "99", "a=1")
    assert proc.returncode == 3
    assert "not found" in proc.stderr


def test_update_cannot_set_id_exit_2(recdb: Any) -> None:
    rid = json.loads(recdb("add", "a=1").stdout)
    assert recdb("update", str(rid), "id=5").returncode == 2


def test_update_cannot_unset_id_exit_2(recdb: Any) -> None:
    rid = json.loads(recdb("add", "a=1").stdout)
    assert recdb("update", str(rid), "--unset", "id").returncode == 2


def test_delete_removes(recdb: Any) -> None:
    rid = json.loads(recdb("add", "title=a").stdout)
    proc = recdb("delete", str(rid))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert recdb("get", str(rid)).returncode == 3
    assert recdb("list").stdout == ""


def test_delete_unknown_and_twice_exit_3(recdb: Any) -> None:
    assert recdb("delete", "99").returncode == 3
    rid = json.loads(recdb("add", "title=a").stdout)
    assert recdb("delete", str(rid)).returncode == 0
    assert recdb("delete", str(rid)).returncode == 3
