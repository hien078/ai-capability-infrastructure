"""Hidden acceptance: the report cache and its freshness.

The cache is an optimization, never a source of truth: any mutation
that changes what a report would say must invalidate it. A stale daily
summary is a wrong dashboard.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from ledger.cache import ReportCache
from ledger.model import Posting
from ledger.money import Money
from ledger.service import LedgerService
from ledger.store import Store

UTC = UTC
DAY = date(2026, 1, 5)
TS = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def test_get_misses_then_hits():
    cache = ReportCache()
    assert cache.get("daily_summary", ("2026-01-05",)) is None
    cache.put("daily_summary", ("2026-01-05",), "value")
    assert cache.get("daily_summary", ("2026-01-05",)) == "value"
    assert (cache.hits, cache.misses) == (1, 1)


def test_invalidate_drops_only_the_named_kind():
    cache = ReportCache()
    cache.put("daily_summary", ("a",), 1)
    cache.put("balance", ("b",), 2)
    assert cache.invalidate("daily_summary") == 1
    assert cache.get("balance", ("b",)) == 2
    assert cache.get("daily_summary", ("a",)) is None


def test_invalidate_all_drops_every_kind():
    cache = ReportCache()
    cache.put("daily_summary", ("a",), 1)
    cache.put("balance", ("b",), 2)
    assert cache.invalidate() == 2
    assert len(cache) == 0


def test_eviction_is_oldest_first():
    cache = ReportCache(max_entries=2)
    cache.put("k", ("a",), 1)
    cache.put("k", ("b",), 2)
    cache.put("k", ("c",), 3)
    assert cache.get("k", ("a",)) is None
    assert cache.get("k", ("b",)) == 2
    assert cache.get("k", ("c",)) == 3


def test_service_summary_uses_the_cache(tmp_path):
    service = LedgerService(Store(tmp_path), cache=ReportCache(), tz="UTC")
    service.record("a", TS, [Posting("checking", Money(100))])
    service.daily_summary(DAY)
    service.daily_summary(DAY)
    assert (service._cache.hits, service._cache.misses) == (1, 1)


def test_record_invalidates_the_cached_summary(tmp_path):
    """A manual entry must be visible to the next summary immediately."""
    service = LedgerService(Store(tmp_path), cache=ReportCache(), tz="UTC")
    service.record("a", TS, [Posting("checking", Money(100))])
    assert service.daily_summary(DAY).count == 1
    service.record("b", TS, [Posting("checking", Money(200))])
    assert service.daily_summary(DAY).count == 2


def test_import_invalidates_the_cached_summary(tmp_path):
    service = LedgerService(Store(tmp_path), cache=ReportCache(), tz="UTC")
    assert service.daily_summary(DAY).count == 0
    text = (
        "timestamp,description,account,amount,currency\n"
        f"{TS.isoformat()},imported,checking,50.00,USD\n"
    )
    service.import_csv(text)
    assert service.daily_summary(DAY).count == 1
