"""Mechanism dedup tests (crawl.md STEP 7, §13).

§13 verbatim: text similarity is insufficient — "self-healing coding
loop" and "iterative autonomous repair" may both BE observe→diagnose→
patch→test→retry; two things called "agent memory" may be entirely
different mechanisms. The signature compares STRUCTURE, not prose.
"""

from aci.domain.acquisition.mechanism import (
    MechanismSignature,
    dedupe_mechanisms,
    signature_from_mined,
    signature_similarity,
)


def _sig(mid: str, name: str, **over: object) -> MechanismSignature:
    fields: dict[str, object] = {
        "mined_id": mid,
        "name": name,
        "state_transitions": ["observe", "diagnose", "patch", "test", "retry"],
        "tools_required": ["shell", "test-runner"],
        "feedback_source": "test output",
        "control_loop": "iterative",
        "memory_behavior": "stateless",
        "termination_condition": "max_attempts",
        "verification_behavior": "self-verified",
    }
    fields.update(over)
    return MechanismSignature(**fields)  # type: ignore[arg-type]


def test_same_mechanism_different_names_is_duplicate() -> None:
    """§13: 'self-healing coding loop' vs 'iterative autonomous repair' —
    identical structure, different prose → high similarity."""
    a = _sig("m1", "self-healing-coding-loop")
    b = _sig("m2", "iterative-autonomous-repair")
    sim = signature_similarity(a, b)
    assert sim >= 0.75
    assert dedupe_mechanisms([a, b]) == [("m1", "m2", sim)]


def test_same_name_different_mechanism_is_not_duplicate() -> None:
    """§13 converse: two things called 'agent memory' with different
    structure → low similarity, no match."""
    a = _sig("m1", "agent-memory", state_transitions=["write", "read"])
    b = _sig(
        "m2",
        "agent-memory",
        state_transitions=["embed", "search", "rerank"],
        tools_required=["vector-db"],
        feedback_source="none",
        control_loop="linear",
        memory_behavior="persistent",
    )
    assert signature_similarity(a, b) < 0.5
    assert dedupe_mechanisms([a, b]) == []


def test_empty_fields_never_match() -> None:
    """§60.9: missing evidence is never a match — two signatures with
    empty structural fields score honestly low, not vacuously 1.0."""
    a = MechanismSignature(mined_id="a", name="x")
    b = MechanismSignature(mined_id="b", name="y")
    assert signature_similarity(a, b) == 0.0


def test_partial_overlap_scores_between() -> None:
    a = _sig("a", "x")
    b = _sig("b", "y", state_transitions=["observe", "diagnose", "patch"])
    sim = signature_similarity(a, b)
    assert sim > 0.8  # 3/5 transitions + tools + 4 categorical fields shared


def test_dedupe_returns_matches_never_merges() -> None:
    """The §27-auto2 rule carries into §13: output is (left, right, sim)
    evidence pairs — no merge, no delete, no action field."""
    a = _sig("a", "x")
    b = _sig("b", "y")
    matches = dedupe_mechanisms([a, b])
    assert matches and not hasattr(matches[0], "action")


def test_signature_from_mined_only_takes_structural_fields() -> None:
    """The miner's mechanism PROSE is not the signature — only the
    structural fields; prose never leaks into similarity."""
    mined = {
        "name": "retry-engine",
        "mechanism": "a very long prose description of retrying things",
        "state_transitions": ["try", "fail", "backoff", "retry"],
        "tools_required": ["http"],
        "control_loop": "iterative",
    }
    sig = signature_from_mined(mined)
    assert sig.state_transitions == ["try", "fail", "backoff", "retry"]
    assert not hasattr(sig, "mechanism")  # prose is not on the signature
