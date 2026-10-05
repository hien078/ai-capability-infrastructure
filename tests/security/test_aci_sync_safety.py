"""Security boundaries for scripts/aci_sync.py — the native-skill-dir sync
client (job p-aci-sync, 2026-10-03).

The tool takes REMOTE bytes (an ACI server's catalog) and writes them into
a user's home/project directory, next to the user's OWN skills. These
tests pin the safety contract that must hold even against a hostile or
compromised server:

    * served paths must be relative POSIX with no traversal — an unsafe
      skill is rejected whole and NOTHING is written outside the target;
    * the lockfile is never written through a symlink, and a managed dir
      that became a symlink is REPLACED, never written through;
    * a dir that is not in the lockfile (the user's own skill) is never
      touched, and a served skill colliding with one is refused;
    * the bearer token (ACI_API_TOKEN) never appears in any output;
    * --dry-run writes nothing, not even the lockfile.

All HTTP is httpx.MockTransport — no network, no DB.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import aci_sync as sync  # noqa: E402


def _transport(index: dict[str, Any], files: dict[str, bytes] | None = None) -> httpx.MockTransport:
    """Serve a hand-crafted index (and optional file bytes by URL path)."""
    served = files or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/opencode/skills/index.json":
            return httpx.Response(200, json=index)
        data = served.get(request.url.path)
        if data is not None:
            return httpx.Response(200, content=data)
        return httpx.Response(200, content=b"malicious bytes")

    return httpx.MockTransport(handler)


def _opts(target: Path, **kw: Any) -> sync.SyncOptions:
    kw.setdefault("client", "dir")
    kw.setdefault("server", "http://aci.test:8000")
    return sync.SyncOptions(target=str(target), **kw)


def _skill(name: str, version: str, files: list[Any]) -> dict[str, Any]:
    return {"name": name, "version": version, "files": files}


def _index(*skills: dict[str, Any]) -> dict[str, Any]:
    return {"skills": list(skills)}


def _file_url(name: str, catalog_path: str) -> str:
    return f"/opencode/skills/{name}/{catalog_path}"


# -- served-path traversal ------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["../../evil.md", "/etc/passwd", "..\\windows.md", "a/../../../b.md", "a//b.md", "a/./b.md"],
)
def test_traversal_paths_reject_the_whole_skill(tmp_path: Path, path: str) -> None:
    index = _index(_skill("debugging", "1.0.0", [path, "debugging.md"]))
    target = tmp_path / "skills"

    code = sync.run_sync(_opts(target), transport=_transport(index))

    assert code == 1
    assert not (target / "debugging").exists()  # the skill is rejected whole
    assert list(target.iterdir()) == [target / sync.LOCK_NAME]  # nothing else written
    # Nothing escaped the target.
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("name", ["../evil", "a/b", ".aci-sync.lock.json", ".hidden", ""])
def test_unsafe_skill_names_are_rejected(tmp_path: Path, name: str) -> None:
    index = _index(_skill(name, "1.0.0", [f"{name}.md"]))
    target = tmp_path / "skills"

    code = sync.run_sync(_opts(target), transport=_transport(index))

    assert code == 1
    assert [p.name for p in target.iterdir() if p.is_dir()] == []


def test_absolute_path_in_lockfile_is_refused(tmp_path: Path) -> None:
    """A hand-crafted/replaced lockfile with an unsafe path must abort the
    run — never hash or write through it."""
    target = tmp_path / "skills"
    target.mkdir(parents=True)
    (target / sync.LOCK_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "server": "http://aci.test:8000",
                "skills": {"debugging": {"version": "1.0.0", "files": {"../../evil": "x" * 64}}},
            }
        ),
        encoding="utf-8",
    )
    index = _index(_skill("debugging", "1.0.0", ["debugging.md"]))

    code = sync.run_sync(_opts(target), transport=_transport(index))

    assert code == 1
    assert not (tmp_path / "evil").exists()


# -- symlinks -------------------------------------------------------------------


def test_symlinked_lockfile_is_refused_not_followed(tmp_path: Path) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text("precious", encoding="utf-8")
    target = tmp_path / "skills"
    target.mkdir()
    os.symlink(victim, target / sync.LOCK_NAME)
    index = _index(_skill("debugging", "1.0.0", ["debugging.md"]))

    code = sync.run_sync(_opts(target), transport=_transport(index))

    assert code == 1
    assert victim.read_text(encoding="utf-8") == "precious"  # never written through
    assert (target / sync.LOCK_NAME).is_symlink()  # and not replaced either


def test_managed_dir_turned_symlink_is_replaced_not_followed(tmp_path: Path) -> None:
    """A managed skill dir that an attacker replaced with a symlink to their
    own dir: the sync must swap in a REAL dir, never write through the
    symlink (the victim's content stays untouched)."""
    index = _index(_skill("debugging", "1.0.0", ["debugging.md"]))
    target = tmp_path / "skills"
    assert sync.run_sync(_opts(target), transport=_transport(index)) == 0

    victim = tmp_path / "victim-dir"
    victim.mkdir()
    (victim / "keep.md").write_text("precious", encoding="utf-8")
    shutil.rmtree(target / "debugging")
    os.symlink(victim, target / "debugging")

    code = sync.run_sync(_opts(target), transport=_transport(index))

    assert code == 0
    assert not (target / "debugging").is_symlink()
    assert (target / "debugging").is_dir()
    assert (victim / "keep.md").read_text(encoding="utf-8") == "precious"  # untouched


def test_symlink_inside_managed_dir_blocks_prune_of_drifted_content(
    tmp_path: Path,
) -> None:
    """_dir_matches_lock treats any symlink as drift: a revoked skill whose
    local dir contains a symlink is never auto-deleted without --force."""
    index = _index(_skill("debugging", "1.0.0", ["debugging.md"]))
    target = tmp_path / "skills"
    assert sync.run_sync(_opts(target), transport=_transport(index)) == 0
    # Revoke server-side, then plant a symlink inside the managed dir.
    empty_index = _index()
    os.symlink(tmp_path / "elsewhere", target / "debugging" / "link")

    code = sync.run_sync(_opts(target), transport=_transport(empty_index))

    assert code == 1  # drift -> prune refused
    assert (target / "debugging").exists()
    assert not (tmp_path / "elsewhere").exists()  # never followed/created through it


# -- user content ---------------------------------------------------------------


def test_served_skill_colliding_with_user_dir_is_refused(tmp_path: Path) -> None:
    own = tmp_path / "skills" / "my-own"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("# mine\n", encoding="utf-8")
    index = _index(_skill("my-own", "1.0.0", ["my-own.md"]))

    code = sync.run_sync(_opts(tmp_path / "skills"), transport=_transport(index))

    assert code == 1
    assert (own / "SKILL.md").read_text(encoding="utf-8") == "# mine\n"  # untouched
    assert (
        "my-own"
        not in json.loads((tmp_path / "skills" / sync.LOCK_NAME).read_text(encoding="utf-8"))[
            "skills"
        ]
    )


def test_dry_run_touches_no_existing_state(tmp_path: Path) -> None:
    index = _index(_skill("debugging", "1.0.0", ["debugging.md"]))
    target = tmp_path / "skills"
    assert sync.run_sync(_opts(target), transport=_transport(index)) == 0
    before = (target / "debugging" / "SKILL.md").read_bytes()
    lock_before = (target / sync.LOCK_NAME).read_bytes()

    code = sync.run_sync(_opts(target, dry_run=True), transport=_transport(index))

    assert code == 0
    assert (target / "debugging" / "SKILL.md").read_bytes() == before
    assert (target / sync.LOCK_NAME).read_bytes() == lock_before


# -- secret hygiene ---------------------------------------------------------------


def test_bearer_token_never_appears_in_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "s3cret-bearer-token"
    monkeypatch.setenv("ACI_API_TOKEN", token)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(401)

    target = tmp_path / "skills"
    code = sync.main(
        ["--client", "dir", "--target", str(target), "--server", "http://aci.test:8000"],
        transport=httpx.MockTransport(handler),
    )

    assert code == 1
    assert seen == [f"Bearer {token}"]  # the token does reach the server...
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err  # ...but never any output


def test_transport_errors_never_leak_the_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "s3cret-bearer-token"
    monkeypatch.setenv("ACI_API_TOKEN", token)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    code = sync.main(
        ["--client", "dir", "--target", str(tmp_path / "s"), "--server", "http://aci.test:8000"],
        transport=httpx.MockTransport(boom),
    )
    assert code == 1
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


# -- write containment -------------------------------------------------------------


def test_nothing_is_ever_written_outside_the_target(tmp_path: Path) -> None:
    index = _index(
        _skill("debugging", "1.0.0", ["debugging.md", "refs/deep/notes.md", "refs/deep/more.md"])
    )
    target = tmp_path / "skills"

    code = sync.run_sync(_opts(target), transport=_transport(index))

    assert code == 0
    # The target's parent sees ONLY the target — no sibling escapes.
    assert [p.name for p in tmp_path.iterdir()] == ["skills"]
    # And every written path is inside the target.
    for path in target.rglob("*"):
        assert target in path.parents


# -- goose / antigravity targets ----------------------------------------------------

_NEW_CLIENTS = [
    ("goose", (".agents", "skills")),
    ("antigravity", (".gemini", "config", "skills")),
]


@pytest.mark.parametrize(("client", "rel"), _NEW_CLIENTS)
def test_new_client_writes_stay_inside_its_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str, rel: tuple[str, ...]
) -> None:
    """A hostile index (traversal + absolute paths) next to a good skill:
    under a fake HOME, nothing lands outside the client's skill root — in
    particular NOT in ~/.claude/skills (Claude Code's dir) for goose."""
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_own = tmp_path / ".claude" / "skills" / "debugging"
    claude_own.mkdir(parents=True)
    (claude_own / "SKILL.md").write_text("# claude's own\n", encoding="utf-8")
    index = _index(
        _skill("debugging", "1.0.0", ["debugging.md", "refs/notes.md"]),
        _skill("evil", "1.0.0", ["evil.md", "../../escape.md"]),
        _skill("evil-abs", "1.0.0", ["evil-abs.md", "/etc/passwd"]),
    )
    root = tmp_path.joinpath(*rel)
    before = {p for p in tmp_path.rglob("*")}

    code = sync.run_sync(
        sync.SyncOptions(client=client, server="http://aci.test:8000"),
        transport=_transport(index),
    )

    assert code == 1  # the unsafe skills are rejected
    assert not (root / "evil").exists() and not (root / "evil-abs").exists()
    new_paths = {p for p in tmp_path.rglob("*")} - before
    for path in new_paths:
        assert path == root or root in path.parents or path in root.parents
    assert (claude_own / "SKILL.md").read_text(encoding="utf-8") == "# claude's own\n"


