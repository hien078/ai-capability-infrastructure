"""JevosSkillJudge unit tests (docs/plans/jev-reranker.md §6, worker task 8).

httpx ``MockTransport`` only — NO network, NO live jevos service, NO real
key. The one-wire-call contract: request shape (model ``jev-latest``,
``state`` = task text, ONE ``choice`` question keyed ``skill`` whose
criteria = candidate ids (trusted routing text ≤160 chars) + a ``none``
option, bearer header when a key is set), the ``probabilities`` →
``distribution`` fallback, threshold / ``none`` / low-confidence
abstention, unknown option ids dropped, the ``max_select`` cap, failure
mapping (timeout / 5xx / malformed body → JudgeVerdict statuses), and the
key-leakage boundary (the key never appears in any exception text or log
record).
"""

import json
import logging

import httpx
import pytest

from aci.adapters.outbound.model_provider.jevos_judge import JEVOS_MODEL, JevosSkillJudge
from aci.domain.routing.models import JudgeCandidate

BASE = "http://jevos.local"
KEY = "sk-test-SECRET-KEY"

#: The §6 one-sentence choice instructions (asserted verbatim on the wire).
INSTRUCTIONS = (
    "Which listed skill would most directly help a coding agent with this task; "
    "pick none if none clearly applies."
)
#: The §6 ``none`` criteria text (asserted verbatim on the wire).
NONE_CRITERIA = "no listed skill clearly helps"


def jevos_response(answer: dict) -> httpx.Response:  # type: ignore[type-arg]
    """A well-shaped jevos response: answers.skill = ``answer``."""
    return httpx.Response(200, json={"answers": {"skill": answer}})


def make_judge(
    handler,  # type: ignore[no-untyped-def]
) -> tuple[JevosSkillJudge, list[httpx.Request]]:
    """A judge over a capturing MockTransport (the test owns the client)."""
    captured: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(transport))
    judge = JevosSkillJudge(
        base_url=BASE,
        api_key=KEY,
        timeout_s=8.0,
        min_probability=0.35,
        min_confidence=0.30,
        client=client,
    )
    return judge, captured


def candidates(*ids: str, text: str = "a skill description") -> list[JudgeCandidate]:
    return [JudgeCandidate(capability_id=i, document_text=text) for i in ids]


def body_of(request: httpx.Request) -> dict:  # type: ignore[type-arg]
    return json.loads(request.content.decode("utf-8"))


def ok_answer(
    choice: str = "debugging",
    confidence: float = 0.77,
    probabilities: dict[str, float] | None = None,
) -> dict:  # type: ignore[type-arg]
    probs = {"debugging": 0.8, "tdd": 0.1, "none": 0.1} if probabilities is None else probabilities
    return {"choice": choice, "confidence": confidence, "probabilities": probs}


# -- request shape ---------------------------------------------------------


def test_request_shape_is_the_spec_contract() -> None:
    judge, captured = make_judge(lambda request: jevos_response(ok_answer()))
    verdict = judge.judge("fix a bug", candidates("debugging", "tdd"), 2)
    assert verdict.status == "ok"
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE}/v1/systemone"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    body = body_of(request)
    assert body["model"] == JEVOS_MODEL == "jev-latest"
    assert body["state"] == "fix a bug"
    assert set(body) == {"model", "state", "questions"}
    question = body["questions"]["skill"]
    assert set(question) == {"type", "instructions", "criteria"}
    assert question["type"] == "choice"
    assert question["instructions"] == INSTRUCTIONS
    # criteria = one entry per candidate (trusted text) + the none option.
    assert question["criteria"] == {
        "debugging": "a skill description",
        "tdd": "a skill description",
        "none": NONE_CRITERIA,
    }


def test_criteria_text_is_bounded_to_160_chars() -> None:
    judge, captured = make_judge(lambda request: jevos_response(ok_answer()))
    judge.judge("fix a bug", candidates("debugging", text="y" * 2000), 2)
    criteria = body_of(captured[0])["questions"]["skill"]["criteria"]
    assert criteria["debugging"] == "y" * 160
    assert criteria["none"] == NONE_CRITERIA


def test_state_is_truncated_head_and_tail() -> None:
    judge, captured = make_judge(lambda request: jevos_response(ok_answer()))
    task = "H" * 3000 + "M" * 2000 + "T" * 1000
    judge.judge(task, candidates("debugging"), 2)
    state = body_of(captured[0])["state"]
    assert state.startswith("H" * 3000)
    assert state.endswith("T" * 1000)
    # The dropped middle never reaches the wire.
    assert "M" * 100 not in state


