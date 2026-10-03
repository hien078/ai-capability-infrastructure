"""The where() query DSL: small, composable, pure.

``where("age") > 18`` builds a :class:`Gt`; ``And``/``Or`` combine;
``match(query, doc)`` answers for one row dict. Rules that pin the DSL:

* a missing field never matches — not even ``!=``;
* ordering comparisons that would raise ``TypeError`` (int vs str)
  are a non-match, never a crash;
* ``contains`` is a substring test on string values only;
* ``in_`` is equality against each candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from flatvault.errors import QueryError


@dataclass(frozen=True)
class Query:
    """Base class — every node answers ``match(doc)``."""

    def match(self, doc: dict[str, Any]) -> bool:  # pragma: no cover - overridden
        raise QueryError(f"not a query: {self!r}")


@dataclass(frozen=True)
class Eq(Query):
    field: str
    value: Any

    def match(self, doc: dict[str, Any]) -> bool:
        return self.field in doc and doc[self.field] == self.value


@dataclass(frozen=True)
class Ne(Query):
    field: str
    value: Any

    def match(self, doc: dict[str, Any]) -> bool:
        return self.field in doc and doc[self.field] != self.value


@dataclass(frozen=True)
class Gt(Query):
    field: str
    value: Any

    def match(self, doc: dict[str, Any]) -> bool:
        return _compare(doc, self.field, self.value, lambda a, b: a > b)


@dataclass(frozen=True)
class Gte(Query):
    field: str
    value: Any

    def match(self, doc: dict[str, Any]) -> bool:
        return _compare(doc, self.field, self.value, lambda a, b: a >= b)


@dataclass(frozen=True)
class Lt(Query):
    field: str
    value: Any

    def match(self, doc: dict[str, Any]) -> bool:
        return _compare(doc, self.field, self.value, lambda a, b: a < b)


@dataclass(frozen=True)
class Lte(Query):
    field: str
    value: Any

    def match(self, doc: dict[str, Any]) -> bool:
        return _compare(doc, self.field, self.value, lambda a, b: a <= b)


@dataclass(frozen=True)
class In(Query):
    field: str
    values: tuple

    def match(self, doc: dict[str, Any]) -> bool:
        if self.field not in doc:
            return False
        return any(doc[self.field] == value for value in self.values)


@dataclass(frozen=True)
class Contains(Query):
    field: str
    text: str

    def match(self, doc: dict[str, Any]) -> bool:
        if self.field not in doc:
            return False
        value = doc[self.field]
        return isinstance(value, str) and self.text in value


@dataclass(frozen=True)
class AndQuery(Query):
    queries: tuple

    def match(self, doc: dict[str, Any]) -> bool:
        return all(q.match(doc) for q in self.queries)


@dataclass(frozen=True)
class OrQuery(Query):
    queries: tuple

    def match(self, doc: dict[str, Any]) -> bool:
        return any(q.match(doc) for q in self.queries)


def And(*queries: Query) -> Query:
    """Combine queries: every one must match."""
    return AndQuery(tuple(queries))


def Or(*queries: Query) -> Query:
    """Combine queries: any one may match."""
    return OrQuery(tuple(queries))


def _compare(doc: dict[str, Any], field: str, value: Any, op: Any) -> bool:
    if field not in doc:
        return False
    try:
        return bool(op(doc[field], value))
    except TypeError:
        return False


class FieldRef:
    """``where("field")`` — the DSL entry point."""

    __slots__ = ("field",)

    def __init__(self, field: str) -> None:
        if not isinstance(field, str) or not field:
            raise QueryError("field name must be a non-empty string")
        self.field = field

    def __eq__(self, other: Any) -> Eq:  # type: ignore[override]
        return Eq(self.field, other)

    def __ne__(self, other: Any) -> Ne:  # type: ignore[override]
        return Ne(self.field, other)

    def __gt__(self, other: Any) -> Gt:
        return Gt(self.field, other)

    def __ge__(self, other: Any) -> Gte:
        return Gte(self.field, other)

    def __lt__(self, other: Any) -> Lt:
        return Lt(self.field, other)

    def __le__(self, other: Any) -> Lte:
        return Lte(self.field, other)

    def in_(self, values: Any) -> In:
        return In(self.field, tuple(values))

    def contains(self, text: str) -> Contains:
        return Contains(self.field, text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"where({self.field!r})"


def where(field: str) -> FieldRef:
    """Start a query: ``where("age") > 18``."""
    return FieldRef(field)


def match(query: Any, doc: dict[str, Any]) -> bool:
    """Does one row dict answer one query? (The Table.find entry point.)"""
    if isinstance(query, Query):
        return query.match(doc)
    raise QueryError(f"not a query: {query!r}")


def utcnow() -> datetime:
    """The store's clock (aware UTC)."""
    return datetime.now(UTC)
