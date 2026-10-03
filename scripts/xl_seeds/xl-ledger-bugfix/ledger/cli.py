"""The ledger command line.

Every command takes ``--store DIR`` (default ``ledger-data``) and prints
human-readable text; exit code 0 on success, 1 on any ledger error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from ledger.cache import ReportCache
from ledger.errors import LedgerError, ValidationError
from ledger.model import Posting
from ledger.money import Money
from ledger.periods import parse_day
from ledger.service import LedgerService
from ledger.store import Store


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger", description="Small-business bookkeeping.")
    parser.add_argument("--store", default="ledger-data", help="store directory")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="import a bank export")
    imp.add_argument("--file", required=True, help="path to the export")

    rec = sub.add_parser("record", help="record a manual entry")
    rec.add_argument("--description", required=True)
    rec.add_argument("--timestamp", required=True, help="ISO-8601 with offset")
    rec.add_argument("--currency", default="USD")
    rec.add_argument("--posting", action="append", required=True, metavar="ACCOUNT:AMOUNT")

    summ = sub.add_parser("summary", help="daily summary for one business day")
    summ.add_argument("--day", help="YYYY-MM-DD (default: today)")

    bal = sub.add_parser("balance", help="current balance of one account")
    bal.add_argument("--account", required=True)

    lst = sub.add_parser("list", help="list transactions")
    lst.add_argument("--account")
    lst.add_argument("--limit", type=int)

    acc = sub.add_parser("accounts", help="list accounts and their balances")
    acc.add_argument("--kind", help="only accounts of this kind")

    exp = sub.add_parser("export", help="export transactions as bank CSV")
    exp.add_argument("--file", required=True)

    rec = sub.add_parser("reconcile", help="match the ledger against a statement")
    rec.add_argument("--file", required=True)

    sub.add_parser("totals", help="per-account totals report")

    stmt = sub.add_parser("statement", help="render a one-day account statement")
    stmt.add_argument("--account", required=True)
    stmt.add_argument("--day", required=True, help="YYYY-MM-DD")

    return parser


def _parse_posting(text: str, currency: str) -> Posting:
    account, sep, amount = text.partition(":")
    if not sep or not account.strip():
        raise ValidationError(f"bad posting {text!r} (expected ACCOUNT:AMOUNT)")
    return Posting(account=account.strip(), amount=Money.from_text(amount, currency))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    store = Store(Path(args.store))
    service = LedgerService(store, cache=ReportCache())
    try:
        if args.command == "import":
            text = Path(args.file).read_text(encoding="utf-8")
            result = service.import_csv(text)
            print(f"imported {result.imported} transactions ({result.skipped} skipped)")
            return 0
        if args.command == "record":
            postings = [_parse_posting(p, args.currency) for p in args.posting]
            ts = datetime.fromisoformat(args.timestamp.replace("Z", "+00:00"))
            txn = service.record(args.description, ts, postings)
            print(f"recorded {txn.description} (id {txn.id})")
            return 0
        if args.command == "summary":
            day = parse_day(args.day) if args.day else None
            row = service.daily_summary(day)
            print(
                f"{row.day}: {row.count} transactions, net {row.net.to_text()} {row.net.currency}"
            )
            return 0
        if args.command == "balance":
            money = service.balance(args.account)
            print(f"{args.account}: {money.to_text()} {money.currency}")
            return 0
        if args.command == "list":
            txns = store.all(args.account)
            if args.limit is not None:
                txns = txns[: args.limit]
            for txn in txns:
                amount = txn.total()
                print(
                    f"{txn.id:>4} {txn.timestamp.strftime('%Y-%m-%d %H:%M')} "
                    f"{txn.description[:40]:<40} {amount.to_text():>14} {amount.currency}"
                )
            return 0
        if args.command == "accounts":
            for account in store.accounts():
                if args.kind and account.kind != args.kind:
                    continue
                money = store.balance(account.name)
                print(f"{account.name:<28} {account.kind:<10} {money.to_text():>14}")
            return 0
        if args.command == "export":
            from ledger.exporter import export_store

            count = export_store(store, Path(args.file))
            print(f"exported {count} transactions to {args.file}")
            return 0
        if args.command == "reconcile":
            from ledger.csvimport import parse_csv
            from ledger.reconcile import format_reconcile, reconcile

            statement = parse_csv(Path(args.file).read_text(encoding="utf-8"))
            result = reconcile(store.all(), statement)
            print(format_reconcile(result))
            return 0
        if args.command == "totals":
            from ledger.reports import format_totals_report

            print(format_totals_report(service.account_totals()))
            return 0
        if args.command == "statement":
            from ledger.statements import render_statement, statement_rows

            day = parse_day(args.day)
            rows = statement_rows(store.all(), args.account, day, service.tz)
            print(render_statement(rows, args.account, day))
            return 0
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
