"""Canned wire payloads + MockTransport handlers for the aci-client tests.

No network, no DB: every handler here mirrors the server's actual wire
shapes (pinned against the live app by test_contract_integration.py).
Importable as a plain module (pytest puts this directory on sys.path).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import httpx
from aci_client import ACIClient, AsyncACIClient

BASE = "http://testserver"

SKILL_MD = "---\nname: debugging\ndescription: debug things\n---\n\nFind the root cause first.\n"
GUIDE_MD = "# guide\n1. reproduce\n2. isolate\n"
BIN_BYTES = b"\x00\x01\x02\xff\xfe"
SKILL_SHA = hashlib.sha256(SKILL_MD.encode()).hexdigest()
GUIDE_SHA = hashlib.sha256(GUIDE_MD.encode()).hexdigest()
BIN_SHA = hashlib.sha256(BIN_BYTES).hexdigest()

#: Canned wire payloads (server shapes, verbatim field sets).
WIRE: dict[str, Any] = {
    "route_result": {
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
    },
    "index": {
        "skills": [
            {
                "name": "debugging",
                "version": "1.0.0",
                "files": ["debugging.md", "references/guide.md"],
            }
        ]
    },
    "resolved": {
        "version": {
            "capability_id": "debugging",
            "version": "1.0.0",
            "kind": "skill",
            "content_digest": "sha256:" + "b" * 64,
            "display_name": "Debugging",
            "description": "systematic debugging",
            # The server's full model carries a spec union the SDK ignores
            # (CapabilityVersionSummary is extra="ignore").
            "spec": {"kind": "skill", "entry": "SKILL.md"},
        },
        "artifact": {
            "capability_id": "debugging",
            "version": "1.0.0",
            "package_digest": "sha256:" + "c" * 64,
            "manifest": {},
            "files": [
                {"path": "SKILL.md", "sha256": SKILL_SHA, "size_bytes": len(SKILL_MD)},
                {"path": "references/guide.md", "sha256": GUIDE_SHA, "size_bytes": len(GUIDE_MD)},
            ],
        },
    },
    "outcome": {
        "outcome_id": "out_1",
        "route_run_id": "rr_1",
        "bundle_id": "bun_1",
        "received_at": "2026-10-03T00:00:01Z",
        "verdicts": [{"source": "test_harness", "status": "success", "confidence": "high"}],
        "tests_before": {"failed": 1},
        "tests_after": {"failed": 0},
        "latency_ms": 900,
        "client_status": "completed",
        "lint_passed": True,
        "build_passed": True,
        "changed_files": 2,
        "tool_calls": 3,
        "human_corrected": False,
        "input_tokens": 1000,
        "output_tokens": 200,
        "estimated_usd": 0.01,
    },
    "agent_run": {
        "run_id": "run_1",
        "status": "succeeded",
        "stop_reason": "SUCCESS",
        "detail_code": None,
        "summary": "fixed the bug",
        "evidence_verdict": "PASS",
        "checks": ["command_passed_after_last_change"],
        "evidence_refs": ["ev_1"],
        "artifacts": [],
        "turns": 3,
        "tool_calls": 2,
        "wall_time_seconds": 12.5,
        "approval_id": None,
    },
}

#: The p-agentrun-api additive read model (usage + changes present).
WIRE["agent_run_rich"] = {
    **WIRE["agent_run"],
    "usage": {
        "model_input_tokens": 5000,
        "model_output_tokens": 800,
        "turns": 3,
        "tool_calls": 2,
        "wall_seconds": 12.5,
    },
    "changes": {
        "files": [
            {
                "path": "src/app.py",
                "status": "modified",
                "sha256_before": "d" * 64,
                "sha256_after": "e" * 64,
                "size_after": 120,
            }
        ],
        "diff": "--- a/src/app.py\n+++ b/src/app.py\n",
        "truncated": False,
    },
}

Handler = Callable[[httpx.Request], httpx.Response]


def preload_flow_handler(request: httpx.Request) -> httpx.Response:
    """The full preload-example flow: route + catalog + resolve + files."""
    if request.url.path == "/v1/routes":
        return httpx.Response(201, json=WIRE["route_result"])
    return skill_catalog_handler(request)


def skill_catalog_handler(request: httpx.Request) -> httpx.Response:
    """Index + resolve + catalog files for the canned `debugging` skill."""
    path = request.url.path
    if path == "/opencode/skills/index.json":
        return httpx.Response(200, json=WIRE["index"])
    if path == "/v1/capabilities/debugging/versions/1.0.0":
        return httpx.Response(200, json=WIRE["resolved"])
    if path == "/opencode/skills/debugging/debugging.md":
        return httpx.Response(
            200, content=SKILL_MD.encode(), headers={"content-type": "text/markdown"}
        )
    if path == "/opencode/skills/debugging/references/guide.md":
        return httpx.Response(
            200, content=GUIDE_MD.encode(), headers={"content-type": "text/markdown"}
        )
    return httpx.Response(
        404, json={"error": {"code": "CAPABILITY_NOT_FOUND", "message": f"no {path}"}}
    )


def make_client(handler: Handler, **kwargs: Any) -> ACIClient:
    return ACIClient(BASE, transport=httpx.MockTransport(handler), **kwargs)


def make_async_client(handler: Handler, **kwargs: Any) -> AsyncACIClient:
    return AsyncACIClient(BASE, transport=httpx.MockTransport(handler), **kwargs)
