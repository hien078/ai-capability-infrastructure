"""DelegationManager unit tests (INV-02/INV-03 invariants)."""

from datetime import UTC, datetime

import pytest

from aci.domain.runtime.actions import DelegationRequest
from aci.domain.runtime.authority import (
    FilesystemScope,
    GrantEnvelope,
    NetworkScope,
    ProcessScope,
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


def _parent(
    depth: int = 0, consumed: int = 0, budget: BudgetLedger | None = None
) -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="parent", status=RunStatus.RUNNING, created_at=datetime.now(UTC)),
        task=TaskState(
            task_id="t-parent",
            objective="parent objective",
            progress=["turn 1: read 40 files"],
        ),
        budget=budget
        or BudgetLedger(
            max_total_tokens=100_000,
            consumed_input_tokens=consumed,
        ),
        grants=GrantEnvelope(
            filesystem=FilesystemScope(read=["/repo"], write=["/repo/src"]),
            network=NetworkScope(enabled=True, allowed_hosts=["pypi.org"]),
            process=ProcessScope(allowed_prefixes=["git", "pytest"]),
        ),
        depth=depth,
        changed_resources=["file:/repo/src/a.py"],
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
            process=ProcessScope(allowed_prefixes=["git", "rm"]),
        )
        _, grants, _ = dm.derive_child(_parent(), _request(), child_grants=wider)
        assert grants.filesystem.read == ["/repo"]  # /etc dropped
        assert grants.filesystem.write == ["/repo/src"]  # "/" dropped
        assert grants.network.allowed_hosts == ["pypi.org"]  # evil.com dropped
        assert grants.process.allowed_prefixes == ["git"]  # rm dropped

    def test_child_may_take_a_narrower_scope_than_parent(self) -> None:
        """INV-02 is ⊆, not equality: a sub-path or a longer command prefix
        lies within the parent's authority and is kept."""
        dm = DelegationManager(enabled=True)
        narrower = GrantEnvelope(
            filesystem=FilesystemScope(read=["/repo/tests"], write=["/repo/src/pkg"]),
            process=ProcessScope(allowed_prefixes=["git status", "pytest -q"]),
        )
        _, grants, _ = dm.derive_child(_parent(), _request(), child_grants=narrower)
        assert grants.filesystem.read == ["/repo/tests"]
        assert grants.filesystem.write == ["/repo/src/pkg"]
        assert grants.process.allowed_prefixes == ["git status", "pytest -q"]
        assert not grants.network.enabled

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

    def test_every_dimension_carved_from_parent_remaining(self) -> None:
        """INV-03: the child gets at most what the parent has LEFT — turns,
        tool calls, wall time, output tokens, cost and recoveries included."""
        parent = _parent(
            budget=BudgetLedger(
                max_turns=10,
                consumed_turns=8,
                max_total_tokens=100_000,
                consumed_input_tokens=30_000,
                consumed_output_tokens=10_000,
                reserved_tokens=5_000,
                max_output_tokens=30_000,
                max_tool_calls=10,
                consumed_tool_calls=9,
                max_wall_time_seconds=100,
                consumed_wall_time_seconds=90.4,
                max_cost_usd=1.0,
                consumed_cost_usd=0.95,
                max_recoveries=8,
                consumed_recoveries=6,
            )
        )
        dm = DelegationManager(enabled=True)
        _, _, child = dm.derive_child(
            parent, _request(tokens=999_999), child_grants=GrantEnvelope()
        )
        assert child.max_turns == 2
        assert child.max_total_tokens == 55_000
        assert child.max_output_tokens == 20_000
        assert child.max_tool_calls == 1
        assert child.max_wall_time_seconds == 9
        assert child.max_cost_usd == pytest.approx(0.05)
        assert child.max_recoveries == 2

    def test_exhausted_dimension_refuses_delegation(self) -> None:
        dm = DelegationManager(enabled=True)
        spent_turns = BudgetLedger(max_turns=3, consumed_turns=3, max_total_tokens=100_000)
        with pytest.raises(DelegationBudgetError, match="remaining turns"):
            dm.derive_child(_parent(budget=spent_turns), _request(), child_grants=GrantEnvelope())
        spent_calls = BudgetLedger(
            max_tool_calls=2, consumed_tool_calls=2, max_total_tokens=100_000
        )
        with pytest.raises(DelegationBudgetError, match="remaining tool calls"):
            dm.derive_child(_parent(budget=spent_calls), _request(), child_grants=GrantEnvelope())

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
        assert "read 40 files" not in contract.global_context  # no parent progress/transcript
        assert contract.budget is not None

    def test_record_unknown_child_rejected(self) -> None:
        dm = DelegationManager(enabled=True)
        with pytest.raises(ValueError, match="unknown child"):
            dm.record_child_result("parent", "ghost")
