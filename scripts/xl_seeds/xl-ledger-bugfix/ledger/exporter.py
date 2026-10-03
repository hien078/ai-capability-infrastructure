"""Export the store back out as bank CSV.

The inverse of :mod:`ledger.csvimport`: the same header, the same
quoting rules. A round trip (import -> export -> import) must preserve
every row — the export is what we send the accountant.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from ledger.csvimport import REQUIRED_COLUMNS
from ledger.model import Transaction
from ledger.store import Store


def format_field(text: str) -> str:
    """Quote a field when it contains a comma, a quote or a newline."""
    if any(ch in text for ch in (",", '"', "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


def format_row(values: Sequence[str]) -> str:
    """Join fields into one CSV line (no trailing newline)."""
    return ",".join(format_field(v) for v in values)


def export_transactions(txns: Iterable[Transaction]) -> str:
    """Render transactions as a full bank export (header included)."""
    lines = [format_row(REQUIRED_COLUMNS)]
    for txn in txns:
        amount = txn.total()
        lines.append(
            format_row(
                [
                    txn.timestamp.isoformat(),
                    txn.description,
                    txn.postings[0].account if txn.postings else "",
                    amount.to_text(),
                    amount.currency,
                ]
            )
        )
    return "\n".join(lines) + "\n"


def export_store(store: Store, path: Path) -> int:
    """Write the store's transactions to ``path``; returns the row count."""
    text = export_transactions(store.all())
    path.write_text(text, encoding="utf-8")
    return text.count("\n") - 1
