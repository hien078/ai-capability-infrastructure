"""Change acceptance: the bank's new semicolon dialect.

``;`` delimiters, ``#`` comment lines, ``DD.MM.YYYY`` dates recorded at
midnight UTC. The comma dialect stays the default and unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ledger.csvimport import parse_csv, parse_export
from ledger.errors import ParseError, ValidationError

SEMI_HEADER = "timestamp;description;account;amount;currency"


def test_semicolon_dialect_parses_every_row():
    text = (
        SEMI_HEADER
        + "\n"
        + "05.01.2026;Card payment;groceries;-54.20;EUR\n"
        + "06.01.2026;Invoice;income;900.00;EUR\n"
    )
    txns = parse_export(text, dialect="semicolon")
    assert len(txns) == 2
    assert txns[0].timestamp == datetime(2026, 1, 5, tzinfo=UTC)
    assert txns[0].postings[0].amount.cents == -5420
    assert txns[0].postings[0].amount.currency == "EUR"
    assert txns[1].postings[0].amount.cents == 90000


def test_semicolon_dialect_skips_comment_lines():
    text = (
        SEMI_HEADER
        + "\n"
        + "# bank of tokyo export v2\n"
        + "05.01.2026;Card payment;groceries;-54.20;EUR\n"
        + "# trailing comment\n"
    )
    rows = parse_csv(text, dialect="semicolon")
    assert len(rows) == 1
    assert rows[0].fields["description"] == "Card payment"


def test_semicolon_dialect_rejects_a_bad_date():
    text = SEMI_HEADER + "\n" + "2026-01-05;Card payment;groceries;-54.20;EUR\n"
    with pytest.raises(ValidationError):
        parse_export(text, dialect="semicolon")


def test_comma_dialect_is_still_the_default():
    text = (
        "timestamp,description,account,amount,currency\n"
        "2026-01-05T10:00:00+00:00,Card payment,groceries,-54.20,USD\n"
    )
    txns = parse_export(text)
    assert len(txns) == 1
    assert txns[0].postings[0].amount.cents == -5420


def test_unknown_dialect_is_still_rejected():
    text = "timestamp,description,account,amount,currency\n"
    with pytest.raises(ParseError):
        parse_export(text, dialect="pipe")
