"""Evidence-chain completeness (docs/plans/aci-improvement-2026-10.md §3.2).

READ-ONLY instrument over the OPERATIONAL DB (default aci_bench — never the
dev/test `aci` DB; pass --database-url to point it anywhere). Measures, for
a time window, the fraction of runs whose capability EVIDENCE CHAIN is
COMPLETE — an OBSERVATIONAL join, never a causal claim (ADR-014 amendments
14–17: loaded-in-context is not "caused the outcome"):

  route → bundle → activation → verdict

Two populations, two questions:

  1. AGENT RUNS (HarnessKernel, migration 0016): per run, which links are
     recorded and where. Links and where they live today:
       - route:     route_runs row (client_type 'harness-kernel'), written
                    by the SAME RouteCapabilitiesService /v1/routes uses.
       - bundle:    bundles + bundle_items rows (FK to the route run).
       - activation: agent_run_events rows — capability.loaded (payload:
                    capability_id, version, ENTRY digest, activation_id,
                    context_tokens, origin, and route_run_id/bundle_id when
                    the selection carried them), capability.preload (loaded
                    ids + count), capability.exposure at a TRUE terminal
                    result (stop_reason + verifier_verdict + evidence_refs
                    joined with the activated set + provenance).
       - verdict:   agent_runs.evidence->verification_verdict (the kernel's
                    own verifier), verification.completed events, and —
                    optionally — a §33 outcome_events row on the linked bundle
                    (NONE is written for agent runs today: the kernel's
                    verdict reaches the §33 stream only through the
                    CapabilityFeedback seam, which REST does not wire).
     A run that never touched the capability plane (no route, no
     activation) is reported as its own category — a naked run is a valid
     run, not an evidence defect.

  2. ROUTED BUNDLES (every /v1/routes + OpenCode/MCP client flow): does the
     bundle carry a §33 outcome event (route → bundle → outcome)? Split by
     client_type. The ACTIVATION link is NOT observable server-side for
     client flows (OpenCode lazy-loads catalog files; MCP reads skill://
     resources — neither is telemetry), so population 2 measures the §33
     feedback-loop coverage only.

Route linkage for agent runs: DURABLE first (capability.loaded/exposure
payload route_run_id — the format since task B, commit 5a72206), else the
TIME-WINDOW approximation over harness-kernel route_runs (a route_run whose
created_at falls inside the run's [created_at, finished_at] window) — the
same honest fallback scripts/usage_report.py uses, and the ONLY option for
runs recorded before task B (their capability.loaded payloads carry just
capability_id/version). Window linkage can mislink when a run_hbench arm
S/P route lands inside a real run's window; every window-linked run is
flagged in the per-run detail.

Never writes: the engine connects with default_transaction_read_only=on and
asserts the setting before reading (fail closed) — the same helpers as
scripts/usage_report.py, reused by import.

Usage:
    .venv/bin/python scripts/evidence_completeness.py [--json] \\
        [--since 2026-10-01] [--until 2026-10-03] [--database-url URL]

    ACI_DATABASE_URL=... .venv/bin/python scripts/evidence_completeness.py --json
"""

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import make_url, text
from sqlalchemy.engine import Connection

# Read-only enforcement is usage_report's fail-closed helper pair — reused,
# not duplicated (same scripts/ dir; tests put it on sys.path the same way).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usage_report import (  # noqa: E402
    assert_read_only,
    open_read_only_engine,
)

DEFAULT_DATABASE_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci_bench"

#: The kernel's capability client routes under this client_type
#: (adapters/outbound/agent_capabilities.py) — the only route_runs a real
#: agent run can link to.
KERNEL_CLIENT_TYPE = "harness-kernel"

# -- chain vocabulary -----------------------------------------------------------

LINK_ROUTE = "route"
LINK_BUNDLE = "bundle"
LINK_ACTIVATION = "activation"
LINK_VERDICT = "verdict"
CHAIN_LINKS: tuple[str, ...] = (LINK_ROUTE, LINK_BUNDLE, LINK_ACTIVATION, LINK_VERDICT)

