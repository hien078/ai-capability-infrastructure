"""Rendered statements: the accountant-facing text reports.

A statement is a header, an opening balance, the account's rows for a
period and a closing balance. The period is expressed in business days
of a timezone (``day_bounds``), so a Tokyo statement brackets midnight
JST even though every timestamp underneath is UTC.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from ledger.model import Transaction
from ledger.money import Money
from ledger.periods import day_bounds, format_day
from ledger.reports import BalanceRow, running_balance


def statement_rows(
    txns: Iterable[Transaction],
    account: str,
    day: date,
    tz: object,
    opening: Money | None = None,
) -> list[BalanceRow]:
    """The account's rows inside one business day, in statement order."""
    start, end = day_bounds(day, tz)
    selected = [
        t
        for t in txns
        if start <= t.timestamp < end and any(p.account == account for p in t.postings)
    ]
    return running_balance(selected, account, opening)


def render_statement(
    rows: list[BalanceRow],
    account: str,
    day: date,
    currency: str = "USD",
) -> str:
    """Render one day's statement as text."""
    opening = (
        rows[0].balance - rows[0].transaction.amount_for(account) if rows else Money.zero(currency)
    )
    closing = rows[-1].balance if rows else Money.zero(currency)
    lines = [
        f"{account} — statement {format_day(day)}",
        f"opening: {opening.to_text()} {currency}",
        f"{'when':<17}{'description':<28}{'amount':>14}{'balance':>14}",
    ]
    for row in rows:
        txn = row.transaction
        amount = txn.amount_for(account)
        lines.append(
            f"{txn.timestamp.strftime('%Y-%m-%d %H:%M'):<17}"
            f"{txn.description[:26]:<28}"
            f"{amount.to_text():>14}"
            f"{row.balance.to_text():>14}"
        )
    lines.append(f"closing: {closing.to_text()} {currency}")
    lines.append(f"rows: {len(rows)}")
    return "\n".join(lines)


def period_closing(
    txns: Iterable[Transaction],
    account: str,
    day: date,
    tz: object,
    opening: Money | None = None,
) -> Money:
    """The account's balance at the end of a business day."""
    rows = statement_rows(txns, account, day, tz, opening)
    if rows:
        return rows[-1].balance
    return opening if opening is not None else Money.zero()


def timestamp_in_period(ts: datetime, day: date, tz: object) -> bool:
    """Whether a timestamp falls inside a business day (half-open)."""
    start, end = day_bounds(day, tz)
    return start <= ts < end
