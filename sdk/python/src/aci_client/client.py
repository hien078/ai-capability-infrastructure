"""ACIClient — thin typed REST client for ACI (plan §31, ADR-007).

A convenience client only: it wraps the public REST surface and adds what
§31 says an SDK owns — auth headers, serialization, typed models,
deadlines, safe retries for idempotent GETs (§31.1), and consistent error
types carrying the stable §45 codes. It never imports ``aci`` internals,
so it works against a remote server.

Retry policy (§31.1): only connection failures on idempotent GETs are
retried (``_GET_ATTEMPTS``). POSTs are never auto-retried — ``route``
persists telemetry, ``report_outcome`` and ``run_agent`` have side effects;
a retry would double them. Timeouts are never retried (deadline semantics).

Auth: one bearer token for the REST/catalog surface; ``agent_runs_token``
(defaults to ``token``) for ``/v1/agent-runs`` — a deployment may gate the
two surfaces with different tokens (``ACI_API_TOKEN`` vs
``ACI_AGENT_RUNS_TOKEN``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from aci_client._version import __version__
from aci_client.errors import (
    ACIConnectionError,
    ACIError,
    ACIValidationError,
    SkillIntegrityError,
)
from aci_client.models import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    AgentRun,
    ArtifactFile,
    Budget,
    CapabilitySearchResult,
    OutcomeEvidence,
    OutcomeVerdict,
    ResolvedVersion,
    RouteResult,
    SkillContent,
    SkillFile,
    TaskContext,
)

#: §31.1 — idempotent GETs may retry connection failures (never POSTs).
_GET_ATTEMPTS = 3

#: Synchronous agent runs are long (the kernel executes tools + a model
#: loop before answering); the per-call default for ``run_agent``.
_AGENT_RUN_TIMEOUT = 600.0

_CATALOG_INDEX = "/opencode/skills/index.json"


def _quote_segment(value: str) -> str:
    """URL-encode one path segment (ids: no ``/`` allowed through)."""
    return quote(value, safe="")


def _quote_path(value: str) -> str:
    """URL-encode a multi-segment catalog path, keeping ``/`` separators."""
    return quote(value, safe="/")


def _decode_text(data: bytes) -> str | None:
    """UTF-8 decode, or ``None`` for binary content (never raises)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _validation_message(details: Any) -> str:
    """Summarize a FastAPI ``detail`` list without dumping raw bodies."""
    if not isinstance(details, list):
        return "request failed validation"
    parts: list[str] = []
    for item in details:
        if isinstance(item, dict):
            loc = ".".join(str(p) for p in item.get("loc", []))
            parts.append(f"{loc}: {item.get('msg', 'invalid')}")
    return "; ".join(parts) if parts else "request failed validation"


def _error_from_response(response: httpx.Response) -> ACIError:
    """Map an HTTP error response to a typed error (§45 body preferred)."""
    status = response.status_code
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and "code" in error:
            # The §45 shape: {"error": {"code": ..., "message": ...}}.
            return ACIError(
                str(error.get("code")),
                str(error.get("message", "")),
                status_code=status,
            )
        detail = body.get("detail")
        if isinstance(detail, list):
            # FastAPI request-validation 422 — never reached domain logic.
            return ACIValidationError(_validation_message(detail), detail)
        if isinstance(detail, str):
            # The bearer gate's 401 ({"detail": "invalid or missing token"})
            # and any other non-§45 FastAPI error body.
            code = "AUTHENTICATION_REQUIRED" if status == 401 else "HTTP_ERROR"
            return ACIError(code, detail, status_code=status)
    return ACIError("HTTP_ERROR", f"HTTP {status}", status_code=status)


def _check_response(response: httpx.Response) -> httpx.Response:
    if response.status_code < 400:
        return response
    raise _error_from_response(response)


