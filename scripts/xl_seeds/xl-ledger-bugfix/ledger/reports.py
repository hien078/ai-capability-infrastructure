"""Balance and summary reports over stored transactions.

The account statement is the load-bearing report: transactions in
chronological order (timestamp, then id for same-timestamp entries) with
the running balance after each. The daily summary groups net movement by
business day in the report timezone.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from ledger.model import Transaction
from ledger.money import Money
from ledger.periods import day_of


@dataclass(frozen=True)
class BalanceRow:
    """One line of an account statement."""

    transaction: Transaction
    balance: Money


@dataclass(frozen=True)
class DayRow:
    """One line of a daily summary."""

    day: date
    count: int
    net: Money


@dataclass(frozen=True)
class TotalRow:
    """One line of an account-totals report."""

    account: str
    count: int
    total: Money


@dataclass(frozen=True)
class MonthRow:
    """One line of a monthly summary."""

    year: int
    month: int
    count: int
    net: Money


def running_balance(
    txns: Iterable[Transaction], account: str, opening: Money | None = None
) -> list[BalanceRow]:
    """The running balance of one account, in chronological order.

    Only transactions that post to ``account`` appear. Order is by
    timestamp, tie-broken by id, so a backfilled entry lands where it
    happened rather than where it was typed.
    """
    if opening is None:
        opening = Money.zero()
    ordered = sorted(txns, key=lambda t: t.timestamp.date())
    rows: list[BalanceRow] = []
    balance = opening
    for txn in ordered:
        if not any(p.account == account for p in txn.postings):
            continue
        balance = balance + txn.amount_for(account)
        rows.append(BalanceRow(transaction=txn, balance=balance))
    return rows


def first_overdraft(rows: list[BalanceRow]) -> BalanceRow | None:
    """The first row whose balance is below zero, else None."""
    for row in rows:
        if row.balance.is_negative():
            return row
    return None


def daily_summary(
    txns: Iterable[Transaction], tz: object, account: str | None = None
) -> list[DayRow]:
    """Per-business-day count and net movement, days ascending.

    ``net`` is the sum of the account's postings when ``account`` is
    given, else the sum of every posting (the transaction total).
    """
    days: dict[date, Money] = {}
    counts: dict[date, int] = {}
    for txn in txns:
        day = day_of(txn.timestamp, tz)
        delta = txn.amount_for(account) if account is not None else txn.total()
        days[day] = days.get(day, Money.zero(delta.currency)) + delta
        counts[day] = counts.get(day, 0) + 1
    return [DayRow(day=day, count=counts[day], net=days[day]) for day in sorted(days)]


def account_totals(txns: Iterable[Transaction]) -> list[TotalRow]:
    """Per-account count and total, accounts sorted by name."""
    totals: dict[str, Money] = {}
    counts: dict[str, int] = {}
    for txn in txns:
        for posting in txn.postings:
            bucket = totals.setdefault(posting.account, Money.zero(posting.amount.currency))
            totals[posting.account] = bucket + posting.amount
            counts[posting.account] = counts.get(posting.account, 0) + 1
    return [
        TotalRow(account=name, count=counts[name], total=totals[name]) for name in sorted(totals)
    ]


def monthly_summary(
    txns: Iterable[Transaction], tz: object, account: str | None = None
) -> list[MonthRow]:
    """Per-calendar-month count and net movement (business days in ``tz``)."""
    months: dict[tuple[int, int], Money] = {}
    counts: dict[tuple[int, int], int] = {}
    for txn in txns:
        day = day_of(txn.timestamp, tz)
        key = (day.year, day.month)
        delta = txn.amount_for(account) if account is not None else txn.total()
        months[key] = months.get(key, Money.zero(delta.currency)) + delta
        counts[key] = counts.get(key, 0) + 1
    return [
        MonthRow(year=year, month=month, count=counts[(year, month)], net=months[(year, month)])
        for (year, month) in sorted(months)
    ]


def format_balance_report(rows: list[BalanceRow], account: str) -> str:
    """Render an account statement as text lines."""
    lines = [f"account: {account}"]
    lines.append(f"{'when':<17}{'description':<28}{'amount':>14}{'balance':>14}")
    for row in rows:
        txn = row.transaction
        amount = txn.amount_for(account)
        lines.append(
            f"{txn.timestamp.strftime('%Y-%m-%d %H:%M'):<17}"
            f"{txn.description[:26]:<28}"
            f"{amount.to_text():>14}"
            f"{row.balance.to_text():>14}"
        )
    return "\n".join(lines)


def format_summary_report(rows: list[DayRow]) -> str:
    """Render a daily summary as text lines."""
    lines = ["day              count          net"]
    for row in rows:
        lines.append(
            f"{row.day.isoformat():<17}{row.count:>6}{row.net.to_text():>14} {row.net.currency}"
        )
    return "\n".join(lines)


def format_monthly_report(rows: list[MonthRow]) -> str:
    """Render a monthly summary as text lines."""
    lines = [f"{'month':<10}{'count':>6}{'net':>16}"]
    for row in rows:
        lines.append(
            f"{row.year:04d}-{row.month:02d}".ljust(10)
            + f"{row.count:>6}{row.net.to_text():>14} {row.net.currency}"
        )
    return "\n".join(lines)


def format_totals_report(rows: list[TotalRow]) -> str:
    """Render an account-totals report as text lines."""
    lines = [f"{'account':<28}{'count':>6}{'total':>16}"]
    for row in rows:
        lines.append(
            f"{row.account:<28}{row.count:>6}{row.total.to_text():>14} {row.total.currency}"
        )
    return "\n".join(lines)
