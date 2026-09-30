"""ACI_API_TOKEN exposure gate over the REST, catalog and console surfaces.

Token set: every gated route needs `Authorization: Bearer <token>` (the
console needs a login-issued session cookie instead). Token empty:
unauthenticated mode, nothing changes. /health stays open either way.
Requests are shaped to fail validation or miss rows after auth, so the
suite writes no telemetry; a down database only turns passes into 500s.
"""

import hmac
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest import auth
from aci.adapters.inbound.rest.ui import SESSION_COOKIE, session_value
from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.main import create_app

DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"
TOKEN = "s3cret-api-token"
ORIGIN = "http://testserver"

#: (method, path, json body) — one or more per gated router.
GATED: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/v1/routes", {}),
    ("GET", "/v1/routes/rr-missing", None),
    ("GET", "/v1/bundles/b-missing", None),
    ("POST", "/v1/outcomes", {}),
    ("POST", "/v1/evaluations", {}),
    ("POST", "/v1/capabilities/search", {"limit": -1}),
    ("GET", "/v1/capabilities/cap-missing", None),
    ("GET", "/v1/capabilities/cap-missing/versions/1.0.0", None),
    ("GET", "/opencode/skills/index.json", None),
    ("GET", "/opencode/skills/skill-missing/skill-missing.md", None),
]


def _client(tmp_path: Path, **settings: Any) -> TestClient:
    config = Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects"), **settings)
    return TestClient(create_app(Container(config)), raise_server_exceptions=False)


def _call(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None, **kwargs: Any
) -> int:
    return client.request(method, path, json=body, **kwargs).status_code


@pytest.mark.parametrize(("method", "path", "body"), GATED)
def test_token_set_requires_bearer(
    tmp_path: Path, method: str, path: str, body: dict[str, Any] | None
) -> None:
    client = _client(tmp_path, api_token=TOKEN)
    missing = client.request(method, path, json=body)
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert missing.json() == {"detail": "invalid or missing token"}
    for header in (f"Bearer {TOKEN}x", TOKEN, f"Basic {TOKEN}", "Bearer "):
        assert _call(client, method, path, body, headers={"Authorization": header}) == 401
    ok = _call(client, method, path, body, headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok != 401


@pytest.mark.parametrize(("method", "path", "body"), GATED)
def test_token_unset_is_unauthenticated_mode(
    tmp_path: Path, method: str, path: str, body: dict[str, Any] | None
) -> None:
    assert _call(_client(tmp_path), method, path, body) != 401


def test_health_stays_open(tmp_path: Path) -> None:
    assert _client(tmp_path, api_token=TOKEN).get("/health").status_code == 200


def test_agent_runs_keep_their_own_token(tmp_path: Path) -> None:
    """The shared gate reads ACI_AGENT_RUNS_TOKEN for /v1/agent-runs, not the api token."""
    client = _client(tmp_path, api_token=TOKEN, agent_runs_token="runs-token")
    api = {"Authorization": f"Bearer {TOKEN}"}
    runs = {"Authorization": "Bearer runs-token"}
    assert _call(client, "POST", "/v1/agent-runs", {}, headers=api) == 401
    assert _call(client, "POST", "/v1/agent-runs", {}, headers=runs) == 422


def test_bearer_compare_is_constant_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real: Callable[[bytes, bytes], bool] = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    client = _client(tmp_path, api_token=TOKEN)
    client.post("/v1/routes", json={}, headers={"Authorization": "Bearer nope"})
    assert (b"Bearer nope", f"Bearer {TOKEN}".encode()) in calls


# -- console (/ui): login cookie + same-origin unsafe methods ---------------------


def _login(client: TestClient, token: str = TOKEN, origin: str = ORIGIN) -> int:
    response = client.post(
        "/ui/login", data={"token": token}, headers={"Origin": origin}, follow_redirects=False
    )
    return response.status_code


def test_console_redirects_to_login_without_session(tmp_path: Path) -> None:
    client = _client(tmp_path, api_token=TOKEN)
    for path in ("/ui/", "/ui/routes", "/ui/corpus", "/ui/try", "/ui/routes/rr-x"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/login"
    assert client.post("/ui/try", data={"task": "x"}, headers={"Origin": ORIGIN}).status_code == 401
    # A Bearer header is not a console session.
    bearer = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/ui/try", headers=bearer, follow_redirects=False).status_code == 303
    assert client.get("/ui/login").status_code == 200


def test_console_login_issues_derived_strict_cookie(tmp_path: Path) -> None:
    client = _client(tmp_path, api_token=TOKEN)
    assert _login(client, token="wrong") == 401
    assert SESSION_COOKIE not in client.cookies
    assert _login(client, origin="http://evil.example") == 403
    assert SESSION_COOKIE not in client.cookies

    response = client.post(
        "/ui/login", data={"token": TOKEN}, headers={"Origin": ORIGIN}, follow_redirects=False
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert TOKEN not in cookie
    assert client.cookies[SESSION_COOKIE] == session_value(TOKEN)
    assert client.get("/ui/try").status_code == 200


def test_console_unsafe_methods_need_same_origin(tmp_path: Path) -> None:
    client = _client(tmp_path, api_token=TOKEN)
    assert _login(client) == 303
    for headers in ({}, {"Origin": "http://localhost:3000"}, {"Referer": "http://evil/ui/try"}):
        assert client.post("/ui/try", data={}, headers=headers).status_code == 403
    # Same origin passes the gate: the missing form field is the only failure.
    assert client.post("/ui/try", data={}, headers={"Origin": ORIGIN}).status_code == 422
    referer = {"Referer": f"{ORIGIN}/ui/try"}
    assert client.post("/ui/try", data={}, headers=referer).status_code == 422


def test_console_session_from_another_token_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path, api_token=TOKEN)
    client.cookies.set(SESSION_COOKIE, session_value("old-token"), path="/ui")
    assert client.get("/ui/try", follow_redirects=False).status_code == 303
    client.cookies.set(SESSION_COOKIE, TOKEN, path="/ui")
    assert client.get("/ui/try", follow_redirects=False).status_code == 303
    client.cookies.set(SESSION_COOKIE, session_value(TOKEN), path="/ui")
    assert client.get("/ui/try", follow_redirects=False).status_code == 200


def test_console_open_when_token_unset(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/ui/try").status_code == 200
    login = client.get("/ui/login", follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/ui/"
    assert client.post("/ui/try", data={}).status_code == 422
