"""JSON with datetime support — the flatvault wire format.

A row may carry ``datetime`` values; JSON has none, so they encode as
``{"$datetime": "2026-01-05T09:00:00+00:00"}`` and decode back. Everything
else (str, int, float, bool, None, dict, list) round-trips natively.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from flatvault.errors import PersistenceError

DATETIME_KEY = "$datetime"


def encode(value: Any) -> Any:
    """Mark datetimes; recurse through dicts and lists."""
    if isinstance(value, datetime):
        return {DATETIME_KEY: value.isoformat()}
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return value


def decode(value: Any) -> Any:
    """Restore datetimes from their markers."""
    if isinstance(value, dict):
        if set(value) == {DATETIME_KEY}:
            return datetime.fromisoformat(value[DATETIME_KEY])
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def dump(obj: Any) -> str:
    """Encode to a JSON string."""
    return json.dumps(encode(obj), indent=2)


def load(text: str) -> Any:
    """Decode a JSON string."""
    return decode(json.loads(text))


def write_document(path: Path, obj: Any) -> None:
    """Write one document to one file."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(encode(obj), handle, indent=2)


def read_document(path: Path) -> Any:
    """Read one document from one file."""
    try:
        with open(path, encoding="utf-8") as handle:
            return decode(json.load(handle))
    except json.JSONDecodeError as exc:
        raise PersistenceError(f"corrupt document {path}: {exc}") from None
