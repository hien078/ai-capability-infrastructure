"""JEV domain contracts (docs/plans/jev-reranker.md §2.3, spec task 1).

JudgeCandidate / JudgeVerdict / SkillJudge / the optional RerankTrace judge
fields. The load-bearing property here is byte-compatibility: a trace
produced WITHOUT a judge (the heuristic) must serialize exactly as before
JEV existed — unset judge fields are dropped, never emitted as nulls.
"""

import json

import pytest
from pydantic import ValidationError

from aci.application.protocols import SkillJudge
from aci.domain.routing.models import (
    JudgeCandidate,
    JudgeVerdict,
    RankedCandidate,
    RerankResult,
    RerankTrace,
)


def test_judge_candidate_is_frozen_and_minimal() -> None:
    candidate = JudgeCandidate(capability_id="debugging", document_text="debug skills")
    assert candidate.capability_id == "debugging"
    assert candidate.document_text == "debug skills"
    with pytest.raises(ValidationError):
        candidate.capability_id = "tdd"  # type: ignore[misc]


def test_judge_candidate_document_text_defaults_empty() -> None:
    assert JudgeCandidate(capability_id="x").document_text == ""


def test_judge_verdict_status_is_closed() -> None:
    for status in ("ok", "timeout", "error", "invalid_output"):
        assert JudgeVerdict(status=status).status == status
    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate({"status": "bogus"})


def test_judge_verdict_defaults() -> None:
    verdict = JudgeVerdict(status="ok")
    assert verdict.selected == []
    assert verdict.reason == ""
    assert verdict.model_id == ""
    assert verdict.latency_ms is None
    assert verdict.invalid_ids == 0
    assert verdict.necessities is None  # exp 3: no report = gate no-op


def test_judge_verdict_necessities_are_closed() -> None:
    verdict = JudgeVerdict(status="ok", selected=["a", "b"], necessities=["required", "optional"])
    assert verdict.necessities == ["required", "optional"]
    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate({"status": "ok", "selected": ["a"], "necessities": ["bogus"]})


def test_judge_verdict_reason_is_bounded() -> None:
    assert JudgeVerdict(status="ok", reason="x" * 500).reason == "x" * 500
    with pytest.raises(ValidationError):
        JudgeVerdict(status="ok", reason="x" * 501)


def test_judge_verdict_is_frozen() -> None:
    verdict = JudgeVerdict(status="ok", selected=["debugging"])
    with pytest.raises(ValidationError):
        verdict.status = "error"  # type: ignore[misc]


def test_rerank_trace_without_judge_fields_is_byte_compatible() -> None:
    """The heuristic trace serializes exactly as before JEV (§2.3)."""
    trace = RerankTrace(
        implementation="heuristic-reranker", version="3", input_count=2, output_count=2
    )
    assert trace.model_dump(mode="json") == {
        "implementation": "heuristic-reranker",
        "version": "3",
        "input_count": 2,
        "output_count": 2,
    }


def test_rerank_trace_carries_judge_fields_when_set() -> None:
    trace = RerankTrace(
        implementation="jev",
        version="1.0.0",
        input_count=12,
        output_count=2,
        judge_status="ok",
        judge_model_id="OneNexus/glm-5.3",
        judge_latency_ms=1500,
        judge_reason="picked debugging skills",
        selected_ids=["debugging", "testing"],
        invalid_ids=1,
    )
    dump = trace.model_dump(mode="json")
    assert dump["judge_status"] == "ok"
    assert dump["judge_model_id"] == "OneNexus/glm-5.3"
    assert dump["judge_latency_ms"] == 1500
    assert dump["judge_reason"] == "picked debugging skills"
    assert dump["selected_ids"] == ["debugging", "testing"]
    assert dump["invalid_ids"] == 1


def test_rerank_trace_keeps_empty_selected_ids_but_drops_unset() -> None:
    """An honest abstention records selected_ids == []; an unset field is absent."""
    abstain = RerankTrace(
        implementation="jev",
        version="1.0.0",
        input_count=5,
        output_count=0,
        judge_status="ok",
        selected_ids=[],
    )
    dump = abstain.model_dump(mode="json")
    assert dump["selected_ids"] == []
    assert "invalid_ids" not in dump
    assert "judge_reason" not in dump
    assert "necessity_dropped_ids" not in dump  # exp 3: unset = absent


def test_rerank_trace_carries_necessity_dropped_ids_when_set() -> None:
    """exp 3: the gate's drops are telemetry — ids only, present when the
    gate ran (even when it dropped nothing)."""
    trace = RerankTrace(
        implementation="jev",
        version="1.1.0",
        input_count=12,
        output_count=1,
        judge_status="ok",
        selected_ids=["debugging"],
        necessity_dropped_ids=["verification-before-completion"],
    )
    dump = trace.model_dump(mode="json")
    assert dump["necessity_dropped_ids"] == ["verification-before-completion"]
    empty = RerankTrace(
        implementation="jev",
        version="1.1.0",
        input_count=1,
        output_count=1,
        judge_status="ok",
        selected_ids=["debugging"],
        necessity_dropped_ids=[],
    )
    assert empty.model_dump(mode="json")["necessity_dropped_ids"] == []


