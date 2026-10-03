"""Error types for flatvault.

``TableError`` — bad table usage (wrong row shape, unknown table).
``QueryError`` — bad query DSL usage.
``PersistenceError`` — the store could not be read or written.
"""

from __future__ import annotations


class FlatvaultError(Exception):
    """Base class for every flatvault failure."""


class TableError(FlatvaultError):
    """A row or table operation was invalid."""


class QueryError(FlatvaultError):
    """A query was malformed."""


class PersistenceError(FlatvaultError):
    """Reading or writing the store failed."""
