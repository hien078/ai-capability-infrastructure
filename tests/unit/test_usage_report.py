"""Unit tests for scripts/usage_report.py — the E3 instrument (pure parts, no DB).

Everything here exercises the pure classification/aggregation functions;
the DB layer (fetch_report_data) is covered by the optional integration
test against the DEV DB only (tests/integration/test_usage_report.py).
"""

import json
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import usage_report as ur  # noqa: E402


def _fact(
    route_run_id: str,
    moment: datetime,
    client_type: str,
    principal_id: str,
    *,
    linked: bool = False,
) -> ur.RouteRunFact:
    return ur.RouteRunFact(
        route_run_id=route_run_id,
        created_at=moment,
        client_type=client_type,
        principal_id=principal_id,
        agent_run_linked=linked,
    )


def _report_data() -> ur.ReportData:
    """One small week of facts: an organic route, a linked kernel route, a
    bundle with one outcome, one succeeded agent run."""
    w40 = datetime(2026, 9, 30, tzinfo=UTC)
    return ur.ReportData(
        route_runs=(
            _fact("r1", w40, "opencode", "opencode"),
            _fact("r2", w40, "harness-kernel", "harness-kernel", linked=True),
        ),
        bundles=(ur.BundleFact("b1", w40),),
        outcomes=(ur.OutcomeFact("o1", "b1", w40, (("test_harness", "success"),)),),
        agent_runs=(ur.AgentRunFact("a1", w40, "succeeded"),),
    )


class TestIsoWeek:
    def test_monday_starts_the_week(self) -> None:
        assert ur.iso_week(datetime(2026, 9, 28)) == "2026-W40"  # Monday
        assert ur.iso_week(datetime(2026, 10, 4)) == "2026-W40"  # Sunday
        assert ur.iso_week(datetime(2026, 10, 5)) == "2026-W41"  # next Monday

    def test_iso_year_boundary_belongs_to_previous_iso_year(self) -> None:
        # 2027-01-01 is a Friday → ISO year 2026, week 53.
        assert ur.iso_week(datetime(2027, 1, 1)) == "2026-W53"
        assert ur.iso_week(datetime(2026, 12, 31)) == "2026-W53"

    def test_tzaware_converts_to_utc_first(self) -> None:
        # 2026-10-05 01:30 +07:00 is 2026-10-04 18:30 UTC (Sunday) → W40,
        # NOT the wall-clock week of the +07:00 date (which would be W41).
        plus7 = timezone(timedelta(hours=7))
        assert ur.iso_week(datetime(2026, 10, 5, 1, 30, tzinfo=plus7)) == "2026-W40"

    def test_naive_is_taken_as_utc(self) -> None:
        assert ur.iso_week(datetime(2026, 9, 28, 0, 0)) == "2026-W40"


class TestClassifyRouteRun:
    @pytest.mark.parametrize("client_type", sorted(ur.ORGANIC_CLIENT_TYPES))
    def test_real_clients_are_organic(self, client_type: str) -> None:
        assert ur.classify_route_run(client_type) == ur.ORGANIC

    @pytest.mark.parametrize("client_type", sorted(ur.MEASUREMENT_CLIENT_TYPES))
    def test_measurement_drivers_are_measurement(self, client_type: str) -> None:
        assert ur.classify_route_run(client_type) == ur.MEASUREMENT

    def test_harness_kernel_splits_on_agent_run_linkage(self) -> None:
        assert ur.classify_route_run("harness-kernel") == ur.MEASUREMENT  # run_hbench arm S/P
        assert ur.classify_route_run("harness-kernel", agent_run_linked=True) == ur.ORGANIC

    def test_unknown_client_type_is_never_silently_bucketed(self) -> None:
        assert ur.classify_route_run("some-future-client") == ur.UNKNOWN


class TestInAnyWindow:
    W = (datetime(2026, 10, 1, 3, 51, 29, tzinfo=UTC), datetime(2026, 10, 1, 3, 52, 42, tzinfo=UTC))

    def test_inside_and_edges(self) -> None:
        start, end = self.W
        assert ur.in_any_window(start, [self.W])  # inclusive start
        assert ur.in_any_window(end, [self.W])  # inclusive end
        assert ur.in_any_window(datetime(2026, 10, 1, 3, 52, 0, tzinfo=UTC), [self.W])

    def test_outside(self) -> None:
        before = datetime(2026, 10, 1, 3, 51, 28, tzinfo=UTC)
        after = datetime(2026, 10, 1, 3, 52, 43, tzinfo=UTC)
        assert not ur.in_any_window(before, [self.W])
        assert not ur.in_any_window(after, [self.W])

    def test_any_of_several_windows_and_none(self) -> None:
        other = (datetime(2026, 10, 2, tzinfo=UTC), datetime(2026, 10, 3, tzinfo=UTC))
        assert ur.in_any_window(datetime(2026, 10, 2, 12, tzinfo=UTC), [self.W, other])
        assert not ur.in_any_window(datetime(2026, 10, 2, 12, tzinfo=UTC), [])


