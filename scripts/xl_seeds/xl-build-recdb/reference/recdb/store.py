"""The JSONL store: load, insert, update, delete, atomic save.

Implements README.md §2 (records), §3 (value typing) and §5 (store file
semantics). Read commands tolerate corruption (blank lines skipped,
unparseable/malformed lines counted, last duplicate wins); writes are
atomic (temp file in the same directory + rename).
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any


class StoreError(Exception):
    """A usage/validation error (exit 2)."""


class NotFoundError(Exception):
    """A record id was not found (exit 3)."""


def default_path(flag: str | None = None) -> str:
    """Resolve the store path: --db flag > $RECDB_HOME/store.jsonl > ./recdb.jsonl."""
    if flag:
        return flag
    home = os.environ.get("RECDB_HOME")
    if home:
        return str(Path(home) / "store.jsonl")
    return "recdb.jsonl"


def parse_value(text: str) -> Any:
    """Type a bare value per README §3: JSON scalar if it parses, else string."""
    try:
        value = json.loads(text)
    except ValueError:
        return text
    if isinstance(value, (dict, list)):
        return text
    if isinstance(value, float) and not math.isfinite(value):
        return text
    return value


def unquote(text: str) -> tuple[str, bool]:
    """Strip one level of matching quotes; returns (value, was_quoted)."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1], True
    return text, False


def typed_value(text: str) -> Any:
    """Full §3 typing for key=value pair values and id arguments."""
    inner, quoted = unquote(text)
    if quoted:
        return inner
    return parse_value(inner)


def _id_eq(a: Any, b: Any) -> bool:
    """Type-strict equality for ids (JSON true never equals 1)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def _valid_id(rid: Any) -> bool:
    return isinstance(rid, str) or (isinstance(rid, int) and not isinstance(rid, bool))


def _find(records: list[dict[str, Any]], rid: Any) -> dict[str, Any] | None:
    for record in records:
        if _id_eq(record.get("id"), rid):
            return record
    return None


def next_id(records: list[dict[str, Any]]) -> int:
    """1 + the largest existing integer id (bools excluded), or 1."""
    biggest = 0
    for record in records:
        rid = record.get("id")
        if isinstance(rid, int) and not isinstance(rid, bool) and rid > biggest:
            biggest = rid
    return biggest + 1


def _check_key(key: str) -> None:
    if not key or key.strip() == "" or any(ch.isspace() for ch in key):
        raise StoreError(f"invalid field name: {key!r}")


def _parse_pairs(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, text in pairs:
        _check_key(key)
        if key in fields:
            raise StoreError(f"duplicate field: {key}")
        fields[key] = typed_value(text)
    return fields


class Store:
    """The loaded store: records in file order + integrity bookkeeping."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.records: list[dict[str, Any]] = []
        self.corrupt_lines: list[int] = []
        self.duplicate_ids: list[Any] = []
        self._load()

    def _load(self) -> None:
        try:
            text = Path(self.path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StoreError(f"cannot read store: {self.path}") from exc
        index: dict[Any, int] = {}
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                self.corrupt_lines.append(lineno)
                continue
            if not isinstance(record, dict) or "id" not in record:
                self.corrupt_lines.append(lineno)
                continue
            rid = record["id"]
            if rid in index:
                if rid not in self.duplicate_ids:
                    self.duplicate_ids.append(rid)
                self.records[index[rid]] = record
            else:
                index[rid] = len(self.records)
                self.records.append(record)

    def save(self) -> None:
        """Atomically write the records back as JSON Lines."""
        target = Path(self.path)
        parent = target.parent if str(target.parent) else Path(".")
        try:
            fd, tmp = tempfile.mkstemp(dir=parent, prefix=f".{target.name}.", suffix=".tmp")
        except OSError as exc:
            raise StoreError(f"cannot write store: {self.path}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for record in self.records:
                    handle.write(json.dumps(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise StoreError(f"cannot write store: {self.path}") from exc

    # -- commands -----------------------------------------------------------

    def add(self, pairs: list[tuple[str, str]]) -> dict[str, Any]:
        fields = _parse_pairs(pairs)
        if not fields:
            raise StoreError("at least one key=value pair is required")
        if "id" in fields:
            rid = fields["id"]
            if not _valid_id(rid):
                raise StoreError("id must be a string or an integer")
            if _find(self.records, rid) is not None:
                raise StoreError(f"duplicate id: {json.dumps(rid)}")
        else:
            fields["id"] = next_id(self.records)
        self.records.append(fields)
        self.save()
        return fields

    def get(self, rid: Any) -> dict[str, Any]:
        record = _find(self.records, rid)
        if record is None:
            raise NotFoundError(json.dumps(rid))
        return record

    def update(self, rid: Any, pairs: list[tuple[str, str]], unsets: list[str]) -> dict[str, Any]:
        record = self.get(rid)
        fields = _parse_pairs(pairs)
        if "id" in fields:
            raise StoreError("id cannot be updated")
        for key in unsets:
            if key == "id":
                raise StoreError("id cannot be unset")
        record.update(fields)
        for key in unsets:
            record.pop(key, None)
        self.save()
        return record

    def delete(self, rid: Any) -> None:
        for position, record in enumerate(self.records):
            if _id_eq(record.get("id"), rid):
                del self.records[position]
                self.save()
                return
        raise NotFoundError(json.dumps(rid))

    def import_records(self, file_path: str, fmt: str) -> tuple[int, int]:
        """Import CSV/JSONL; all-or-nothing (any bad row aborts, nothing saved)."""
        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise StoreError(f"cannot read: {file_path}") from exc
        rows: list[dict[str, Any]] = []
        if fmt == "csv":
            for row in csv.DictReader(io.StringIO(text)):
                rows.append({key: _cell(value) for key, value in row.items() if key is not None})
        elif fmt == "jsonl":
            for lineno, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError as exc:
                    raise StoreError(f"{file_path}:{lineno}: not a JSON object") from exc
                if not isinstance(obj, dict):
                    raise StoreError(f"{file_path}:{lineno}: not a JSON object")
                rows.append(dict(obj))
        else:
            raise StoreError(f"unknown format: {fmt}")
        imported = updated = 0
        for fields in rows:
            rid = fields.pop("id", None)
            if rid is not None and not _valid_id(rid):
                raise StoreError("id must be a string or an integer")
            if rid is not None:
                existing = _find(self.records, rid)
                if existing is not None:
                    existing.update(fields)
                    updated += 1
                    continue
                fields["id"] = rid
            else:
                fields["id"] = next_id(self.records)
            self.records.append(fields)
            imported += 1
        self.save()
        return imported, updated


def _cell(cell: str | None) -> Any:
    """Type a CSV cell per README §4 (import): empty cell is the empty string."""
    if cell is None or cell == "":
        return ""
    if cell == "true":
        return True
    if cell == "false":
        return False
    if cell == "null":
        return None
    try:
        value = json.loads(cell)
    except ValueError:
        return cell
    if isinstance(value, (dict, list)):
        return cell
    if isinstance(value, float) and not math.isfinite(value):
        return cell
    return value
