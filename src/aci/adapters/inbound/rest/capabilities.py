"""Capability discovery + registry reads (plan §43, Phase 9)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from aci.adapters.inbound.rest.auth import api_token, bearer_gate
from aci.adapters.inbound.rest.schemas import CapabilityDetail, SearchRequest
from aci.adapters.inbound.rest.wiring import Container, get_container
from aci.domain.capability.models import SearchCapabilitiesQuery
from aci.domain.routing.models import CapabilitySearchResult, ResolvedVersion

router = APIRouter(
    prefix="/v1/capabilities", tags=["capabilities"], dependencies=[Depends(bearer_gate(api_token))]
)


@router.post("/search")
def search_capabilities(
    body: SearchRequest, container: Annotated[Container, Depends(get_container)]
) -> list[CapabilitySearchResult]:
    query = SearchCapabilitiesQuery(
        query=body.query,
        kinds=list(body.filters.kinds),
        domains=list(body.filters.domains),
        limit=body.limit,
    )
    return container.search_service.search(query)


@router.get("/{capability_id}")
def get_capability(
    capability_id: str, container: Annotated[Container, Depends(get_container)]
) -> CapabilityDetail:
    capability = container.resolve_service.get_capability(capability_id)
    versions = container.resolve_service.list_versions(capability_id)
    return CapabilityDetail(
        capability=capability.model_dump(mode="json"),
        versions=[v.model_dump(mode="json") for v in versions],
    )


@router.get("/{capability_id}/versions/{version}")
def resolve_version(
    capability_id: str,
    version: str,
    container: Annotated[Container, Depends(get_container)],
) -> ResolvedVersion:
    return container.resolve_service.resolve(capability_id, version)
