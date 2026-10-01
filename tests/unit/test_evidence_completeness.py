"""Unit tests for scripts/evidence_completeness.py — the §3.2 instrument
(pure parts, no DB).

Everything here exercises the pure classification/aggregation functions; the
DB layer (fetch_evidence_data) is covered by the optional integration test
against the DEV DB only (tests/integration/test_evidence_completeness.py).
"""

import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import evidence_completeness as ec  # noqa: E402
import usage_report as ur  # noqa: E402

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

#: Sentinel: a run with finished_at=None (an OPEN run) — distinct from the
#: helper's default (created_at + 1 minute).
_OPEN = object()


def _run(
    run_id: str,
    *,
    created_at: datetime | None = None,
    finished_at: object = _OPEN,
    verdict: str | None = "PASS",
    loaded: tuple[ec.LoadedEventFact, ...] = (),
    preloaded: tuple[str, ...] = (),
    exposure_routes: tuple[str, ...] = (),
    exposure_bundles: tuple[str, ...] = (),
    status: str = "succeeded",
) -> ec.AgentRunFact:
    created_at = created_at or datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)
    end = created_at + timedelta(minutes=1) if finished_at is _OPEN else finished_at
    assert isinstance(end, datetime) or end is None
    return ec.AgentRunFact(
        run_id=run_id,
        created_at=created_at,
        finished_at=end,
        status=status,
        stop_reason="SUCCESS" if status == "succeeded" else None,
        verifier_verdict=verdict,
        loaded=loaded,
        preloaded=preloaded,
        exposure_route_run_ids=exposure_routes,
        exposure_bundle_ids=exposure_bundles,
    )


def _loaded(
    capability_id: str = "debugging",
    *,
    route_run_id: str | None = None,
    bundle_id: str | None = None,
    origin: str | None = "model_request",
) -> ec.LoadedEventFact:
    return ec.LoadedEventFact(
        capability_id=capability_id,
        version="0.1.0",
        route_run_id=route_run_id,
        bundle_id=bundle_id,
        origin=origin,
        digest="abc123",
    )


def _kernel_route(
    route_run_id: str,
    *,
    created_at: datetime | None = None,
    bundle_id: str | None = "bun-1",
) -> ec.RouteRunFact:
    return ec.RouteRunFact(
        route_run_id=route_run_id,
        created_at=created_at or datetime(2026, 10, 1, 3, 0, 30, tzinfo=UTC),
        client_type=ec.KERNEL_CLIENT_TYPE,
        bundle_id=bundle_id,
    )


def _bundles(*ids: str) -> dict[str, ec.BundleFact]:
    """bundle_id-keyed rows (event-linkage tests resolve bundle ids directly)."""
    return {bid: ec.BundleFact(bid, f"rr-for-{bid}", 3) for bid in ids}


def _bundle_rows(*pairs: tuple[str, str]) -> dict[str, ec.BundleFact]:
    """(route_run_id, bundle_id) pairs → bundle_id-keyed rows (window-linkage
    tests resolve the bundle through the FK direction)."""
    return {bid: ec.BundleFact(bid, rid, 3) for rid, bid in pairs}


def _by_route(bundles: dict[str, ec.BundleFact]) -> dict[str, ec.BundleFact]:
    """bundle_id-keyed rows → route_run_id-keyed (classify_bundle's input)."""
    return {b.route_run_id: b for b in bundles.values()}


def _outcome(bundle_id: str) -> ec.OutcomeFact:
    return ec.OutcomeFact(
        outcome_id=f"out-{bundle_id}",
        route_run_id=f"rr-for-{bundle_id}",
        bundle_id=bundle_id,
        received_at=NOW,
    )


def _classify(
    run: ec.AgentRunFact,
    *,
    kernel_routes: tuple[ec.RouteRunFact, ...] = (),
    bundles: dict[str, ec.BundleFact] | None = None,
    outcomes: tuple[ec.OutcomeFact, ...] = (),
    now: datetime = NOW,
) -> ec.AgentRunChain:
    bundles = bundles if bundles is not None else _bundles("bun-1")
    return ec.classify_agent_run(
        run,
        kernel_route_runs=kernel_routes,
        bundles=bundles,
        bundles_by_route={b.route_run_id: b for b in bundles.values()},
        outcomes=outcomes,
        now=now,
    )