def test_no_authorization_header_without_key() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return jevos_response(ok_answer())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    judge = JevosSkillJudge(base_url=BASE, api_key="", timeout_s=8.0, client=client)
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert "Authorization" not in captured[0].headers


# -- selection semantics ----------------------------------------------------


def test_selection_threshold_best_first_capped() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            ok_answer(
                choice="b",
                confidence=0.9,
                probabilities={"a": 0.5, "b": 0.8, "c": 0.4, "d": 0.34, "none": 0.01},
            )
        )
    )
    verdict = judge.judge("t", candidates("a", "b", "c", "d"), 2)
    assert verdict.status == "ok"
    # threshold 0.35 keeps a/b/c (d at 0.34 fails); best first: b, a, c; cap 2.
    assert verdict.selected == ["b", "a"]


def test_reason_is_the_spec_short_text() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            ok_answer(choice="debugging", confidence=0.77, probabilities={"debugging": 0.8})
        )
    )
    verdict = judge.judge("t", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.reason == "jevos choice=debugging confidence=0.77"
    assert verdict.model_id == JEVOS_MODEL
    assert isinstance(verdict.latency_ms, int)
    assert verdict.latency_ms >= 0


def test_choice_none_abstains_even_with_high_candidate_probability() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            ok_answer(choice="none", confidence=0.9, probabilities={"debugging": 0.8, "none": 0.2})
        )
    )
    verdict = judge.judge("t", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == []
    assert verdict.reason == "jevos choice=none confidence=0.90"


def test_low_confidence_abstains() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            ok_answer(choice="debugging", confidence=0.2, probabilities={"debugging": 0.9})
        )
    )
    verdict = judge.judge("t", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == []
    assert verdict.reason.startswith("jevos choice=debugging confidence=0.20")
    assert "min_confidence" in verdict.reason


def test_hallucinated_long_choice_keeps_reason_bounded() -> None:
    """A judge that answers with a huge invented id must not break the
    ≤500-char JudgeVerdict.reason bound (the abstention suffix is appended
    BEFORE truncation — a ValidationError must never escape judge())."""
    judge, _ = make_judge(
        lambda request: jevos_response(
            ok_answer(choice="x" * 600, confidence=0.1, probabilities={})
        )
    )
    verdict = judge.judge("t", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == []
    assert len(verdict.reason) <= 500
    assert "min_confidence" in verdict.reason
    assert verdict.invalid_ids == 1


def test_distribution_key_is_the_fallback() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            {"choice": "debugging", "confidence": 0.8, "distribution": {"debugging": 0.9}}
        )
    )
    verdict = judge.judge("t", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["debugging"]


def test_probabilities_wins_over_distribution() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            {
                "choice": "debugging",
                "confidence": 0.8,
                "probabilities": {"debugging": 0.9},
                "distribution": {"tdd": 0.99},
            }
        )
    )
    verdict = judge.judge("t", candidates("debugging", "tdd"), 2)
    assert verdict.selected == ["debugging"]


def test_unknown_choice_id_dropped_and_counted() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            ok_answer(
                choice="evil-skill",
                confidence=0.8,
                probabilities={"evil-skill": 0.6, "debugging": 0.5},
            )
        )
    )
    verdict = judge.judge("t", candidates("debugging"), 2)
    assert verdict.status == "ok"
    # The unknown choice is never selected; the threshold path still works.
    assert verdict.selected == ["debugging"]
    assert verdict.invalid_ids == 1


def test_unknown_probability_keys_dropped_and_counted() -> None:
    """An invented id with the TOP probability still cannot be selected."""
    judge, _ = make_judge(
        lambda request: jevos_response(
            ok_answer(
                choice="debugging",
                confidence=0.8,
                probabilities={"debugging": 0.4, "evil-skill": 0.9},
            )
        )
    )
    verdict = judge.judge("t", candidates("debugging"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["debugging"]
    assert "evil-skill" not in verdict.selected
    assert verdict.invalid_ids == 1


def test_none_option_is_never_selected() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            ok_answer(
                choice="debugging", confidence=0.8, probabilities={"none": 0.9, "debugging": 0.4}
            )
        )
    )
    verdict = judge.judge("t", candidates("debugging"), 2)
    assert verdict.selected == ["debugging"]
    assert "none" not in verdict.selected


def test_unscored_candidate_is_never_selected() -> None:
    """A candidate with no probability entry cannot pass the threshold —
    not even with min_probability=0 (absent ≠ zero-scored)."""
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: jevos_response(
                ok_answer(choice="debugging", confidence=0.8, probabilities={"debugging": 0.9})
            )
        )
    )
    zero_threshold = JevosSkillJudge(
        base_url=BASE, api_key=KEY, min_probability=0.0, min_confidence=0.0, client=client
    )
    verdict = zero_threshold.judge("t", candidates("debugging", "tdd"), 2)
    # tdd has NO probability entry: absent, not zero — never selected.
    assert verdict.status == "ok"
    assert verdict.selected == ["debugging"]