COMPLETE = "complete"
#: A run that never searched/loaded capabilities: no route, no activation —
#: the chain is not applicable (a naked run, not an evidence defect).
NO_CAPABILITY_PLANE = "no_capability_plane"
#: category prefix for a run that touched the plane but lacks a link.
MISSING = "missing:"
#: The §33 feedback link of population 2 (a bundle without an outcome event).
LINK_OUTCOME = "outcome"

#: How an agent run's route link was established.
LINKED_BY_EVENT = "event"  # durable: capability.loaded/exposure payload ids
LINKED_BY_WINDOW = "window"  # approximation: route_run created inside the run window

#: Honest bounds of this instrument — printed with every report.
LIMITATIONS: tuple[str, ...] = (
    "Window linkage is an APPROXIMATION: a harness-kernel route_run whose created_at "
    "falls inside a run's [created_at, finished_at] window is assumed to be the run's "
    "route. A run_hbench arm S/P route concurrent with a real run mislinks; a real run "
    "whose agent_runs row failed to persist (persistence is telemetry, §50) is invisible "
    "here entirely. Runs recorded before task B (commit 5a72206) have capability.loaded "
    "payloads without route_run_id/bundle_id and can ONLY be window-linked.",
    "The activation link is observable for AGENT RUNS only. Client flows (OpenCode "
    "catalog lazy-loads, MCP skill:// resource reads) leave no server-side activation "
    "telemetry, so population 2 measures route → bundle → outcome coverage only.",
    "'missing:activation' cannot distinguish a run that routed but activated nothing "
    "(empty bundle ADR-008, or the kernel selection policy dropped every item) from "
    "lost activation events: the kernel emits no capability.search.* events, so a "
    "search that kept nothing leaves no run-event trace beyond the route_run row.",
    "No §33 outcome_events row is written for agent runs today (0 on harness-kernel "
    "bundles): the kernel's verdict satisfies the verdict link through "
    "agent_runs.evidence / capability.exposure, and the CapabilityFeedback seam that "
    "would bridge it into outcome_events is called but unwired on REST.",
    "The verdict link counts the kernel's OWN verifier (agent_runs.evidence."
    "verification_verdict) — a run whose verifier never ran (paused and never "
    "resumed) is 'missing:verdict' by design, not a defect.",
    "Reads the full route_runs/bundles/outcome_events tables (fine at this DB's "
    "scale — hundreds of rows; not an instrument for a huge DB).",
)


# ---------------------------------------------------------------------------
# Facts — plain rows read from the telemetry surfaces (§41 + migration 0016).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedEventFact:
    """One capability.loaded event payload (fields absent pre-task-B are None)."""

    capability_id: str
    version: str
    route_run_id: str | None = None
    bundle_id: str | None = None
    origin: str | None = None
    digest: str | None = None


@dataclass(frozen=True)
class AgentRunFact:
    run_id: str
    created_at: datetime
    finished_at: datetime | None
    status: str
    stop_reason: str | None
    #: agent_runs.evidence->>'verification_verdict' (None = no verifier verdict).
    verifier_verdict: str | None
    loaded: tuple[LoadedEventFact, ...] = ()
    #: capability ids from capability.preload events (count>0 entries only).
    preloaded: tuple[str, ...] = ()
    #: route_run_ids / bundle_ids named in capability.exposure payloads.
    exposure_route_run_ids: tuple[str, ...] = ()
    exposure_bundle_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteRunFact:
    route_run_id: str
    created_at: datetime
    client_type: str
    bundle_id: str | None


@dataclass(frozen=True)
class BundleFact:
    bundle_id: str
    route_run_id: str
    item_count: int


@dataclass(frozen=True)
class OutcomeFact:
    outcome_id: str
    route_run_id: str
    bundle_id: str
    received_at: datetime


@dataclass(frozen=True)
class EvidenceData:
    agent_runs: tuple[AgentRunFact, ...]
    #: ALL harness-kernel route_runs (window linkage ignores the report window).
    kernel_route_runs: tuple[RouteRunFact, ...]
    #: route_runs created inside the report window (population 2).
    window_route_runs: tuple[RouteRunFact, ...]
    bundles: tuple[BundleFact, ...]
    outcomes: tuple[OutcomeFact, ...]


