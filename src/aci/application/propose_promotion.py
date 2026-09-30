"""Promotion proposal builder (auto.md §24): aggregate evidence → proposal.

Application service over the REAL gate evaluators — no new gate logic,
no bypass: ingestion checks come from PromotionService.prerequisites
(the same gates promote() enforces), compatibility from
evaluate_compatibility, benchmark/regression from the §41 benchmark
store when a run exists for the candidate.

The output is a PromotionProposal (DATA). Creating it changes nothing;
approve() is the human's call and goes through PromotionService.promote
— the deterministic pointer move (§25, ADR-012).
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from aci.application.evaluate_compatibility import (
    CompatibilityReport,
    evaluate_compatibility,
)
from aci.application.protocols import CapabilityRepository
from aci.control_plane.promotion.service import PromotionService
from aci.domain.acquisition.promotion_proposal import (
    PromotionProposal,
    ProposalSection,
)
from aci.domain.provenance.models import PromotionCheck


class PromotionProposalBuilder:
    def __init__(
        self,
        capabilities: CapabilityRepository,
        promotion: PromotionService,
    ) -> None:
        self._capabilities = capabilities
        self._promotion = promotion

    def build(
        self,
        capability_id: str,
        version: str,
        *,
        benchmark_evidence: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> PromotionProposal:
        """Aggregate every evidence source into one proposal (§24).

        ``benchmark_evidence`` is supplied by the caller from the §41
        benchmark store (run id + delta summary) — the builder does not
        run benchmarks itself; an absent benchmark stays an honest
        informational section, never a faked pass (§27: do not fake
        benchmark evidence).
        """
        proposed_at = now or datetime.now(UTC)

        # Ingestion gates: the SAME checks promote() enforces (no bypass).
        checks = self._promotion.prerequisites(capability_id, version, "production")
        ingestion_passed = all(c.passed for c in checks)
        ingestion = ProposalSection(
            name="ingestion",
            passed=ingestion_passed,
            detail="PromotionService production gates (provenance/license/security)",
            checks=checks,
        )

        # Compatibility: intrinsic declaration vs the real clients (§20).
        v = self._capabilities.get_version(capability_id, version)
        compat_report = (
            evaluate_compatibility(v)
            if v is not None
            else CompatibilityReport(
                capability_id=capability_id,
                version=version,
                results={},
                notes=["version not found — cannot evaluate"],
            )
        )
        # Compatibility blocks only on REAL incompatibility; an undeclared
        # version is unknown (§60.9) — surfaced in the section detail for
        # the human to see, never silently converted to either verdict.
        compat_ok = v is not None and compat_report.all_compatible_or_unknown
        compatibility = ProposalSection(
            name="compatibility",
            passed=compat_ok if v is not None else False,
            detail="; ".join(
                f"{client}={status}" for client, status in compat_report.results.items()
            )
            or "no clients evaluated",
            checks=[],
        )
        if compat_report.notes:
            compatibility = compatibility.model_copy(
                update={"detail": compatibility.detail + " | " + "; ".join(compat_report.notes)}
            )

        # Benchmark / regression: caller-supplied evidence, informational
        # when absent (never faked — §27). A supplied run is a pass/fail
        # verdict read from the evidence — never an automatic pass.
        bench = benchmark_evidence or {}
        bench_passed: bool | None = None
        if bench:
            verdict = bench.get("passed")
            if verdict is not None:
                # callers may pass bool (JSON) or string — both are verdicts
                bench_passed = str(verdict).lower() not in {"false", "0", "no", "f"}
            else:
                # §41 benchmark store shape: "major_regressions: N" (or a
                # bare count) — 0 = pass, >0 = fail. No verdict field and
                # no count = informational (None), never a faked pass.
                reg = bench.get("regression", bench.get("major_regressions", ""))
                m = re.search(r"major_regressions:?\s*(\d+)", reg)
                if m:
                    bench_passed = int(m.group(1)) == 0
        benchmark = ProposalSection(
            name="benchmark",
            passed=bench_passed,
            detail=bench.get("summary", "no benchmark run recorded (informational)"),
            checks=[],
        )
        regression = ProposalSection(
            name="regression",
            passed=bench_passed,
            detail=bench.get("regression", "no regression comparison recorded (informational)"),
            checks=[],
        )

        recommendation: str = "promote" if (ingestion_passed and compat_ok) else "hold"
        if not ingestion_passed:
            recommendation = "reject"

        return PromotionProposal(
            proposal_id=f"prop-{uuid.uuid4().hex[:12]}",
            capability_id=capability_id,
            version=version,
            created_at=proposed_at,
            ingestion=ingestion,
            compatibility=compatibility,
            benchmark=benchmark,
            regression=regression,
            recommendation=recommendation,  # type: ignore[arg-type]
            evidence={
                "benchmark_run": bench.get("run_id", ""),
                "gates_evaluated_by": "PromotionService.prerequisites(production)",
                "compatibility_evaluated_by": "evaluate_compatibility(DEFAULT_CLIENTS)",
            },
        )


def checks_to_promotion_checks(raw: list[tuple[str, bool, str]]) -> list[PromotionCheck]:
    """Convenience for tests/callers building sections from tuples."""
    return [PromotionCheck(name=n, passed=p, detail=d) for n, p, d in raw]
