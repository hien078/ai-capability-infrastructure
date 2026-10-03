"""The command line interface: add/get/list/query/update/delete/import/export/stats/check.

Unimplemented skeleton — see README.md §4 (commands) and §6 (exit codes)
for the contract this module must implement. ``main(argv)`` returns the
process exit code.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Run the CLI with the given arguments (default: sys.argv[1:])."""
    raise NotImplementedError("recdb CLI is not implemented yet")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
