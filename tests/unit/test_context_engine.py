"""ContextEngine unit acceptance (harness.md §9, §35, §50): budgeted selection, offload, compaction."""  # noqa: E501

from datetime import UTC, datetime

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.state import (
    BudgetLedger,
    PlanItem,
    RunState,
    RuntimeStateSnapshot,
    TaskState,
)
from aci.domain.runtime.stop_reason import RunStatus
from aci.domain.runtime.tools import OutputPolicy, ToolObservation, ToolSpec
from aci.runtime.context_engine import (
    ContextBudget,
    ContextEngine,
    ContextItem,
    estimate_tokens,
)

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def snapshot(
    *,
    objective: str = "do the thing",
    plan: list[PlanItem] | None = None,
) -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="r1", status=RunStatus.CREATED, created_at=NOW),
        task=TaskState(task_id="t1", objective=objective),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
        plan=plan or [],
    )


def item(
    item_id: str,
    kind: str = "history",
    content: str = "x" * 40,
    *,
    priority: int = 1,
    pin_policy: str = "droppable",
    relevance: float = 0.0,
    created_at_turn: int = 0,
    last_used_turn: int = 0,
) -> ContextItem:
    return ContextItem(
        item_id=item_id,
        kind=kind,  # type: ignore[arg-type]
        content=content,
        priority=priority,
        estimated_tokens=estimate_tokens(content),
        created_at_turn=created_at_turn,
        last_used_turn=last_used_turn,
        pin_policy=pin_policy,  # type: ignore[arg-type]
        source="test",
        relevance=relevance,
    )


def tool(policy: OutputPolicy | None = None) -> ToolSpec:
    return ToolSpec(
        tool_id="t",
        version="1",
        output_policy=policy if policy is not None else OutputPolicy(),
    )


def observation(inline: str, call_id: str = "c1") -> ToolObservation:
    return ToolObservation(tool_call_id=call_id, tool_id="t", inline_output=inline)


def test_pinned_items_always_included_even_when_budget_tiny():
    engine = ContextEngine(ContextBudget(total_tokens=8))
    inv = item("inv", kind="invariant", content="never drop me", pin_policy="pinned")
    task = item("obj", kind="task", content="objective", pin_policy="pinned")
    engine.register(inv)
    engine.register(task)
    ctx = engine.build(snapshot(), turn=1)
    ids = [i.item_id for i in ctx.items]
    assert "inv" in ids
    assert any(i.item_id.startswith("task:") for i in ctx.items)
    assert task.item_id in ids


def test_droppable_lowest_score_dropped_first_under_pressure():
    # task item ~5 tokens; only one 10-token droppable fits in 15.
    engine = ContextEngine(ContextBudget(total_tokens=15))
    engine.register(item("low", content="y" * 40, relevance=0.0, last_used_turn=0))
    engine.register(item("high", content="y" * 40, relevance=1.0, last_used_turn=5))
    ctx = engine.build(snapshot(), turn=5)
    assert "high" in [i.item_id for i in ctx.items]
    assert "low" in ctx.dropped_item_ids


def test_compaction_preserves_protected_kinds_and_merges_old_history():
    engine = ContextEngine(ContextBudget(total_tokens=1000), compaction_window=2)
    engine.register(item("inv", kind="invariant", content="keep", pin_policy="pinned"))
    engine.register(item("cap", kind="capability", content="keep too"))
    engine.register(item("old1", kind="history", content="old history 1", created_at_turn=0))
    engine.register(item("old2", kind="observation", content="old obs 2", created_at_turn=1))
    engine.register(item("new", kind="history", content="recent", created_at_turn=10))
    engine.compact(turn=10)
    assert engine.get("inv") is not None
    assert engine.get("cap") is not None
    assert engine.get("old1") is None
    assert engine.get("old2") is None
    assert engine.get("new") is not None
    merged = engine.get("compaction:10")
    assert merged is not None
    assert "old1" in merged.content
    assert "old2" in merged.content


def test_compaction_never_touches_pinned_droppable_protected_kinds():
    engine = ContextEngine(ContextBudget(total_tokens=1000), compaction_window=1)
    engine.register(
        item(
            "pin-h",
            kind="history",
            content="pinned",
            pin_policy="pinned",
            created_at_turn=0,
        )
    )
    engine.compact(turn=10)
    assert engine.get("pin-h") is not None


def test_offload_returns_ref_and_stub_keeps_ref():
    engine = ContextEngine(ContextBudget(total_tokens=1000))
    original = item("big", kind="observation", content="z" * 400)
    engine.register(original)
    ref = engine.offload(original)
    assert ref.startswith("artifact://")
    stub = engine.get("big")
    assert stub is not None
    assert stub.kind == "reference"
    assert ref in stub.content
    ctx = engine.build(snapshot(), turn=1)
    assert ref in ctx.offloaded_refs


def test_token_estimation():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 1
    assert estimate_tokens("abcdefgh") == 2


def test_pressure_tier_boundaries():
    engine = ContextEngine(ContextBudget(total_tokens=1000))
    assert engine.pressure(0) == "normal"
    assert engine.pressure(549) == "normal"
    assert engine.pressure(550) == "clear"
    assert engine.pressure(699) == "clear"
    assert engine.pressure(700) == "compact"
    assert engine.pressure(819) == "compact"
    assert engine.pressure(820) == "aggressive"
    assert engine.pressure(900) == "aggressive"
    assert engine.pressure(901) == "critical"
    assert engine.pressure(1000) == "critical"


def test_oversized_tool_output_offloaded_not_inlined():
    engine = ContextEngine(ContextBudget(total_tokens=1000))
    big = "a" * 60_000  # 15000 tokens > default max_inline_tokens 3000
    got = engine.observe_tool_output(observation(big), tool())
    assert got.kind == "reference"
    assert "artifact://tool/c1" in got.content
    assert big not in got.content
    assert got.estimated_tokens <= 100


def test_small_tool_output_inlined():
    engine = ContextEngine(ContextBudget(total_tokens=100_000))
    small = "result: ok"
    got = engine.observe_tool_output(observation(small), tool())
    assert got.kind == "observation"
    assert got.content == small


def test_build_respects_total_budget():
    engine = ContextEngine(ContextBudget(total_tokens=50))
    for n in range(10):
        engine.register(item(f"h{n}", content="y" * 40, relevance=0.5))
    ctx = engine.build(snapshot(), turn=1)
    assert ctx.total_tokens <= 50
    assert ctx.total_tokens == sum(i.estimated_tokens for i in ctx.items)
    assert ctx.dropped_item_ids  # overflow was dropped, not silently included


def test_active_plan_and_capabilities_included():
    engine = ContextEngine(ContextBudget(total_tokens=1000))
    snap = snapshot(
        plan=[PlanItem(item_id="p1", objective="step", status="running")],
    )
    ctx = engine.build(snap, turn=1)
    kinds = [i.kind for i in ctx.items]
    assert "plan" in kinds
    assert "task" in kinds


def test_empty_bundle_is_valid():
    engine = ContextEngine(ContextBudget(total_tokens=1000))
    ctx = engine.build(snapshot(), turn=0)
    assert ctx.total_tokens >= 0
    assert ctx.dropped_item_ids == []