# -- the durable (event-payload) join -------------------------------------------


class TestRouteLink:
    def test_event_ids_win_over_the_window(self) -> None:
        run = _run("r-evt", loaded=(_loaded(route_run_id="rr-9", bundle_id="bun-9"),))
        kernel = (_kernel_route("rr-window", bundle_id="bun-window"),)
        chain = _classify(run, kernel_routes=kernel, bundles=_bundles("bun-9"))
        assert chain.linked_by == ec.LINKED_BY_EVENT
        assert chain.route_run_ids == ("rr-9",)
        assert chain.bundle_ids == ("bun-9",)

    def test_exposure_payload_ids_link_too(self) -> None:
        run = _run(
            "r-exp",
            loaded=(_loaded(route_run_id="rr-1", bundle_id="bun-1"),),
            exposure_routes=("rr-2",),
            exposure_bundles=("bun-2",),
        )
        chain = _classify(run, bundles=_bundles("bun-1", "bun-2"))
        assert chain.linked_by == ec.LINKED_BY_EVENT
        assert set(chain.route_run_ids) == {"rr-1", "rr-2"}
        assert set(chain.bundle_ids) == {"bun-1", "bun-2"}

    def test_window_fallback_for_pre_task_b_events(self) -> None:
        """Old-format capability.loaded (no ids) links by the run window —
        flagged as the approximation it is."""
        run = _run("r-old", loaded=(_loaded(),))  # no route/bundle ids
        kernel = (_kernel_route("rr-w"),)
        chain = _classify(run, kernel_routes=kernel, bundles=_bundle_rows(("rr-w", "bun-1")))
        assert chain.linked_by == ec.LINKED_BY_WINDOW
        assert chain.route_run_ids == ("rr-w",)
        assert chain.bundle_ids == ("bun-1",)

    def test_window_uses_finished_at_and_extends_to_now_when_open(self) -> None:
        created = datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)
        inside = _kernel_route("rr-in", created_at=created + timedelta(seconds=30))
        # Created AFTER the report moment: outside even an open run's window.
        after = _kernel_route("rr-after", created_at=NOW + timedelta(hours=1))
        run = _run("r-open", created_at=created, finished_at=None, loaded=(_loaded(),))
        bundles = _bundle_rows(("rr-in", "bun-in"), ("rr-after", "bun-after"))
        chain = _classify(run, kernel_routes=(inside, after), bundles=bundles)
        assert chain.route_run_ids == ("rr-in",)
        # An OPEN run (finished_at NULL) extends to the report moment.
        late = _kernel_route("rr-late", created_at=NOW - timedelta(minutes=1))
        bundles = _bundle_rows(("rr-in", "bun-in"), ("rr-late", "bun-late"))
        chain = _classify(run, kernel_routes=(inside, late), bundles=bundles)
        assert "rr-late" in chain.route_run_ids

    def test_window_never_links_a_route_created_before_the_run(self) -> None:
        run = _run("r-x", loaded=(_loaded(),))
        early = _kernel_route("rr-early", created_at=run.created_at - timedelta(minutes=5))
        chain = _classify(run, kernel_routes=(early,))
        assert chain.linked_by is None
        assert chain.route_run_ids == ()

    def test_window_ignores_non_kernel_client_types(self) -> None:
        """Only harness-kernel route_runs are linkage candidates — a foreign
        client_type in the window is never used, even with a bundle row."""
        run = _run("r-x", loaded=(_loaded(),))
        foreign = ec.RouteRunFact(
            route_run_id="rr-rest",
            created_at=run.created_at + timedelta(seconds=1),
            client_type="rest-client",
            bundle_id="bun-foreign",
        )
        chain = _classify(
            run, kernel_routes=(foreign,), bundles=_bundle_rows(("rr-rest", "bun-foreign"))
        )
        assert chain.linked_by is None


# -- agent-run chain categories --------------------------------------------------


