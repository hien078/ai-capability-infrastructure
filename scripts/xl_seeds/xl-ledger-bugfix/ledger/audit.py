"""An append-only JSONL audit log.

One JSON object per line, oldest first, never rewritten: the auditor's
copy of what happened. ``tail(n)`` reads the last n events without
loading the whole file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ledger.errors import LedgerError


@dataclass(frozen=True)
class AuditEvent:
    """One recorded happening."""

    kind: str
    at: datetime
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "at": self.at.isoformat(), "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEvent:
        return cls(
            kind=data["kind"],
            at=datetime.fromisoformat(data["at"]),
            detail=data.get("detail", {}),
        )


class AuditLog:
    """The append-only file behind :class:`AuditEvent`."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, kind: str, detail: dict[str, Any] | None = None) -> AuditEvent:
        """Append one event; returns it."""
        if not kind or not kind.strip():
            raise ValueError("audit kind must not be empty")
        event = AuditEvent(
            kind=kind,
            at=datetime.now(UTC),
            detail=detail or {},
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict()) + "\n")
        return event

    def load(self) -> list[AuditEvent]:
        """Every event, oldest first; a missing file is an empty log."""
        if not self.path.exists():
            return []
        events: list[AuditEvent] = []
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                events.append(AuditEvent.from_dict(json.loads(line)))
            except (KeyError, ValueError) as exc:
                raise LedgerError(f"corrupt audit line {line_no}: {exc}") from None
        return events

    def tail(self, n: int) -> list[AuditEvent]:
        """The last ``n`` events, oldest first."""
        if n < 0:
            raise ValueError("n must be >= 0")
        events = self.load()
        return events[-n:] if n else []
