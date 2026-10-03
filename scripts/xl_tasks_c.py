"""XL-orchestration fixtures (builder "c", 2026-10-03): RESEARCH + PROJECT.

Two XL fixtures for the xl-bench orchestration question (ADR-014 founding
architecture: client-side global DAG, ACI = capability WHAT + kernel HOW —
does an orchestrator that delegates code-writing leaves to the ACI service
beat OpenCode doing the whole task alone, on REALLY HARD, LONG tasks, and
what does a mid-run requirement change cost). THIS MODULE SHIPS FIXTURES
ONLY: it runs no model round and touches no product code (the kernel is
frozen for expansion; the bench harness is scripts/xl_bench.py, job
xl-harness).

Prior evidence, stated honestly (the standing rule): delegation/orchestration
value has NEVER been shown at this model tier on single-repo tasks — the
delegation probe (n=5 rubric), §80 (7 rounds, n=33, acceptance) and E1
(n=136/arm, powered) all found NO delta for decomposition or for the kernel
vs a naive loop (ADR-014 amendments 18/23). These fixtures exist to test
the ORCHESTRATION-SCALE boundary that none of those instruments measured;
a null result on them extends the prior verdict, it does not overturn it.

The two fixtures (one per this job's assignment):

1. xl-research-cinderpeak (kind "research") — an offline engineering-document
   archive of a FICTIONAL company (Cinderpeak Analytics Ltd, product
   "Beacon" — invented 2026-10-03, internally consistent BY CONSTRUCTION:
   every file is rendered from the single world-model below, and the hidden
   answer key is DERIVED from the same world-model, so corpus and key can
   never drift). 81 files: specs, per-release changelogs, incident reports,
   ADRs, runbooks, meeting notes, policies, and data CSVs. The task is 14
   multi-hop synthesis questions; the deliverable is answers.json (answers
   + per-answer citations), graded by a hidden key (exact values, sets,
   required citations). No web, no code to write — the work is reading,
   joining and computing.

2. xl-project-postbox (kind "project") — a small multi-component service
   (HTTP API + file storage + background delivery worker + CLI + docs)
   built FROM a product brief (27 numbered requirements), stdlib-only.
   The brief is deliberately ARCHITECTURE-NEUTRAL where the coming
   requirement change bites: the original hidden suite is satisfiable by
   an embedded-worker, cached-stats design AND by a clean shared-store
   design; the REQUIREMENT CHANGE (operations now mandates API and worker
   as SEPARATE processes sharing one store, stats correct across
   processes) is cheap for the clean design and expensive for the embedded/
   cached one — that is the "wrong early architectural choice is costly"
   lever the perturbation exploits.

Fixture contract (what scripts/xl_bench.py verify-fixture checks, and what
tests/unit/test_xl_tasks_c.py proves mechanically here):

- FAIL-AS-SHIPPED: the seed alone fails the original hidden suite
  (research: no answers.json; project: no postbox package).
- PASS-WHEN-SOLVED: the reference (kept OUTSIDE the workspace, applied as a
  file overlay) passes BOTH the original suite AND the post-change suite
  (original minus invalidated plus the change tests).
- INVALIDATES semantics — IMPORTANT, this is a harness constraint, not a
  style choice: xl_bench.verify_fixture requires the ONE reference to pass
  the FULL original suite (unperturbed case) AND the post-change suite, so
  the change can only ADD requirements and RETIRE original tests whose
  coverage the change subsumes; it can never reverse a behavior an
  original test pins. The retired tests still pass under the final
  reference — they are listed in change/invalidates.txt because the change
  supersedes them as the canonical pin of that coverage.
- Hidden tests are NEVER inside the seed workspace; the task text reveals
  no hidden test; all paths are safe relative paths.

§34 caveat applies to any round run on these fixtures: author-built,
small n, one model — directional only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ===========================================================================
# Part 1 — the Cinderpeak research fixture (xl-research-cinderpeak)
# ===========================================================================
#
# The world model below is the SINGLE SOURCE OF TRUTH: every corpus file is
# rendered from it, and the hidden answer key is DERIVED from it. Nothing is
# hand-copied between corpus and key, so they cannot drift.

_COMPANY = "Cinderpeak Analytics Ltd"
_PRODUCT = "Beacon"

#: beacon server releases: (version, date, kind, headline, [highlights], [fixes])
_RELEASES: list[tuple[str, str, str, str, list[str], list[str]]] = [
    (
        "0.4.0",
        "2023-11-14",
        "minor",
        "First GA of the ingest API.",
        [
            "Ingest API v1: `POST /api/v1/event` (one event per call).",
            "Site-id keys identify tenants (`site_id` config key, required).",
            "Daily-partitioned event tables (ADR-001, ADR-003).",
        ],
        [],
    ),
    (
        "0.5.0",
        "2024-02-06",
        "minor",
        "Query API v1 and TLS-ready ingest listener.",
        [
            "Query API v1: `GET /api/v1/stats` (site + metric parameters).",
            "New config key `retention_days` (default 90): events older than "
            "this are dropped by the nightly retention job.",
            "New config key `tls_cert_path` (default empty = plaintext listener): "
            "path to the PEM bundle for the ingest listener.",
            "Dashboard CSV export (beta).",
        ],
        [],
    ),
    (
        "0.6.0",
        "2024-05-21",
        "minor",
        "Batch ingest and the dashboard beta.",
        [
            "Batch ingest: `POST /api/v1/events` (up to 500 events per call).",
            "New config key `flush_interval` (default 5 seconds): how long the "
            "ingest buffer holds a batch before writing it.",
            "Dashboard beta shipped to all tenants.",
        ],
        [],
    ),
    (
        "0.6.1",
        "2024-06-04",
        "patch",
        "Clock-skew drop fix (INC-2024-003).",
        [],
        [
            "Events with future timestamps beyond the skew window were silently "
            "dropped (INC-2024-003); they are now rejected with 422 and counted.",
            "New config key `max_clock_skew` (default 300 seconds): events "
            "timestamped further than this from server time are rejected, never "
            "dropped.",
        ],
    ),
    (
        "0.7.0",
        "2024-09-10",
        "minor",
        "Retention tiers and per-tenant quotas.",
        [
            "Retention tiers (ADR-005): bronze/silver/gold with per-tier "
            "defaults of 90/180/365 days; a tenant-level `retention_days` value "
            "still overrides the tier default.",
            "Quota enforcement: `max_events_per_month` per tenant; over-quota "
            "writes fail closed with 429.",
            "Admin API v1: `GET /api/v1/admin/tenants` (tier, quota, usage).",
        ],
        [],
    ),
    (
        "0.8.0",
        "2025-01-15",
        "minor",
        "Event ID scheme v2 and the backfill tool.",
        [
            "Event ID scheme v2 (ADR-004): `evt_` + ULID; new config key "
            "`id_scheme` (default `v2`; `v1` ids remain readable but no new v1 "
            "ids are minted).",
            "Backfill tool `beacon backfill` (see rb-backfill).",
            "Config key RENAME: `flush_interval` -> `flush_interval_seconds` "
            "(default unchanged, 5 seconds); the old name is accepted as an "
            "alias for one release per policy-deprecation.",
        ],
        [],
    ),
    (
        "0.8.1",
        "2025-02-27",
        "patch",
        "Quota-overflow cascade fix (INC-2025-002).",
        [],
        [
            "Quota overflow returned 500 and cascaded into ingest backpressure "
            "(INC-2025-002); over-quota is now a first-class 429.",
            "New config key `quota_response` (default `reject`; `shed` accepts "
            "and drops) per ADR-006.",
        ],
    ),
    (
        "0.9.0",
        "2025-04-29",
        "minor",
        "Query API v2.",
        [
            "Query API v2: `GET /api/v2/query` with an explicit `aggregation` "
            "parameter (default `count`) per ADR-008.",
            "DEPRECATED `GET /api/v1/stats`: v1 stats answers v2 queries "
            "verbatim; removal is scheduled for the next major release per "
            "policy-deprecation.",
        ],
        [],
    ),
    (
        "0.9.1",
        "2025-06-10",
        "patch",
        "Shard-rebalance lock leak fix (INC-2025-004).",
        [],
        [
            "The shard rebalance lock leaked on failed rebalances and blocked "
            "the next one (INC-2025-004); locks are now released on every exit "
            "path.",
        ],
    ),
    (
        "1.0.0",
        "2025-09-18",
        "minor",
        "GA 1.0: v1 stats removed, aggregation cache.",
        [
            "REMOVED `GET /api/v1/stats` (deprecated in 0.9.0; removed per policy-deprecation).",
            "Aggregation cache (ADR-007) with new config key `cache_max_age` (default 60 seconds).",
            "Shipped in appliance bundle 2.0 (see changelogs/appliance-2.0.md).",
        ],
        [],
    ),
    (
        "1.0.1",
        "2025-11-25",
        "patch",
        "Stale aggregation cache fix (INC-2025-005).",
        [],
        [
            "The aggregation cache served counts past `cache_max_age` after a "
            "shard rebalance (INC-2025-005); entries now hard-expire on "
            "`cache_max_age` regardless of access.",
        ],
    ),
    (
        "1.0.2",
        "2026-03-05",
        "patch",
        "DST boundary double-count fix (INC-2026-002).",
        [],
        [
            "Query results double-counted events on DST transition days for "
            "non-UTC tenant timezones (INC-2026-002); bucketing is now on "
            "UTC day boundaries only.",
            "New config key `tz` (default `UTC`): DISPLAY-ONLY timezone for "
            "dashboards; internal timestamps are UTC-only per ADR-010.",
        ],
    ),
]

#: appliance (bundle) releases: (version, date, headline, [notes])
_APPLIANCE_RELEASES: list[tuple[str, str, str, list[str]]] = [
    (
        "1.3",
        "2024-08-30",
        "Appliance bundle 1.3: beacon server 0.7.0.",
        [
            "Ships beacon server 0.7.0.",
            "Adds the retention-tier migration wizard (bronze/silver/gold).",
            "Supported on the 1.x appliance line only.",
        ],
    ),
    (
        "1.4",
        "2025-02-20",
        "Appliance bundle 1.4: beacon server 0.8.1.",
        [
            "Ships beacon server 0.8.1.",
            "Adds the ID scheme v2 migration wizard (ADR-004).",
            "Last bundle of the 1.x appliance line.",
        ],
    ),
    (
        "2.0",
        "2025-09-18",
        "Appliance bundle 2.0: beacon server 1.0.0.",
        [
            "Ships beacon server 1.0.0.",
            "New bundle layout (ADR-009): server, worker and retention job are "
            "separate systemd units.",
            "Upgrade path from appliance 1.4 is in-place; from 1.3 it is export/import.",
        ],
    ),
    (
        "2.1",
        "2026-01-29",
        "Appliance bundle 2.1: beacon server 1.0.1.",
        [
            "Ships beacon server 1.0.1.",
            "Adds the TLS rotation timer (see rb-tls-rotation).",
        ],
    ),
]

#: incidents: (id, date, severity, component, duration_min, title, summary,
#:             timeline, root_cause, resolution, refs)
_INCIDENTS: list[dict[str, str]] = [
    {
        "id": "INC-2024-001",
        "date": "2024-01-19",
        "severity": "sev-2",
        "component": "ingest-gateway",
        "duration_min": "94",
        "title": "Ingest latency spike after the 0.5.0 deploy",
        "summary": (
            "Ingest p95 latency rose to 4s for 94 minutes after the 0.5.0 "
            "deploy; no events were lost."
        ),
        "timeline": [
            "09:02 — 0.5.0 deployed to the ingest fleet.",
            "09:14 — latency alert fired (p95 > 2s).",
            "09:41 — rollback of the query warmup job started.",
            "10:36 — latency back under 500ms; alert cleared.",
        ],
        "root_cause": (
            "The new query warmup job ran unthrottled against cold caches on the ingest nodes."
        ),
        "resolution": ("Config workaround (throttled warmup); no release fix required."),
        "refs": "docs/changelogs/beacon-0.5.0.md",
    },
    {
        "id": "INC-2024-002",
        "date": "2024-03-08",
        "severity": "sev-3",
        "component": "query-engine",
        "duration_min": "41",
        "title": "v1 stats timeouts under dashboard beta load",
        "summary": (
            "`GET /api/v1/stats` timed out for 41 minutes while the "
            "dashboard beta refreshed aggressively."
        ),
        "timeline": [
            "14:20 — dashboard beta rolled to all tenants.",
            "14:26 — v1 stats p99 > 30s; timeouts began.",
            "15:07 — dashboard refresh interval raised; timeouts cleared.",
        ],
        "root_cause": (
            "The dashboard beta issued one v1 stats call per tile with no client-side caching."
        ),
        "resolution": ("Config workaround (dashboard refresh interval); no release fix."),
        "refs": "docs/changelogs/beacon-0.6.0.md",
    },
    {
        "id": "INC-2024-003",
        "date": "2024-05-28",
        "severity": "sev-1",
        "component": "ingest-gateway",
        "duration_min": "212",
        "title": "Future-timestamped events silently dropped",
        "summary": (
            "Events timestamped in the future (client clock skew) were "
            "silently dropped for 212 minutes; an estimated 1.2M events from "
            "3 tenants were lost."
        ),
        "timeline": [
            "06:12 — a tenant reported missing events.",
            "06:40 — drop confirmed in the ingest logs (future timestamps).",
            "08:30 — hotpatch filter disabled; events flowed again.",
            "09:44 — incident closed; counts reconciled.",
        ],
        "root_cause": (
            "The 0.6.0 buffer wrote only events within a fixed window; "
            "future timestamps fell outside it and were dropped without "
            "counting."
        ),
        "resolution": "Fixed in beacon 0.6.1 (max_clock_skew, 422 not drop).",
        "refs": "docs/changelogs/beacon-0.6.1.md",
    },
    {
        "id": "INC-2024-004",
        "date": "2024-07-02",
        "severity": "sev-4",
        "component": "export",
        "duration_min": "18",
        "title": "CSV export mojibake",
        "summary": (
            "Dashboard CSV export produced mojibake for non-ASCII site names for 18 minutes."
        ),
        "timeline": [
            "11:02 — report received.",
            "11:20 — export switched to UTF-8 with BOM; verified.",
        ],
        "root_cause": "The export wrote latin-1 regardless of content.",
        "resolution": "Config workaround (export encoding); cosmetic.",
        "refs": "docs/changelogs/beacon-0.5.0.md",
    },
    {
        "id": "INC-2024-005",
        "date": "2024-08-16",
        "severity": "sev-2",
        "component": "storage",
        "duration_min": "155",
        "title": "Disk pressure from unbounded daily tables",
        "summary": (
            "Daily event tables grew unbounded and filled the data volume to "
            "93%; ingest degraded for 155 minutes."
        ),
        "timeline": [
            "02:00 — disk watermark alert (80%).",
            "02:40 — ingest degraded (backpressure).",
            "04:15 — oldest tables archived to cold storage.",
            "04:35 — ingest recovered; disk at 61%.",
        ],
        "root_cause": (
            "No retention policy existed; every event was kept forever "
            "(the unbounded-growth class)."
        ),
        "resolution": ("Led to ADR-005 (retention tiers), shipped in beacon 0.7.0."),
        "refs": "docs/adr/ADR-005.md",
    },
    {
        "id": "INC-2024-006",
        "date": "2024-10-30",
        "severity": "sev-4",
        "component": "alerting",
        "duration_min": "12",
        "title": "Misrouted severity alerts",
        "summary": "Two sev-2 alerts were routed to the sev-4 queue for 12 minutes.",
        "timeline": [
            "16:05 — misroute noticed on the on-call board.",
            "16:17 — routing map fixed; alerts re-fired correctly.",
        ],
        "root_cause": "A stale routing map after the on-call rotation change.",
        "resolution": "Config fix; no release fix.",
        "refs": "docs/policies/policy-severity.md",
    },
    {
        "id": "INC-2025-001",
        "date": "2025-01-26",
        "severity": "sev-2",
        "component": "ingest-gateway",
        "duration_min": "88",
        "title": "ID collisions during the v2 dual-write window",
        "summary": (
            "During the ID scheme v2 rollout, the dual-write window minted "
            "duplicate ids for 88 minutes; 4,120 events were deduplicated."
        ),
        "timeline": [
            "10:02 — v2 rollout started (dual-write on).",
            "10:31 — duplicate-id alert fired.",
            "11:15 — dual-write window closed; v2 only.",
            "11:30 — dedupe verified; incident closed.",
        ],
        "root_cause": (
            "Both schemes derived ids from the same timestamp+site material "
            "during the dual-write window."
        ),
        "resolution": (
            "Config resolution (close the dual-write window); procedural follow-up, no release fix."
        ),
        "refs": "docs/adr/ADR-004.md",
    },
    {
        "id": "INC-2025-002",
        "date": "2025-02-11",
        "severity": "sev-1",
        "component": "quota-enforcer",
        "duration_min": "176",
        "title": "Quota overflow cascaded into ingest backpressure",
        "summary": (
            "Over-quota tenants produced unhandled 500s in the quota enforcer; "
            "retries piled up and backpressured ingest for 176 minutes."
        ),
        "timeline": [
            "08:20 — month-start quota window opened.",
            "08:44 — 500-rate alert on the quota enforcer.",
            "09:50 — ingest backpressure visible (p99 > 8s).",
            "11:16 — enforcer hotpatched to fail closed; ingest recovered.",
        ],
        "root_cause": (
            "The quota enforcer raised an unhandled exception on over-quota "
            "writes — the same unbounded-growth class as INC-2024-005, on the "
            "write path."
        ),
        "resolution": ("Fixed in beacon 0.8.1 (quota_response config, 429 not 500, per ADR-006)."),
        "refs": "docs/adr/ADR-006.md",
    },
    {
        "id": "INC-2025-003",
        "date": "2025-03-22",
        "severity": "sev-3",
        "component": "backfill",
        "duration_min": "37",
        "title": "Backfill tool ignored rate limits",
        "summary": (
            "A tenant backfill replayed 40 days of events unthrottled for 37 "
            "minutes, starving batch ingest."
        ),
        "timeline": [
            "13:05 — backfill started (tenant-initiated).",
            "13:19 — batch ingest latency alert.",
            "13:42 — backfill paused by ops; throttle added.",
        ],
        "root_cause": "The backfill tool had no client-side rate limiter.",
        "resolution": "Procedural (throttle guidance in rb-backfill); no release fix.",
        "refs": "docs/runbooks/rb-backfill.md",
    },
    {
        "id": "INC-2025-004",
        "date": "2025-05-30",
        "severity": "sev-2",
        "component": "storage",
        "duration_min": "129",
        "title": "Shard rebalance lock leak",
        "summary": (
            "A failed rebalance leaked the shard lock and blocked the next "
            "rebalance for 129 minutes; during the stall the v1 stats endpoint "
            "served stale counts (it was already deprecated, read-only)."
        ),
        "timeline": [
            "21:00 — scheduled rebalance started.",
            "21:24 — rebalance failed (disk watermark); lock NOT released.",
            "22:40 — next rebalance blocked; alert fired.",
            "23:09 — lock cleared manually; rebalance completed.",
        ],
        "root_cause": "The rebalance lock was held in a context that swallowed the failure path.",
        "resolution": "Fixed in beacon 0.9.1 (lock released on every exit path).",
        "refs": "docs/runbooks/rb-shard-rebalance.md",
    },
    {
        "id": "INC-2025-005",
        "date": "2025-10-12",
        "severity": "sev-2",
        "component": "query-engine",
        "duration_min": "64",
        "title": "Stale aggregation cache reads after rebalance",
        "summary": (
            "After a shard rebalance, the aggregation cache served stale "
            "counts for 64 minutes (v2 queries under-reported by up to 9%)."
        ),
        "timeline": [
            "07:30 — rebalance completed.",
            "08:02 — tenant reported a count mismatch.",
            "08:41 — cache flush; counts verified correct.",
            "09:06 — incident closed.",
        ],
        "root_cause": (
            "Cache entries refreshed on access, so an unaccessed shard's "
            "entries never hard-expired past `cache_max_age`."
        ),
        "resolution": "Fixed in beacon 1.0.1 (hard expiry on cache_max_age).",
        "refs": "docs/adr/ADR-007.md",
    },
    {
        "id": "INC-2025-006",
        "date": "2025-12-04",
        "severity": "sev-3",
        "component": "query-engine",
        "duration_min": "45",
        "title": "v2 query 500s on empty date ranges after appliance 2.0",
        "summary": (
            "After the appliance 2.0 rollout, `GET /api/v2/query` returned 500 "
            "on empty date ranges for 45 minutes."
        ),
        "timeline": [
            "05:12 — appliance 2.0 rollout finished on shard 3.",
            "05:31 — 500-rate alert on v2 query.",
            "06:16 — hotpatch (empty-range guard); alert cleared.",
        ],
        "root_cause": (
            "The 1.0.0 aggregation cache returned None for an empty range and "
            "the serializer did not guard it."
        ),
        "resolution": "Hotpatch; guarded upstream in beacon 1.0.1.",
        "refs": "docs/changelogs/appliance-2.0.md",
    },
    {
        "id": "INC-2026-001",
        "date": "2026-01-27",
        "severity": "sev-2",
        "component": "ingest-gateway",
        "duration_min": "52",
        "title": "TLS certificate rotation gap",
        "summary": (
            "A manual TLS rotation left the ingest listener serving an expired "
            "certificate for 52 minutes."
        ),
        "timeline": [
            "03:00 — rotation started (manual, shard 1).",
            "03:18 — expiry alert fired (the old bundle was still bound).",
            "03:52 — listener rebound to the new bundle; verified.",
        ],
        "root_cause": ("The rotation rebinding step was manual and skipped in the runbook."),
        "resolution": (
            "Procedural (rb-tls-rotation updated, timer added in appliance 2.1); no release fix."
        ),
        "refs": "docs/runbooks/rb-tls-rotation.md",
    },
    {
        "id": "INC-2026-002",
        "date": "2026-02-14",
        "severity": "sev-1",
        "component": "query-engine",
        "duration_min": "97",
        "title": "DST boundary double-count",
        "summary": (
            "On the Europe/Berlin DST transition day, v2 queries "
            "double-counted the 01:00-02:00 hour for 97 minutes; dashboards "
            "showed up to 2x counts."
        ),
        "timeline": [
            "00:58 — tenant report (counts visibly wrong).",
            "01:20 — double-count confirmed (bucketing on local day boundaries).",
            "02:35 — hotpatch (UTC bucketing); counts verified.",
        ],
        "root_cause": (
            "Query bucketing used the tenant's local day boundary; on the DST "
            "transition the 01:00-02:00 hour existed twice. The same "
            "time-handling class as INC-2024-003 (timestamps trusted from "
            "clients)."
        ),
        "resolution": "Fixed in beacon 1.0.2 (UTC-only bucketing, tz display-only).",
        "refs": "docs/adr/ADR-010.md",
    },
    {
        "id": "INC-2026-003",
        "date": "2026-04-09",
        "severity": "sev-3",
        "component": "retention",
        "duration_min": "33",
        "title": "Gold-tier retention job overlap",
        "summary": (
            "Two gold-tier retention jobs overlapped after a manual rerun and "
            "double-dropped one day of one tenant's events (restored from "
            "archive)."
        ),
        "timeline": [
            "04:30 — manual rerun of the retention job.",
            "04:44 — overlap detected (double drop).",
            "05:03 — job stopped; day restored from cold archive.",
        ],
        "root_cause": "The retention job had no run-lock.",
        "resolution": "Procedural (run-lock added to rb-retention-audit); no release fix.",
        "refs": "docs/runbooks/rb-retention-audit.md",
    },
]

#: ADRs: (id, date, author, title, status, context, decision, consequences)
_ADRS: list[dict[str, str]] = [
    {
        "id": "ADR-001",
        "date": "2023-10-02",
        "author": "Priya Nair",
        "title": "Single-node SQLite-first storage for 0.4",
        "status": "Accepted",
        "context": "0.4 must run on one appliance with zero external services.",
        "decision": (
            "SQLite in WAL mode as the only 0.4 store; Postgres was rejected "
            "(an external process violates the appliance contract)."
        ),
        "consequences": "Migration to Postgres stays open for multi-node (see ADR-003).",
    },
    {
        "id": "ADR-002",
        "date": "2023-12-11",
        "author": "Marco Reyes",
        "title": "Site-id as the only tenant key",
        "status": "Accepted",
        "context": "Tenants are identified on every event and every query.",
        "decision": (
            "One opaque site-id per tenant; per-site API keys were rejected "
            "(key rotation would break ingest URLs)."
        ),
        "consequences": "Site-ids are immutable; rotation means a new tenant.",
    },
    {
        "id": "ADR-003",
        "date": "2024-04-08",
        "author": "Priya Nair",
        "title": "Daily-partitioned event tables",
        "status": "Accepted",
        "context": "Range scans dominate (retention, day rollups).",
        "decision": "One partition per UTC day; drops and rollups are partition-wide.",
        "consequences": "Rebalances move whole days (see rb-shard-rebalance).",
    },
    {
        "id": "ADR-004",
        "date": "2024-11-15",
        "author": "Juno Park",
        "title": "Event ID scheme v2 (ULID)",
        "status": "Accepted",
        "context": "v1 ids (site + timestamp) collided under batch ingest.",
        "decision": (
            "v2 ids are `evt_` + ULID; v1 stays readable; dual-write only "
            "during the 0.8.0 rollout window."
        ),
        "consequences": "Shipped in beacon 0.8.0; backfill tool mints v2 (rb-backfill).",
    },
    {
        "id": "ADR-005",
        "date": "2024-08-23",
        "author": "Dana Kovacs",
        "title": "Retention tiers (bronze/silver/gold)",
        "status": "Accepted",
        "context": "INC-2024-005: unbounded growth filled the data volume.",
        "decision": (
            "Three tiers with per-tier retention defaults (90/180/365 days) "
            "and per-tier monthly event quotas; a tenant-level retention_days "
            "overrides the tier default."
        ),
        "consequences": "Shipped in beacon 0.7.0; quotas enforced from 0.7.0.",
    },
    {
        "id": "ADR-006",
        "date": "2025-02-20",
        "author": "Marco Reyes",
        "title": "Quota response modes (reject/shed)",
        "status": "Accepted",
        "context": "INC-2025-002: over-quota 500s cascaded into ingest.",
        "decision": (
            "Over-quota is a first-class 429; `quota_response` selects reject "
            "(default) or shed (accept and drop)."
        ),
        "consequences": "Shipped in beacon 0.8.1.",
    },
    {
        "id": "ADR-007",
        "date": "2025-08-29",
        "author": "Priya Nair",
        "title": "Aggregation cache with hard max_age",
        "status": "Accepted",
        "context": "v2 queries recompute the same day rollups on every dashboard load.",
        "decision": (
            "Cache day-level aggregates with `cache_max_age` (default 60 "
            "seconds); entries hard-expire, never refresh-on-access only."
        ),
        "consequences": "Shipped in beacon 1.0.0; INC-2025-005 forced the hard expiry.",
    },
    {
        "id": "ADR-008",
        "date": "2025-04-10",
        "author": "Sam Okafor",
        "title": "Query API v2 with explicit aggregation",
        "status": "Accepted",
        "context": "v1 stats fixed the metric shape; tenants need sum/avg/uniques.",
        "decision": (
            "`GET /api/v2/query` takes an explicit `aggregation` (default "
            "`count`); v1 stats is deprecated in 0.9.0 and answers v2 "
            "verbatim until removal."
        ),
        "consequences": "Shipped in beacon 0.9.0; v1 stats removed in 1.0.0.",
    },
    {
        "id": "ADR-009",
        "date": "2025-12-12",
        "author": "Dana Kovacs",
        "title": "Appliance 2.0 bundle layout",
        "status": "Accepted",
        "context": "1.x bundles ran server, worker and retention job as one unit.",
        "decision": (
            "2.0 splits server, worker and retention job into separate "
            "systemd units sharing one store."
        ),
        "consequences": "Shipped in appliance 2.0 (beacon 1.0.0).",
    },
    {
        "id": "ADR-010",
        "date": "2026-01-20",
        "author": "Juno Park",
        "title": "UTC-only internal timestamps",
        "status": "Accepted",
        "context": "INC-2026-002: local-boundary bucketing double-counted a DST hour.",
        "decision": (
            "All internal timestamps and bucket boundaries are UTC; the `tz` "
            "config key is display-only."
        ),
        "consequences": "Shipped in beacon 1.0.2.",
    },
]

#: runbooks: (file, title, owner, body lines)
_RUNBOOKS: list[tuple[str, str, str, str]] = [
    (
        "rb-shard-rebalance.md",
        "Shard rebalance",
        "Priya Nair",
        "Moves whole UTC days between shards (ADR-003).\n\n"
        "1. Take the rebalance lock (one at a time — a leaked lock blocks the\n"
        "   next run, see INC-2025-004; beacon 0.9.1 releases it on every exit\n"
        "   path).\n"
        "2. Copy the day partitions; verify row counts per partition.\n"
        "3. Flip the routing map; drop the source partitions after 24h.\n\n"
        "During a rebalance, expect stale counts on the deprecated v1 stats\n"
        "endpoint (read-only since 0.9.0) — v2 queries stay correct.",
    ),
    (
        "rb-quota-overflow.md",
        "Quota overflow",
        "Marco Reyes",
        "What to do when a tenant hits `max_events_per_month`.\n\n"
        "1. Confirm the over-quota in the admin API (`GET /api/v1/admin/tenants`).\n"
        "2. Over-quota writes are 429 (never 500 — INC-2025-002); the tenant\n"
        "   sees the retry window in the response body.\n"
        "3. `quota_response` (ADR-006) selects reject (default) or shed.\n"
        "4. Raising a tier is a billing action, not an ops one.",
    ),
    (
        "rb-backfill.md",
        "Backfill",
        "Juno Park",
        "Replaying events with `beacon backfill` (shipped in beacon 0.8.0).\n\n"
        "1. Backfill mints v2 ids only (ADR-004); v1 material is read-only.\n"
        "2. Throttle to 100 events/s — the tool has no built-in limiter\n"
        "   (INC-2025-003 starved batch ingest for 37 minutes).\n"
        "3. Verify with a v2 count query before closing the ticket.",
    ),
    (
        "rb-tls-rotation.md",
        "TLS certificate rotation",
        "Marco Reyes",
        "Rotating the ingest listener certificate.\n\n"
        "1. Drop the new PEM bundle at `tls_cert_path` (config key since\n"
        "   beacon 0.5.0; default empty = plaintext listener).\n"
        "2. REBIND the listener — the rebinding step was skipped in\n"
        "   INC-2026-001 and the listener served an expired cert for 52\n"
        "   minutes.\n"
        "3. Verify with an external probe before closing.\n"
        "4. Appliance 2.1 ships the rotation timer; manual rotation stays\n"
        "   supported.",
    ),
    (
        "rb-retention-audit.md",
        "Retention audit",
        "Dana Kovacs",
        "Auditing the nightly retention job (tiers per ADR-005).\n\n"
        "1. One job per store — the retention job has no run-lock\n"
        "   (INC-2026-003: a manual rerun overlapped and double-dropped a\n"
        "   day; restored from cold archive).\n"
        "2. Gold-tier tenants keep 365 days; verify the archive manifest\n"
        "   matches the drop manifest before deleting anything.\n"
        "3. A tenant-level `retention_days` overrides the tier default\n"
        "   (shipped in beacon 0.7.0).",
    ),
]

#: meetings: (file, date, attendees, title, [decisions])
_MEETINGS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "2024-06-07-eng-weekly.md",
        "2024-06-07",
        "Priya Nair, Marco Reyes, Juno Park",
        "Eng weekly — post-INC-2024-003",
        [
            "INC-2024-003 postmortem read-out: future-timestamp drops were "
            "uncounted; the fix (0.6.1) rejects with 422 and counts them.",
            "Agreed: every drop path must be counted — silent drops are a sev-1 class.",
        ],
    ),
    (
        "2024-08-23-eng-weekly.md",
        "2024-08-23",
        "Dana Kovacs, Priya Nair, Marco Reyes",
        "Eng weekly — retention tiers proposal",
        [
            "INC-2024-005 (disk pressure) made retention urgent; Dana "
            "proposed the bronze/silver/gold tier model.",
            "Agreed to write it up as an ADR (became ADR-005); per-tier "
            "defaults 90/180/365 days, tenant-level override allowed.",
        ],
    ),
    (
        "2024-11-15-eng-weekly.md",
        "2024-11-15",
        "Juno Park, Priya Nair, Sam Okafor",
        "Eng weekly — ID scheme v2",
        [
            "Juno presented the ULID-based v2 scheme (`evt_` prefix); v1 "
            "stays readable, dual-write only during the rollout window.",
            "Agreed to write it up as an ADR (became ADR-004) and ship it in "
            "0.8.0 with the backfill tool.",
        ],
    ),
    (
        "2025-02-20-eng-weekly.md",
        "2025-02-20",
        "Marco Reyes, Dana Kovacs",
        "Eng weekly — quota response modes",
        [
            "INC-2025-002 postmortem: over-quota must be a first-class 429, "
            "never an unhandled 500.",
            "Marco proposed the reject/shed modes; agreed to write it up as "
            "an ADR (became ADR-006) and ship the config in 0.8.1.",
        ],
    ),
    (
        "2025-04-10-eng-weekly.md",
        "2025-04-10",
        "Sam Okafor, Priya Nair",
        "Eng weekly — query API v2",
        [
            "Sam presented v2 with the explicit `aggregation` parameter "
            "(default count); v1 stats is deprecated in 0.9.0 and answers v2 "
            "verbatim until removal.",
            "Agreed to write it up as an ADR (became ADR-008).",
        ],
    ),
    (
        "2025-08-29-eng-weekly.md",
        "2025-08-29",
        "Priya Nair, Sam Okafor, Juno Park",
        "Eng weekly — aggregation cache",
        [
            "Priya proposed the day-level aggregation cache with a hard "
            "`cache_max_age` (default 60 seconds); refresh-on-access alone "
            "was rejected (stale shards would never expire).",
            "Agreed to write it up as an ADR (became ADR-007) and ship it in 1.0.0.",
        ],
    ),
    (
        "2025-12-12-eng-weekly.md",
        "2025-12-12",
        "Dana Kovacs, Marco Reyes",
        "Eng weekly — appliance 2.0",
        [
            "Dana presented the 2.0 bundle layout: server, worker and "
            "retention job as separate systemd units sharing one store.",
            "Agreed to write it up as an ADR (became ADR-009); upgrade path from 1.4 in-place.",
        ],
    ),
    (
        "2026-02-20-eng-weekly.md",
        "2026-02-20",
        "Juno Park, Priya Nair, Sam Okafor",
        "Eng weekly — DST postmortem",
        [
            "INC-2026-002 postmortem: bucketing moves to UTC day boundaries "
            "only; `tz` becomes display-only.",
            "Agreed to write it up as an ADR (became ADR-010) and ship in 1.0.2.",
        ],
    ),
]

#: policies: (file, title, body)
_POLICIES: list[tuple[str, str, str]] = [
    (
        "policy-severity.md",
        "Severity policy",
        "# Severity policy\n\n"
        "How every incident is graded (see also data/incidents.csv).\n\n"
        "| Severity | Meaning | Page | Ack SLA |\n"
        "|----------|---------|------|---------|\n"
        "| sev-1 | Full outage or data loss | yes | 15 min |\n"
        "| sev-2 | Degraded core flow (ingest or query) | yes | 60 min |\n"
        "| sev-3 | Degraded non-core flow | no | 240 min |\n"
        "| sev-4 | Cosmetic | no | next business day |\n\n"
        "Core flows are ingest and query; everything else (export, alerting,\n"
        "appliance tooling) is non-core.\n",
    ),
    (
        "policy-versioning.md",
        "Versioning policy",
        "# Versioning policy\n\n"
        "Beacon server follows semver:\n\n"
        "- minor: new features, new config keys;\n"
        "- patch: fixes only — a patch release never adds a config key\n"
        "  (0.6.1 and 0.8.1 added keys during incidents; that is the\n"
        "  recorded exception, not the rule);\n"
        "- config keys are stable within a minor.\n",
    ),
    (
        "policy-deprecation.md",
        "Deprecation policy",
        "# Deprecation policy\n\n"
        "- An API deprecated in release X is REMOVED in the next MAJOR\n"
        "  release after X.\n"
        "- A renamed config key keeps the old name as an accepted alias for\n"
        "  one release, then the alias is removed.\n",
    ),
    (
        "policy-support.md",
        "Support policy",
        "# Support policy\n\n"
        "- The CURRENT appliance major and the PREVIOUS appliance major are\n"
        "  supported; older majors are end-of-life.\n"
        "- An appliance bundle runs the beacon server it ships, plus at most\n"
        "  one minor ahead.\n",
    ),
]

#: team roster: (name, team, role, runbook owned)
_TEAM: list[tuple[str, str, str, str]] = [
    ("Priya Nair", "Storage & Query", "lead engineer", "rb-shard-rebalance.md"),
    ("Marco Reyes", "Ingest & Gateway", "staff engineer", "rb-tls-rotation.md"),
    ("Juno Park", "Core Services", "senior engineer", "rb-backfill.md"),
    ("Dana Kovacs", "Operations", "ops lead", "rb-retention-audit.md"),
    ("Sam Okafor", "Query & Dashboards", "engineer", "rb-quota-overflow.md"),
]

#: tenants: (id, name, tier, region, since)
_TENANTS: list[tuple[str, str, str, str, str]] = [
    ("t-01", "harbor-lights.example", "bronze", "us-east", "2023-11-20"),
    ("t-02", "glasshouse.example", "bronze", "us-west", "2023-12-04"),
    ("t-03", "northwind-motors.example", "gold", "eu-central", "2023-11-22"),
    ("t-04", "bluefin.example", "bronze", "us-east", "2024-01-09"),
    ("t-05", "copperfield.example", "silver", "us-west", "2024-02-15"),
    ("t-06", "dunlin.example", "bronze", "eu-west", "2024-03-01"),
    ("t-07", "atlas-logistics.example", "gold", "eu-central", "2024-02-15"),
    ("t-08", "emberly.example", "bronze", "us-east", "2024-04-18"),
    ("t-09", "fjordworks.example", "silver", "eu-north", "2024-05-06"),
    ("t-10", "gravelpit.example", "bronze", "us-west", "2024-06-11"),
    ("t-11", "kestrel-bank.example", "gold", "us-east", "2024-07-23"),
    ("t-12", "larkspur.example", "silver", "eu-west", "2024-08-29"),
]

#: tier quotas: tier -> (max_events_per_month, retention_days)
_QUOTA_TIERS: dict[str, tuple[int, int]] = {
    "bronze": (250_000, 90),
    "silver": (2_500_000, 180),
    "gold": (25_000_000, 365),
}

#: monthly event volume bases (events/month scale) per tenant
_USAGE_BASE: dict[str, int] = {
    "t-01": 120_000,
    "t-02": 60_000,
    "t-03": 3_200_000,
    "t-04": 90_000,
    "t-05": 800_000,
    "t-06": 45_000,
    "t-07": 6_500_000,
    "t-08": 70_000,
    "t-09": 950_000,
    "t-10": 30_000,
    "t-11": 4_100_000,
    "t-12": 600_000,
}


def _monthly_events(tenant: str, year: int, month: int) -> int:
    """Deterministic, mildly seasonal monthly volume (no external randomness)."""
    base = _USAGE_BASE[tenant]
    bump = 1.0 + 0.06 * ((month * 7 + year) % 5)
    season = 1.0 + (0.04 if month in (3, 6, 9) else 0.0)
    return int(base * bump * season)


def _usage_rows(year: int, months: range) -> list[tuple[str, str, int]]:
    return [
        (tenant, f"{year}-{month:02d}", _monthly_events(tenant, year, month))
        for tenant, _, _, _, _ in _TENANTS
        for month in months
    ]


#: p99 query latency (ms) per version per month: (version, month, p99_ms)
_LATENCY: list[tuple[str, str, int]] = (
    [
        ("0.8.0", f"2025-{m:02d}", v)
        for m, v in enumerate([172, 168, 165, 161, 158, 154, 151, 149, 146, 143, 141, 139], start=1)
    ]
    + [
        ("0.9.0", f"2025-{m:02d}", v)
        for m, v in enumerate([158, 152, 149, 145, 141, 138, 135, 133, 130, 128, 126, 124], start=1)
    ]
    + [("0.9.0", f"2026-{m:02d}", v) for m, v in enumerate([122, 121, 119, 118, 117, 116], start=1)]
    + [("1.0.0", f"2025-{m:02d}", v) for m, v in enumerate([128, 121, 116, 112, 109], start=9)]
    + [("1.0.0", f"2026-{m:02d}", v) for m, v in enumerate([107, 106, 104, 103, 102, 101], start=1)]
    + [("1.0.1", f"2025-{m:02d}", v) for m, v in enumerate([104, 101], start=11)]
    + [
        ("1.0.1", "2026-01", 99),
        ("1.0.1", "2026-02", 812),
        ("1.0.1", "2026-03", 96),
        ("1.0.1", "2026-04", 95),
        ("1.0.1", "2026-05", 94),
        ("1.0.1", "2026-06", 93),
    ]
    + [("1.0.2", f"2026-{m:02d}", v) for m, v in enumerate([92, 91, 90, 89], start=3)]
)

#: installs per version per month: version -> {month: installs}
_ADOPTION: dict[str, dict[str, int]] = {
    "0.4.0": {
        "2023-11": 40,
        "2023-12": 95,
        "2024-01": 150,
        "2024-02": 190,
        "2024-03": 210,
        "2024-04": 205,
        "2024-05": 180,
        "2024-06": 150,
        "2024-07": 120,
        "2024-08": 95,
        "2024-09": 80,
        "2024-10": 70,
        "2024-11": 60,
        "2024-12": 55,
    },
    "0.5.0": {
        "2024-02": 60,
        "2024-03": 140,
        "2024-04": 260,
        "2024-05": 380,
        "2024-06": 450,
        "2024-07": 500,
        "2024-08": 540,
        "2024-09": 560,
        "2024-10": 540,
        "2024-11": 500,
        "2024-12": 460,
        "2025-01": 420,
        "2025-02": 380,
        "2025-03": 340,
        "2025-04": 300,
        "2025-05": 270,
        "2025-06": 240,
        "2025-07": 210,
        "2025-08": 190,
        "2025-09": 170,
        "2025-10": 150,
        "2025-11": 140,
        "2025-12": 130,
        "2026-01": 120,
        "2026-02": 110,
        "2026-03": 100,
        "2026-04": 95,
        "2026-05": 90,
        "2026-06": 85,
    },
    "0.6.0": {
        "2024-05": 30,
        "2024-06": 90,
        "2024-07": 160,
        "2024-08": 220,
        "2024-09": 260,
        "2024-10": 280,
        "2024-11": 290,
        "2024-12": 285,
        "2025-01": 270,
        "2025-02": 255,
        "2025-03": 240,
        "2025-04": 225,
        "2025-05": 210,
        "2025-06": 195,
        "2025-07": 180,
        "2025-08": 165,
        "2025-09": 150,
        "2025-10": 140,
        "2025-11": 130,
        "2025-12": 120,
        "2026-01": 110,
        "2026-02": 100,
        "2026-03": 92,
        "2026-04": 85,
        "2026-05": 80,
        "2026-06": 75,
    },
    "0.6.1": {
        "2024-06": 25,
        "2024-07": 70,
        "2024-08": 110,
        "2024-09": 140,
        "2024-10": 155,
        "2024-11": 160,
        "2024-12": 158,
        "2025-01": 150,
        "2025-02": 142,
        "2025-03": 135,
        "2025-04": 128,
        "2025-05": 120,
        "2025-06": 112,
        "2025-07": 105,
        "2025-08": 98,
        "2025-09": 90,
        "2025-10": 84,
        "2025-11": 78,
        "2025-12": 72,
        "2026-01": 66,
        "2026-02": 60,
        "2026-03": 55,
        "2026-04": 50,
        "2026-05": 46,
        "2026-06": 42,
    },
    "0.7.0": {
        "2024-09": 45,
        "2024-10": 130,
        "2024-11": 210,
        "2024-12": 280,
        "2025-01": 330,
        "2025-02": 370,
        "2025-03": 400,
        "2025-04": 425,
        "2025-05": 445,
        "2025-06": 460,
        "2025-07": 470,
        "2025-08": 478,
        "2025-09": 470,
        "2025-10": 460,
        "2025-11": 450,
        "2025-12": 440,
        "2026-01": 430,
        "2026-02": 420,
        "2026-03": 410,
        "2026-04": 400,
        "2026-05": 392,
        "2026-06": 385,
    },
    "0.8.0": {
        "2025-01": 35,
        "2025-02": 95,
        "2025-03": 170,
        "2025-04": 240,
        "2025-05": 300,
        "2025-06": 350,
        "2025-07": 390,
        "2025-08": 420,
        "2025-09": 445,
        "2025-10": 460,
        "2025-11": 470,
        "2025-12": 475,
        "2026-01": 470,
        "2026-02": 465,
        "2026-03": 455,
        "2026-04": 445,
        "2026-05": 438,
        "2026-06": 430,
    },
    "0.8.1": {
        "2025-02": 20,
        "2025-03": 60,
        "2025-04": 110,
        "2025-05": 155,
        "2025-06": 195,
        "2025-07": 230,
        "2025-08": 258,
        "2025-09": 280,
        "2025-10": 298,
        "2025-11": 312,
        "2025-12": 322,
        "2026-01": 318,
        "2026-02": 312,
        "2026-03": 305,
        "2026-04": 298,
        "2026-05": 292,
        "2026-06": 286,
    },
    "0.9.0": {
        "2025-04": 25,
        "2025-05": 75,
        "2025-06": 130,
        "2025-07": 180,
        "2025-08": 225,
        "2025-09": 265,
        "2025-10": 300,
        "2025-11": 330,
        "2025-12": 355,
        "2026-01": 348,
        "2026-02": 340,
        "2026-03": 332,
        "2026-04": 325,
        "2026-05": 318,
        "2026-06": 310,
    },
    "0.9.1": {
        "2025-06": 15,
        "2025-07": 45,
        "2025-08": 80,
        "2025-09": 112,
        "2025-10": 140,
        "2025-11": 165,
        "2025-12": 186,
        "2026-01": 182,
        "2026-02": 178,
        "2026-03": 172,
        "2026-04": 168,
        "2026-05": 164,
        "2026-06": 160,
    },
    "1.0.0": {
        "2025-09": 120,
        "2025-10": 310,
        "2025-11": 512,
        "2025-12": 640,
        "2026-01": 705,
        "2026-02": 750,
        "2026-03": 780,
        "2026-04": 800,
        "2026-05": 815,
        "2026-06": 830,
    },
    "1.0.1": {
        "2025-11": 40,
        "2025-12": 95,
        "2026-01": 150,
        "2026-02": 200,
        "2026-03": 245,
        "2026-04": 285,
        "2026-05": 320,
        "2026-06": 350,
    },
    "1.0.2": {
        "2026-03": 30,
        "2026-04": 85,
        "2026-05": 135,
        "2026-06": 180,
    },
}

_ROADMAP_2024 = [
    ("Retention tiers", "shipped in beacon 0.7.0 (ADR-005)"),
    ("Per-tenant quotas", "shipped in beacon 0.7.0"),
]
_ROADMAP_2025 = [
    ("Event ID scheme v2", "shipped in beacon 0.8.0 (ADR-004)"),
    ("Query API v2", "shipped in beacon 0.9.0 (ADR-008)"),
    ("Aggregation cache", "shipped in beacon 1.0.0 (ADR-007)"),
    ("Appliance 2.0 bundle", "shipped 2025-09-18 (ADR-009)"),
]
_ROADMAP_2026 = [
    ("UTC-only internals", "shipped in beacon 1.0.2 (ADR-010)"),
    ("Multi-region ingest", "OPEN — no design yet"),
    ("Query API v3 (streaming)", "OPEN — no design yet"),
]

_GLOSSARY: list[tuple[str, str]] = [
    ("site-id", "the opaque per-tenant key every event and query carries (ADR-002)"),
    ("ingest", "the write path: v1 single event, v1 batch (0.6.0+)"),
    ("query", "the read path: v1 stats (removed in 1.0.0), v2 query (0.9.0+)"),
    ("retention job", "the nightly drop of events past the tier default (ADR-005)"),
    ("appliance", "the self-hosted bundle: server + worker + retention job (ADR-009)"),
    ("shard", "one store partition; rebalances move whole UTC days (ADR-003)"),
    ("spool", "n/a in this archive — Cinderpeak has no delivery spool"),
]


# ---------------------------------------------------------------------------
# Research corpus renderers (world model -> files)
# ---------------------------------------------------------------------------


def _csv(header: list[str], rows: list[list[Any]]) -> str:
    lines = [",".join(header)]
    lines += [",".join(str(cell) for cell in row) for row in rows]
    return "\n".join(lines) + "\n"


def _release(version: str) -> dict[str, Any]:
    for rel in _RELEASES:
        if rel[0] == version:
            return {
                "version": rel[0],
                "date": rel[1],
                "kind": rel[2],
                "headline": rel[3],
                "highlights": rel[4],
                "fixes": rel[5],
            }
    raise KeyError(version)


def _release_date(version: str) -> str:
    return _release(version)["date"]


def _version_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return (int(major), int(minor), int(patch))


#: config keys: key -> {introduced, defaults: [(version, default)], note}
_CONFIG_KEYS: dict[str, dict[str, Any]] = {
    "site_id": {
        "introduced": "0.4.0",
        "defaults": [("0.4.0", "required")],
        "note": "the tenant key on every event and query (ADR-002)",
    },
    "retention_days": {
        "introduced": "0.5.0",
        "defaults": [
            ("0.5.0", "90"),
            ("0.7.0", "per-tier (90/180/365); a tenant-level value overrides"),
        ],
        "note": "days before the nightly retention drop",
    },
    "tls_cert_path": {
        "introduced": "0.5.0",
        "defaults": [("0.5.0", "(empty = plaintext listener)")],
        "note": "PEM bundle for the ingest listener",
    },
    "flush_interval": {
        "introduced": "0.6.0",
        "defaults": [("0.6.0", "5")],
        "note": "ingest buffer hold, seconds (RENAMED in 0.8.0)",
    },
    "max_clock_skew": {
        "introduced": "0.6.1",
        "defaults": [("0.6.1", "300")],
        "note": "events timestamped further than this from server time are "
        "rejected (422), never dropped (INC-2024-003)",
    },
    "max_events_per_month": {
        "introduced": "0.7.0",
        "defaults": [("0.7.0", "per tenant (tier)")],
        "note": "monthly event quota; over-quota fails closed with 429",
    },
    "id_scheme": {
        "introduced": "0.8.0",
        "defaults": [("0.8.0", "v2")],
        "note": "v2 = evt_ + ULID (ADR-004); v1 readable, no new v1 ids",
    },
    "flush_interval_seconds": {
        "introduced": "0.8.0",
        "defaults": [("0.8.0", "5")],
        "note": "RENAMED from flush_interval (alias accepted one release)",
    },
    "quota_response": {
        "introduced": "0.8.1",
        "defaults": [("0.8.1", "reject")],
        "note": "over-quota mode: reject (429) or shed (ADR-006)",
    },
    "aggregation": {
        "introduced": "0.9.0",
        "defaults": [("0.9.0", "count")],
        "note": "v2 query default aggregation (ADR-008)",
    },
    "cache_max_age": {
        "introduced": "1.0.0",
        "defaults": [("1.0.0", "60")],
        "note": "aggregation cache hard expiry, seconds (ADR-007)",
    },
    "tz": {
        "introduced": "1.0.2",
        "defaults": [("1.0.2", "UTC")],
        "note": "DISPLAY-ONLY dashboard timezone; internals are UTC-only (ADR-010)",
    },
}

#: endpoints: (method, path, introduced, note, removed_in)
_ENDPOINTS: list[tuple[str, str, str, str, str | None]] = [
    ("POST", "/api/v1/event", "0.4.0", "single-event ingest", None),
    ("GET", "/api/v1/stats", "0.5.0", "v1 stats (site + metric)", "1.0.0"),
    ("POST", "/api/v1/events", "0.6.0", "batch ingest, up to 500 events", None),
    ("GET", "/api/v1/admin/tenants", "0.7.0", "admin: tier, quota, usage", None),
    ("GET", "/api/v2/query", "0.9.0", "v2 query with the aggregation parameter", None),
]


def _default_at(key: str, version: str) -> str:
    current = _CONFIG_KEYS[key]["defaults"][0][1]
    for at, default in _CONFIG_KEYS[key]["defaults"]:
        if _version_key(at) <= _version_key(version):
            current = default
    return current


def _render_spec(version: str) -> str:
    rel = _release(version)
    lines = [
        f"# Beacon server spec — {version}",
        "",
        f"- Spec date: {rel['date']}",
        f"- Covers everything shipped through beacon {version}.",
        "",
        "## Endpoints",
        "",
        "| Method | Path | Note |",
        "|--------|-----|------|",
    ]
    for method, path, introduced, note, removed in _ENDPOINTS:
        if removed is not None and _version_key(removed) <= _version_key(version):
            continue
        if _version_key(introduced) <= _version_key(version):
            lines.append(f"| {method} | {path} | {note} |")
    lines += [
        "",
        "## Config keys",
        "",
        "| Key | Default | Note |",
        "|-----|---------|------|",
    ]
    for key in sorted(_CONFIG_KEYS):
        meta = _CONFIG_KEYS[key]
        if _version_key(meta["introduced"]) > _version_key(version):
            continue
        lines.append(f"| `{key}` | {_default_at(key, version)} | {meta['note']} |")
    lines += [
        "",
        "## Notes",
        "",
    ]
    if version == "0.9.0":
        lines.append(
            "- `GET /api/v1/stats` is DEPRECATED in this release: it answers v2 "
            "queries verbatim and is scheduled for removal in the next major "
            "release per policy-deprecation."
        )
    if version == "1.0.0":
        lines.append(
            "- `GET /api/v1/stats` (deprecated in 0.9.0) is REMOVED in this "
            "release per policy-deprecation."
        )
    lines.append("- Config keys are stable within a minor (policy-versioning).")
    return "\n".join(lines) + "\n"


def _render_changelog(version: str) -> str:
    rel = _release(version)
    lines = [
        f"# Beacon {version} changelog",
        "",
        f"- Release date: {rel['date']}",
        f"- Kind: {rel['kind']}",
        f"- {rel['headline']}",
        "",
    ]
    if rel["highlights"]:
        lines += ["## Highlights", ""]
        lines += [f"- {h}" for h in rel["highlights"]]
        lines += [""]
    if rel["fixes"]:
        lines += ["## Fixes", ""]
        lines += [f"- {f}" for f in rel["fixes"]]
        lines += [""]
    return "\n".join(lines).rstrip() + "\n"


def _render_appliance_changelog(version: str) -> str:
    for ver, date, headline, notes in _APPLIANCE_RELEASES:
        if ver == version:
            lines = [
                f"# Appliance {version} changelog",
                "",
                f"- Release date: {date}",
                f"- {headline}",
                "",
                "## Notes",
                "",
            ]
            lines += [f"- {n}" for n in notes]
            return "\n".join(lines) + "\n"
    raise KeyError(version)


def _render_incident(inc: dict[str, str]) -> str:
    lines = [
        f"# {inc['id']} — {inc['title']}",
        "",
        f"- Date: {inc['date']}",
        f"- Severity: {inc['severity']} (per policy-severity)",
        f"- Component: {inc['component']}",
        f"- Duration: {inc['duration_min']} minutes",
        f"- Resolution: {inc['resolution']}",
        f"- References: {inc['refs']}",
        "",
        "## Summary",
        "",
        inc["summary"],
        "",
        "## Timeline",
        "",
    ]
    lines += [f"- {step}" for step in inc["timeline"]]
    lines += [
        "",
        "## Root cause",
        "",
        inc["root_cause"],
        "",
    ]
    return "\n".join(lines) + "\n"


def _render_adr(adr: dict[str, str]) -> str:
    return "\n".join(
        [
            f"# {adr['id']} — {adr['title']}",
            "",
            f"- Date: {adr['date']}",
            f"- Author: {adr['author']}",
            f"- Status: {adr['status']}",
            "",
            "## Context",
            "",
            adr["context"],
            "",
            "## Decision",
            "",
            adr["decision"],
            "",
            "## Consequences",
            "",
            adr["consequences"],
            "",
        ]
    )


def _render_meeting(filename: str) -> str:
    for fname, date, attendees, title, decisions in _MEETINGS:
        if fname == filename:
            lines = [
                f"# Eng weekly — {date}",
                "",
                f"- Attendees: {attendees}",
                f"- Topic: {title}",
                "",
                "## Decisions",
                "",
            ]
            lines += [f"- {d}" for d in decisions]
            return "\n".join(lines) + "\n"
    raise KeyError(filename)


def _research_seed() -> dict[str, str]:
    """The Cinderpeak archive (rendered from the world model above)."""
    files: dict[str, str] = {}
    files["README.md"] = (
        "# Cinderpeak engineering archive\n"
        "\n"
        f"The internal engineering archive of {_COMPANY} for its self-hosted\n"
        f"web-analytics product {_PRODUCT} (server + appliance bundles).\n"
        "Everything here is internally consistent; cross-references between\n"
        "files are by relative path.\n"
        "\n"
        "Layout:\n"
        "\n"
        "- docs/specs/ — the server spec per release (endpoints + config keys)\n"
        "- docs/changelogs/ — one file per server release and per appliance bundle\n"
        "- docs/incidents/ — incident reports (timeline, root cause, resolution)\n"
        "- docs/adr/ — architecture decision records\n"
        "- docs/runbooks/ — operational procedures\n"
        "- docs/meetings/ — eng weekly notes\n"
        "- docs/policies/ — severity, versioning, deprecation, support\n"
        "- data/ — the operational CSVs (releases, incidents, tenants, quotas,\n"
        "  usage, latency, adoption)\n"
        "- docs/glossary.md, docs/team.md, docs/roadmap.md\n"
        "\n"
        "QUESTIONS.md holds the research questions; answers go to answers.json.\n"
    )
    files["docs/glossary.md"] = "\n".join(
        ["# Glossary", ""] + [f"- **{term}** — {meaning}" for term, meaning in _GLOSSARY] + [""]
    )
    files["docs/team.md"] = "\n".join(
        [
            "# Team roster",
            "",
            "| Name | Team | Role | Runbook owned |",
            "|------|------|------|---------------|",
        ]
        + [f"| {n} | {t} | {r} | docs/runbooks/{rb} |" for n, t, r, rb in _TEAM]
        + [""]
    )
    files["docs/roadmap.md"] = "\n".join(
        ["# Roadmap", ""]
        + ["## 2024 themes", ""]
        + [f"- **{theme}** — {status}" for theme, status in _ROADMAP_2024]
        + ["", "## 2025 themes", ""]
        + [f"- **{theme}** — {status}" for theme, status in _ROADMAP_2025]
        + ["", "## 2026 themes", ""]
        + [f"- **{theme}** — {status}" for theme, status in _ROADMAP_2026]
        + [""]
    )
    for spec_version in ("0.4.0", "0.5.0", "0.6.0", "0.7.0", "0.8.0", "0.9.0", "1.0.0"):
        files[f"docs/specs/beacon-spec-{spec_version}.md"] = _render_spec(spec_version)
    for rel in _RELEASES:
        files[f"docs/changelogs/beacon-{rel[0]}.md"] = _render_changelog(rel[0])
    for ver, _date, _headline, _notes in _APPLIANCE_RELEASES:
        files[f"docs/changelogs/appliance-{ver}.md"] = _render_appliance_changelog(ver)
    for inc in _INCIDENTS:
        files[f"docs/incidents/{inc['id']}.md"] = _render_incident(inc)
    for adr in _ADRS:
        files[f"docs/adr/{adr['id']}.md"] = _render_adr(adr)
    for filename, _title, _owner, body in _RUNBOOKS:
        files[f"docs/runbooks/{filename}"] = f"# {_title}\n\n{body}\n"
    for filename, _date, _att, _title, _dec in _MEETINGS:
        files[f"docs/meetings/{filename}"] = _render_meeting(filename)
    for filename, _title, body in _POLICIES:
        files[f"docs/policies/{filename}"] = body
    files["data/releases.csv"] = _csv(
        ["version", "date", "kind", "changelog"],
        [[rel[0], rel[1], rel[2], f"docs/changelogs/beacon-{rel[0]}.md"] for rel in _RELEASES]
        + [
            [f"appliance-{ver}", date, "bundle", f"docs/changelogs/appliance-{ver}.md"]
            for ver, date, _h, _n in _APPLIANCE_RELEASES
        ],
    )
    files["data/incidents.csv"] = _csv(
        ["incident", "date", "severity", "component", "duration_min", "resolution", "report"],
        [
            [
                inc["id"],
                inc["date"],
                inc["severity"],
                inc["component"],
                inc["duration_min"],
                _resolution_sort(inc["resolution"]),
                f"docs/incidents/{inc['id']}.md",
            ]
            for inc in _INCIDENTS
        ],
    )
    files["data/tenants.csv"] = _csv(
        ["tenant_id", "name", "tier", "region", "since"],
        [[t[0], t[1], t[2], t[3], t[4]] for t in _TENANTS],
    )
    files["data/quotas.csv"] = _csv(
        ["tier", "max_events_per_month", "retention_days"],
        [[tier, quota[0], quota[1]] for tier, quota in _QUOTA_TIERS.items()],
    )
    files["data/usage-2024.csv"] = _csv(
        ["tenant_id", "month", "events"], _usage_rows(2024, range(1, 13))
    )
    files["data/usage-2025.csv"] = _csv(
        ["tenant_id", "month", "events"], _usage_rows(2025, range(1, 13))
    )
    files["data/usage-2026H1.csv"] = _csv(
        ["tenant_id", "month", "events"], _usage_rows(2026, range(1, 7))
    )
    files["data/latency.csv"] = _csv(
        ["version", "month", "p99_ms"],
        [[v, m, p] for v, m, p in _LATENCY],
    )
    adoption_rows: list[list[Any]] = []
    for version in sorted(_ADOPTION, key=_version_key):
        for month in sorted(_ADOPTION[version]):
            adoption_rows.append([version, month, _ADOPTION[version][month]])
    files["data/adoption.csv"] = _csv(["version", "month", "installs"], adoption_rows)
    files["QUESTIONS.md"] = _RESEARCH_QUESTIONS
    return files


def _resolution_sort(resolution: str) -> str:
    """The incidents.csv resolution column: the fixing release, or the mode."""
    for rel in _RELEASES:
        if f"beacon {rel[0]}" in resolution:
            return rel[0]
    if "Config" in resolution or "config" in resolution:
        return "config"
    if "rocedural" in resolution or "otpatch" in resolution:
        return "procedural"
    return "none"


# ---------------------------------------------------------------------------
# Research questions, answer key (derived from the world model), graders
# ---------------------------------------------------------------------------


def _q03_answer() -> dict[str, Any]:
    totals: dict[str, int] = {}
    for tenant, _month, events in _usage_rows(2025, range(1, 13)):
        totals[tenant] = totals.get(tenant, 0) + events
    best = max(totals, key=lambda t: totals[t])
    tier = {t[0]: t[2] for t in _TENANTS}[best]
    return {"tenant_id": best, "tier": tier}


def _q07_answer() -> dict[str, Any]:
    rows = [
        (month, p99, version)
        for version, month, p99 in _LATENCY
        if version.startswith("1.0.") and month.startswith("2026") and int(month[-2:]) <= 6
    ]
    month, p99, _version = max(rows, key=lambda row: row[1])
    incident = [i["id"] for i in _INCIDENTS if i["date"].startswith(month[:7])]
    assert len(incident) == 1, incident
    return {"month": month, "p99_ms": p99, "incident": incident[0]}


def _q09_answer() -> dict[str, Any]:
    first = min(month for month, installs in _ADOPTION["1.0.0"].items() if installs > 500)
    total = sum(installs.get(first, 0) for installs in _ADOPTION.values())
    return {"month": first, "total_installs": total}


def _q12_answer() -> dict[str, Any]:
    gold = [t[0] for t in _TENANTS if t[2] == "gold"]
    combined = sum(
        events for tenant, _month, events in _usage_rows(2025, range(1, 13)) if tenant in gold
    )
    return {"gold_tenants": gold, "combined_2025_events": combined}


_SEV_ORDER = {"sev-1": 0, "sev-2": 1, "sev-3": 2, "sev-4": 3}


def _q16_answer() -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    for inc in _INCIDENTS:
        year = inc["date"][:4]
        counts.setdefault(year, {})
        counts[year][inc["component"]] = counts[year].get(inc["component"], 0) + 1
    most_2024 = max(counts["2024"], key=lambda c: counts["2024"][c])
    most_2025 = max(counts["2025"], key=lambda c: counts["2025"][c])
    hi_2024 = min(
        (i for i in _INCIDENTS if i["date"].startswith("2024")),
        key=lambda i: _SEV_ORDER[i["severity"]],
    )
    hi_2025 = min(
        (i for i in _INCIDENTS if i["date"].startswith("2025")),
        key=lambda i: _SEV_ORDER[i["severity"]],
    )
    return {
        "most_incidents_2024": most_2024,
        "most_incidents_2025": most_2025,
        "highest_severity_2024": hi_2024["id"],
        "fix_2024": _resolution_sort(hi_2024["resolution"]),
        "highest_severity_2025": hi_2025["id"],
        "fix_2025": _resolution_sort(hi_2025["resolution"]),
    }


#: The hidden answer key: qid -> {answer, citations}. DERIVED from the world
#: model above (never hand-copied), so the corpus and the key cannot drift.
_RESEARCH_KEY: dict[str, dict[str, Any]] = {
    "q01": {
        "answer": {
            "introduced_in": "0.6.0",
            "renamed_in": "0.8.0",
            "new_name": "flush_interval_seconds",
        },
        "citations": [
            "docs/changelogs/beacon-0.6.0.md",
            "docs/changelogs/beacon-0.8.0.md",
        ],
    },
    "q02": {
        "answer": {"incident": "INC-2024-003", "severity": "sev-1", "duration_min": 212},
        "citations": [
            "docs/changelogs/beacon-0.6.1.md",
            "docs/incidents/INC-2024-003.md",
            "docs/policies/policy-severity.md",
        ],
    },
    "q03": {"answer": _q03_answer(), "citations": ["data/usage-2025.csv", "data/tenants.csv"]},
    "q04": {
        "answer": {
            "superseded_in": "0.7.0",
            "tier_retention_days": {
                "bronze": _QUOTA_TIERS["bronze"][1],
                "silver": _QUOTA_TIERS["silver"][1],
                "gold": _QUOTA_TIERS["gold"][1],
            },
        },
        "citations": [
            "docs/specs/beacon-spec-0.5.0.md",
            "docs/changelogs/beacon-0.7.0.md",
            "data/quotas.csv",
        ],
    },
    "q05": {
        "answer": {"adr": "ADR-007", "release": "1.0.0", "cache_max_age_default_seconds": 60},
        "citations": [
            "docs/adr/ADR-007.md",
            "docs/changelogs/beacon-1.0.0.md",
            "docs/specs/beacon-spec-1.0.0.md",
        ],
    },
    "q06": {
        "answer": {
            "api": "GET /api/v1/stats",
            "removed_in": "1.0.0",
            "incident": "INC-2025-004",
        },
        "citations": [
            "docs/changelogs/beacon-0.9.0.md",
            "docs/policies/policy-deprecation.md",
            "docs/changelogs/beacon-1.0.0.md",
            "docs/incidents/INC-2025-004.md",
        ],
    },
    "q07": {
        "answer": _q07_answer(),
        "citations": ["data/latency.csv", "docs/incidents/INC-2026-002.md"],
    },
    "q08": {
        "answer": {
            "author": "Juno Park",
            "team": "Core Services",
            "role": "senior engineer",
            "release": "0.8.0",
        },
        "citations": [
            "docs/adr/ADR-004.md",
            "docs/team.md",
            "docs/changelogs/beacon-0.8.0.md",
        ],
    },
    "q09": {"answer": _q09_answer(), "citations": ["data/adoption.csv"]},
    "q10": {
        "answer": {
            "earlier_incident": "INC-2024-003",
            "config_key": "max_clock_skew",
            "default_seconds": 300,
        },
        "citations": [
            "docs/incidents/INC-2026-002.md",
            "docs/incidents/INC-2024-003.md",
            "docs/changelogs/beacon-0.6.1.md",
        ],
    },
    "q11": {
        "answer": {"release": "1.0.0", "adr": "ADR-007"},
        "citations": [
            "docs/meetings/2025-08-29-eng-weekly.md",
            "docs/changelogs/beacon-1.0.0.md",
            "docs/adr/ADR-007.md",
        ],
    },
    "q12": {"answer": _q12_answer(), "citations": ["data/tenants.csv", "data/usage-2025.csv"]},
    "q13": {
        "answer": {"config_key": "tls_cert_path", "introduced_in": "0.5.0"},
        "citations": [
            "docs/runbooks/rb-tls-rotation.md",
            "docs/changelogs/beacon-0.5.0.md",
        ],
    },
    "q14": {
        "answer": {"theme": "UTC-only internals", "release": "1.0.2", "adr": "ADR-010"},
        "citations": [
            "docs/roadmap.md",
            "docs/changelogs/beacon-1.0.2.md",
            "docs/adr/ADR-010.md",
        ],
    },
}

#: The audit (change) questions and their key entries.
_RESEARCH_CHANGE_KEY: dict[str, dict[str, Any]] = {
    "q15": {
        "answer": {"beacon_version": "1.0.0", "supported_appliance_majors": ["1", "2"]},
        "citations": [
            "docs/changelogs/appliance-2.0.md",
            "docs/policies/policy-support.md",
        ],
    },
    "q16": {
        "answer": _q16_answer(),
        "citations": [
            "data/incidents.csv",
            "docs/changelogs/beacon-0.6.1.md",
            "docs/changelogs/beacon-0.8.1.md",
        ],
    },
}

#: The audited quote pins (change-graded): qid -> one verbatim line of a
#: cited file that carries a fact the answer depends on.
_RESEARCH_QUOTES: dict[str, str] = {
    "q01": (
        "New config key `flush_interval` (default 5 seconds): how long the "
        "ingest buffer holds a batch before writing it."
    ),
    "q03": ",".join(str(cell) for cell in next(t for t in _TENANTS if t[0] == "t-07")),
    "q09": f"1.0.0,{_q09_answer()['month']},{_ADOPTION['1.0.0'][_q09_answer()['month']]}",
    "q15": "Ships beacon server 1.0.0.",
    "q16": ",".join(
        str(cell)
        for cell in [
            "INC-2025-002",
            "2025-02-11",
            "sev-1",
            "quota-enforcer",
            "176",
            _resolution_sort(
                next(i for i in _INCIDENTS if i["id"] == "INC-2025-002")["resolution"]
            ),
            "docs/incidents/INC-2025-002.md",
        ]
    ),
}

_RESEARCH_QUESTIONS = """\
# Cinderpeak archive — research questions (q01–q14)

