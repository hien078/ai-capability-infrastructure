"""A2A protocol gateway (V3 §56, §30.1; ADR-006 spirit: adapters translate).

Wire shapes verified against the A2A v1.0.0 specification (§77, 2026-09-28):
JSON-RPC 2.0 binding §9 (methods ``SendMessage`` / ``GetTask`` /
``CancelTask``), data model §4 (``Task`` / ``TaskStatus`` / ``TaskState`` /
``Message`` / ``Part`` / ``Artifact``), discovery §8 (Agent Card at
``/.well-known/agent-card.json``), error mapping §5.4 (``-32001``
TaskNotFound, ``-32002`` TaskNotCancelable, ``-32004`` UnsupportedOperation,
``-32009`` VersionNotSupported).

Mapping (§30.1): ``Capability(kind=agent)`` with an active production release
-> Agent Card ``skills`` entries; a delegated ``AgentTask`` -> A2A ``Task``;
``TaskMessage`` -> A2A ``Message`` (author agent|caller -> ROLE_AGENT|
ROLE_USER); ``TaskArtifact`` -> A2A ``Artifact`` whose text part carries the
content digest (artifact bytes live in the object store, §39 — the wire never
carries raw bodies). This adapter ONLY translates; all lifecycle semantics
live in the domain (``advance_task``) and the runtime.
"""

import hmac
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from aci.application.delegate_task import ProfileDrivenAgentRuntime
from aci.application.protocols import (
    CapabilityRepository,
    ReleaseRepository,
    TaskRepository,
)
from aci.domain.agent.models import (
    AgentProfile,
    AgentTask,
    TaskArtifact,
    TaskMessage,
    advance_task,
    is_terminal,
)
from aci.domain.capability.errors import DomainError, ErrorCode

A2A_VERSION = "1.0"
#: JSON-RPC implementation-defined server error; auth is HTTP-level in A2A (401).
_UNAUTHORIZED = -32000
_STATE_WIRE = {
    "submitted": "TASK_STATE_SUBMITTED",
    "working": "TASK_STATE_WORKING",
    "needs_input": "TASK_STATE_INPUT_REQUIRED",
    "completed": "TASK_STATE_COMPLETED",
    "failed": "TASK_STATE_FAILED",
    "canceled": "TASK_STATE_CANCELED",
}


def _rpc_error(request_id: object, code: int, message: str, *, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status_code=status,
        headers={"A2A-Version": A2A_VERSION},
    )


def _rpc_result(request_id: object, result: dict[str, object]) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        headers={"A2A-Version": A2A_VERSION},
    )


