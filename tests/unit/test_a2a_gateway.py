"""A2A gateway (V3 §56, §30.1) — wire translation over fakes, no DB.

Pins the JSON-RPC surface against the A2A v1.0.0 shapes (§77-verified):
methods SendMessage/GetTask/CancelTask, Task/TaskStatus/TaskState/Message/
Artifact projection, error codes -32600/-32601/-32602/-32700/-32001/
-32002/-32009, Agent Card at the well-known URI.
"""

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, cast

import httpx
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


class SlowExecutor(EchoExecutor):
    """Blocks like a synchronous model HTTP call before completing."""

    def execute(
        self,
        task: AgentTask,
        profile: AgentProfile,
        skills: list[Any],
        *,
        now: datetime,
    ) -> ExecutorResult:
        time.sleep(1.0)
        return super().execute(task, profile, skills, now=now)


class ForeignOutputExecutor:
    """Returns a message for another task: the runtime raises a DomainError
    whose text interpolates internal message/task ids."""

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
                    message_id="msg-SECRET-internal",
                    task_id="task-SECRET-foreign",
                    author="agent",
                    content="x",
                    created_at=now,
                )
            ],
        )


def _app(
    executor: Any = None,
    *,
    token: str | None = None,
    principals: dict[str, str] | None = None,
) -> tuple[FastAPI, FakeTasks]:
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
            executor=cast(AgentExecutor, executor or EchoExecutor()),
        ),
        tasks=cast(TaskRepository, tasks),
        releases=cast(ReleaseRepository, releases),
        capabilities=cast(CapabilityRepository, capabilities),
        profiles={"reviewer": _profile()},
        service_url="http://testserver",
    )
    app = FastAPI()
    app.include_router(create_a2a_router(gateway, token=token, principals=principals))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app, tasks


def _client(
    executor: Any = None,
    *,
    token: str | None = None,
    principals: dict[str, str] | None = None,
) -> tuple[TestClient, FakeTasks]:
    app, tasks = _app(executor, token=token, principals=principals)
    return TestClient(app), tasks


