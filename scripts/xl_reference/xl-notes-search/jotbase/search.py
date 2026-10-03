"""Full-text search over notes: query model, inverted index, scoring.

The index is an inverted map from tokens to notes, persisted at
``<root>/index.json`` — a separate file; ``notes.json`` is never touched
by search. Tokens are casefolded alphanumeric runs. Scoring:

    score(note) = sum over query tokens t present in the note of
                  tf(t) * (2.0 if t is in the title else 1.0)
                      * (1.5 if updated within the last 7 days else 1.0)

A query matches a note when the note contains at least one query token;
more matched tokens score higher. Ties break by ``created_at`` descending
(older notes first lose), then id ascending — the same tail as
``list_notes``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jotbase.errors import JotbaseError
from jotbase.model import Note

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
INDEX_VERSION = 1
RECENCY_WINDOW = timedelta(days=7)
TITLE_BOOST = 2.0
RECENCY_BOOST = 1.5


class SearchError(JotbaseError):
    """A bad search query (empty text, out-of-range page)."""


@dataclass(frozen=True)
class SearchQuery:
    """A validated search request."""

    text: str
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise SearchError("query text must not be empty")
        if isinstance(self.page, bool) or not isinstance(self.page, int) or self.page < 1:
            raise SearchError("page must be an integer >= 1")
        if (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= MAX_PAGE_SIZE
        ):
            raise SearchError(f"page_size must be an integer in 1..{MAX_PAGE_SIZE}")


def tokenize(text: str) -> list[str]:
    """Casefolded alphanumeric tokens, order preserved, empties dropped."""
    return re.findall(r"[a-z0-9]+", text.casefold())


def _counts(tokens: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return counts


@dataclass(frozen=True)
class Page:
    """One page of results plus the totals the envelope carries."""

    query: str
    page: int
    page_size: int
    total: int
    items: list[Note] = field(default_factory=list)

    @property
    def total_pages(self) -> int:
        if self.total == 0:
            return 0
        return -(-self.total // self.page_size)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "total_pages": self.total_pages,
            "items": [note.to_dict() for note in self.items],
        }


class SearchIndex:
    """The inverted index file: token -> note statistics."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.docs: dict[str, dict[str, Any]] = {}

    @property
    def path(self) -> Path:
        return self.root / "index.json"

    def load(self) -> bool:
        """Read the index; False when missing, corrupt or stale-version
        (the caller rebuilds)."""
        if not self.path.exists():
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        if data.get("version") != INDEX_VERSION:
            return False
        docs = data.get("docs", {})
        if not isinstance(docs, dict):
            return False
        self.docs = docs
        return True

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"version": INDEX_VERSION, "docs": self.docs}, indent=2) + "\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------- maintenance

    def index_note(self, doc: dict[str, Any]) -> None:
        """(Re)index one note dict."""
        self.docs[doc["id"]] = {
            "tokens": _counts(tokenize(doc["title"]) + tokenize(doc.get("body", ""))),
            "title_tokens": _counts(tokenize(doc["title"])),
            "updated": doc["updated_at"],
        }

    def note_added(self, doc: dict[str, Any]) -> None:
        self.index_note(doc)
        self.save()

    def note_updated(self, doc: dict[str, Any]) -> None:
        self.index_note(doc)
        self.save()

    def note_deleted(self, note_id: str) -> None:
        self.docs.pop(note_id, None)
        self.save()

    def rebuild(self, docs: Iterable[dict[str, Any]]) -> None:
        """Rebuild from the note dicts (the source of truth)."""
        self.docs = {}
        for doc in docs:
            self.index_note(doc)

    # ----------------------------------------------------------------- lookup

    def candidates(self, tokens: list[str]) -> set[str]:
        """Note ids containing at least one query token."""
        hits: set[str] = set()
        for token in set(tokens):
            for note_id, stats in self.docs.items():
                if token in stats["tokens"]:
                    hits.add(note_id)
        return hits

    def score(self, note_id: str, tokens: list[str], now: datetime) -> float:
        """The ranking score of one note for a query."""
        stats = self.docs.get(note_id)
        if stats is None:
            return 0.0
        boost = RECENCY_BOOST if _recent(stats["updated"], now) else 1.0
        total = 0.0
        for token in set(tokens):
            tf = stats["tokens"].get(token, 0)
            if not tf:
                continue
            weight = TITLE_BOOST if token in stats["title_tokens"] else 1.0
            total += tf * weight
        return total * boost


def _recent(updated_iso: str, now: datetime) -> bool:
    try:
        updated = datetime.fromisoformat(updated_iso)
    except ValueError:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return now - updated <= RECENCY_WINDOW
