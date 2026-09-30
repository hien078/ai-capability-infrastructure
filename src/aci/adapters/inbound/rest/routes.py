"""Route runtime: run the §14 pipeline, read telemetry back (§36)."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request

from aci.adapters.inbound.rest.auth import api_token, bearer_gate
from aci.adapters.inbound.rest.schemas import RouteRequest
from aci.adapters.inbound.rest.wiring import Container, get_container
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import RouteCapabilitiesCommand
from aci.domain.policy.models import ClientDescriptor, ProtocolDescriptor, RequestContext
from aci.domain.routing.models import RouteResult, RouteRun

router = APIRouter(
    prefix="/v1/routes", tags=["routes"], dependencies=[Depends(bearer_gate(api_token))]
)


@router.post("", status_code=201)
def route_capabilities(
    body: RouteRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> RouteResult:
    """Edge builds the envelope (§11.1) + command (§11.3); the service runs §14."""
    task_context = body.context.to_domain()
    request_context = RequestContext(
        request_id=f"req_{uuid4().hex}",
        trace_id=request.headers.get("x-trace-id", f"trc_{uuid4().hex}"),
        principal_id=body.principal_id,
        organization_id=body.organization_id,
        workspace_id=body.workspace_id,
        client=ClientDescriptor(type=body.client_type, version=body.client_version),
        protocol=ProtocolDescriptor(type="rest", version="1"),
    )
    command = RouteCapabilitiesCommand(
        task_text=body.task.text,
        context=task_context,
        max_items=body.constraints.max_items,
        max_context_tokens=body.constraints.max_context_tokens,
        allowed_kinds=list(body.constraints.allowed_kinds),
    )
    return container.route_service.route(
        command,
        request_context.to_routing_context(task_context),
        request=request_context,
    )


@router.get("/{route_run_id}")
def get_route_run(
    route_run_id: str, container: Annotated[Container, Depends(get_container)]
) -> RouteRun:
    run = container.route_runs.get_route_run(route_run_id)
    if run is None:
        raise DomainError(ErrorCode.ROUTE_RUN_NOT_FOUND, f"unknown route run {route_run_id}")
    return run
