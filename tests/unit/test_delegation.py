"""DelegationManager unit tests (INV-02/INV-03 invariants)."""

from datetime import UTC, datetime

import pytest

from aci.domain.runtime.actions import DelegationRequest
from aci.domain.runtime.authority import (
    FilesystemScope,
    GrantEnvelope,
    NetworkScope,
)
from aci.domain.runtime.state import (
    BudgetLedger,
    RunState,
    RuntimeStateSnapshot,
    TaskState,
)
from aci.domain.runtime.stop_reason import RunStatus
from aci.runtime.delegation import (
    DelegationBudgetError,
    DelegationDisabled,
    DelegationManager,
)


def _parent(depth: int = 0, consumed: int = 0) -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="parent", status=RunStatus.RUNNING, created_at=datetime.now(UTC)),
        task=TaskState(task_id="t-parent", objective="parent objective"),
        budget=BudgetLedger(
            max_total_tokens=100_000,
            consumed_input_tokens=consumed,
        ),
        grants=GrantEnvelope(
            filesystem=FilesystemScope(read=["/repo"], write=["/repo/src"]),
            network=NetworkScope(enabled=True, allowed_hosts=["pypi.org"]),
        ),
        depth=depth,
    )


def _request(tokens: int = 20_000) -> DelegationRequest:
    return DelegationRequest(
        subtask_objective="inspect test failures",
        requested_budget_tokens=tokens,
        requested_budget_tool_calls=10,
    )


class TestDelegationManager:
    def test_disabled_by_default(self) -> None:
        dm = DelegationManager(enabled=False)
        with pytest.raises(DelegationDisabled):
            dm.derive_child(_parent(), _request(), child_grants=GrantEnvelope())

    def test_child_grants_never_exceed_parent(self) -> None:
        dm = DelegationManager(enabled=True)
        wider = GrantEnvelope(
            filesystem=FilesystemScope(read=["/repo", "/etc"], write=["/", "/repo/src"]),
            network=NetworkScope(enabled=True, allowed_hosts=["pypi.org", "evil.com"]),
        )
        _, grants, _ = dm.derive_child(_parent(), _request(), child_grants=wider)
        assert grants.filesystem.read == ["/repo"]  # /etc dropped
        assert grants.filesystem.write == ["/repo/src"]  # "/" dropped
        assert grants.network.allowed_hosts == ["pypi.org"]  # evil.com dropped

    def test_depth_limit_enforced(self) -> None:
        dm = DelegationManager(enabled=True, max_depth=1)
        with pytest.raises(DelegationBudgetError, match="depth"):
            dm.derive_child(_parent(depth=1), _request(), child_grants=GrantEnvelope())

    def test_budget_carved_not_multiplied(self) -> None:
        dm = DelegationManager(enabled=True)
        _, _, child_budget = dm.derive_child(
            _parent(), _request(tokens=999_999), child_grants=GrantEnvelope()
        )
        assert child_budget.max_total_tokens <= 100_000  # INV-03

    def test_insufficient_remaining_budget_refuses(self) -> None:
        dm = DelegationManager(enabled=True)
        with pytest.raises(DelegationBudgetError, match="below child reserve"):
            dm.derive_child(_parent(consumed=95_000), _request(), child_grants=GrantEnvelope())

    def test_max_children_bounded(self) -> None:
        dm = DelegationManager(enabled=True, max_children=1)
        dm.derive_child(_parent(), _request(), child_grants=GrantEnvelope())
        with pytest.raises(DelegationBudgetError, match="max children"):
            dm.derive_child(_parent(), _request(), child_grants=GrantEnvelope())

    def test_child_context_is_projected_not_cloned(self) -> None:
        dm = DelegationManager(enabled=True)
        contract, _, _ = dm.derive_child(_parent(), _request(), child_grants=GrantEnvelope())
        assert contract.parent_task_id == "t-parent"
        assert contract.global_context == "parent objective"  # summary only
        assert contract.budget is not None

    def test_record_unknown_child_rejected(self) -> None:
        dm = DelegationManager(enabled=True)
        with pytest.raises(ValueError, match="unknown child"):
            dm.record_child_result("parent", "ghost")
