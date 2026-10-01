"""DomainError -> HTTP translation (plan §45).

Adapters own transport status codes; the domain only raises stable
machine-readable ErrorCode values. Every error body is
``{"error": {"code": ..., "message": ...}}``.

Every ``ErrorCode`` is mapped deliberately — there is no fallback status, and
``tests/unit/test_rest_error_map.py`` fails if a new code is added without a
mapping. 4xx messages are client-facing by design (they describe the caller's
own request); 5xx messages are replaced by a fixed generic text and the raiser's
text is logged server-side only, because server/upstream failures interpolate
provider responses, internal ids, and paths the client must never see.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from aci.domain.capability.errors import DomainError, ErrorCode

logger = logging.getLogger(__name__)

_STATUS: dict[ErrorCode, int] = {
    # Registry / catalog
    ErrorCode.CAPABILITY_NOT_FOUND: 404,
    ErrorCode.CAPABILITY_VERSION_NOT_FOUND: 404,
    ErrorCode.CAPABILITY_ALREADY_EXISTS: 409,
    ErrorCode.CAPABILITY_NOT_ELIGIBLE: 422,
    ErrorCode.CAPABILITY_REVOKED: 404,
    ErrorCode.SKILL_PACKAGE_INVALID: 400,
    ErrorCode.BUNDLE_VALIDATION_FAILED: 422,
    ErrorCode.ROUTING_TIMEOUT: 504,
    ErrorCode.POLICY_DENIED: 403,
    ErrorCode.ARTIFACT_INTEGRITY_ERROR: 409,
    ErrorCode.CLIENT_INCOMPATIBLE: 422,
    # Access
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.AUTHENTICATION_REQUIRED: 401,
    ErrorCode.PERMISSION_DENIED: 403,
    # Telemetry lookups
    ErrorCode.ROUTE_RUN_NOT_FOUND: 404,
    ErrorCode.BUNDLE_NOT_FOUND: 404,
    ErrorCode.OUTCOME_NOT_FOUND: 404,
    # Agent tasks / executor
    ErrorCode.TASK_NOT_FOUND: 404,
    ErrorCode.TASK_TRANSITION_INVALID: 409,
    ErrorCode.EXECUTOR_CONTRACT_VIOLATION: 500,
    # Model provider (upstream)
    ErrorCode.MODEL_FAILURE: 502,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.MODEL_MALFORMED_OUTPUT: 502,
    # Workspaces
    ErrorCode.WORKSPACE_NOT_FOUND: 404,
    ErrorCode.WORKSPACE_PATH_INVALID: 422,
    ErrorCode.WORKSPACE_SNAPSHOT_NOT_FOUND: 404,
    # Tools
    ErrorCode.TOOL_NOT_FOUND: 404,
    ErrorCode.TOOL_ARGUMENT_INVALID: 422,
    ErrorCode.TOOL_EXECUTION_FAILED: 500,
    # Approval / authority
    ErrorCode.APPROVAL_REJECTED: 403,
    ErrorCode.APPROVAL_REPLAY_INVALID: 409,
    ErrorCode.AUTHORITY_EXPIRED: 403,
    # Checkpoint resume (migration 0019)
    ErrorCode.RUN_NOT_RESUMABLE: 409,
    ErrorCode.CHECKPOINT_CONSUMED: 409,
}

#: Fixed client-facing text for server/upstream failures, keyed by status.
_GENERIC_5XX: dict[int, str] = {
    502: "upstream model error",
    503: "upstream model unavailable",
    504: "request timed out",
}
_GENERIC_INTERNAL = "internal error"


def client_message(code: ErrorCode, raw: str) -> str:
    """The message a client may see for ``code``: raw for 4xx, generic for 5xx."""

    status = _STATUS[code]
    if status < 500:
        return raw
    return _GENERIC_5XX.get(status, _GENERIC_INTERNAL)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status = _STATUS[exc.code]
        if status >= 500:
            logger.warning(
                "domain error %s -> %d on %s %s: %s",
                exc.code.value,
                status,
                request.method,
                request.url.path,
                exc,
            )
        return JSONResponse(
            status_code=status,
            content={
                "error": {"code": exc.code.value, "message": client_message(exc.code, str(exc))}
            },
        )