Answer EVERY question below from the archive (docs/, data/). Write your
answers to `answers.json` at the workspace root, one entry per id:

```json
{"answers": [{"id": "q01", "answer": <value>, "citations": ["docs/..."]}]}
```

- `answer` is the exact value(s) the question asks for; when a question has
  several parts, answer with ONE JSON object using the part names the
  question lists.
- `citations` lists the archive files (paths relative to the workspace
  root) that carry the facts your answer depends on — cite every such file.
- Lists compare as sets (order does not matter); strings compare exactly;
  numbers compare exactly.
- The archive is the only source of truth: do not guess, do not use
  outside knowledge, do not invent paths.

## q01 — config-key lifecycle

Which release introduced the `flush_interval` config key, and which later
release renamed it (and to what)?
Answer as `{"introduced_in": ..., "renamed_in": ..., "new_name": ...}`.

## q02 — the incident a release fixed

Which incident did beacon 0.6.1 fix? Give the incident id, its severity
per docs/policies/policy-severity.md, and its duration in minutes.
Answer as `{"incident": ..., "severity": ..., "duration_min": ...}`.

## q03 — highest-volume tenant of 2025

Which tenant had the highest total event volume across 2025 (sum of all
months in data/usage-2025.csv), and what retention tier is it on
(data/tenants.csv)?
Answer as `{"tenant_id": ..., "tier": ...}`.