def _route_payload(
    *,
    task_text: str,
    context: TaskContext | None,
    max_items: int,
    max_context_tokens: int,
    allowed_kinds: Sequence[str],
    principal_id: str,
    organization_id: str | None,
    workspace_id: str | None,
    client_type: str,
    client_version: str | None,
) -> dict[str, Any]:
    return {
        "task": {"text": task_text},
        "context": (context or TaskContext()).model_dump(),
        "constraints": {
            "max_items": max_items,
            "max_context_tokens": max_context_tokens,
            "allowed_kinds": list(allowed_kinds),
        },
        "principal_id": principal_id,
        "organization_id": organization_id,
        "workspace_id": workspace_id,
        "client_type": client_type,
        "client_version": client_version,
    }


def _search_payload(
    *, query: str, kinds: Sequence[str], domains: Sequence[str], limit: int
) -> dict[str, Any]:
    return {
        "query": query,
        "filters": {"kinds": list(kinds), "domains": list(domains)},
        "limit": limit,
    }


def _outcome_payload(
    *,
    route_run_id: str,
    bundle_id: str,
    verdicts: Sequence[OutcomeVerdict],
    tests_before: dict[str, Any] | None,
    tests_after: dict[str, Any] | None,
    latency_ms: int | None,
    client_status: str | None,
    lint_passed: bool | None,
    build_passed: bool | None,
    changed_files: int | None,
    tool_calls: int | None,
    human_corrected: bool | None,
    input_tokens: int | None,
    output_tokens: int | None,
    estimated_usd: float | None,
) -> dict[str, Any]:
    return {
        "route_run_id": route_run_id,
        "bundle_id": bundle_id,
        "verdicts": [v.model_dump() for v in verdicts],
        "tests_before": tests_before or {},
        "tests_after": tests_after or {},
        "latency_ms": latency_ms,
        "client_status": client_status,
        "lint_passed": lint_passed,
        "build_passed": build_passed,
        "changed_files": changed_files,
        "tool_calls": tool_calls,
        "human_corrected": human_corrected,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_usd": estimated_usd,
    }


def _agent_run_payload(
    *,
    objective: str,
    global_context: str,
    constraints: Sequence[str],
    acceptance_criteria: Sequence[str],
    requested_profile: str,
    max_turns: int | None,
    budget: Budget | None,
    workspace: str | None,
    verification_command: Sequence[str] | None,
    write_scopes: Sequence[str] | None,
    command_prefixes: Sequence[str] | None,
    approval_required_tools: Sequence[str] | None,
    preload_capabilities: bool | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "objective": objective,
        "global_context": global_context,
        "constraints": list(constraints),
        "acceptance_criteria": list(acceptance_criteria),
        "requested_profile": requested_profile,
    }
    if max_turns is not None:
        payload["max_turns"] = max_turns
    if budget is not None:
        payload["budget"] = budget.model_dump()
    if workspace is not None:
        payload["workspace"] = workspace
    if verification_command is not None:
        payload["verification_command"] = list(verification_command)
    if write_scopes is not None:
        payload["write_scopes"] = list(write_scopes)
    if command_prefixes is not None:
        payload["command_prefixes"] = list(command_prefixes)
    if approval_required_tools is not None:
        payload["approval_required_tools"] = list(approval_required_tools)
    if preload_capabilities is not None:
        payload["preload_capabilities"] = preload_capabilities
    return payload


def _find_catalog_entry(index: dict[str, Any], capability_id: str) -> dict[str, Any]:
    """Locate one skill in the OpenCode catalog index (production skills)."""
    skills = index.get("skills")
    if isinstance(skills, list):
        for entry in skills:
            if isinstance(entry, dict) and entry.get("name") == capability_id:
                return entry
    raise ACIError(
        "CAPABILITY_NOT_FOUND",
        f"skill {capability_id!r} is not in the production catalog",
        status_code=404,
    )


