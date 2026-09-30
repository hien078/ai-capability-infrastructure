"""Bundle lookup (plan §43): pinned, immutable, replayable."""

from typing import Annotated

from fastapi import APIRouter, Depends

from aci.adapters.inbound.rest.auth import api_token, bearer_gate
from aci.adapters.inbound.rest.wiring import Container, get_container
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import CapabilityBundle

router = APIRouter(
    prefix="/v1/bundles", tags=["bundles"], dependencies=[Depends(bearer_gate(api_token))]
)


@router.get("/{bundle_id}")
def get_bundle(
    bundle_id: str, container: Annotated[Container, Depends(get_container)]
) -> CapabilityBundle:
    bundle = container.bundles.get_bundle(bundle_id)
    if bundle is None:
        raise DomainError(ErrorCode.BUNDLE_NOT_FOUND, f"unknown bundle {bundle_id}")
    return bundle
