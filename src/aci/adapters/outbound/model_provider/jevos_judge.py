"""Jevos skill judge — the ``SkillJudge`` plug-in over the Jev typed-decision API.

docs/plans/jev-reranker.md §6 (addendum, worker task 8): jevos
(github.com/feder-cr/jev, MIT) is an open-source CPU-only implementation of
TypeSafe's Jev typed-decision API. ONE ``POST {base}/v1/systemone`` per
``judge()`` call: a single ``choice`` question (key ``skill``) whose criteria
are the candidate ids — each mapped to its trusted routing document text,
≤160 chars — plus a ``none`` option. The judge sees ONLY trusted text
(§17.1): the reranker built ``JudgeCandidate`` from sanitized metadata, so
raw ``SKILL.md`` bodies and artifact contents are structurally unreachable
here (same boundary as the chat judge, pinned by
tests/security/test_jev_boundaries.py).

Selection (§6): ``answers.skill`` carries ``choice``, ``confidence`` and
per-option probabilities (accept the key ``probabilities``, fall back to
``distribution``). Selected = CANDIDATE ids — never ``none``, never ids
outside the candidate set — with probability ≥ ``min_probability``, best
first, capped at ``max_select``. ``choice == "none"`` or
``confidence < min_confidence`` abstains (``selected = []`` — abstention is
success, ADR-008). Every transport/parse failure maps to a ``JudgeVerdict``
status exactly like OpenAICompatSkillJudge — timeout → ``timeout``; HTTP
error → ``error``; malformed body → ``invalid_output`` — so the reranker
abstains (fail-safe) rather than crashing a route. The API key never appears
in any log line or verdict text, and the response body is never logged (a
server error body can echo request headers).
"""

import logging
import time
from typing import Any

import httpx

from aci.domain.routing.models import JudgeCandidate, JudgeStatus, JudgeVerdict

#: Model requested from jevos (§6: the body pins "jev-latest"). Also the
#: ``JudgeVerdict.model_id`` — telemetry can tell a jevos verdict from a
#: chat-judge verdict by it.
JEVOS_MODEL = "jev-latest"

#: The one question key (§6: ``questions.skill``).
_QUESTION_KEY = "skill"
#: The ``none`` criteria text (§6, verbatim).
_NONE_CRITERIA = "no listed skill clearly helps"
#: The one-sentence choice instructions (§6, verbatim).
_INSTRUCTIONS = (
    "Which listed skill would most directly help a coding agent with this task; "
    "pick none if none clearly applies."
)
#: Criteria value bound (§6: ``document_text[:160]``).
_CRITERIA_MAX = 160
#: Choice display bound inside ``reason``: a hallucinated long id must not
#: crowd out the reason's informative tail (the abstention cause).
_CHOICE_DISPLAY_MAX = 64
#: Task text bound for the wire (§6: head 3000 + tail 1000 chars — the same
#: bound as the chat judge's user message).
_TASK_HEAD = 3000
_TASK_TAIL = 1000
#: JudgeVerdict.reason bound (the domain model enforces ≤500; truncate before).
_REASON_MAX = 500

log = logging.getLogger(__name__)


def _state_for_wire(task_text: str) -> str:
    """Head + tail bound: the middle of a long prompt never reaches the wire.

    (Duplicated from judge.py rather than imported: that module's helper is
    private, and this file must stay independently reviewable — same call
    as judge.py duplicating executor.py.)
    """
    if len(task_text) <= _TASK_HEAD + _TASK_TAIL:
        return task_text
    return task_text[:_TASK_HEAD] + "\n[…truncated…]\n" + task_text[-_TASK_TAIL:]


