"""Unit tests for scripts/aci_sync.py — the native-skill-dir sync client.

All HTTP is httpx.MockTransport (no network, no DB): a tiny fake catalog
serves the SAME wire shapes as the real OpenCode projection
(`GET /opencode/skills/index.json` + `GET /opencode/skills/<name>/<file>`,
ADR-005). The adversarial cases (traversal, symlinks, secret leakage) live
in tests/security/test_aci_sync_safety.py; this file pins the functional
contract: fresh sync, byte-idempotence, update, prune, filters, dry-run.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import aci_sync as sync  # noqa: E402


class FakeCatalog:
    """The ACI OpenCode catalog, as a wire-shaped fake."""

    def __init__(self) -> None:
        # name -> {"version": str, "files": {catalog_path: bytes}}
        self.skills: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []

    def add(self, name: str, version: str, files: dict[str, str]) -> None:
        self.skills[name] = {
            "version": version,
            "files": {path: text.encode() for path, text in files.items()},
        }

    def remove(self, name: str) -> None:
        self.skills.pop(name, None)

    def index(self) -> dict[str, Any]:
        return {
            "skills": [
                {"name": name, "version": skill["version"], "files": list(skill["files"])}
                for name, skill in self.skills.items()
            ]
        }

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            path = request.url.path
            if path == "/opencode/skills/index.json":
                return httpx.Response(200, json=self.index())
            rest = path.removeprefix("/opencode/skills/")
            name, _, file_path = rest.partition("/")
            skill = self.skills.get(name)
            if skill is not None and file_path in skill["files"]:
                return httpx.Response(200, content=skill["files"][file_path])
            return httpx.Response(404, json={"detail": "not found"})

        return httpx.MockTransport(handler)


def opts(target: Path, **kw: Any) -> sync.SyncOptions:
    kw.setdefault("client", "dir")
    kw.setdefault("server", "http://aci.test:8000")
    return sync.SyncOptions(target=str(target), **kw)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lock_of(target: Path) -> dict[str, Any]:
    return json.loads((target / sync.LOCK_NAME).read_text(encoding="utf-8"))


def snapshot(target: Path) -> dict[str, tuple[int, bytes]]:
    """path -> (mtime_ns, content) for every file under target."""
    out: dict[str, tuple[int, bytes]] = {}
    for path in sorted(target.rglob("*")):
        if path.is_file():
            stat = path.stat()
            out[path.relative_to(target).as_posix()] = (stat.st_mtime_ns, path.read_bytes())
    return out


def test_fresh_sync_writes_native_layout_and_lock(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.add(
        "debugging",
        "1.0.0",
        {"debugging.md": "# debug\n", "refs/notes.md": "notes\n"},
    )
    catalog.add("tdd", "2.0.0", {"tdd.md": "# tdd\n"})
    target = tmp_path / "skills"

    code = sync.run_sync(opts(target), transport=catalog.transport())

    assert code == 0
    # The catalog's `<name>.md` entry alias lands as the native `SKILL.md`.
    assert (target / "debugging" / "SKILL.md").read_bytes() == b"# debug\n"
    assert (target / "debugging" / "refs" / "notes.md").read_bytes() == b"notes\n"
    assert (target / "tdd" / "SKILL.md").read_bytes() == b"# tdd\n"

    lock = lock_of(target)
    assert lock["version"] == sync.LOCK_VERSION
    assert lock["server"] == "http://aci.test:8000"
    assert lock["skills"]["debugging"] == {
        "version": "1.0.0",
        "files": {"SKILL.md": sha(b"# debug\n"), "refs/notes.md": sha(b"notes\n")},
    }
    assert lock["skills"]["tdd"]["version"] == "2.0.0"
    # Lockfile paths are relative POSIX, never absolute.
    assert all(not p.startswith("/") for p in lock["skills"]["debugging"]["files"])


def test_second_run_is_a_byte_noop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# debug\n"})
    target = tmp_path / "skills"
    assert sync.run_sync(opts(target), transport=catalog.transport()) == 0
    before = snapshot(target)
    lock_before = (target / sync.LOCK_NAME).read_bytes()

    code = sync.run_sync(opts(target), transport=catalog.transport())

    assert code == 0
    assert snapshot(target) == before  # no file touched, no lockfile rewrite
    assert (target / sync.LOCK_NAME).read_bytes() == lock_before
    assert "= debugging" in capsys.readouterr().out


def test_update_on_new_version_replaces_the_whole_dir(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "old", "extra.md": "E"})
    target = tmp_path / "skills"
    assert sync.run_sync(opts(target), transport=catalog.transport()) == 0

    catalog.add("debugging", "1.1.0", {"debugging.md": "new", "added.md": "A"})
    code = sync.run_sync(opts(target), transport=catalog.transport())

    assert code == 0
    assert (target / "debugging" / "SKILL.md").read_bytes() == b"new"
    assert (target / "debugging" / "added.md").read_bytes() == b"A"
    assert not (target / "debugging" / "extra.md").exists()  # removed server-side
    entry = lock_of(target)["skills"]["debugging"]
    assert entry["version"] == "1.1.0"
    assert entry["files"] == {"SKILL.md": sha(b"new"), "added.md": sha(b"A")}


def test_prune_of_revoked_skill(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    catalog.add("tdd", "1.0.0", {"tdd.md": "# t\n"})
    target = tmp_path / "skills"
    assert sync.run_sync(opts(target), transport=catalog.transport()) == 0

    catalog.remove("tdd")  # revoked / removed server-side
    code = sync.run_sync(opts(target), transport=catalog.transport())

    assert code == 0
    assert not (target / "tdd").exists()
    assert (target / "debugging" / "SKILL.md").exists()  # the rest is untouched
    assert set(lock_of(target)["skills"]) == {"debugging"}


def test_prune_refuses_drifted_local_content_until_forced(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    target = tmp_path / "skills"
    assert sync.run_sync(opts(target), transport=catalog.transport()) == 0
    catalog.remove("debugging")
    (target / "debugging" / "SKILL.md").write_bytes(b"user edits")

    code = sync.run_sync(opts(target), transport=catalog.transport())

    assert code == 1  # refused: local content no longer matches the lockfile
    assert (target / "debugging" / "SKILL.md").read_bytes() == b"user edits"
    assert "debugging" in lock_of(target)["skills"]

    code = sync.run_sync(opts(target, force=True), transport=catalog.transport())
    assert code == 0
    assert not (target / "debugging").exists()
    assert lock_of(target)["skills"] == {}


def test_user_skill_dirs_are_never_touched(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    target = tmp_path / "skills"
    own = target / "my-own-skill"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_bytes(b"# mine\n")

    code = sync.run_sync(opts(target), transport=catalog.transport())

    assert code == 0
    assert (own / "SKILL.md").read_bytes() == b"# mine\n"  # untouched
    assert "my-own-skill" not in lock_of(target)["skills"]


def test_filters_select_what_gets_synced(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    for name in ("debugging", "tdd", "testing", "claude-api"):
        catalog.add(name, "1.0.0", {f"{name}.md": f"# {name}\n"})
    target = tmp_path / "skills"

    code = sync.run_sync(opts(target, only_ids=("debugging", "tdd")), transport=catalog.transport())
    assert code == 0
    assert [p.name for p in sorted(target.iterdir()) if p.is_dir()] == ["debugging", "tdd"]

    target2 = tmp_path / "skills2"
    code = sync.run_sync(opts(target2, include=("t*",)), transport=catalog.transport())
    assert code == 0
    assert sorted(p.name for p in target2.iterdir() if p.is_dir()) == ["tdd", "testing"]

    target3 = tmp_path / "skills3"
    code = sync.run_sync(opts(target3, exclude=("claude-*",)), transport=catalog.transport())
    assert code == 0
    assert not (target3 / "claude-api").exists()
    assert (target3 / "debugging" / "SKILL.md").exists()


def test_only_ids_naming_an_unknown_skill_is_an_error(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    target = tmp_path / "skills"

    code = sync.run_sync(opts(target, only_ids=("nope",)), transport=catalog.transport())

    assert code == 1
    assert not (target / "nope").exists()


def test_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    target = tmp_path / "skills"

    code = sync.run_sync(opts(target, dry_run=True), transport=catalog.transport())

    out = capsys.readouterr().out
    assert code == 0
    assert not target.exists()  # not even the target dir, let alone a lockfile
    assert "dry run — nothing written" in out
    assert "+ debugging" in out  # the plan row is still printed


def test_dry_run_after_a_real_sync_changes_nothing(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "old"})
    target = tmp_path / "skills"
    assert sync.run_sync(opts(target), transport=catalog.transport()) == 0
    before = snapshot(target)

    catalog.add("debugging", "2.0.0", {"debugging.md": "new"})
    code = sync.run_sync(opts(target, dry_run=True), transport=catalog.transport())

    assert code == 0
    assert snapshot(target) == before
    assert (target / "debugging" / "SKILL.md").read_bytes() == b"old"


def test_bearer_token_flows_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    target = tmp_path / "skills"

    monkeypatch.setenv("ACI_API_TOKEN", "tok-123")
    code = sync.main(
        ["--client", "dir", "--target", str(target), "--server", "http://aci.test:8000"],
        transport=catalog.transport(),
    )
    assert code == 0
    assert catalog.requests[0].headers.get("authorization") == "Bearer tok-123"

    catalog.requests.clear()
    monkeypatch.delenv("ACI_API_TOKEN", raising=False)
    code = sync.main(
        ["--client", "dir", "--target", str(target), "--server", "http://aci.test:8000"],
        transport=catalog.transport(),
    )
    assert code == 0
    assert catalog.requests[0].headers.get("authorization") is None


def test_index_digests_are_verified_when_provided(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    target = tmp_path / "skills"
    good = {
        "skills": [
            {
                "name": "debugging",
                "version": "1.0.0",
                "files": [{"path": "debugging.md", "sha256": sha(b"# d\n")}],
            }
        ]
    }
    code = sync.run_sync(opts(target), transport=_index_transport(catalog, good))
    assert code == 0
    assert (target / "debugging" / "SKILL.md").read_bytes() == b"# d\n"

    tampered = {
        "skills": [
            {
                "name": "debugging",
                "version": "1.0.0",
                "files": [{"path": "debugging.md", "sha256": "0" * 64}],
            }
        ]
    }
    target2 = tmp_path / "skills2"
    code = sync.run_sync(opts(target2), transport=_index_transport(catalog, tampered))
    assert code == 1  # integrity rejection
    assert not (target2 / "debugging").exists()


def _index_transport(catalog: FakeCatalog, index: dict[str, Any]) -> httpx.MockTransport:
    """A transport serving a custom index but the catalog's file bytes."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/opencode/skills/index.json":
            return httpx.Response(200, json=index)
        rest = request.url.path.removeprefix("/opencode/skills/")
        name, _, file_path = rest.partition("/")
        skill = catalog.skills.get(name)
        if skill is not None and file_path in skill["files"]:
            return httpx.Response(200, content=skill["files"][file_path])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_http_failures_are_clear_not_tracebacks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "skills"

    def status_transport(status: int) -> httpx.MockTransport:
        return httpx.MockTransport(lambda request: httpx.Response(status))

    code = sync.main(
        ["--client", "dir", "--target", str(target), "--server", "http://aci.test:8000"],
        transport=status_transport(401),
    )
    assert code == 1
    assert "ACI_API_TOKEN" in capsys.readouterr().err
    assert not target.exists()

    code = sync.main(
        ["--client", "dir", "--target", str(tmp_path / "s2"), "--server", "http://aci.test:8000"],
        transport=status_transport(404),
    )
    assert code == 1
    assert "catalog" in capsys.readouterr().err.lower()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    code = sync.main(
        ["--client", "dir", "--target", str(tmp_path / "s3"), "--server", "http://aci.test:8000"],
        transport=httpx.MockTransport(boom),
    )
    assert code == 1
    assert "cannot reach" in capsys.readouterr().err


