"""Structured logging setup (stdlib; JSON format later if needed)."""

import logging
import sys

_configured = False


def setup_logging(level: str = "INFO") -> None:
    """Idempotent basicConfig; call once at process startup."""
    global _configured
    if _configured:
        return
    logging.basicConfig(
        stream=sys.stdout,
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _configured = True
