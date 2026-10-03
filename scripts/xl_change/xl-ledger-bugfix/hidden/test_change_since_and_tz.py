"""Change acceptance: ``summary --since`` and ``summary --tz``.

``--since DAY`` summarizes every business day from that day forward
(ascending, one line per day); ``--tz ZONE`` overrides the business
timezone for the invocation.
"""

from __future__ import annotations

from datetime import date

from ledger.cli import main
from ledger.service import LedgerService
from ledger.store import Store

CSV = (
    "timestamp,description,account,amount,currency\n"
    "2026-01-01T22:30:00+00:00,evening one,groceries,-10.00,USD\n"
    "2026-01-01T23:00:00+00:00,evening two,groceries,-20.00,USD\n"
    "2026-01-02T10:00:00+00:00,morning,income,900.00,USD\n"
)


def test_summaries_since_returns_days_ascending(tmp_path):
    service = LedgerService(Store(tmp_path), tz="UTC")
    service.import_csv(CSV)
    rows = service.summaries_since(date(2026, 1, 1))
    assert [r.day.isoformat() for r in rows] == ["2026-01-01", "2026-01-02"]
    assert rows[0].count == 2
    assert rows[0].net.cents == -3000
    assert rows[1].count == 1
    assert rows[1].net.cents == 90000


def test_summaries_since_respects_the_timezone(tmp_path):
    service = LedgerService(Store(tmp_path), tz="Asia/Tokyo")
    service.import_csv(CSV)
    rows = service.summaries_since(date(2026, 1, 1))
    # all three rows are the Tokyo January 2 business day
    assert [r.day.isoformat() for r in rows] == ["2026-01-02"]
    assert rows[0].count == 3


def test_cli_since_flag_prints_one_line_per_day(tmp_path, capsys):
    export = tmp_path / "export.csv"
    export.write_text(CSV, encoding="utf-8")
    store_dir = tmp_path / "store"
    main(["--store", str(store_dir), "import", "--file", str(export)])
    capsys.readouterr()
    assert main(["--store", str(store_dir), "summary", "--since", "2026-01-01"]) == 0
    out = capsys.readouterr().out
    assert "2026-01-01" in out
    assert "2026-01-02" in out
    assert "900.00 USD" in out


def test_cli_tz_flag_overrides_the_business_timezone(tmp_path, capsys):
    export = tmp_path / "export.csv"
    export.write_text(CSV, encoding="utf-8")
    store_dir = tmp_path / "store"
    main(["--store", str(store_dir), "import", "--file", str(export)])
    capsys.readouterr()
    rc = main(["--store", str(store_dir), "summary", "--day", "2026-01-02", "--tz", "Asia/Tokyo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "3 transactions" in out
    assert "870.00 USD" in out


def test_cli_tz_flag_rejects_an_unknown_zone(tmp_path, capsys):
    store_dir = tmp_path / "store"
    rc = main(["--store", str(store_dir), "summary", "--day", "2026-01-02", "--tz", "Mars/Olympus"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cli_summary_without_flags_is_unchanged(tmp_path, capsys):
    export = tmp_path / "export.csv"
    export.write_text(CSV, encoding="utf-8")
    store_dir = tmp_path / "store"
    main(["--store", str(store_dir), "import", "--file", str(export)])
    capsys.readouterr()
    assert main(["--store", str(store_dir), "summary", "--day", "2026-01-02"]) == 0
    out = capsys.readouterr().out
    assert "2026-01-02" in out
    assert "1 transactions" in out