def _send_body(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "Review PR #42."}],
            "metadata": {"aci": {"profileId": "reviewer", "capabilityId": "code-reviewer-agent"}},
        }
    }
    params.update(overrides)
    return {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": params}


def _send(client: TestClient, headers: dict[str, str] | None = None, **overrides: Any) -> Any:
    return client.post("/a2a", json=_send_body(**overrides), headers=headers)


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


def test_blocking_dispatch_does_not_stall_the_event_loop() -> None:
    """Regression: a SendMessage whose executor blocks (sync model HTTP call)
    must run off the loop — /health keeps answering meanwhile."""
    app, _ = _app(SlowExecutor())

    async def scenario() -> tuple[float, int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            started = time.perf_counter()
            send = asyncio.create_task(client.post("/a2a", json=_send_body()))
            await asyncio.sleep(0.05)
            health = await client.get("/health")
            health_elapsed = time.perf_counter() - started
            sent = await send
        assert health.json() == {"status": "ok"}
        return health_elapsed, sent.status_code

    health_elapsed, send_status = asyncio.run(scenario())
    assert send_status == 200
    assert health_elapsed < 0.5, f"/health blocked {health_elapsed:.2f}s behind SendMessage"


def test_token_set_requires_matching_bearer() -> None:
    client, _ = _client(token="s3cret")
    for headers in (None, {"Authorization": "Bearer wrong"}, {"Authorization": "s3cret"}):
        response = _send(client, headers=headers)
        assert response.status_code == 401
        body = response.json()
        assert body["error"] == {"code": -32000, "message": "Unauthorized"}
        assert "s3cret" not in response.text
    ok = _send(client, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert ok.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    # Discovery stays public.
    assert client.get("/.well-known/agent-card.json").status_code == 200


def test_token_unset_is_unauthenticated_mode() -> None:
    for token in (None, ""):
        client, _ = _client(token=token)
        assert _send(client).status_code == 200


def test_domain_error_text_never_reaches_the_wire() -> None:
    client, _ = _client(ForeignOutputExecutor())
    body = _send(client).json()
    assert body["error"] == {"code": -32602, "message": "EXECUTOR_CONTRACT_VIOLATION"}
    assert "SECRET" not in str(body)


# -- principal isolation ------------------------------------------------------

PRINCIPALS = {"alice": "tok-alice", "bob": "tok-bob"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _call(client: TestClient, method: str, task_id: str, token: str | None) -> Any:
    return client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": 9, "method": method, "params": {"id": task_id}},
        headers=_bearer(token) if token is not None else None,
    )


def _seed_working(tasks: FakeTasks, owner: str) -> str:
    task = AgentTask(
        task_id=f"task-{owner}-working",
        profile_id="reviewer",
        capability_id="code-reviewer-agent",
        input_text="owner-only input",
        owner=owner,
        status="working",
        created_at=NOW,
        updated_at=NOW,
    )
    tasks.put_task(task)
    return task.task_id


def test_send_message_stamps_the_authenticated_principal_as_owner() -> None:
    client, tasks = _client(principals=PRINCIPALS)
    task_id = _send(client, headers=_bearer("tok-alice")).json()["result"]["task"]["id"]
    assert tasks.tasks[task_id].owner == "alice"
    assert "owner" not in str(_call(client, "GetTask", task_id, "tok-alice").json())


def test_foreign_get_task_is_indistinguishable_from_unknown_id() -> None:
    client, tasks = _client(principals=PRINCIPALS)
    task_id = _send(client, headers=_bearer("tok-alice")).json()["result"]["task"]["id"]

    foreign = _call(client, "GetTask", task_id, "tok-bob")
    unknown = _call(client, "GetTask", "task-does-not-exist", "tok-bob")
    assert foreign.status_code == unknown.status_code == 200
    assert foreign.json()["error"] == {"code": -32001, "message": f"Task not found: {task_id}"}
    assert unknown.json()["error"] == {
        "code": -32001,
        "message": "Task not found: task-does-not-exist",
    }
    assert "result" not in foreign.json() and "Review PR" not in foreign.text
    # the owner still reads its own task
    assert _call(client, "GetTask", task_id, "tok-alice").json()["result"]["task"]["id"] == task_id


def test_foreign_cancel_is_not_found_and_leaves_the_task_alone() -> None:
    client, tasks = _client(principals=PRINCIPALS)
    working = _seed_working(tasks, "alice")

    foreign = _call(client, "CancelTask", working, "tok-bob").json()
    # -32001, not -32002: "not cancelable" would confirm the id exists.
    assert foreign["error"] == {"code": -32001, "message": f"Task not found: {working}"}
    assert tasks.tasks[working].status == "working"

    own = _call(client, "CancelTask", working, "tok-alice").json()
    assert own["result"]["task"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert tasks.tasks[working].status == "canceled"
    assert tasks.tasks[working].owner == "alice"


def test_foreign_terminal_task_cancel_is_not_found_not_uncancelable() -> None:
    client, _ = _client(principals=PRINCIPALS)
    done = _send(client, headers=_bearer("tok-alice")).json()["result"]["task"]["id"]
    assert _call(client, "CancelTask", done, "tok-bob").json()["error"]["code"] == -32001
    assert _call(client, "CancelTask", done, "tok-alice").json()["error"]["code"] == -32002


def test_a2a_token_alone_is_principal_default() -> None:
    client, tasks = _client(token="solo")
    task_id = _send(client, headers=_bearer("solo")).json()["result"]["task"]["id"]
    assert tasks.tasks[task_id].owner == "default"


def test_token_and_principals_coexist() -> None:
    client, tasks = _client(token="solo", principals=PRINCIPALS)
    mine = _send(client, headers=_bearer("solo")).json()["result"]["task"]["id"]
    theirs = _send(client, headers=_bearer("tok-bob")).json()["result"]["task"]["id"]
    assert (tasks.tasks[mine].owner, tasks.tasks[theirs].owner) == ("default", "bob")
    assert _call(client, "GetTask", theirs, "solo").json()["error"]["code"] == -32001


def test_unauthenticated_mode_owner_is_anonymous() -> None:
    client, tasks = _client()
    task_id = _send(client).json()["result"]["task"]["id"]
    assert tasks.tasks[task_id].owner == "anonymous"
    assert _call(client, "GetTask", task_id, None).json()["result"]["task"]["id"] == task_id
    # An anonymous caller cannot reach a named principal's task either.
    other = _seed_working(tasks, "alice")
    assert _call(client, "GetTask", other, None).json()["error"]["code"] == -32001


def test_principals_wrong_or_missing_bearer_is_401() -> None:
    client, tasks = _client(principals=PRINCIPALS)
    for headers in (None, _bearer("tok-carol"), {"Authorization": "tok-alice"}, _bearer("")):
        response = _send(client, headers=headers)
        assert response.status_code == 401
        assert response.json()["error"] == {"code": -32000, "message": "Unauthorized"}
        assert "tok-" not in response.text and "alice" not in response.text
    assert tasks.tasks == {}


def test_empty_token_principal_never_authenticates() -> None:
    """A principal configured with an empty token must not match 'Bearer '."""
    client, _ = _client(principals={"alice": "tok-alice", "ghost": ""})
    assert _send(client, headers={"Authorization": "Bearer "}).status_code == 401
    assert _send(client).status_code == 401


def test_reserved_anonymous_principal_cannot_be_claimed() -> None:
    """'anonymous' owns unauthenticated-mode and pre-0015 tasks; a configured
    principal of that name must never authenticate as it."""
    client, tasks = _client(principals={"anonymous": "tok-anon", "alice": "tok-alice"})
    legacy = _seed_working(tasks, "anonymous")
    assert _call(client, "GetTask", legacy, "tok-anon").status_code == 401
    assert _call(client, "GetTask", legacy, "tok-alice").json()["error"]["code"] == -32001


def test_shared_token_between_principals_is_ambiguous_401() -> None:
    client, _ = _client(principals={"alice": "same", "bob": "same"})
    assert _send(client, headers=_bearer("same")).status_code == 401
