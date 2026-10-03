"""Error types for jotbase.

Every failure derives from :class:`JotbaseError` so callers (the API, the
CLI) can map one base class to one wire/exit behavior.
"""

from __future__ import annotations


class JotbaseError(Exception):
    """Base class for every jotbase failure."""


class NoteNotFound(JotbaseError):
    """A lookup referenced a note id that does not exist."""


class InvalidNote(JotbaseError):
    """A note failed validation (title, tags, types)."""
