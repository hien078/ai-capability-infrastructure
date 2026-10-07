"""OpenAI-compatible skill judge — the ``SkillJudge`` plug-in (JEV §2.3).

docs/plans/jev-reranker.md. ONE ``POST {base}/chat/completions`` per
``judge()`` call: the §2.4 system prompt (versioned as
``JEV_PROMPT_VERSION``) + a user message of TASK + one CANDIDATES line per
id. The judge sees ONLY trusted routing document text (§17.1) — the
reranker built ``JudgeCandidate`` from sanitized metadata, so raw
``SKILL.md`` bodies and artifact contents are structurally unreachable
here (pinned by tests/security/test_jev_boundaries.py).

Every failure maps to a ``JudgeVerdict`` status instead of raising, so the
reranker can abstain (ADR-008) rather than crash a route:

- ``httpx.TimeoutException`` → ``timeout``;
- transport errors / non-200 → ``error`` (the STATUS CODE only is logged —
  never the response body, which can echo request headers);
- non-JSON / wrong-shape output → ``invalid_output``.

P1 (independent review 2026-10-07): ``judge()`` NEVER raises — an unexpected
exception of ANY other kind (a non-ASCII API key making header encoding raise
``UnicodeEncodeError`` whose ``.object`` holds the Bearer header, a malformed
base URL, an exotic response shape) maps to ``error`` with a reason that
carries the exception TYPE NAME only — never ``str(exc)``, which can hold the
URL, the key, or response bodies. The base URL scheme is validated at
construction so a malformed ``ACI_JEV_BASE_URL`` fails closed at
wiring/startup, before any request. ``JevReranker.rerank`` additionally wraps
the judge call defensively (a raising judge abstains).

P3 (same review): ``timeout_s`` is a TOTAL wall-clock deadline per judge call
— ``httpx`` ``timeout=`` alone is per phase, so a dribbling endpoint could
overrun it indefinitely; the request runs in a worker thread joined for
``timeout_s`` and an overrun is a ``timeout`` verdict.

The API key never appears in any log line or verdict text, and the full
prompt is never logged (§2.3; pinned by test).
"""

import json
import logging
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from aci.domain.routing.models import JudgeCandidate, JudgeStatus, JudgeVerdict

#: Version of the judge prompt (§2.4). Bump when the system message changes
#: — telemetry can then separate prompt eras.
JEV_PROMPT_VERSION = "1"

#: Task text bound for the wire (§2.4): head 3000 + tail 1000 chars.
_TASK_HEAD = 3000
_TASK_TAIL = 1000
#: One CANDIDATES line bound (§2.4): ``- <id>: <document_text ≤ 400 chars>``.
_LINE_MAX = 400
#: JudgeVerdict.reason bound (domain model enforces ≤500; truncate before).
_REASON_MAX = 500

_SYSTEM_TEMPLATE = (
    "You select skills for a coding agent. You get a TASK and CANDIDATE skills (id: description).\n"
    'Return ONLY JSON: {"selected": [<ids>], "reason": "<one sentence>"}.\n'
    "Select at most {max_select} ids, and only skills that DIRECTLY help "
    "with this task as written.\n"
    "A skill is NOT relevant just because it shares words "
    "(security, token, audit, CI, review, rate limit).\n"
    "Prefer [] when none clearly apply — an empty selection is a correct answer.\n"
    "Never output an id that is not in the candidate list."
)

log = logging.getLogger(__name__)


def _task_for_wire(task_text: str) -> str:
    """Head + tail bound: the middle of a long prompt never reaches the wire."""
    if len(task_text) <= _TASK_HEAD + _TASK_TAIL:
        return task_text
    return task_text[:_TASK_HEAD] + "\n[…truncated…]\n" + task_text[-_TASK_TAIL:]


def _candidate_line(candidate: JudgeCandidate) -> str:
    """One wire line per candidate: id + whitespace-collapsed trusted text."""
    text = " ".join(candidate.document_text.split())
    return f"- {candidate.capability_id}: {text[:_LINE_MAX]}"


def _user_message(task_text: str, candidates: list[JudgeCandidate]) -> str:
    lines = "\n".join(_candidate_line(c) for c in candidates)
    return f"TASK:\n{_task_for_wire(task_text)}\n\nCANDIDATES:\n{lines}"