# ---------- P4: the drop runs on EVERY serialization path (review 2026-10-07) ----------
#
# The old ``model_dump`` OVERRIDE was bypassed by NESTED dumps
# (``RerankResult.model_dump`` serializes the trace through the core
# serializer, never calling the child's method) and by ``model_dump_json``
# — both emitted the unset judge fields as nulls. A ``model_serializer``
# wrap runs on every path.


def test_nested_rerank_result_dump_drops_unset_judge_fields() -> None:
    """The heuristic trace inside RerankResult stays key-identical to main
    (no judge nulls) on the NESTED path."""
    trace = RerankTrace(
        implementation="heuristic-reranker", version="3", input_count=2, output_count=2
    )
    result = RerankResult(ranked=[], trace=trace)
    assert result.model_dump(mode="json")["trace"] == {
        "implementation": "heuristic-reranker",
        "version": "3",
        "input_count": 2,
        "output_count": 2,
    }


def test_nested_rerank_result_dump_keeps_set_judge_fields() -> None:
    trace = RerankTrace(
        implementation="jev",
        version="1.0.0",
        input_count=5,
        output_count=0,
        judge_status="ok",
        selected_ids=[],
    )
    result = RerankResult(ranked=[], trace=trace)
    dumped = result.model_dump(mode="json")["trace"]
    assert dumped["judge_status"] == "ok"
    assert dumped["selected_ids"] == []
    assert "judge_reason" not in dumped
    assert "invalid_ids" not in dumped


def test_model_dump_json_drops_unset_judge_fields() -> None:
    """``model_dump_json`` bypassed the old override entirely — the unset
    judge fields serialized as nulls. The wrap serializer covers it."""
    trace = RerankTrace(
        implementation="heuristic-reranker", version="3", input_count=2, output_count=2
    )
    payload = json.loads(trace.model_dump_json())
    assert payload == {
        "implementation": "heuristic-reranker",
        "version": "3",
        "input_count": 2,
        "output_count": 2,
    }


def test_model_dump_json_keeps_set_judge_fields() -> None:
    trace = RerankTrace(
        implementation="jev",
        version="1.0.0",
        input_count=5,
        output_count=0,
        judge_status="timeout",
        judge_reason="slow",
        selected_ids=[],
        invalid_ids=1,
    )
    payload = json.loads(trace.model_dump_json())
    assert payload["judge_status"] == "timeout"
    assert payload["judge_reason"] == "slow"
    assert payload["selected_ids"] == []
    assert payload["invalid_ids"] == 1


def test_nested_dump_via_model_dump_json_drops_unset_judge_fields() -> None:
    """The double bypass: a nested trace inside RerankResult, serialized
    through model_dump_json."""
    trace = RerankTrace(
        implementation="heuristic-reranker", version="3", input_count=1, output_count=1
    )
    result = RerankResult(ranked=[], trace=trace)
    payload = json.loads(result.model_dump_json())
    assert payload["trace"] == {
        "implementation": "heuristic-reranker",
        "version": "3",
        "input_count": 1,
        "output_count": 1,
    }


def test_judge_reason_is_bounded_on_the_trace() -> None:
    """P4: judge_reason carries the same 500-char bound as JudgeVerdict.reason
    (telemetry lands the reason verbatim)."""
    base = {
        "implementation": "jev",
        "version": "1.0.0",
        "input_count": 0,
        "output_count": 0,
    }
    assert RerankTrace.model_validate({**base, "judge_reason": "r" * 500}).judge_reason == "r" * 500
    with pytest.raises(ValidationError):
        RerankTrace.model_validate({**base, "judge_reason": "r" * 501})


def test_rerank_result_default_ranked_is_empty() -> None:
    result = RerankResult(
        ranked=[],
        trace=RerankTrace(implementation="jev", version="1.0.0", input_count=0, output_count=0),
    )
    assert result.ranked == []
    assert isinstance(result.ranked, list)


def test_ranked_candidate_reasons_default_empty() -> None:
    """RankedCandidate is unchanged by JEV (it is the shared output shape)."""
    from aci.domain.policy.models import EligibleCandidate

    candidate = EligibleCandidate.model_validate(
        {
            "capability_id": "debugging",
            "version": "1.0.0",
            "digest": "sha256:" + "ab" * 32,
            "kind": "skill",
            "channel": "production",
            "status": "active",
        }
    )
    ranked = RankedCandidate(candidate=candidate, score=0.5, retrieval_score=0.5, rank=1)
    assert ranked.reasons == []
    assert ranked.document_text == ""


def test_skill_judge_protocol_shape() -> None:
    """A fake judge satisfies SkillJudge structurally (§44: swappable)."""

    class FakeJudge:
        def judge(
            self, task_text: str, candidates: list[JudgeCandidate], max_select: int
        ) -> JudgeVerdict:
            return JudgeVerdict(
                status="ok",
                selected=[c.capability_id for c in candidates[:max_select]],
                reason="fake",
            )

    judge: SkillJudge = FakeJudge()
    verdict = judge.judge("fix a bug", [JudgeCandidate(capability_id="debugging")], 2)
    assert verdict.selected == ["debugging"]
