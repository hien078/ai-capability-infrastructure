"""OpenAICompatSkillJudge unit tests (docs/plans/jev-reranker.md §2.5, task 3).

httpx ``MockTransport`` only — NO network, NO live model, NO real key. The
one-wire-call contract: request shape (model, temperature 0,
reasoning_effort, stream false), fenced/bare JSON parse, failure mapping
(5xx → error, timeout → timeout, bad JSON → invalid_output), bounded
reason, and the key-leakage boundary (the key never appears in any
exception text or log record).
"""

import json
import logging

import httpx
import pytest

from aci.adapters.outbound.model_provider.judge import (
    JEV_PROMPT_VERSION,
    OpenAICompatSkillJudge,
)
from aci.domain.routing.models import JudgeCandidate

BASE = "http://judge.local/v1"
KEY = "sk-test-SECRET-KEY"
MODEL = "OneNexus/glm-5.3"


def completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def make_judge(
    handler,  # type: ignore[no-untyped-def]
) -> tuple[OpenAICompatSkillJudge, list[httpx.Request]]:
    """A judge over a capturing MockTransport (the test owns the client)."""
    captured: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(transport))
    judge = OpenAICompatSkillJudge(
        base_url=BASE,
        api_key=KEY,
        model=MODEL,
        reasoning_effort="low",
        timeout_s=8.0,
        client=client,
    )
    return judge, captured


def candidates(*ids: str, text: str = "a skill description") -> list[JudgeCandidate]:
    return [JudgeCandidate(capability_id=i, document_text=text) for i in ids]


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


def test_request_shape_is_the_spec_contract() -> None:
    judge, captured = make_judge(lambda request: completion('{"selected": [], "reason": "none"}'))
    verdict = judge.judge("fix a bug", candidates("debugging", "tdd"), 2)
    assert verdict.status == "ok"
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE}/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    body = body_of(request)
    assert body["model"] == MODEL
    assert body["temperature"] == 0
    assert body["stream"] is False
    assert body["reasoning_effort"] == "low"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    system = body["messages"][0]["content"]
    user = body["messages"][1]["content"]
    # The spec §2.4 system prompt, verbatim shape.
    assert "Return ONLY JSON" in system
    assert "Select at most 2 ids" in system
    assert "Prefer [] when none clearly apply" in system
    assert "Never output an id that is not in the candidate list." in system
    # The user message carries TASK + one CANDIDATES line per id.
    assert user.startswith("TASK:\nfix a bug\n\nCANDIDATES:\n")
    assert "- debugging: a skill description" in user
    assert "- tdd: a skill description" in user
    assert isinstance(JEV_PROMPT_VERSION, str)