def _skill_content(
    capability_id: str,
    version: str,
    resolved: ResolvedVersion,
    catalog_files: Sequence[str],
    fetch: Callable[[str], bytes],
) -> SkillContent:
    """Shared get_skill body: verify every advertised file against the
    immutable artifact manifest (§39) before returning any content.

    ``fetch`` receives the CATALOG path (``<id>.md`` alias for the entry
    file) and returns the served bytes; the alias→canonical mapping mirrors
    the server's own ``CatalogProjection.read_file`` translation.
    """
    artifact = resolved.artifact
    if artifact is None:
        raise ACIError(
            "CAPABILITY_NOT_FOUND",
            f"{capability_id}@{version} has no artifact",
            status_code=404,
        )
    by_path = {f.path: f for f in artifact.files}
    files: list[SkillFile] = []
    skill_md: str | None = None
    for catalog_path in catalog_files:
        artifact_path = "SKILL.md" if catalog_path == f"{capability_id}.md" else catalog_path
        manifest_file = by_path.get(artifact_path)
        if manifest_file is None:
            # Fail closed: the catalog advertised a file the immutable
            # manifest knows nothing about — server inconsistency, never
            # served unverified content.
            raise ACIError(
                "ARTIFACT_INTEGRITY_ERROR",
                f"catalog advertises {catalog_path!r} but the artifact manifest "
                f"for {capability_id}@{version} has no {artifact_path!r}",
            )
        data = fetch(catalog_path)
        digest = hashlib.sha256(data).hexdigest()
        if digest != manifest_file.sha256:
            raise SkillIntegrityError(
                f"{capability_id}/{artifact_path}: sha256 mismatch "
                f"(expected {manifest_file.sha256}, got {digest})"
            )
        text = _decode_text(data)
        if artifact_path == "SKILL.md":
            if text is None:
                raise ACIError(
                    "SKILL_PACKAGE_INVALID",
                    f"{capability_id}@{version}: SKILL.md is not UTF-8 text",
                )
            skill_md = text
        files.append(
            SkillFile(
                path=artifact_path,
                sha256=manifest_file.sha256,
                size_bytes=manifest_file.size_bytes,
                text=text,
            )
        )
    if skill_md is None:
        raise ACIError(
            "SKILL_PACKAGE_INVALID",
            f"{capability_id}@{version} advertises no entry file (SKILL.md)",
        )
    return SkillContent(
        capability_id=capability_id,
        version=version,
        package_digest=artifact.package_digest,
        skill_md=skill_md,
        files=files,
    )


