"""Hidden acceptance: the supporting modules.

Money, the exporter, reconciliation, config, the audit log, statements
and the validator — the stable substrate the reports stand on.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from ledger.audit import AuditLog
from ledger.config import LedgerConfig, load_config
from ledger.errors import ValidationError
from ledger.exporter import export_transactions, format_field
from ledger.model import Posting, Transaction
from ledger.money import Money
from ledger.periods import load_tz
from ledger.reconcile import normalize_description, reconcile
from ledger.statements import period_closing, statement_rows, timestamp_in_period
from ledger.store import Store
from ledger.validate import validate_store, validate_transaction

UTC = UTC


def test_money_parse_and_render_roundtrip():
    assert Money.from_text("1,234.56").cents == 123456
    assert Money.from_text("-12.50").to_text() == "-12.50"
    assert Money.from_text("1234.56").to_text() == "1,234.56"
    assert Money.from_text("900", "JPY").cents == 900
    with pytest.raises(ValidationError):
        Money.from_text("12.5")
    with pytest.raises(ValidationError):
        Money.from_text("12.500")
    with pytest.raises(ValidationError):
        Money.from_text("12,34.56")
    with pytest.raises(ValidationError):
        Money.from_text("1000.00", "JPY")


def test_money_allocate_never_loses_a_cent():
    parts = Money(100).allocate([1, 1, 1])
    assert sum(p.cents for p in parts) == 100
    assert [p.cents for p in parts] == [34, 33, 33]
    assert sum(p.cents for p in Money(-100).allocate([1, 1, 1])) == -100
    assert sum(p.cents for p in Money(0).allocate([2, 3])) == 0


def test_money_rejects_mixed_currency_arithmetic():
    with pytest.raises(ValidationError):
        Money(100, "USD") + Money(100, "EUR")


def test_posting_debit_and_credit():
    assert Posting("a", Money(5)).is_debit()
    assert not Posting("a", Money(5)).is_credit()
    assert Posting("a", Money(-5)).is_credit()
    assert not Posting("a", Money(-5)).is_debit()


def test_model_rejects_a_naive_timestamp():
    txn = Transaction(
        timestamp=datetime(2026, 1, 5),
        description="naive",
        postings=[Posting("checking", Money(1))],
    )
    with pytest.raises(ValidationError):
        txn.validate()


def test_model_rejects_mixed_currency_postings():
    txn = Transaction(
        timestamp=datetime(2026, 1, 5, tzinfo=UTC),
        description="mixed",
        postings=[Posting("a", Money(1, "USD")), Posting("b", Money(1, "EUR"))],
    )
    with pytest.raises(ValidationError):
        txn.validate()


def test_exporter_roundtrips_an_import(tmp_path):
    from ledger.csvimport import parse_export

    text = (
        "timestamp,description,account,amount,currency\n"
        '2026-01-05T10:00:00+00:00,"Payroll, net",checking,"-3,120.00",USD\n'
        "2026-01-05T11:00:00+00:00,Invoice,income,900.00,USD\n"
    )
    txns = parse_export(text)
    exported = export_transactions(txns)
    assert format_field('a "quoted", value') == '"a ""quoted"", value"'
    reparsed = parse_export(exported)
    assert len(reparsed) == 2
    assert reparsed[0].postings[0].amount.cents == -312000
    assert reparsed[0].description == "Payroll, net"


def test_reconcile_matches_on_normalized_descriptions():
    from ledger.csvimport import parse_csv

    statement = parse_csv(
        "timestamp,description,account,amount,currency\n"
        "2026-01-05T10:00:00+00:00,CARD PAYMENT!!,groceries,-54.20,USD\n"
        "2026-01-05T11:00:00+00:00,Unmatched bank fee,bank,-1.00,USD\n"
    )
    txns = [
        Transaction(
            id=1,
            timestamp=datetime(2026, 1, 5, 10, tzinfo=UTC),
            description="Card payment",
            postings=[Posting("groceries", Money(-5420))],
        )
    ]
    result = reconcile(txns, statement)
    assert len(result.matched) == 1
    assert result.matched[0].ledger_id == 1
    assert len(result.statement_only) == 1
    assert result.ledger_only == []
    assert normalize_description("CARD  Payment!!") == "card payment"


def test_config_defaults_validation_and_load(tmp_path):
    config = load_config(tmp_path / "missing.json")
    assert config.store_dir == "ledger-data"
    assert config.timezone == "UTC"
    config.validate()
    bad = LedgerConfig(timezone="Mars/Olympus_Mons")
    with pytest.raises(ValidationError):
        bad.validate()
    from ledger.config import save_config

    save_config(config, tmp_path / "config.json")
    assert load_config(tmp_path / "config.json").store_dir == "ledger-data"


def test_audit_log_append_load_and_tail(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    assert log.load() == []
    log.append("import", {"rows": 3})
    log.append("record", {"id": 1})
    events = log.load()
    assert [e.kind for e in events] == ["import", "record"]
    assert events[0].detail == {"rows": 3}
    assert [e.kind for e in log.tail(1)] == ["record"]
    assert log.tail(0) == []


def test_statements_bracket_the_business_day():
    txns = [
        Transaction(
            id=1,
            timestamp=datetime(2026, 1, 1, 22, 30, tzinfo=UTC),
            description="tokyo evening",
            postings=[Posting("checking", Money(10))],
        )
    ]
    tokyo = load_tz("Asia/Tokyo")
    assert timestamp_in_period(txns[0].timestamp, date(2026, 1, 2), tokyo)
    assert not timestamp_in_period(txns[0].timestamp, date(2026, 1, 1), tokyo)
    rows = statement_rows(txns, "checking", date(2026, 1, 2), tokyo)
    assert len(rows) == 1
    assert period_closing(txns, "checking", date(2026, 1, 2), tokyo).cents == 10


def test_validate_store_reports_unknown_accounts_and_clean_stores(tmp_path):
    store = Store(tmp_path)
    store.add_transaction(
        Transaction(
            timestamp=datetime(2026, 1, 5, tzinfo=UTC),
            description="orphan",
            postings=[Posting("ghost", Money(1))],
        )
    )
    report = validate_store(store)
    assert not report.ok
    assert any(f.code == "UNKNOWN_ACCOUNT" for f in report.findings)

    clean = Store(tmp_path / "clean")
    clean.ensure_account("checking")
    clean.add_transaction(
        Transaction(
            timestamp=datetime(2026, 1, 5, tzinfo=UTC),
            description="fine",
            postings=[Posting("checking", Money(1))],
        )
    )
    assert validate_store(clean).ok


def test_validate_transaction_flags_a_naive_timestamp():
    txn = Transaction(
        timestamp=datetime(2026, 1, 5),
        description="naive",
        postings=[Posting("checking", Money(1))],
    )
    findings = validate_transaction(txn)
    assert any(f.code == "NAIVE_TIMESTAMP" for f in findings)
