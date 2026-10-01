"""Caller-visible text hygiene (m5 ADV-1, m9 ADV-2): absolute server paths
never reach a client.

Two surfaces are built from text a SERVER-side tool printed and then handed
to a caller verbatim: the sandbox probe's refusal reason (ADV-1 — bwrap's
own stderr, which can name a failed bind source or the probe tempdir) and the
verifier's failed-check summary (ADV-2 — the verification command's output,
which can name ``sys.prefix``, the repo, anything the command can print; the
client picks that command). Scrubbing is textual and conservative: anything
that looks like an absolute path becomes ``<path>``. Relative paths —
workspace-relative, what the model itself created — stay readable.

This is a disclosure boundary, not a correctness one: the MODEL still gets
the full unscrubbed output where it needs it (repair feedback lives in the
server-side transcript), and the operator still gets raw diagnostics in the
server log.
"""

import re

#: An absolute-path-shaped token: a ``/`` not preceded by a word character,
#: dot or hyphen (so ``a/b`` and ``x-y/z`` stay relative), extending to the
#: next whitespace or quote. Paths with spaces are cut at the space — the
#: first segment still never reaches the client.
ABSOLUTE_PATH = re.compile(r"(?<![\w.-])/(?:[^\s\"']*)")


def scrub_paths(text: str) -> str:
    """Replace every absolute path in ``text`` with ``<path>`` (ADV-1/ADV-2:
    the text is about to become caller-visible — no server paths on it)."""
    return ABSOLUTE_PATH.sub("<path>", text)


__all__ = ["ABSOLUTE_PATH", "scrub_paths"]
