"""Security boundaries of the aci-client SDK (§61 extension, platform-surface batch).

The SDK is a new client of the existing wire surfaces; these cases pin the
CLIENT-side boundaries the shared contract requires: token hygiene (the
bearer token reaches ONLY the Authorization header — never a URL, a body,
an error message, or an exception string), fail-closed skill content
(§39: tampered bytes never reach the caller), and no absolute-path or
traversal leakage through the read models.

Self-contained + MockTransport only — no network, no DB (the security
conftest imports live-DB fixtures; these tests do not request them).
"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from aci_client import (
    ACIClient,
    ACIConnectionError,
    ACIError,
    AgentRun,
    OutcomeVerdict,
    SkillContent,
    SkillIntegrityError,
)
from pydantic import ValidationError

TOKEN = "top-secret-bearer-token"
SKILL_MD = "---\nname: debugging\n---\n\nFind the root cause first.\n"
SKILL_SHA = hashlib.sha256(SKILL_MD.encode()).hexdigest()

ROUTE_RESULT = {
    "route_run_id": "rr_1",
    "bundle": {
        "bundle_id": "bun_1",
        "route_run_id": "rr_1",
        "created_at": "2026-10-03T00:00:00Z",
        "items": [
            {
                "capability_id": "debugging",
                "version": "1.0.0",
                "digest": "sha256:" + "a" * 64,
                "kind": "skill",
                "role": "primary",
                "load_mode": "lazy",
                "reason_code": "",
            }
        ],
        "execution_order": ["debugging"],
        "budget": {"max_items": 5, "max_context_tokens": 8000},
        "policy_snapshot_id": None,
    },
}
INDEX = {"skills": [{"name": "debugging", "version": "1.0.0", "files": ["debugging.md"]}]}
RESOLVED = {
    "version": {
        "capability_id": "debugging",
        "version": "1.0.0",
        "kind": "skill",
        "content_digest": "sha256:" + "b" * 64,
    },
    "artifact": {
        "capability_id": "debugging",
        "version": "1.0.0",
        "package_digest": "sha256:" + "c" * 64,
        "manifest": {},
        "files": [{"path": "SKILL.md", "sha256": SKILL_SHA, "size_bytes": len(SKILL_MD)}],
    },
}
OUTCOME = {
    "outcome_id": "out_1",
    "route_run_id": "rr_1",
    "bundle_id": "bun_1",
    "received_at": "2026-10-03T00:00:01Z",
    "verdicts": [{"source": "test_harness", "status": "success", "confidence": "high"}],
    "tests_before": {},
    "tests_after": {},
}
AGENT_RUN_RICH = {
    "run_id": "run_1",
    "status": "succeeded",
    "stop_reason": "SUCCESS",
    "detail_code": None,
    "summary": "",
    "evidence_verdict": None,
    "checks": [],
    "evidence_refs": [],
    "artifacts": [],
    "turns": 1,
    "tool_calls": 0,
    "wall_time_seconds": 1.0,
    "approval_id": None,
    "usage": {
        "model_input_tokens": 5,
        "model_output_tokens": 1,
        "turns": 1,
        "tool_calls": 0,
        "wall_seconds": 1.0,
    },
    "changes": {
        "files": [
            {
                "path": "src/app.py",
                "status": "modified",
                "sha256_before": None,
                "sha256_after": None,
                "size_after": 1,
            }
        ],
        "diff": None,
        "truncated": False,
    },
}


def catalog_handler(request: httpx.Request) -> httpx.Response:
    """Index + resolve + the (verifiable) entry file."""
    path = request.url.path
    if path == "/opencode/skills/index.json":
        return httpx.Response(200, json=INDEX)
    if path == "/v1/capabilities/debugging/versions/1.0.0":
        return httpx.Response(200, json=RESOLVED)
    if path == "/opencode/skills/debugging/debugging.md":
        return httpx.Response(200, content=SKILL_MD.encode(), headers={"content-type": "text/m"})
    return httpx.Response(404, json={"error": {"code": "CAPABILITY_NOT_FOUND", "message": "no"}})


def make_client(handler: object, **kwargs: object) -> ACIClient:
    return ACIClient(
        "http://testserver",
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]
    )


def test_token_travels_only_in_the_authorization_header() -> None:
    """The token must never reach a URL, a query, or a request body."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/routes":
            return httpx.Response(201, json=ROUTE_RESULT)
        if request.url.path == "/v1/capabilities/search":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json=OUTCOME)

    client = make_client(handler, token=TOKEN)
    client.route("fix the thing")
    client.search("debug")
    client.report_outcome(
        "rr_1", "bun_1", [OutcomeVerdict(source="client_report", status="success")]
    )
    assert seen, "no requests were made"
    for request in seen:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(request.url)
        assert TOKEN not in (request.read().decode() or "")