class A2AGateway:
    """Translates the A2A wire surface onto the agent runtime + repositories."""

    def __init__(
        self,
        runtime: ProfileDrivenAgentRuntime,
        tasks: TaskRepository,
        releases: ReleaseRepository,
        capabilities: CapabilityRepository,
        profiles: dict[str, AgentProfile],
        *,
        service_url: str,
    ) -> None:
        self._runtime = runtime
        self._tasks = tasks
        self._releases = releases
        self._capabilities = capabilities
        self._profiles = profiles
        self._service_url = service_url

    # -- Agent Card (§8) --------------------------------------------------

    def agent_card(self) -> dict[str, object]:
        """Card skills project kind=agent capabilities with active production releases.

        Batch reads (one query per kind) — the card is fetched by every
        A2A client on discovery; the per-release loop was N+1.
        """
        releases = self._releases.list_channel("production", status="active")
        capabilities = {
            c.id: c
            for c in self._capabilities.get_capabilities([r.capability_id for r in releases])
        }
        versions = {
            v.capability_id: v
            for v in self._capabilities.get_versions(
                [(r.capability_id, r.version) for r in releases]
            )
        }
        skills: list[dict[str, object]] = []
        for release in releases:
            capability = capabilities.get(release.capability_id)
            if capability is None or capability.kind != "agent":
                continue
            version = versions.get(release.capability_id)
            skills.append(
                {
                    "id": capability.id,
                    "name": version.display_name if version else capability.id,
                    "description": version.description if version else "",
                    "tags": ["agent"],
                }
            )
        return {
            "name": "aci",
            "description": "AI Capability Infrastructure — delegated agent tasks",
            "supportedInterfaces": [
                {
                    "url": f"{self._service_url}/a2a",
                    "protocolBinding": "HTTP+JSON",
                    "protocolVersion": A2A_VERSION,
                }
            ],
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": skills,
        }

    # -- JSON-RPC (§9) ------------------------------------------------------

    def dispatch(self, request_id: object, method: str, params: dict[str, object]) -> JSONResponse:
        if method == "SendMessage":
            return self._send_message(request_id, params)
        if method == "GetTask":
            return self._get_task(request_id, params)
        if method == "CancelTask":
            return self._cancel_task(request_id, params)
        return _rpc_error(request_id, -32601, f"Method not found: {method}")

    def _send_message(self, request_id: object, params: dict[str, object]) -> JSONResponse:
        message = params.get("message")
        if not isinstance(message, dict):
            return _rpc_error(request_id, -32602, "params.message is required")
        text = self._text_of(message)
        if not text:
            return _rpc_error(request_id, -32602, "at least one non-empty text part is required")
        metadata = message.get("metadata")
        aci = metadata.get("aci") if isinstance(metadata, dict) else None
        if not isinstance(aci, dict):
            return _rpc_error(
                request_id, -32602, "message.metadata.aci {profileId, capabilityId} is required"
            )
        profile_id = aci.get("profileId")
        capability_id = aci.get("capabilityId")
        if not isinstance(profile_id, str) or not isinstance(capability_id, str):
            return _rpc_error(request_id, -32602, "profileId and capabilityId must be strings")
        profile = self._profiles.get(profile_id)
        if profile is None:
            return _rpc_error(request_id, -32602, f"unknown profile: {profile_id}")
        capability = self._capabilities.get_capability(capability_id)
        if capability is None or capability.kind != "agent":
            return _rpc_error(request_id, -32602, f"not an agent capability: {capability_id}")

        now = datetime.now(UTC)
        task = AgentTask(
            task_id=f"task-{uuid4().hex}",
            profile_id=profile_id,
            capability_id=capability_id,
            input_text=text,
            created_at=now,
            updated_at=now,
        )
        try:
            final = self._runtime.delegate(task, profile, now=now)
        except DomainError as exc:
            return self._domain_error(request_id, exc)
        return _rpc_result(request_id, {"task": self._task_wire(final.task_id)})

    def _get_task(self, request_id: object, params: dict[str, object]) -> JSONResponse:
        task_id = params.get("id")
        if not isinstance(task_id, str) or not task_id:
            return _rpc_error(request_id, -32602, "params.id is required")
        if self._tasks.get_task(task_id) is None:
            return _rpc_error(request_id, -32001, f"Task not found: {task_id}")
        history_length = params.get("historyLength")
        limit = history_length if isinstance(history_length, int) and history_length >= 0 else None
        return _rpc_result(request_id, {"task": self._task_wire(task_id, history_limit=limit)})

    def _cancel_task(self, request_id: object, params: dict[str, object]) -> JSONResponse:
        task_id = params.get("id")
        if not isinstance(task_id, str) or not task_id:
            return _rpc_error(request_id, -32602, "params.id is required")
        task = self._tasks.get_task(task_id)
        if task is None:
            return _rpc_error(request_id, -32001, f"Task not found: {task_id}")
        if is_terminal(task.status):
            return _rpc_error(request_id, -32002, f"Task not cancelable: {task_id}")
        now = datetime.now(UTC)
        canceled = advance_task(task, "canceled", now=now)
        self._tasks.put_task(canceled)
        return _rpc_result(request_id, {"task": self._task_wire(task_id)})

    # -- wire projection ----------------------------------------------------

    def _task_wire(self, task_id: str, *, history_limit: int | None = None) -> dict[str, object]:
        task = self._tasks.get_task(task_id)
        if task is None:  # pragma: no cover - caller just wrote it
            raise DomainError(ErrorCode.TASK_NOT_FOUND, task_id)
        history = [self._message_wire(m) for m in self._tasks.list_messages(task_id)]
        if history_limit is not None:
            history = history[-history_limit:] if history_limit > 0 else []
        return {
            "id": task.task_id,
            "contextId": task.capability_id,
            "status": {
                "state": _STATE_WIRE[task.status],
                "timestamp": task.updated_at.isoformat(),
            },
            "artifacts": [self._artifact_wire(a) for a in self._tasks.list_artifacts(task_id)],
            "history": history,
            "metadata": {"aci": {"profileId": task.profile_id, "capabilityId": task.capability_id}},
        }

    def _message_wire(self, message: TaskMessage) -> dict[str, object]:
        return {
            "messageId": message.message_id,
            "taskId": message.task_id,
            "role": "ROLE_AGENT" if message.author == "agent" else "ROLE_USER",
            "parts": [{"text": message.content}],
        }

    def _artifact_wire(self, artifact: TaskArtifact) -> dict[str, object]:
        return {
            "artifactId": artifact.artifact_id,
            "name": artifact.name,
            # Artifact bytes live in the content-addressed object store (§39);
            # the wire carries the digest, never raw bodies.
            "parts": [{"text": artifact.digest}],
        }

    def _text_of(self, message: dict[str, object]) -> str:
        parts = message.get("parts")
        if not isinstance(parts, list):
            return ""
        texts = [p.get("text") for p in parts if isinstance(p, dict)]
        return "\n".join(t for t in texts if isinstance(t, str) and t.strip())

    def _domain_error(self, request_id: object, exc: DomainError) -> JSONResponse:
        # The stable code only: DomainError text is raiser-interpolated
        # (paths, model output, internal ids) and never goes on the wire.
        if exc.code in (ErrorCode.TASK_NOT_FOUND,):
            return _rpc_error(request_id, -32001, exc.code.value)
        return _rpc_error(request_id, -32602, exc.code.value)


