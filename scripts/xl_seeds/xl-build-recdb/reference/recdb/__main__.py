"""Entry point: ``python -m recdb <command> ...``."""

from __future__ import annotations

import sys

from recdb.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
