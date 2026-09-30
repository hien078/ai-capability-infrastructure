"""The REST DomainError map is total and 5xx bodies never echo raiser text (plan §45)."""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest.errors import _STATUS, register_error_handlers
from aci.domain.capability.errors import DomainError, ErrorCode

SECRET_MESSAGE = "failed reading /home/secret/path with key sk-test-abc123"

SERVER_CODES = sorted(code for code, status in _STATUS.items() if status >= 500)
CLIENT_CODES = sorted(code for code, status in _STATUS.items() if status < 500)


def test_every_error_code_is_mapped() -> None:
    assert set(ErrorCode) - set(_STATUS) == set()


def test_every_status_is_an_error_status() -> None:
    for code, status in _STATUS.items():
        assert 400 <= status <= 599, code


def test_server_and_upstream_codes_are_5xx() -> None:
    for code in (
        ErrorCode.MODEL_FAILURE,
        ErrorCode.MODEL_UNAVAILABLE,
        ErrorCode.MODEL_MALFORMED_OUTPUT,
        ErrorCode.EXECUTOR_CONTRACT_VIOLATION,
        ErrorCode.TOOL_EXECUTION_FAILED,
        ErrorCode.ROUTING_TIMEOUT,
    ):
        assert _STATUS[code] >= 500, code


def _client(code: ErrorCode, message: str) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise DomainError(code, message)

    return TestClient(app)


@pytest.mark.parametrize("code", SERVER_CODES, ids=lambda c: c.value)
def test_5xx_body_never_echoes_raiser_text(
    code: ErrorCode, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="aci.adapters.inbound.rest.errors"):
        response = _client(code, SECRET_MESSAGE).get("/boom")

    assert response.status_code == _STATUS[code]
    body = response.json()
    assert body["error"]["code"] == code.value
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert "/home/secret/path" not in response.text
    assert "sk-test" not in response.text
    # The real text is kept server-side for operators.
    assert SECRET_MESSAGE in caplog.text


@pytest.mark.parametrize("code", CLIENT_CODES, ids=lambda c: c.value)
def test_4xx_body_carries_the_client_facing_message(code: ErrorCode) -> None:
    response = _client(code, "unknown profile: nope").get("/boom")

    assert response.status_code == _STATUS[code]
    assert response.json() == {"error": {"code": code.value, "message": "unknown profile: nope"}}