def test_missing_skill_dir_self_heals(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    target = tmp_path / "skills"
    assert sync.run_sync(opts(target), transport=catalog.transport()) == 0

    shutil.rmtree(target / "debugging")  # user deleted it; lockfile still knows it
    code = sync.run_sync(opts(target), transport=catalog.transport())

    assert code == 0
    assert (target / "debugging" / "SKILL.md").read_bytes() == b"# d\n"


def test_verify_re_downloads_and_detects_local_tampering(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    target = tmp_path / "skills"
    assert sync.run_sync(opts(target), transport=catalog.transport()) == 0
    (target / "debugging" / "SKILL.md").write_bytes(b"tampered")

    # Without --verify: (version, file list) match the lockfile -> no-op.
    sync.run_sync(opts(target), transport=catalog.transport())
    assert (target / "debugging" / "SKILL.md").read_bytes() == b"tampered"

    # With --verify: bytes are re-downloaded and the drift is repaired.
    code = sync.run_sync(opts(target, verify=True), transport=catalog.transport())
    assert code == 0
    assert (target / "debugging" / "SKILL.md").read_bytes() == b"# d\n"


def test_server_change_warns_and_updates_lock_provenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    target = tmp_path / "skills"
    assert sync.run_sync(opts(target), transport=catalog.transport()) == 0

    code = sync.run_sync(opts(target, server="http://other:9000"), transport=catalog.transport())

    assert code == 0
    out = capsys.readouterr().out
    assert "lockfile was written by server" in out
    assert lock_of(target)["server"] == "http://other:9000"


def test_client_target_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "proj"

    assert sync._resolve_target(sync.SyncOptions(client="opencode")) == (
        tmp_path / ".config" / "opencode" / "skills"
    )
    assert (
        sync._resolve_target(sync.SyncOptions(client="claude-code"))
        == tmp_path / ".claude" / "skills"
    )
    assert (
        sync._resolve_target(sync.SyncOptions(client="opencode", project=str(proj)))
        == proj / ".opencode" / "skills"
    )
    assert (
        sync._resolve_target(sync.SyncOptions(client="claude-code", project=str(proj)))
        == proj / ".claude" / "skills"
    )
    assert (
        sync._resolve_target(sync.SyncOptions(client="dir", target=str(tmp_path / "x")))
        == tmp_path / "x"
    )

    with pytest.raises(sync.SyncError):
        sync._resolve_target(sync.SyncOptions(client="dir"))  # no --target
    with pytest.raises(sync.SyncError):
        sync._resolve_target(sync.SyncOptions(client="dir", project=str(proj)))
    with pytest.raises(sync.SyncError):
        sync._resolve_target(
            sync.SyncOptions(client="opencode", project=str(proj), target=str(tmp_path))
        )


def test_goose_and_antigravity_target_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """goose -> ~/.agents/skills (NOT ~/.claude/skills: no collision with
    claude-code); antigravity -> ~/.gemini/config/skills (its global
    customization root). Both use the `.agents/skills` workspace dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "proj"

    goose = sync._resolve_target(sync.SyncOptions(client="goose"))
    agy = sync._resolve_target(sync.SyncOptions(client="antigravity"))
    claude = sync._resolve_target(sync.SyncOptions(client="claude-code"))
    assert goose == tmp_path / ".agents" / "skills"
    assert agy == tmp_path / ".gemini" / "config" / "skills"
    assert len({goose, agy, claude}) == 3  # three distinct global dirs/lockfiles
    for client in ("goose", "antigravity"):
        assert (
            sync._resolve_target(sync.SyncOptions(client=client, project=str(proj)))
            == proj / ".agents" / "skills"
        )
        assert sync._resolve_target(
            sync.SyncOptions(client=client, target=str(tmp_path / "x"))
        ) == (tmp_path / "x")
        with pytest.raises(sync.SyncError):
            sync._resolve_target(
                sync.SyncOptions(client=client, project=str(proj), target=str(tmp_path))
            )


@pytest.mark.parametrize(
    ("client", "rel"),
    [("goose", (".agents", "skills")), ("antigravity", (".gemini", "config", "skills"))],
)
def test_new_client_default_target_sync_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str, rel: tuple[str, ...]
) -> None:
    """Full cycle on the client's DEFAULT dir under a fake HOME: fresh sync,
    byte-idempotent rerun, update, prune — the same lockfile semantics as
    every other target, and the user's own skill dir is never touched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path.joinpath(*rel)
    own = target / "my-own-skill"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_bytes(b"# mine\n")
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n", "refs/n.md": "n\n"})
    catalog.add("tdd", "1.0.0", {"tdd.md": "# t\n"})
    o = sync.SyncOptions(client=client, server="http://aci.test:8000")

    assert sync.run_sync(o, transport=catalog.transport()) == 0
    assert (target / "debugging" / "SKILL.md").read_bytes() == b"# d\n"
    assert (target / "debugging" / "refs" / "n.md").read_bytes() == b"n\n"
    assert set(lock_of(target)["skills"]) == {"debugging", "tdd"}

    before = snapshot(target)
    assert sync.run_sync(o, transport=catalog.transport()) == 0
    assert snapshot(target) == before  # no-op rerun

    catalog.add("debugging", "1.1.0", {"debugging.md": "new"})
    catalog.remove("tdd")
    assert sync.run_sync(o, transport=catalog.transport()) == 0
    assert (target / "debugging" / "SKILL.md").read_bytes() == b"new"
    assert not (target / "debugging" / "refs").exists()
    assert not (target / "tdd").exists()
    assert set(lock_of(target)["skills"]) == {"debugging"}
    assert (own / "SKILL.md").read_bytes() == b"# mine\n"  # never touched


@pytest.mark.parametrize("client", ["goose", "antigravity"])
def test_new_client_project_target_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n"})
    proj = tmp_path / "proj"

    code = sync.main(
        ["--client", client, "--project", str(proj), "--server", "http://aci.test:8000"],
        transport=catalog.transport(),
    )

    assert code == 0
    assert (proj / ".agents" / "skills" / "debugging" / "SKILL.md").read_bytes() == b"# d\n"
    assert not (tmp_path / "home").exists()  # the global dir was not touched


@pytest.mark.parametrize("client", ["goose", "antigravity"])
def test_new_client_name_rule_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], client: str
) -> None:
    catalog = FakeCatalog()
    catalog.add("Bad_Name", "1.0.0", {"Bad_Name.md": "# x\n"})
    target = tmp_path / "skills"

    code = sync.run_sync(opts(target, client=client), transport=catalog.transport())

    assert code == 0
    assert (target / "Bad_Name" / "SKILL.md").exists()
    assert "will not discover it" in capsys.readouterr().out