def _strip_fences(text: str) -> str:
    """Drop ``` / ```json fences around a JSON payload."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    body = "\n".join(lines[1:])
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


def _decode_payload(content: str) -> dict[str, Any] | None:
    """Best-effort JSON object extraction: bare, fenced, prose-prefixed."""
    for attempt in (_strip_fences(content), content):
        try:
            data = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    # Prose prefix ("Here is the JSON: {...}"): decode from the first brace.
    start = content.find("{")
    if start >= 0:
        try:
            data, _ = json.JSONDecoder().raw_decode(content[start:])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    return None


class OpenAICompatSkillJudge:
    """``SkillJudge`` over an OpenAI-compatible ``/chat/completions`` endpoint.

    The endpoint is deployment infrastructure (the user's gateway, a local
    inference server); the platform gains no model authority from it (§76).
    ``client`` is injectable so tests drive a ``httpx.MockTransport`` —
    without it a fresh client is created per call and closed after (the
    executor.py pattern).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        reasoning_effort: str = "low",
        timeout_s: float = 8.0,
        client: httpx.Client | None = None,
    ) -> None:
        # P1: fail closed at construction (= wiring/startup, both wirings
        # build the adapter there) on a malformed endpoint — a bad
        # ACI_JEV_BASE_URL must never reach a request.
        scheme = urlparse(base_url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"jev base_url must be an http/https URL, got scheme {scheme!r} in {base_url!r}"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._timeout_s = timeout_s
        self._client = client

    def judge(
        self, task_text: str, candidates: list[JudgeCandidate], max_select: int
    ) -> JudgeVerdict:
        """One selection call; every failure is a status, never an exception.

        P1: this method is the NEVER-RAISE boundary — an unexpected exception
        from ANY layer (URL parsing, header encoding, an exotic response
        shape) becomes an ``error`` verdict instead of an unhandled 500 on
        /v1/routes.
        """
        if not candidates:
            # Nothing to select from: skip the call entirely (the reranker
            # already short-circuits empty input; this guards direct use).
            return JudgeVerdict(status="ok", reason="no candidates", model_id=self._model)

        started = time.perf_counter()
        try:
            return self._judge(task_text, candidates, max_select, started)
        except Exception as exc:  # noqa: BLE001 — the contract IS never-raise
            # TYPE NAME ONLY: str(exc) can carry the URL, response bodies,
            # or (UnicodeEncodeError) the Authorization header bytes.
            log.warning("judge call failed unexpectedly: %s", type(exc).__name__)
            return self._verdict("error", f"judge call failed: {type(exc).__name__}", started)

    def _judge(
        self,
        task_text: str,
        candidates: list[JudgeCandidate],
        max_select: int,
        started: float,
    ) -> JudgeVerdict:
        """The call itself; the EXPECTED failure classes map to their statuses
        here, everything else falls to the never-raise boundary above."""
        request_body = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_TEMPLATE.replace("{max_select}", str(max_select)),
                },
                {"role": "user", "content": _user_message(task_text, candidates)},
            ],
            "temperature": 0,
            "stream": False,
            "reasoning_effort": self._reasoning_effort,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

        try:
            response = self._post_with_deadline(request_body, headers)
        except httpx.TimeoutException:
            # Type name only: the exception text can carry the URL, and the
            # verdict must stay free of anything request-shaped.
            return self._verdict("timeout", "judge request timed out", started)
        except httpx.HTTPError:
            return self._verdict("error", "judge endpoint unreachable", started)
        latency_ms = self._latency_ms(started)

        if response.status_code != 200:
            # Status code ONLY: a server error body can echo request headers
            # (incl. Authorization) — it never reaches logs or the verdict.
            log.warning("judge endpoint returned %s", response.status_code)
            return self._verdict(
                "error", f"judge endpoint returned {response.status_code}", started
            )

        try:
            content = self._completion_content(response)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            # P1: AttributeError joined the tuple — an SSE ``data:`` line can
            # decode to a non-dict (list/string) or carry a string delta, and
            # ``.get`` on those raised past the old tuple. Body-shape problems
            # are ``invalid_output``; the type name is logged, never str(exc).
            log.warning("malformed judge response: %s", type(exc).__name__)
            return self._verdict("invalid_output", "judge response had no content", started)

        data = _decode_payload(content)
        if data is None:
            return self._verdict("invalid_output", "judge output was not JSON", started)
        selected = data.get("selected")
        if not isinstance(selected, list):
            return self._verdict("invalid_output", 'judge JSON has no "selected" list', started)
        ids: list[str] = []
        invalid = 0
        for entry in selected:
            if isinstance(entry, str) and entry:
                ids.append(entry)
            else:
                invalid += 1  # dropped at parse level; the reranker counts it
        reason = data.get("reason")
        reason_text = reason[:_REASON_MAX] if isinstance(reason, str) else ""
        return JudgeVerdict(
            status="ok",
            selected=ids,
            reason=reason_text,
            model_id=self._model,
            latency_ms=latency_ms,
            invalid_ids=invalid,
        )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _latency_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def _post_with_deadline(
        self, request_body: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        """POST bounded by a TOTAL wall-clock deadline (P3).

        ``timeout=`` is PER PHASE — connect/read/write each get
        ``timeout_s`` separately — so an endpoint that dribbles bytes (or a
        transport with no phase timeouts at all) can run many multiples
        over ``timeout_s`` without ever tripping a single phase. The
        request runs in a daemon worker thread joined for ``timeout_s``:
        an overrun raises ``httpx.ReadTimeout`` on THIS thread (→ status
        ``timeout``) while the worker finishes in the background and its
        outcome is discarded. The per-phase ``timeout`` still applies
        inside the worker, so a hung connect/read raises
        ``TimeoutException`` there and is re-raised here unchanged.
        """
        box: list[httpx.Response | BaseException] = []

        def run() -> None:
            client = self._client or httpx.Client()
            try:
                box.append(
                    client.post(
                        f"{self._base_url}/chat/completions",
                        json=request_body,
                        headers=headers,
                        timeout=self._timeout_s,
                    )
                )
            except BaseException as exc:  # noqa: BLE001 — re-raised on the caller thread
                box.append(exc)
            finally:
                if self._client is None:
                    client.close()

        worker = threading.Thread(target=run, daemon=True, name="aci-jev-judge")
        worker.start()
        worker.join(self._timeout_s)
        if worker.is_alive():
            raise httpx.ReadTimeout(f"judge call exceeded the total deadline of {self._timeout_s}s")
        if not box:  # the worker died without an outcome (thread machinery)
            raise RuntimeError("judge worker ended without an outcome")
        outcome = box[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def _verdict(self, status: JudgeStatus, reason: str, started: float) -> JudgeVerdict:
        """A failure verdict: nothing selected, honest reason, no secrets."""
        return JudgeVerdict(
            status=status,
            reason=reason[:_REASON_MAX],
            model_id=self._model,
            latency_ms=self._latency_ms(started),
        )

    def _completion_content(self, response: httpx.Response) -> str:
        """Extract the completion text from a JSON or SSE-shaped body.

        Some OpenAI-compatible gateways stream unconditionally (§77: verify
        wire shapes at implementation time — verified live 2026-09-28 in
        executor.py): the body arrives as ``text/event-stream`` with either
        the whole ``chat.completion`` as one event or chunk deltas. Both are
        handled; plain JSON is the fast path. (Duplicated from
        executor.py rather than refactored: that file is outside this
        change's allowed surface.)
        """
        raw = response.text
        try:
            data, _ = json.JSONDecoder().raw_decode(raw.lstrip())
        except json.JSONDecodeError:
            return self._content_from_sse(raw)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"completion object has no message content: {exc}") from exc
        return content if isinstance(content, str) else ""

    @staticmethod
    def _content_from_sse(raw: str) -> str:
        """Extract the completion text from an SSE body.

        P1: a ``data:`` line can decode to ANY JSON value — a list
        (``data: [1]``), a string (``data: "x"``), a chunk whose ``delta`` is
        a string — and ``.get`` on those raises ``AttributeError``. The
        caller maps that to ``invalid_output`` (a body-shape problem), so
        this method stays simple: no per-shape isinstance ladder.
        """
        chunks: list[str] = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue  # e.g. ``data: [1]`` / ``data: "x"`` — not an event
            if event.get("object") == "chat.completion":
                content = event["choices"][0]["message"]["content"]
                return content if isinstance(content, str) else ""
            if event.get("object") == "chat.completion.chunk":
                delta = event["choices"][0]["delta"].get("content")
                if delta:
                    chunks.append(str(delta))
        return "".join(chunks)