## q04 — retention defaults

The `retention_days` config key (spec 0.5, default 90) was superseded by
per-tier retention. In which release did that happen, and what are the
per-tier retention defaults (data/quotas.csv)?
Answer as `{"superseded_in": ...,
"tier_retention_days": {"bronze": ..., "silver": ..., "gold": ...}}`.

## q05 — the aggregation cache

Which ADR proposed the aggregation cache, which release shipped it, and
what is the `cache_max_age` default (in seconds) in spec 1.0?
Answer as `{"adr": ..., "release": ..., "cache_max_age_default_seconds": ...}`.

## q06 — the deprecation window

Which API was deprecated in beacon 0.9.0, in which release was it removed
(per docs/policies/policy-deprecation.md), and which incident during that
deprecation window mentions it?
Answer as `{"api": ..., "removed_in": ..., "incident": ...}`.

## q07 — the 2026-H1 latency spike

Which month of 2026-H1 had the highest p99 query latency for a 1.0.x
version (data/latency.csv), what was that p99 in ms, and which incident
explains it?
Answer as `{"month": ..., "p99_ms": ..., "incident": ...}`.

## q08 — ADR authorship

Who authored ADR-004, which team and role are they on (docs/team.md), and
which release shipped the ADR's decision?
Answer as `{"author": ..., "team": ..., "role": ..., "release": ...}`.

