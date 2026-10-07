"""JEV reranker — an LLM judge behind ``CapabilityReranker`` (§17 JevReranker).

docs/plans/jev-reranker.md (2026-10-07, design approved). The heuristic
reranker's measured failure on long multi-topic prompts is attachment, not
ranking: embedding similarities go flat (0.65–0.70 across the top), the
min-max calibration amplifies tiny gaps, and the composer fills the budget —
the heuristic cannot abstain. A judge reads the task and can say "none".

Invariants (§2.2, review-blocking — pinned by tests/unit/test_jev_reranker.py):

1. SUBSET ONLY: the judge may return ids from the candidates it was given;
   anything else is dropped and counted (``invalid_ids``). The reranker can
   never add or rescue an ineligible capability (ADR-009) — eligibility
   already ran.
2. TRUSTED TEXT ONLY (§17.1): the judge sees ``ScoredCandidate.document_text``
   (the trusted routing document) and the capability id — never raw
   ``SKILL.md`` bodies or artifact contents.
3. ABSTENTION IS SUCCESS: ``selected = []`` → empty bundle (ADR-008).
4. UNSELECTED CANDIDATES ARE NOT RETURNED: ``RerankResult.ranked`` carries
   only the judge's selection — the composer must not be able to fill the
   budget with candidates the judge rejected.
5. FAIL-SAFE = ABSTAIN: timeout / transport error / non-JSON / schema-invalid
   output → ``ranked = []`` with ``judge_status`` set. Never a silent fallback
   to the heuristic (it is the measured source of harm);
   ``on_failure="heuristic"`` exists only as an explicit opt-in. A judge that
   RAISES instead of returning (P1: the adapter contract is never-raise,
   but a judge is a plug-in) abstains through the same path.
6. DETERMINISTIC CONTRACT AROUND A NON-DETERMINISTIC JUDGE: temperature 0 and
   bounded output are the ADAPTER's job; here, order = judge order and ties
   are impossible (rank = position).

Pure logic, no HTTP — the ``SkillJudge`` protocol is plugged in by wiring
(``adapters/inbound/rest/wiring.py``); unit tests use a fake judge.
"""

from typing import Literal

from aci.application.protocols import CapabilityReranker, SkillJudge
from aci.domain.policy.models import RoutingRequestContext
from aci.domain.routing.models import (
    JudgeCandidate,
    JudgeVerdict,
    RankedCandidate,
    RerankResult,
    RerankTrace,
    ScoredCandidate,
    TaskDescriptor,
)

IMPLEMENTATION = "jev"
VERSION = "1.0.0"

OnFailure = Literal["abstain", "heuristic"]


