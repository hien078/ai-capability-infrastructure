"""DomainError -> HTTP translation (plan §45).

Adapters own transport status codes; the domain only raises stable
machine-readable ErrorCode values. Every error body is
``{"error": {"code": ..., "message": ...}}``.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from aci.domain.capability.errors import DomainError, ErrorCode

_STATUS: dict[ErrorCode, int] = {
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
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.AUTHENTICATION_REQUIRED: 401,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.ROUTE_RUN_NOT_FOUND: 404,
    ErrorCode.BUNDLE_NOT_FOUND: 404,
    ErrorCode.OUTCOME_NOT_FOUND: 404,
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS.get(exc.code, 400),
            content={"error": {"code": exc.code.value, "message": str(exc)}},
        )
