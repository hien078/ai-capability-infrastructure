"""Error types for the ledger package.

Every failure the ledger can raise derives from :class:`LedgerError`, so
callers can catch one base class. The names are part of the public API:
the CLI maps them to exit code 1 and a stderr line, and the import tool
reports them verbatim.
"""

from __future__ import annotations


class LedgerError(Exception):
    """Base class for every ledger failure."""


class ParseError(LedgerError):
    """A bank export could not be parsed (bad header, bad row shape)."""


class ValidationError(LedgerError):
    """A transaction, account, amount or timestamp failed validation."""


class DuplicateTransactionError(LedgerError):
    """A transaction id already exists in the store."""


class UnknownAccountError(LedgerError):
    """A lookup referenced an account that does not exist."""
