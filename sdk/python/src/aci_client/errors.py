"""Typed errors carrying the stable §45 error codes.

The server translates every ``DomainError`` into
``{"error": {"code": ..., "message": ...}}`` with a deliberate HTTP status
(``adapters/inbound/rest/errors.py``); the SDK turns that body back into a
typed exception so callers can branch on the stable machine-readable code
instead of parsing strings. Two shapes are NOT §45 bodies and get their own
types: FastAPI's request-validation 422 (``{"detail": [...]}``) and the
bearer gate's 401 (``{"detail": "..."}``).
"""

from typing import Any


class ACIError(Exception):
    """The server answered with an error (or the SDK failed a local check).

    ``code`` is the stable §45 error code string (e.g.
    ``"CAPABILITY_NOT_FOUND"``); ``status_code`` is the HTTP status the
    server used, or ``None`` for client-side failures that never produced
    a response.
    """

    def __init__(self, code: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.status_code = status_code

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(code={self.code!r}, status_code={self.status_code!r})"


class ACIValidationError(ACIError):
    """422 request-validation failure (FastAPI ``{"detail": [...]}``).

    The request never reached domain logic — the wire schema rejected it.
    ``details`` carries the raw validation entries (loc/msg/type).
    """

    def __init__(self, message: str, details: Any) -> None:
        super().__init__("REQUEST_INVALID", message, status_code=422)
        self.details = details


class SkillIntegrityError(ACIError):
    """A skill file's bytes failed its sha256 check (§39 supply chain).

    Raised client-side by ``get_skill`` BEFORE the content is returned: a
    tampered payload must never reach model context. Carries the server's
    own §45 code for this condition (``ARTIFACT_INTEGRITY_ERROR``).
    """

    def __init__(self, message: str) -> None:
        super().__init__("ARTIFACT_INTEGRITY_ERROR", message)


class ACIConnectionError(Exception):
    """Transport failure — no HTTP response was received.

    Deliberately NOT an :class:`ACIError`: there is no §45 code for "the
    server is down". Never carries request headers (the bearer token).
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
