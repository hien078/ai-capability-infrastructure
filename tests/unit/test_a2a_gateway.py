"""A2A gateway (V3 §56, §30.1) — wire translation over fakes, no DB.

Pins the JSON-RPC surface against the A2A v1.0.0 shapes (§77-verified):
methods SendMessage/GetTask/CancelTask, Task/TaskStatus/TaskState/Message/
Artifact projection, error codes -32600/-32601/-32602/-32700/-32001/
-32002/-32009, Agent Card at the well-known URI.
"""

from datetime import UTC, datetime
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aci.adapters.inbound.a2a.gateway import A2AGateway, create_a2a_router
from aci.application.delegate_task import ProfileDrivenAgentRuntime
from aci.application.protocols import (
    AgentExecutor,
    CapabilityRepository,
    ReleaseRepository,
    TaskRepository,
)
from aci.domain.agent.models import (
    AgentProfile,
    AgentTask,
    ExecutionPolicy,
    ExecutorResult,
    ProfileBudget,
    SkillPolicy,
    TaskArtifact,
    TaskMessage,
)
from aci.domain.capability.models import Capability, CapabilityRelease

NOW = datetime(2026, 9, 28, tzinfo=UTC)
DIGEST = "sha256:" + "ab" * 32


class FakeTasks:
    def __init__(self) -> None:
        self.tasks: dict[str, AgentTask] = {}
        self.messages: list[TaskMessage] = []
        self.artifacts: list[TaskArtifact] = []

    def put_task(self, task: AgentTask) -> AgentTask:
        self.tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str) -> AgentTask | None:
        return self.tasks.get(task_id)

    def put_message(self, message: TaskMessage) -> TaskMessage:
        self.messages.append(message)
        return message

    def list_messages(self, task_id: str) -> list[TaskMessage]:
        return [m for m in self.messages if m.task_id == task_id]

    def put_artifact(self, artifact: TaskArtifact) -> TaskArtifact:
        self.artifacts.append(artifact)
        return artifact

    def list_artifacts(self, task_id: str) -> list[TaskArtifact]:
        return [a for a in self.artifacts if a.task_id == task_id]


class FakeReleases:
    def __init__(self) -> None:
        self._channel: list[CapabilityRelease] = []

    def add(self, release: CapabilityRelease) -> None:
        self._channel.append(release)

    def set_release(self, release: CapabilityRelease) -> CapabilityRelease:
        return release

    def get_release(self, capability_id: str, channel: str) -> CapabilityRelease | None:
        for r in self._channel:
            if r.capability_id == capability_id and r.channel == channel:
                return r
        return None

    def list_releases(self, capability_id: str) -> list[CapabilityRelease]:
        return []

    def list_channel(self, channel: str, *, status: str | None = None) -> list[CapabilityRelease]:
        return [r for r in self._channel if r.channel == channel and r.status == status]


class FakeCapabilities:
    def __init__(self) -> None:
        self.caps: dict[str, Capability] = {}

    def get_capability(self, capability_id: str) -> Capability | None:
        return self.caps.get(capability_id)

    def get_capabilities(self, ids: list[str]) -> list[Capability]:
        return [self.caps[i] for i in ids if i in self.caps]

    def get_version(self, capability_id: str, version: str) -> Any:
        return None

    def get_versions(self, pairs: list[tuple[str, str]]) -> list[Any]:
        return []

    def create_capability(self, capability: Capability) -> Capability:
        self.caps[capability.id] = capability
        return capability

    def create_version(self, version: Any) -> Any:
        return version

    def list_versions(self, capability_id: str) -> list[Any]:
        return []


class EchoExecutor:
    """Completes every task with one message + one digest-pinned artifact."""

    def execute(
        self,
        task: AgentTask,
        profile: AgentProfile,
        skills: list[Any],
        *,
        now: datetime,
    ) -> ExecutorResult:
        return ExecutorResult(
            status="completed",
            messages=[
                TaskMessage(
                    message_id="m-1",
                    task_id=task.task_id,
                    author="agent",
                    content="review finished",
                    created_at=now,
                )
            ],
            artifacts=[
                TaskArtifact(
                    artifact_id="a-1",
                    task_id=task.task_id,
                    name="review.md",
                    digest=DIGEST,
                    created_at=now,
                )
            ],
        )


def _profile() -> AgentProfile:
    return AgentProfile(
        profile_id="reviewer",
        version="1.0.0",
        model_profile="reasoning-medium",
        allowed_tools=["repository.read"],
        skill_policy=SkillPolicy(required=[]),
        budget=ProfileBudget(max_tokens=1000, max_wall_time_seconds=60),
        execution_policy=ExecutionPolicy(side_effect_class="read_only"),
        created_at=NOW,
    )


