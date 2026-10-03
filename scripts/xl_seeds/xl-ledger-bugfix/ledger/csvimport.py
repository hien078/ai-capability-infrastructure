"""A small hand-rolled CSV reader for bank exports.

The bank's export format is a header row followed by one row per
transaction:

    timestamp,description,account,amount,currency

Quoted fields may contain commas; a literal quote inside a quoted field
is doubled (``""``). Lines end with ``\n`` or ``\r\n``; blank lines are
skipped. The header must be exactly the five required columns.

This module deliberately avoids the stdlib ``csv`` reader: the bank
format is small, the error messages are ours, and the dialect switch
(comma/semicolon) is a single parameter away.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ledger.errors import ParseError, ValidationError
from ledger.model import Posting, Transaction, validate_timestamp
from ledger.money import Money

#: The exact header the bank export must carry, in order.
REQUIRED_COLUMNS = ("timestamp", "description", "account", "amount", "currency")


@dataclass(frozen=True)
class RawRow:
    """One parsed data row: header name -> raw field text."""

    line_no: int
    fields: dict[str, str]


def split_line(line: str, delimiter: str = ",") -> list[str]:
    """Split one CSV line into fields, honouring quoted fields.

    A field wrapped in double quotes may contain the delimiter and
    doubled quotes; the quotes themselves are not part of the value.
    """
    fields: list[str] = []
    current: list[str] = []
    quoted = False
    i = 0
    while i < len(line):
        ch = line[i]
        if quoted:
            if ch == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    current.append('"')
                    i += 2
                    continue
                quoted = False
                i += 1
                continue
            current.append(ch)
            i += 1
            continue
        if ch == '"':
            quoted = True
            i += 1
            continue
        if ch == delimiter:
            fields.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields


def parse_csv(text: str, dialect: str = "comma") -> list[RawRow]:
    """Parse a bank export into raw rows (no conversion, no validation).

    ``dialect`` selects the delimiter family; only ``"comma"`` exists
    today (the semicolon dialect is tracked for the bank's next format).
    """
    if dialect != "comma":
        raise ParseError(f"unknown dialect {dialect!r}")
    text = text.replace("\r\n", "\n")
    lines = text.rstrip("\n").split("\n")
    if not lines or not lines[0].strip():
        raise ParseError("empty export")
    header = [name.strip() for name in split_line(lines[0])]
    if tuple(header) != REQUIRED_COLUMNS:
        raise ParseError(f"bad header: expected {list(REQUIRED_COLUMNS)}, got {header}")
    rows: list[RawRow] = []
    for line_no, line in enumerate(lines[1:-1], start=2):
        # skip the header row and the trailing blank line
        if not line.strip():
            continue
        fields = split_line(line)
        if len(fields) != len(header):
            raise ParseError(f"row {line_no}: expected {len(header)} fields, got {len(fields)}")
        rows.append(RawRow(line_no=line_no, fields=dict(zip(header, fields, strict=True))))
    return rows


def parse_export(text: str, dialect: str = "comma") -> list[Transaction]:
    """Parse a full bank export into transactions (ids unassigned).

    Every row is validated before it is returned: a bad amount, an
    unknown currency or a naive timestamp raises :class:`ValidationError`
    and nothing is returned for the rows before it — callers add the
    returned list to the store as a unit.
    """
    txns: list[Transaction] = []
    for row in parse_csv(text, dialect):
        ts = _parse_timestamp(row.fields["timestamp"])
        currency = row.fields["currency"].strip()
        amount = Money.from_text(row.fields["amount"], currency)
        account = row.fields["account"].strip()
        description = row.fields["description"].strip()
        if not account:
            raise ValidationError(f"row {row.line_no}: empty account")
        txn = Transaction(
            timestamp=ts,
            description=description,
            postings=[Posting(account=account, amount=amount)],
            source="import",
        )
        txn.validate()
        txns.append(txn)
    return txns


def _parse_timestamp(text: str) -> datetime:
    """Parse an ISO-8601 timestamp with an offset (``Z`` accepted)."""
    s = text.strip()
    try:
        ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(f"bad timestamp {text!r}") from None
    return validate_timestamp(ts)