# ---------------------------------------------------------------------------
# Pure classification + aggregation (unit-tested without any DB).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentRunChain:
    """One agent run's classified chain (observational, never causal)."""

    run_id: str
    category: str
    linked_by: str | None
    route_run_ids: tuple[str, ...] = ()
    bundle_ids: tuple[str, ...] = ()
    activated: int = 0
    has_outcome_event: bool = False


def _route_link(
    run: AgentRunFact,
    kernel_route_runs: Sequence[RouteRunFact],
    bundles_by_route: dict[str, BundleFact],
    *,
    now: datetime,
) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """(route_run_ids, bundle_ids, linked_by) for one run — durable event ids
    first, else the window approximation. Never fabricates an id."""
    event_routes = {fact.route_run_id for fact in run.loaded if fact.route_run_id} | set(
        run.exposure_route_run_ids
    )
    event_bundles = {fact.bundle_id for fact in run.loaded if fact.bundle_id} | set(
        run.exposure_bundle_ids
    )
    if event_routes or event_bundles:
        return (
            tuple(sorted(event_routes)),
            tuple(sorted(event_bundles)),
            LINKED_BY_EVENT,
        )
    end = run.finished_at if run.finished_at is not None else now
    window = [
        rr.route_run_id
        for rr in kernel_route_runs
        if rr.client_type == KERNEL_CLIENT_TYPE and run.created_at <= rr.created_at <= end
    ]
    if not window:
        return (), (), None
    # The bundle link follows the FK direction (bundles.route_run_id is
    # authoritative; route_runs.bundle_id is a plain pointer that old rows
    # may not carry).
    bundle_ids = {bundles_by_route[rid].bundle_id for rid in window if rid in bundles_by_route}
    return (
        tuple(sorted(window)),
        tuple(sorted(bundle_ids)),
        LINKED_BY_WINDOW,
    )


def classify_agent_run(
    run: AgentRunFact,
    *,
    kernel_route_runs: Sequence[RouteRunFact],
    bundles: dict[str, BundleFact],
    bundles_by_route: dict[str, BundleFact],
    outcomes: Sequence[OutcomeFact],
    now: datetime,
) -> AgentRunChain:
    """The run's chain category + links.

    Categories: ``complete`` | ``no_capability_plane`` | ``missing:<link>``
    where <link> is the FIRST missing link in chain order (route, bundle,
    activation, verdict). A run with no route AND no activation never touched
    the capability plane — its own category, not a broken link."""
    route_ids, bundle_ids, linked_by = _route_link(
        run, kernel_route_runs, bundles_by_route, now=now
    )
    activated = len(run.loaded) + len(run.preloaded)
    if not route_ids and not bundle_ids and activated == 0:
        return AgentRunChain(run.run_id, NO_CAPABILITY_PLANE, None)
    bundle_ids = tuple(sorted(set(bundle_ids) & bundles.keys()))
    outcome_bundle_ids = {o.bundle_id for o in outcomes}
    has_outcome = any(bid in outcome_bundle_ids for bid in bundle_ids)
    verdict = run.verifier_verdict is not None or has_outcome
    category = COMPLETE
    for link, present in (
        (LINK_ROUTE, bool(route_ids)),
        (LINK_BUNDLE, bool(bundle_ids)),
        (LINK_ACTIVATION, activated > 0),
        (LINK_VERDICT, verdict),
    ):
        if not present:
            category = MISSING + link
            break
    return AgentRunChain(
        run_id=run.run_id,
        category=category,
        linked_by=linked_by,
        route_run_ids=route_ids,
        bundle_ids=bundle_ids,
        activated=activated,
        has_outcome_event=has_outcome,
    )


def classify_agent_runs(data: EvidenceData, *, now: datetime) -> tuple[AgentRunChain, ...]:
    bundles = {b.bundle_id: b for b in data.bundles}
    bundles_by_route = {b.route_run_id: b for b in data.bundles}
    return tuple(
        classify_agent_run(
            run,
            kernel_route_runs=data.kernel_route_runs,
            bundles=bundles,
            bundles_by_route=bundles_by_route,
            outcomes=data.outcomes,
            now=now,
        )
        for run in data.agent_runs
    )


@dataclass(frozen=True)
class BundleChain:
    """One routed bundle's §33 chain (population 2)."""

    route_run_id: str
    bundle_id: str | None
    client_type: str
    category: str


