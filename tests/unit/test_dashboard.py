"""Unit tests for scripts/dashboard.py — the benchmark section's
"latest run per label" recall (pure parts, no DB).

Pins the era-mixing fix: the old SQL was
``DISTINCT ON (r.label, res.case_id) ... ORDER BY r.label, res.case_id,
r.created_at DESC`` — the latest RESULT PER CASE, not the latest RUN per
label. A later run that covered fewer cases (partial/aborted) was averaged
together with an older run's results for the missing cases, while the
printed header said "latest run per label".
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dashboard as dash  # noqa: E402


def _row(label, run_id, created_at, case_id, recall):
    return (label, run_id, created_at, case_id, recall)


T0 = datetime(2026, 9, 28, tzinfo=UTC)
T1 = T0 + timedelta(days=1)
T2 = T0 + timedelta(days=2)


class TestLatestRunMeanRecall:
    def test_a_partial_later_run_does_not_mix_eras(self) -> None:
        """The header says "latest run per label": a later run covering
        fewer cases must not be averaged with an older run's results for the
        cases it did not cover."""
        rows = [
            _row("dev-31", "run-old", T1, "dev-a", 0.5),
            _row("dev-31", "run-old", T1, "dev-b", 0.5),
            _row("dev-31", "run-new", T2, "dev-a", 0.0),  # partial run: only case A
        ]
        got = dash.latest_run_mean_recall(rows)
        # The LATEST run's own mean (0.0), never mean(0.0, 0.5) = 0.25.
        assert got["dev-31"] == pytest.approx(0.0)

    def test_full_latest_run_wins_whole(self) -> None:
        rows = [
            _row("smoke-10", "run-old", T1, "s-1", 0.1),
            _row("smoke-10", "run-old", T1, "s-2", 0.3),
            _row("smoke-10", "run-new", T2, "s-1", 0.2),
            _row("smoke-10", "run-new", T2, "s-2", 0.4),
        ]
        assert dash.latest_run_mean_recall(rows)["smoke-10"] == pytest.approx(0.3)

    def test_labels_are_independent_and_sorted(self) -> None:
        rows = [
            _row("zeta", "z1", T1, "c1", 1.0),
            _row("alpha", "a1", T2, "c1", 0.0),
        ]
        got = dash.latest_run_mean_recall(rows)
        assert list(got) == ["alpha", "zeta"]
        assert got["alpha"] == pytest.approx(0.0)
        assert got["zeta"] == pytest.approx(1.0)

    def test_null_recalls_are_excluded_never_counted_as_zero(self) -> None:
        """Same semantics as SQL avg(): a NULL recall (case annotated with
        nothing relevant) is not a zero."""
        rows = [
            _row("dev-31", "run-1", T1, "dev-a", 0.4),
            _row("dev-31", "run-1", T1, "dev-b", None),
        ]
        assert dash.latest_run_mean_recall(rows)["dev-31"] == pytest.approx(0.4)

    def test_latest_run_with_no_measured_results_is_omitted(self) -> None:
        """A label whose LATEST run produced no full_pipeline recalls at all
        has no recall to report — it is omitted from the recall line (it is
        still listed in the runs-by-label list above), never averaged from
        an older run's results."""
        rows = [
            _row("dev-31", "run-old", T1, "dev-a", 0.5),
            _row("dev-31", "run-new", T2, "dev-a", None),
        ]
        assert "dev-31" not in dash.latest_run_mean_recall(rows)

    def test_created_at_ties_break_deterministically_on_run_id(self) -> None:
        rows = [
            _row("dev-31", "run-a", T1, "dev-a", 0.1),
            _row("dev-31", "run-b", T1, "dev-a", 0.9),
        ]
        assert dash.latest_run_mean_recall(rows)["dev-31"] == pytest.approx(0.9)
