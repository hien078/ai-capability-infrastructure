"""Hidden acceptance: the reports.

The account statement is chronological — by timestamp, tie-broken by id
— so a backfilled entry lands where it happened. The daily summary
groups by business day in the report timezone.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ledger.model import Posting, Transaction
from ledger.money import Money
from ledger.periods import load_tz
from ledger.reports import (
    account_totals,
    daily_summary,
    first_overdraft,
    format_balance_report,
    monthly_summary,
    running_balance,
)

UTC = UTC
TOKYO = load_tz("Asia/Tokyo")


def txn(ts, cents, account="checking", desc="t"):
    return Transaction(
        timestamp=ts,
        description=desc,
        postings=[Posting(account, Money(cents))],
    )


def test_running_balance_is_chronological():
    """A backfilled entry lands where it happened, not where it was typed."""
    txns = [
        txn(datetime(2026, 1, 5, 9, 0, tzinfo=UTC), 100),
        txn(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), 200),
        txn(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 50),
    ]
    rows = running_balance(txns, "checking")
    hours = [r.transaction.timestamp.strftime("%H:%M") for r in rows]
    assert hours == ["08:00", "09:00", "10:00"]
    assert [r.balance.cents for r in rows] == [50, 150, 350]


def test_running_balance_tiebreaks_by_id():
    txns = [
        txn(datetime(2026, 1, 5, 9, 0, tzinfo=UTC), 10),
        txn(datetime(2026, 1, 5, 9, 0, tzinfo=UTC), 20),
    ]
    txns[0].id = 1
    txns[1].id = 2
    rows = running_balance(txns, "checking")
    assert [r.transaction.id for r in rows] == [1, 2]


def test_running_balance_honours_the_opening_balance():
    txns = [txn(datetime(2026, 1, 5, 9, 0, tzinfo=UTC), 100)]
    rows = running_balance(txns, "checking", opening=Money(500))
    assert rows[0].balance.cents == 600


def test_running_balance_skips_other_accounts():
    txns = [
        txn(datetime(2026, 1, 5, 9, 0, tzinfo=UTC), 100, account="checking"),
        txn(datetime(2026, 1, 5, 9, 30, tzinfo=UTC), 7, account="groceries"),
    ]
    rows = running_balance(txns, "checking")
    assert len(rows) == 1
    assert rows[0].balance.cents == 100


def test_first_overdraft_is_none_when_never_negative():
    txns = [txn(datetime(2026, 1, 5, 9, 0, tzinfo=UTC), 100)]
    rows = running_balance(txns, "checking")
    assert first_overdraft(rows) is None


def test_first_overdraft_points_at_the_first_negative_row():
    """With the backfilled debit first, the overdraft is the 08:00 row."""
    txns = [
        txn(datetime(2026, 1, 5, 9, 0, tzinfo=UTC), 100),
        txn(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), -50),
        txn(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), 10),
    ]
    rows = running_balance(txns, "checking")
    overdraft = first_overdraft(rows)
    assert overdraft is not None
    assert overdraft.transaction.timestamp.strftime("%H:%M") == "08:00"
    assert overdraft.balance.cents == -50


def test_daily_summary_groups_by_business_day():
    """22:30 UTC Jan 1 and 10:00 UTC Jan 2 are both the Jan 2 Tokyo day."""
    txns = [
        txn(datetime(2026, 1, 1, 22, 30, tzinfo=UTC), -10),
        txn(datetime(2026, 1, 2, 10, 0, tzinfo=UTC), 20),
    ]
    rows = daily_summary(txns, TOKYO)
    assert len(rows) == 1
    assert rows[0].day.isoformat() == "2026-01-02"
    assert rows[0].count == 2
    assert rows[0].net.cents == 10


def test_daily_summary_net_for_one_account():
    txns = [
        txn(datetime(2026, 1, 2, 10, 0, tzinfo=UTC), 30, account="checking"),
        txn(datetime(2026, 1, 2, 11, 0, tzinfo=UTC), -5, account="groceries"),
    ]
    rows = daily_summary(txns, load_tz("UTC"), account="checking")
    assert rows[0].net.cents == 30


def test_monthly_summary_groups_by_business_month():
    txns = [
        txn(datetime(2026, 1, 31, 23, 0, tzinfo=UTC), 10),
        txn(datetime(2026, 2, 1, 1, 0, tzinfo=UTC), 5),
    ]
    rows = monthly_summary(txns, load_tz("UTC"))
    assert [(r.year, r.month) for r in rows] == [(2026, 1), (2026, 2)]
    assert rows[0].net.cents == 10


def test_account_totals_sorted_by_name():
    txns = [
        txn(datetime(2026, 1, 5, 9, 0, tzinfo=UTC), 100, account="groceries"),
        txn(datetime(2026, 1, 5, 9, 30, tzinfo=UTC), -7, account="checking"),
    ]
    rows = account_totals(txns)
    assert [r.account for r in rows] == ["checking", "groceries"]
    assert rows[0].total.cents == -7


def test_format_balance_report_mentions_rows():
    txns = [txn(datetime(2026, 1, 5, 9, 0, tzinfo=UTC), -54, desc="Card payment")]
    rows = running_balance(txns, "checking")
    text = format_balance_report(rows, "checking")
    assert "Card payment" in text
    assert "-0.54" in text