def test_non_numeric_probability_values_are_ignored() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            ok_answer(
                choice="debugging",
                confidence=0.8,
                probabilities={"debugging": "high", "tdd": 0.9},
            )
        )
    )
    verdict = judge.judge("t", candidates("debugging", "tdd"), 2)
    assert verdict.status == "ok"
    assert verdict.selected == ["tdd"]


def test_empty_candidates_short_circuits_without_a_call() -> None:
    judge, captured = make_judge(lambda request: jevos_response(ok_answer()))
    verdict = judge.judge("fix a bug", [], 2)
    assert verdict.status == "ok"
    assert verdict.selected == []
    assert captured == []


# -- failure mapping --------------------------------------------------------


def test_non_json_body_maps_to_invalid_output() -> None:
    judge, _ = make_judge(lambda request: httpx.Response(200, text="not json, sorry"))
    verdict = judge.judge("fix a bug", candidates("debugging"), 2)
    assert verdict.status == "invalid_output"
    assert verdict.selected == []


def test_list_payload_maps_to_invalid_output() -> None:
    judge, _ = make_judge(lambda request: httpx.Response(200, json=["debugging"]))
    assert judge.judge("t", candidates("debugging"), 2).status == "invalid_output"


def test_missing_answers_maps_to_invalid_output() -> None:
    judge, _ = make_judge(lambda request: httpx.Response(200, json={"result": {}}))
    assert judge.judge("t", candidates("debugging"), 2).status == "invalid_output"


def test_missing_skill_answer_maps_to_invalid_output() -> None:
    judge, _ = make_judge(
        lambda request: httpx.Response(
            200,
            json={"answers": {"other": {"choice": "x", "confidence": 1, "probabilities": {}}}},
        )
    )
    assert judge.judge("t", candidates("debugging"), 2).status == "invalid_output"


def test_non_object_answer_maps_to_invalid_output() -> None:
    judge, _ = make_judge(
        lambda request: httpx.Response(200, json={"answers": {"skill": ["debugging"]}})
    )
    assert judge.judge("t", candidates("debugging"), 2).status == "invalid_output"


def test_missing_choice_maps_to_invalid_output() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response({"confidence": 0.8, "probabilities": {"debugging": 0.9}})
    )
    assert judge.judge("t", candidates("debugging"), 2).status == "invalid_output"


def test_non_string_choice_maps_to_invalid_output() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response({"choice": 7, "confidence": 0.8, "probabilities": {}})
    )
    assert judge.judge("t", candidates("debugging"), 2).status == "invalid_output"


def test_missing_confidence_maps_to_invalid_output() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response({"choice": "debugging", "probabilities": {"debugging": 0.9}})
    )
    assert judge.judge("t", candidates("debugging"), 2).status == "invalid_output"


def test_non_numeric_confidence_maps_to_invalid_output() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            {"choice": "debugging", "confidence": "high", "probabilities": {}}
        )
    )
    assert judge.judge("t", candidates("debugging"), 2).status == "invalid_output"


def test_missing_probabilities_and_distribution_maps_to_invalid_output() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response({"choice": "debugging", "confidence": 0.8})
    )
    assert judge.judge("t", candidates("debugging"), 2).status == "invalid_output"


def test_non_object_probabilities_maps_to_invalid_output() -> None:
    judge, _ = make_judge(
        lambda request: jevos_response(
            {"choice": "debugging", "confidence": 0.8, "probabilities": [0.9]}
        )
    )
    assert judge.judge("t", candidates("debugging"), 2).status == "invalid_output"


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
    assert verdict.selected == []


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


# -- key hygiene ------------------------------------------------------------


def test_api_key_never_leaks_into_logs_or_verdicts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """§6: never logs the key or the task text — on ANY failure path."""

    def handler(request: httpx.Request) -> httpx.Response:
        # A server that echoes headers back in the error body (worst case).
        return httpx.Response(500, text=f"server saw {request.headers['Authorization']}")

    with caplog.at_level(logging.DEBUG, logger="aci.adapters.outbound.model_provider.jevos_judge"):
        judge, _ = make_judge(handler)
        verdict = judge.judge("fix a bug", candidates("debugging"), 2)
        assert verdict.status == "error"
        assert KEY not in verdict.reason
        assert KEY not in verdict.model_id
        for record in caplog.records:
            assert KEY not in record.getMessage(), record.getMessage()
            assert "fix a bug" not in record.getMessage(), "the task text leaked into logs"
