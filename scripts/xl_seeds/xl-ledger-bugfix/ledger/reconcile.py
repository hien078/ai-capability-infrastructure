"""Reconcile the ledger against a bank statement.

Month-end: the bank sends a statement export (the same CSV shape as the
import format) and we match it against what we already recorded. A match
is the same amount in the same currency with the same normalized
description; matching is greedy in ledger order, each statement row used
at most once. Whatever is left on either side is the discrepancy list.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ledger.csvimport import RawRow
from ledger.model import Transaction
from ledger.money import Money


def normalize_description(text: str) -> str:
    """Casefold, drop punctuation, collapse spaces — for matching only."""
    lowered = text.casefold()
    stripped = re.sub(r"[^a-z0-9 ]+", " ", lowered)
    return " ".join(stripped.split())


def _statement_amount(row: RawRow) -> Money:
    return Money.from_text(row.fields["amount"], row.fields["currency"].strip())


@dataclass(frozen=True)
class Match:
    """One matched pair."""

    ledger_id: int
    description: str
    amount: Money
    statement_line: int


@dataclass
class ReconcileResult:
    """What a reconciliation found."""

    matched: list[Match] = field(default_factory=list)
    ledger_only: list[Transaction] = field(default_factory=list)
    statement_only: list[RawRow] = field(default_factory=list)

    @property
    def balanced(self) -> bool:
        return not self.ledger_only and not self.statement_only


def reconcile(txns: Iterable[Transaction], statement: Sequence[RawRow]) -> ReconcileResult:
    """Match ledger transactions against statement rows."""
    result = ReconcileResult()
    available = list(statement)
    for txn in txns:
        amount = txn.total()
        needle = normalize_description(txn.description)
        found = None
        for row in available:
            if row.fields.get("description") is None:
                continue
            if _statement_amount(row) != amount:
                continue
            if normalize_description(row.fields["description"]) != needle:
                continue
            found = row
            break
        if found is not None:
            available.remove(found)
            assert txn.id is not None
            result.matched.append(
                Match(
                    ledger_id=txn.id,
                    description=txn.description,
                    amount=amount,
                    statement_line=found.line_no,
                )
            )
        else:
            result.ledger_only.append(txn)
    result.statement_only = available
    return result


def format_reconcile(result: ReconcileResult) -> str:
    """Render a reconciliation as text."""
    lines = [
        f"matched {len(result.matched)}",
        f"ledger only {len(result.ledger_only)}:",
    ]
    for txn in result.ledger_only:
        lines.append(f"  id {txn.id} {txn.description} {txn.total().to_text()}")
    lines.append(f"statement only {len(result.statement_only)}:")
    for row in result.statement_only:
        lines.append(
            f"  line {row.line_no} {row.fields.get('description', '')} "
            f"{row.fields.get('amount', '')}"
        )
    return "\n".join(lines)
