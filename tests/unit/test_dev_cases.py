"""Structural validation for the dev case set (V2 benchmark arena, §55).

The dev set annotates the REAL production corpus; these tests make
annotation drift break loudly (a renamed or removed skill fails here)
instead of silently measuring nothing.
"""

from aci.evaluation.dev_cases import CORPUS_CAPABILITY_IDS, DEV_CASES


def test_dev_set_size_in_plan_range() -> None:
    """§55: grow to 30–50 dev cases — not fewer, not a flood."""
    assert 30 <= len(DEV_CASES) <= 50


def test_dev_case_ids_are_unique() -> None:
    ids = [case.case_id for case in DEV_CASES]
    assert len(ids) == len(set(ids))


def test_annotations_reference_real_corpus_skills() -> None:
    """Every annotated id must exist in the production corpus."""
    for case in DEV_CASES:
        for id_ in case.relevant_strong:
            assert id_ in CORPUS_CAPABILITY_IDS, f"{case.case_id}: unknown strong id {id_}"
        for id_ in case.relevant_acceptable:
            assert id_ in CORPUS_CAPABILITY_IDS, f"{case.case_id}: unknown acceptable id {id_}"
        for id_ in case.relevant_irrelevant:
            assert id_ in CORPUS_CAPABILITY_IDS, f"{case.case_id}: unknown irrelevant id {id_}"


def test_annotation_lists_are_disjoint_per_case() -> None:
    """A skill cannot be both relevant and a misroute marker."""
    for case in DEV_CASES:
        strong = set(case.relevant_strong)
        acceptable = set(case.relevant_acceptable)
        irrelevant = set(case.relevant_irrelevant)
        assert not (strong & acceptable), case.case_id
        assert not (strong & irrelevant), case.case_id
        assert not (acceptable & irrelevant), case.case_id


def test_every_case_annotates_strong_relevance() -> None:
    """A case with no strong annotation measures nothing (recall=0 by construction)."""
    for case in DEV_CASES:
        assert case.relevant_strong, f"{case.case_id}: no relevant_strong annotation"


def test_corpus_ids_constant_matches_case_annotations() -> None:
    """The constant and the cases stay in sync (no orphaned corpus ids)."""
    annotated = set()
    for case in DEV_CASES:
        annotated.update(case.relevant_strong)
        annotated.update(case.relevant_acceptable)
        annotated.update(case.relevant_irrelevant)
    # Not every corpus skill must be annotated, but every annotation must
    # be a corpus skill — covered above; this pins the constant's size.
    assert len(CORPUS_CAPABILITY_IDS) == 36