def _configured_token(request: Request, token: str | None) -> str:
    """Explicit router token, else the app container's ``a2a_token`` ("" = open)."""
    if token is not None:
        return token
    container = getattr(request.app.state, "container", None)
    return str(getattr(getattr(container, "settings", None), "a2a_token", "") or "")


def create_a2a_router(gateway: A2AGateway, *, token: str | None = None) -> APIRouter:
    """HTTP surface: the well-known Agent Card + one JSON-RPC endpoint.

    ``/a2a`` requires ``Authorization: Bearer <token>`` when a token is
    configured (``token`` here, else ``ACI_A2A_TOKEN`` via the app
    container); empty = UNAUTHENTICATED, localhost-only by deployment. The
    Agent Card stays public (discovery).
    """
    router = APIRouter()

    @router.get("/.well-known/agent-card.json")
    def agent_card() -> JSONResponse:
        return JSONResponse(gateway.agent_card(), headers={"A2A-Version": A2A_VERSION})

    @router.post("/a2a")
    async def rpc(request: Request) -> JSONResponse:
        expected = _configured_token(request, token)
        if expected:
            presented = request.headers.get("Authorization", "").encode()
            # Constant-time compare: != leaks the match position via timing.
            if not hmac.compare_digest(presented, f"Bearer {expected}".encode()):
                return _rpc_error(None, _UNAUTHORIZED, "Unauthorized", status=401)
        try:
            body = await request.json()
        except Exception:
            return _rpc_error(None, -32700, "Invalid JSON payload")
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "id" not in body:
            return _rpc_error(None, -32600, "Invalid Request: jsonrpc 2.0 + id required")
        version = request.headers.get("A2A-Version", A2A_VERSION)
        if version not in {A2A_VERSION, ""}:
            return _rpc_error(body["id"], -32009, f"A2A-Version not supported: {version}")
        method = body.get("method")
        params = body.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return _rpc_error(body["id"], -32602, "method and params are required")
        # dispatch is sync and may block for minutes (model HTTP call, DB):
        # run it off the event loop so one delegation cannot stall the server.
        return await run_in_threadpool(gateway.dispatch, body["id"], method, params)

    return router