class JevReranker:
    """Implements the ``CapabilityReranker`` protocol (§17; §44 swappable).

    Takes the top ``candidate_limit`` candidates by retrieval score, asks
    the judge which (if any) directly help with the task as written, and
    returns ONLY the validated selection in judge order.
    """

    def __init__(
        self,
        judge: SkillJudge,
        *,
        candidate_limit: int = 12,
        max_select: int = 2,
        on_failure: OnFailure = "abstain",
        fallback: CapabilityReranker | None = None,
    ) -> None:
        if candidate_limit < 1:
            raise ValueError(f"candidate_limit must be >= 1, got {candidate_limit}")
        if max_select < 1:
            raise ValueError(f"max_select must be >= 1, got {max_select}")
        if on_failure not in ("abstain", "heuristic"):
            raise ValueError(f"unknown on_failure {on_failure!r} (abstain|heuristic)")
        if on_failure == "heuristic" and fallback is None:
            raise ValueError("on_failure='heuristic' requires a fallback reranker")
        self._judge = judge
        self._candidate_limit = candidate_limit
        self._max_select = max_select
        self._on_failure = on_failure
        self._fallback = fallback

    def rerank(
        self,
        task: TaskDescriptor,
        candidates: list[ScoredCandidate],
        context: RoutingRequestContext,
    ) -> RerankResult:
        _ = context  # eligibility already constrained the candidate set
        if not candidates:
            # Nothing to judge: abstain without a call (no judge fields on
            # the trace — the byte-compatible pre-JEV shape).
            return RerankResult(
                ranked=[],
                trace=RerankTrace(
                    implementation=IMPLEMENTATION,
                    version=VERSION,
                    input_count=0,
                    output_count=0,
                ),
            )

        top = self._top(candidates)
        try:
            verdict = self._judge.judge(
                task.task_text,
                [
                    JudgeCandidate(
                        capability_id=s.candidate.capability_id, document_text=s.document_text
                    )
                    for s in top
                ],
                self._max_select,
            )
        except Exception as exc:  # noqa: BLE001 — defensive: a raising judge abstains
            # P1: the adapter contract is never-raise, but a judge is a
            # plug-in (§44) — this backstop keeps a raising one from turning
            # into an unhandled 500 on /v1/routes. TYPE NAME ONLY in the
            # reason: str(exc) can carry anything the judge saw. The verdict
            # flows through the SAME failure path as any other judge failure
            # (abstain by default, the explicit heuristic opt-in included).
            verdict = JudgeVerdict(
                status="error",
                reason=f"judge raised {type(exc).__name__}",
            )

        if verdict.status != "ok":
            return self._on_judge_failure(task, candidates, context, verdict)

        selected = self._validate_subset(verdict, top)
        ranked = [
            RankedCandidate(
                candidate=s.candidate,
                # The judge gives no scores: the honest per-item affinity is
                # the retrieval score; ORDER (rank) is the judge's verdict.
                score=s.score,
                retrieval_score=s.score,
                rank=position,
                reasons=["judge-selected"],
                document_text=s.document_text,
            )
            for position, s in enumerate(selected, start=1)
        ]
        return RerankResult(
            ranked=ranked,
            trace=self._trace(verdict, input_count=len(candidates), selected=selected, top=top),
        )

    # -- internals ---------------------------------------------------------

    def _top(self, candidates: list[ScoredCandidate]) -> list[ScoredCandidate]:
        """Top ``candidate_limit`` by retrieval score (deterministic tiebreak)."""
        ordered = sorted(
            candidates,
            key=lambda s: (-s.score, s.candidate.capability_id, s.candidate.version),
        )
        return ordered[: self._candidate_limit]

    def _validate_subset(
        self, verdict: JudgeVerdict, top: list[ScoredCandidate]
    ) -> list[ScoredCandidate]:
        """Keep the judge's ids that exist in the candidate set, in order.

        Unknown ids are dropped and counted; duplicates collapse to their
        first occurrence; the result is capped at ``max_select`` (bounded
        output even when the judge ignores the instruction).
        """
        by_id = {s.candidate.capability_id: s for s in top}
        selected: list[ScoredCandidate] = []
        seen: set[str] = set()
        for capability_id in verdict.selected:
            scored = by_id.get(capability_id)
            if scored is None:
                continue  # counted below via invalid_ids
            if capability_id in seen:
                continue
            seen.add(capability_id)
            selected.append(scored)
            if len(selected) >= self._max_select:
                break
        return selected

    def _invalid_count(self, verdict: JudgeVerdict, top: list[ScoredCandidate]) -> int:
        """Parse-level drops (verdict) + judge-selected unknown ids.

        Counted against the FULL top slice: an id over the ``max_select`` cap
        is truncated, not invalid — only ids the judge invented (never given
        to it) are invalid.
        """
        known = {s.candidate.capability_id for s in top}
        unknown = sum(1 for cid in verdict.selected if cid not in known)
        return verdict.invalid_ids + unknown

    def _trace(
        self,
        verdict: JudgeVerdict,
        *,
        input_count: int,
        selected: list[ScoredCandidate],
        top: list[ScoredCandidate],
    ) -> RerankTrace:
        return RerankTrace(
            implementation=IMPLEMENTATION,
            version=VERSION,
            input_count=input_count,
            output_count=len(selected),
            judge_status=verdict.status,
            judge_model_id=verdict.model_id or None,
            judge_latency_ms=verdict.latency_ms,
            judge_reason=verdict.reason or None,
            selected_ids=[s.candidate.capability_id for s in selected],
            invalid_ids=self._invalid_count(verdict, top),
        )

    def _on_judge_failure(
        self,
        task: TaskDescriptor,
        candidates: list[ScoredCandidate],
        context: RoutingRequestContext,
        verdict: JudgeVerdict,
    ) -> RerankResult:
        """Fail-safe (§2.2.5): abstain — or the explicit heuristic opt-in."""
        if self._on_failure != "heuristic" or self._fallback is None:
            return RerankResult(
                ranked=[],
                trace=RerankTrace(
                    implementation=IMPLEMENTATION,
                    version=VERSION,
                    input_count=len(candidates),
                    output_count=0,
                    judge_status=verdict.status,
                    judge_model_id=verdict.model_id or None,
                    judge_latency_ms=verdict.latency_ms,
                    judge_reason=verdict.reason or None,
                    selected_ids=[],
                    invalid_ids=verdict.invalid_ids,
                ),
            )
        result = self._fallback.rerank(task, candidates, context)
        # The fallback's ranking stands (its implementation/version say who
        # ranked); the judge failure rides along for telemetry.
        return RerankResult(
            ranked=result.ranked,
            trace=result.trace.model_copy(
                update={
                    "judge_status": verdict.status,
                    "judge_model_id": verdict.model_id or None,
                    "judge_latency_ms": verdict.latency_ms,
                    "judge_reason": verdict.reason or None,
                    "selected_ids": [],
                    "invalid_ids": verdict.invalid_ids,
                }
            ),
        )