class JevosSkillJudge:
    """``SkillJudge`` over a Jev typed-decision ``POST /v1/systemone`` endpoint.

    The endpoint is deployment infrastructure (the local jevos service on
    home-sever); the platform gains no model authority from it (§76).
    ``client`` is injectable so tests drive a ``httpx.MockTransport`` —
    without it a fresh client is created per call and closed after (the
    judge.py pattern).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        timeout_s: float = 8.0,
        min_probability: float = 0.35,
        min_confidence: float = 0.30,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._min_probability = min_probability
        self._min_confidence = min_confidence
        self._client = client

    def judge(
        self, task_text: str, candidates: list[JudgeCandidate], max_select: int
    ) -> JudgeVerdict:
        """One typed-decision call; every failure is a status, never an exception."""
        if not candidates:
            # Nothing to select from: skip the call entirely (the reranker
            # already short-circuits empty input; this guards direct use).
            return JudgeVerdict(status="ok", reason="no candidates", model_id=JEVOS_MODEL)

        request_body = {
            "model": JEVOS_MODEL,
            "state": _state_for_wire(task_text),
            "questions": {
                _QUESTION_KEY: {
                    "type": "choice",
                    "instructions": _INSTRUCTIONS,
                    "criteria": {
                        **{
                            candidate.capability_id: candidate.document_text[:_CRITERIA_MAX]
                            for candidate in candidates
                        },
                        "none": _NONE_CRITERIA,
                    },
                }
            },
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

        started = time.perf_counter()
        client = self._client or httpx.Client()
        try:
            response = client.post(
                f"{self._base_url}/v1/systemone",
                json=request_body,
                headers=headers,
                timeout=self._timeout_s,
            )
        except httpx.TimeoutException:
            # Type name only: the exception text can carry the URL, and the
            # verdict must stay free of anything request-shaped.
            return self._verdict("timeout", "jevos request timed out", started)
        except httpx.HTTPError:
            return self._verdict("error", "jevos endpoint unreachable", started)
        finally:
            if self._client is None:
                client.close()
        latency_ms = self._latency_ms(started)

        if response.status_code != 200:
            # Status code ONLY: a server error body can echo request headers
            # (incl. Authorization) — it never reaches logs or the verdict.
            log.warning("jevos endpoint returned %s", response.status_code)
            return self._verdict(
                "error", f"jevos endpoint returned {response.status_code}", started
            )

        answer = self._answer(response)
        if answer is None:
            return self._verdict("invalid_output", "jevos response has no answers.skill", started)

        choice = answer.get("choice")
        if not isinstance(choice, str) or not choice:
            return self._verdict("invalid_output", 'jevos answer has no "choice"', started)
        confidence = answer.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return self._verdict(
                "invalid_output", 'jevos answer has no numeric "confidence"', started
            )
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict):
            probabilities = answer.get("distribution")
        if not isinstance(probabilities, dict):
            return self._verdict(
                "invalid_output",
                'jevos answer has neither "probabilities" nor "distribution"',
                started,
            )

        known = {candidate.capability_id for candidate in candidates}
        # Ids the judge asserted that it was never given ("none" is a real
        # option): dropped from selection, counted for telemetry.
        unknown = {key for key in probabilities if key != "none" and key not in known}
        if choice != "none" and choice not in known:
            unknown.add(choice)

        confidence_value = float(confidence)
        # The choice is display-only here (selection comes from the
        # probabilities): bound it so a hallucinated long id can never crowd
        # out the reason's tail or the 500-char verdict bound.
        reason = f"jevos choice={choice[:_CHOICE_DISPLAY_MAX]} confidence={confidence_value:.2f}"
        if choice == "none":
            return JudgeVerdict(
                status="ok",
                selected=[],
                reason=reason[:_REASON_MAX],
                model_id=JEVOS_MODEL,
                latency_ms=latency_ms,
                invalid_ids=len(unknown),
            )
        if confidence_value < self._min_confidence:
            return JudgeVerdict(
                status="ok",
                selected=[],
                # Truncate AFTER the suffix: a hallucinated long choice must
                # never push the reason past the domain's 500-char bound.
                reason=(reason + " below min_confidence")[:_REASON_MAX],
                model_id=JEVOS_MODEL,
                latency_ms=latency_ms,
                invalid_ids=len(unknown),
            )

        # Threshold selection over the CANDIDATE ids only: an unscored
        # candidate (no numeric entry) can never pass, and a numeric entry
        # for an unknown id is never looked at. Best first, deterministic
        # tiebreak by id, capped at max_select (bounded output, §2.2.6).
        entries: list[tuple[str, float]] = []
        for candidate in candidates:
            value = probabilities.get(candidate.capability_id)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            entries.append((candidate.capability_id, float(value)))
        entries.sort(key=lambda entry: (-entry[1], entry[0]))
        selected = [
            capability_id
            for capability_id, probability in entries
            if probability >= self._min_probability
        ][:max_select]
        return JudgeVerdict(
            status="ok",
            selected=selected,
            reason=reason[:_REASON_MAX],
            model_id=JEVOS_MODEL,
            latency_ms=latency_ms,
            invalid_ids=len(unknown),
        )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _latency_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def _verdict(self, status: JudgeStatus, reason: str, started: float) -> JudgeVerdict:
        """A failure verdict: nothing selected, honest reason, no secrets."""
        return JudgeVerdict(
            status=status,
            reason=reason[:_REASON_MAX],
            model_id=JEVOS_MODEL,
            latency_ms=self._latency_ms(started),
        )

    @staticmethod
    def _answer(response: httpx.Response) -> dict[str, Any] | None:
        """Extract the ``answers.skill`` object; None when the shape is wrong."""
        try:
            payload = response.json()
        except ValueError:  # json.JSONDecodeError / UnicodeDecodeError
            return None
        if not isinstance(payload, dict):
            return None
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            return None
        answer = answers.get(_QUESTION_KEY)
        return answer if isinstance(answer, dict) else None
