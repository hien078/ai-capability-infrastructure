"""Unit tests for the OpenAI-compatible delegated-task executor (V3 §56.1).

Pins the executor contract with fakes + httpx.MockTransport: granted skills
are loaded from the artifact store with content re-verification (§39), the
system prompt carries identity + policy + skill bodies, and every failure
is a caller-visible failed ExecutorResult — never an exception past the
runtime, never a stuck task.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from aci.adapters.outbound.model_provider.executor import OpenAICompatExecutor
from aci.domain.agent.models import (
    AgentProfile,
    AgentTask,
    ExecutorResult,
    ProfileBudget,
    SkillGrant,
    SkillPolicy,
)
from aci.domain.capability.models import (
    AgentSpec,
    ArtifactFile,
    CapabilityArtifact,
    CapabilityVersion,
    SkillSpec,
)

SKILL_BODY = "# test-skill\n\nAlways verify before declaring done.\n"
SKILL_SHA = hashlib.sha256(SKILL_BODY.encode()).hexdigest()
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
GRANTS = [SkillGrant(capability_id="test-skill", version="1.0.0")]


class FakeCapabilities:
    def __init__(self, versions: dict[tuple[str, str], CapabilityVersion]) -> None:
        self._versions = versions

    def get_version(self, capability_id: str, version: str) -> CapabilityVersion | None:
        return self._versions.get((capability_id, version))


class FakeArtifacts:
    def __init__(self, artifacts: dict[tuple[str, str], CapabilityArtifact]) -> None:
        self._artifacts = artifacts

    def get_artifact(self, capability_id: str, version: str) -> CapabilityArtifact | None:
        return self._artifacts.get((capability_id, version))


class FakeObjects:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs

    def get(self, key: str) -> bytes | None:
        return self._blobs.get(key)


def _skill_version(capability_id: str = "test-skill", version: str = "1.0.0") -> CapabilityVersion:
    return CapabilityVersion(
        capability_id=capability_id,
        version=version,
        kind="skill",
        content_digest=f"sha256:{'a' * 64}",
        created_at=NOW,
        spec=SkillSpec(),
    )


def _agent_version(capability_id: str = "some-agent", version: str = "1.0.0") -> CapabilityVersion:
    return CapabilityVersion(
        capability_id=capability_id,
        version=version,
        kind="agent",
        content_digest=f"sha256:{'c' * 64}",
        created_at=NOW,
        spec=AgentSpec(),
    )


def _skill_artifact(
    capability_id: str = "test-skill", version: str = "1.0.0"
) -> CapabilityArtifact:
    return CapabilityArtifact(
        capability_id=capability_id,
        version=version,
        package_digest=f"sha256:{'b' * 64}",
        files=[ArtifactFile(path="SKILL.md", sha256=SKILL_SHA, size_bytes=len(SKILL_BODY))],
    )


def _profile(**overrides: Any) -> AgentProfile:
    record: dict[str, Any] = {
        "profile_id": "coder",
        "version": "1",
        "model_profile": "test-model",
        "skill_policy": SkillPolicy(required=["test-skill"]),
        "budget": ProfileBudget(max_tokens=512, max_wall_time_seconds=30),
        "execution_policy": {
            "side_effect_class": "read_only",
            "can_write_repository": False,
        },
        "created_at": NOW,
    }
    record.update(overrides)
    return AgentProfile.model_validate(record)


def _task() -> AgentTask:
    return AgentTask(
        task_id="task-1",
        profile_id="coder",
        capability_id="aci-coder",
        input_text="Fix the failing test.",
        created_at=NOW,
        updated_at=NOW,
    )


def _executor(
    handler: Any,
    *,
    blobs: dict[str, bytes] | None = None,
    versions: dict[tuple[str, str], CapabilityVersion] | None = None,
    artifacts: dict[tuple[str, str], CapabilityArtifact] | None = None,
) -> tuple[OpenAICompatExecutor, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    executor = OpenAICompatExecutor(
        base_url="http://gateway.test/v1",
        capabilities=FakeCapabilities(versions or {("test-skill", "1.0.0"): _skill_version()}),
        artifacts=FakeArtifacts(artifacts or {("test-skill", "1.0.0"): _skill_artifact()}),
        objects=FakeObjects(blobs if blobs is not None else {SKILL_SHA: SKILL_BODY.encode()}),
        client=httpx.Client(transport=httpx.MockTransport(wrapped)),
    )
    return executor, requests


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "Fixed and verified."}}]})


def _sse_handler(events: list[dict[str, Any]]) -> Any:
    """SSE body like the live gateway: data: <completion> ... data: [DONE]."""

    def handler(request: httpx.Request) -> httpx.Response:
        lines = [f"data: {json.dumps(e)}" for e in events] + ["data: [DONE]"]
        return httpx.Response(
            200, text="\n".join(lines), headers={"content-type": "text/event-stream"}
        )

    return handler


def test_sse_whole_completion_event_is_parsed() -> None:
    """The live gateway streams the full chat.completion as ONE SSE event."""
    events = [
        {
            "id": "1",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Streamed."}}],
        }
    ]
    executor, _ = _executor(_sse_handler(events))
    result = executor.execute(_task(), _profile(), GRANTS, now=NOW)
    assert result.status == "completed"
    assert result.messages[0].content == "Streamed."


def test_sse_chunk_deltas_are_accumulated() -> None:
    events = [
        {
            "id": "1",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "Fix"}}],
        },
        {
            "id": "1",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "ed."}}],
        },
    ]
    executor, _ = _executor(_sse_handler(events))
    result = executor.execute(_task(), _profile(), GRANTS, now=NOW)
    assert result.status == "completed"
    assert result.messages[0].content == "Fixed."


def test_live_gateway_hybrid_body_is_parsed() -> None:
    """The real gateway: whole JSON completion + trailing `data: [DONE]`,
    labeled text/event-stream, with NO per-event prefix (verified live)."""

    def handler(request: httpx.Request) -> httpx.Response:
        completion = json.dumps(
            {
                "id": "1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hybrid form parsed.",
                            "reasoning_content": "thinking...",
                        },
                    }
                ],
            }
        )
        return httpx.Response(
            200,
            text=completion + "data: [DONE]",
            headers={"content-type": "text/event-stream"},
        )

    executor, _ = _executor(handler)
    result = executor.execute(_task(), _profile(), GRANTS, now=NOW)
    assert result.status == "completed"
    assert result.messages[0].content == "Hybrid form parsed."


def test_completed_result_carries_one_agent_message() -> None:
    executor, _ = _executor(_ok_handler)
    result = executor.execute(_task(), _profile(), GRANTS, now=NOW)
    assert isinstance(result, ExecutorResult)
    assert result.status == "completed"
    assert result.detail is None
    assert len(result.messages) == 1
    message = result.messages[0]
    assert message.task_id == "task-1"
    assert message.author == "agent"
    assert message.content == "Fixed and verified."


def test_system_prompt_carries_policy_and_skill_body() -> None:
    executor, requests = _executor(_ok_handler)
    executor.execute(_task(), _profile(), GRANTS, now=NOW)
    body = json.loads(requests[0].read())
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 512
    system = body["messages"][0]["content"]
    user = body["messages"][1]["content"]
    assert user == "Fix the failing test."
    # identity + explicit policy (§32) + the granted skill's entry body
    assert "profile: coder" in system
    assert "side effect class: read_only" in system
    assert "may write to a repository: False" in system
    assert "Always verify before declaring done." in system
    assert "test-skill @ 1.0.0" in system


def test_api_error_fails_the_task_with_detail() -> None:
    executor, _ = _executor(lambda r: httpx.Response(503, text="overloaded at /srv/secret"))
    result = executor.execute(_task(), _profile(), GRANTS, now=NOW)
    assert result.status == "failed"
    assert "503" in (result.detail or "")
    # §61: the detail lands in A2A task history — the provider body never does.
    assert "/srv/secret" not in (result.detail or "")


def test_malformed_response_fails_the_task() -> None:
    executor, _ = _executor(lambda r: httpx.Response(200, json={"unexpected": True}))
    result = executor.execute(_task(), _profile(), GRANTS, now=NOW)
    assert result.status == "failed"
    assert "malformed" in (result.detail or "")


def test_empty_completion_fails_the_task() -> None:
    executor, _ = _executor(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]})
    )
    result = executor.execute(_task(), _profile(), GRANTS, now=NOW)
    assert result.status == "failed"
    assert "empty" in (result.detail or "")


def test_missing_blob_fails_the_task() -> None:
    executor, _ = _executor(_ok_handler, blobs={})
    result = executor.execute(_task(), _profile(), GRANTS, now=NOW)
    assert result.status == "failed"
    assert "missing" in (result.detail or "")


def test_tampered_blob_fails_the_task() -> None:
    executor, _ = _executor(_ok_handler, blobs={SKILL_SHA: b"tampered bytes"})
    result = executor.execute(_task(), _profile(), GRANTS, now=NOW)
    assert result.status == "failed"
    assert "content hash" in (result.detail or "")


def test_non_skill_grant_is_rejected() -> None:
    """A grant pointing at a kind=agent version must not be loaded as a skill."""
    executor, _ = _executor(
        _ok_handler,
        versions={("some-agent", "1.0.0"): _agent_version()},
        artifacts={("some-agent", "1.0.0"): _skill_artifact("some-agent")},
        blobs={SKILL_SHA: SKILL_BODY.encode()},
    )
    result = executor.execute(
        _task(), _profile(), [SkillGrant(capability_id="some-agent", version="1.0.0")], now=NOW
    )
    assert result.status == "failed"
    assert "not a skill" in (result.detail or "")


def test_unreachable_endpoint_fails_the_task() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    executor, _ = _executor(handler)
    result = executor.execute(_task(), _profile(), GRANTS, now=NOW)
    assert result.status == "failed"
    assert "unreachable" in (result.detail or "")


def test_profile_stays_delegation_only() -> None:
    """Guard the §32 invariant the profile loader relies on."""
    with pytest.raises(ValidationError):
        _profile(execution_policy={"execution_mode": "remote_call", "side_effect_class": "none"})