## q09 — an adoption milestone

In which month did beacon 1.0.0 installs first exceed 500
(data/adoption.csv), and what was the TOTAL installs across all versions
that month?
Answer as `{"month": ..., "total_installs": ...}`.

## q10 — a cross-incident reference

INC-2026-002's root-cause analysis cites an earlier incident as the same
failure class. Which one, and which config key did that earlier incident's
fix release introduce (with its default, in seconds)?
Answer as `{"earlier_incident": ..., "config_key": ..., "default_seconds": ...}`.

## q11 — meeting-to-release trace

Which release shipped the feature agreed in the 2025-08-29 eng weekly, and
which ADR records its design?
Answer as `{"release": ..., "adr": ...}`.

## q12 — gold-tier volume

List the gold-tier tenants (data/tenants.csv) and their combined total
event volume across 2025 (data/usage-2025.csv).
Answer as `{"gold_tenants": [...], "combined_2025_events": ...}`.

## q13 — a runbook's config key

Which config key does docs/runbooks/rb-tls-rotation.md reference for the
ingest listener certificate, and since which release does that key exist?
Answer as `{"config_key": ..., "introduced_in": ...}`.

## q14 — roadmap delivery

Which 2026 roadmap theme (docs/roadmap.md) had shipped by 2026-06, in
which release, and which ADR is its design record?
Answer as `{"theme": ..., "release": ..., "adr": ...}`.
"""

_RESEARCH_TASK = """\
# Cinderpeak engineering archive — research task

