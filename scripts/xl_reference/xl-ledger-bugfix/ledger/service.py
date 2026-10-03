"""The ledger service: imports, manual entries, reports.

The service owns the report cache: any mutation that changes what a
report would say must invalidate it, or the dashboard serves stale
numbers until the process restarts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from ledger.cache import ReportCache
from ledger.csvimport import parse_export
from ledger.model import Posting, Transaction, validate_timestamp
from ledger.money import Money
from ledger.periods import day_of, format_day, load_tz
from ledger.reports import BalanceRow, DayRow, daily_summary, running_balance
from ledger.store import Store


@dataclass(frozen=True)
class ImportResult:
    """What an import did."""

    imported: int
    skipped: int
    ids: list[int] = field(default_factory=list)


class LedgerService:
    """Import bank exports, record manual entries, run reports."""

    def __init__(
        self,
        store: Store,
        cache: ReportCache | None = None,
        tz: str = "UTC",
    ) -> None:
        self.store = store
        self._cache = cache
        self.tz = load_tz(tz)

    # -------------------------------------------------------------- mutation

    def import_csv(self, text: str, dialect: str = "comma") -> ImportResult:
        """Import a bank export; every row becomes a transaction."""
        txns = parse_export(text, dialect)
        ids: list[int] = []
        for txn in txns:
            self.store.add_transaction(txn)
            assert txn.id is not None
            ids.append(txn.id)
        self._invalidate()
        return ImportResult(imported=len(txns), skipped=0, ids=ids)

    def record(
        self,
        description: str,
        timestamp: datetime,
        postings: Sequence[Posting],
        source: str = "manual",
    ) -> Transaction:
        """Record a manual entry (validated, id-assigned)."""
        ts = validate_timestamp(timestamp)
        txn = Transaction(
            description=description,
            timestamp=ts,
            postings=list(postings),
            source=source,
        )
        txn.validate()
        self.store.add_transaction(txn)
        self._invalidate()
        return txn

    def _invalidate(self) -> None:
        if self._cache is not None:
            self._cache.invalidate()

    # ---------------------------------------------------------------- reports

    def daily_summary(self, day: date | None = None) -> DayRow:
        """The summary of one business day (default: today, in the service tz)."""
        if day is None:
            day = day_of(datetime.now(UTC), self.tz)
        key = (format_day(day),)
        if self._cache is not None:
            cached = self._cache.get("daily_summary", key)
            if cached is not None:
                return cached
        txns = [t for t in self.store.all() if day_of(t.timestamp, self.tz) == day]
        rows = daily_summary(txns, self.tz)
        row = rows[0] if rows else DayRow(day=day, count=0, net=Money.zero())
        if self._cache is not None:
            self._cache.put("daily_summary", key, row)
        return row

    def account_statement(self, account: str, upto: datetime | None = None) -> list[BalanceRow]:
        """The running-balance statement of one account."""
        txns = self.store.all(account)
        if upto is not None:
            limit = validate_timestamp(upto)
            txns = [t for t in txns if t.timestamp <= limit]
        return running_balance(txns, account)

    def balance(self, account: str) -> Money:
        """The current balance of one account."""
        return self.store.balance(account)

    def monthly_summary(self, account: str | None = None):
        """Per-month count and net movement, in the service timezone."""
        from ledger.reports import monthly_summary

        return monthly_summary(self.store.all(), self.tz, account)

    def account_totals(self):
        """Per-account count and total, accounts sorted by name."""
        from ledger.reports import account_totals

        return account_totals(self.store.all())

    def reconcile_statement(self, statement_text: str):
        """Match the store against a bank statement export."""
        from ledger.csvimport import parse_csv
        from ledger.reconcile import reconcile

        return reconcile(self.store.all(), parse_csv(statement_text))
