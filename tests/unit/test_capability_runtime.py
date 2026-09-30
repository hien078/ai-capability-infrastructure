"""CapabilityRuntime unit tests (digest verification, pinning, refresh bounds)."""

from datetime import UTC, datetime

import pytest

from aci.domain.runtime.actions import CapabilityRequest
from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.state import (
    BudgetLedger,
    CapabilityActivation,
    RunState,
    RuntimeStateSnapshot,
    TaskState,
)
from aci.domain.runtime.stop_reason import RunStatus
from aci.runtime.capability_runtime import (
    CapabilityRuntime,
    CapabilitySearchError,
)


class FakeSelection:
    def __init__(self, cid: str, version: str, digest: str, tokens: int = 500) -> None:
        self.capability_id = cid
        self.version = version
        self.payload_ref = f"cap://{cid}/{version}"
        self.digest = digest
        self.estimated_context_tokens = tokens


class FakeACI:
    def __init__(self, selections: list[FakeSelection], payloads: dict[str, bytes]) -> None:
        self._selections = selections
        self._payloads = payloads
        self.search_calls: list[CapabilityRequest] = []

    def search(self, request: CapabilityRequest) -> list[FakeSelection]:
        self.search_calls.append(request)
        return self._selections

    def resolve(self, capability_id: str, version: str) -> tuple[bytes, str]:
        payload = self._payloads[f"{capability_id}@{version}"]
        import hashlib

        return payload, hashlib.sha256(payload).hexdigest()


class FakeFeedback:
    def __init__(self) -> None:
        self.reports: list[dict[str, object]] = []

    def report(self, **kwargs: object) -> None:
        self.reports.append(kwargs)


def _snapshot(n_active: int = 0) -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="run-1", status=RunStatus.RUNNING, created_at=datetime.now(UTC)),
        task=TaskState(task_id="t", objective="o"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
        active_capabilities=[
            CapabilityActivation(
                capability_id=f"cap-{i}",
                version="1.0.0",
                digest=f"d{i}",
                activation_id=f"act-{i}",
            )
            for i in range(n_active)
        ],
    )


def _request() -> CapabilityRequest:
    return CapabilityRequest(objective="diagnose postgres query plan", reason="gap")


class TestCapabilityRuntime:
    def _runtime(self, feedback: FakeFeedback | None = None) -> tuple[CapabilityRuntime, FakeACI]:
        import hashlib

        payload = b"# skill body"
        digest = hashlib.sha256(payload).hexdigest()
        aci = FakeACI([FakeSelection("cap-x", "1.0.0", digest)], {"cap-x@1.0.0": payload})
        return CapabilityRuntime(aci, feedback=feedback), aci  # type: ignore[arg-type]

    def test_activate_verifies_digest(self) -> None:
        cr, _ = self._runtime()
        activation, payload = cr.activate(cr.search(_request())[0], run_id="run-1")
        assert payload == b"# skill body"
        assert activation.status == "ACTIVE"
        assert activation.capability_id == "cap-x"
        assert activation.version == "1.0.0"

    def test_digest_mismatch_never_loads(self) -> None:
        aci = FakeACI(
            [FakeSelection("cap-x", "1.0.0", "deadbeef")],
            {"cap-x@1.0.0": b"tampered"},
        )
        cr = CapabilityRuntime(aci)
        with pytest.raises(CapabilitySearchError, match="digest mismatch"):
            cr.activate(cr.search(_request())[0], run_id="run-1")

    def test_version_pinned_per_activation(self) -> None:
        cr, _ = self._runtime()
        activation, _ = cr.activate(cr.search(_request())[0], run_id="run-1")
        assert activation.version == "1.0.0"  # pinned, never silently upgraded

    def test_cache_hit_is_digest_verified_too(self) -> None:
        """A pinned version has one digest: a later selection claiming another
        digest for the same (id, version) must not be served from cache."""
        cr, aci = self._runtime()
        good = cr.search(_request())[0]
        cr.activate(good, run_id="run-1")
        resolves_before = len(aci.search_calls)
        with pytest.raises(CapabilitySearchError, match="digest mismatch"):
            cr.activate(FakeSelection("cap-x", "1.0.0", "deadbeef"), run_id="run-1")
        assert len(aci.search_calls) == resolves_before
        # The genuine selection still activates (cache intact, idempotent).
        activation, _ = cr.activate(good, run_id="run-1")
        assert activation.digest == good.digest

    def test_handle_request_matches_kernel_contract(self) -> None:
        cr, _ = self._runtime()
        activations = cr.handle_request(_request(), _snapshot())
        assert [a.capability_id for a in activations] == ["cap-x"]
        assert all(isinstance(a, CapabilityActivation) for a in activations)

    def test_refresh_budget_bounded(self) -> None:
        cr, _ = self._runtime()
        cr.handle_request(_request(), _snapshot())
        cr.handle_request(_request(), _snapshot())
        with pytest.raises(CapabilitySearchError, match="refresh budget"):
            cr.handle_request(_request(), _snapshot())

    def test_max_loaded_enforced(self) -> None:
        cr, _ = self._runtime()
        with pytest.raises(CapabilitySearchError, match="max loaded"):
            cr.handle_request(_request(), _snapshot(n_active=8))

    def test_feedback_carries_outcomes(self) -> None:
        fb = FakeFeedback()
        cr, _ = self._runtime(feedback=fb)
        cr.report_outcome(
            capability_id="cap-x",
            version="1.0.0",
            outcome="failed_verification",
            failure_class="CAPABILITY_INSUFFICIENT",
            evidence_refs=["artifact://v/1"],
        )
        assert fb.reports[0]["outcome"] == "failed_verification"
        assert fb.reports[0]["evidence_refs"] == ["artifact://v/1"]

    def test_search_receives_normalized_need_only(self) -> None:
        cr, aci = self._runtime()
        cr.search(_request())
        req = aci.search_calls[0]
        assert req.objective == "diagnose postgres query plan"
        assert not req.constraints  # no conversation history smuggled in