def test_bare_json_parse() -> None:
    judge, _ = make_judge(
        lambda request: completion('{"selected": ["debugging"], "reason": "helps here"}')
    )
    verdict = judge.judge("fix a bug", candidates("debugging", "tdd"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["debugging"]
    assert verdict.reason == "helps here"
    assert verdict.model_id == MODEL
    assert isinstance(verdict.latency_ms, int)
    assert verdict.latency_ms >= 0


def test_fenced_json_parse() -> None:
    judge, _ = make_judge(
        lambda request: completion('```json\n{"selected": ["tdd"], "reason": "r"}\n```')
    )
    verdict = judge.judge("fix a bug", candidates("debugging", "tdd"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["tdd"]


def test_fenced_json_without_language_tag_parse() -> None:
    judge, _ = make_judge(lambda request: completion('```\n{"selected": [], "reason": ""}\n```'))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == []


def test_prose_prefixed_json_parse() -> None:
    content = 'Here is the JSON you asked for: {"selected": [], "reason": "x"}'
    judge, _ = make_judge(lambda request: completion(content))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == []


def test_hybrid_sse_body_parse() -> None:
    """The local gateway gotcha (§77): one whole JSON completion + a trailing
    ``data: [DONE]`` with NO per-event prefix, even for stream:false."""
    inner = '{"selected": ["debugging"], "reason": "r"}'
    raw = json.dumps({"choices": [{"message": {"content": inner}}]}) + " data: [DONE]"
    judge, _ = make_judge(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=raw)
    )
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["debugging"]


def test_invalid_json_maps_to_invalid_output() -> None:
    judge, _ = make_judge(lambda request: completion("not json at all, sorry"))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "invalid_output"
    assert verdict.selected == []


def test_wrong_shape_maps_to_invalid_output() -> None:
    judge, _ = make_judge(lambda request: completion('{"pick": ["debugging"]}'))
    assert judge.judge("t", candidates("debugging"), 1).status == "invalid_output"
    judge2, _ = make_judge(lambda request: completion('{"selected": "debugging", "reason": "r"}'))
    assert judge2.judge("t", candidates("debugging"), 1).status == "invalid_output"
    judge3, _ = make_judge(lambda request: completion('["debugging"]'))
    assert judge3.judge("t", candidates("debugging"), 1).status == "invalid_output"


def test_non_string_selected_entries_dropped_and_counted() -> None:
    judge, _ = make_judge(
        lambda request: completion('{"selected": ["debugging", 7, null], "reason": "r"}')
    )
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["debugging"]
    assert verdict.invalid_ids == 2


def test_http_5xx_maps_to_error() -> None:
    judge, _ = make_judge(lambda request: httpx.Response(503, text="overloaded"))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "error"
    assert verdict.selected == []
    assert "503" in verdict.reason


def test_http_4xx_maps_to_error() -> None:
    judge, _ = make_judge(lambda request: httpx.Response(401, text="bad key"))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "error"


def test_timeout_maps_to_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    judge, _ = make_judge(handler)
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "timeout"
    assert verdict.selected == []


def test_transport_error_maps_to_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    judge, _ = make_judge(handler)
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "error"
    assert verdict.selected == []


def test_empty_completion_maps_to_invalid_output() -> None:
    judge, _ = make_judge(lambda request: completion("   "))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "invalid_output"


def test_reason_is_truncated_to_500() -> None:
    long_reason = "x" * 900
    payload = json.dumps({"selected": [], "reason": long_reason})
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert len(verdict.reason) == 500


def test_task_text_is_truncated_head_and_tail() -> None:
    judge, captured = make_judge(lambda request: completion('{"selected": [], "reason": ""}'))
    task = "H" * 3000 + "M" * 2000 + "T" * 1000
    judge.judge(task, candidates("debugging"), 2)
    user = body_of(captured[0])["messages"][1]["content"]
    assert user.startswith("TASK:\n" + "H" * 3000)
    assert user.endswith("T" * 1000 + "\n\nCANDIDATES:\n- debugging: a skill description")
    # The dropped middle never reaches the wire.
    assert "M" * 100 not in user


def test_candidate_line_is_bounded_to_400_chars() -> None:
    judge, captured = make_judge(lambda request: completion('{"selected": [], "reason": ""}'))
    judge.judge("fix a bug", candidates("debugging", text="y" * 2000), 2)
    user = body_of(captured[0])["messages"][1]["content"]
    line = user.split("CANDIDATES:\n", 1)[1]
    assert line == "- debugging: " + "y" * 400  # document_text ≤ 400 chars (§2.4)


def test_multiline_document_text_stays_one_line() -> None:
    judge, captured = make_judge(lambda request: completion('{"selected": [], "reason": ""}'))
    judge.judge("fix a bug", candidates("debugging", text="line one\nline two\nline three"), 2)
    user = body_of(captured[0])["messages"][1]["content"]
    lines = user.split("CANDIDATES:\n", 1)[1].splitlines()
    assert lines == ["- debugging: line one line two line three"]


def test_empty_candidates_short_circuits_without_a_call() -> None:
    judge, captured = make_judge(lambda request: completion('{"selected": [], "reason": ""}'))
    verdict = judge.judge("fix a bug", [], 2)
    assert verdict.status == "ok"
    assert verdict.selected == []
    assert captured == []


def test_api_key_never_leaks_into_logs_or_verdicts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """§2.3: never logs the key or the full prompt — on ANY failure path."""

    def handler(request: httpx.Request) -> httpx.Response:
        # A server that echoes headers back in the error body (worst case).
        return httpx.Response(500, text=f"server saw {request.headers['Authorization']}")

    with caplog.at_level(logging.DEBUG, logger="aci.adapters.outbound.model_provider.judge"):
        judge, _ = make_judge(handler)
        verdict = judge.judge("fix a bug", candidates("debugging"), 2)
        assert verdict.status == "error"
        assert KEY not in verdict.reason
        assert KEY not in verdict.model_id
        for record in caplog.records:
            assert KEY not in record.getMessage(), record.getMessage()
            assert "fix a bug" not in record.getMessage(), "the prompt leaked into logs"


def test_no_authorization_header_without_key() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: completion('{"selected": [], "reason": ""}'))
    )
    judge = OpenAICompatSkillJudge(
        base_url=BASE,
        api_key="",
        model=MODEL,
        reasoning_effort="low",
        timeout_s=8.0,
        client=client,
    )
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "ok"


# ---------- P1: judge() NEVER raises (independent review 2026-10-07) ----------
#
# The SSE fallback called ``.get`` on whatever a ``data:`` line decoded to;
# the caller tuple caught KeyError/IndexError/TypeError/ValueError but NOT
# AttributeError, so list/string events (and a string delta) escaped judge()
# entirely — JevReranker.rerank has no try/except, so an unhandled 500 on
# /v1/routes and an aborted eval. Same for a non-ASCII API key
# (UnicodeEncodeError — whose .object holds the Bearer header) and any other
# unexpected exception. Contract: judge() maps EVERY failure to a status.


def sse(*data_lines: str) -> httpx.Response:
    """An SSE-shaped body: one ``data:`` line per argument."""
    body = "".join(f"data: {line}\n\n" for line in data_lines)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)