class TestAgentRunCategories:
    def test_complete_chain(self) -> None:
        run = _run("r-ok", loaded=(_loaded(route_run_id="rr-1", bundle_id="bun-1"),))
        chain = _classify(run)
        assert chain.category == ec.COMPLETE
        assert chain.activated == 1
        assert chain.has_outcome_event is False  # verdict from the verifier

    def test_naked_run_is_no_capability_plane_not_a_defect(self) -> None:
        run = _run("r-naked", verdict="PASS")  # no loaded, no preloaded
        chain = _classify(run)
        assert chain.category == ec.NO_CAPABILITY_PLANE
        assert chain.linked_by is None

    def test_missing_route_when_events_carry_no_ids_and_no_window_match(self) -> None:
        run = _run("r-noroute", loaded=(_loaded(),))
        chain = _classify(run, kernel_routes=())  # nothing in the window
        assert chain.category == ec.MISSING + ec.LINK_ROUTE

    def test_missing_bundle_when_the_named_bundle_row_is_absent(self) -> None:
        run = _run("r-nobun", loaded=(_loaded(route_run_id="rr-1", bundle_id="bun-gone"),))
        chain = _classify(run, bundles=_bundles("bun-other"))
        assert chain.category == ec.MISSING + ec.LINK_BUNDLE

    def test_missing_activation_when_routed_but_no_activation_events(self) -> None:
        """A run whose route+bundle exist (window) but no capability.loaded /
        preload events — either an empty bundle (ADR-008), the kernel's
        selection policy dropping everything, or lost events: the activation
        link is NOT verifiable from run events (see LIMITATIONS)."""
        run = _run("r-noact", verdict="PASS", loaded=(), preloaded=())
        kernel = (_kernel_route("rr-1", bundle_id="bun-1"),)
        chain = _classify(run, kernel_routes=kernel, bundles=_bundle_rows(("rr-1", "bun-1")))
        assert chain.category == ec.MISSING + ec.LINK_ACTIVATION
        assert chain.route_run_ids == ("rr-1",)

    def test_missing_verdict_for_a_paused_never_resumed_run(self) -> None:
        run = _run(
            "r-paused",
            verdict=None,
            status="interrupted_approval",
            loaded=(_loaded(route_run_id="rr-1", bundle_id="bun-1"),),
        )
        chain = _classify(run)
        assert chain.category == ec.MISSING + ec.LINK_VERDICT

    def test_verdict_satisfied_by_an_outcome_event_on_the_linked_bundle(self) -> None:
        run = _run(
            "r-outcome",
            verdict=None,  # no verifier verdict of its own
            loaded=(_loaded(route_run_id="rr-1", bundle_id="bun-1"),),
        )
        chain = _classify(run, outcomes=(_outcome("bun-1"),))
        assert chain.category == ec.COMPLETE
        assert chain.has_outcome_event is True

    def test_outcome_on_a_foreign_bundle_does_not_satisfy_the_verdict(self) -> None:
        run = _run(
            "r-foreign",
            verdict=None,
            loaded=(_loaded(route_run_id="rr-1", bundle_id="bun-1"),),
        )
        chain = _classify(run, outcomes=(_outcome("bun-other"),))
        assert chain.category == ec.MISSING + ec.LINK_VERDICT

    def test_preload_events_count_as_activation(self) -> None:
        run = _run(
            "r-pre",
            verdict="PASS",
            preloaded=("debugging", "testing"),
            exposure_routes=("rr-1",),
            exposure_bundles=("bun-1",),
        )
        chain = _classify(run)
        assert chain.category == ec.COMPLETE
        assert chain.activated == 2


# -- population 2: routed bundles -------------------------------------------------