class _ClientCore:
    """Shared config + request-argument assembly for both client flavors."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        agent_runs_token: str | None = None,
        timeout: float | httpx.Timeout | None = 30.0,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
        client_type: str = "aci-client-python",
        client_version: str | None = None,
    ) -> None:
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be an http(s) URL, got {base_url!r}")
        self._base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self._token = token
        self._agent_runs_token = agent_runs_token if agent_runs_token is not None else token
        self._timeout = timeout
        self._transport = transport
        self._client_type = client_type
        self._client_version = client_version if client_version is not None else __version__

    def _headers(self, *, agent_runs: bool) -> dict[str, str]:
        token = self._agent_runs_token if agent_runs else self._token
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _host(self) -> str:
        """Host:port for error messages — never the userinfo or full URL."""
        url = httpx.URL(self._base_url)
        return url.host or "server"

    def _http_kwargs(self) -> dict[str, Any]:
        return {
            "base_url": self._base_url,
            "timeout": self._timeout,
            "transport": self._transport,
            "headers": {"Accept": "application/json"},
        }


class ACIClient(_ClientCore):
    """Synchronous typed client for the ACI REST + catalog surface.

    Usage::

        from aci_client import ACIClient

        client = ACIClient("http://localhost:8000", token="...")
        bundle = client.route("fix the flaky login test")
        for item in bundle.bundle.items:
            skill = client.get_skill(item.capability_id, version=item.version)
            print(skill.skill_md)
    """

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)
        self._http = httpx.Client(**self._http_kwargs())

    # -- transport -----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | httpx.Timeout | None = None,
        agent_runs: bool = False,
        retry_get: bool = False,
    ) -> httpx.Response:
        """One request with §31.1 semantics: retry connection failures on
        idempotent GETs only; map every error response to a typed error."""
        headers = self._headers(agent_runs=agent_runs)
        attempts = _GET_ATTEMPTS if retry_get else 1
        last_exc: httpx.ConnectError | None = None
        for _ in range(attempts):
            try:
                response = self._http.request(
                    method, path, json=json, headers=headers, timeout=timeout
                )
            except httpx.ConnectError as exc:
                last_exc = exc
                continue
            return _check_response(response)
        raise ACIConnectionError(
            f"cannot reach the ACI server at {self._host()}: {last_exc}"
        ) from last_exc

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._http.close()

    def __enter__(self) -> ACIClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- routing (§14 pipeline behind one call) ------------------------------

    def route(
        self,
        task_text: str,
        *,
        context: TaskContext | None = None,
        max_items: int = 5,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        allowed_kinds: Sequence[str] = ("skill",),
        principal_id: str = "anonymous",
        organization_id: str | None = None,
        workspace_id: str | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> RouteResult:
        """Route a task through the §14 pipeline → pinned bundle (§11.3).

        Never auto-retried (§31.1): a route persists telemetry on the server.
        """
        payload = _route_payload(
            task_text=task_text,
            context=context,
            max_items=max_items,
            max_context_tokens=max_context_tokens,
            allowed_kinds=allowed_kinds,
            principal_id=principal_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            client_type=self._client_type,
            client_version=self._client_version,
        )
        response = self._request("POST", "/v1/routes", json=payload, timeout=timeout)
        return RouteResult.model_validate(response.json())

    # -- discovery ------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        kinds: Sequence[str] = ("skill",),
        domains: Sequence[str] = (),
        limit: int = 20,
        timeout: float | httpx.Timeout | None = None,
    ) -> list[CapabilitySearchResult]:
        """Discovery-only search (§11.2): production-active metadata."""
        payload = _search_payload(query=query, kinds=kinds, domains=domains, limit=limit)
        response = self._request("POST", "/v1/capabilities/search", json=payload, timeout=timeout)
        return [CapabilitySearchResult.model_validate(hit) for hit in response.json()]

    # -- skills (catalog + resolve, digest-verified) --------------------------

    def get_skill(
        self,
        capability_id: str,
        *,
        version: str | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> SkillContent:
        """SKILL.md text + every package file, sha256-verified (§39).

        ``version`` defaults to the active production release (the catalog
        is a projection of live release state, so that is the only version
        it can serve). Every advertised file is fetched through the catalog
        and verified against the immutable artifact manifest BEFORE it is
        returned — a tampered blob raises :class:`SkillIntegrityError`.
        """

        def fetch(catalog_path: str) -> bytes:
            response = self._request(
                "GET",
                f"/opencode/skills/{_quote_segment(capability_id)}/{_quote_path(catalog_path)}",
                retry_get=True,
                timeout=timeout,
            )
            return response.content

        return self._get_skill(capability_id, version=version, fetch=fetch)

    def _get_skill(
        self,
        capability_id: str,
        *,
        version: str | None,
        fetch: Callable[[str], bytes],
        timeout: float | httpx.Timeout | None = None,
    ) -> SkillContent:
        index = self._request("GET", _CATALOG_INDEX, retry_get=True, timeout=timeout).json()
        entry = _find_catalog_entry(index, capability_id)
        active_version = str(entry.get("version", ""))
        if version is not None and version != active_version:
            raise ACIError(
                "CAPABILITY_VERSION_NOT_FOUND",
                f"{capability_id}@{version} is not the active production release "
                f"({active_version})",
                status_code=404,
            )
        resolved_body = self._request(
            "GET",
            f"/v1/capabilities/{_quote_segment(capability_id)}/versions/"
            f"{_quote_segment(active_version)}",
            retry_get=True,
            timeout=timeout,
        ).json()
        resolved = ResolvedVersion.model_validate(resolved_body)
        catalog_files = [str(p) for p in entry.get("files", [])]
        return _skill_content(capability_id, active_version, resolved, catalog_files, fetch)

    # -- outcomes (§33) -------------------------------------------------------

    def report_outcome(
        self,
        route_run_id: str,
        bundle_id: str,
        verdicts: Sequence[OutcomeVerdict],
        *,
        tests_before: dict[str, Any] | None = None,
        tests_after: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        client_status: str | None = None,
        lint_passed: bool | None = None,
        build_passed: bool | None = None,
        changed_files: int | None = None,
        tool_calls: int | None = None,
        human_corrected: bool | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_usd: float | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> OutcomeEvidence:
        """Attach multi-source evidence to a routed bundle (§33, ADR-010).

        Never auto-retried (§31.1): an outcome is an append-only record.
        """
        payload = _outcome_payload(
            route_run_id=route_run_id,
            bundle_id=bundle_id,
            verdicts=verdicts,
            tests_before=tests_before,
            tests_after=tests_after,
            latency_ms=latency_ms,
            client_status=client_status,
            lint_passed=lint_passed,
            build_passed=build_passed,
            changed_files=changed_files,
            tool_calls=tool_calls,
            human_corrected=human_corrected,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_usd=estimated_usd,
        )
        response = self._request("POST", "/v1/outcomes", json=payload, timeout=timeout)
        return OutcomeEvidence.model_validate(response.json())

    # -- agent runs (HarnessKernel surface, ADR-014) --------------------------

    def run_agent(
        self,
        objective: str,
        *,
        global_context: str = "",
        constraints: Sequence[str] = (),
        acceptance_criteria: Sequence[str] = (),
        requested_profile: str = "coder",
        max_turns: int | None = None,
        budget: Budget | None = None,
        workspace: str | None = None,
        verification_command: Sequence[str] | None = None,
        write_scopes: Sequence[str] | None = None,
        command_prefixes: Sequence[str] | None = None,
        approval_required_tools: Sequence[str] | None = None,
        preload_capabilities: bool | None = None,
        timeout: float | httpx.Timeout | None = _AGENT_RUN_TIMEOUT,
    ) -> AgentRun:
        """Start a synchronous HarnessKernel run (POST /v1/agent-runs).

        The POST blocks until the run reaches a terminal state; the default
        ``timeout`` is therefore much larger than the client default.
        Never auto-retried (§31.1): a run executes real work.
        """
        payload = _agent_run_payload(
            objective=objective,
            global_context=global_context,
            constraints=constraints,
            acceptance_criteria=acceptance_criteria,
            requested_profile=requested_profile,
            max_turns=max_turns,
            budget=budget,
            workspace=workspace,
            verification_command=verification_command,
            write_scopes=write_scopes,
            command_prefixes=command_prefixes,
            approval_required_tools=approval_required_tools,
            preload_capabilities=preload_capabilities,
        )
        response = self._request(
            "POST", "/v1/agent-runs", json=payload, timeout=timeout, agent_runs=True
        )
        return AgentRun.model_validate(response.json())

    def get_run(self, run_id: str, *, timeout: float | httpx.Timeout | None = None) -> AgentRun:
        """Read one agent run's compact state (§29A — never the transcript)."""
        response = self._request(
            "GET",
            f"/v1/agent-runs/{_quote_segment(run_id)}",
            agent_runs=True,
            retry_get=True,
            timeout=timeout,
        )
        return AgentRun.model_validate(response.json())

    def cancel_run(self, run_id: str, *, timeout: float | httpx.Timeout | None = None) -> bool:
        """Request cancellation; returns whether the run was cancelled."""
        response = self._request(
            "POST",
            f"/v1/agent-runs/{_quote_segment(run_id)}/cancel",
            agent_runs=True,
            timeout=timeout,
        )
        return bool(response.json().get("cancelled"))


