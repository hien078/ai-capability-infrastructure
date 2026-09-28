"""Compatibility evaluation + promotion proposal tests (auto.md §20, §24).

Invariants: no declaration → honest unknown (never silently compatible);
entrypoint mismatch → incompatible; the proposal aggregates the REAL
gates (same checks promote() enforces — no bypass); absent benchmark
stays informational, never faked (§27); recommendation follows evidence.
"""

from datetime import UTC, datetime
from typing import Any

from aci.application.evaluate_compatibility import (
    ClientRequirement,
    evaluate_compatibility,
)
from aci.domain.capability.models import CapabilityVersion, SkillSpec

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _version(**compat: Any) -> CapabilityVersion:
    return CapabilityVersion(
        capability_id="cap-x",
        version="1.0.0",
        kind="skill",
        content_digest="sha256:" + "a" * 64,
        created_at=NOW,
        spec=SkillSpec(),
        **compat,
    )


# ---------------------------------------------------------------------------
# Compatibility evaluation (§20)
# ---------------------------------------------------------------------------


def test_no_declaration_is_honest_unknown() -> None:
    """§60.9: absence of a Compatibility declaration is unknown for every
    client — never silently compatible."""
    report = evaluate_compatibility(_version())
    assert all(s == "unknown" for s in report.results.values())
    assert report.all_compatible_or_unknown  # unknown ≠ incompatible


def test_entrypoint_mismatch_is_incompatible() -> None:
    v = _version()
    v = v.model_copy(
        update={"spec": SkillSpec(entrypoint="OTHER.md")},
    )
    report = evaluate_compatibility(
        v,
        clients=(ClientRequirement(client="opencode", required_entrypoint="SKILL.md"),),
    )
    assert report.results["opencode"] == "incompatible"
    assert not report.all_compatible_or_unknown


def test_supported_clients_filter() -> None:
    from aci.domain.capability.models import Compatibility

    v = _version(
        compatibility=Compatibility(supported_clients=["opencode"]),
    )
    report = evaluate_compatibility(
        v,
        clients=(
            ClientRequirement(client="opencode", required_entrypoint="SKILL.md"),
            ClientRequirement(client="mcp", required_entrypoint="SKILL.md"),
        ),
    )
    assert report.results["opencode"] == "compatible"
    assert report.results["mcp"] == "incompatible"


def test_language_requirement_mismatch() -> None:
    from aci.domain.capability.models import Compatibility

    v = _version(compatibility=Compatibility(supported_languages=["python"]))
    report = evaluate_compatibility(
        v,
        clients=(ClientRequirement(client="opencode", language="typescript"),),
    )
    assert report.results["opencode"] == "incompatible"


# ---------------------------------------------------------------------------
# Promotion proposal (§24)
# ---------------------------------------------------------------------------


class FakeCapabilities:
    def __init__(self, version: CapabilityVersion | None) -> None:
        self._v = version

    def get_version(self, capability_id: str, version: str) -> CapabilityVersion | None:
        return self._v

    def get_capability(self, capability_id: str) -> Any:
        return None

    def create_capability(self, capability: Any) -> Any:
        return capability

    def create_version(self, v: CapabilityVersion) -> CapabilityVersion:
        return v


class FakePromotion:
    """Stands in for PromotionService.prerequisites — the proposal builder
    must surface EXACTLY these checks (no bypass, §24)."""

    def __init__(self, passed: bool) -> None:
        self.passed = passed

    def prerequisites(self, capability_id: str, version: str, channel: str) -> list[Any]:
        from aci.domain.provenance.models import PromotionCheck

        return [
            PromotionCheck(name="provenance-chain", passed=self.passed, detail="fake"),
            PromotionCheck(name="license-redistributable", passed=self.passed, detail="fake"),
            PromotionCheck(name="security-passed", passed=self.passed, detail="fake"),
        ]


def _builder(passed: bool = True, with_version: bool = True):
    from aci.application.propose_promotion import PromotionProposalBuilder

    v = _version() if with_version else None
    return PromotionProposalBuilder(
        capabilities=FakeCapabilities(v),  # type: ignore[arg-type]
        promotion=FakePromotion(passed),  # type: ignore[arg-type]
    )


def test_proposal_aggregates_real_gates() -> None:
    p = _builder().build("cap-x", "1.0.0")
    assert [c.name for c in p.ingestion.checks] == [
        "provenance-chain",
        "license-redistributable",
        "security-passed",
    ]
    assert p.ingestion.passed is True
    assert p.recommendation == "promote"
    assert p.decision_ready


def test_failed_gate_recommends_reject() -> None:
    p = _builder(passed=False).build("cap-x", "1.0.0")
    assert p.ingestion.passed is False
    assert p.recommendation == "reject"
    assert not p.decision_ready


def test_absent_benchmark_is_informational_never_faked() -> None:
    """§27: no benchmark run → the section is informational (passed=None),
    never a fabricated pass."""
    p = _builder().build("cap-x", "1.0.0")
    assert p.benchmark.passed is None
    assert "no benchmark run" in p.benchmark.detail
    assert p.decision_ready  # informational does not block


def test_benchmark_evidence_recorded_when_supplied() -> None:
    p = _builder().build(
        "cap-x",
        "1.0.0",
        benchmark_evidence={
            "run_id": "bench_88",
            "summary": "success_delta +0.12 vs baseline A",
            "regression": "major_regressions: 0",
        },
    )
    assert p.benchmark.passed is True
    assert "+0.12" in p.benchmark.detail
    assert p.evidence["benchmark_run"] == "bench_88"


def test_summary_line_shape() -> None:
    p = _builder().build("cap-x", "1.0.0")
    line = p.summary_line()
    assert "cap-x@1.0.0" in line
    assert "ing:✓" in line and "bench:?" in line
