"""The boardline HTTP server, store and page rendering.

Unimplemented skeleton — see README.md for the normative contract this
module must implement: ``make_server(host, port, data_path)`` returns a
ready ``ThreadingHTTPServer``; ``main(argv)`` is the CLI entry point.
"""

from __future__ import annotations

import sys
from typing import Any


def make_server(host: str, port: int, data_path: str) -> Any:
    """Create a bound, ready ThreadingHTTPServer (serving is the caller's job)."""
    raise NotImplementedError("boardline is not implemented yet")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI: parse flags, print the listening line, serve forever."""
    raise NotImplementedError("boardline is not implemented yet")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