class AsyncACIClient(_ClientCore):
    """Async twin of :class:`ACIClient` (same wire surface, same errors)."""

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)
        self._http = httpx.AsyncClient(**self._http_kwargs())

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | httpx.Timeout | None = None,
        agent_runs: bool = False,
        retry_get: bool = False,
    ) -> httpx.Response:
        headers = self._headers(agent_runs=agent_runs)
        attempts = _GET_ATTEMPTS if retry_get else 1
        last_exc: httpx.ConnectError | None = None
        for _ in range(attempts):
            try:
                response = await self._http.request(
                    method, path, json=json, headers=headers, timeout=timeout
                )
            except httpx.ConnectError as exc:
                last_exc = exc
                continue
            return _check_response(response)
        raise ACIConnectionError(
            f"cannot reach the ACI server at {self._host()}: {last_exc}"
        ) from last_exc

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncACIClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def route(
        self,
        task_text: str,
        *,
        context: TaskContext | None = None,
        max_items: int = 5,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        allowed_kinds: Sequence[str] = ("skill",),
        principal_id: str = "anonymous",
        organization_id: str | None = None,
        workspace_id: str | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> RouteResult:
        payload = _route_payload(
            task_text=task_text,
            context=context,
            max_items=max_items,
            max_context_tokens=max_context_tokens,
            allowed_kinds=allowed_kinds,
            principal_id=principal_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            client_type=self._client_type,
            client_version=self._client_version,
        )
        response = await self._request("POST", "/v1/routes", json=payload, timeout=timeout)
        return RouteResult.model_validate(response.json())

    async def search(
        self,
        query: str,
        *,
        kinds: Sequence[str] = ("skill",),
        domains: Sequence[str] = (),
        limit: int = 20,
        timeout: float | httpx.Timeout | None = None,
    ) -> list[CapabilitySearchResult]:
        payload = _search_payload(query=query, kinds=kinds, domains=domains, limit=limit)
        response = await self._request(
            "POST", "/v1/capabilities/search", json=payload, timeout=timeout
        )
        return [CapabilitySearchResult.model_validate(hit) for hit in response.json()]

    async def get_skill(
        self,
        capability_id: str,
        *,
        version: str | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> SkillContent:
        index = (await self._request("GET", _CATALOG_INDEX, retry_get=True, timeout=timeout)).json()
        entry = _find_catalog_entry(index, capability_id)
        active_version = str(entry.get("version", ""))
        if version is not None and version != active_version:
            raise ACIError(
                "CAPABILITY_VERSION_NOT_FOUND",
                f"{capability_id}@{version} is not the active production release "
                f"({active_version})",
                status_code=404,
            )
        resolved_body = (
            await self._request(
                "GET",
                f"/v1/capabilities/{_quote_segment(capability_id)}/versions/"
                f"{_quote_segment(active_version)}",
                retry_get=True,
                timeout=timeout,
            )
        ).json()
        resolved = ResolvedVersion.model_validate(resolved_body)
        catalog_files = [str(p) for p in entry.get("files", [])]

        async def fetch(catalog_path: str) -> bytes:
            response = await self._request(
                "GET",
                f"/opencode/skills/{_quote_segment(capability_id)}/{_quote_path(catalog_path)}",
                retry_get=True,
                timeout=timeout,
            )
            return response.content

        # The verification loop is synchronous over awaited bytes.
        artifact = resolved.artifact
        if artifact is None:
            raise ACIError(
                "CAPABILITY_NOT_FOUND",
                f"{capability_id}@{active_version} has no artifact",
                status_code=404,
            )
        by_path: dict[str, ArtifactFile] = {f.path: f for f in artifact.files}
        files: list[SkillFile] = []
        skill_md: str | None = None
        for catalog_path in catalog_files:
            artifact_path = "SKILL.md" if catalog_path == f"{capability_id}.md" else catalog_path
            manifest_file = by_path.get(artifact_path)
            if manifest_file is None:
                raise ACIError(
                    "ARTIFACT_INTEGRITY_ERROR",
                    f"catalog advertises {catalog_path!r} but the artifact manifest "
                    f"for {capability_id}@{active_version} has no {artifact_path!r}",
                )
            data = await fetch(catalog_path)
            digest = hashlib.sha256(data).hexdigest()
            if digest != manifest_file.sha256:
                raise SkillIntegrityError(
                    f"{capability_id}/{artifact_path}: sha256 mismatch "
                    f"(expected {manifest_file.sha256}, got {digest})"
                )
            text = _decode_text(data)
            if artifact_path == "SKILL.md":
                if text is None:
                    raise ACIError(
                        "SKILL_PACKAGE_INVALID",
                        f"{capability_id}@{active_version}: SKILL.md is not UTF-8 text",
                    )
                skill_md = text
            files.append(
                SkillFile(
                    path=artifact_path,
                    sha256=manifest_file.sha256,
                    size_bytes=manifest_file.size_bytes,
                    text=text,
                )
            )
        if skill_md is None:
            raise ACIError(
                "SKILL_PACKAGE_INVALID",
                f"{capability_id}@{active_version} advertises no entry file (SKILL.md)",
            )
        return SkillContent(
            capability_id=capability_id,
            version=active_version,
            package_digest=artifact.package_digest,
            skill_md=skill_md,
            files=files,
        )

    async def report_outcome(
        self,
        route_run_id: str,
        bundle_id: str,
        verdicts: Sequence[OutcomeVerdict],
        *,
        tests_before: dict[str, Any] | None = None,
        tests_after: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        client_status: str | None = None,
        lint_passed: bool | None = None,
        build_passed: bool | None = None,
        changed_files: int | None = None,
        tool_calls: int | None = None,
        human_corrected: bool | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_usd: float | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> OutcomeEvidence:
        payload = _outcome_payload(
            route_run_id=route_run_id,
            bundle_id=bundle_id,
            verdicts=verdicts,
            tests_before=tests_before,
            tests_after=tests_after,
            latency_ms=latency_ms,
            client_status=client_status,
            lint_passed=lint_passed,
            build_passed=build_passed,
            changed_files=changed_files,
            tool_calls=tool_calls,
            human_corrected=human_corrected,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_usd=estimated_usd,
        )
        response = await self._request("POST", "/v1/outcomes", json=payload, timeout=timeout)
        return OutcomeEvidence.model_validate(response.json())

    async def run_agent(
        self,
        objective: str,
        *,
        global_context: str = "",
        constraints: Sequence[str] = (),
        acceptance_criteria: Sequence[str] = (),
        requested_profile: str = "coder",
        max_turns: int | None = None,
        budget: Budget | None = None,
        workspace: str | None = None,
        verification_command: Sequence[str] | None = None,
        write_scopes: Sequence[str] | None = None,
        command_prefixes: Sequence[str] | None = None,
        approval_required_tools: Sequence[str] | None = None,
        preload_capabilities: bool | None = None,
        timeout: float | httpx.Timeout | None = _AGENT_RUN_TIMEOUT,
    ) -> AgentRun:
        payload = _agent_run_payload(
            objective=objective,
            global_context=global_context,
            constraints=constraints,
            acceptance_criteria=acceptance_criteria,
            requested_profile=requested_profile,
            max_turns=max_turns,
            budget=budget,
            workspace=workspace,
            verification_command=verification_command,
            write_scopes=write_scopes,
            command_prefixes=command_prefixes,
            approval_required_tools=approval_required_tools,
            preload_capabilities=preload_capabilities,
        )
        response = await self._request(
            "POST", "/v1/agent-runs", json=payload, timeout=timeout, agent_runs=True
        )
        return AgentRun.model_validate(response.json())

    async def get_run(
        self, run_id: str, *, timeout: float | httpx.Timeout | None = None
    ) -> AgentRun:
        response = await self._request(
            "GET",
            f"/v1/agent-runs/{_quote_segment(run_id)}",
            agent_runs=True,
            retry_get=True,
            timeout=timeout,
        )
        return AgentRun.model_validate(response.json())

    async def cancel_run(
        self, run_id: str, *, timeout: float | httpx.Timeout | None = None
    ) -> bool:
        response = await self._request(
            "POST",
            f"/v1/agent-runs/{_quote_segment(run_id)}/cancel",
            agent_runs=True,
            timeout=timeout,
        )
        return bool(response.json().get("cancelled"))
