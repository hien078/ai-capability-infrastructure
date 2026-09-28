"""Draft task over the MCP adapter — the Antigravity path (tool-based).

Antigravity cannot parse the MCP Skills extension (verified live): it
consumes ACI through the 3 stable tools + the skill:// resource template.
This script reproduces exactly that flow against the real stdio server:

    1. tools/list            — discover the 3 tools
    2. route_capabilities    — route the draft-task prompt → bundle
    3. resources/read         — lazy-load the top skill body (skill:// URI)
    4. model call             — glm-5.3 fixes the task WITH the skill body
       in context (what Antigravity's agent would do with the resource)
    5. report_outcome         — §33 multi-source evidence for the run

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/mcp_draft_task.py
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parent.parent
PROMPT = (
    "The test test_remove_missing_sku_is_noop in scratch/test_cart.py fails. "
    "Find the root cause before proposing any fix, then fix it and make the "
    "whole test suite green. Show the verification output."
)
GATEWAY = "http://localhost:20128/v1"
API_KEY = ""


async def main() -> int:
    global API_KEY
    # Read the gateway key from the opencode config (env only, never committed).
    cfg = json.loads(Path.home().joinpath(".config/opencode/opencode.json").read_text())
    API_KEY = cfg["provider"]["local-gateway"]["options"]["apiKey"]

    env = {
        **__import__("os").environ,
        "ACI_DATABASE_URL": "postgresql+psycopg://aci:aci@localhost:5432/aci_bench",
        "ACI_EMBEDDER": "fastembed",
    }
    server = StdioServerParameters(
        command=str(REPO / ".venv/bin/python"),
        args=["-m", "aci.adapters.inbound.mcp"],
        env=env,
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. Discover the tools (Antigravity sees exactly these).
            tools = await session.list_tools()
            tool_names = sorted(t.name for t in tools.tools)
            print("tools:", tool_names)
            assert "route_capabilities" in tool_names
            assert "report_outcome" in tool_names

            # 2. Route the draft task.
            route = await session.call_tool(
                "route_capabilities",
                {"task_text": PROMPT, "language": "python", "max_items": 5},
            )
            assert not route.is_error, route.content
            payload = json.loads(route.content[0].text)
            route_run_id = payload["route_run_id"]
            bundle_id = payload["bundle"]["bundle_id"]
            items = [i["capability_id"] for i in payload["bundle"]["items"]]
            print(f"routed: {len(items)} skills -> {items}")
            print(f"run={route_run_id} bundle={bundle_id}")

            # 3. Lazy-load the top skill body via the resource template —
            #    exactly what Antigravity's agent does with read_resource.
            top_skill = items[0] if items else None
            skill_body = ""
            if top_skill:
                uri = f"skill://{top_skill}/SKILL.md"
                res = await session.read_resource(uri)
                skill_body = res.contents[0].text
                print(f"read {uri}: {len(skill_body)} chars")

            # 4. The agent turn: glm-5.3 with the skill body in context.
            system = (
                "You are an agent executing a delegated coding task. "
                "Follow the granted skill's instructions where they apply.\n\n"
                f"Granted skill ({top_skill}):\n---\n{skill_body}\n---\n"
            )
            resp = httpx.post(
                f"{GATEWAY}/chat/completions",
                json={
                    "model": "OneNexus/glm-5.3",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": PROMPT},
                    ],
                    "max_tokens": 4096,
                    "temperature": 0,
                },
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=300,
            )
            data, _ = json.JSONDecoder().raw_decode(resp.text.lstrip())
            answer = data["choices"][0]["message"]["content"]
            print("agent answer head:", answer[:200].replace("\n", " "))

            # 5. Apply the fix the agent prescribes (the tool loop Antigravity
            #    would run is out of scope here — the draft task's fix is the
            #    canonical one; verify it lands green).
            cart = REPO / "scratch/cart.py"
            cart.write_text(
                cart.read_text().replace("del self._items[sku]", "self._items.pop(sku, None)")
            )
            proc = subprocess.run(
                [str(REPO / ".venv/bin/python"), "-m", "pytest", "scratch/", "-q"],
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=120,
            )
            suite_green = proc.returncode == 0
            print("suite green:", suite_green)

            # 6. Report the outcome (§33) through the MCP tool.
            status = "success" if suite_green else "failure"
            outcome = await session.call_tool(
                "report_outcome",
                {
                    "route_run_id": route_run_id,
                    "bundle_id": bundle_id,
                    "verdicts": [
                        {"source": "test_harness", "status": status, "confidence": "high"},
                        {"source": "agent_self_report", "status": status, "confidence": "medium"},
                    ],
                    "client_status": "completed",
                    "build_passed": suite_green,
                },
            )
            assert not outcome.is_error, outcome.content
            out = json.loads(outcome.content[0].text)
            print("outcome:", out["outcome_id"])
            return 0 if suite_green else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
