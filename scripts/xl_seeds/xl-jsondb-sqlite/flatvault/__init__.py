"""flatvault — a tiny document store (JSON file per table) + a blog app."""

from __future__ import annotations

from flatvault.database import Database
from flatvault.errors import FlatvaultError, PersistenceError, QueryError, TableError
from flatvault.queries import (
    And,
    AndQuery,
    Contains,
    Eq,
    Gt,
    Gte,
    In,
    Lt,
    Lte,
    Ne,
    Or,
    OrQuery,
    Query,
    where,
)
from flatvault.serialize import dump, encode, load, read_document, write_document
from flatvault.tables import Table

__version__ = "0.9.0"

__all__ = [
    "And",
    "AndQuery",
    "Contains",
    "Database",
    "Eq",
    "FlatvaultError",
    "Gt",
    "Gte",
    "In",
    "Lt",
    "Lte",
    "Ne",
    "Or",
    "OrQuery",
    "PersistenceError",
    "Query",
    "QueryError",
    "Table",
    "TableError",
    "dump",
    "encode",
    "load",
    "read_document",
    "where",
    "write_document",
]
