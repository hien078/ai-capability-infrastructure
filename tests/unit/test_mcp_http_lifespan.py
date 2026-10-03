"""The in-app ``/mcp`` mount survives more than one app lifespan.

The SDK's StreamableHTTPSessionManager allows ``run()`` ONCE per instance.
The first platform integration (2026-10-03) built it once in create_app, so
the second lifespan on the same app (every extra TestClient / lifespan_context
— 5 integration tests) raised RuntimeError. Each lifespan must get a fresh
session manager, and /mcp must serve through the CURRENT one."""

from pathlib import Path

import anyio

from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.main import create_app


def _app(tmp_path: Path):  # type: ignore[no-untyped-def]
    # Unreachable DB on purpose: building the app and entering its lifespan
    # must not need the database.
    return create_app(
        Container(
            Settings(
                database_url="postgresql+psycopg://aci:aci@127.0.0.1:9/aci",
                object_store_root=str(tmp_path / "objects"),
            )
        )
    )


def test_app_lifespan_can_run_twice(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async def twice() -> None:
        async with app.router.lifespan_context(app):
            pass
        async with app.router.lifespan_context(app):
            pass

    anyio.run(twice)


def test_each_lifespan_gets_a_fresh_session_manager(tmp_path: Path) -> None:
    app = _app(tmp_path)
    seen: list[object] = []

    async def twice() -> None:
        for _ in range(2):
            async with app.router.lifespan_context(app):
                seen.append(app.state.mcp_session_manager)

    anyio.run(twice)
    assert seen[0] is not seen[1]
