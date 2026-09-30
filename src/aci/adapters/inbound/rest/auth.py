"""Bearer-token gates for the HTTP surfaces (opt-in, constant-time).

An empty configured token is UNAUTHENTICATED mode (localhost-only by
deployment assumption, backward compatible); a non-empty one requires
``Authorization: Bearer <token>`` on every gated route.
"""

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from aci.config import Settings


def tokens_match(presented: str, expected: str) -> bool:
    """Constant-time compare: a plain ``!=`` leaks the match position via timing."""
    return hmac.compare_digest(presented.encode(), expected.encode())


def verify_bearer(authorization: str | None, token: str) -> None:
    """Raise 401 unless ``authorization`` is ``Bearer <token>``; no-op if token is empty."""
    if not token:
        return
    if not tokens_match(authorization or "", f"Bearer {token}"):
        raise HTTPException(
            status_code=401,
            detail="invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def api_token(settings: Settings) -> str:
    return settings.api_token


def bearer_gate(get_token: Callable[[Settings], str]) -> Callable[..., None]:
    """Router dependency checking the token ``get_token`` reads off the
    injected Container's settings (so test container overrides apply)."""
    # Deferred: wiring imports the OpenCode catalog, which imports this module.
    from aci.adapters.inbound.rest.wiring import Container, get_container

    def require_bearer(
        container: Annotated[Container, Depends(get_container)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        verify_bearer(authorization, get_token(container.settings))

    return require_bearer
