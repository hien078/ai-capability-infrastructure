"""The query expression language: terms, operators, AND groups.

Unimplemented skeleton — see README.md §4 (query) for the grammar and
comparison semantics this module must implement.
"""

from __future__ import annotations

from typing import Any


def parse(expr: str) -> Any:
    """Parse a query expression into AND-joined terms."""
    raise NotImplementedError("recdb query language is not implemented yet")


def matches(record: dict[str, Any], parsed: Any) -> bool:
    """Does the record satisfy the parsed expression?"""
    raise NotImplementedError("recdb query language is not implemented yet")
