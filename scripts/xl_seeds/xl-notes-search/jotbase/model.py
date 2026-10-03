"""The note model: a plain dataclass with strict validation.

A note is a title, a body and up to ten slug tags, stamped with UTC
creation/update times. The dict shape produced by ``to_dict`` is the
on-disk format and the API response body — it is pinned.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jotbase.errors import InvalidNote

MAX_TITLE = 200
MAX_TAGS = 10
_TAG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def new_id() -> str:
    """A fresh note id (``note_`` + 12 hex chars)."""
    return "note_" + secrets.token_hex(6)


def slugify_tag(tag: str) -> str:
    """Casefold, drop everything that is not a slug character."""
    lowered = tag.casefold().strip()
    dashed = re.sub(r"[^a-z0-9]+", "-", lowered)
    return dashed.strip("-")


def normalize_tags(tags: list[str] | tuple[str, ...]) -> list[str]:
    """Slugify, drop empties, dedupe preserving first occurrence."""
    seen: list[str] = []
    for tag in tags:
        slug = slugify_tag(tag)
        if slug and slug not in seen:
            seen.append(slug)
    return seen


def _parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text)


@dataclass
class Note:
    """One note."""

    id: str
    title: str
    body: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def validate(self) -> None:
        """Raise :class:`InvalidNote` unless the note is sound."""
        if not isinstance(self.title, str) or not self.title.strip():
            raise InvalidNote("title must not be empty")
        if len(self.title) > MAX_TITLE:
            raise InvalidNote(f"title longer than {MAX_TITLE} characters")
        if not isinstance(self.body, str):
            raise InvalidNote("body must be text")
        if not isinstance(self.tags, list):
            raise InvalidNote("tags must be a list")
        if len(self.tags) > MAX_TAGS:
            raise InvalidNote(f"more than {MAX_TAGS} tags")
        for tag in self.tags:
            if not _TAG_RE.match(tag):
                raise InvalidNote(f"bad tag {tag!r} (expected a slug)")
        if len(set(self.tags)) != len(self.tags):
            raise InvalidNote("duplicate tags")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise InvalidNote("timestamps must be timezone-aware")

    def to_dict(self) -> dict[str, Any]:
        """The pinned persisted/API shape."""
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "tags": list(self.tags),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Note:
        return cls(
            id=data["id"],
            title=data["title"],
            body=data.get("body", ""),
            tags=list(data.get("tags", [])),
            created_at=_parse_ts(data["created_at"]),
            updated_at=_parse_ts(data["updated_at"]),
        )
