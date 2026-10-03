"""The recdb command line interface (README.md §4 commands, §6 exit codes).

``main(argv)`` returns the process exit code: 0 success, 2 usage/validation,
3 not found, 4 corrupt store (check). argparse's own errors (no command,
unknown command, bad flags) also exit 2.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import Any

from recdb import query as querylang
from recdb.query import QueryError, Term
from recdb.store import (
    NotFoundError,
    Store,
    StoreError,
    default_path,
    typed_value,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CORRUPT = 4


def _pairs(items: list[str]) -> list[tuple[str, str]]:
    """Split ``key=value`` CLI words on the first ``=``."""
    pairs: list[tuple[str, str]] = []
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise StoreError(f"expected key=value, got: {item}")
        pairs.append((key, value))
    return pairs


def _parse_sort(text: str) -> tuple[str, bool]:
    """``FIELD[:asc|desc]`` -> (field, descending?)."""
    field, _, direction = text.partition(":")
    if direction not in ("", "asc", "desc"):
        raise StoreError(f"invalid sort direction: {direction}")
    if not field:
        raise StoreError("expected a sort field")
    return field, direction == "desc"


def _sort_key(value: Any) -> tuple[Any, ...]:
    """Numbers first, then strings, then bools, then anything else."""
    if isinstance(value, bool):
        return (2,)
    if isinstance(value, (int, float)):
        return (0, value)
    if isinstance(value, str):
        return (1, value)
    return (3,)


def _sort_records(
    records: list[dict[str, Any]], field: str, descending: bool
) -> list[dict[str, Any]]:
    """Sort by field; records missing the field sort LAST in both directions."""
    present = [r for r in records if field in r]
    missing = [r for r in records if field not in r]
    present.sort(key=lambda r: _sort_key(r[field]), reverse=descending)
    return present + missing


def _filter(records: list[dict[str, Any]], where: str | None) -> list[dict[str, Any]]:
    if where is None or where == "":
        return list(records)
    terms: list[Term] = querylang.parse(where)
    return [r for r in records if querylang.matches(r, terms)]


def _window(records: list[dict[str, Any]], offset: int, limit: int | None) -> list[dict[str, Any]]:
    if offset < 0:
        raise StoreError(f"offset must be a non-negative integer: {offset}")
    if limit is not None and limit < 0:
        raise StoreError(f"limit must be a non-negative integer: {limit}")
    windowed = records[offset:] if offset else list(records)
    if limit is not None:
        windowed = windowed[:limit]
    return windowed


def _render(value: Any) -> str:
    """Render a value for a CSV cell: true/false/null, numbers, strings."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    return json.dumps(value)


# -- command handlers --------------------------------------------------------


def _cmd_add(store: Store, args: argparse.Namespace) -> int:
    record = store.add(_pairs(args.pairs))
    if args.json:
        print(json.dumps(record))
    else:
        print(json.dumps(record["id"]))
    return EXIT_OK


def _cmd_get(store: Store, args: argparse.Namespace) -> int:
    record = store.get(typed_value(args.id))
    print(json.dumps(record))
    return EXIT_OK


def _cmd_list(store: Store, args: argparse.Namespace) -> int:
    records = _filter(store.records, args.where)
    if args.sort:
        records = _sort_records(records, *_parse_sort(args.sort))
    records = _window(records, args.offset, args.limit)
    for record in records:
        print(json.dumps(record))
    return EXIT_OK


def _cmd_query(store: Store, args: argparse.Namespace) -> int:
    terms = querylang.parse(args.expr)
    records = [r for r in store.records if querylang.matches(r, terms)]
    if args.sort:
        records = _sort_records(records, *_parse_sort(args.sort))
    records = _window(records, args.offset, args.limit)
    for record in records:
        print(json.dumps(record))
    return EXIT_OK


def _cmd_update(store: Store, args: argparse.Namespace) -> int:
    record = store.update(typed_value(args.id), _pairs(args.pairs), args.unset or [])
    print(json.dumps(record))
    return EXIT_OK


