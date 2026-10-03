"""The blog service: what the CLI and (someday) the API call."""

from __future__ import annotations

from typing import Any

from app.models import Comment, Post
from app.repository import CommentRepository, PostRepository


class BlogError(Exception):
    """Base class for blog failures."""


class PostNotFound(BlogError):
    """A slug that does not exist."""


class BlogService:
    """Posts and comments over one flatvault database."""

    def __init__(self, db) -> None:
        self.db = db
        self.posts = PostRepository(db)
        self.comments = CommentRepository(db)

    def create_post(
        self, title: str, body: str = "", tags: list[str] | tuple[str, ...] = ()
    ) -> Post:
        return self.posts.create(title, body, tags)

    def publish(self, slug: str) -> Post:
        post = self.posts.publish(slug)
        if post is None:
            raise PostNotFound(slug)
        return post

    def list_posts(self, published: bool | None = None) -> list[Post]:
        return self.posts.all(published)

    def get_post(self, slug: str) -> dict[str, Any]:
        post = self.posts.by_slug(slug)
        if post is None:
            raise PostNotFound(slug)
        return {
            "post": post,
            "comments": self.comments.for_post(post.id),
        }

    def add_comment(self, slug: str, author: str, body: str) -> Comment:
        post = self.posts.by_slug(slug)
        if post is None:
            raise PostNotFound(slug)
        return self.comments.add(post.id, author, body)

    def stats(self) -> dict[str, Any]:
        posts = self.posts.all()
        return {
            "posts": len(posts),
            "published": len(self.posts.all(published=True)),
            "comments": self.comments.count(),
            "tags": self.posts.count_by_tag(),
        }

    def archive(self) -> dict[str, list[str]]:
        """Published slugs grouped by year-month, newest month first."""
        months: dict[str, list[str]] = {}
        for post in self.posts.all(published=True):
            key = f"{post.created_at.year:04d}-{post.created_at.month:02d}"
            months.setdefault(key, []).append(post.slug)
        return dict(sorted(months.items(), reverse=True))

    def export(self) -> dict[str, Any]:
        """The whole blog as plain data (backup/migration format)."""
        return {
            "posts": [post.to_row() for post in self.posts.all()],
            "comments": [comment.to_row() for comment in self.comments.all_rows()],
        }