@pytest.mark.parametrize(("client", "rel"), _NEW_CLIENTS)
def test_new_client_never_clobbers_unowned_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str, rel: tuple[str, ...]
) -> None:
    """A served skill colliding with a dir the lockfile does not own is
    refused; and an unowned dir is never pruned, even with --force."""
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path.joinpath(*rel)
    own = root / "my-own"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("# mine\n", encoding="utf-8")
    o = sync.SyncOptions(client=client, server="http://aci.test:8000")

    code = sync.run_sync(o, transport=_transport(_index(_skill("my-own", "1.0.0", ["my-own.md"]))))

    assert code == 1
    assert (own / "SKILL.md").read_text(encoding="utf-8") == "# mine\n"
    lock = json.loads((root / sync.LOCK_NAME).read_text(encoding="utf-8"))
    assert "my-own" not in lock["skills"]

    forced = sync.SyncOptions(client=client, server="http://aci.test:8000", force=True)
    assert sync.run_sync(forced, transport=_transport(_index())) == 0
    assert (own / "SKILL.md").read_text(encoding="utf-8") == "# mine\n"


@pytest.mark.parametrize(("client", "rel"), _NEW_CLIENTS)
def test_new_client_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str, rel: tuple[str, ...]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    index = _index(_skill("debugging", "1.0.0", ["debugging.md"]))

    code = sync.run_sync(
        sync.SyncOptions(client=client, server="http://aci.test:8000", dry_run=True),
        transport=_transport(index),
    )

    assert code == 0
    assert list(tmp_path.iterdir()) == []  # not even the root dir
