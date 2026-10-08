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
import threading
import time
from collections.abc import Callable, Iterator

import httpx
import pytest

from aci.adapters.outbound.model_provider.judge import (
    JEV_PROMPT_VERSION,
    OpenAICompatSkillJudge,
)
from aci.domain.routing.models import JudgeCandidate, JudgeVerdict

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
    # The §2.4 system prompt, verbatim shape (v3: classify first, then select).
    assert "Return ONLY JSON" in system
    assert "Select at most 2 entries" in system
    assert "First classify the task by the artifact it produces or changes" in system
    assert '"type": "<code|plan|review|schema|docs>"' in system
    assert '"necessity": "required"|"optional"' in system
    assert '"required" = this skill' in system
    assert "a second pick on a plan or docs task is ALWAYS optional" in system
    assert "Never add a skill from an unrelated activity as a filler second pick." in system
    assert "Prefer [] when none clearly apply" in system
    assert "Never output an id that is not in the candidate list." in system
    # The user message carries TASK + one CANDIDATES line per id.
    assert user.startswith("TASK:\nfix a bug\n\nCANDIDATES:\n")
    assert "- debugging: a skill description" in user
    assert "- tdd: a skill description" in user
    assert isinstance(JEV_PROMPT_VERSION, str)
    assert JEV_PROMPT_VERSION == "7"


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


# ---------- P3: a TOTAL wall-clock deadline per judge call (review 2026-10-07) ----------
#
# httpx ``timeout=`` is PER PHASE (connect/read/write each get timeout_s
# separately) — and MockTransport bypasses httpcore's phase timeouts
# entirely — so a delaying endpoint never trips any single phase and the
# call blocks for the handler's full delay. The judge call must return
# status ``timeout`` after ~timeout_s TOTAL, not wait the endpoint out.


def test_slow_endpoint_hits_the_total_deadline() -> None:
    """A handler that delays past timeout_s → status ``timeout`` at the
    deadline, not a blocked call that eventually returns ``ok``."""

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(0.6)
        return completion('{"selected": [], "reason": ""}')

    client = httpx.Client(transport=httpx.MockTransport(handler))
    judge = OpenAICompatSkillJudge(
        base_url=BASE,
        api_key=KEY,
        model=MODEL,
        reasoning_effort="low",
        timeout_s=0.15,
        client=client,
    )
    started = time.perf_counter()
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    elapsed = time.perf_counter() - started
    assert verdict.status == "timeout"
    assert verdict.selected == []
    # The deadline is ENFORCED (the verdict returns at ~timeout_s), not
    # merely detected after the fact: a 0.15s deadline must not wait out
    # the 0.6s handler.
    assert elapsed < 0.5, elapsed


def test_dribbling_endpoint_hits_the_total_deadline() -> None:
    """The per-phase gap the finding is about: an endpoint that yields a
    byte often enough to keep every read under timeout_s still overruns
    the TOTAL wall clock — the deadline, not a phase, stops it."""
    payload = json.dumps(
        {"choices": [{"message": {"content": '{"selected": [], "reason": ""}'}}]}
    ).encode()

    def slow_bytes() -> Iterator[bytes]:
        for i in range(0, len(payload), 8):
            time.sleep(0.05)
            yield payload[i : i + 8]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=slow_bytes())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    judge = OpenAICompatSkillJudge(
        base_url=BASE,
        api_key=KEY,
        model=MODEL,
        reasoning_effort="low",
        timeout_s=0.2,
        client=client,
    )
    started = time.perf_counter()
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    elapsed = time.perf_counter() - started
    assert verdict.status == "timeout"
    assert elapsed < 1.0, elapsed


def test_fast_endpoint_is_unaffected_by_the_deadline() -> None:
    """The deadline machinery must not perturb a normal call: a fast
    endpoint returns ``ok`` with the parsed selection."""
    judge, captured = make_judge(
        lambda request: completion('{"selected": ["debugging"], "reason": "r"}')
    )
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["debugging"]
    assert len(captured) == 1


# ---------- in-flight limiter (2026-10-08: abandoned workers keep slots) ----------
#
# DIAGNOSIS (measured on the shared gateway): _post_with_deadline ABANDONS an
# overrun worker but the HTTP request keeps running upstream (a client
# disconnect does not cancel it) — back-to-back calls then queue behind
# stalled requests and cascade into gateway fail-fast 503s. The limiter
# counts worker requests STILL RUNNING (abandoned ones included): the slot
# is held until the worker thread ends, whatever its outcome. A call over
# max_inflight answers status "error" / reason "judge busy" WITHOUT any
# HTTP call, and the reranker abstains as for any failure (§2.2.5).


