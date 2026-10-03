"""Entry point: ``python -m boardline [--host H] [--port P] [--data PATH]``."""

from __future__ import annotations

import sys

from boardline.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