def _cmd_delete(store: Store, args: argparse.Namespace) -> int:
    store.delete(typed_value(args.id))
    return EXIT_OK


def _cmd_import(store: Store, args: argparse.Namespace) -> int:
    imported, updated = store.import_records(args.file, args.format)
    print(f"{imported} imported, {updated} updated")
    return EXIT_OK


def _cmd_export(store: Store, args: argparse.Namespace) -> int:
    records = _filter(store.records, args.where)
    try:
        with open(args.file, "w", encoding="utf-8", newline="") as handle:
            if args.format == "jsonl":
                for record in records:
                    handle.write(json.dumps(record) + "\n")
            else:
                writer = csv.writer(handle)
                columns = sorted({key for record in records for key in record})
                writer.writerow(columns)
                for record in records:
                    writer.writerow(
                        [
                            "" if column not in record else _render(record[column])
                            for column in columns
                        ]
                    )
    except OSError as exc:
        raise StoreError(f"cannot write: {args.file}") from exc
    return EXIT_OK


def _cmd_stats(store: Store, args: argparse.Namespace) -> int:
    fields: dict[str, int] = {}
    for record in store.records:
        for key in record:
            if key == "id":
                continue
            fields[key] = fields.get(key, 0) + 1
    print(
        json.dumps(
            {
                "records": len(store.records),
                "fields": fields,
                "corrupt_lines": len(store.corrupt_lines),
                "duplicate_ids": len(store.duplicate_ids),
            }
        )
    )
    return EXIT_OK


def _cmd_check(store: Store, args: argparse.Namespace) -> int:
    print(
        json.dumps(
            {
                "corrupt_lines": store.corrupt_lines,
                "duplicate_ids": store.duplicate_ids,
            }
        )
    )
    if store.corrupt_lines or store.duplicate_ids:
        return EXIT_CORRUPT
    return EXIT_OK


def _add_window_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sort", default=None, help="FIELD[:asc|desc]")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recdb", description="a tiny record database CLI")
    parser.add_argument("--db", default=None, help="store file path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="create a record")
    p.add_argument("pairs", nargs="+", metavar="key=value")
    p.add_argument("--json", action="store_true", help="print the whole record")

    p = sub.add_parser("get", help="print one record")
    p.add_argument("id")

    p = sub.add_parser("list", help="print records as JSON Lines")
    p.add_argument("--where", default=None, help="filter expression")
    _add_window_flags(p)

    p = sub.add_parser("query", help="print matching records")
    p.add_argument("expr")
    _add_window_flags(p)

    p = sub.add_parser("update", help="set and/or unset fields on one record")
    p.add_argument("id")
    p.add_argument("pairs", nargs="*", metavar="key=value")
    p.add_argument("--unset", action="append", default=[], metavar="FIELD")

    p = sub.add_parser("delete", help="remove one record")
    p.add_argument("id")

    p = sub.add_parser("import", help="import CSV or JSONL")
    p.add_argument("--file", required=True)
    p.add_argument("--format", default="csv", choices=["csv", "jsonl"])

    p = sub.add_parser("export", help="export to CSV or JSONL")
    p.add_argument("--file", required=True)
    p.add_argument("--format", default="jsonl", choices=["csv", "jsonl"])
    p.add_argument("--where", default=None, help="filter expression")

    sub.add_parser("stats", help="store statistics as JSON")
    sub.add_parser("check", help="integrity report; exit 4 if not clean")

    return parser


_COMMANDS = {
    "add": _cmd_add,
    "get": _cmd_get,
    "list": _cmd_list,
    "query": _cmd_query,
    "update": _cmd_update,
    "delete": _cmd_delete,
    "import": _cmd_import,
    "export": _cmd_export,
    "stats": _cmd_stats,
    "check": _cmd_check,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        store = Store(default_path(args.db))
        return _COMMANDS[args.command](store, args)
    except StoreError as exc:
        print(f"recdb: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except NotFoundError as exc:
        print(f"recdb: not found: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    except QueryError as exc:
        print(f"recdb: usage: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
