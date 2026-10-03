"""An in-memory LRU cache for computed reports.

The cache is keyed by ``(kind, key)`` where ``kind`` names the report
("daily_summary", ...) and ``key`` is a tuple of the report's parameters.
Entries are evicted oldest-first at ``max_entries``. Hit/miss counters
are exposed for the dashboard.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


class ReportCache:
    """A small bounded cache of computed report rows."""

    def __init__(self, max_entries: int = 128) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[str, tuple[Any, ...]], Any] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, kind: str, key: tuple[Any, ...]) -> Any:
        """The cached value for ``(kind, key)``, or None on a miss."""
        entry = self._entries.get((kind, key))
        if entry is None:
            self.misses += 1
            return None
        self._entries.move_to_end((kind, key))
        self.hits += 1
        return entry

    def put(self, kind: str, key: tuple[Any, ...], value: Any) -> None:
        """Cache a value; evicts the oldest entry when full."""
        self._entries[(kind, key)] = value
        self._entries.move_to_end((kind, key))
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def invalidate(self, kind: str | None = None) -> int:
        """Drop entries of one kind (every kind when None); returns how many."""
        if kind is None:
            dropped = len(self._entries)
            self._entries.clear()
            return dropped
        victims = [k for k in self._entries if k[0] == kind]
        for victim in victims:
            del self._entries[victim]
        return len(victims)

    def __len__(self) -> int:
        return len(self._entries)