def _await(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    """Poll until the condition holds (bounded — never a hung test)."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


def blocking_judge(
    handler,  # type: ignore[no-untyped-def]
    *,
    timeout_s: float,
    max_inflight: int,
) -> tuple[OpenAICompatSkillJudge, list[httpx.Request]]:
    """A judge over a capturing MockTransport with a controllable handler."""
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
        timeout_s=timeout_s,
        max_inflight=max_inflight,
        client=client,
    )
    return judge, captured


def test_max_inflight_zero_fails_closed_at_construction() -> None:
    """0 would mean "permanently busy" — refuse at wiring/startup."""
    with pytest.raises(ValueError, match="max_inflight"):
        OpenAICompatSkillJudge(base_url=BASE, api_key=KEY, model=MODEL, max_inflight=0)


def test_busy_verdict_when_inflight_at_cap() -> None:
    """max_inflight=1 with one call in flight: the next call answers
    ``error``/"judge busy" WITHOUT an HTTP call; the reranker abstains."""
    release = threading.Event()
    verdicts: list[JudgeVerdict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        release.wait(10)
        return completion('{"selected": [], "reason": ""}')

    judge, captured = blocking_judge(handler, timeout_s=5.0, max_inflight=1)
    assert judge.inflight == 0

    first = threading.Thread(target=lambda: verdicts.append(judge.judge("t1", candidates("a"), 2)))
    first.start()
    # Wait until the worker ENTERED the handler (captured) — the slot is
    # acquired before the thread runs, so inflight alone would race.
    _await(lambda: len(captured) == 1)
    assert judge.inflight == 1

    busy = judge.judge("t2", candidates("b"), 2)
    assert busy.status == "error"
    assert busy.reason == "judge busy"
    assert busy.selected == []
    assert busy.model_id == MODEL
    assert isinstance(busy.latency_ms, int)
    # NO HTTP call for the busy one — only the first request was sent.
    assert len(captured) == 1
    # The empty-candidates short-circuit never consumes a slot (no call).
    assert judge.judge("t3", [], 2).status == "ok"
    assert len(captured) == 1

    release.set()
    first.join(10)
    assert verdicts[0].status == "ok"
    assert judge.inflight == 0


def test_abandoned_timeout_worker_keeps_its_slot_until_it_finishes() -> None:
    """The measured cascade shape: an overrun call times out (the worker is
    ABANDONED but still running) — it must stay counted, so the next call
    answers ``judge busy`` until the worker actually ends."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(0.5)  # the first request stalls past the deadline
        return completion('{"selected": [], "reason": ""}')

    judge, _ = blocking_judge(handler, timeout_s=0.1, max_inflight=1)
    verdict = judge.judge("t", candidates("a"), 2)
    assert verdict.status == "timeout"
    assert judge.inflight == 1  # abandoned, still running upstream

    busy = judge.judge("t2", candidates("b"), 2)
    assert busy.status == "error"
    assert busy.reason == "judge busy"

    assert judge.wait_idle(10) is True  # the worker ended → slot released
    assert judge.inflight == 0
    ok = judge.judge("t3", candidates("c"), 2)
    assert ok.status == "ok"


def test_wait_idle_returns_false_while_a_worker_is_stalled() -> None:
    """wait_idle is honest: False while the abandoned worker still runs,
    True once it quiesces (the eval records the wait either way)."""

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(0.4)
        return completion('{"selected": [], "reason": ""}')

    judge, _ = blocking_judge(handler, timeout_s=0.05, max_inflight=1)
    assert judge.wait_idle(0.1) is True  # idle already
    verdict = judge.judge("t", candidates("a"), 2)
    assert verdict.status == "timeout"
    assert judge.wait_idle(0.05) is False  # still in flight
    assert judge.wait_idle(10) is True
    assert judge.inflight == 0


def test_inflight_slot_is_released_on_every_outcome() -> None:
    """Whatever the worker's outcome — transport error, non-200, invalid
    output, ok — the slot goes back when the worker ends."""

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    def overloaded(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="overloaded")

    def not_json(request: httpx.Request) -> httpx.Response:
        return completion("not json")

    def picks_a(request: httpx.Request) -> httpx.Response:
        return completion('{"selected": ["a"], "reason": "r"}')

    for handler, expected in (
        (refused, "error"),
        (overloaded, "error"),
        (not_json, "invalid_output"),
        (picks_a, "ok"),
    ):
        judge, _ = blocking_judge(handler, timeout_s=2.0, max_inflight=1)
        verdict = judge.judge("t", candidates("a"), 2)
        assert verdict.status == expected
        assert judge.inflight == 0


def test_max_inflight_two_admits_a_second_concurrent_call() -> None:
    """The cap is a CAP, not a mutex: with one abandoned worker running,
    a second call still goes through (in flight = 2), then settles to 1."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(0.4)  # the first request stalls past the deadline
        return completion('{"selected": [], "reason": ""}')

    judge, _ = blocking_judge(handler, timeout_s=0.1, max_inflight=2)
    first = judge.judge("t", candidates("a"), 2)
    assert first.status == "timeout"  # abandoned, slot held
    assert judge.inflight == 1
    second = judge.judge("t2", candidates("b"), 2)  # 1 < 2 → admitted
    assert second.status == "ok"
    assert judge.inflight == 1  # the abandoned worker still holds its slot
    assert judge.wait_idle(10) is True
    assert judge.inflight == 0


# ---------- exp 3: per-pick necessity parse (prompt v2 output shape) ----------
#
# v2 asks for {"selected": [{"id": ..., "necessity": "required"|"optional"}]}.
# "required" only when EXPLICITLY said (case-insensitive); a missing or
# garbage necessity is "optional" (not endorsed as required). v1-shape
# strings carry no necessity concept → "required" (old-shape output keeps
# v1 semantics instead of being silently dropped by the gate).


def test_v2_shape_parses_ids_and_necessities() -> None:
    payload = json.dumps(
        {
            "selected": [
                {"id": "debugging", "necessity": "required"},
                {"id": "tdd", "necessity": "optional"},
            ],
            "reason": "one clear pick",
        }
    )
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("fix a bug", candidates("debugging", "tdd"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["debugging", "tdd"]
    assert verdict.necessities == ["required", "optional"]


def test_necessity_required_is_case_insensitive() -> None:
    payload = json.dumps({"selected": [{"id": "debugging", "necessity": " Required "}]})
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.necessities == ["required"]


def test_necessity_garbage_maps_to_optional() -> None:
    """A synonym or garbage value is NOT "required" — only the exact word
    (case-insensitive) endorses a pick as required."""
    payload = json.dumps(
        {
            "selected": [
                {"id": "debugging", "necessity": "necessary"},
                {"id": "tdd", "necessity": 7},
            ]
        }
    )
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("fix a bug", candidates("debugging", "tdd"), 2)
    assert verdict.selected == ["debugging", "tdd"]
    assert verdict.necessities == ["optional", "optional"]


def test_v2_object_without_necessity_is_optional() -> None:
    payload = json.dumps({"selected": [{"id": "debugging"}]})
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.selected == ["debugging"]
    assert verdict.necessities == ["optional"]


def test_v1_string_entries_are_required() -> None:
    """Old-shape strings keep v1 semantics: the gate must not drop them."""
    payload = json.dumps({"selected": ["debugging", "tdd"], "reason": "v1 shape"})
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("fix a bug", candidates("debugging", "tdd"), 2)
    assert verdict.selected == ["debugging", "tdd"]
    assert verdict.necessities == ["required", "required"]


def test_mixed_shape_strings_required_objects_by_their_value() -> None:
    payload = json.dumps({"selected": ["debugging", {"id": "tdd", "necessity": "optional"}]})
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("fix a bug", candidates("debugging", "tdd"), 2)
    assert verdict.selected == ["debugging", "tdd"]
    assert verdict.necessities == ["required", "optional"]


def test_v2_object_without_id_is_invalid() -> None:
    payload = json.dumps({"selected": [{"necessity": "required"}, {"id": "debugging"}]})
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["debugging"]
    assert verdict.invalid_ids == 1


def test_necessities_align_with_selected_after_parse_drops() -> None:
    """Position alignment survives parse-level drops: the surviving id's
    necessity is the one reported FOR THAT id."""
    payload = json.dumps(
        {
            "selected": [
                {"id": "debugging", "necessity": "optional"},
                {"necessity": "required"},  # no id → parse-level drop
                {"id": "tdd", "necessity": "required"},
            ]
        }
    )
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("fix a bug", candidates("debugging", "tdd"), 2)
    assert verdict.selected == ["debugging", "tdd"]
    assert verdict.necessities == ["optional", "required"]
    assert verdict.invalid_ids == 1


# ---------- exp 4: task-type classification rides in the reason ----------


def test_v3_type_is_folded_into_the_reason() -> None:
    """The judge's classification is telemetry: "[plan] <reason>"."""
    payload = json.dumps(
        {
            "type": "plan",
            "selected": [{"id": "writing-plans", "necessity": "required"}],
            "reason": "a plan deliverable",
        }
    )
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("write a plan", candidates("writing-plans"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["writing-plans"]
    assert verdict.reason == "[plan] a plan deliverable"


def test_v3_missing_type_leaves_the_reason_untouched() -> None:
    """A judge that omits "type" (or emits a non-string) is NOT a parse
    failure — the type is display-only."""
    payload = json.dumps({"selected": [], "reason": "none apply"})
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("t", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.reason == "none apply"
    payload2 = json.dumps({"type": 7, "selected": [], "reason": "r"})
    judge2, _ = make_judge(lambda request: completion(payload2))
    verdict2 = judge2.judge("t", candidates("debugging"), 2)
    assert verdict2.reason == "r"


def test_v3_oversized_type_is_bounded() -> None:
    """A hallucinated long type cannot crowd out the reason's tail."""
    payload = json.dumps({"type": "x" * 900, "selected": [], "reason": "tail"})
    judge, _ = make_judge(lambda request: completion(payload))
    verdict = judge.judge("t", candidates("debugging"), 2)
    assert verdict.reason.startswith("[")
    assert verdict.reason.endswith("tail")
    assert len(verdict.reason) <= 500
