"""OpenAI-compatible model client as the delegated-task executor (V3 §56.1).

The whole point of the ``AgentExecutor`` protocol (§50: no model gateway in
the platform — a CLIENT plugs in here): this adapter calls a deployment-owned
chat-completions endpoint and nothing else. Granted skills are loaded from
the immutable artifact store with content re-verification (§39) — the same
boundary the catalog enforces — so the model only ever sees trusted package
bytes, and only the ones the profile's ``SkillPolicy`` pinned.

Every failure is a caller-visible failed ``ExecutorResult`` (the runtime
records it and fails the task); the executor never raises past the runtime
and never leaves a task stuck.
"""

import hashlib
import json
from datetime import datetime
from uuid import uuid4

import httpx

from aci.application.protocols import (
    ArtifactStore,
    CapabilityRepository,
    ObjectStore,
)
from aci.domain.agent.models import (
    AgentProfile,
    AgentTask,
    ExecutorResult,
    SkillGrant,
    TaskMessage,
)
from aci.domain.capability.models import SkillSpec

_SYSTEM_TEMPLATE = """\
You are an agent executing one delegated task.

Agent identity:
- profile: {profile_id} (model class: {model_profile})
- capability: {capability_id}

Execution policy (§32 — explicit, never implicit):
- side effect class: {side_effect_class}
- may write to a repository: {can_write_repository}
- allowed tools: {allowed_tools}

{skills}Answer the caller's task. Your response text is returned to the
caller verbatim as the task's result message."""

_SKILL_TEMPLATE = """\
Granted skill (capability {capability_id} @ {version}) — follow its
instructions where they apply:

---
{body}
---

"""


class OpenAICompatExecutor:
    """``AgentExecutor`` over an OpenAI-compatible ``/chat/completions`` endpoint.

    The endpoint is deployment infrastructure (the user's gateway, a local
    inference server, anything speaking the OpenAI wire shape); the platform
    gains no model authority from it (§76) — it is a client, not a gateway.
    """

    def __init__(
        self,
        *,
        base_url: str,
        capabilities: CapabilityRepository,
        artifacts: ArtifactStore,
        objects: ObjectStore,
        api_key: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._capabilities = capabilities
        self._artifacts = artifacts
        self._objects = objects
        self._api_key = api_key
        self._client = client

    def execute(
        self,
        task: AgentTask,
        profile: AgentProfile,
        skills: list[SkillGrant],
        *,
        now: datetime,
    ) -> ExecutorResult:
        """One delegated execution: load grants -> prompt -> model -> result."""
        loaded: list[tuple[SkillGrant, str]] = []
        for grant in skills:
            body, problem = self._load_skill_body(grant)
            if body is None or problem is not None:
                return ExecutorResult(
                    status="failed", detail=problem or f"cannot load {grant.capability_id}"
                )
            loaded.append((grant, body))

        system = _SYSTEM_TEMPLATE.format(
            profile_id=profile.profile_id,
            model_profile=profile.model_profile,
            capability_id=task.capability_id,
            side_effect_class=profile.execution_policy.side_effect_class,
            can_write_repository=profile.execution_policy.can_write_repository,
            allowed_tools=", ".join(profile.allowed_tools) or "(none)",
            skills="".join(
                _SKILL_TEMPLATE.format(capability_id=g.capability_id, version=g.version, body=b)
                for g, b in loaded
            ),
        )
        request_body = {
            "model": profile.model_profile,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": task.input_text},
            ],
            "max_tokens": profile.budget.max_tokens,
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

        client = self._client or httpx.Client()
        try:
            response = client.post(
                f"{self._base_url}/chat/completions",
                json=request_body,
                headers=headers,
                timeout=profile.budget.max_wall_time_seconds,
            )
        except httpx.HTTPError as exc:
            return ExecutorResult(status="failed", detail=f"model endpoint unreachable: {exc}")
        finally:
            if self._client is None:
                client.close()

        if response.status_code != 200:
            return ExecutorResult(
                status="failed",
                detail=f"model endpoint returned {response.status_code}: {response.text[:300]}",
            )
        try:
            content = self._completion_content(response)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return ExecutorResult(status="failed", detail=f"malformed model response: {exc}")
        if not content.strip():
            return ExecutorResult(status="failed", detail="model returned an empty completion")
        return ExecutorResult(
            status="completed",
            messages=[
                TaskMessage(
                    message_id=f"msg-{uuid4().hex[:12]}",
                    task_id=task.task_id,
                    author="agent",
                    content=content,
                    created_at=now,
                )
            ],
        )

    def _completion_content(self, response: httpx.Response) -> str:
        """Extract the completion text from a JSON or SSE response body.

        Some OpenAI-compatible gateways stream unconditionally (§77: verify
        wire shapes at implementation time — verified live 2026-09-28): the
        body arrives as ``text/event-stream`` with either the whole
        ``chat.completion`` as one event or ``chat.completion.chunk`` deltas.
        Both are handled; plain JSON is the fast path.
        """
        raw = response.text
        # The live gateway's "SSE" body is actually one whole JSON object
        # with a trailing `data: [DONE]` line and NO per-event prefix —
        # raw_decode takes the first JSON value and ignores the trailing
        # garbage, so try that before falling back to real SSE parsing.
        try:
            data, _ = json.JSONDecoder().raw_decode(raw.lstrip())
        except json.JSONDecodeError:
            return self._content_from_sse(raw)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"completion object has no message content: {exc}") from exc
        return content if isinstance(content, str) else ""

    @staticmethod
    def _content_from_sse(raw: str) -> str:
        chunks: list[str] = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("object") == "chat.completion":
                # whole completion streamed as one event
                content = event["choices"][0]["message"]["content"]
                return content if isinstance(content, str) else ""
            if event.get("object") == "chat.completion.chunk":
                delta = event["choices"][0]["delta"].get("content")
                if delta:
                    chunks.append(str(delta))
        return "".join(chunks)

    def _load_skill_body(self, grant: SkillGrant) -> tuple[str | None, str | None]:
        """Load a granted skill's entry file with content re-verification (§39).

        Returns ``(body, None)`` on success or ``(None, problem)`` — the caller
        turns the problem into a caller-visible failed result.
        """
        version = self._capabilities.get_version(grant.capability_id, grant.version)
        if version is None or version.kind != "skill":
            return None, f"granted capability {grant.capability_id} is not a skill"
        artifact = self._artifacts.get_artifact(grant.capability_id, grant.version)
        if artifact is None:
            return None, f"no artifact for {grant.capability_id}@{grant.version}"
        entry_path = version.spec.entrypoint if isinstance(version.spec, SkillSpec) else "SKILL.md"
        entry = next((f for f in artifact.files if f.path == entry_path), None)
        if entry is None:
            return None, f"{grant.capability_id}@{grant.version} has no file {entry_path}"
        data = self._objects.get(entry.sha256)
        if data is None:
            return None, f"blob {entry.sha256} missing for {grant.capability_id}/{entry_path}"
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            return None, (f"blob for {grant.capability_id}/{entry_path} failed its content hash")
        return data.decode("utf-8", errors="replace"), None
