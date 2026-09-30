"""aci console — the read-only ops UI for the routing platform.

Server-rendered Jinja2, zero JS, one hand-written stylesheet. Every query
is read-only except ``POST /try``, which runs a REAL route (principal
``console``) so inspector traffic is honest telemetry, not a shadow path.
Governance actions (approve/promote) deliberately do NOT live here — the
human gates stay CLI (capctl) on purpose; a button is too easy to press.

All SQL reads go through the container's OWN database URL (§ never a
second source of truth for which DB to read).
"""

from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine, create_engine, text

from aci.adapters.inbound.rest.wiring import Container, get_container
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import RouteCapabilitiesCommand, TaskContext
from aci.domain.policy.models import ClientDescriptor, ProtocolDescriptor, RequestContext

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates" / "ui"))

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

#: Read-only engines, one per database URL — the console must read the
#: SAME database the container's repos write to, never a second URL.
_engines: dict[str, Engine] = {}


def _rows(
    container: Container, query: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    url = container.settings.database_url
    if url not in _engines:
        _engines[url] = create_engine(url)
    with _engines[url].connect() as conn:
        result = conn.execute(text(query), params or {})
        return [dict(row._mapping) for row in result]


def _render(request: Request, template: str, **context: object) -> HTMLResponse:
    return _TEMPLATES.TemplateResponse(
        request=request,
        name=template,
        context={**context, "env": {"date": "2026-09-30"}},
    )


@router.get("/")
def dashboard(
    request: Request, container: Annotated[Container, Depends(get_container)]
) -> HTMLResponse:
    corpus = _rows(
        container,
        "SELECT kind, count(*) AS n FROM capabilities GROUP BY kind ORDER BY kind",
    )
    releases = _rows(
        container,
        "SELECT channel, status, count(*) AS n FROM capability_releases"
        " GROUP BY channel, status ORDER BY channel, status",
    )
    routing = _rows(
        container,
        "SELECT count(*) AS runs,"
        " coalesce(round(avg(latency_ms)), 0) AS mean_latency,"
        " coalesce(round(avg(eligible_count)), 0) AS mean_eligible"
        " FROM route_runs",
    )[0]
    bundles = _rows(
        container,
        "SELECT count(*) AS total,"
        " count(*) FILTER (WHERE NOT EXISTS"
        "   (SELECT 1 FROM bundle_items bi WHERE bi.bundle_id = bundles.bundle_id)"
        " ) AS empty FROM bundles",
    )[0]
    verdicts = _rows(
        container,
        "SELECT source, status, count(*) AS n FROM outcome_verdicts"
        " GROUP BY source, status ORDER BY source, status",
    )
    benchmarks = _rows(
        container,
        """
        SELECT label, count(*) AS results,
               round(avg((metrics ->> 'recall')::numeric), 3) AS mean_recall
        FROM (
            SELECT DISTINCT ON (r.run_id, res.case_id)
                   r.label, res.case_id, res.variant, res.metrics
            FROM benchmark_runs r
            JOIN benchmark_results res ON res.run_id = r.run_id
            WHERE res.variant = 'full_pipeline'
            ORDER BY r.run_id, res.case_id, res.created_at DESC
        ) latest_per_case
        GROUP BY label
        ORDER BY label
        """,
    )
    return _render(
        request,
        "dashboard.html",
        corpus=corpus,
        releases=releases,
        routing=routing,
        bundles=bundles,
        verdicts=verdicts,
        benchmarks=benchmarks,
    )


@router.get("/routes")
def routes(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    principal: str = "",
) -> HTMLResponse:
    params: dict[str, Any] = {}
    where = ""
    if principal:
        where = " WHERE principal_id = :principal"
        params["principal"] = principal
    runs = _rows(
        container,
        "SELECT route_run_id, created_at, principal_id, client_type, task_text,"
        " latency_ms, eligible_count, bundle_id, error_code,"
        " stages -> 'retrieval' ->> 'model_id' AS embedder"
        f" FROM route_runs{where} ORDER BY created_at DESC LIMIT 60",
        params,
    )
    return _render(request, "routes.html", runs=runs, principal=principal)


@router.get("/routes/{route_run_id}")
def route_detail(
    route_run_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> HTMLResponse:
    run = container.route_runs.get_route_run(route_run_id)
    if run is None:
        raise DomainError(ErrorCode.ROUTE_RUN_NOT_FOUND, f"unknown route run {route_run_id}")
    bundle = container.bundles.get_bundle(run.bundle_id) if run.bundle_id else None
    verdicts = (
        _rows(
            container,
            "SELECT v.source, v.status, v.confidence, v.position"
            " FROM outcome_events oe"
            " JOIN outcome_verdicts v ON v.outcome_id = oe.outcome_id"
            " WHERE oe.bundle_id = :bid ORDER BY v.position",
            {"bid": run.bundle_id},
        )
        if run.bundle_id
        else []
    )
    return _render(
        request,
        "route_detail.html",
        run=run,
        bundle=bundle,
        verdicts=verdicts,
        stages=run.stages,
    )


@router.get("/corpus")
def corpus(
    request: Request, container: Annotated[Container, Depends(get_container)]
) -> HTMLResponse:
    rows = _rows(
        container,
        """
        SELECT c.id AS capability_id, c.kind, v.version, v.display_name,
               r.channel, r.status,
               lic.license_identifier,
               lic.permissions ->> 'can_redistribute' AS redistributable,
               sec.scan_status
        FROM capability_releases r
        JOIN capabilities c ON c.id = r.capability_id
        JOIN capability_versions v ON v.capability_id = r.capability_id AND v.version = r.version
        LEFT JOIN LATERAL (
            SELECT license_identifier, permissions, assessed_at FROM license_assessments la
            WHERE la.capability_id = r.capability_id AND la.version = r.version
            ORDER BY assessed_at DESC LIMIT 1
        ) lic ON true
        LEFT JOIN LATERAL (
            SELECT scan_status, scanned_at FROM security_assessments sa
            WHERE sa.capability_id = r.capability_id AND sa.version = r.version
            ORDER BY scanned_at DESC LIMIT 1
        ) sec ON true
        WHERE r.channel = 'production' AND r.status = 'active'
        ORDER BY c.id
        """,
    )
    return _render(request, "corpus.html", rows=rows)


@router.get("/corpus/{capability_id}")
def capability(
    capability_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> HTMLResponse:
    cap = container.capabilities.get_capability(capability_id)
    if cap is None:
        raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"unknown capability {capability_id}")
    versions = container.capabilities.list_versions(capability_id)
    releases = container.releases.list_releases(capability_id)
    provenance = _rows(
        container,
        "SELECT source_repository, commit_sha, source_url_reference, ingestion_status,"
        " ingested_at FROM source_records WHERE capability_id = :cid",
        {"cid": capability_id},
    )
    licenses = _rows(
        container,
        "SELECT DISTINCT ON (version) version, license_identifier,"
        " permissions ->> 'can_redistribute' AS redistributable, assessed_by, notes"
        " FROM license_assessments WHERE capability_id = :cid"
        " ORDER BY version, assessed_at DESC",
        {"cid": capability_id},
    )
    scans = _rows(
        container,
        "SELECT DISTINCT ON (version) version, scan_status, scanner_version, scanned_at"
        " FROM security_assessments WHERE capability_id = :cid"
        " ORDER BY version, scanned_at DESC",
        {"cid": capability_id},
    )
    return _render(
        request,
        "capability.html",
        cap=cap,
        versions=versions,
        releases=releases,
        provenance=provenance,
        licenses=licenses,
        scans=scans,
    )


@router.get("/try")
def try_form(request: Request) -> HTMLResponse:
    return _render(request, "try.html")


@router.post("/try")
def try_route(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    task: Annotated[str, Form()],
    language: Annotated[str, Form()] = "",
    frameworks: Annotated[str, Form()] = "",
    phase: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Run a REAL route (§14 pipeline, telemetry persisted) as principal
    'console' — the inspector is not a shadow path: its runs show up in
    /routes like any other client's."""
    context = TaskContext(
        language=language or None,
        frameworks=[f.strip() for f in frameworks.split(",") if f.strip()],
        phase=phase or None,
    )
    request_context = RequestContext(
        request_id=f"req_{uuid4().hex}",
        trace_id=f"trc_{uuid4().hex}",
        principal_id="console",
        client=ClientDescriptor(type="console", version=None),
        protocol=ProtocolDescriptor(type="rest", version="1"),
    )
    command = RouteCapabilitiesCommand(task_text=task, context=context)
    result = container.route_service.route(
        command,
        request_context.to_routing_context(context),
        request=request_context,
    )
    return RedirectResponse(f"/ui/routes/{result.route_run_id}", status_code=303)