def test_token_never_in_error_messages_or_exceptions() -> None:
    """Every error path must stay token-free: §45 bodies, gate 401s,
    validation 422s, and transport failures."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/routes":
            return httpx.Response(
                403, json={"error": {"code": "PERMISSION_DENIED", "message": "no"}}
            )
        if request.url.path == "/v1/capabilities/search":
            return httpx.Response(401, json={"detail": "invalid or missing token"})
        # /v1/outcomes and everything else: transport failure.
        raise httpx.ConnectError("refused", request=request)

    client = make_client(handler, token=TOKEN)
    for action in (lambda: client.route("t"), lambda: client.search("q")):
        with pytest.raises(ACIError) as exc_info:
            action()
        assert TOKEN not in str(exc_info.value)
        assert TOKEN not in exc_info.value.message
    with pytest.raises(ACIConnectionError) as conn_info:
        client.report_outcome("r", "b", [OutcomeVerdict(source="client_report", status="unknown")])
    assert TOKEN not in str(conn_info.value)


def test_client_repr_and_str_carry_no_token() -> None:
    client = make_client(lambda request: httpx.Response(200, json=[]), token=TOKEN)
    for rendered in (repr(client), str(client)):
        assert TOKEN not in rendered


def test_tampered_skill_bytes_never_reach_the_caller() -> None:
    """§39 supply chain: a sha256 mismatch raises BEFORE any content is
    returned — the tampered text is unreachable through the SDK."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/opencode/skills/debugging/debugging.md":
            tampered = SKILL_MD.replace("root cause", "ROOT CAUSE")
            return httpx.Response(
                200, content=tampered.encode(), headers={"content-type": "text/m"}
            )
        return catalog_handler(request)

    client = make_client(handler)
    with pytest.raises(SkillIntegrityError) as exc_info:
        client.get_skill("debugging")
    assert exc_info.value.code == "ARTIFACT_INTEGRITY_ERROR"
    # The tampered content did not leak through the error either.
    assert "ROOT CAUSE" not in str(exc_info.value)


def test_skill_paths_stay_canonical_and_relative() -> None:
    """SkillContent carries only canonical, relative artifact paths —
    never absolute paths, never traversal (server-enforced, re-checked)."""
    skill = make_client(catalog_handler).get_skill("debugging")
    assert skill.capability_id == "debugging"
    assert skill.skill_md == SKILL_MD
    for file in skill.files:
        assert not file.path.startswith("/")
        assert "\\" not in file.path
        assert ".." not in file.path.split("/")


def test_run_changes_rejects_absolute_or_traversal_paths() -> None:
    """The p-agentrun-api read model is workspace-relative POSIX: a hostile
    or inconsistent server response fails closed at parse time."""
    for hostile in ("/etc/passwd", "../../secrets", "src\\backslash.py"):
        payload = {
            **AGENT_RUN_RICH,
            "changes": {
                "files": [
                    {
                        "path": hostile,
                        "status": "modified",
                        "sha256_before": None,
                        "sha256_after": None,
                        "size_after": 1,
                    }
                ],
                "diff": None,
                "truncated": False,
            },
        }
        with pytest.raises(ValidationError):
            AgentRun.model_validate(payload)


def test_run_changes_parses_clean_relative_paths() -> None:
    run = AgentRun.model_validate(AGENT_RUN_RICH)
    assert run.changes is not None
    assert run.usage is not None
    assert run.changes.files[0].path == "src/app.py"


def test_outcome_payload_carries_only_caller_given_fields() -> None:
    """No ambient env scraping, no extra keys: the §33 envelope is exactly
    what the caller passed (the server is the only other party involved)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=OUTCOME)

    make_client(handler, token=TOKEN).report_outcome(
        "rr_1",
        "bun_1",
        [OutcomeVerdict(source="test_harness", status="success", confidence="high")],
        tests_after={"passed": 3},
    )
    payload = json.loads(seen[0].read())
    assert set(payload) == {
        "route_run_id",
        "bundle_id",
        "verdicts",
        "tests_before",
        "tests_after",
        "latency_ms",
        "client_status",
        "lint_passed",
        "build_passed",
        "changed_files",
        "tool_calls",
        "human_corrected",
        "input_tokens",
        "output_tokens",
        "estimated_usd",
    }
    assert payload["tests_after"] == {"passed": 3}
    assert payload["tests_before"] == {}


def test_skill_content_model_is_frozen() -> None:
    """Read models are immutable projections — tampering with a parsed
    SkillContent cannot silently redirect a caller."""
    skill = SkillContent(
        capability_id="x",
        version="1.0.0",
        package_digest="sha256:" + "0" * 64,
        skill_md="body",
        files=[],
    )
    with pytest.raises(Exception, match="[Ff]rozen"):
        skill.skill_md = "forged"  # type: ignore[misc]
