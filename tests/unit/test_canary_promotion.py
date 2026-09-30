"""Canary promotion + monitor decision tests (crawl.md §26-27, STEP 11).

Invariants: promote_canary enforces the SAME production gates as
promote (no bypass); canary_percent is bounded 0-100 at the domain
contract AND at the service; the monitor's rollback decision honors
both the relative regression margin (vs the incumbent baseline) and
the §27 absolute acceptable-failure-rate when no baseline exists.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from aci.control_plane.promotion.service import PromotionService
from aci.domain.capability.errors import DomainError
from aci.domain.capability.models import CapabilityRelease

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from canary_monitor import (  # noqa: E402
    MAX_ACCEPTABLE_FAIL_RATE,
    MIN_CANARY_OUTCOMES,
    REGRESSION_MARGIN,
    rollback_decision,
)

NOW = datetime(2026, 9, 29, tzinfo=UTC)


class FakeReleases:
    def __init__(self) -> None:
        self.releases: list[CapabilityRelease] = []

    def set_release(self, release: CapabilityRelease) -> CapabilityRelease:
        self.releases.append(release)
        return release

    def get_release(self, capability_id: str, channel: str) -> CapabilityRelease | None:
        return next(
            (r for r in self.releases if r.capability_id == capability_id and r.channel == channel),
            None,
        )


class FakeGateRepo:
    """Every gate passes — the service under test is the pointer move."""

    def get_version(self, capability_id: str, version: str) -> Any:
        return object()

    def list_source_records(self, capability_id: str) -> list[Any]:
        return [type("R", (), {"version": "1.0.0", "ingestion_status": "accepted"})()]

    def get_assessment(self, capability_id: str, version: str) -> Any:
        class L:
            license_identifier = "MIT"
            scan_status = "passed"

            class permissions:
                @staticmethod
                def blocks_production_redistribution() -> bool:
                    return False

        return L()


def _service() -> PromotionService:
    repo = FakeGateRepo()
    return PromotionService(
        capabilities=repo,  # type: ignore[arg-type]
        releases=FakeReleases(),  # type: ignore[arg-type]
        source_records=repo,  # type: ignore[arg-type]
        licenses=repo,  # type: ignore[arg-type]
        securities=repo,  # type: ignore[arg-type]
    )


def test_promote_canary_moves_pointer_with_percent() -> None:
    svc = _service()
    release = svc.promote_canary(
        "cap-c", "1.0.0", "production", canary_percent=25, approved_by="rev-1", now=NOW
    )
    assert release.status == "canary"
    assert release.canary_percent == 25
    assert release.approved_by == "rev-1"


def test_promote_canary_rejects_out_of_range_percent() -> None:
    svc = _service()
    for bad in (-1, 101, 150):
        with pytest.raises(DomainError, match="out of range"):
            svc.promote_canary(
                "cap-c", "1.0.0", "production", canary_percent=bad, approved_by="rev-1"
            )


def test_canary_percent_domain_contract_is_bounded() -> None:
    from pydantic import ValidationError

    from aci.domain.capability.models import CapabilityRelease

    with pytest.raises(ValidationError):
        CapabilityRelease(capability_id="c", version="1", channel="production", canary_percent=150)


# --- monitor decision (§27) ---


def test_insufficient_evidence_is_never_a_verdict() -> None:
    assert rollback_decision(1, 1) == "insufficient"
    assert rollback_decision(2, 0) == "insufficient"


def test_no_baseline_absolute_check_rolls_back_all_fail() -> None:
    """§27: with no incumbent, 3/3 failures exceed the acceptable
    failure rate — the canary rolls back on its OWN evidence."""
    assert rollback_decision(3, 0) == "rollback"
    assert rollback_decision(0, 3) == "healthy"


def test_no_baseline_healthy_below_acceptable_rate() -> None:
    assert rollback_decision(1, 2) == "healthy"  # 0.33 < 0.5


def test_relative_margin_rolls_back_on_regression() -> None:
    """canary 0.8 vs incumbent 0.5 + margin 0.2 → rollback."""
    assert rollback_decision(8, 2, 5, 5) == "rollback"


def test_relative_margin_healthy_at_or_below_baseline() -> None:
    assert rollback_decision(5, 5, 5, 5) == "healthy"
    assert rollback_decision(2, 8, 5, 5) == "healthy"


def test_constants_are_the_documented_policy() -> None:
    assert MIN_CANARY_OUTCOMES == 3
    assert REGRESSION_MARGIN == 0.2
    assert MAX_ACCEPTABLE_FAIL_RATE == 0.5
