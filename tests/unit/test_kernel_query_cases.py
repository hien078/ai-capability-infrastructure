"""Structural validation for the kernel-style capability-request case set.

Same contract as ``test_dev_cases``: annotations reference the REAL production
corpus, so a renamed/removed skill breaks loudly instead of silently
measuring nothing.
"""

from aci.evaluation.dev_cases import CORPUS_CAPABILITY_IDS
from aci.evaluation.kernel_query_cases import KERNEL_QUERY_CASES


def test_kernel_set_size() -> None:
    assert 20 <= len(KERNEL_QUERY_CASES) <= 50


def test_kernel_case_ids_are_unique() -> None:
    ids = [case.case_id for case in KERNEL_QUERY_CASES]
    assert len(ids) == len(set(ids))


def test_annotations_reference_real_corpus_skills() -> None:
    for case in KERNEL_QUERY_CASES:
        for id_ in [*case.relevant_strong, *case.relevant_acceptable, *case.relevant_irrelevant]:
            assert id_ in CORPUS_CAPABILITY_IDS, f"{case.case_id}: unknown id {id_}"


def test_annotation_lists_are_disjoint_per_case() -> None:
    for case in KERNEL_QUERY_CASES:
        strong = set(case.relevant_strong)
        acceptable = set(case.relevant_acceptable)
        irrelevant = set(case.relevant_irrelevant)
        assert not (strong & acceptable), case.case_id
        assert not (strong & irrelevant), case.case_id
        assert not (acceptable & irrelevant), case.case_id


def test_every_case_annotates_strong_relevance() -> None:
    for case in KERNEL_QUERY_CASES:
        assert case.relevant_strong, f"{case.case_id}: no relevant_strong annotation"


def test_cases_have_the_kernel_request_shape() -> None:
    """objective line + at least one constraint line (normalized_need shape)."""
    for case in KERNEL_QUERY_CASES:
        lines = case.task_text.split("\n")
        assert len(lines) >= 2, case.case_id
        assert all(line.strip() == line and line for line in lines), case.case_id


def test_real_kernel_queries_are_included() -> None:
    """The verbatim harness-kernel route-run queries anchor the set."""
    real = [c for c in KERNEL_QUERY_CASES if c.case_id.startswith("kernel-real-")]
    assert len(real) >= 2
    assert all("systematic-debugging" in c.relevant_strong for c in real)