def test_sse_data_line_decoding_to_a_list_maps_to_invalid_output() -> None:
    """``data: [1]`` decodes to a list — ``.get`` on it raised AttributeError."""
    judge, _ = make_judge(lambda request: sse("[1]"))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "invalid_output"
    assert verdict.selected == []


def test_sse_data_line_decoding_to_a_string_maps_to_invalid_output() -> None:
    """``data: "x"`` decodes to a str — ``.get`` on it raised AttributeError."""
    judge, _ = make_judge(lambda request: sse('"x"'))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "invalid_output"
    assert verdict.selected == []


def test_sse_chunk_delta_string_maps_to_invalid_output() -> None:
    """``{"choices":[{"delta":"s"}]}``: delta is a str, ``.get("content")`` on
    it raised AttributeError."""
    judge, _ = make_judge(
        lambda request: sse('{"object": "chat.completion.chunk", "choices": [{"delta": "s"}]}')
    )
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "invalid_output"
    assert verdict.selected == []


def test_nonascii_api_key_maps_to_error_without_leaking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-ASCII key makes header ENCODING raise UnicodeEncodeError — whose
    ``.object`` holds the Bearer header. The verdict carries the TYPE NAME
    only; the key never reaches the reason or any log line."""
    key = "sk-" + "\U0001f600" * 3
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: completion('{"selected": [], "reason": ""}'))
    )
    judge = OpenAICompatSkillJudge(
        base_url=BASE,
        api_key=key,
        model=MODEL,
        reasoning_effort="low",
        timeout_s=8.0,
        client=client,
    )
    with caplog.at_level(logging.DEBUG, logger="aci.adapters.outbound.model_provider.judge"):
        verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "error"
    assert verdict.selected == []
    assert "UnicodeEncodeError" in verdict.reason
    assert key not in verdict.reason
    for record in caplog.records:
        assert key not in record.getMessage(), record.getMessage()


def test_unexpected_exception_maps_to_error_with_type_name_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Any unexpected exception → status ``error``; the reason carries the
    exception TYPE NAME only — never str(exc), which can hold the URL, the
    key, or response bodies."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"boom {KEY} http://judge.local/v1")

    judge, _ = make_judge(handler)
    with caplog.at_level(logging.DEBUG, logger="aci.adapters.outbound.model_provider.judge"):
        verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "error"
    assert verdict.selected == []
    assert "RuntimeError" in verdict.reason
    assert "boom" not in verdict.reason
    assert KEY not in verdict.reason
    for record in caplog.records:
        assert KEY not in record.getMessage(), record.getMessage()
        assert "boom" not in record.getMessage(), record.getMessage()


def test_base_url_scheme_is_validated_at_construction() -> None:
    """Fail closed at wiring/startup: a malformed ACI_JEV_BASE_URL never
    reaches a request (P1)."""
    for bad in ("ftp://judge.local/v1", "not a url", "//judge.local/v1", ""):
        with pytest.raises(ValueError, match="http/https"):
            OpenAICompatSkillJudge(base_url=bad, api_key=KEY, model=MODEL)
