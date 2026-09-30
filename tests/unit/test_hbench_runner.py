"""H-bench runner structural tests — the A/B fairness invariants (§42).

The runner measures HARNESS value (kernel arm K vs naive loop arm N); these
pins keep the comparison honest: same fixtures, same ceiling, same model
action protocol, and a verification command that can actually run.
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_hbench  # noqa: E402

from aci.evaluation.harness_cases import HARNESS_CASE_IDS  # noqa: E402
from aci.runtime.run_controller import _ACTION_PROTOCOL  # noqa: E402
from aci.runtime.workspace import command_within_prefixes  # noqa: E402


class TestHbenchRunner:
    def test_fixture_pack_is_the_verified_eight(self) -> None:
        """The 8 §80-verified fixtures (verify_multi_fixtures.py: each fails
        as shipped, passes after the root-cause fix) — pinned so proof_loop
        drift breaks loudly HERE, not silently inside a report."""
        assert {f["name"] for f in run_hbench._all_fixtures()} == {
            "multi-config-precedence",
            "multi-cache-invalidation",
            "multi-pipeline-ordering",
            "multi-error-translation",
            "multi-event-aliasing",
            "long-order-pipeline",
            "long-auth-session",
            "long-notify-fanout",
        }

    def test_every_fixture_is_labeled_with_a_real_h_case(self) -> None:
        for fixture in run_hbench._all_fixtures():
            ref = run_hbench.H_REFS.get(fixture["name"])
            assert ref in HARNESS_CASE_IDS, fixture["name"]

    def test_pending_h_cases_are_recorded_not_hidden(self) -> None:
        """H005/H006/H007/H011 need dedicated fixtures (context flood,
        approval, injected transient failure, crash/resume) — the report
        must SAY they are pending instead of implying 20/20 coverage."""
        assert "H005" in run_hbench.PENDING_H_CASES
        assert "H011" in run_hbench.PENDING_H_CASES

    def test_verification_command_is_within_the_process_ceiling(self) -> None:
        """If this breaks, both arms refuse to verify (INV-06 fail-closed)
        and every `accepted` in every report is a lie."""
        assert command_within_prefixes(run_hbench.VERIFICATION, run_hbench.PROCESS_PREFIXES)

    def test_naive_arm_uses_the_kernel_action_protocol(self) -> None:
        """§42: same model interface in both arms — the A/B measures harness
        mechanisms (verification gate, recovery, context), not prompts."""
        import inspect

        source = inspect.getsource(run_hbench.run_naive_arm)
        assert "_ACTION_PROTOCOL" in source
        assert _ACTION_PROTOCOL.startswith("Work on the workspace ONLY")
