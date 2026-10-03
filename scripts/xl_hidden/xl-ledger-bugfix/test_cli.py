"""Hidden acceptance: the command line.

Every command exits 0 on success and 1 on a ledger error, and the
numbers it prints are the store's real numbers.
"""

from __future__ import annotations

import pytest
from ledger.cli import main
from ledger.store import Store

CSV = (
    "timestamp,description,account,amount,currency\n"
    "2026-01-02T10:00:00+00:00,first,groceries,-10.00,USD\n"
    "2026-01-02T11:00:00+00:00,second,groceries,-20.00,USD\n"
    "2026-01-02T12:00:00+00:00,third,income,900.00,USD\n"
)


def test_cli_import_writes_the_full_store(tmp_path, capsys):
    export = tmp_path / "export.csv"
    export.write_text(CSV, encoding="utf-8")
    store_dir = tmp_path / "store"
    assert main(["--store", str(store_dir), "import", "--file", str(export)]) == 0
    assert len(Store(store_dir)) == 3


def test_cli_summary_prints_the_days_numbers(tmp_path, capsys):
    export = tmp_path / "export.csv"
    export.write_text(CSV, encoding="utf-8")
    store_dir = tmp_path / "store"
    main(["--store", str(store_dir), "import", "--file", str(export)])
    capsys.readouterr()
    assert main(["--store", str(store_dir), "summary", "--day", "2026-01-02"]) == 0
    out = capsys.readouterr().out
    assert "2026-01-02" in out
    assert "3 transactions" in out
    assert "870.00 USD" in out


def test_cli_balance_prints_the_account(tmp_path, capsys):
    export = tmp_path / "export.csv"
    export.write_text(CSV, encoding="utf-8")
    store_dir = tmp_path / "store"
    main(["--store", str(store_dir), "import", "--file", str(export)])
    capsys.readouterr()
    assert main(["--store", str(store_dir), "balance", "--account", "groceries"]) == 0
    out = capsys.readouterr().out
    assert "groceries" in out
    assert "-30.00 USD" in out


def test_cli_record_then_list(tmp_path, capsys):
    store_dir = tmp_path / "store"
    rc = main(
        [
            "--store",
            str(store_dir),
            "record",
            "--description",
            "Payroll",
            "--timestamp",
            "2026-01-05T09:00:00+00:00",
            "--currency",
            "USD",
            "--posting",
            "checking:-3,120.00",
        ]
    )
    assert rc == 0
    capsys.readouterr()
    assert main(["--store", str(store_dir), "list", "--account", "checking"]) == 0
    out = capsys.readouterr().out
    assert "Payroll" in out
    assert "-3,120.00" in out


def test_cli_summary_has_no_timezone_flag(tmp_path):
    """v1 has no --tz on summary; an unknown flag must exit 2."""
    store_dir = tmp_path / "store"
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--store",
                str(store_dir),
                "summary",
                "--day",
                "2026-01-02",
                "--tz",
                "Asia/Tokyo",
            ]
        )
    assert exc.value.code == 2
