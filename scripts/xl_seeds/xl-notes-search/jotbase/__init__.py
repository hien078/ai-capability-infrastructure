"""jotbase — a small note-taking service.

JSON storage, a service layer, a stdlib HTTP API and a CLI.
"""

from __future__ import annotations

from jotbase.errors import InvalidNote, JotbaseError, NoteNotFound
from jotbase.model import Note, new_id, normalize_tags, slugify_tag
from jotbase.service import NoteService
from jotbase.storage import NoteStore

__version__ = "1.3.0"

__all__ = [
    "InvalidNote",
    "JotbaseError",
    "Note",
    "NoteNotFound",
    "NoteService",
    "NoteStore",
    "new_id",
    "normalize_tags",
    "slugify_tag",
]