class TestRouteRunAggregation:
    def test_week_split_and_class_counts(self) -> None:
        w40 = datetime(2026, 9, 30, tzinfo=UTC)
        w41 = datetime(2026, 10, 6, tzinfo=UTC)
        facts = [
            _fact("r1", w40, "opencode", "opencode"),
            _fact("r2", w40, "opencode", "opencode"),
            _fact("r3", w40, "benchmark-harness", "benchmark"),
            _fact("r4", w40, "harness-kernel", "harness-kernel"),
            _fact("r5", w40, "harness-kernel", "harness-kernel", linked=True),
            _fact("r6", w41, "mcp-client", "anonymous"),
            _fact("r7", w41, "mystery-client", "who"),
        ]
        weeks = ur.aggregate_route_runs(facts)
        assert set(weeks) == {"2026-W40", "2026-W41"}

        w40_bucket = weeks["2026-W40"]
        assert w40_bucket["total"] == 5
        assert w40_bucket[ur.ORGANIC]["total"] == 3  # opencode×2 + linked harness-kernel
        assert w40_bucket[ur.ORGANIC]["by_client_type"] == {"opencode": 2, "harness-kernel": 1}
        assert w40_bucket[ur.MEASUREMENT]["total"] == 2  # benchmark + unlinked harness-kernel
        assert w40_bucket[ur.MEASUREMENT]["by_client_type"] == {
            "benchmark-harness": 1,
            "harness-kernel": 1,
        }
        assert w40_bucket[ur.UNKNOWN]["total"] == 0

        w41_bucket = weeks["2026-W41"]
        assert w41_bucket["total"] == 2
        assert w41_bucket[ur.ORGANIC]["total"] == 1
        assert w41_bucket[ur.UNKNOWN]["total"] == 1
        assert w41_bucket[ur.UNKNOWN]["by_principal"] == {"who": 1}

    def test_counts_sort_count_desc_then_key(self) -> None:
        facts = [
            _fact("r1", datetime(2026, 9, 29, tzinfo=UTC), "rest-client", "zeta"),
            _fact("r2", datetime(2026, 9, 29, tzinfo=UTC), "rest-client", "alpha"),
            _fact("r3", datetime(2026, 9, 29, tzinfo=UTC), "opencode", "beta"),
        ]
        bucket = ur.summarize_route_runs(facts)
        assert list(bucket[ur.ORGANIC]["by_client_type"]) == ["rest-client", "opencode"]
        assert list(bucket[ur.ORGANIC]["by_principal"]) == ["alpha", "beta", "zeta"]

    def test_empty_summary_is_all_zero(self) -> None:
        bucket = ur.summarize_route_runs([])
        assert bucket["total"] == 0
        assert bucket[ur.ORGANIC] == {
            "total": 0,
            "by_client_type": {},
            "by_principal": {},
        }


class TestOutcomeAggregation:
    def test_coverage_and_verdicts(self) -> None:
        moment = datetime(2026, 9, 30, tzinfo=UTC)
        bundles = [ur.BundleFact(f"b{i}", moment) for i in range(4)]
        outcomes = [
            ur.OutcomeFact("o1", "b0", moment, (("test_harness", "success"),)),
            # two events on the SAME bundle → distinct-bundle count stays 1
            ur.OutcomeFact("o2", "b0", moment, (("agent_self_report", "unknown"),)),
            ur.OutcomeFact(
                "o3", "b1", moment, (("test_harness", "success"), ("human_review", "success"))
            ),
        ]
        bucket = ur.summarize_outcomes(bundles, outcomes)
        assert bucket["bundles_created"] == 4
        assert bucket["bundles_with_outcomes"] == 2
        assert bucket["outcome_events"] == 3
        assert bucket["verdicts"] == {
            "test_harness/success": 2,
            "agent_self_report/unknown": 1,
            "human_review/success": 1,
        }

    def test_week_split_by_received_at(self) -> None:
        w40 = datetime(2026, 9, 30, tzinfo=UTC)
        w41 = datetime(2026, 10, 7, tzinfo=UTC)
        bundles = [ur.BundleFact("b40", w40), ur.BundleFact("b41", w41)]
        outcomes = [ur.OutcomeFact("o40", "b40", w40, ()), ur.OutcomeFact("o41", "b41", w41, ())]
        weeks = ur.aggregate_outcomes(bundles, outcomes)
        assert set(weeks) == {"2026-W40", "2026-W41"}
        assert weeks["2026-W40"]["bundles_created"] == 1
        assert weeks["2026-W40"]["outcome_events"] == 1
        assert weeks["2026-W41"]["bundles_created"] == 1

    def test_empty(self) -> None:
        assert ur.summarize_outcomes((), ()) == {
            "bundles_created": 0,
            "bundles_with_outcomes": 0,
            "outcome_events": 0,
            "verdicts": {},
        }


