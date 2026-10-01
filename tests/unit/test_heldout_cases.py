"""Structural validation for the held-out routing case set (plan §3.1).

Same contract as ``test_dev_cases`` / ``test_kernel_query_cases``: annotations
reference the REAL production corpus, so a renamed/removed skill breaks loudly
instead of silently measuring nothing. On top of that, this set carries the
held-out-specific guarantees:

- task texts are DISJOINT from ``DEV_CASES`` and ``KERNEL_QUERY_CASES`` —
  no exact match and no >80% token overlap (both Jaccard and containment,
  so a paraphrase inside a longer text is caught too);
- the empty-bundle cases exist and are genuinely empty;
- the vocabulary pin matches the same real corpus the dev sets pin.
"""

import re

from aci.evaluation.dev_cases import CORPUS_CAPABILITY_IDS, DEV_CASES
from aci.evaluation.heldout_cases import (
    EMPTY_BUNDLE_CASE_IDS,
    HELDOUT_CASES,
    HELDOUT_CORPUS_CAPABILITY_IDS,
)
from aci.evaluation.kernel_query_cases import KERNEL_QUERY_CASES

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Disjointness threshold from the job spec: >80% token overlap = a copy.
_OVERLAP_LIMIT = 0.8


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    """|A∩B| / |A∪B| — overall similarity."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _containment(a: set[str], b: set[str]) -> float:
    """|A∩B| / min(|A|,|B|) — catches a paraphrase embedded in a longer text."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def test_heldout_set_size() -> None:
    """Plan §3.1: a usable held-out set is 20–50 cases."""
    assert 20 <= len(HELDOUT_CASES) <= 50


def test_heldout_case_ids_are_unique() -> None:
    ids = [case.case_id for case in HELDOUT_CASES]
    assert len(ids) == len(set(ids))


def test_heldout_ids_do_not_collide_with_dev_or_kernel() -> None:
    """A shared id would make results ambiguous across instruments."""
    other = {c.case_id for c in [*DEV_CASES, *KERNEL_QUERY_CASES]}
    for case in HELDOUT_CASES:
        assert case.case_id not in other, case.case_id


def test_annotations_reference_real_corpus_skills() -> None:
    """Every annotated id must exist in the production corpus vocabulary."""
    for case in HELDOUT_CASES:
        for id_ in [*case.relevant_strong, *case.relevant_acceptable, *case.relevant_irrelevant]:
            assert id_ in HELDOUT_CORPUS_CAPABILITY_IDS, f"{case.case_id}: unknown id {id_}"


def test_vocabulary_matches_the_real_corpus_pin() -> None:
    """The held-out pin and the dev pin must describe the SAME real corpus.

    Both were read from ``aci_bench`` production releases; drift means one
    of them is stale and the sets are no longer comparable.
    """
    assert HELDOUT_CORPUS_CAPABILITY_IDS == CORPUS_CAPABILITY_IDS


def test_annotation_lists_are_disjoint_per_case() -> None:
    """A skill cannot be both relevant and a misroute marker."""
    for case in HELDOUT_CASES:
        strong = set(case.relevant_strong)
        acceptable = set(case.relevant_acceptable)
        irrelevant = set(case.relevant_irrelevant)
        assert not (strong & acceptable), case.case_id
        assert not (strong & irrelevant), case.case_id
        assert not (acceptable & irrelevant), case.case_id


def test_non_empty_cases_annotate_strong_relevance() -> None:
    """A measurable case needs a strong annotation; empty-bundle cases do not."""
    for case in HELDOUT_CASES:
        if case.case_id in EMPTY_BUNDLE_CASE_IDS:
            continue
        assert case.relevant_strong, f"{case.case_id}: no relevant_strong annotation"


def test_empty_bundle_cases_are_truly_empty() -> None:
    """The empty-bundle label means: correct answer = no skill selected."""
    for case in HELDOUT_CASES:
        if case.case_id in EMPTY_BUNDLE_CASE_IDS:
            assert case.category == "empty-bundle", case.case_id
            assert case.relevant_strong == [], case.case_id
            assert case.relevant_acceptable == [], case.case_id
            # Confusers must be annotated so a misroute is measurable.
            assert case.relevant_irrelevant, case.case_id


def test_empty_bundle_ids_constant_matches_case_categories() -> None:
    """The constant and the cases stay in sync (no orphaned label either way)."""
    by_category = {c.case_id for c in HELDOUT_CASES if c.category == "empty-bundle"}
    assert by_category == set(EMPTY_BUNDLE_CASE_IDS)


def test_at_least_three_empty_bundle_cases() -> None:
    """Job spec: >= 3 cases whose correct answer is an empty bundle."""
    assert len(EMPTY_BUNDLE_CASE_IDS) >= 3


def test_at_least_six_task_families() -> None:
    """Job spec: the set must span >= 6 task families."""
    families = {case.category for case in HELDOUT_CASES}
    assert len(families) >= 6


def test_styles_mix_user_tasks_and_capability_requests() -> None:
    """Job spec: mix user-style tasks with agent-style capability requests."""
    fixtures = {case.fixture for case in HELDOUT_CASES}
    assert "heldout/user-task" in fixtures
    assert "heldout/capability-request" in fixtures
    requests = [c for c in HELDOUT_CASES if c.fixture == "heldout/capability-request"]
    assert len(requests) >= 3


def test_capability_request_cases_have_the_kernel_shape() -> None:
    """objective line + >= 1 constraint line (normalized_need shape)."""
    for case in HELDOUT_CASES:
        if case.fixture != "heldout/capability-request":
            continue
        lines = case.task_text.split("\n")
        assert len(lines) >= 2, case.case_id
        assert all(line.strip() == line and line for line in lines), case.case_id


def test_task_texts_disjoint_from_dev_and_kernel() -> None:
    """No exact match and no >80% token overlap with either existing set.

    Both Jaccard and containment are checked: containment catches a
    near-copy padded into a longer text that Jaccard would dilute.
    """
    existing = [c.task_text for c in [*DEV_CASES, *KERNEL_QUERY_CASES]]
    for case in HELDOUT_CASES:
        heldout_tokens = _tokens(case.task_text)
        for other_text in existing:
            assert case.task_text != other_text, case.case_id
            other_tokens = _tokens(other_text)
            overlap = max(
                _jaccard(heldout_tokens, other_tokens), _containment(heldout_tokens, other_tokens)
            )
            assert overlap < _OVERLAP_LIMIT, (
                f"{case.case_id} overlaps an existing case at {overlap:.2f}: "
                f"{case.task_text!r} vs {other_text!r}"
            )


def test_task_texts_are_non_trivial() -> None:
    """Each case must carry enough words for the overlap test to be meaningful."""
    for case in HELDOUT_CASES:
        assert len(_tokens(case.task_text)) >= 10, case.case_id