def test_catalog_wire_contract_paths(tmp_path: Path) -> None:
    """Pin the exact catalog surface aci_sync consumes (ADR-005): the index
    at `<base>/index.json` and files at `<base>/<name>/<file>`, GET only."""
    catalog = FakeCatalog()
    catalog.add("debugging", "1.0.0", {"debugging.md": "# d\n", "refs/notes.md": "n\n"})
    target = tmp_path / "skills"

    assert sync.run_sync(opts(target), transport=catalog.transport()) == 0

    seen = {(request.method, request.url.path) for request in catalog.requests}
    assert seen == {
        ("GET", "/opencode/skills/index.json"),
        ("GET", "/opencode/skills/debugging/debugging.md"),
        ("GET", "/opencode/skills/debugging/refs/notes.md"),
    }


def test_client_name_rule_warning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A name the client cannot discover (OpenCode/Claude native rule:
    lowercase + single hyphens) is still synced, with a warning."""
    catalog = FakeCatalog()
    catalog.add("Bad_Name", "1.0.0", {"Bad_Name.md": "# x\n"})
    target = tmp_path / "skills"

    code = sync.run_sync(opts(target, client="opencode"), transport=catalog.transport())

    out = capsys.readouterr().out
    assert code == 0
    assert (target / "Bad_Name" / "SKILL.md").exists()
    assert "will not discover it" in out


def test_empty_index_creates_only_the_lock_marker(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    target = tmp_path / "skills"

    code = sync.run_sync(opts(target), transport=catalog.transport())

    assert code == 0
    assert list(target.iterdir()) == [target / sync.LOCK_NAME]
    assert lock_of(target)["skills"] == {}


# -- end-to-end over the REAL catalog router (no DB, no network) ----------------


class _ASGITransport(httpx.BaseTransport):
    """Minimal sync ASGI transport for httpx 0.28 (its own ASGITransport is
    async-only, and the installed starlette's testclient transport targets
    the httpx2 package). Runs the app in-process with asyncio.run."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        import asyncio

        body = request.read()
        received = {"done": False}
        sent: dict[str, Any] = {"status": 500, "headers": [], "chunks": bytearray()}

        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": request.method,
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?", 1)[0],
            "query_string": request.url.query,
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in request.headers.multi_items()
            ],
            "client": ("testclient", 50000),
            "server": (request.url.host, request.url.port or 80),
        }

        async def receive() -> dict[str, Any]:
            if received["done"]:
                return {"type": "http.disconnect"}
            received["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]
                sent["headers"] = message["headers"]
            elif message["type"] == "http.response.body":
                sent["chunks"] += message.get("body", b"")

        asyncio.run(self.app(scope, receive, send))
        headers = [
            (key.decode("latin-1"), value.decode("latin-1")) for key, value in sent["headers"]
        ]
        return httpx.Response(
            sent["status"], headers=headers, content=bytes(sent["chunks"]), request=request
        )


def test_end_to_end_against_the_real_catalog_router(tmp_path: Path) -> None:
    """The full chain over the REAL FastAPI routes + CatalogProjection with
    in-memory fakes (the same shape tests/unit/test_mcp_skills.py uses):
    aci_sync's URL construction, the index shape (`files` as plain path
    strings, the entry renamed to `<name>.md`), the ACI_API_TOKEN gate, and
    the entry-alias mapping back to a native `SKILL.md`."""
    from datetime import UTC, datetime

    from fastapi import FastAPI

    from aci.adapters.inbound.opencode.catalog import CatalogProjection, router
    from aci.config import Settings
    from aci.domain.capability.models import (
        ArtifactFile,
        CapabilityArtifact,
        CapabilityRelease,
        CapabilityVersion,
        SkillSpec,
    )

    skill_bytes = b"---\nname: debugging\ndescription: d\n---\n\nbody\n"
    guide_bytes = b"# guide\n"
    skill_sha = hashlib.sha256(skill_bytes).hexdigest()
    guide_sha = hashlib.sha256(guide_bytes).hexdigest()
    now = datetime(2026, 9, 28, tzinfo=UTC)

    class _Releases:
        def __init__(self) -> None:
            self.rows = [
                CapabilityRelease(
                    capability_id="debugging",
                    version="1.0.0",
                    channel="production",
                    status="active",
                )
            ]

        def list_channel(self, channel: str, *, status: str | None = None) -> list[Any]:
            return [
                row
                for row in self.rows
                if row.channel == channel and (status is None or row.status == status)
            ]

        def get_release(self, capability_id: str, channel: str) -> Any:
            return next(
                (r for r in self.rows if r.capability_id == capability_id and r.channel == channel),
                None,
            )

    class _Capabilities:
        def __init__(self) -> None:
            self.versions = {
                ("debugging", "1.0.0"): CapabilityVersion(
                    capability_id="debugging",
                    version="1.0.0",
                    kind="skill",
                    content_digest=f"sha256:{'a' * 64}",
                    created_at=now,
                    spec=SkillSpec(),
                )
            }

        def get_version(self, capability_id: str, version: str) -> Any:
            return self.versions.get((capability_id, version))

        def get_versions(self, pairs: list[tuple[str, str]]) -> list[Any]:
            return [v for p, v in self.versions.items() if p in pairs]

    class _Artifacts:
        def __init__(self) -> None:
            self.rows = {
                ("debugging", "1.0.0"): CapabilityArtifact(
                    capability_id="debugging",
                    version="1.0.0",
                    package_digest=f"sha256:{'b' * 64}",
                    manifest={"entrypoint": "SKILL.md"},
                    files=[
                        ArtifactFile(
                            path="SKILL.md", size_bytes=len(skill_bytes), sha256=skill_sha
                        ),
                        ArtifactFile(
                            path="references/guide.md",
                            size_bytes=len(guide_bytes),
                            sha256=guide_sha,
                        ),
                    ],
                )
            }

        def get_artifact(self, capability_id: str, version: str) -> Any:
            return self.rows.get((capability_id, version))

        def get_artifacts(self, pairs: list[tuple[str, str]]) -> list[Any]:
            return [a for p, a in self.rows.items() if p in pairs]

    class _Objects:
        def __init__(self, blobs: dict[str, bytes]) -> None:
            self.blobs = blobs

        def get(self, key: str) -> bytes | None:
            return self.blobs.get(key)

    class _Container:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

    app = FastAPI()
    app.state.container = _Container(Settings(api_token="tok-e2e"))
    app.state.catalog = CatalogProjection(
        _Releases(),
        _Capabilities(),
        _Artifacts(),
        _Objects({skill_sha: skill_bytes, guide_sha: guide_bytes}),
    )
    app.include_router(router)
    transport = _ASGITransport(app)
    target = tmp_path / "skills"

    # Without the token the real gate refuses (401) and nothing is written.
    code = sync.run_sync(opts(target), transport=transport)
    assert code == 1
    assert not target.exists()

    # With the token the sync lands the native layout.
    code = sync.run_sync(opts(target, token="tok-e2e"), transport=transport)
    assert code == 0
    assert (target / "debugging" / "SKILL.md").read_bytes() == skill_bytes
    assert (target / "debugging" / "references" / "guide.md").read_bytes() == guide_bytes
    entry = lock_of(target)["skills"]["debugging"]
    assert entry["version"] == "1.0.0"
    assert entry["files"] == {"SKILL.md": skill_sha, "references/guide.md": guide_sha}
