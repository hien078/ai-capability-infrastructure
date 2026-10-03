"""Blog domain models — plain dataclasses over flatvault rows."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


def slugify(text: str) -> str:
    """Casefold, drop non-slugs, collapse dashes."""
    dashed = re.sub(r"[^a-z0-9]+", "-", text.casefold().strip())
    return dashed.strip("-")


@dataclass
class Post:
    """A blog post row (table ``posts``)."""

    id: int | None
    slug: str
    title: str
    body: str = ""
    published: bool = False
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now())

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "body": self.body,
            "published": self.published,
            "tags": list(self.tags),
            "created_at": self.created_at,
        }

    def to_json_row(self) -> dict:
        """The row with JSON-safe values (timestamps as ISO strings)."""
        row = self.to_row()
        row["created_at"] = self.created_at.isoformat()
        return row

    @classmethod
    def from_row(cls, row: dict) -> Post:
        return cls(
            id=row["id"],
            slug=row["slug"],
            title=row["title"],
            body=row.get("body", ""),
            published=bool(row.get("published", False)),
            tags=list(row.get("tags", [])),
            created_at=row["created_at"],
        )


@dataclass
class Comment:
    """A comment row (table ``comments``)."""

    id: int | None
    post_id: int
    author: str
    body: str
    created_at: datetime = field(default_factory=lambda: datetime.now())

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "post_id": self.post_id,
            "author": self.author,
            "body": self.body,
            "created_at": self.created_at,
        }

    def to_json_row(self) -> dict:
        """The row with JSON-safe values (timestamps as ISO strings)."""
        row = self.to_row()
        row["created_at"] = self.created_at.isoformat()
        return row

    @classmethod
    def from_row(cls, row: dict) -> Comment:
        return cls(
            id=row["id"],
            post_id=row["post_id"],
            author=row["author"],
            body=row["body"],
            created_at=row["created_at"],
        )
