"""Hidden acceptance: the service, end to end.

Import -> store -> reports through the real service, with the cache on.
These are the integration paths the dashboard actually uses.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from ledger.cache import ReportCache
from ledger.model import Posting
from ledger.money import Money
from ledger.service import LedgerService
from ledger.store import Store

UTC = UTC

#: Three rows that are all one Tokyo business day (2026-01-02) but two
#: UTC calendar days.
CSV_TOKYO = (
    "timestamp,description,account,amount,currency\n"
    "2026-01-01T22:30:00+00:00,evening one,groceries,-10.00,USD\n"
    "2026-01-01T23:00:00+00:00,evening two,groceries,-20.00,USD\n"
    "2026-01-02T10:00:00+00:00,morning,income,900.00,USD\n"
)


def test_import_then_summary_end_to_end(tmp_path):
    service = LedgerService(Store(tmp_path), cache=ReportCache(), tz="Asia/Tokyo")
    result = service.import_csv(CSV_TOKYO)
    assert result.imported == 3
    row = service.daily_summary(date(2026, 1, 2))
    assert row.count == 3
    assert row.net.cents == 87000


def test_import_result_counts_and_ids(tmp_path):
    service = LedgerService(Store(tmp_path), tz="UTC")
    result = service.import_csv(CSV_TOKYO)
    assert result.imported == 3
    assert result.skipped == 0
    assert result.ids == [1, 2, 3]


def test_record_then_summary_with_the_cache(tmp_path):
    service = LedgerService(Store(tmp_path), cache=ReportCache(), tz="UTC")
    ts = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    service.record("a", ts, [Posting("checking", Money(100))])
    assert service.daily_summary(date(2026, 1, 5)).count == 1
    service.record("b", ts, [Posting("checking", Money(200))])
    assert service.daily_summary(date(2026, 1, 5)).count == 2


def test_record_then_balance_without_the_cache(tmp_path):
    service = LedgerService(Store(tmp_path), tz="UTC")
    ts = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    service.record("a", ts, [Posting("checking", Money(100))])
    service.record("b", ts, [Posting("checking", Money(-30))])
    assert service.balance("checking").cents == 70


def test_statement_after_a_backfill(tmp_path):
    service = LedgerService(Store(tmp_path), tz="UTC")
    service.record(
        "typed first", datetime(2026, 1, 5, 9, 0, tzinfo=UTC), [Posting("checking", Money(100))]
    )
    service.record(
        "backfilled", datetime(2026, 1, 5, 8, 0, tzinfo=UTC), [Posting("checking", Money(50))]
    )
    statement = service.account_statement("checking")
    assert statement[0].transaction.description == "backfilled"
    assert statement[0].balance.cents == 50
    assert statement[1].balance.cents == 150


def test_summary_respects_the_service_timezone(tmp_path):
    ts = datetime(2026, 1, 1, 22, 30, tzinfo=UTC)
    tokyo = LedgerService(Store(tmp_path), tz="Asia/Tokyo")
    utc = LedgerService(Store(tmp_path / "utc"), tz="UTC")
    tokyo.record("evening", ts, [Posting("checking", Money(10))])
    utc.record("evening", ts, [Posting("checking", Money(10))])
    assert tokyo.daily_summary(date(2026, 1, 2)).count == 1
    assert utc.daily_summary(date(2026, 1, 1)).count == 1


def test_summary_cache_key_includes_the_day(tmp_path):
    service = LedgerService(Store(tmp_path), cache=ReportCache(), tz="UTC")
    service.record("a", datetime(2026, 1, 5, 9, 0, tzinfo=UTC), [Posting("checking", Money(100))])
    service.record("b", datetime(2026, 1, 6, 9, 0, tzinfo=UTC), [Posting("checking", Money(200))])
    assert service.daily_summary(date(2026, 1, 5)).count == 1
    assert service.daily_summary(date(2026, 1, 6)).count == 1


def test_import_is_atomic_on_a_bad_row(tmp_path):
    service = LedgerService(Store(tmp_path), tz="UTC")
    text = (
        "timestamp,description,account,amount,currency\n"
        "2026-01-05T10:00:00+00:00,good,checking,10.00,USD\n"
        "2026-01-05T11:00:00+00:00,bad,checking,not-a-number,USD\n"
    )
    with pytest.raises(Exception, match="amount"):
        service.import_csv(text)
    assert len(Store(tmp_path)) == 0


def test_reconcile_against_the_same_statement(tmp_path):
    service = LedgerService(Store(tmp_path), tz="UTC")
    service.import_csv(CSV_TOKYO)
    result = service.reconcile_statement(CSV_TOKYO)
    assert len(result.matched) == 3
    assert result.balanced
