"""Harness benchmark pack structural tests (§Appendix D)."""

from aci.evaluation.harness_cases import (
    HARNESS_CASE_IDS,
    HARNESS_CASES,
    harness_cases_by_id,
)


class TestHarnessCases:
    def test_pack_has_twenty_cases(self) -> None:
        assert len(HARNESS_CASES) == 20

    def test_ids_are_h001_to_h020_no_gaps(self) -> None:
        expected = {f"H{i:03d}" for i in range(1, 21)}
        assert HARNESS_CASE_IDS == expected

    def test_ids_unique(self) -> None:
        assert len(HARNESS_CASE_IDS) == len(HARNESS_CASES)

    def test_every_case_has_task_text_and_category(self) -> None:
        for case in HARNESS_CASES:
            assert len(case.task_text) >= 20, case.case_id
            assert case.category.startswith("harness/"), case.case_id

    def test_authority_cases_declare_forbidden_actions(self) -> None:
        for case_id in ("H006", "H019", "H020"):
            case = harness_cases_by_id()[case_id]
            assert case.forbidden_actions, f"{case_id} must forbid silent violations"

    def test_non_coder_profiles_pinned_where_the_plan_requires(self) -> None:
        by_id = harness_cases_by_id()
        # §Appendix D: H016 researcher, H017 reviewer, H018 debugger,
        # H019 architect, H020 security — the profile-specific loops.
        assert by_id["H016"].category == "harness/research"
        assert by_id["H017"].category == "harness/review"
        assert by_id["H018"].category == "harness/debugging"
        assert by_id["H019"].category == "harness/authority"
        assert by_id["H020"].category == "harness/authority"

    def test_lookup_by_id(self) -> None:
        assert harness_cases_by_id()["H008"].category == "harness/verification"
