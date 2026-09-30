"""Mechanism-level deduplication (crawl.md STEP 7, §13).

Text similarity is insufficient (§13 verbatim): "self-healing coding
loop" and "iterative autonomous repair" may both BE
observe→diagnose→patch→test→retry; conversely two things called
"agent memory" may use entirely different mechanisms.

MechanismSignature captures WHAT A MECHANISM DOES structurally:
state transitions, tools required, feedback source, control loop,
memory behavior, termination, verification. Dedup then compares
signatures, not prose.

The signature is extracted by the LLM miner (already producing
mechanism descriptions) or derived deterministically from the mined
capability fields — both feed the same comparison.
"""

from typing import Any

from pydantic import BaseModel, Field


class MechanismSignature(BaseModel):
    """§13: the structural fingerprint of one mechanism."""

    model_config = {"frozen": True}

    mined_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    #: ordered state transitions, e.g. observe → diagnose → patch → test → retry
    state_transitions: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    feedback_source: str = ""  # test output | user | static check | none
    control_loop: str = ""  # linear | iterative | recursive | event-driven
    memory_behavior: str = ""  # stateless | session | persistent | none
    termination_condition: str = ""  # max_attempts | success | human | never
    verification_behavior: str = ""  # self-verified | external | unverified
    extracted_by: str = "unknown"


def signature_similarity(a: MechanismSignature, b: MechanismSignature) -> float:
    """Deterministic signature overlap (0..1).

    Compares the STRUCTURAL fields as sets: state transitions (the
    strongest signal), tools, then the categorical fields. Two
    mechanisms with identical transitions/tools/loops are duplicates
    regardless of what their READMEs call them.
    """
    # Identity shortcut only for REAL identities — a placeholder identity
    # ("unknown" name or mined_id, §60.9: missing evidence is never a
    # match) must fall through to the structural comparison, not
    # vacuously report 1.0.
    if (
        a.name == b.name
        and a.mined_id == b.mined_id
        and "unknown" not in (a.name, a.mined_id, b.name, b.mined_id)
    ):
        return 1.0
    scores: list[float] = []
    # state transitions: set overlap over the union (order-insensitive;
    # order-SENSITIVE comparison is the LLM comparator's job)
    ta, tb = set(a.state_transitions), set(b.state_transitions)
    if ta or tb:
        scores.append(len(ta & tb) / len(ta | tb))
    # tools
    oa, ob = set(a.tools_required), set(b.tools_required)
    if oa or ob:
        scores.append(len(oa & ob) / len(oa | ob))
    # categorical fields: exact match = 1, else 0 (unknown "" never matches)
    for fa, fb in (
        (a.feedback_source, b.feedback_source),
        (a.control_loop, b.control_loop),
        (a.memory_behavior, b.memory_behavior),
        (a.termination_condition, b.termination_condition),
        (a.verification_behavior, b.verification_behavior),
    ):
        if fa and fb:
            scores.append(1.0 if fa == fb else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def dedupe_mechanisms(
    signatures: list[MechanismSignature],
    *,
    threshold: float = 0.75,
) -> list[tuple[str, str, float]]:
    """Pairwise mechanism-duplicate detection (§13 layer 5).

    Returns (left, right, similarity) pairs above the threshold —
    MATCHES, never merges (the same rule as text dedup §27 auto2:
    the caller decides from the evidence).
    """
    matches: list[tuple[str, str, float]] = []
    for i, a in enumerate(signatures):
        for b in signatures[i + 1 :]:
            sim = signature_similarity(a, b)
            if sim >= threshold:
                matches.append((a.mined_id, b.mined_id, round(sim, 3)))
    return matches


def signature_from_mined(mined: dict[str, Any]) -> MechanismSignature:
    """Derive a signature from a CapabilityMiner output dict.

    The miner's `mechanism` prose is NOT the signature — only the
    structural fields are. Where the miner did not fill them, the
    signature stays empty and similarity honestly drops (§60.9:
    missing evidence is never a match).
    """
    return MechanismSignature(
        mined_id=str(mined.get("mined_id", mined.get("name", "unknown"))),
        name=str(mined.get("name", "unknown")),
        state_transitions=[str(s) for s in mined.get("state_transitions", [])],
        tools_required=[str(t) for t in mined.get("tools_required", [])],
        feedback_source=str(mined.get("feedback_source", "")),
        control_loop=str(mined.get("control_loop", "")),
        memory_behavior=str(mined.get("memory_behavior", "")),
        termination_condition=str(mined.get("termination_condition", "")),
        verification_behavior=str(mined.get("verification_behavior", "")),
        extracted_by=str(mined.get("mined_by", "capability-miner")),
    )
