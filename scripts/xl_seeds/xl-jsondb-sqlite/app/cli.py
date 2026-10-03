"""The blog command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from flatvault.database import Database

from app.service import BlogError, BlogService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blog", description="A small blog on flatvault.")
    parser.add_argument("--root", default="blog-data", help="data directory")
    sub = parser.add_subparsers(dest="command", required=True)

    post = sub.add_parser("post", help="post subcommands")
    post_sub = post.add_subparsers(dest="post_command", required=True)
    add = post_sub.add_parser("add")
    add.add_argument("--title", required=True)
    add.add_argument("--body", default="")
    add.add_argument("--tag", action="append", default=[])
    listing = post_sub.add_parser("list")
    listing.add_argument("--all", action="store_true")
    publisher = post_sub.add_parser("publish")
    publisher.add_argument("slug")

    comment = sub.add_parser("comment", help="comment subcommands")
    comment_sub = comment.add_subparsers(dest="comment_command", required=True)
    cadd = comment_sub.add_parser("add")
    cadd.add_argument("slug")
    cadd.add_argument("--author", required=True)
    cadd.add_argument("--body", required=True)

    sub.add_parser("stats", help="counts")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    service = BlogService(Database(Path(args.root)))
    try:
        if args.command == "post":
            if args.post_command == "add":
                post = service.create_post(args.title, args.body, args.tag)
                print(f"created {post.slug} (id {post.id})")
                return 0
            if args.post_command == "list":
                posts = service.list_posts(published=None if args.all else True)
                for post in posts:
                    print(f"{post.slug}  {post.title}  [{','.join(post.tags)}]")
                return 0
            if args.post_command == "publish":
                post = service.publish(args.slug)
                print(f"published {post.slug}")
                return 0
        if args.command == "comment":
            comment = service.add_comment(args.slug, args.author, args.body)
            print(f"comment {comment.id} on {args.slug}")
            return 0
        if args.command == "stats":
            stats = service.stats()
            print(f"posts: {stats['posts']} ({stats['published']} published)")
            print(f"comments: {stats['comments']}")
            for tag, count in stats["tags"].items():
                print(f"  {tag}: {count}")
            return 0
    except BlogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
