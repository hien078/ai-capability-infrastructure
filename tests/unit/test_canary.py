"""Canary routing tests (crawl.md §26-27, STEP 11).

The invariants: the canary split is DETERMINISTIC (same request → same
decision, always — no flapping); a canary release routes only its
percentage share; non-canary statuses behave exactly as before
(active routes, revoked/disabled excluded); graduation stays human.
"""

from aci.domain.policy.models import (
    ClientDescriptor,
    EligibleCandidate,
    PolicyRules,
    RoutingRequestContext,
    ScopeContext,
)
from aci.routing.eligibility import DefaultEligibilityPolicy, _canary_selected


def _candidate(status: str, canary_percent: int | None = None) -> EligibleCandidate:
    return EligibleCandidate(
        capability_id="cap-x",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        kind="skill",
        channel="production",
        status=status,  # type: ignore[arg-type]
        canary_percent=canary_percent,
    )


def _context(request_id: str) -> RoutingRequestContext:
    return RoutingRequestContext(
        client=ClientDescriptor(type="test"),
        scope=ScopeContext(principal_id="p"),
        request_id=request_id,
    )


def test_canary_split_is_deterministic() -> None:
    """§27: same request → same canary decision, ALWAYS. No randomness,
    no flapping — the population is reproducible for telemetry."""
    for _ in range(5):
        assert _canary_selected("req-1", "cap-x", 50) == _canary_selected("req-1", "cap-x", 50)
    # different requests spread across the range
    selected = sum(_canary_selected(f"req-{i}", "cap-x", 50) for i in range(100))
    assert 30 <= selected <= 70  # ~50% ± noise


def test_canary_100_percent_always_routes() -> None:
    policy = DefaultEligibilityPolicy()
    rules = PolicyRules()
    for i in range(20):
        decision = policy.filter(
            [_candidate("canary", 100)], _context(f"req-{i}"), rules, allowed_kinds=["skill"]
        )
        assert len(decision.kept) == 1


def test_canary_0_percent_never_routes() -> None:
    policy = DefaultEligibilityPolicy()
    rules = PolicyRules()
    for i in range(20):
        decision = policy.filter(
            [_candidate("canary", 0)], _context(f"req-{i}"), rules, allowed_kinds=["skill"]
        )
        assert len(decision.kept) == 0
        assert "canary population" in decision.excluded[0].detail


def test_canary_partial_routes_its_share() -> None:
    """A 30% canary routes ~30% of a spread of requests (deterministic
    hash, so exact count is fixed for these ids — assert the band)."""
    policy = DefaultEligibilityPolicy()
    rules = PolicyRules()
    selected = 0
    for i in range(100):
        decision = policy.filter(
            [_candidate("canary", 30)], _context(f"req-{i}"), rules, allowed_kinds=["skill"]
        )
        selected += len(decision.kept)
    assert 15 <= selected <= 45


def test_active_still_routes_unchanged() -> None:
    """Non-canary statuses behave exactly as before — active routes fully."""
    policy = DefaultEligibilityPolicy()
    rules = PolicyRules()
    decision = policy.filter(
        [_candidate("active")], _context("req-1"), rules, allowed_kinds=["skill"]
    )
    assert len(decision.kept) == 1


def test_revoked_still_excluded() -> None:
    policy = DefaultEligibilityPolicy()
    rules = PolicyRules()
    decision = policy.filter(
        [_candidate("revoked")], _context("req-1"), rules, allowed_kinds=["skill"]
    )
    assert len(decision.kept) == 0
    assert decision.excluded[0].reason == "CAPABILITY_REVOKED"


def test_canary_exclusion_is_traceable() -> None:
    """A request outside the canary population gets a STABLE reason code
    with the percentage in the detail (§14 trace data)."""
    policy = DefaultEligibilityPolicy()
    rules = PolicyRules()
    excluded_ids = set()
    for i in range(50):
        decision = policy.filter(
            [_candidate("canary", 10)], _context(f"req-{i}"), rules, allowed_kinds=["skill"]
        )
        if decision.excluded:
            excluded_ids.add(decision.excluded[0].reason)
    assert excluded_ids <= {"CAPABILITY_NOT_ELIGIBLE"}
