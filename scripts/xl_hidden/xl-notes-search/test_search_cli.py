"""Hidden acceptance: the CLI — the new search command and the pinned
old commands.
"""

from __future__ import annotations

import json

from jotbase.cli import main
from jotbase.service import NoteService
from jotbase.storage import NoteStore


def test_cli_search_prints_ids_and_titles(tmp_path, capsys):
    service = NoteService(NoteStore(tmp_path / "data"))
    service.add("Deploy runbook", body="how we ship")
    service.add("Garden", body="basil")
    rc = main(["--root", str(tmp_path / "data"), "search", "deploy"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Deploy runbook" in out
    assert "Garden" not in out
    assert "1 of 1 results" in out


def test_cli_search_json_envelope(tmp_path, capsys):
    service = NoteService(NoteStore(tmp_path / "data"))
    service.add("Deploy runbook", body="how we ship")
    rc = main(["--root", str(tmp_path / "data"), "search", "deploy", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "query",
        "page",
        "page_size",
        "total",
        "total_pages",
        "items",
    }
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "Deploy runbook"


def test_cli_search_page_flags(tmp_path, capsys):
    service = NoteService(NoteStore(tmp_path / "data"))
    for i in range(5):
        service.add(f"deploy note {i}", body="filler")
    rc = main(
        ["--root", str(tmp_path / "data"), "search", "deploy", "--page", "2", "--page-size", "2"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "page 2/3" in out


def test_cli_search_empty_query_is_an_error(tmp_path, capsys):
    NoteService(NoteStore(tmp_path / "data"))
    rc = main(["--root", str(tmp_path / "data"), "search", ""])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cli_old_commands_still_work(tmp_path, capsys):
    root = tmp_path / "data"
    rc = main(["--root", str(root), "add", "--title", "Deploy", "--tag", "ops"])
    assert rc == 0
    capsys.readouterr()
    rc = main(["--root", str(root), "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Deploy" in out and "[ops]" in out
    note_id = out.split()[0]
    rc = main(["--root", str(root), "show", note_id])
    assert rc == 0
    rc = main(["--root", str(root), "stats"])
    assert rc == 0
    assert "ops: 1" in capsys.readouterr().out
