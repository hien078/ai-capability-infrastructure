"""The jotbase command line.

Every command takes ``--root DIR`` (default ``jotbase-data``) and prints
human-readable text; exit 0 on success, 1 on any jotbase error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from jotbase.errors import JotbaseError
from jotbase.service import NoteService
from jotbase.storage import NoteStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jotbase", description="Note taking.")
    parser.add_argument("--root", default="jotbase-data", help="data directory")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="add a note")
    add.add_argument("--title", required=True)
    add.add_argument("--body", default="")
    add.add_argument("--tag", action="append", default=[])

    lst = sub.add_parser("list", help="list notes (newest first)")
    lst.add_argument("--tag")
    lst.add_argument("--limit", type=int)

    show = sub.add_parser("show", help="print one note")
    show.add_argument("id")

    delete = sub.add_parser("delete", help="delete a note")
    delete.add_argument("id")

    sub.add_parser("stats", help="counts per tag")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    service = NoteService(NoteStore(Path(args.root)))
    try:
        if args.command == "add":
            note = service.add(title=args.title, body=args.body, tags=args.tag)
            print(f"added {note.id} {note.title}")
            return 0
        if args.command == "list":
            notes = service.list_notes(tag=args.tag, limit=args.limit)
            for note in notes:
                tag_text = ",".join(note.tags)
                print(f"{note.id}  {note.created_at.isoformat()}  {note.title}  [{tag_text}]")
            return 0
        if args.command == "show":
            note = service.get(args.id)
            print(note.title)
            print(",".join(note.tags))
            print(note.body)
            return 0
        if args.command == "delete":
            service.delete(args.id)
            print(f"deleted {args.id}")
            return 0
        if args.command == "stats":
            stats = service.stats()
            print(f"notes: {stats['notes']}")
            for tag, count in stats["tags"].items():
                print(f"  {tag}: {count}")
            return 0
    except JotbaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
