"""get_skill: catalog alias mapping, §39 digest verification, fail-closed."""

from __future__ import annotations

import hashlib

import httpx
import pytest
from aci_client import ACIError, SkillIntegrityError
from aci_wire import (
    BIN_BYTES,
    BIN_SHA,
    GUIDE_MD,
    GUIDE_SHA,
    SKILL_MD,
    SKILL_SHA,
    WIRE,
    make_client,
    skill_catalog_handler,
)


def test_get_skill_end_to_end() -> None:
    """Alias mapping, nested paths, digest verification, UTF-8 decode."""
    client = make_client(skill_catalog_handler)
    skill = client.get_skill("debugging")
    assert skill.capability_id == "debugging"
    assert skill.version == "1.0.0"
    assert skill.package_digest == "sha256:" + "c" * 64
    assert skill.skill_md == SKILL_MD
    # The catalog alias <id>.md maps back to the canonical SKILL.md path.
    entry = skill.files_by_path["SKILL.md"]
    assert entry.sha256 == SKILL_SHA
    assert entry.text == SKILL_MD
    guide = skill.files_by_path["references/guide.md"]
    assert guide.sha256 == GUIDE_SHA
    assert guide.text == GUIDE_MD
    assert [f.path for f in skill.files] == ["SKILL.md", "references/guide.md"]


def test_get_skill_explicit_version_resolves_same_content() -> None:
    client = make_client(skill_catalog_handler)
    skill = client.get_skill("debugging", version="1.0.0")
    assert skill.version == "1.0.0"
    assert skill.skill_md == SKILL_MD


def test_get_skill_version_mismatch_fails_closed() -> None:
    """The catalog is a projection of the ACTIVE release: a request for any
    other version can never be served — refuse it instead of serving the
    active bytes under the wrong label."""
    client = make_client(skill_catalog_handler)
    with pytest.raises(ACIError) as exc_info:
        client.get_skill("debugging", version="2.0.0")
    assert exc_info.value.code == "CAPABILITY_VERSION_NOT_FOUND"
    assert exc_info.value.status_code == 404


def test_get_skill_unknown_skill() -> None:
    client = make_client(skill_catalog_handler)
    with pytest.raises(ACIError) as exc_info:
        client.get_skill("nope-never")
    assert exc_info.value.code == "CAPABILITY_NOT_FOUND"


def test_get_skill_tampered_blob_raises_integrity_error() -> None:
    """§39 supply chain: bytes that fail their sha256 never reach the caller."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/opencode/skills/debugging/debugging.md":
            # Tampered: same file, one byte flipped.
            return httpx.Response(
                200, content=SKILL_MD.encode() + b"x", headers={"content-type": "text/markdown"}
            )
        return skill_catalog_handler(request)

    client = make_client(handler)
    with pytest.raises(SkillIntegrityError) as exc_info:
        client.get_skill("debugging")
    assert exc_info.value.code == "ARTIFACT_INTEGRITY_ERROR"
    assert exc_info.value.status_code is None


def test_get_skill_tampered_reference_file_raises() -> None:
    """Every advertised file is verified, not just the entry file."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/opencode/skills/debugging/references/guide.md":
            return httpx.Response(
                200, content=b"tampered", headers={"content-type": "text/markdown"}
            )
        return skill_catalog_handler(request)

    with pytest.raises(SkillIntegrityError):
        make_client(handler).get_skill("debugging")


