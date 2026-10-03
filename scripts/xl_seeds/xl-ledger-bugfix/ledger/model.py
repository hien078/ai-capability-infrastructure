"""Transaction and account models — plain dataclasses, no framework.

A transaction carries one or more postings; each posting is a signed
amount on one account (positive = debit, negative = credit). Timestamps
are timezone-aware and normalized to UTC on the way in — a naive
datetime is a validation error, never a silent local-time guess.

The JSON shapes produced by ``to_dict`` are the on-disk store format and
are pinned: ``Money`` serializes as ``{"cents": n, "currency": "USD"}``
and timestamps as ISO-8601 strings with an offset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ledger.errors import ValidationError
from ledger.money import DECIMALS, Money

#: Allowed transaction sources. ``import`` rows come from bank exports,
#: ``manual`` rows are typed by a person (CLI or service).
SOURCES = ("manual", "import")

#: Allowed account kinds (the five bookkeeping kinds).
ACCOUNT_KINDS = ("asset", "liability", "income", "expense", "equity")


def validate_timestamp(ts: datetime) -> datetime:
    """Require an aware datetime; normalize to UTC."""
    if not isinstance(ts, datetime):
        raise ValidationError("timestamp must be a datetime")
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValidationError("timestamp must be timezone-aware")
    return ts.astimezone(UTC)


@dataclass(frozen=True)
class Posting:
    """A signed amount on one account."""

    account: str
    amount: Money

    def is_debit(self) -> bool:
        """Positive postings are debits."""
        return self.amount.cents > 0

    def is_credit(self) -> bool:
        """Negative postings are credits."""
        return self.amount.cents < 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "amount": {"cents": self.amount.cents, "currency": self.amount.currency},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Posting:
        amount = data["amount"]
        return cls(
            account=data["account"],
            amount=Money(amount["cents"], amount["currency"]),
        )


@dataclass
class Transaction:
    """A dated entry with postings. ``id`` is assigned by the store."""

    id: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    description: str = ""
    postings: list[Posting] = field(default_factory=list)
    source: str = "manual"

    def validate(self) -> None:
        """Raise :class:`ValidationError` unless the transaction is sound."""
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValidationError("transaction description must not be empty")
        if not self.postings:
            raise ValidationError("transaction needs at least one posting")
        currencies = {p.amount.currency for p in self.postings}
        if len(currencies) > 1:
            raise ValidationError(f"postings mix currencies: {sorted(currencies)}")
        for posting in self.postings:
            if not posting.account or not posting.account.strip():
                raise ValidationError("posting account must not be empty")
        if self.source not in SOURCES:
            raise ValidationError(f"unknown source {self.source!r}")
        if self.id is not None and (isinstance(self.id, bool) or not isinstance(self.id, int)):
            raise ValidationError("transaction id must be an integer or None")
        validate_timestamp(self.timestamp)

    def amount_for(self, account: str) -> Money:
        """Sum of this transaction's postings on one account."""
        total = Money.zero(self.postings[0].amount.currency if self.postings else "USD")
        for posting in self.postings:
            if posting.account == account:
                total = total + posting.amount
        return total

    def total(self) -> Money:
        """Sum of all postings (non-zero for single-sided bank imports)."""
        if not self.postings:
            return Money.zero()
        total = Money.zero(self.postings[0].amount.currency)
        for posting in self.postings:
            total = total + posting.amount
        return total

    def accounts(self) -> list[str]:
        """Accounts touched, in posting order, without duplicates."""
        seen: list[str] = []
        for posting in self.postings:
            if posting.account not in seen:
                seen.append(posting.account)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "description": self.description,
            "source": self.source,
            "postings": [p.to_dict() for p in self.postings],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transaction:
        return cls(
            id=data["id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            description=data["description"],
            source=data.get("source", "manual"),
            postings=[Posting.from_dict(p) for p in data["postings"]],
        )


@dataclass(frozen=True)
class Account:
    """A bookkeeping account."""

    name: str
    kind: str = "asset"
    currency: str = "USD"

    def validate(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("account name must not be empty")
        if self.kind not in ACCOUNT_KINDS:
            raise ValidationError(f"unknown account kind {self.kind!r}")
        if self.currency not in DECIMALS:
            raise ValidationError(f"unknown currency {self.currency!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "currency": self.currency}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        return cls(name=data["name"], kind=data["kind"], currency=data["currency"])