class TestBundleClassification:
    def test_complete_with_outcome(self) -> None:
        rr = ec.RouteRunFact("rr-1", NOW, "opencode", "bun-1")
        chain = ec.classify_bundle(rr, _by_route(_bundle_rows(("rr-1", "bun-1"))), {"bun-1"})
        assert chain.category == ec.COMPLETE
        assert chain.bundle_id == "bun-1"

    def test_missing_outcome_without_an_event(self) -> None:
        rr = ec.RouteRunFact("rr-1", NOW, "opencode", "bun-1")
        chain = ec.classify_bundle(rr, _by_route(_bundle_rows(("rr-1", "bun-1"))), set())
        assert chain.category == ec.MISSING + ec.LINK_OUTCOME

    def test_missing_bundle_resolved_through_the_fk_direction(self) -> None:
        """The bundle row (bundles.route_run_id) is authoritative — a NULL
        route_runs.bundle_id pointer does NOT hide an existing bundle."""
        null_pointer = ec.RouteRunFact("rr-1", NOW, "rest-client", None)
        chain = ec.classify_bundle(null_pointer, _by_route(_bundle_rows(("rr-1", "bun-1"))), set())
        assert chain.category == ec.MISSING + ec.LINK_OUTCOME
        assert chain.bundle_id == "bun-1"
        # No bundle row for the route at all → missing:bundle.
        vanished = ec.RouteRunFact("rr-2", NOW, "rest-client", "bun-gone")
        assert (
            ec.classify_bundle(vanished, _by_route(_bundle_rows(("rr-1", "bun-1"))), set()).category
            == ec.MISSING + ec.LINK_BUNDLE
        )


# -- the report -------------------------------------------------------------------


def _data() -> ec.EvidenceData:
    w = datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)
    return ec.EvidenceData(
        agent_runs=(
            _run("r-complete", loaded=(_loaded(route_run_id="rr-1", bundle_id="bun-1"),)),
            # A naked run: created BEFORE any kernel route exists → no window
            # link, no activation events → no_capability_plane.
            _run("r-naked", created_at=w - timedelta(hours=1)),
            _run(
                "r-old",
                created_at=w + timedelta(minutes=2),
                loaded=(_loaded(),),
            ),
        ),
        kernel_route_runs=(
            _kernel_route("rr-1"),
            # Inside r-old's [03:02, 03:03] window — the pre-task-B fallback.
            _kernel_route("rr-old", created_at=w + timedelta(minutes=2, seconds=30)),
        ),
        window_route_runs=(
            ec.RouteRunFact("rr-1", w, "harness-kernel", "bun-1"),
            ec.RouteRunFact("rr-2", w, "opencode", "bun-2"),
            ec.RouteRunFact("rr-3", w, "rest-client", None),
        ),
        bundles=(
            ec.BundleFact("bun-1", "rr-1", 3),
            ec.BundleFact("bun-2", "rr-2", 5),
            # The window-linked run's bundle (resolved via the FK direction).
            ec.BundleFact("bun-old", "rr-old", 2),
        ),
        outcomes=(_outcome("bun-2"),),
    )


class TestBuildReport:
    def test_fractions_and_categories(self) -> None:
        report = ec.build_report(
            _data(),
            generated_at=NOW,
            database="aci_bench",
            since=datetime(2026, 10, 1, tzinfo=UTC),
            until=None,
        )
        ar = report.agent_runs
        # 3 runs: 1 complete (event), 1 naked (no plane), 1 old-format complete
        # via window linkage.
        assert ar["total"] == 3
        assert ar["categories"][ec.COMPLETE] == 2
        assert ar["categories"][ec.NO_CAPABILITY_PLANE] == 1
        assert ar["plane_runs"] == 2
        assert ar["complete_fraction_plane_runs"] == 1.0
        # All-runs fraction counts the naked run in the denominator.
        assert ar["complete_fraction_all_runs"] == round(3 / 3, 4)
        assert ar["route_linked_by"] == {ec.LINKED_BY_EVENT: 1, ec.LINKED_BY_WINDOW: 1}
        assert ar["with_outcome_event"] == 0
        rb = report.routed_bundles
        assert rb["route_runs_in_window"] == 3
        assert rb["bundles_created"] == 2
        assert rb["categories"][ec.COMPLETE] == 1
        assert rb["categories"][ec.MISSING + ec.LINK_OUTCOME] == 1
        assert rb["categories"][ec.MISSING + ec.LINK_BUNDLE] == 1
        assert rb["outcome_coverage_fraction"] == 0.5
        assert rb["complete_by_client_type"] == {"opencode": 1}
        assert report.window == {
            "since": "2026-10-01T00:00:00+00:00",
            "until": None,
        }

    def test_per_run_rows_carry_the_audit_detail(self) -> None:
        report = ec.build_report(
            _data(), generated_at=NOW, database="aci_bench", since=None, until=None
        )
        by_id = {row["run_id"]: row for row in report.per_run}
        assert by_id["r-complete"]["linked_by"] == ec.LINKED_BY_EVENT
        assert by_id["r-complete"]["route_run_ids"] == ["rr-1"]
        assert by_id["r-old"]["linked_by"] == ec.LINKED_BY_WINDOW
        assert by_id["r-naked"]["category"] == ec.NO_CAPABILITY_PLANE

    def test_report_is_json_serializable(self) -> None:
        report = ec.build_report(
            _data(), generated_at=NOW, database="aci_bench", since=None, until=None
        )
        assert json.loads(json.dumps(asdict(report))) == asdict(report)

    def test_empty_data_is_all_zero_not_a_crash(self) -> None:
        empty = ec.EvidenceData((), (), (), (), ())
        report = ec.build_report(empty, generated_at=NOW, database="aci", since=None, until=None)
        assert report.agent_runs["total"] == 0
        assert report.agent_runs["complete_fraction_plane_runs"] == 0.0
        assert report.routed_bundles["bundles_created"] == 0
        assert report.routed_bundles["outcome_coverage_fraction"] == 0.0

    def test_limitations_are_stated(self) -> None:
        report = ec.build_report(
            _data(), generated_at=NOW, database="aci_bench", since=None, until=None
        )
        assert report.limitations
        assert any("window" in line.lower() for line in report.limitations)


