"""Hidden acceptance: the docs.

The README is the contract a client reads — the search endpoint, the
CLI command and the ranking rules must be in it. (The README lives at
the workspace root, next to the package.)
"""

from __future__ import annotations

from pathlib import Path

import jotbase

README = Path(jotbase.__file__).resolve().parent.parent / "README.md"


def test_readme_documents_the_search_endpoint():
    text = README.read_text(encoding="utf-8")
    assert "/notes/search" in text
    assert "q=" in text


def test_readme_documents_the_search_cli_and_rules():
    text = README.read_text(encoding="utf-8")
    assert "search" in text
    assert "page_size" in text
    assert "index.json" in text
    assert "title" in text  # the title boost is documented
