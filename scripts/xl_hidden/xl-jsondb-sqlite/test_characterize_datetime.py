"""Hidden characterization: datetime round-trips.

Rows may carry datetimes; they survive save/load through the
``{"$datetime": iso}`` markers — aware and naive alike.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flatvault import Database
from flatvault.serialize import decode, dump, encode, load

UTC = UTC


def test_datetime_roundtrips_through_the_store(tmp_path):
    table = Database(tmp_path).table("events")
    stamp = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)
    row_id = table.insert({"what": "launch", "at": stamp})
    reloaded = Database(tmp_path).table("events").get(row_id)
    assert reloaded is not None
    assert reloaded["at"] == stamp
    assert reloaded["at"].tzinfo is not None


def test_naive_datetime_roundtrips(tmp_path):
    table = Database(tmp_path).table("events")
    stamp = datetime(2026, 1, 5, 9, 30)
    row_id = table.insert({"at": stamp})
    assert table.get(row_id)["at"] == stamp


def test_datetime_in_nested_structures(tmp_path):
    table = Database(tmp_path).table("events")
    stamp = datetime(2026, 1, 5, tzinfo=UTC)
    row_id = table.insert({"meta": {"times": [stamp], "label": "x"}})
    row = table.get(row_id)
    assert row["meta"]["times"][0] == stamp


def test_encode_decode_markers():
    stamp = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)
    assert encode(stamp) == {"$datetime": "2026-01-05T09:30:00+00:00"}
    assert decode(encode(stamp)) == stamp
    assert load(dump({"at": stamp}))["at"] == stamp
    assert decode({"other": {"$datetime": "2026-01-05T09:30:00+00:00"}}) == {"other": stamp}
