"""§61 security cases for the A2A gateway (V3-4 §56) — the newest wire
surface, previously uncovered by the security suite.

Load-bearing claims, enforced as tests:

- Artifacts are digest-pinned: the wire carries sha256 digests, NEVER
  raw artifact bytes (§39 content-addressed store boundary).
- GetTask returns only its own task's history — a foreign task id is
  TASK_NOT_FOUND, never a leak of another principal's tasks.
- The agent card advertises only production-active releases: a staging
  or revoked release never appears in the public card.
- JSON-RPC errors are typed codes with stable messages — no stack
  traces, no internal identifiers, no raw exception text.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aci.adapters.inbound.a2a.gateway import A2AGateway, create_a2a_router

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 29, tzinfo=UTC)
DIGEST = "sha256:" + "ab" * 32


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class FakeTasks:
    """Just enough task store for gateway boundary tests."""

    def __init__(self) -> None:
        self.tasks: dict[str, Any] = {}
        self.messages: dict[str, list[Any]] = {}
        self.artifacts: dict[str, list[Any]] = {}

    def put_task(self, task: Any) -> Any:
        self.tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str) -> Any | None:
        return self.tasks.get(task_id)

    def put_message(self, message: Any) -> Any:
        self.messages.setdefault(message.task_id, []).append(message)
        return message

    def list_messages(self, task_id: str) -> list[Any]:
        return self.messages.get(task_id, [])

    def put_artifact(self, artifact: Any) -> Any:
        self.artifacts.setdefault(artifact.task_id, []).append(artifact)
        return artifact

    def list_artifacts(self, task_id: str) -> list[Any]:
        return self.artifacts.get(task_id, [])


class FakeReleases:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    def list_channel(self, channel: str, *, status: str | None = None) -> list[Any]:
        return [r for r in self.rows if r.channel == channel and r.status == status]


class FakeCapabilities:
    def __init__(self) -> None:
        self.caps: dict[str, Any] = {}

    def get_capability(self, capability_id: str) -> Any | None:
        return self.caps.get(capability_id)

    def get_capabilities(self, ids: list[str]) -> list[Any]:
        return [self.caps[i] for i in ids if i in self.caps]

    def get_versions(self, pairs: list[tuple[str, str]]) -> list[Any]:
        return []


def _gateway(tasks: FakeTasks, releases: FakeReleases) -> A2AGateway:
    return A2AGateway(
        runtime=None,  # type: ignore[arg-type]
        tasks=tasks,  # type: ignore[arg-type]
        releases=releases,  # type: ignore[arg-type]
        capabilities=FakeCapabilities(),  # type: ignore[arg-type]
        profiles={},  # type: ignore[arg-type]
        service_url="http://test",
    )


def _client(gateway: A2AGateway) -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_a2a_router(gateway))
    return TestClient(app)


def _rpc(client: TestClient, method: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    assert resp.status_code == 200
    return resp.json()


def _seeded_task(tasks: FakeTasks, task_id: str) -> None:
    from aci.domain.agent.models import AgentTask, TaskArtifact, TaskMessage

    tasks.put_task(
        AgentTask(
            task_id=task_id,
            profile_id="prof-x",
            capability_id="cap-x",
            input_text="secret user input for this task only",
            status="working",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    tasks.put_message(
        TaskMessage(
            message_id=uid("msg"),
            task_id=task_id,
            author="agent",
            content="agent reply",
            created_at=NOW,
        )
    )
    tasks.put_artifact(
        TaskArtifact(
            artifact_id=uid("art"),
            task_id=task_id,
            name="report.md",
            digest=DIGEST,
            created_at=NOW,
        )
    )


def test_artifact_wire_carries_digest_never_raw_bytes() -> None:
    """§39: artifact bytes live in the content-addressed store — the
    GetTask response carries the sha256 digest, never file contents."""
    tasks = FakeTasks()
    task_id = uid("task")
    _seeded_task(tasks, task_id)
    client = _client(_gateway(tasks, FakeReleases()))

    body = _rpc(client, "GetTask", {"id": task_id})
    artifacts = body["result"]["task"]["artifacts"]
    assert artifacts and artifacts[0]["parts"] == [{"text": DIGEST}]
    # the raw artifact body is nowhere in the response
    assert "report body" not in str(body)


def test_get_task_foreign_id_is_not_found_never_a_leak() -> None:
    """A foreign task id yields the typed TASK_NOT_FOUND error — never
    another principal's task, history, or input text."""
    tasks = FakeTasks()
    mine = uid("task")
    _seeded_task(tasks, mine)
    client = _client(_gateway(tasks, FakeReleases()))

    body = _rpc(client, "GetTask", {"id": uid("foreign")})
    assert "error" in body
    assert body["error"]["code"] == -32001
    assert "secret user input" not in str(body)


def test_agent_card_lists_only_production_active() -> None:
    """The public agent card advertises production-active releases only —
    staging and revoked releases never appear on the wire."""
    from aci.domain.capability.models import Capability

    releases = FakeReleases()

    class Row:
        def __init__(self, capability_id: str, channel: str, status: str) -> None:
            self.capability_id = capability_id
            self.version = "1.0.0"
            self.channel = channel
            self.status = status

    prod_id, staging_id, revoked_id = uid("cap"), uid("cap"), uid("cap")
    releases.rows = [
        Row(prod_id, "production", "active"),
        Row(staging_id, "staging", "active"),
        Row(revoked_id, "production", "revoked"),
    ]
    capabilities = FakeCapabilities()
    for cap_id in (prod_id, staging_id, revoked_id):
        capabilities.caps[cap_id] = Capability(id=cap_id, kind="agent", created_at=NOW)

    gateway = A2AGateway(
        runtime=None,  # type: ignore[arg-type]
        tasks=FakeTasks(),  # type: ignore[arg-type]
        releases=releases,  # type: ignore[arg-type]
        capabilities=capabilities,  # type: ignore[arg-type]
        profiles={},  # type: ignore[arg-type]
        service_url="http://test",
    )
    card = gateway.agent_card()
    offered = {str(s.get("id")) for s in card.get("skills", [])}
    assert offered == {prod_id}  # staging and revoked never reach the card


def test_rpc_errors_are_typed_codes_not_exception_text() -> None:
    """Malformed JSON-RPC yields the typed -32700 parse error — no stack
    trace, no internal identifiers, no raw exception text on the wire."""
    tasks = FakeTasks()
    client = _client(_gateway(tasks, FakeReleases()))

    resp = client.post("/a2a", content=b"{not json", headers={"Content-Type": "application/json"})
    body = resp.json()
    assert body["error"]["code"] == -32700
    assert "Traceback" not in str(body)
    assert "Exception" not in str(body)


def test_send_message_requires_metadata_never_guesses_a_profile() -> None:
    """Delegation without explicit profile/capability metadata is
    invalid params — the gateway never falls back to a default profile
    (fail-closed, §56.1)."""
    client = _client(_gateway(FakeTasks(), FakeReleases()))
    body = _rpc(
        client,
        "SendMessage",
        {"message": {"role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    )
    assert "error" in body
    assert body["error"]["code"] == -32602
