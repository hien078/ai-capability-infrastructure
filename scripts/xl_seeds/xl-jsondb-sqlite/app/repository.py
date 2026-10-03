"""Repositories: the only code that knows table names and row shapes."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from flatvault.queries import utcnow, where
from flatvault.tables import Table

from app.models import Comment, Post, slugify


def _aware_now():
    return utcnow().astimezone(UTC)


class PostRepository:
    """Posts over the ``posts`` table."""

    TABLE = "posts"

    def __init__(self, db) -> None:
        self.db = db

    def _table(self) -> Table:
        return self.db.table(self.TABLE)

    def create(self, title: str, body: str = "", tags: list[str] | tuple[str, ...] = ()) -> Post:
        """Create a post with a unique slug (``slug``, ``slug-2``, ...)."""
        base = slugify(title) or "post"
        slug = base
        suffix = 2
        while self.by_slug(slug) is not None:
            slug = f"{base}-{suffix}"
            suffix += 1
        now = _aware_now()
        self._table().insert(
            {
                "slug": slug,
                "title": title,
                "body": body,
                "published": False,
                "tags": list(tags),
                "created_at": now,
            }
        )
        return self.by_slug(slug)  # type: ignore[return-value]

    def by_slug(self, slug: str) -> Post | None:
        rows = self._table().find(where("slug") == slug)
        return Post.from_row(rows[0]) if rows else None

    def get(self, post_id: int) -> Post | None:
        row = self._table().get(post_id)
        return Post.from_row(row) if row else None

    def all(self, published: bool | None = None) -> list[Post]:
        table = self._table()
        if published is None:
            rows = table.all()
        else:
            rows = table.find(where("published") == published)
        return [Post.from_row(row) for row in rows]

    def publish(self, slug: str) -> Post | None:
        post = self.by_slug(slug)
        if post is None:
            return None
        self._table().update(post.id, {"published": True})
        return self.by_slug(slug)

    def delete(self, slug: str) -> bool:
        post = self.by_slug(slug)
        if post is None:
            return False
        return self._table().delete(post.id)

    def count_by_tag(self) -> dict[str, int]:
        tags: dict[str, int] = {}
        for post in self.all():
            for tag in post.tags:
                tags[tag] = tags.get(tag, 0) + 1
        return tags


class CommentRepository:
    """Comments over the ``comments`` table."""

    TABLE = "comments"

    def __init__(self, db) -> None:
        self.db = db

    def _table(self) -> Table:
        return self.db.table(self.TABLE)

    def add(self, post_id: int, author: str, body: str) -> Comment:
        row = self._table().insert(
            {
                "post_id": post_id,
                "author": author,
                "body": body,
                "created_at": _aware_now(),
            }
        )
        return Comment.from_row(self._table().get(row) or {})

    def for_post(self, post_id: int) -> list[Comment]:
        rows = self._table().find(where("post_id") == post_id)
        return [Comment.from_row(row) for row in rows]

    def count(self) -> int:
        return len(self._table().all())

    def any_row(self) -> dict[str, Any] | None:
        rows = self._table().all()
        return rows[0] if rows else None
