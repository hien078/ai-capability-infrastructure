"""CHANGE v2 — ids: zero-padded string auto ids (r0001, …), explicit ids unchanged."""

from __future__ import annotations

import json
from typing import Any


def test_change_auto_id_format(recdb: Any) -> None:
    proc = recdb("add", "title=a")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == "r0001"


def test_change_auto_id_sequence(recdb: Any) -> None:
    ids = [json.loads(recdb("add", f"title={t}").stdout) for t in ("a", "b", "c")]
    assert ids == ["r0001", "r0002", "r0003"]


def test_change_auto_ids_sort_lexically(recdb: Any) -> None:
    for i in range(12):
        assert recdb("add", f"title=t{i}").returncode == 0
    proc = recdb("list", "--sort", "id")
    assert proc.returncode == 0, proc.stderr
    ids = [json.loads(line)["id"] for line in proc.stdout.splitlines()]
    assert ids[0] == "r0001"
    assert ids[-1] == "r0012"
    assert ids == sorted(ids)  # zero-padded: lexical order == numeric order


def test_change_explicit_ids_unchanged(recdb: Any) -> None:
    assert json.loads(recdb("add", "id=abc", "a=1").stdout) == "abc"
    assert json.loads(recdb("add", "id=7", "b=2").stdout) == 7


def test_change_old_integer_id_no_longer_auto(recdb: Any) -> None:
    recdb("add", "title=a")  # auto id is r0001 now
    proc = recdb("get", "1")
    assert proc.returncode == 3


def test_change_get_update_delete_by_new_id(recdb: Any) -> None:
    rid = json.loads(recdb("add", "title=a").stdout)
    assert recdb("get", rid).returncode == 0
    assert json.loads(recdb("update", rid, "title=b").stdout)["title"] == "b"
    assert recdb("delete", rid).returncode == 0
    assert recdb("get", rid).returncode == 3
