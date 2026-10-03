"""AsyncACIClient: the same wire surface, awaited (no pytest-asyncio needed —
each test drives the loop with asyncio.run)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from aci_client import ACIConnectionError, ACIError, OutcomeVerdict
from aci_wire import (
    GUIDE_MD,
    SKILL_MD,
    WIRE,
    make_async_client,
    skill_catalog_handler,
)


def test_async_route_and_search() -> None:
    async def scenario() -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path == "/v1/routes":
                return httpx.Response(201, json=WIRE["route_result"])
            return httpx.Response(200, json=[])

        async with make_async_client(handler, token="tok") as client:
            result = await client.route("fix the bug", max_items=2)
            assert result.route_run_id == "rr_1"
            assert result.bundle.items[0].capability_id == "debugging"
            assert seen[0].headers["Authorization"] == "Bearer tok"

            hits = await client.search("debug")
            assert hits == []

    asyncio.run(scenario())


def test_async_get_skill_verifies_digests() -> None:
    async def scenario() -> None:
        async with make_async_client(skill_catalog_handler) as client:
            skill = await client.get_skill("debugging")
        assert skill.skill_md == SKILL_MD
        assert skill.files_by_path["references/guide.md"].text == GUIDE_MD

    asyncio.run(scenario())


def test_async_get_skill_integrity_error() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/opencode/skills/debugging/debugging.md":
                return httpx.Response(
                    200, content=b"tampered", headers={"content-type": "text/markdown"}
                )
            return skill_catalog_handler(request)

        async with make_async_client(handler) as client:
            with pytest.raises(ACIError) as exc_info:
                await client.get_skill("debugging")
        assert exc_info.value.code == "ARTIFACT_INTEGRITY_ERROR"

    asyncio.run(scenario())


def test_async_report_outcome_and_agent_runs() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/outcomes":
                return httpx.Response(201, json=WIRE["outcome"])
            if request.url.path == "/v1/agent-runs":
                return httpx.Response(201, json=WIRE["agent_run_rich"])
            if request.url.path == "/v1/agent-runs/run_1":
                return httpx.Response(200, json=WIRE["agent_run_rich"])
            if request.url.path == "/v1/agent-runs/run_1/cancel":
                return httpx.Response(200, json={"cancelled": False})
            return httpx.Response(404, json={"error": {"code": "X", "message": "m"}})

        async with make_async_client(handler) as client:
            evidence = await client.report_outcome(
                "rr_1",
                "bun_1",
                [OutcomeVerdict(source="client_report", status="success", confidence="medium")],
            )
            assert evidence.outcome_id == "out_1"

            run = await client.run_agent("objective", workspace="w")
            assert run.usage is not None
            assert run.usage.model_input_tokens == 5000

            run = await client.get_run("run_1")
            assert run.changes is not None
            assert run.changes.files[0].path == "src/app.py"

            assert await client.cancel_run("run_1") is False

    asyncio.run(scenario())


def test_async_post_not_retried() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            raise httpx.ConnectError("refused", request=request)

        async with make_async_client(handler) as client:
            with pytest.raises(ACIConnectionError):
                await client.route("t")
        assert len(calls) == 1

    asyncio.run(scenario())


def test_async_get_retried() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if len(calls) < 2:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, json=WIRE["index"])

        async with make_async_client(handler) as client:
            response = await client._request("GET", "/opencode/skills/index.json", retry_get=True)
        assert response.status_code == 200
        assert len(calls) == 2

    asyncio.run(scenario())