You are handed the engineering archive of Cinderpeak Analytics Ltd
(docs/, data/) — the company's internal documentation for its self-hosted
web-analytics product "Beacon". The archive is internally consistent:
every fact you need is in it, cross-references are by relative path, and
there is no network access (none is needed).

Answer the questions in QUESTIONS.md. Each answer must be derived from
the archive and must cite the archive files it depends on.

Write your answers to `answers.json` at the workspace root, in this exact
format:

```json
{
  "answers": [
    {"id": "q01", "answer": <value>, "citations": ["docs/glossary.md"]},
    ...
  ]
}
```

Rules:

- One entry per question id in QUESTIONS.md, exactly those ids.
- `answer` is the exact value(s) the question asks for — a structured
  JSON object using the part names the question lists when it has
  several parts.
- `citations` lists the archive files (paths relative to the workspace
  root) that carry the facts your answer depends on. Cite every such
  file; every cited path must exist.
- Lists compare as sets (order does not matter); strings compare
  exactly; numbers compare exactly.
- The archive is the only source of truth: do not guess, do not use
  outside knowledge, do not invent paths.

Deliverable: `answers.json` at the workspace root. Nothing else is graded.
"""

_RESEARCH_CHANGE_NOTE = """\
# Requirement change — the quarterly audit addendum

The audit review just landed three changes on the research deliverable.
answers.json keeps its format (ids, answers, citations); apply these on
top of whatever you have already produced.

1. q07 is WITHDRAWN. The 2026-02 and 2026-03 rows of data/latency.csv are
   under investigation (the probe firmware was misconfigured for those
   months), so the question is no longer graded. If you already answered
   it, leave the entry in place — it is not graded either way.

2. Two audit questions are ADDED (answer them into the same answers.json,
   ids q15 and q16):

   - q15 — appliance bundle. docs/changelogs/appliance-2.0.md ships which
     beacon server version? Per docs/policies/policy-support.md, which
     appliance MAJOR versions were supported at its release date?
     Answer as `{"beacon_version": ...,
     "supported_appliance_majors": [...]}`.

   - q16 — incident components. Which component had the most incidents in
     2024, and which in 2025 (data/incidents.csv)? Which release fixed
     the highest-severity incident of each year?
     Answer as `{"most_incidents_2024": ..., "most_incidents_2025": ...,
     "highest_severity_2024": ..., "fix_2024": ...,
     "highest_severity_2025": ..., "fix_2025": ...}`.

3. Every answer must now carry a `quote` field: ONE verbatim line from one
   of that answer's cited files that carries a fact the answer depends on
   (whitespace-normalized exact match; a full CSV row counts as a line).
   The audit grades quotes on q01, q03, q09, q15 and q16.
"""


def _json_literal(payload: Any) -> str:
    """A bulletproof Python string literal carrying JSON (double-escaped)."""
    return json.dumps(json.dumps(payload, sort_keys=True))


_RESEARCH_GRADER_STATIC = '''\
"""Hidden acceptance — the Cinderpeak research answer key (POST-HOC ONLY).

This file is NEVER part of a run workspace: the bench copies it into a
fresh copy of the workspace after the run and grades answers.json against
the key below. Grading is objective: exact values (lists as sets), every
required citation cited, every cited path real.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
KEY = json.loads(__KEY_JSON__)


def _load():
    path = ROOT / "answers.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("answers"), list):
        return {}
    out = {}
    for entry in data["answers"]:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            out[entry["id"]] = entry
    return out


ANSWERS = _load()