class TestAgentRunAggregation:
    def test_by_status(self) -> None:
        w40 = datetime(2026, 9, 30, tzinfo=UTC)
        facts = [
            ur.AgentRunFact("a1", w40, "succeeded"),
            ur.AgentRunFact("a2", w40, "succeeded"),
            ur.AgentRunFact("a3", w40, "failed"),
        ]
        weeks = ur.aggregate_agent_runs(facts)
        assert weeks["2026-W40"] == {"total": 3, "by_status": {"succeeded": 2, "failed": 1}}
        assert ur.summarize_agent_runs([]) == {"total": 0, "by_status": {}}


class TestBuildReport:
    def test_week_union_covers_every_surface(self) -> None:
        # A week with ONLY an agent_run still appears (empty route/outcome rows).
        data = ur.ReportData(
            route_runs=(),
            bundles=(),
            outcomes=(),
            agent_runs=(ur.AgentRunFact("a1", datetime(2026, 10, 7, tzinfo=UTC), "failed"),),
        )
        report = ur.build_report(
            data, generated_at=datetime(2026, 10, 8, tzinfo=UTC), database="aci_bench"
        )
        assert [w["week"] for w in report["weeks"]] == ["2026-W41"]
        assert report["weeks"][0]["route_runs"]["total"] == 0
        assert report["weeks"][0]["agent_runs"]["by_status"] == {"failed": 1}

    def test_totals_and_metadata(self) -> None:
        report = ur.build_report(
            _report_data(), generated_at=datetime(2026, 10, 8, tzinfo=UTC), database="aci_bench"
        )
        assert report["database"] == "aci_bench"
        assert report["read_only"] is True
        assert report["generated_at"].startswith("2026-10-08T")
        assert report["totals"]["route_runs"]["total"] == 2
        assert report["totals"]["route_runs"][ur.ORGANIC]["total"] == 2
        assert report["totals"]["outcomes"]["bundles_with_outcomes"] == 1
        assert report["totals"]["agent_runs"]["by_status"] == {"succeeded": 1}
        assert report["limitations"] == list(ur.LIMITATIONS)
        assert report["limitations"], "the instrument must state its own limits"

    def test_report_is_json_serializable(self) -> None:
        report = ur.build_report(
            _report_data(), generated_at=datetime(2026, 10, 8, tzinfo=UTC), database="aci_bench"
        )
        assert json.loads(json.dumps(report)) == report


class TestRenderText:
    def test_renders_sections_and_limitations(self) -> None:
        report = ur.build_report(
            _report_data(),
            generated_at=datetime(2026, 10, 8, tzinfo=UTC),
            database="aci_bench",
        )
        text = ur.render_text(report)
        assert "database: aci_bench" in text
        assert "2026-W40" in text
        assert "ORGANIC (real clients) vs MEASUREMENT" in text
        assert "outcomes per bundle" in text
        assert "agent_runs per ISO week, by status" in text
        assert "limitations (read before quoting any number)" in text
        assert "read-only connection" in text
        assert "test_harness/success 1" in text


class TestReadOnlyEnforcement:
    def test_connect_args_carry_the_read_only_option(self) -> None:
        assert "default_transaction_read_only=on" in ur.READ_ONLY_OPTION

    def test_assert_read_only_fails_closed_on_off(self) -> None:
        class _Scalar:
            def __init__(self, setting: str) -> None:
                self._setting = setting

            def scalar(self) -> str:
                return self._setting

        class _Conn:
            def execute(self, _sql: object) -> _Scalar:
                return _Scalar("off")

        with pytest.raises(RuntimeError, match="never writes"):
            ur.assert_read_only(_Conn())  # type: ignore[arg-type]