def classify_bundle(
    route_run: RouteRunFact,
    bundles_by_route: dict[str, BundleFact],
    outcome_bundle_ids: set[str],
) -> BundleChain:
    """``complete`` (bundle + ≥1 outcome event) | ``missing:outcome`` (bundle,
    none) | ``missing:bundle`` (no bundle row — an error/abstained route).

    The bundle is resolved through the FK direction (bundles.route_run_id →
    route_runs): route_runs.bundle_id is a plain pointer that historical rows
    may not carry."""
    bundle = bundles_by_route.get(route_run.route_run_id)
    if bundle is None:
        return BundleChain(
            route_run.route_run_id, None, route_run.client_type, MISSING + LINK_BUNDLE
        )
    category = COMPLETE if bundle.bundle_id in outcome_bundle_ids else MISSING + LINK_OUTCOME
    return BundleChain(route_run.route_run_id, bundle.bundle_id, route_run.client_type, category)


def classify_bundles(data: EvidenceData) -> tuple[BundleChain, ...]:
    bundles_by_route = {b.route_run_id: b for b in data.bundles}
    outcome_bundle_ids = {o.bundle_id for o in data.outcomes}
    return tuple(
        classify_bundle(rr, bundles_by_route, outcome_bundle_ids) for rr in data.window_route_runs
    )


def _fraction(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _sorted(counter: Counter[str]) -> dict[str, int]:
    """Deterministic count dict: count desc, then key asc (stable output)."""
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


@dataclass(frozen=True)
class CompletenessReport:
    """The full report (JSON-serializable): both populations + limitations."""

    generated_at: str
    database: str
    window: dict[str, str | None]
    read_only: bool = True
    agent_runs: dict[str, Any] = field(default_factory=dict)
    routed_bundles: dict[str, Any] = field(default_factory=dict)
    per_run: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=lambda: list(LIMITATIONS))