def _norm(path):
    text = str(path).strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _canon(value):
    if isinstance(value, list):
        return sorted(
            (_canon(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    if isinstance(value, dict):
        return {key: _canon(item) for key, item in value.items()}
    if isinstance(value, str):
        return value.strip()
    return value


@pytest.mark.parametrize("qid", sorted(KEY))
def test_answer(qid):
    entry = ANSWERS.get(qid)
    assert entry is not None, f"{qid}: answers.json has no entry with this id"
    expected = KEY[qid]
    assert _canon(entry.get("answer")) == _canon(expected["answer"]), (
        f"{qid}: answer mismatch — expected the exact value(s) the archive gives"
    )
    cited = {_norm(c) for c in entry.get("citations", []) if isinstance(c, str)}
    for rel in cited:
        assert (ROOT / rel).is_file(), f"{qid}: cited path does not exist: {rel}"
    required = {_norm(c) for c in expected["citations"]}
    missing = sorted(required - cited)
    assert not missing, f"{qid}: missing required citations: {missing}"
'''

_RESEARCH_CHANGE_GRADER_STATIC = '''\
"""Hidden acceptance — the Cinderpeak AUDIT addendum (POST-HOC ONLY).

Grades the two audit questions (q15, q16) and the quote requirement on the
five audited ids. Never part of a run workspace; the bench copies this in
post-hoc. Objective only: exact values, required citations, verbatim
(whitespace-normalized) quotes from cited files.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
CHANGE_KEY = json.loads(__CHANGE_KEY_JSON__)
QUOTE_KEY = json.loads(__QUOTE_KEY_JSON__)


def _load():
    path = ROOT / "answers.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("answers"), list):
        return {}
    out = {}
    for entry in data["answers"]:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            out[entry["id"]] = entry
    return out


ANSWERS = _load()


def _norm(path):
    text = str(path).strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _collapse(text):
    return re.sub(r"\\s+", " ", str(text)).strip()


def _canon(value):
    if isinstance(value, list):
        return sorted(
            (_canon(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    if isinstance(value, dict):
        return {key: _canon(item) for key, item in value.items()}
    if isinstance(value, str):
        return value.strip()
    return value


@pytest.mark.parametrize("qid", sorted(CHANGE_KEY))
def test_audit_answer(qid):
    entry = ANSWERS.get(qid)
    assert entry is not None, f"{qid}: answers.json has no entry with this id"
    expected = CHANGE_KEY[qid]
    assert _canon(entry.get("answer")) == _canon(expected["answer"]), (
        f"{qid}: audit answer mismatch — expected the exact value(s) the archive gives"
    )
    cited = {_norm(c) for c in entry.get("citations", []) if isinstance(c, str)}
    for rel in cited:
        assert (ROOT / rel).is_file(), f"{qid}: cited path does not exist: {rel}"
    required = {_norm(c) for c in expected["citations"]}
    missing = sorted(required - cited)
    assert not missing, f"{qid}: missing required citations: {missing}"


@pytest.mark.parametrize("qid", sorted(QUOTE_KEY))
def test_quote(qid):
    entry = ANSWERS.get(qid)
    assert entry is not None, f"{qid}: answers.json has no entry with this id"
    quote = entry.get("quote")
    assert isinstance(quote, str) and quote.strip(), (
        f"{qid}: the audit requires a non-empty 'quote' field"
    )
    cited = [_norm(c) for c in entry.get("citations", []) if isinstance(c, str)]
    assert cited, f"{qid}: no citations to check the quote against"
    needle = _collapse(quote)
    for rel in cited:
        path = ROOT / rel
        if path.is_file() and needle in _collapse(path.read_text(encoding="utf-8")):
            return
    pytest.fail(
        f"{qid}: the quote is not a verbatim (whitespace-normalized) line "
        "of any cited file"
    )
'''


def _research_hidden_tests() -> dict[str, str]:
    """The ORIGINAL hidden suite: the main key (q01–q14 minus the withdrawn
    q07) plus q07 in its own file (so the change can retire exactly it)."""
    main_key = {qid: entry for qid, entry in _RESEARCH_KEY.items() if qid != "q07"}
    return {
        "test_research_key.py": _RESEARCH_GRADER_STATIC.replace(
            "__KEY_JSON__", _json_literal(main_key)
        ),
        "test_q07_latency.py": _RESEARCH_GRADER_STATIC.replace(
            "__KEY_JSON__", _json_literal({"q07": _RESEARCH_KEY["q07"]})
        ),
    }


def _research_change_tests() -> dict[str, str]:
    """The CHANGE pack's extra hidden tests (the audit addendum)."""
    change_static = _RESEARCH_CHANGE_GRADER_STATIC.replace(
        "__CHANGE_KEY_JSON__", _json_literal(_RESEARCH_CHANGE_KEY)
    ).replace("__QUOTE_KEY_JSON__", _json_literal(_RESEARCH_QUOTES))
    return {"test_research_change.py": change_static}


def _research_reference() -> dict[str, str]:
    """The reference solution: answers.json with every question answered
    (the audit questions and quotes included — the reference is the FINAL
    post-change state, which is what xl_bench.verify_fixture requires)."""
    answers: list[dict[str, Any]] = []
    for qid in sorted(_RESEARCH_KEY):
        entry: dict[str, Any] = {
            "id": qid,
            "answer": _RESEARCH_KEY[qid]["answer"],
            "citations": list(_RESEARCH_KEY[qid]["citations"]),
        }
        if qid in _RESEARCH_QUOTES:
            entry["quote"] = _RESEARCH_QUOTES[qid]
        answers.append(entry)
    for qid in sorted(_RESEARCH_CHANGE_KEY):
        entry = {
            "id": qid,
            "answer": _RESEARCH_CHANGE_KEY[qid]["answer"],
            "citations": list(_RESEARCH_CHANGE_KEY[qid]["citations"]),
        }
        if qid in _RESEARCH_QUOTES:
            entry["quote"] = _RESEARCH_QUOTES[qid]
        answers.append(entry)
    return {"answers.json": json.dumps({"answers": answers}, indent=1) + "\n"}


def _research_task() -> dict[str, Any]:
    return {
        "name": "xl-research-cinderpeak",
        "kind": "research",
        "seed": _research_seed(),
        "task": _RESEARCH_TASK,
        "hidden_tests": _research_hidden_tests(),
        "change": {
            "note": _RESEARCH_CHANGE_NOTE,
            "hidden_tests": _research_change_tests(),
            "invalidates": ["test_q07_latency.py"],
        },
        "reference": _research_reference(),
        "expected_hours": 2.0,
    }


# ===========================================================================
# Part 2 — the Postbox project fixture (xl-project-postbox)
# ===========================================================================
#
# A small multi-component service (HTTP API + file storage + background
# delivery worker + CLI + docs) built FROM a product brief. The brief is
# deliberately ARCHITECTURE-NEUTRAL where the requirement change bites:
# the original hidden suite never pins whether `postbox serve` embeds a
# worker or whether stats are cached in-process — both designs pass it —
# while the CHANGE pack (operations mandates separate processes) is cheap
# for the shared-store design and expensive for the embedded/cached one.

_POSTBOX_BRIEF = """\
# Postbox — a local message outbox service (product brief)

Postbox is a small, self-hosted service that accepts outbound messages
from local applications, stores them durably, and delivers them in the
background to a local spool. Single user, one machine, fully offline.

Stack constraints: Python 3.11+ STANDARD LIBRARY ONLY (no third-party
runtime dependencies, no build step). HTTP via the standard library.
Storage on the local filesystem. Operated from the command line.

## Components you must build

1. `postbox/` — the Python package, importable from the repo root.
2. `postbox serve` — the HTTP API server (the main process).
3. The delivery worker — the background job that delivers queued messages.
4. `postbox` CLI — the operator commands (`python -m postbox ...`).
5. `docs/` — API reference + operations guide.
6. `README.md` — what it is, quickstart, config table.

## Functional requirements

### Message lifecycle

R1. A message has: id, recipient, payload, priority, status, created_at,
    attempts, next_attempt_at, delivered_at. ids are `msg_` + exactly 16
    lowercase hex chars, unique forever.
R2. Statuses: queued -> delivering -> delivered | failed | expired.
    `delivering` is transient (only during a delivery attempt).
R3. Enqueue via API: `POST /v1/messages` with JSON
    `{"recipient": ..., "payload": ..., "priority": ...}` ->
    `201` with `{"id": ..., "status": "queued", "created_at": ...}`.
    `priority` defaults to `"normal"`.
R4. Validation (all failures are `400` with
    `{"error": {"code": "invalid_message", "message": ...}}`):
    recipient is a non-empty string, at most 200 chars; payload is a
    string, at most 10_000 chars; priority is one of
    `low|normal|high`; unknown request fields are rejected.
R5. Idempotency: an enqueue carrying `"idempotency_key"` (string, at
    most 100 chars) returns the ORIGINAL message for that key
    (`200`, not `201`, with an `Idempotent-Replay: true` response
    header). The same key with a DIFFERENT recipient/payload/priority
    is `400` `{"error": {"code": "idempotency_conflict", ...}}`.
R6. `GET /v1/messages/{id}` -> `200` with the full message JSON, or
    `404` `{"error": {"code": "not_found", ...}}`.
R7. `GET /v1/stats` -> `200` with integer counts:
    `{"queued": ..., "delivering": ..., "delivered": ..., "failed": ...,
    "expired": ...}`.
R8. `POST /v1/messages/{id}/retry`: a `failed` or `expired` message goes
    back to `queued` (attempts reset to 0, next_attempt_at = now) ->
    `200` with the message; a `queued` or `delivered` message -> `409`
    `{"error": {"code": "not_retryable", ...}}`; unknown id -> `404`.
R9. `GET /healthz` -> `200` `{"status": "ok"}`.

### Delivery (the worker)

R10. The worker picks up queued messages with next_attempt_at <= now,
    oldest first (by created_at), one at a time.
R11. Delivery = writing the message as JSON to
    `<home>/spool/<recipient>/<id>.json` (the "mailbox"), containing
    exactly `{"id", "recipient", "payload", "priority", "delivered_at"}`.
    The write must be atomic (tmp file + rename).
R12. A recipient starting with `poison:` NEVER delivers (simulated
    permanent failure): the attempt fails, attempts += 1,
    next_attempt_at = now + backoff, status stays queued until
    attempts reaches max_attempts (3), then status = `failed`.
    Backoff: `backoff_base * 2**(attempts-1)` seconds after the failed
    attempt (attempts counted AFTER the increment).
R13. On success: status = `delivered`, delivered_at = ISO-8601 UTC.
R14. Expiry: a queued message older than `ttl_seconds` is set to
    `expired` by the worker (never delivered, even if eligible).
R15. The worker polls every `poll_interval` seconds and must not
    busy-loop when there is no work.

### Storage

R16. All state lives under the home directory (see R24). Contractual
    layout: `<home>/messages/<id>.json` — one file per message, holding
    the full message JSON. Everything else under `<home>/` is yours to
    design.
R17. Storage must be durable across restarts and correct when the API
    and the worker run CONCURRENTLY against the same home.
R18. The API must never lose an acknowledged message: a `201` response
    means the message file is on disk.

### CLI (`python -m postbox ...`)

R19. `postbox send --recipient R [--payload TEXT | --payload-file F]
    [--priority P] [--key K]` -> prints the new message id.
R20. `postbox status <id>` -> prints the message JSON.
R21. `postbox stats` -> prints the stats JSON.
R22. `postbox retry <id>` -> requeues a failed/expired message (prints
    it); a non-retryable or unknown id exits non-zero.
R23. `postbox worker` -> runs the delivery worker loop (no API).

### Config

R24. Home resolution: `--home` flag > `POSTBOX_HOME` env var >
    default `~/.postbox`. Worker knobs come from
    `<home>/postbox.json` (a JSON object) when present, else defaults:
    `backoff_base` (2.0), `poll_interval` (0.5), `ttl_seconds` (3600),
    `max_attempts` (3). A malformed or missing file means defaults.
R25. `postbox serve [--home H] [--port P]`: the port comes from
    `--port`, else `POSTBOX_PORT`, else 8733. The API binds 127.0.0.1.

### Docs

R26. `README.md`: what it is, quickstart (serve + worker + CLI), the
    full config table.
R27. `docs/API.md`: every endpoint with request/response examples.
    `docs/OPERATIONS.md`: running the worker, the spool layout,
    retry/expiry semantics, failure modes (poison recipients, quotas
    of nothing), and how the API and the worker share the store.

## Non-requirements

- No real network delivery (the spool IS the delivery).
- No auth (single user, local).
- No horizontal scale-out: one machine, one operator.
"""

_POSTBOX_TASK = """\
# Postbox — build the service

Build Postbox to the product brief in BRIEF.md (this repo's only file):
a local message outbox service — HTTP API + durable file storage +
background delivery worker + operator CLI + docs. Python 3.11+
standard library ONLY (no third-party runtime dependencies, no build
step); the package must be importable as `postbox` from the repo root
and runnable as `python -m postbox ...`.

Read BRIEF.md carefully first: the numbered requirements R1–R27 are the
contract (validation rules, status machine, retry/backoff/expiry
semantics, the spool and message-file layouts, CLI commands, config
resolution, docs). Build the whole thing: the API server, the delivery
worker, the CLI, and the docs (README.md, docs/API.md,
docs/OPERATIONS.md).

Quality bar: the service must be durable across restarts, correct when
the API and the worker touch the same store, and free of busy-loops.
Keep the code small and readable — this is a service one person
operates, not a framework.
"""

_POSTBOX_CHANGE_NOTE = """\
# Requirement change — operations splits the processes

Operations just landed a change on Postbox (this supersedes part of the
original brief):

1. `postbox serve` is the API ONLY. It must NOT start, embed, or
   supervise any delivery worker — no worker thread, no worker
   subprocess from the serve command. A serve process that delivers
   messages by itself is now a defect.
2. `postbox worker` (already in the brief) is THE delivery process:
   it runs standalone against the same home as a running or stopped
   API.
3. The API must keep serving (healthz, enqueue, read, stats) while the
   worker is STOPPED, and the worker must pick up every message
   enqueued while it was down — the store is the only shared state.
4. `GET /v1/stats` must be correct with the worker running as a
   SEPARATE process: after the worker delivers, the API's stats must
   show the delivered count without any restart.

Nothing else in the brief changes (validation, statuses, retry/backoff/
expiry, spool layout, CLI, config resolution all stay as written).
Update whatever the change makes wrong, and keep the docs truthful.
"""


# ---------------------------------------------------------------------------
# The Postbox REFERENCE solution (kept OUTSIDE the workspace; the bench
# overlays it on the seed to prove pass-when-solved). This is the FINAL
# post-change state: serve = API only (no embedded worker), the worker is
# a separate process, stats scan the store per request (correct across
# processes). Stdlib only.
# ---------------------------------------------------------------------------

_POSTBOX_REFERENCE: dict[str, str] = {}

_POSTBOX_REFERENCE["postbox/__init__.py"] = '''\
"""Postbox — a local message outbox service (stdlib only).

Components: an HTTP API (`postbox serve`), a delivery worker
(`postbox worker`, a SEPARATE process), a file store under the home
directory, and an operator CLI (`python -m postbox ...`).
"""

__version__ = "1.1.0"
'''

_POSTBOX_REFERENCE["postbox/config.py"] = '''\
"""Config resolution: --home flag > POSTBOX_HOME env > default ~/.postbox.

Worker knobs come from <home>/postbox.json when present (a JSON object);
a malformed or missing file means defaults. The port comes from --port,
else POSTBOX_PORT, else 8733.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 8733


@dataclass
class Config:
    """The worker knobs (R24) plus the resolved home."""

    home: Path
    backoff_base: float = 2.0
    poll_interval: float = 0.5
    ttl_seconds: float = 3600.0
    max_attempts: int = 3


def resolve_home(flag: str | None) -> Path:
    if flag:
        return Path(flag).expanduser()
    env = os.environ.get("POSTBOX_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".postbox"


def resolve_port(flag: int | None) -> int:
    if flag is not None:
        return int(flag)
    env = os.environ.get("POSTBOX_PORT")
    if env and env.strip().isdigit():
        return int(env)
    return DEFAULT_PORT


def load_config(home: Path) -> Config:
    config = Config(home=Path(home))
    path = Path(home) / "postbox.json"
    if not path.is_file():
        return config
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return config
    if not isinstance(data, dict):
        return config
    for key in ("backoff_base", "poll_interval", "ttl_seconds", "max_attempts"):
        if key not in data:
            continue
        try:
            value = type(getattr(config, key))(data[key])
        except (TypeError, ValueError):
            continue
        setattr(config, key, value)
    return config
'''

_POSTBOX_REFERENCE["postbox/store.py"] = '''\
"""The Postbox store: one JSON file per message under <home>/messages/,
plus the idempotency index under <home>/idempotency/.

All writes are atomic (tmp file + os.replace). Reads are PER-OPERATION —
there is deliberately no in-process cache, because the API and the worker
are separate processes sharing this store (docs/OPERATIONS.md): a cached
read would go stale the moment the other process writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUSES = ("queued", "delivering", "delivered", "failed", "expired")


def new_id() -> str:
    """`msg_` + exactly 16 lowercase hex chars (R1)."""
    return "msg_" + secrets.token_hex(8)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Store:
    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.messages = self.home / "messages"
        self.idempotency = self.home / "idempotency"
        self.spool = self.home / "spool"
        self.messages.mkdir(parents=True, exist_ok=True)
        self.idempotency.mkdir(parents=True, exist_ok=True)

    # -- messages ---------------------------------------------------------

    def _path(self, mid: str) -> Path:
        return self.messages / f"{mid}.json"

    def write_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def put(self, message: dict[str, Any]) -> None:
        self.write_atomic(self._path(message["id"]), message)

    def get(self, mid: str) -> dict[str, Any] | None:
        path = self._path(mid)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def all_messages(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self.messages.glob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def stats(self) -> dict[str, int]:
        """Counts per status, computed from the store on EVERY call (R7) —
        never cached, so a separately-running worker's writes are visible."""
        counts = {status: 0 for status in STATUSES}
        for message in self.all_messages():
            status = message.get("status")
            if status in counts:
                counts[status] += 1
        return counts

    # -- idempotency ------------------------------------------------------

    def _idem_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.idempotency / f"{digest}.json"

    def idem_get(self, key: str) -> dict[str, Any] | None:
        path = self._idem_path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def idem_claim(self, key: str, mid: str) -> bool:
        """Atomically claim an idempotency key; False if it is taken."""
        path = self._idem_path(key)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"key": key, "message_id": mid}, handle, indent=1)
        return True
'''

_POSTBOX_REFERENCE["postbox/service.py"] = '''\
"""The API logic layer: enqueue / read / stats / retry over the store."""

from __future__ import annotations

from typing import Any

from postbox.store import Store, new_id, now_iso

PRIORITIES = ("low", "normal", "high")
MAX_RECIPIENT = 200
MAX_PAYLOAD = 10_000
MAX_KEY = 100
#: A recipient is also a spool PATH COMPONENT (R11) — these would escape it.
_FORBIDDEN = ("/", "\\x00", "\\\\")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _validate(recipient: Any, payload: Any, priority: Any) -> tuple[str, str, str]:
    if not isinstance(recipient, str) or not recipient.strip() or len(recipient) > MAX_RECIPIENT:
        raise ApiError(
            400, "invalid_message", "recipient must be a non-empty string of at most 200 chars"
        )
    if any(chunk in recipient for chunk in _FORBIDDEN) or recipient in (".", ".."):
        raise ApiError(400, "invalid_message", "recipient must not contain path separators")
    if not isinstance(payload, str) or len(payload) > MAX_PAYLOAD:
        raise ApiError(400, "invalid_message", "payload must be a string of at most 10000 chars")
    if priority not in PRIORITIES:
        raise ApiError(400, "invalid_message", f"priority must be one of {list(PRIORITIES)}")
    return recipient, payload, priority


def enqueue(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    """Returns {"replay": bool, "message": dict} (R3, R5, R18)."""
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_message", "body must be a JSON object")
    unknown = sorted(set(body) - {"recipient", "payload", "priority", "idempotency_key"})
    if unknown:
        raise ApiError(400, "invalid_message", f"unknown field(s): {unknown}")
    recipient, payload, priority = _validate(
        body.get("recipient"), body.get("payload"), body.get("priority", "normal")
    )
    key = body.get("idempotency_key")
    if key is not None and (
        not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY
    ):
        raise ApiError(
            400, "invalid_message", "idempotency_key must be a string of at most 100 chars"
        )

    def _replay_or_conflict(prior: dict[str, Any]) -> dict[str, Any]:
        triple = (prior["recipient"], prior["payload"], prior["priority"])
        if triple != (recipient, payload, priority):
            raise ApiError(400, "idempotency_conflict", "the key was used with a different message")
        return {"replay": True, "message": prior}

    if key is not None:
        existing = store.idem_get(key)
        if existing is not None:
            prior = store.get(existing["message_id"])
            if prior is not None:
                return _replay_or_conflict(prior)

    message = {
        "id": new_id(),
        "recipient": recipient,
        "payload": payload,
        "priority": priority,
        "status": "queued",
        "created_at": now_iso(),
        "attempts": 0,
        "next_attempt_at": now_iso(),
        "delivered_at": None,
        "idempotency_key": key,
    }
    store.put(message)  # R18: on disk BEFORE the 201 goes out
    if key is not None and not store.idem_claim(key, message["id"]):
        # Another writer claimed the key concurrently: their message wins.
        existing = store.idem_get(key)
        if existing is not None:
            prior = store.get(existing["message_id"])
            if prior is not None:
                return _replay_or_conflict(prior)
    return {"replay": False, "message": message}


def get_message(store: Store, mid: str) -> dict[str, Any] | None:
    return store.get(mid)


def stats(store: Store) -> dict[str, int]:
    return store.stats()


def retry(store: Store, mid: str) -> dict[str, Any]:
    message = store.get(mid)
    if message is None:
        raise ApiError(404, "not_found", f"no such message: {mid}")
    if message.get("status") not in ("failed", "expired"):
        raise ApiError(409, "not_retryable", f"status {message.get('status')} is not retryable")
    message["status"] = "queued"
    message["attempts"] = 0
    message["next_attempt_at"] = now_iso()
    message["delivered_at"] = None
    store.put(message)
    return message
'''

_POSTBOX_REFERENCE["postbox/delivery.py"] = '''\
"""The delivery worker (R10–R15): a STANDALONE loop (`postbox worker`).

There is deliberately no server-side embedding: the API and the worker
are separate processes sharing the store (docs/OPERATIONS.md). The loop
expires past-TTL messages, then delivers one eligible message per tick
(oldest first), sleeping poll_interval when there is no work.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from postbox.config import Config
from postbox.store import Store, now_iso, parse_ts


def _iso_after(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def deliver_spool(store: Store, message: dict[str, Any]) -> None:
    """R11: the mailbox write — <home>/spool/<recipient>/<id>.json, atomic."""
    payload = {
        "id": message["id"],
        "recipient": message["recipient"],
        "payload": message["payload"],
        "priority": message["priority"],
        "delivered_at": now_iso(),
    }
    store.write_atomic(store.spool / message["recipient"] / f"{message['id']}.json", payload)


def attempt_one(store: Store, config: Config) -> bool:
    """One worker tick: expire past-TTL, then deliver one eligible message.
    Returns True if anything was done (so the loop can continue without
    sleeping); False means "no work" (the loop sleeps poll_interval)."""
    now = datetime.now(timezone.utc)
    did = False
    for message in store.all_messages():
        if message.get("status") != "queued":
            continue
        age = (now - parse_ts(message["created_at"])).total_seconds()
        if age > config.ttl_seconds:
            message["status"] = "expired"  # R14: never delivered, even if eligible
            store.put(message)
            did = True
    eligible = sorted(
        (
            message
            for message in store.all_messages()
            if message.get("status") == "queued"
            and parse_ts(message["next_attempt_at"]) <= now
        ),
        key=lambda message: message["created_at"],  # R10: oldest first
    )
    if not eligible:
        return did
    message = eligible[0]
    if str(message.get("recipient", "")).startswith("poison:"):
        # R12: simulated permanent failure, backoff, failed at max_attempts.
        message["attempts"] = int(message.get("attempts", 0)) + 1
        if message["attempts"] >= config.max_attempts:
            message["status"] = "failed"
        else:
            wait = config.backoff_base * (2 ** (message["attempts"] - 1))
            message["next_attempt_at"] = _iso_after(wait)
        store.put(message)
    else:
        deliver_spool(store, message)
        message["status"] = "delivered"  # R13
        message["delivered_at"] = now_iso()
        store.put(message)
    return True


def run_worker(store: Store, config: Config) -> None:
    """The worker loop (R15): never busy-loops, runs until terminated."""
    try:
        while True:
            if not attempt_one(store, config):
                time.sleep(config.poll_interval)
    except KeyboardInterrupt:
        return
'''

_POSTBOX_REFERENCE["postbox/server.py"] = '''\
"""The Postbox HTTP API (stdlib ThreadingHTTPServer).

`postbox serve` is the API ONLY — no worker thread, no worker subprocess
(the worker is a separate process; see docs/OPERATIONS.md). Stats are
computed from the store per request, so a separately-running worker's
writes are visible immediately.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from postbox import service
from postbox.store import Store

_ID = r"msg_[0-9a-f]{16}"


def _error(code: str, message: str) -> dict[str, str]:
    return {"error": {"code": code, "message": message}}


class _Handler(BaseHTTPRequestHandler):
    server_version = "postbox/1.1"
    protocol_version = "HTTP/1.1"
    store: Store  # bound by create_server

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # a single-user local service; keep the console quiet

    def _send(
        self, status: int, payload: dict[str, Any] | None, headers: dict[str, str] | None = None
    ) -> None:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b"{}"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def do_GET(self) -> None:  # noqa: N802 — stdlib naming
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send(200, {"status": "ok"})  # R9
            return
        if path == "/v1/stats":
            self._send(200, service.stats(self.store))  # R7
            return
        match = re.fullmatch(f"/v1/messages/({_ID})", path)
        if match:
            message = service.get_message(self.store, match.group(1))
            if message is None:
                self._send(404, _error("not_found", "no such message"))  # R6
            else:
                self._send(200, message)
            return
        self._send(404, _error("not_found", "no such route"))

    def do_POST(self) -> None:  # noqa: N802 — stdlib naming
        path = self.path.split("?", 1)[0]
        match = re.fullmatch(f"/v1/messages/({_ID})/retry", path)
        if match:
            try:
                message = service.retry(self.store, match.group(1))  # R8
            except service.ApiError as err:
                self._send(err.status, _error(err.code, err.message))
                return
            self._send(200, message)
            return
        if path == "/v1/messages":
            body = self._read_body()
            if body is None:
                self._send(400, _error("invalid_message", "body must be a JSON object"))
                return
            try:
                result = service.enqueue(self.store, body)  # R3–R5
            except service.ApiError as err:
                self._send(err.status, _error(err.code, err.message))
                return
            if result["replay"]:
                self._send(200, result["message"], {"Idempotent-Replay": "true"})
            else:
                message = result["message"]
                self._send(
                    201,
                    {
                        "id": message["id"],
                        "status": message["status"],
                        "created_at": message["created_at"],
                    },
                )
            return
        self._send(404, _error("not_found", "no such route"))


def create_server(store: Store, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (_Handler,), {"store": store})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    return httpd


def serve(store: Store, port: int) -> None:
    httpd = create_server(store, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
'''

_POSTBOX_REFERENCE["postbox/__main__.py"] = '''\
"""The Postbox CLI: serve | worker | send | status | stats | retry (R19–R25)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from postbox import service
from postbox.config import load_config, resolve_home, resolve_port
from postbox.delivery import run_worker
from postbox.server import serve
from postbox.store import Store


def _add_home(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", default=None, help="home dir (flag > POSTBOX_HOME env)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="postbox", description="A local message outbox service.")
    sub = parser.add_subparsers(dest="command", metavar="command", required=True)

    serve_parser = sub.add_parser("serve", help="run the HTTP API (no worker)")
    _add_home(serve_parser)
    serve_parser.add_argument("--port", type=int, default=None, help="listen port (default 8733)")

    worker_parser = sub.add_parser("worker", help="run the delivery worker loop (no API)")
    _add_home(worker_parser)

    send_parser = sub.add_parser("send", help="enqueue a message, print its id")
    send_parser.add_argument("--recipient", required=True)
    send_parser.add_argument("--payload", default=None, help="the message payload text")
    send_parser.add_argument("--payload-file", default=None, help="read the payload from a file")
    send_parser.add_argument("--priority", default="normal", choices=("low", "normal", "high"))
    send_parser.add_argument("--key", default=None, help="idempotency key")
    _add_home(send_parser)

    status_parser = sub.add_parser("status", help="print a message as JSON")
    status_parser.add_argument("id")
    _add_home(status_parser)

    stats_parser = sub.add_parser("stats", help="print the stats JSON")
    _add_home(stats_parser)

    retry_parser = sub.add_parser("retry", help="requeue a failed/expired message")
    retry_parser.add_argument("id")
    _add_home(retry_parser)

    args = parser.parse_args(argv)
    home = resolve_home(args.home)
    store = Store(home)

    if args.command == "serve":
        config = load_config(home)
        serve(store, resolve_port(args.port))
        return 0

    if args.command == "worker":
        config = load_config(home)
        run_worker(store, config)
        return 0

    if args.command == "send":
        if args.payload is None and args.payload_file is None:
            print("one of --payload / --payload-file is required", file=sys.stderr)
            return 2
        if args.payload is not None and args.payload_file is not None:
            print("use either --payload or --payload-file, not both", file=sys.stderr)
            return 2
        if args.payload_file is not None:
            try:
                payload = Path(args.payload_file).read_text(encoding="utf-8")
            except OSError as err:
                print(f"cannot read {args.payload_file}: {err}", file=sys.stderr)
                return 2
        else:
            payload = args.payload
        body = {"recipient": args.recipient, "payload": payload, "priority": args.priority}
        if args.key:
            body["idempotency_key"] = args.key
        try:
            result = service.enqueue(store, body)
        except service.ApiError as err:
            print(f"{err.code}: {err.message}", file=sys.stderr)
            return 1
        print(result["message"]["id"])
        return 0

    if args.command == "status":
        message = store.get(args.id)
        if message is None:
            print(f"no such message: {args.id}", file=sys.stderr)
            return 1
        print(json.dumps(message, indent=2))
        return 0

    if args.command == "stats":
        print(json.dumps(store.stats(), indent=2))
        return 0

    if args.command == "retry":
        try:
            message = service.retry(store, args.id)
        except service.ApiError as err:
            print(f"{err.code}: {err.message}", file=sys.stderr)
            return 1
        print(json.dumps(message, indent=2))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''

_POSTBOX_REFERENCE["README.md"] = """\
# Postbox

A local message outbox service: applications POST messages to the API,
Postbox stores them durably under a home directory, and a delivery
worker writes them to a per-recipient spool in the background. Single
user, one machine, fully offline. Python 3.11+ standard library only.

## Quickstart

```bash
# terminal 1 — the API (no worker inside)
python -m postbox serve --home ./postbox-data --port 8733

# terminal 2 — the delivery worker (its own process)
python -m postbox worker --home ./postbox-data

# send / inspect from anywhere
python -m postbox send --home ./postbox-data --recipient ops@example --payload "hello"
python -m postbox status --home ./postbox-data msg_0123456789abcdef
python -m postbox stats  --home ./postbox-data
python -m postbox retry  --home ./postbox-data msg_0123456789abcdef
```

The API and the worker are SEPARATE processes sharing the store; the API
keeps serving while the worker is stopped, and the worker picks up
everything enqueued while it was down.

## Layout

```
<home>/messages/<id>.json     one file per message (the full message JSON)
<home>/idempotency/<sha>.json idempotency-key index
<home>/spool/<recipient>/<id>.json   the delivered "mailbox" files
<home>/postbox.json           optional worker knobs
```

## Config

| Knob | Default | Meaning |
|------|---------|---------|
| `backoff_base` | 2.0 | seconds; retry backoff is `backoff_base * 2**(attempts-1)` |
| `poll_interval` | 0.5 | worker sleep between polls (seconds) |
| `ttl_seconds` | 3600 | a queued message older than this is expired |
| `max_attempts` | 3 | failed attempts before a poison delivery gives up |

Knobs come from `<home>/postbox.json` (a JSON object); a malformed or
missing file means defaults. Home resolution: `--home` flag >
`POSTBOX_HOME` env > `~/.postbox`. Port: `--port` > `POSTBOX_PORT` >
8733 (binds 127.0.0.1).

See docs/API.md for the endpoints and docs/OPERATIONS.md for running it.
"""

_POSTBOX_REFERENCE["docs/API.md"] = """\
# Postbox API reference

The API binds 127.0.0.1 and speaks JSON. Errors are always
`{"error": {"code": ..., "message": ...}}`.

## GET /healthz

`200 {"status": "ok"}`

## POST /v1/messages

Enqueue. Body: `{"recipient": str, "payload": str, "priority"?: "low|normal|high",
"idempotency_key"?: str}`. Unknown fields are rejected.

- `201` `{"id": "msg_...", "status": "queued", "created_at": "..."}`
- `400` `invalid_message` — recipient not a non-empty string <= 200 chars,
  payload not a string <= 10 000 chars, priority not low/normal/high,
  an unknown field, or a bad idempotency_key (string <= 100 chars).
  A recipient is also a spool path component, so path separators are
  rejected.
- With `idempotency_key`: the first request stores the key; a replay
  with the SAME recipient/payload/priority returns the original message
  as `200` with an `Idempotent-Replay: true` header; a replay with a
  DIFFERENT message is `400` `idempotency_conflict`.

```bash
curl -s -X POST localhost:8733/v1/messages \\
  -H 'content-type: application/json' \\
  -d '{"recipient": "ops@example", "payload": "hello", "priority": "high"}'
# -> {"id": "msg_9f2c...", "status": "queued", "created_at": "2026-10-03T09:00:00+00:00"}
```

## GET /v1/messages/{id}

`200` with the full message JSON
(`id, recipient, payload, priority, status, created_at, attempts,
next_attempt_at, delivered_at, idempotency_key`), or `404 not_found`.

## GET /v1/stats

`200` with integer counts:
`{"queued": ..., "delivering": ..., "delivered": ..., "failed": ..., "expired": ...}`.
Counts are computed from the store on every request — a separately
running worker's deliveries are visible immediately.

## POST /v1/messages/{id}/retry

A `failed` or `expired` message goes back to `queued` (attempts reset to
0, next_attempt_at = now) -> `200` with the message. A `queued` or
`delivered` message -> `409` `not_retryable`. Unknown id -> `404`.
"""

_POSTBOX_REFERENCE["docs/OPERATIONS.md"] = """\
# Postbox operations guide

## Processes

Postbox is TWO processes sharing one store (this is the supported
topology — one API process plus one worker process):

- `postbox serve` — the HTTP API ONLY. It never starts, embeds, or
  supervises a worker: no worker thread, no worker subprocess. A serve
  process that delivers messages by itself is a defect.
- `postbox worker` — the delivery loop, standalone. Run it against the
  same `--home` as the API; it may run while the API is up or down.

The API keeps serving (healthz, enqueue, read, stats) while the worker is
stopped, and the worker picks up every message enqueued while it was
down — the store is the only shared state. `GET /v1/stats` is computed
from the store per request, so worker deliveries show up without any
restart.

## The delivery loop

Every `poll_interval` seconds (and immediately after each delivery):

1. Expiry: a queued message older than `ttl_seconds` becomes `expired`
   (never delivered, even if it was eligible).
2. Delivery: the oldest eligible queued message
   (`next_attempt_at <= now`, oldest `created_at` first) is delivered:
   its JSON is written atomically (tmp + rename) to
   `<home>/spool/<recipient>/<id>.json` with exactly
   `{"id", "recipient", "payload", "priority", "delivered_at"}`, then the
   message becomes `delivered` with `delivered_at` set (ISO-8601 UTC).

## Failure modes

- A recipient starting with `poison:` never delivers (simulated
  permanent failure): each attempt fails, `attempts` increments, and the
  next attempt waits `backoff_base * 2**(attempts-1)` seconds; after
  `max_attempts` (default 3) the message becomes `failed`. `postbox
  retry <id>` (or `POST /v1/messages/{id}/retry`) requeues it.
- A recipient is a spool path component: path separators are rejected
  at enqueue time (400), so a recipient can never escape its spool dir.
- A crash between the spool write and the status write re-delivers on
  restart (the spool write is idempotent — same id, same file).
- A malformed `<home>/postbox.json` means defaults, never a crash.

## Durability

Every message write is atomic (tmp file + rename) and the 201 response
goes out only after the message file is on disk — an acknowledged
message is never lost. The API and the worker both read per operation,
so neither process ever serves stale state.
"""


# ---------------------------------------------------------------------------
# The Postbox HIDDEN acceptance suite (post-hoc only, never in the seed).
# Design-neutral where the change bites: the original files below never pin
# whether serve embeds a worker or whether stats are cached — both designs
# pass them; the CHANGE pack (test_change_separation.py) is what punishes
# the embedded/cached choices.
# ---------------------------------------------------------------------------

_POSTBOX_TEST_HELPERS = """\
# Shared helpers for the Postbox hidden acceptance suite (post-hoc only).
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _req(base, method, path, body=None, headers=None):
    # (status, headers, parsed-json) for one API request.
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, dict(resp.headers), json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"raw": raw}
        return err.code, dict(err.headers), parsed


class Serve:
    # A `postbox serve` subprocess bound to a temp home (context manager).
    def __init__(self, home, config=None):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        if config is not None:
            (self.home / "postbox.json").write_text(json.dumps(config), encoding="utf-8")
        self.port = _free_port()
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [PY, "-m", "postbox", "serve", "--home", str(self.home), "--port", str(self.port)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("serve exited early: " + (self.proc.stdout.read() or ""))
            try:
                status, _headers, _body = _req(self.base, "GET", "/healthz")
                if status == 200:
                    return self
            except Exception:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("serve did not become healthy in 20s")

    def __exit__(self, *exc):
        self.stop()
        return False

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


class Worker:
    # A `postbox worker` subprocess. start()/stop() are explicit so a test
    # can keep the worker DOWN while it enqueues (the change's core shape).
    def __init__(self, home):
        self.home = Path(home)
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(
            [PY, "-m", "postbox", "worker", "--home", str(self.home)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return self

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


def _wait_for(check, timeout=15.0, interval=0.05):
    # Poll until check() returns truthy; returns the last value seen.
    deadline = time.time() + timeout
    value = None
    while True:
        try:
            value = check()
        except Exception:
            value = None
        if value:
            return value
        if time.time() >= deadline:
            return value
        time.sleep(interval)


def _enqueue(base, recipient="ops@example", payload="hello", priority=None, key=None, expect=201):
    body = {"recipient": recipient, "payload": payload}
    if priority is not None:
        body["priority"] = priority
    if key is not None:
        body["idempotency_key"] = key
    status, headers, parsed = _req(base, "POST", "/v1/messages", body)
    assert status == expect, f"enqueue expected {expect}, got {status}: {parsed}"
    return status, headers, parsed


def _status_of(api, mid):
    status, _headers, body = _req(api.base, "GET", f"/v1/messages/{mid}")
    if status != 200:
        return None
    return body.get("status")


def _delivered(api, mid):
    return _status_of(api, mid) == "delivered"


def _failed(api, mid):
    return _status_of(api, mid) == "failed"


def _spool_file(home, recipient, mid):
    return Path(home) / "spool" / recipient / f"{mid}.json"
"""


def _pb(tests: str) -> str:
    return _POSTBOX_TEST_HELPERS + "\n\n" + tests.strip() + "\n"


_POSTBOX_HIDDEN: dict[str, str] = {}

_POSTBOX_HIDDEN["test_api_contract.py"] = _pb("""
import re


def test_healthz(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _req(api.base, "GET", "/healthz")
        assert status == 200
        assert body == {"status": "ok"}


def test_enqueue_201_shape(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _enqueue(api.base)
        assert status == 201
        assert re.fullmatch(r"msg_[0-9a-f]{16}", body["id"])
        assert body["status"] == "queued"
        assert body["created_at"]


def test_enqueue_default_priority(tmp_path):
    with Serve(tmp_path / "home") as api:
        _status, _headers, body = _enqueue(api.base)
        _s, _h, full = _req(api.base, "GET", "/v1/messages/" + body["id"])
        assert full["priority"] == "normal"


def test_reject_missing_recipient(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _req(api.base, "POST", "/v1/messages", {"payload": "x"})
        assert status == 400
        assert body["error"]["code"] == "invalid_message"


def test_reject_empty_recipient(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _req(
            api.base, "POST", "/v1/messages", {"recipient": "", "payload": "x"}
        )
        assert status == 400
        assert body["error"]["code"] == "invalid_message"


def test_reject_long_recipient(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _req(
            api.base, "POST", "/v1/messages", {"recipient": "r" * 201, "payload": "x"}
        )
        assert status == 400
        assert body["error"]["code"] == "invalid_message"


def test_reject_missing_payload(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _req(
            api.base, "POST", "/v1/messages", {"recipient": "ops@example"}
        )
        assert status == 400
        assert body["error"]["code"] == "invalid_message"


def test_reject_long_payload(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _req(
            api.base, "POST", "/v1/messages",
            {"recipient": "ops@example", "payload": "x" * 10001},
        )
        assert status == 400
        assert body["error"]["code"] == "invalid_message"


def test_reject_bad_priority(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _req(
            api.base, "POST", "/v1/messages",
            {"recipient": "ops@example", "payload": "x", "priority": "urgent"},
        )
        assert status == 400
        assert body["error"]["code"] == "invalid_message"


def test_reject_unknown_field(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _req(
            api.base, "POST", "/v1/messages",
            {"recipient": "ops@example", "payload": "x", "extra": 1},
        )
        assert status == 400
        assert body["error"]["code"] == "invalid_message"


def test_get_roundtrip(tmp_path):
    with Serve(tmp_path / "home") as api:
        _status, _headers, body = _enqueue(
            api.base, recipient="ops@example", payload="hello world"
        )
        status, _headers, full = _req(api.base, "GET", "/v1/messages/" + body["id"])
        assert status == 200
        assert full["id"] == body["id"]
        assert full["recipient"] == "ops@example"
        assert full["payload"] == "hello world"


def test_get_unknown_404(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _req(api.base, "GET", "/v1/messages/msg_0123456789abcdef")
        assert status == 404
        assert body["error"]["code"] == "not_found"


def test_message_file_exists(tmp_path):
    # R16/R18: a 201 means the message file is on disk at the contractual path.
    home = tmp_path / "home"
    with Serve(home) as api:
        _status, _headers, body = _enqueue(api.base)
        assert (home / "messages" / (body["id"] + ".json")).is_file()
""")

_POSTBOX_HIDDEN["test_idempotency.py"] = _pb("""
def test_replay_returns_original_with_header(tmp_path):
    with Serve(tmp_path / "home") as api:
        status1, _headers1, body1 = _enqueue(
            api.base, recipient="ops@example", payload="p1", key="k1"
        )
        assert status1 == 201
        status2, headers2, body2 = _enqueue(
            api.base, recipient="ops@example", payload="p1", key="k1", expect=200
        )
        assert status2 == 200
        assert body2["id"] == body1["id"]
        replay = headers2.get("Idempotent-Replay") or headers2.get("idempotent-replay")
        assert replay == "true"


def test_conflict_400(tmp_path):
    with Serve(tmp_path / "home") as api:
        _enqueue(api.base, recipient="ops@example", payload="p1", key="k1")
        status, _headers, body = _enqueue(
            api.base, recipient="ops@example", payload="DIFFERENT", key="k1", expect=400
        )
        assert body["error"]["code"] == "idempotency_conflict"


def test_no_key_two_enqueues(tmp_path):
    with Serve(tmp_path / "home") as api:
        _s1, _h1, body1 = _enqueue(api.base)
        _s2, _h2, body2 = _enqueue(api.base)
        assert body1["id"] != body2["id"]


def test_key_too_long_400(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _enqueue(api.base, key="k" * 101, expect=400)
        assert body["error"]["code"] == "invalid_message"
""")

_POSTBOX_HIDDEN["test_retry_endpoint.py"] = _pb("""
def test_retry_unknown_404(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, body = _req(
            api.base, "POST", "/v1/messages/msg_0123456789abcdef/retry"
        )
        assert status == 404
        assert body["error"]["code"] == "not_found"


def test_retry_queued_409(tmp_path):
    # A poison recipient never delivers, so the message stays queued even
    # if a worker is running — this test does not depend on worker timing.
    with Serve(tmp_path / "home") as api:
        _s, _h, body = _enqueue(api.base, recipient="poison:never")
        status, _headers, err = _req(
            api.base, "POST", "/v1/messages/" + body["id"] + "/retry"
        )
        assert status == 409
        assert err["error"]["code"] == "not_retryable"


def test_retry_delivered_409(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        _s, _h, body = _enqueue(api.base, recipient="ops@example")
        with Worker(home):
            assert _wait_for(lambda: _delivered(api, body["id"]))
        status, _headers, err = _req(
            api.base, "POST", "/v1/messages/" + body["id"] + "/retry"
        )
        assert status == 409
        assert err["error"]["code"] == "not_retryable"


def test_retry_failed_requeues(tmp_path):
    home = tmp_path / "home"
    with Serve(home, config={"backoff_base": 0.05, "poll_interval": 0.02}) as api:
        _s, _h, body = _enqueue(api.base, recipient="poison:never")
        with Worker(home):
            assert _wait_for(lambda: _failed(api, body["id"]))
        status, _headers, message = _req(
            api.base, "POST", "/v1/messages/" + body["id"] + "/retry"
        )
        assert status == 200
        assert message["status"] == "queued"
        assert message["attempts"] == 0
""")

_POSTBOX_HIDDEN["test_worker_cli.py"] = _pb("""
def test_worker_delivers_enqueued_message(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        _s, _h, body = _enqueue(api.base, recipient="ops@example", payload="hello")
        with Worker(home):
            assert _wait_for(lambda: _delivered(api, body["id"]))
        assert _spool_file(home, "ops@example", body["id"]).is_file()


def test_spool_json_shape(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        _s, _h, body = _enqueue(
            api.base, recipient="ops@example", payload="hello", priority="high"
        )
        with Worker(home):
            assert _wait_for(lambda: _delivered(api, body["id"]))
        spool = json.loads(
            _spool_file(home, "ops@example", body["id"]).read_text(encoding="utf-8")
        )
        assert set(spool) == {"id", "recipient", "payload", "priority", "delivered_at"}
        assert spool["id"] == body["id"]
        assert spool["recipient"] == "ops@example"
        assert spool["payload"] == "hello"
        assert spool["priority"] == "high"
        assert spool["delivered_at"]


def test_worker_delivers_many(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        ids = []
        for index in range(3):
            _s, _h, body = _enqueue(
                api.base, recipient="ops" + str(index) + "@example", payload="m" + str(index)
            )
            ids.append(body["id"])
        with Worker(home):
            for mid in ids:
                assert _wait_for(lambda mid=mid: _delivered(api, mid))
""")

_POSTBOX_HIDDEN["test_retry_backoff.py"] = _pb("""
def test_poison_fails_after_three_attempts(tmp_path):
    home = tmp_path / "home"
    with Serve(home, config={"backoff_base": 0.05, "poll_interval": 0.02}) as api:
        _s, _h, body = _enqueue(api.base, recipient="poison:never")
        with Worker(home):
            assert _wait_for(lambda: _failed(api, body["id"]))
        _s, _h, full = _req(api.base, "GET", "/v1/messages/" + body["id"])
        assert full["status"] == "failed"
        assert full["attempts"] == 3


def test_poison_never_delivered(tmp_path):
    home = tmp_path / "home"
    with Serve(home, config={"backoff_base": 0.05, "poll_interval": 0.02}) as api:
        _s, _h, body = _enqueue(
            api.base, recipient="poison:never", payload="do not deliver"
        )
        with Worker(home):
            _wait_for(lambda: _failed(api, body["id"]))
        assert not _spool_file(home, "poison:never", body["id"]).exists()


def test_backoff_delays_second_attempt(tmp_path):
    # With backoff_base 5 the second attempt is ~5s away: after the first
    # failure the message must still be queued with exactly one attempt.
    home = tmp_path / "home"
    with Serve(home, config={"backoff_base": 5.0, "poll_interval": 0.02}) as api:
        _s, _h, body = _enqueue(api.base, recipient="poison:never")
        with Worker(home):

            def one_attempt():
                _s, _h, full = _req(api.base, "GET", "/v1/messages/" + body["id"])
                if full.get("attempts", 0) >= 1 and full.get("status") == "queued":
                    return full
                return None

            full = _wait_for(one_attempt, timeout=10)
            assert full is not None
        time.sleep(0.4)
        _s, _h, after = _req(api.base, "GET", "/v1/messages/" + body["id"])
        assert after["status"] == "queued"
        assert after["attempts"] == 1
""")

_POSTBOX_HIDDEN["test_expiry.py"] = _pb("""
def test_expired_after_ttl(tmp_path):
    # A POISON recipient: delivery is impossible, so the message can only
    # ever be expired — the test cannot race any worker (design-neutral).
    home = tmp_path / "home"
    with Serve(home, config={"ttl_seconds": 1, "poll_interval": 0.02}) as api:
        _s, _h, body = _enqueue(
            api.base, recipient="poison:never", payload="too late"
        )
        time.sleep(1.3)
        with Worker(home):

            def expired():
                return _status_of(api, body["id"]) == "expired"

            assert _wait_for(expired)
        assert not _spool_file(home, "poison:never", body["id"]).exists()


def test_fresh_message_not_expired(tmp_path):
    home = tmp_path / "home"
    with Serve(home, config={"ttl_seconds": 3600, "poll_interval": 0.02}) as api:
        _s, _h, body = _enqueue(api.base, recipient="ops@example", payload="fresh")
        with Worker(home):
            assert _wait_for(lambda: _delivered(api, body["id"]))
        _s, _h, full = _req(api.base, "GET", "/v1/messages/" + body["id"])
        assert full["status"] == "delivered"
""")

_POSTBOX_HIDDEN["test_stats.py"] = _pb("""
def test_stats_counts(tmp_path):
    # Poison recipients never deliver, so these three stay queued (or
    # failed after their attempts) — the assertion is status-conservation,
    # which holds whether or not a worker is running.
    with Serve(tmp_path / "home") as api:
        for index in range(3):
            _enqueue(api.base, recipient="poison:never" + str(index), payload="m" + str(index))
        status, _headers, stats = _req(api.base, "GET", "/v1/stats")
        assert status == 200
        total = (
            stats["queued"]
            + stats["delivering"]
            + stats["delivered"]
            + stats["failed"]
            + stats["expired"]
        )
        assert total == 3
        assert stats["delivered"] == 0


def test_stats_keys(tmp_path):
    with Serve(tmp_path / "home") as api:
        status, _headers, stats = _req(api.base, "GET", "/v1/stats")
        assert status == 200
        assert set(stats) == {"queued", "delivering", "delivered", "failed", "expired"}
        for value in stats.values():
            assert isinstance(value, int)
""")

_POSTBOX_HIDDEN["test_cli.py"] = _pb("""
def _run_cli(args, home, env=None):
    full_env = {"PATH": "/usr/bin:/bin", "HOME": str(home.parent)}
    if env:
        full_env.update(env)
    return subprocess.run(
        [PY, "-m", "postbox"] + args + ["--home", str(home)],
        cwd=ROOT, capture_output=True, text=True, env=full_env, timeout=30,
    )


def test_cli_send_prints_id(tmp_path):
    home = tmp_path / "home"
    proc = _run_cli(["send", "--recipient", "ops@example", "--payload", "hello"], home)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().startswith("msg_")
    assert (home / "messages" / (proc.stdout.strip() + ".json")).is_file()


def test_cli_send_payload_file(tmp_path):
    home = tmp_path / "home"
    payload = tmp_path / "payload.txt"
    payload.write_text("from a file", encoding="utf-8")
    proc = _run_cli(
        ["send", "--recipient", "ops@example", "--payload-file", str(payload)], home
    )
    assert proc.returncode == 0, proc.stderr
    mid = proc.stdout.strip()
    message = json.loads((home / "messages" / (mid + ".json")).read_text(encoding="utf-8"))
    assert message["payload"] == "from a file"


def test_cli_send_priority(tmp_path):
    home = tmp_path / "home"
    proc = _run_cli(
        ["send", "--recipient", "ops@example", "--payload", "p", "--priority", "high"], home
    )
    assert proc.returncode == 0, proc.stderr
    mid = proc.stdout.strip()
    message = json.loads((home / "messages" / (mid + ".json")).read_text(encoding="utf-8"))
    assert message["priority"] == "high"


def test_cli_status_json(tmp_path):
    home = tmp_path / "home"
    send = _run_cli(["send", "--recipient", "ops@example", "--payload", "hi"], home)
    mid = send.stdout.strip()
    status = _run_cli(["status", mid], home)
    assert status.returncode == 0, status.stderr
    message = json.loads(status.stdout)
    assert message["id"] == mid
    assert message["recipient"] == "ops@example"


def test_cli_stats_json(tmp_path):
    home = tmp_path / "home"
    _run_cli(["send", "--recipient", "ops@example", "--payload", "hi"], home)
    proc = _run_cli(["stats"], home)
    assert proc.returncode == 0, proc.stderr
    stats = json.loads(proc.stdout)
    assert stats["queued"] >= 1


def test_cli_retry_unknown_nonzero(tmp_path):
    home = tmp_path / "home"
    # the send must WORK first — this test is about the retry contract,
    # not about any crash exiting nonzero
    send = _run_cli(["send", "--recipient", "ops@example", "--payload", "hi"], home)
    assert send.returncode == 0, send.stderr
    proc = _run_cli(["retry", "msg_0123456789abcdef"], home)
    assert proc.returncode != 0
""")

_POSTBOX_HIDDEN["test_durability.py"] = _pb("""
def test_message_survives_restart(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        _s, _h, body = _enqueue(api.base, recipient="ops@example", payload="durable")
    with Serve(home) as api:
        status, _headers, full = _req(api.base, "GET", "/v1/messages/" + body["id"])
        assert status == 200
        assert full["payload"] == "durable"
        assert full["recipient"] == "ops@example"


def test_stats_survive_restart(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        for index in range(2):
            _enqueue(api.base, recipient="poison:never" + str(index), payload="m" + str(index))
    with Serve(home) as api:
        status, _headers, stats = _req(api.base, "GET", "/v1/stats")
        assert status == 200
        total = (
            stats["queued"]
            + stats["delivering"]
            + stats["delivered"]
            + stats["failed"]
            + stats["expired"]
        )
        assert total == 2
        assert stats["delivered"] == 0


def test_two_messages_two_files(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        _s1, _h1, body1 = _enqueue(api.base, recipient="a@example", payload="one")
        _s2, _h2, body2 = _enqueue(api.base, recipient="b@example", payload="two")
        assert body1["id"] != body2["id"]
        assert (home / "messages" / (body1["id"] + ".json")).is_file()
        assert (home / "messages" / (body2["id"] + ".json")).is_file()
        _s, _h, full2 = _req(api.base, "GET", "/v1/messages/" + body2["id"])
        assert full2["payload"] == "two"


def test_201_means_on_disk(tmp_path):
    # R18: the 201 must not precede the write — read the file back right
    # after the response, through a fresh store read (no API involved).
    home = tmp_path / "home"
    with Serve(home) as api:
        _s, _h, body = _enqueue(api.base, recipient="ops@example", payload="ack")
        raw = json.loads((home / "messages" / (body["id"] + ".json")).read_text(encoding="utf-8"))
        assert raw["id"] == body["id"]
        assert raw["status"] == "queued"
""")

_POSTBOX_HIDDEN["test_config.py"] = _pb("""
import os


def _run_cli(args, env):
    return subprocess.run(
        [PY, "-m", "postbox"] + args,
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=30,
    )


def test_home_flag_beats_env(tmp_path):
    env_home = tmp_path / "env-home"
    flag_home = tmp_path / "flag-home"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "POSTBOX_HOME": str(env_home)}
    proc = _run_cli(
        ["send", "--recipient", "ops@example", "--payload", "x", "--home", str(flag_home)], env
    )
    assert proc.returncode == 0, proc.stderr
    mid = proc.stdout.strip()
    assert (flag_home / "messages" / (mid + ".json")).is_file()
    assert not (env_home / "messages").exists()


def test_env_home(tmp_path):
    env_home = tmp_path / "env-home"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "POSTBOX_HOME": str(env_home)}
    proc = _run_cli(["send", "--recipient", "ops@example", "--payload", "x"], env)
    assert proc.returncode == 0, proc.stderr
    mid = proc.stdout.strip()
    assert (env_home / "messages" / (mid + ".json")).is_file()


def test_knobs_from_postbox_json(tmp_path):
    # ttl_seconds=1 from postbox.json: the message must be expired by the
    # worker, not delivered (with the default 3600 it would deliver). A
    # POISON recipient so no worker can deliver it first (design-neutral).
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "postbox.json").write_text(
        json.dumps({"ttl_seconds": 1, "poll_interval": 0.02}), encoding="utf-8"
    )
    with Serve(home) as api:
        _s, _h, body = _enqueue(
            api.base, recipient="poison:never", payload="soon stale"
        )
        time.sleep(1.3)
        with Worker(home):

            def expired():
                return _status_of(api, body["id"]) == "expired"

            assert _wait_for(expired)
        assert not _spool_file(home, "poison:never", body["id"]).exists()


def test_malformed_config_uses_defaults(tmp_path):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "postbox.json").write_text("{not json at all", encoding="utf-8")
    with Serve(home) as api:
        status, _headers, body = _enqueue(api.base)
        assert status == 201
        assert body["status"] == "queued"
""")

#: The CHANGE pack's extra hidden tests: the process-separation contract.
#: These are the tests that punish the embedded-worker / cached-stats
#: designs — the original suite is deliberately neutral on both.
_POSTBOX_HIDDEN_CHANGE = _pb("""
def test_serve_alone_does_not_deliver(tmp_path):
    # serve is the API ONLY: with no worker process running, a message
    # must stay queued (an embedded worker is now a defect).
    home = tmp_path / "home"
    with Serve(home) as api:
        _s, _h, body = _enqueue(api.base, recipient="ops@example", payload="no worker")
        time.sleep(1.2)  # far past any default poll interval
        _s2, _h2, full = _req(api.base, "GET", "/v1/messages/" + body["id"])
        assert full["status"] == "queued"
        assert not _spool_file(home, "ops@example", body["id"]).exists()


def test_worker_subprocess_delivers(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        _s, _h, body = _enqueue(api.base, recipient="ops@example", payload="hi")
        with Worker(home):
            assert _wait_for(lambda: _delivered(api, body["id"]))
        assert _spool_file(home, "ops@example", body["id"]).is_file()


def test_worker_picks_up_enqueued_while_down(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        ids = []
        for index in range(2):
            _s, _h, body = _enqueue(
                api.base, recipient="ops@example", payload="m" + str(index)
            )
            ids.append(body["id"])
        time.sleep(0.6)  # the worker is DOWN; the messages must wait
        worker = Worker(home).start()
        try:
            for mid in ids:
                assert _wait_for(lambda mid=mid: _delivered(api, mid))
        finally:
            worker.stop()


def test_stats_reflect_worker_deliveries(tmp_path):
    # Cross-process stats: the worker delivered in ANOTHER process; the
    # API's stats must show it without any restart (a cached stats index
    # in the serve process would still say queued).
    home = tmp_path / "home"
    with Serve(home) as api:
        ids = []
        for index in range(2):
            _s, _h, body = _enqueue(
                api.base, recipient="ops@example", payload="m" + str(index)
            )
            ids.append(body["id"])
        worker = Worker(home).start()
        try:
            for mid in ids:
                assert _wait_for(lambda mid=mid: _delivered(api, mid))
        finally:
            worker.stop()
        status, _headers, stats = _req(api.base, "GET", "/v1/stats")
        assert status == 200
        assert stats["delivered"] == 2
        assert stats["queued"] == 0


def test_api_serves_while_worker_down(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        # no worker anywhere: the API must be fully usable
        status, _h, _b = _req(api.base, "GET", "/healthz")
        assert status == 200
        status, _headers, body = _enqueue(
            api.base, recipient="ops@example", payload="while down"
        )
        assert status == 201
        status, _headers, full = _req(api.base, "GET", "/v1/messages/" + body["id"])
        assert status == 200
        assert full["status"] == "queued"


def test_worker_restart_continues(tmp_path):
    home = tmp_path / "home"
    with Serve(home) as api:
        _s, _h, first = _enqueue(api.base, recipient="ops@example", payload="first")
        with Worker(home):
            assert _wait_for(lambda: _delivered(api, first["id"]))
        # worker DOWN again; a new message waits, then a fresh worker picks it up
        _s, _h, second = _enqueue(api.base, recipient="ops@example", payload="second")
        time.sleep(0.3)
        worker = Worker(home).start()
        try:
            assert _wait_for(lambda: _delivered(api, second["id"]))
        finally:
            worker.stop()
        assert _spool_file(home, "ops@example", second["id"]).is_file()
""")


def _postbox_task() -> dict[str, Any]:
    return {
        "name": "xl-project-postbox",
        "kind": "project",
        "seed": {"BRIEF.md": _POSTBOX_BRIEF},
        "task": _POSTBOX_TASK,
        "hidden_tests": dict(_POSTBOX_HIDDEN),
        "change": {
            "note": _POSTBOX_CHANGE_NOTE,
            "hidden_tests": {"test_change_separation.py": _POSTBOX_HIDDEN_CHANGE},
            "invalidates": ["test_stats.py", "test_worker_cli.py"],
        },
        "reference": dict(_POSTBOX_REFERENCE),
        "expected_hours": 2.5,
    }


# ===========================================================================
# Part 3 — TASKS + the on-disk materializer (the xl_bench fixture format)
# ===========================================================================

TASKS: list[dict[str, Any]] = [
    _research_task(),
    _postbox_task(),
]


def write_fixture(task: dict[str, Any], out_dir: Path) -> Path:
    """Materialize a TASKS entry into the on-disk format scripts/xl_bench.py
    consumes (workspace/ + TASK.md + hidden/ + change/ + reference/):

        <out_dir>/workspace/<relpath>   the seed (copied into a run)
        <out_dir>/TASK.md               the task text given to the orchestrator
        <out_dir>/hidden/<relpath>      the ORIGINAL hidden suite (post-hoc only)
        <out_dir>/change/CHANGE_NOTE.md  sent as the 35% perturbation message
        <out_dir>/change/hidden/<relpath>  the extra tests encoding the change
        <out_dir>/change/invalidates.txt   original hidden files the change retires
        <out_dir>/reference/<relpath>  the reference solution (pass-when-solved)

    Nothing here ever enters a run workspace: the bench copies workspace/
    in and grades post-hoc from hidden/ + change/hidden/.
    """
    out = Path(out_dir)
    seed = task["seed"]
    if not isinstance(seed, dict):
        raise TypeError("write_fixture supports dict seeds (this builder ships none other)")
    for rel, content in seed.items():
        target = out / "workspace" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (out / "TASK.md").write_text(task["task"], encoding="utf-8")
    for rel, content in task["hidden_tests"].items():
        target = out / "hidden" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    change = task["change"]
    (out / "change" / "CHANGE_NOTE.md").parent.mkdir(parents=True, exist_ok=True)
    (out / "change" / "CHANGE_NOTE.md").write_text(change["note"], encoding="utf-8")
    for rel, content in change["hidden_tests"].items():
        target = out / "change" / "hidden" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (out / "change" / "invalidates.txt").write_text(
        "\n".join(change["invalidates"]) + ("\n" if change["invalidates"] else ""),
        encoding="utf-8",
    )
    for rel, content in task["reference"].items():
        target = out / "reference" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    """Materialize every fixture into data/xl-bench/fixtures/<name>/ (the
    integrating job's input) and print a one-line summary per fixture."""
    import argparse
    import shutil

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=None,
        help="output root (default: <repo>/data/xl-bench/fixtures)",
    )
    args = parser.parse_args(argv)
    root = (
        Path(args.out)
        if args.out
        else Path(__file__).resolve().parent.parent / "data/xl-bench/fixtures"
    )
    for task in TASKS:
        target = root / task["name"]
        if target.exists():
            shutil.rmtree(target)
        write_fixture(task, target)
        print(f"{task['name']}: {len(task['seed'])} seed files -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