class TestRenderText:
    def test_renders_both_populations_and_limitations(self) -> None:
        report = ec.build_report(
            _data(), generated_at=NOW, database="aci_bench", since=None, until=None
        )
        text = ec.render_text(report)
        assert "database: aci_bench" in text
        assert "route → bundle → activation → verdict" in text
        assert "per-run detail" in text
        assert "r-complete" in text
        assert "route → bundle → §33 outcome" in text
        assert "limitations (read before quoting any number)" in text
        assert "read-only connection" in text


# -- read-only enforcement is usage_report's fail-closed pair, reused ------------


class TestReadOnlyReuse:
    def test_reuses_usage_report_helpers_not_copies(self) -> None:
        assert ec.open_read_only_engine is ur.open_read_only_engine
        assert ec.assert_read_only is ur.assert_read_only

    def test_default_database_is_the_operational_db(self) -> None:
        assert ec.DEFAULT_DATABASE_URL.endswith("/aci_bench")

    def test_parse_moment(self) -> None:
        assert ec._parse_moment(None) is None
        assert ec._parse_moment("2026-10-01") == datetime(2026, 10, 1, tzinfo=UTC)
        naive = ec._parse_moment("2026-10-01T03:04:05")
        assert naive == datetime(2026, 10, 1, 3, 4, 5, tzinfo=UTC)
        plus7 = ec._parse_moment("2026-10-01T03:04:05+07:00")
        assert plus7 == datetime(2026, 9, 30, 20, 4, 5, tzinfo=UTC)


class TestLoadedEventFactParsing:
    def test_payload_fields_map_to_the_fact(self) -> None:
        fact = ec._loaded_fact(
            {
                "capability_id": "debugging",
                "version": "0.1.0",
                "route_run_id": "rr-1",
                "bundle_id": "bun-1",
                "origin": "preload",
                "digest": "deadbeef",
                "context_tokens": 100,
            }
        )
        assert fact == ec.LoadedEventFact(
            "debugging", "0.1.0", "rr-1", "bun-1", "preload", "deadbeef"
        )

    def test_pre_task_b_payload_maps_with_none_ids(self) -> None:
        fact = ec._loaded_fact({"capability_id": "debugging", "version": "0.1.0"})
        assert fact.route_run_id is None
        assert fact.bundle_id is None
        assert fact.origin is None


@pytest.mark.parametrize(
    "category",
    [ec.COMPLETE, ec.NO_CAPABILITY_PLANE, ec.MISSING + ec.LINK_ROUTE],
)
def test_categories_are_strings_not_enums(category: str) -> None:
    assert isinstance(category, str)
