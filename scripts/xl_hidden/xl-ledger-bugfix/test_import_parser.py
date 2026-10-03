"""Hidden acceptance: the bank-import parser.

Pins the import format end to end: quoting, the exact header, blank
lines, CRLF, and — above all — that every data row of an export is
imported, last row included.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ledger.csvimport import parse_csv, parse_export, split_line
from ledger.errors import ParseError, ValidationError

HEADER = "timestamp,description,account,amount,currency"


def row(ts, desc, acct, amount, cur="USD"):
    return f"{ts},{desc},{acct},{amount},{cur}"


def test_split_line_plain_fields():
    assert split_line("a,b,c") == ["a", "b", "c"]
    assert split_line("a") == ["a"]
    assert split_line("") == [""]


def test_split_line_quoted_comma():
    assert split_line('x,"a,b",y') == ["x", "a,b", "y"]


def test_split_line_escaped_quote():
    assert split_line('"say ""hi""",z') == ['say "hi"', "z"]


def test_split_line_quoted_last_field():
    assert split_line('a,"b,c"') == ["a", "b,c"]


def test_parse_csv_requires_the_exact_header():
    with pytest.raises(ParseError):
        parse_csv("a,b,c,d,e\n1,2,3,4,5")


def test_parse_csv_rejects_a_short_row():
    with pytest.raises(ParseError):
        parse_csv(HEADER + "\n1,2,3,4")


def test_parse_csv_skips_blank_lines():
    text = HEADER + "\n\n" + row("2026-01-05T10:00:00+00:00", "a", "acct", "1.00") + "\n\n"
    assert len(parse_csv(text)) == 1


def test_parse_csv_handles_crlf():
    text = HEADER + "\r\n" + row("2026-01-05T10:00:00+00:00", "a", "acct", "1.00") + "\r\n"
    assert len(parse_csv(text)) == 1


def test_parse_csv_returns_every_data_row():
    text = (
        HEADER
        + "\n"
        + row("2026-01-05T10:00:00+00:00", "first", "acct", "1.00")
        + "\n"
        + row("2026-01-05T11:00:00+00:00", "second", "acct", "2.00")
        + "\n"
        + row("2026-01-05T12:00:00+00:00", "third", "acct", "3.00")
        + "\n"
    )
    rows = parse_csv(text)
    assert len(rows) == 3
    assert rows[-1].fields["description"] == "third"


def test_parse_csv_single_data_row():
    text = HEADER + "\n" + row("2026-01-05T10:00:00+00:00", "only", "acct", "1.00") + "\n"
    assert len(parse_csv(text)) == 1


def test_parse_csv_without_trailing_newline():
    text = (
        HEADER
        + "\n"
        + row("2026-01-05T10:00:00+00:00", "a", "acct", "1.00")
        + "\n"
        + row("2026-01-05T11:00:00+00:00", "b", "acct", "2.00")
    )
    assert len(parse_csv(text)) == 2


def test_parse_export_builds_transactions():
    text = (
        HEADER
        + "\n"
        + row("2026-01-05T14:30:00+00:00", "Card payment", "groceries", "-54.20")
        + "\n"
        + '2026-01-05T15:00:00Z,Payroll,checking,"-3,120.00",USD'
        + "\n"
    )
    txns = parse_export(text)
    assert len(txns) == 2
    first, second = txns
    assert first.timestamp == datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    assert first.description == "Card payment"
    assert first.postings[0].account == "groceries"
    assert first.postings[0].amount.cents == -5420
    assert first.source == "import"
    assert second.postings[0].amount.cents == -312000
    assert second.postings[0].amount.currency == "USD"


def test_parse_export_rejects_a_bad_amount():
    text = HEADER + "\n" + row("2026-01-05T10:00:00+00:00", "a", "acct", "12.5") + "\n"
    with pytest.raises(ValidationError):
        parse_export(text)


def test_parse_export_rejects_a_naive_timestamp():
    text = HEADER + "\n" + row("2026-01-05", "a", "acct", "1.00") + "\n"
    with pytest.raises(ValidationError):
        parse_export(text)


def test_parse_export_rejects_an_unknown_dialect():
    text = "timestamp;description;account;amount;currency\n05.01.2026;a;acct;1.00;USD\n"
    with pytest.raises(ParseError):
        parse_export(text, dialect="semicolon")
