"""A JSON-file store for transactions and accounts.

Layout: ``<root>/transactions.json`` and ``<root>/accounts.json``. The
store keeps everything in memory and rewrites the files on every
mutation — fine for a small shop, and the files are the only state.

Extension point: ``allocate_hook``. A one-argument callable invoked
after a transaction id has been allocated but before the row is
appended; the sync replication prototype uses it to mirror allocations
into its own journal. The hook receives the allocated id.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ledger.errors import (
    DuplicateTransactionError,
    LedgerError,
    UnknownAccountError,
    ValidationError,
)
from ledger.model import Account, Transaction
from ledger.money import Money


class Store:
    """In-memory transaction/account store backed by two JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # RLock: the sync hook may itself add a transaction while an
        # allocation holds the lock.
        self._lock = threading.RLock()
        self._txns: list[Transaction] = []
        self._accounts: dict[str, Account] = {}
        self._id_counter = 0
        #: Optional sync-replication hook: called with the allocated id
        #: after allocation, before the row is appended.
        self.allocate_hook: Any = None
        self._load()

    # ------------------------------------------------------------------ load

    @property
    def transactions_path(self) -> Path:
        return self.root / "transactions.json"

    @property
    def accounts_path(self) -> Path:
        return self.root / "accounts.json"

    def _load(self) -> None:
        if self.transactions_path.exists():
            try:
                data = json.loads(self.transactions_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise LedgerError(f"corrupt transactions file: {exc}") from None
            self._txns = [Transaction.from_dict(d) for d in data.get("transactions", [])]
            self._id_counter = max((t.id for t in self._txns if t.id is not None), default=0)
        if self.accounts_path.exists():
            try:
                data = json.loads(self.accounts_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise LedgerError(f"corrupt accounts file: {exc}") from None
            self._accounts = {
                name: Account.from_dict(d) for name, d in data.get("accounts", {}).items()
            }

    def save(self) -> None:
        """Write both files."""
        with self._lock:
            self.transactions_path.write_text(
                json.dumps(
                    {"transactions": [t.to_dict() for t in self._txns]},
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.accounts_path.write_text(
                json.dumps(
                    {"accounts": {n: a.to_dict() for n, a in self._accounts.items()}},
                    indent=2,
                ),
                encoding="utf-8",
            )

    # ---------------------------------------------------------- transactions

    def add_transaction(self, txn: Transaction) -> Transaction:
        """Append a transaction, assigning its id.

        The id comes from the store's monotonic counter, allocated and
        appended under the store lock in one critical section, so the
        sync hook (which may add its own transaction) can never collide
        with the allocation in flight. A collision still raises
        :class:`DuplicateTransactionError` — the store never contains two
        rows with the same id.
        """
        if txn.id is not None:
            raise ValidationError("transaction id is assigned by the store")
        with self._lock:
            txn.id = self._id_counter + 1
            self._id_counter += 1
            if self.allocate_hook is not None:
                self.allocate_hook(txn.id)
            for existing in self._txns:
                if existing.id == txn.id:
                    raise DuplicateTransactionError(f"duplicate transaction id {txn.id}")
            self._txns.append(txn)
            self.save()
        return txn

    def get(self, txn_id: int) -> Transaction | None:
        for txn in self._txns:
            if txn.id == txn_id:
                return txn
        return None

    def import_transactions(self, txns: list[Transaction]) -> list[int]:
        """Add many transactions in order; returns their ids."""
        ids: list[int] = []
        for txn in txns:
            self.add_transaction(txn)
            assert txn.id is not None
            ids.append(txn.id)
        return ids

    def all(self, account: str | None = None) -> list[Transaction]:
        """Every transaction, in insertion order; optionally one account's."""
        rows = list(self._txns)
        if account is None:
            return rows
        return [t for t in rows if any(p.account == account for p in t.postings)]

    def __len__(self) -> int:
        return len(self._txns)

    # -------------------------------------------------------------- accounts

    def add_account(self, account: Account) -> Account:
        account.validate()
        self._accounts[account.name] = account
        self.save()
        return account

    def ensure_account(self, name: str, kind: str = "asset", currency: str = "USD") -> Account:
        if name in self._accounts:
            return self._accounts[name]
        return self.add_account(Account(name=name, kind=kind, currency=currency))

    def account(self, name: str) -> Account:
        try:
            return self._accounts[name]
        except KeyError:
            raise UnknownAccountError(f"unknown account {name!r}") from None

    def accounts(self) -> list[Account]:
        return [self._accounts[name] for name in sorted(self._accounts)]

    def balance(self, account_name: str) -> Money:
        """Sum of every posting on one account (zero when none)."""
        currency = (
            self._accounts[account_name].currency if account_name in self._accounts else "USD"
        )
        total = Money.zero(currency)
        for txn in self._txns:
            total = total + txn.amount_for(account_name)
        return total