def _client() -> tuple[TestClient, FakeTasks]:
    tasks = FakeTasks()
    releases = FakeReleases()
    releases.add(
        CapabilityRelease(
            capability_id="code-reviewer-agent", version="1.0.0", channel="production"
        )
    )
    capabilities = FakeCapabilities()
    capabilities.caps["code-reviewer-agent"] = Capability(
        id="code-reviewer-agent", kind="agent", created_at=NOW
    )
    capabilities.caps["some-skill"] = Capability(id="some-skill", kind="skill", created_at=NOW)
    gateway = A2AGateway(
        runtime=ProfileDrivenAgentRuntime(
            tasks=cast(TaskRepository, tasks),
            releases=cast(ReleaseRepository, releases),
            executor=cast(AgentExecutor, EchoExecutor()),
        ),
        tasks=cast(TaskRepository, tasks),
        releases=cast(ReleaseRepository, releases),
        capabilities=cast(CapabilityRepository, capabilities),
        profiles={"reviewer": _profile()},
        service_url="http://testserver",
    )
    app = FastAPI()
    app.include_router(create_a2a_router(gateway))
    return TestClient(app), tasks


def _send(client: TestClient, **overrides: Any) -> Any:
    params: dict[str, Any] = {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "Review PR #42."}],
            "metadata": {"aci": {"profileId": "reviewer", "capabilityId": "code-reviewer-agent"}},
        }
    }
    params.update(overrides)
    return client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": params}
    )


def test_agent_card_projects_agent_capabilities_only() -> None:
    client, _ = _client()
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "aci"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "HTTP+JSON"
    assert card["capabilities"] == {"streaming": False, "pushNotifications": False}
    # kind=agent with an active production release -> one skill; skills excluded.
    assert [s["id"] for s in card["skills"]] == ["code-reviewer-agent"]


def test_send_message_runs_task_to_completion() -> None:
    client, tasks = _client()
    body = _send(client).json()
    task = body["result"]["task"]
    assert body["jsonrpc"] == "2.0"
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["metadata"]["aci"]["profileId"] == "reviewer"
    # history carries the agent message; artifacts carry the digest, not bytes.
    assert task["history"][0]["role"] == "ROLE_AGENT"
    assert task["history"][0]["parts"] == [{"text": "review finished"}]
    assert task["artifacts"][0]["parts"] == [{"text": DIGEST}]
    assert tasks.get_task(task["id"]).status == "completed"


def test_send_message_rejects_bad_params() -> None:
    client, _ = _client()
    assert _send(client, message={}).json()["error"]["code"] == -32602
    no_text = {
        "message": {
            "role": "ROLE_USER",
            "parts": [],
            "metadata": {"aci": {"profileId": "reviewer", "capabilityId": "x"}},
        }
    }
    assert _send(client, **no_text).json()["error"]["code"] == -32602
    bad_profile = {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "hi"}],
            "metadata": {"aci": {"profileId": "nope", "capabilityId": "x"}},
        }
    }
    assert _send(client, **bad_profile).json()["error"]["code"] == -32602
    not_agent = {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "hi"}],
            "metadata": {"aci": {"profileId": "reviewer", "capabilityId": "some-skill"}},
        }
    }
    assert _send(client, **not_agent).json()["error"]["code"] == -32602


def test_get_task_and_history_length() -> None:
    client, _ = _client()
    task_id = _send(client).json()["result"]["task"]["id"]

    def get(extra: dict[str, Any] | None = None) -> Any:
        params: dict[str, Any] = {"id": task_id}
        params.update(extra or {})
        return client.post(
            "/a2a", json={"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": params}
        ).json()

    assert get()["result"]["task"]["id"] == task_id
    assert get({"historyLength": 0})["result"]["task"]["history"] == []
    missing = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": 3, "method": "GetTask", "params": {"id": "nope"}}
    ).json()
    assert missing["error"]["code"] == -32001


def test_cancel_task_terminal_is_not_cancelable() -> None:
    client, _ = _client()
    task_id = _send(client).json()["result"]["task"]["id"]  # sync runtime -> terminal

    def cancel(tid: str) -> Any:
        return client.post(
            "/a2a", json={"jsonrpc": "2.0", "id": 4, "method": "CancelTask", "params": {"id": tid}}
        ).json()

    assert cancel(task_id)["error"]["code"] == -32002  # completed = terminal
    assert cancel("nope")["error"]["code"] == -32001


def test_unknown_method_and_malformed_envelope() -> None:
    client, _ = _client()
    unknown = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": 5, "method": "SubscribeToTask", "params": {}}
    ).json()
    assert unknown["error"]["code"] == -32601
    bad_request = client.post("/a2a", json={"id": 6})
    assert bad_request.json()["error"]["code"] == -32600
    invalid_json = client.post(
        "/a2a", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert invalid_json.json()["error"]["code"] == -32700


def test_unsupported_protocol_version_is_rejected() -> None:
    client, _ = _client()
    response = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": 7, "method": "GetTask", "params": {"id": "x"}},
        headers={"A2A-Version": "0.2"},
    )
    assert response.json()["error"]["code"] == -32009
    assert response.headers["A2A-Version"] == "1.0"