def build_report(
    data: EvidenceData,
    *,
    generated_at: datetime,
    database: str,
    since: datetime | None,
    until: datetime | None,
) -> CompletenessReport:
    chains = classify_agent_runs(data, now=generated_at)
    categories = Counter(chain.category for chain in chains)
    linked_by = Counter(chain.linked_by for chain in chains if chain.linked_by)
    plane_runs = [c for c in chains if c.category != NO_CAPABILITY_PLANE]
    complete = sum(1 for c in plane_runs if c.category == COMPLETE)

    bundle_chains = classify_bundles(data)
    bundle_categories = Counter(chain.category for chain in bundle_chains)
    by_client: Counter[str] = Counter()
    for chain in bundle_chains:
        if chain.category == COMPLETE:
            by_client[chain.client_type] += 1
    bundles_created = sum(1 for c in bundle_chains if c.bundle_id is not None)

    agent_section = {
        "total": len(chains),
        "categories": _sorted(categories),
        "complete_fraction_plane_runs": _fraction(complete, len(plane_runs)),
        "complete_fraction_all_runs": _fraction(
            complete + categories.get(NO_CAPABILITY_PLANE, 0), len(chains)
        ),
        "plane_runs": len(plane_runs),
        "route_linked_by": _sorted(linked_by),
        "with_outcome_event": sum(1 for c in chains if c.has_outcome_event),
    }
    bundle_section = {
        "route_runs_in_window": len(bundle_chains),
        "bundles_created": bundles_created,
        "categories": _sorted(bundle_categories),
        "outcome_coverage_fraction": _fraction(bundle_categories.get(COMPLETE, 0), bundles_created),
        "complete_by_client_type": _sorted(by_client),
    }
    per_run = [
        {
            "run_id": c.run_id,
            "category": c.category,
            "linked_by": c.linked_by,
            "route_run_ids": list(c.route_run_ids),
            "bundle_ids": list(c.bundle_ids),
            "activated": c.activated,
            "outcome_event": c.has_outcome_event,
        }
        for c in chains
    ]
    return CompletenessReport(
        generated_at=generated_at.astimezone(UTC).isoformat(),
        database=database,
        window={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        agent_runs=agent_section,
        routed_bundles=bundle_section,
        per_run=per_run,
    )


def render_text(report: CompletenessReport) -> str:
    """The human table (the --json flag prints the report dict instead)."""
    ar = report.agent_runs
    rb = report.routed_bundles
    lines: list[str] = []
    lines.append(f"== ACI evidence-chain completeness — database: {report.database} ==")
    w = report.window
    lines.append(
        f"generated {report.generated_at} · window "
        f"[{w['since'] or 'begin'}, {w['until'] or 'now'}] · read-only connection "
        "(default_transaction_read_only=on)"
    )
    lines.append("")
    lines.append("-- agent runs: route → bundle → activation → verdict (observational) --")
    lines.append(f"total {ar['total']} · plane-touching {ar['plane_runs']}")
    for category, count in ar["categories"].items():
        lines.append(f"  {category:<22} {count:>4}")
    lines.append(f"complete fraction (plane runs): {ar['complete_fraction_plane_runs']}")
    lines.append(f"complete fraction (all runs):  {ar['complete_fraction_all_runs']}")
    lines.append(f"route linked by: {ar['route_linked_by'] or '(none)'}")
    lines.append(f"agent runs with a §33 outcome event on their bundle: {ar['with_outcome_event']}")
    lines.append("")
    lines.append("-- per-run detail --")
    for row in report.per_run:
        lines.append(
            f"  {row['run_id']}  {row['category']:<22} linked_by={row['linked_by']} "
            f"routes={row['route_run_ids'] or '-'} bundles={row['bundle_ids'] or '-'} "
            f"activated={row['activated']} outcome={row['outcome_event']}"
        )
    lines.append("")
    lines.append("-- routed bundles: route → bundle → §33 outcome --")
    lines.append(
        f"route_runs in window {rb['route_runs_in_window']} · bundles {rb['bundles_created']}"
    )
    for category, count in rb["categories"].items():
        lines.append(f"  {category:<22} {count:>4}")
    lines.append(f"outcome coverage: {rb['outcome_coverage_fraction']}")
    lines.append(f"complete by client_type: {rb['complete_by_client_type'] or '(none)'}")
    lines.append("")
    lines.append("-- limitations (read before quoting any number) --")
    for position, limitation in enumerate(report.limitations, start=1):
        lines.append(f"  {position}. {limitation}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB layer — read-only queries only; every query here is a SELECT.
# ---------------------------------------------------------------------------

_CAPABILITY_EVENT_TYPES = ("capability.loaded", "capability.preload", "capability.exposure")


def _parse_moment(raw: str | None) -> datetime | None:
    """ISO date/datetime → tz-aware UTC moment (date = 00:00 UTC)."""
    if not raw:
        return None
    naive = datetime.fromisoformat(raw)
    if naive.tzinfo is None:
        naive = naive.replace(tzinfo=UTC)
    return naive.astimezone(UTC)


def _loaded_fact(payload: dict[str, Any]) -> LoadedEventFact:
    return LoadedEventFact(
        capability_id=payload.get("capability_id") or "",
        version=payload.get("version") or "",
        route_run_id=payload.get("route_run_id"),
        bundle_id=payload.get("bundle_id"),
        origin=payload.get("origin"),
        digest=payload.get("digest"),
    )


def fetch_evidence_data(
    conn: Connection, *, since: datetime | None = None, until: datetime | None = None
) -> EvidenceData:
    """Read the telemetry surfaces and build the raw facts (no classification)."""
    bounds: list[tuple[str, object]] = []
    if since is not None:
        bounds.append(("created_at >= :since", {"since": since}))
    if until is not None:
        bounds.append(("created_at <= :until", {"until": until}))
    where = (" WHERE " + " AND ".join(clause for clause, _ in bounds)) if bounds else ""
    params: dict[str, object] = {}
    for _, value in bounds:
        params.update(value)

    runs: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        text(
            "SELECT run_id, created_at, finished_at, status, stop_reason, "
            "evidence->>'verification_verdict' AS verdict FROM agent_runs" + where
        ),
        params,
    ):
        runs[row[0]] = {
            "created_at": row[1],
            "finished_at": row[2],
            "status": row[3],
            "stop_reason": row[4],
            "verifier_verdict": row[5],
            "loaded": [],
            "preloaded": [],
            "exposure_route_run_ids": [],
            "exposure_bundle_ids": [],
        }
    if runs:
        marks = ", ".join(f":r{i}" for i in range(len(runs)))
        event_params: dict[str, object] = {f"r{i}": run_id for i, run_id in enumerate(runs)}
        for row in conn.execute(
            text(
                "SELECT run_id, event_type, payload FROM agent_run_events "
                f"WHERE run_id IN ({marks}) AND event_type IN "
                "(:loaded, :preload, :exposure)",
            ),
            {
                **event_params,
                "loaded": "capability.loaded",
                "preload": "capability.preload",
                "exposure": "capability.exposure",
            },
        ):
            entry = runs.get(row[0])
            if entry is None or not isinstance(row[2], dict):
                continue
            if row[1] == "capability.loaded":
                entry["loaded"].append(_loaded_fact(row[2]))
            elif row[1] == "capability.preload":
                entry["preloaded"].extend(
                    cid for cid in row[2].get("loaded", []) if isinstance(cid, str)
                )
            else:  # capability.exposure
                for cap in row[2].get("capabilities", []):
                    if isinstance(cap, dict):
                        if isinstance(cap.get("route_run_id"), str):
                            entry["exposure_route_run_ids"].append(cap["route_run_id"])
                        if isinstance(cap.get("bundle_id"), str):
                            entry["exposure_bundle_ids"].append(cap["bundle_id"])

    def _route_rows(where: str, params: dict[str, object]) -> tuple[RouteRunFact, ...]:
        return tuple(
            RouteRunFact(
                route_run_id=row[0],
                created_at=row[1],
                client_type=row[2],
                bundle_id=row[3],
            )
            for row in conn.execute(
                text(
                    "SELECT route_run_id, created_at, client_type, bundle_id "
                    f"FROM route_runs {where}"
                ),
                params,
            )
        )

    kernel_route_runs = _route_rows("WHERE client_type = :kt", {"kt": KERNEL_CLIENT_TYPE})
    window_route_runs = _route_rows(where, params)
    item_counts: Counter[str] = Counter()
    for bundle_id, count in conn.execute(
        text("SELECT bundle_id, count(*) FROM bundle_items GROUP BY bundle_id")
    ):
        item_counts[bundle_id] = count
    bundles = tuple(
        BundleFact(bundle_id=row[0], route_run_id=row[1], item_count=item_counts[row[0]])
        for row in conn.execute(text("SELECT bundle_id, route_run_id FROM bundles"))
    )
    outcomes = tuple(
        OutcomeFact(
            outcome_id=row[0],
            route_run_id=row[1],
            bundle_id=row[2],
            received_at=row[3],
        )
        for row in conn.execute(
            text("SELECT outcome_id, route_run_id, bundle_id, received_at FROM outcome_events")
        )
    )
    agent_runs = tuple(
        AgentRunFact(
            run_id=run_id,
            created_at=values["created_at"],
            finished_at=values["finished_at"],
            status=values["status"],
            stop_reason=values["stop_reason"],
            verifier_verdict=values["verifier_verdict"],
            loaded=tuple(values["loaded"]),
            preloaded=tuple(values["preloaded"]),
            exposure_route_run_ids=tuple(values["exposure_route_run_ids"]),
            exposure_bundle_ids=tuple(values["exposure_bundle_ids"]),
        )
        for run_id, values in runs.items()
    )
    return EvidenceData(
        agent_runs=agent_runs,
        kernel_route_runs=kernel_route_runs,
        window_route_runs=window_route_runs,
        bundles=bundles,
        outcomes=outcomes,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--since", help="ISO date/datetime (UTC) window start, on created_at")
    parser.add_argument("--until", help="ISO date/datetime (UTC) window end, on created_at")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ACI_DATABASE_URL", DEFAULT_DATABASE_URL),
        help="operational DB URL (default: ACI_DATABASE_URL, else aci_bench on localhost)",
    )
    args = parser.parse_args(argv)
    since, until = _parse_moment(args.since), _parse_moment(args.until)

    generated_at = datetime.now(UTC)
    engine = open_read_only_engine(args.database_url)
    try:
        with engine.connect() as conn:
            assert_read_only(conn)
            data = fetch_evidence_data(conn, since=since, until=until)
    finally:
        engine.dispose()
    report = build_report(
        data,
        generated_at=generated_at,
        database=make_url(args.database_url).database or "?",
        since=since,
        until=until,
    )
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
