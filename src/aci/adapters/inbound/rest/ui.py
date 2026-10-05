"""aci console — the read-only ops UI for the routing platform.

Server-rendered Jinja2, zero JS, one hand-written stylesheet. Every query
is read-only except ``POST /try``, which runs a REAL route (principal
``console``) so inspector traffic is honest telemetry, not a shadow path.
Governance actions (approve/promote) deliberately do NOT live here — the
human gates stay CLI (capctl) on purpose; a button is too easy to press.

All SQL reads go through the container's OWN database URL (§ never a
second source of truth for which DB to read).

Auth (ACI_API_TOKEN set): a browser cannot attach a Bearer header, so
``POST /ui/login`` checks the token once and sets an HttpOnly,
SameSite=Strict session cookie holding an HMAC derived from it (never the
API token itself). Every console page requires that cookie; unsafe methods
additionally require a same-origin Origin/Referer, because SameSite ignores
ports and any other localhost service is "same-site". Empty token = open.
"""

import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from aci.adapters.inbound.rest.auth import tokens_match
from aci.adapters.inbound.rest.wiring import Container, get_container
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import RouteCapabilitiesCommand, TaskContext
from aci.domain.policy.models import ClientDescriptor, ProtocolDescriptor, RequestContext

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates" / "ui"))

SESSION_COOKIE = "aci_token"

_LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sign in · aci console</title><link rel="stylesheet" href="/ui/static/ui.css">
</head><body><main class="login"><div class="login-card">
<div class="brand"><span class="brand-mark">◆</span>
<span>aci <span class="dim">console</span></span></div>
<h1>Sign in</h1><p class="dim">{message}</p>
<form method="post" action="/ui/login">
<label for="token">API token</label>
<input type="password" name="token" id="token" autocomplete="current-password" required autofocus>
<button>Sign in</button>
</form></div></main></body></html>"""


def session_value(api_token: str) -> str:
    """Cookie value for a console session: derived from, never equal to, the token."""
    return hmac.new(api_token.encode(), b"aci-console-session", hashlib.sha256).hexdigest()


def _same_origin(request: Request) -> bool:
    own = f"{request.url.scheme}://{request.url.netloc}"
    origin = request.headers.get("origin")
    if origin is not None:
        return origin == own
    referer = request.headers.get("referer", "")
    return referer == own or referer.startswith(f"{own}/")


def _require_console_session(
    request: Request, container: Annotated[Container, Depends(get_container)]
) -> None:
    """Pages redirect to the login form without a valid session cookie;
    unsafe methods also need a same-origin request (CSRF)."""
    token = container.settings.api_token
    if not token:
        return
    cookie = request.cookies.get(SESSION_COOKIE, "")
    if not tokens_match(cookie, session_value(token)):
        if request.method in ("GET", "HEAD"):
            raise HTTPException(status_code=303, headers={"Location": "/ui/login"})
        raise HTTPException(status_code=401, detail="console session required")
    if request.method not in ("GET", "HEAD") and not _same_origin(request):
        raise HTTPException(status_code=403, detail="cross-origin request refused")


#: Open router: the login form only. Every console page hangs off `_console`.
router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)
_console = APIRouter(dependencies=[Depends(_require_console_session)])

#: Read-only engines, one per database URL — the console must read the
#: SAME database the container's repos write to, never a second URL.
_engines: dict[str, Engine] = {}

#: Example tasks for the inspector — one per routing category the dev set
#: covers, so a first visit exercises a real slice of the corpus.
TRY_EXAMPLES: tuple[str, ...] = (
    "Fix intermittent authentication failures under concurrent token refresh",
    "Migrate the shop service from SQLite to PostgreSQL without downtime",
    "Quarantine and fix flaky integration tests that depend on wall-clock time",
    "Rotate a leaked API key and purge it from git history",
    "Write release notes for the changes merged since v2.3.0",
)

#: Empty stage traces — a run that errored before a stage ran still renders.
_EMPTY_STAGES: dict[str, Any] = {
    "eligibility": {"kept": 0, "excluded": []},
    "retrieval": {
        "model_id": "—",
        "eligible_count": 0,
        "searched_count": 0,
        "returned_count": 0,
        "limit": 0,
    },
    "retrieved": [],
    "rerank": {"implementation": "—", "version": "", "input_count": 0, "output_count": 0},
    "reranked": [],
    "resolution": {"trace": {"selected_count": 0, "dropped_count": 0}, "dropped": []},
}


def _rows(
    container: Container, query: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    url = container.settings.database_url
    if url not in _engines:
        _engines[url] = create_engine(url)
    with _engines[url].connect() as conn:
        result = conn.execute(text(query), params or {})
        return [dict(row._mapping) for row in result]


def _render(
    request: Request, template: str, container: Container, **context: object
) -> HTMLResponse:
    settings = container.settings
    env = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "db": make_url(settings.database_url).database or "?",
        "embedder": settings.embedder,
        "path": request.url.path,
    }
    return _TEMPLATES.TemplateResponse(
        request=request, name=template, context={**context, "env": env}
    )


@_console.get("/")
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
        " coalesce(round(avg(eligible_count)), 0) AS mean_eligible,"
        " count(*) FILTER (WHERE error_code IS NOT NULL) AS errors"
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
    principals = _rows(
        container,
        "SELECT principal_id, client_type, count(*) AS n,"
        " coalesce(round(avg(latency_ms)), 0) AS mean_latency"
        " FROM route_runs GROUP BY principal_id, client_type ORDER BY n DESC LIMIT 6",
    )
    recent = _rows(
        container,
        "SELECT rr.route_run_id, rr.created_at, rr.principal_id, rr.task_text,"
        " rr.latency_ms, rr.error_code,"
        " (SELECT count(*) FROM bundle_items bi WHERE bi.bundle_id = rr.bundle_id) AS item_count"
        " FROM route_runs rr ORDER BY rr.created_at DESC LIMIT 8",
    )
    top_capabilities = _rows(
        container,
        "SELECT capability_id, count(*) AS n FROM bundle_items"
        " GROUP BY capability_id ORDER BY n DESC, capability_id LIMIT 8",
    )

    production_active = sum(
        int(r["n"]) for r in releases if r["channel"] == "production" and r["status"] == "active"
    )
    verdict_total = sum(int(v["n"]) for v in verdicts)
    verdict_success = sum(int(v["n"]) for v in verdicts if v["status"] == "success")
    return _render(
        request,
        "dashboard.html",
        container,
        corpus=corpus,
        corpus_total=sum(int(r["n"]) for r in corpus),
        production_active=production_active,
        releases=releases,
        routing=routing,
        bundles=bundles,
        verdicts=verdicts,
        verdict_total=verdict_total,
        verdict_success=verdict_success,
        benchmarks=benchmarks,
        principals=principals,
        principal_max=max((int(p["n"]) for p in principals), default=1),
        recent=recent,
        top_capabilities=top_capabilities,
        top_max=max((int(c["n"]) for c in top_capabilities), default=1),
    )


@_console.get("/routes")
def routes(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    principal: str = "",
) -> HTMLResponse:
    # Fully static SQL: the optional filter is a bound parameter ('' = no filter),
    # never string-built, so there is no injection surface (bandit B608).
    runs = _rows(
        container,
        "SELECT rr.route_run_id, rr.created_at, rr.principal_id, rr.client_type, rr.task_text,"
        " rr.latency_ms, rr.eligible_count, rr.bundle_id, rr.error_code,"
        " rr.stages -> 'retrieval' ->> 'model_id' AS embedder,"
        " (SELECT count(*) FROM bundle_items bi WHERE bi.bundle_id = rr.bundle_id) AS item_count"
        " FROM route_runs rr"
        " WHERE (CAST(:principal AS text) = '' OR rr.principal_id = :principal)"
        " ORDER BY rr.created_at DESC LIMIT 60",
        {"principal": principal},
    )
    principals = _rows(
        container,
        "SELECT principal_id, count(*) AS n FROM route_runs"
        " GROUP BY principal_id ORDER BY n DESC LIMIT 8",
    )
    return _render(
        request,
        "routes.html",
        container,
        runs=runs,
        principal=principal,
        principals=principals,
        max_latency=max((int(r["latency_ms"] or 0) for r in runs), default=1),
    )


@_console.get("/routes/{route_run_id}")
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
    stages = {key: run.stages.get(key, default) for key, default in _EMPTY_STAGES.items()}
    retrieved = stages["retrieved"]
    reranked = stages["reranked"]
    retrieval_rank = {c["capability_id"]: i + 1 for i, c in enumerate(retrieved)}
    # A real corpus excludes hundreds of candidates for a handful of reasons:
    # show one row per (reason, detail), largest first, with a few examples.
    groups: dict[tuple[str, str], list[str]] = {}
    for e in stages["eligibility"]["excluded"]:
        key = (str(e.get("reason", "")), str(e.get("detail", "")))
        groups.setdefault(key, []).append(str(e.get("capability_id", "")))
    exclusion_groups = [
        {"reason": r, "detail": d, "count": len(ids), "examples": ids[:3]}
        for (r, d), ids in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    return _render(
        request,
        "route_detail.html",
        container,
        run=run,
        bundle=bundle,
        verdicts=verdicts,
        stages=stages,
        retrieval_rank=retrieval_rank,
        exclusion_groups=exclusion_groups,
        max_retrieved=max((float(c["score"]) for c in retrieved), default=1.0) or 1.0,
        max_reranked=max((float(c["score"]) for c in reranked), default=1.0) or 1.0,
    )


@_console.get("/corpus")
def corpus(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    q: str = "",
    kind: str = "",
) -> HTMLResponse:
    rows = _rows(
        container,
        """
        SELECT c.id AS capability_id, c.kind, v.version, v.display_name, v.description,
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
          AND (:kind = '' OR c.kind = :kind)
          AND (:q = '' OR c.id ILIKE :like OR v.display_name ILIKE :like
               OR v.description ILIKE :like)
        ORDER BY c.id
        """,
        {"q": q, "like": f"%{q}%", "kind": kind},
    )
    kinds = _rows(
        container,
        "SELECT c.kind, count(*) AS n FROM capability_releases r"
        " JOIN capabilities c ON c.id = r.capability_id"
        " WHERE r.channel = 'production' AND r.status = 'active'"
        " GROUP BY c.kind ORDER BY c.kind",
    )
    return _render(
        request,
        "corpus.html",
        container,
        rows=rows,
        kinds=kinds,
        total=sum(int(k["n"]) for k in kinds),
        q=q,
        kind=kind,
    )


@_console.get("/corpus/{capability_id}")
def capability(
    capability_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> HTMLResponse:
    cap = container.capabilities.get_capability(capability_id)
    if cap is None:
        raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"unknown capability {capability_id}")
    versions = sorted(
        container.capabilities.list_versions(capability_id),
        key=lambda v: v.created_at,
        reverse=True,
    )
    releases = container.releases.list_releases(capability_id)
    provenance = _rows(
        container,
        "SELECT source_repository, commit_sha, source_url_reference, ingestion_status,"
        " ingested_at FROM source_records WHERE capability_id = :cid ORDER BY ingested_at DESC",
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

    # ADR-012 promotion gates, evaluated for the version that matters: the
    # active production release if there is one, else the newest version.
    production = next(
        (r for r in releases if r.channel == "production" and r.status == "active"), None
    )
    focus_version = production.version if production else (versions[0].version if versions else "")
    head = next((v for v in versions if v.version == focus_version), None)
    gates = {
        "provenance": any(p["ingestion_status"] == "accepted" for p in provenance),
        "license": any(
            lic["version"] == focus_version and lic["redistributable"] == "true" for lic in licenses
        ),
        "scan": any(s["version"] == focus_version and s["scan_status"] == "passed" for s in scans),
    }
    return _render(
        request,
        "capability.html",
        container,
        cap=cap,
        capability_id=capability_id,
        head=head,
        versions=versions,
        releases=releases,
        production=production,
        focus_version=focus_version,
        gates=gates,
        provenance=provenance,
        licenses=licenses,
        scans=scans,
    )


@_console.get("/try")
def try_form(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    task: str = "",
) -> HTMLResponse:
    return _render(request, "try.html", container, task=task, examples=TRY_EXAMPLES)


@_console.post("/try")
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


@router.get("/login")
def login_form(container: Annotated[Container, Depends(get_container)]) -> Response:
    if not container.settings.api_token:
        return RedirectResponse("/ui/", status_code=303)
    return HTMLResponse(_LOGIN_PAGE.format(message="this console requires the ACI api token."))


@router.post("/login")
def login(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    token: Annotated[str, Form()],
) -> Response:
    """Exchange the api token for a session cookie (HttpOnly, SameSite=Strict)."""
    api_token = container.settings.api_token
    if not api_token:
        return RedirectResponse("/ui/", status_code=303)
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="cross-origin request refused")
    if not tokens_match(token, api_token):
        return HTMLResponse(_LOGIN_PAGE.format(message="invalid token."), status_code=401)
    response = RedirectResponse("/ui/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        session_value(api_token),
        path="/ui",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return response


router.include_router(_console)