def test_get_skill_advertised_file_missing_from_manifest_fails_closed() -> None:
    """A catalog file the immutable manifest does not know = server
    inconsistency: never serve it unverified."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/opencode/skills/index.json":
            index = {
                "skills": [
                    {
                        "name": "debugging",
                        "version": "1.0.0",
                        "files": ["debugging.md", "mystery.bin"],
                    }
                ]
            }
            return httpx.Response(200, json=index)
        return skill_catalog_handler(request)

    with pytest.raises(ACIError) as exc_info:
        make_client(handler).get_skill("debugging")
    assert exc_info.value.code == "ARTIFACT_INTEGRITY_ERROR"


def test_get_skill_missing_artifact_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/capabilities/debugging/versions/1.0.0":
            return httpx.Response(
                200, json={"version": WIRE["resolved"]["version"], "artifact": None}
            )
        return skill_catalog_handler(request)

    with pytest.raises(ACIError) as exc_info:
        make_client(handler).get_skill("debugging")
    assert exc_info.value.code == "CAPABILITY_NOT_FOUND"


def test_get_skill_binary_file_kept_as_bytes() -> None:
    """Non-UTF-8 files verify fine and surface as text=None (binary)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/opencode/skills/index.json":
            index = {
                "skills": [
                    {
                        "name": "debugging",
                        "version": "1.0.0",
                        "files": ["debugging.md", "references/guide.md", "asset.bin"],
                    }
                ]
            }
            return httpx.Response(200, json=index)
        if request.url.path == "/v1/capabilities/debugging/versions/1.0.0":
            resolved = {
                "version": WIRE["resolved"]["version"],
                "artifact": {
                    **WIRE["resolved"]["artifact"],
                    "files": [
                        *WIRE["resolved"]["artifact"]["files"],
                        {"path": "asset.bin", "sha256": BIN_SHA, "size_bytes": len(BIN_BYTES)},
                    ],
                },
            }
            return httpx.Response(200, json=resolved)
        if request.url.path == "/opencode/skills/debugging/asset.bin":
            return httpx.Response(
                200, content=BIN_BYTES, headers={"content-type": "application/octet-stream"}
            )
        return skill_catalog_handler(request)

    skill = make_client(handler).get_skill("debugging")
    binary = skill.files_by_path["asset.bin"]
    assert binary.text is None
    assert binary.sha256 == hashlib.sha256(BIN_BYTES).hexdigest()


def test_get_skill_shadowed_entry_still_verifies() -> None:
    """A package that also ships a real <capability_id>.md has it shadowed
    by the entry alias in the catalog namespace (server rule); the SDK
    verifies the ADVERTISED files, so the shadowed copy is simply absent —
    never served unverified."""
    # The canned manifest already covers SKILL.md + references/guide.md;
    # a real <id>.md in the manifest would be shadowed by the alias, so the
    # index never advertises it. Nothing to do but prove the normal path.
    skill = make_client(skill_catalog_handler).get_skill("debugging")
    assert "debugging.md" not in skill.files_by_path
    assert skill.files_by_path["SKILL.md"].sha256 == SKILL_SHA


def test_get_skill_no_skill_md_fails_closed() -> None:
    """A package advertising no entry file cannot be a skill — refuse it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/opencode/skills/index.json":
            index = {
                "skills": [
                    {"name": "debugging", "version": "1.0.0", "files": ["references/guide.md"]}
                ]
            }
            return httpx.Response(200, json=index)
        return skill_catalog_handler(request)

    with pytest.raises(ACIError) as exc_info:
        make_client(handler).get_skill("debugging")
    assert exc_info.value.code == "SKILL_PACKAGE_INVALID"


def test_get_skill_non_utf8_skill_md_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/opencode/skills/debugging/debugging.md":
            return httpx.Response(
                200, content=b"\xff\xfe\x00bad", headers={"content-type": "text/markdown"}
            )
        return skill_catalog_handler(request)

    # The manifest sha will not match either — integrity fires first; both
    # codes are acceptable fail-closed outcomes, assert it is one of them.
    with pytest.raises(ACIError) as exc_info:
        make_client(handler).get_skill("debugging")
    assert exc_info.value.code in {"ARTIFACT_INTEGRITY_ERROR", "SKILL_PACKAGE_INVALID"}


def test_get_skill_quotes_unsafe_ids() -> None:
    """A hostile capability_id must never traverse the URL path — it stays
    one quoted segment even when a (compromised) index lists it."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # raw_path: the bytes actually on the wire (still percent-encoded).
        seen.append(request.url.raw_path.decode())
        if request.url.path == "/opencode/skills/index.json":
            index = {
                "skills": [
                    {
                        "name": "../../etc/passwd",
                        "version": "1.0.0",
                        "files": ["../../etc/passwd.md"],
                    }
                ]
            }
            return httpx.Response(200, json=index)
        return httpx.Response(
            404, json={"error": {"code": "CAPABILITY_NOT_FOUND", "message": "no"}}
        )

    with pytest.raises(ACIError):
        make_client(handler).get_skill("../../etc/passwd")
    # The id reached the wire as ONE quoted segment — no raw traversal.
    resolve_reqs = [p for p in seen if p.startswith("/v1/capabilities/")]
    assert resolve_reqs, seen
    assert all("/etc/passwd" not in p for p in seen)
    assert all("%2F" in p for p in resolve_reqs)
