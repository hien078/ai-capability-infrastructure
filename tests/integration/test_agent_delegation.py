"""Full delegated-task loop against the live DB (V3 §56, §56.1).

End to end through the REAL surfaces: registry rows (agent + granted skill),
the profile-driven runtime, the OpenAICompatExecutor with a mock model
endpoint, and the A2A gateway mounted by create_app. Proves the whole V3
chain: SendMessage -> lifecycle -> grants resolved against active production
releases -> skill body loaded from the object store into the model prompt ->
terminal state -> GetTask history.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest.wiring import Container
from aci.adapters.outbound.model_provider.executor import OpenAICompatExecutor
from aci.application.delegate_task import ProfileDrivenAgentRuntime
from aci.config import Settings
from aci.domain.agent.models import AgentProfile
from aci.domain.capability.models import (
    AgentSpec,
    ArtifactFile,
    Capability,
    CapabilityArtifact,
    CapabilityRelease,
    CapabilityVersion,
    SkillSpec,
)
from aci.main import create_app

pytestmark = pytest.mark.integration

SKILL_BODY = b"# skill\n\nAlways verify before declaring done.\n"


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _register_skill(container: Container, cid: str, now: datetime) -> None:
    """A production skill release the profile's SkillPolicy can pin."""
    sha = hashlib.sha256(SKILL_BODY).hexdigest()
    container.objects.put(sha, SKILL_BODY)
    container.capabilities.create_capability(Capability(id=cid, kind="skill", created_at=now))
    container.capabilities.create_version(
        CapabilityVersion(
            capability_id=cid,
            version="1.0.0",
            kind="skill",
            content_digest=f"sha256:{sha}",
            created_at=now,
            spec=SkillSpec(),
        )
    )
    container.artifacts.put_artifact(
        CapabilityArtifact(
            capability_id=cid,
            version="1.0.0",
            package_digest=f"sha256:{sha}",
            files=[ArtifactFile(path="SKILL.md", sha256=sha, size_bytes=len(SKILL_BODY))],
        )
    )
    container.releases.set_release(
        CapabilityRelease(capability_id=cid, version="1.0.0", channel="production")
    )


def _register_agent(container: Container, cid: str, now: datetime) -> None:
    container.capabilities.create_capability(Capability(id=cid, kind="agent", created_at=now))
    container.capabilities.create_version(
        CapabilityVersion(
            capability_id=cid,
            version="1.0.0",
            kind="agent",
            content_digest=f"sha256:{hashlib.sha256(b'agent').hexdigest()}",
            created_at=now,
            spec=AgentSpec(),
        )
    )
    container.releases.set_release(
        CapabilityRelease(capability_id=cid, version="1.0.0", channel="production")
    )


def _wire_profile(
    container: Container, agent_cid: str, skill_cid: str | None, handler
) -> AgentProfile:
    profile = AgentProfile.model_validate(
        {
            "profile_id": f"{agent_cid}-p",
            "version": "1",
            "model_profile": "test-model",
            "skill_policy": {"required": [skill_cid] if skill_cid else []},
            "budget": {"max_tokens": 256, "max_wall_time_seconds": 30},
            "execution_policy": {"side_effect_class": "read_only"},
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    container.agent_profiles[profile.profile_id] = profile
    container.agent_runtime = ProfileDrivenAgentRuntime(
        container.tasks,
        container.releases,
        OpenAICompatExecutor(
            base_url="http://model.test/v1",
            capabilities=container.capabilities,
            artifacts=container.artifacts,
            objects=container.objects,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
    )
    return profile


def _send(client: TestClient, profile_id: str, capability_id: str, text: str) -> dict:
    response = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "SendMessage",
            "params": {
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": text}],
                    "metadata": {"aci": {"profileId": profile_id, "capabilityId": capability_id}},
                }
            },
        },
    )
    return response.json()


def test_delegated_task_completes_end_to_end(engine: object) -> None:  # noqa: ARG001
    """SendMessage -> model executor -> completed, skill body in the prompt."""
    container = Container(Settings())
    now = datetime.now(UTC)
    agent_cid, skill_cid = uid("agent"), uid("skill")
    _register_skill(container, skill_cid, now)
    _register_agent(container, agent_cid, now)

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["prompt"] = json.loads(request.read())["messages"][0]["content"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Fixed and verified."}}]}
        )

    profile = _wire_profile(container, agent_cid, skill_cid, handler)
    client = TestClient(create_app(container))

    card = client.get("/.well-known/agent-card.json").json()
    assert any(s["id"] == agent_cid for s in card["skills"])

    body = _send(client, profile.profile_id, agent_cid, "Fix the failing test.")
    task = body["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    agent_messages = [m for m in task["history"] if m["role"] == "ROLE_AGENT"]
    assert agent_messages[-1]["parts"][0]["text"] == "Fixed and verified."

    # the granted skill's entry file reached the model prompt (object store -> executor)
    assert "Always verify before declaring done." in str(captured["prompt"])

    # GetTask roundtrip with history
    got = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task["id"]}},
    ).json()
    assert got["result"]["task"]["id"] == task["id"]
    assert got["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_missing_required_release_fails_the_task_caller_visibly(
    engine: object,  # noqa: ARG001
) -> None:
    """A required skill with no active production release fails BEFORE work."""
    container = Container(Settings())
    now = datetime.now(UTC)
    agent_cid, skill_cid = uid("agent"), uid("skill")
    _register_agent(container, agent_cid, now)
    # NOTE: skill_cid has no release — the grant cannot resolve.

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("model must not be called when grants cannot resolve")

    profile = _wire_profile(container, agent_cid, skill_cid, handler)
    client = TestClient(create_app(container))

    body = _send(client, profile.profile_id, agent_cid, "Do the thing.")
    task = body["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_FAILED"
    agent_messages = [m for m in task["history"] if m["role"] == "ROLE_AGENT"]
    assert skill_cid in agent_messages[-1]["parts"][0]["text"]
