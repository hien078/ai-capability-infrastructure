"""§80 proof-loop measurement: real tasks, A (client alone) vs E (full pipeline).

Grows the real-task sample beyond the first manual run: each task is a
small repo with a genuine bug and a failing acceptance test. Every task
runs twice through a real OpenCode client with the same model and the
same prompt —

- variant A: no plugin, no platform skills (the client works naked);
- variant E: the ACI routing plugin installed — the platform routes the
  prompt, injects 0-5 skills, and the run is reported via POST /v1/outcomes
  (§33) with the stashed route/bundle ids.

The only difference between the variants is the platform's presence, so
acceptance deltas are attributable to routed skills. §34 caveat: n is
small, one model, author-selected tasks — directional evidence, never
statistics. The report JSON records both variants side by side.

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/proof_loop.py \
        [--workdir DIR] [--model local-gateway/OneNexus/glm-5.3]

Requires the ACI server on 127.0.0.1:8000 (uvicorn aci.main:app) and the
opencode CLI on PATH. The .opencode template (plugin + pinned deps +
per-project skills catalog) is materialized from the repo's plugin source
on every run — the user's global OpenCode config is never touched.

Gotcha (verified live, OpenCode v2.0.18): the CLI resolves its project
location from $PWD, not getcwd() — programmatic callers MUST export PWD
pointing at the task directory or the session lands in the caller's
project and the plugin never loads (fail-open, silently naked).
"""

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parent.parent
PLUGIN_SRC = REPO / "src/aci/adapters/inbound/opencode/plugin/index.ts"
TEMPLATE = Path("/tmp/opencode/oc-template/.opencode")
REPORT_ROOT = REPO / "data/proof-loop"
OPENCODE = str(Path.home() / ".opencode/bin/opencode")
BUN = str(Path.home() / ".bun/bin/bun")
PYTEST = str(REPO / ".venv/bin/python")


def _task(name: str, files: dict[str, str], prompt: str) -> dict[str, Any]:
    return {"name": name, "files": files, "prompt": prompt}


TASKS = [
    _task(
        "debug-mutable-default",
        {
            "invoice.py": (
                '"""A tiny invoice service with a bug."""\n\n\n'
                "class InvoiceService:\n"
                "    def __init__(self) -> None:\n"
                '        self._rates = {"standard": 1.0, "vip": 0.8}\n\n'
                "    def apply_discount(self, rate_name: str, history: list[dict] = [])"
                " -> list[dict]:\n"
                '        """Record a discount application and return the history."""\n'
                '        history.append({"rate": rate_name, "applied": self._rates[rate_name]})\n'
                "        return history\n"
            ),
            "test_invoice.py": (
                "from invoice import InvoiceService\n\n\n"
                "def test_history_isolated() -> None:\n"
                '    """A discount applied through one instance must never leak into\n'
                '    the history returned by a different instance."""\n'
                "    first = InvoiceService()\n"
                "    second = InvoiceService()\n"
                '    first.apply_discount("standard")\n'
                '    result = second.apply_discount("vip")\n'
                '    assert result == [{"rate": "vip", "applied": 0.8}], result\n'
            ),
        },
        "The test test_history_isolated in test_invoice.py fails. Find the root cause "
        "before proposing any fix, then fix it and make the whole test suite green. "
        "Show the verification output.",
    ),
    _task(
        "debug-late-binding",
        {
            "callbacks.py": (
                '"""Builds worker callbacks — with a bug."""\n\n\n'
                "def make_workers(names: list[str]) -> dict[str, callable]:\n"
                '    """Return one callable per name; calling it greets that name."""\n'
                "    workers = {}\n"
                "    for name in names:\n"
                "        def greet() -> str:\n"
                '            return f"hello {name}"\n'
                "        workers[name] = greet\n"
                "    return workers\n"
            ),
            "test_callbacks.py": (
                "from callbacks import make_workers\n\n\n"
                "def test_each_worker_greets_its_own_name() -> None:\n"
                '    workers = make_workers(["ada", "grace", "linus"])\n'
                '    assert workers["ada"]() == "hello ada"\n'
                '    assert workers["grace"]() == "hello grace"\n'
                '    assert workers["linus"]() == "hello linus"\n'
            ),
        },
        "The test test_each_worker_greets_its_own_name in test_callbacks.py fails. "
        "Find the root cause before proposing any fix, then fix it and make the whole "
        "test suite green. Show the verification output.",
    ),
    _task(
        "testing-timestamps",
        {
            "schedule.py": (
                '"""Parses schedule timestamps — with a bug."""\n\n'
                "from datetime import datetime\n\n\n"
                "def parse_when(raw: str) -> datetime:\n"
                '    """Parse an ISO timestamp into a datetime.\n\n'
                "    The result must still carry the original UTC offset so that\n"
                '    comparisons with other aware datetimes are correct."""\n'
                '    return datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")\n'
            ),
            "test_schedule.py": (
                "from datetime import datetime, timezone\n\n"
                "from schedule import parse_when\n\n\n"
                "def test_offset_survives_parsing() -> None:\n"
                '    when = parse_when("2026-09-28T10:30:00+02:00")\n'
                "    assert when.utcoffset() is not None\n"
                "    assert when == datetime(2026, 9, 28, 8, 30, tzinfo=timezone.utc)\n"
            ),
        },
        "The test test_offset_survives_parsing in test_schedule.py fails. Find the "
        "root cause before proposing any fix, then fix it and make the whole test "
        "suite green. Show the verification output.",
    ),
    _task(
        "perf-duplicate-scan",
        {
            "scanner.py": (
                '"""Finds duplicate records — too slowly."""\n\n\n'
                "def find_duplicates(records: list[dict]) -> list[dict]:\n"
                '    """Return every record whose "id" appears more than once."""\n'
                "    out = []\n"
                "    for record in records:\n"
                '        same = [r for r in records if r["id"] == record["id"]]\n'
                "        if len(same) > 1 and record not in out:\n"
                "            out.append(record)\n"
                "    return out\n"
            ),
            "test_scanner.py": (
                "import time\n\n"
                "from scanner import find_duplicates\n\n\n"
                "def test_large_scan_is_fast() -> None:\n"
                '    records = [{"id": i % 800, "v": i} for i in range(8_000)]\n'
                "    started = time.perf_counter()\n"
                "    dupes = find_duplicates(records)\n"
                "    elapsed = time.perf_counter() - started\n"
                "    assert len(dupes) == 8_000\n"
                '    assert elapsed < 1.0, f"scan took {elapsed:.2f}s — too slow"\n'
            ),
        },
        "The test test_large_scan_is_fast in test_scanner.py fails on the timing "
        "assertion. Find the root cause before proposing any fix, then fix it and "
        "make the whole test suite green. Show the verification output.",
    ),
    _task(
        "i18n-mojibake",
        {
            "names.py": (
                '"""Normalizes user-entered display names — with a bug."""\n\n\n'
                "def normalize(name: str) -> str:\n"
                '    """Normalize a display name for storage.\n\n'
                '    Must round-trip every character the user typed."""\n'
                '    return name.encode("ascii", errors="ignore").decode("ascii")\n'
            ),
            "test_names.py": (
                "from names import normalize\n\n\n"
                "def test_unicode_roundtrip() -> None:\n"
                '    original = "Grüße — 日本語 — café"\n'
                "    assert normalize(original) == original\n"
            ),
        },
        "The test test_unicode_roundtrip in test_names.py fails. Find the root cause "
        "before proposing any fix, then fix it and make the whole test suite green. "
        "Show the verification output.",
    ),
]

# Multi-file fixtures (§80 value-boundary hunt, round 3): the two earlier
# rounds hit a ceiling (A 10/10 = E 10/10) on single-file tasks, so these
# fixtures move the difficulty axis the plan points at next — cross-module
# reasoning. The bug lives in the SEAM between modules (shared module state,
# stage ordering, cache invalidation, exception translation, aliasing across
# a bus), so a symptom patch in one file cannot pass: the agent must trace a
# call across 2–3 files to find the root cause. Each fixture also pins the
# obvious-but-wrong fix out via a second assertion.
MULTI_TASKS = [
    _task(
        "multi-config-precedence",
        {
            "defaults.py": ('DEFAULTS = {"retries": 3, "timeout": 30}\n'),
            "config.py": (
                "from defaults import DEFAULTS\n\n\n"
                "def load(overrides: dict) -> dict:\n"
                '    """Build a config: `overrides` win over DEFAULTS.\n\n'
                "    DEFAULTS is shared module state — one load() call must\n"
                '    never be visible to the next."""\n'
                "    config = DEFAULTS\n"
                "    config.update(overrides)\n"
                "    return config\n"
            ),
            "app.py": (
                "from config import load\n\n\n"
                "def connect(overrides: dict | None = None) -> dict:\n"
                "    return load(overrides or {})\n"
            ),
            "test_config.py": (
                "from defaults import DEFAULTS\n\n"
                "from app import connect\n\n\n"
                "def test_overrides_win_and_defaults_untouched() -> None:\n"
                '    cfg = connect({"timeout": 5})\n'
                '    assert cfg["timeout"] == 5\n'
                '    assert cfg["retries"] == 3\n\n\n'
                "def test_loads_are_isolated() -> None:\n"
                '    connect({"retries": 99})\n'
                "    cfg = connect()\n"
                '    assert cfg["retries"] == 3, cfg\n'
                '    assert DEFAULTS == {"retries": 3, "timeout": 30}\n'
            ),
        },
        "The tests in test_config.py fail. Find the root cause before proposing "
        "any fix, then fix it and make the whole test suite green. Show the "
        "verification output.",
    ),
    _task(
        "multi-pipeline-ordering",
        {
            "parse.py": (
                "class Record:\n"
                "    def __init__(self, raw: str) -> None:\n"
                "        self.raw = raw\n"
                '        self.valid = raw.strip() != "" and not raw.startswith("#")\n\n\n'
                "def parse(line: str) -> Record:\n"
                "    return Record(line)\n"
            ),
            "validate.py": (
                "from parse import Record\n\n\n"
                "def validate(record: Record) -> bool:\n"
                '    """True when the record should be KEPT."""\n'
                "    return record.valid\n"
            ),
            "pipeline.py": (
                "from parse import parse\n"
                "from validate import validate\n\n\n"
                "def run(lines: list[str]) -> list[str]:\n"
                '    """Parse every line, keep only the valid records."""\n'
                "    records = [parse(line) for line in lines]\n"
                "    return [r.raw for r in records if not validate(r)]\n"
            ),
            "test_pipeline.py": (
                "from pipeline import run\n\n\n"
                "def test_valid_lines_are_kept() -> None:\n"
                '    out = run(["alpha", "beta"])\n'
                '    assert out == ["alpha", "beta"]\n\n\n'
                "def test_invalid_lines_are_dropped() -> None:\n"
                '    out = run(["alpha", "", "  ", "# comment", "beta"])\n'
                '    assert out == ["alpha", "beta"]\n'
            ),
        },
        "The tests in test_pipeline.py fail. Find the root cause before "
        "proposing any fix, then fix it and make the whole test suite green. "
        "Show the verification output.",
    ),
    _task(
        "multi-cache-invalidation",
        {
            "store.py": (
                "class Store:\n"
                "    def __init__(self) -> None:\n"
                "        self._data: dict[str, str] = {}\n\n"
                "    def get(self, key: str) -> str | None:\n"
                "        return self._data.get(key)\n\n"
                "    def put(self, key: str, value: str) -> None:\n"
                "        self._data[key] = value\n"
            ),
            "cache.py": (
                "from store import Store\n\n\n"
                "class ReadCache:\n"
                '    """Caches Store reads. A put() through the cache must\n'
                "    always be visible to the next get() — and repeated reads\n"
                '    of an unchanged key must be served from the cache."""\n\n'
                "    def __init__(self, store: Store) -> None:\n"
                "        self._store = store\n"
                "        self._cache: dict[str, str | None] = {}\n"
                "        self.hits = 0\n\n"
                "    def get(self, key: str) -> str | None:\n"
                "        if key in self._cache:\n"
                "            self.hits += 1\n"
                "            return self._cache[key]\n"
                "        value = self._store.get(key)\n"
                "        self._cache[key] = value\n"
                "        return value\n\n"
                "    def put(self, key: str, value: str) -> None:\n"
                "        self._store.put(key, value)\n"
            ),
            "service.py": (
                "from cache import ReadCache\n"
                "from store import Store\n\n\n"
                "def make_service() -> ReadCache:\n"
                "    return ReadCache(Store())\n"
            ),
            "test_service.py": (
                "from service import make_service\n\n\n"
                "def test_put_is_visible_after_cached_read() -> None:\n"
                "    svc = make_service()\n"
                '    svc.put("k", "v1")\n'
                '    assert svc.get("k") == "v1"\n'
                '    svc.put("k", "v2")\n'
                '    assert svc.get("k") == "v2"\n\n\n'
                "def test_repeated_reads_are_cached() -> None:\n"
                "    svc = make_service()\n"
                '    svc.put("k", "v")\n'
                '    svc.get("k")\n'
                '    svc.get("k")\n'
                '    svc.get("k")\n'
                "    assert svc.hits >= 2, svc.hits\n"
            ),
        },
        "The tests in test_service.py fail. Find the root cause before "
        "proposing any fix, then fix it and make the whole test suite green. "
        "Show the verification output.",
    ),
    _task(
        "multi-error-translation",
        {
            "repo.py": (
                "class NotFound(Exception):\n"
                "    pass\n\n\n"
                "def fetch(key: str) -> str:\n"
                '    DATA = {"a": "alpha"}\n'
                "    if not isinstance(key, str):\n"
                '        raise TypeError(f"key must be str, got {type(key).__name__}")\n'
                "    if key not in DATA:\n"
                "        raise NotFound(key)\n"
                "    return DATA[key]\n"
            ),
            "service.py": (
                "from repo import NotFound, fetch\n\n\n"
                "class ServiceError(Exception):\n"
                "    pass\n\n\n"
                "def get(key: str) -> str:\n"
                '    """Contract: a missing key raises ServiceError naming the\n'
                "    key. Programming errors (TypeError, ...) are NEVER\n"
                '    translated — they must propagate unchanged."""\n'
                "    try:\n"
                "        return fetch(key)\n"
                "    except Exception as exc:\n"
                '        raise ServiceError(f"missing: {exc}") from exc\n'
            ),
            "api.py": (
                "from service import ServiceError, get\n\n\n"
                "def status_for(key: str) -> int:\n"
                "    try:\n"
                "        get(key)\n"
                "        return 200\n"
                "    except ServiceError:\n"
                "        return 404\n"
            ),
            "test_api.py": (
                "import pytest\n\n"
                "from api import status_for\n"
                "from repo import NotFound\n"
                "from service import ServiceError, get\n\n\n"
                "def test_missing_key_is_service_error() -> None:\n"
                "    with pytest.raises(ServiceError) as exc_info:\n"
                '        get("nope")\n'
                '    assert "nope" in str(exc_info.value)\n\n\n'
                "def test_notfound_never_leaks_past_service() -> None:\n"
                "    with pytest.raises(ServiceError):\n"
                '        get("nope")\n\n\n'
                "def test_programming_errors_propagate() -> None:\n"
                "    with pytest.raises(TypeError):\n"
                "        get(None)\n\n\n"
                "def test_status_codes() -> None:\n"
                '    assert status_for("a") == 200\n'
                '    assert status_for("nope") == 404\n'
            ),
        },
        "The tests in test_api.py fail. Find the root cause before proposing "
        "any fix, then fix it and make the whole test suite green. Show the "
        "verification output.",
    ),
    _task(
        "multi-event-aliasing",
        {
            "events.py": (
                "def make_event(name: str, payload: dict) -> dict:\n"
                '    return {"name": name, "payload": payload}\n'
            ),
            "bus.py": (
                "from events import make_event\n\n\n"
                "class EventBus:\n"
                '    """Delivers events to subscribers.\n\n'
                "    A subscriber mutating the event it receives must never\n"
                "    corrupt the bus's own record of what was emitted, and\n"
                '    never leak into other subscribers."""\n\n'
                "    def __init__(self) -> None:\n"
                "        self._subs: list = []\n"
                "        self.last_event: dict | None = None\n\n"
                "    def subscribe(self, fn) -> None:\n"
                "        self._subs.append(fn)\n\n"
                "    def emit(self, name: str, payload: dict) -> None:\n"
                "        event = make_event(name, payload)\n"
                "        self.last_event = event\n"
                "        for fn in self._subs:\n"
                "            fn(event)\n"
            ),
            "handlers.py": (
                "from bus import EventBus\n\n\ndef make_bus() -> EventBus:\n    return EventBus()\n"
            ),
            "test_bus.py": (
                "from handlers import make_bus\n\n\n"
                "def test_subscriber_mutation_is_isolated() -> None:\n"
                "    bus = make_bus()\n"
                "    seen = []\n"
                '    bus.subscribe(lambda e: e["payload"].update({"hacked": True}))\n'
                "    bus.subscribe(lambda e: seen.append(e))\n"
                '    bus.emit("deploy", {"app": "web"})\n'
                '    assert seen == [{"name": "deploy", "payload": {"app": "web"}}]\n\n\n'
                "def test_last_event_records_what_was_emitted() -> None:\n"
                "    bus = make_bus()\n"
                '    bus.subscribe(lambda e: e["payload"].update({"hacked": True}))\n'
                '    bus.emit("deploy", {"app": "web"})\n'
                '    assert bus.last_event["payload"] == {"app": "web"}\n'
                '    assert bus.last_event["name"] == "deploy"\n'
            ),
        },
        "The tests in test_bus.py fail. Find the root cause before proposing "
        "any fix, then fix it and make the whole test suite green. Show the "
        "verification output.",
    ),
]

# Harder fixtures (§80 value-boundary hunt): families where naked agents
# commonly fail — aliasing, tuple-ordering, exception translation, boundary
# semantics, defensive copying. Each pins the OBVIOUS-but-wrong fix out via a
# second assertion, so symptom patches cannot pass acceptance.
HARD_TASKS = [
    _task(
        "class-attr-shared-state",
        {
            "limiter.py": (
                '"""Per-instance rate limiter — with a bug."""\n\n\n'
                "class RateLimiter:\n"
                '    """Records timestamps per tenant, PER INSTANCE.\n\n'
                "    Two limiters are independent trackers: what one records\n"
                '    the other must never see."""\n\n'
                "    _windows: dict[str, list[float]] = {}\n\n"
                "    def record(self, tenant: str, t: float) -> None:\n"
                "        self._windows.setdefault(tenant, []).append(t)\n\n"
                "    def count(self, tenant: str) -> int:\n"
                "        return len(self._windows.get(tenant, []))\n"
            ),
            "test_limiter.py": (
                "from limiter import RateLimiter\n\n\n"
                "def test_instances_are_isolated() -> None:\n"
                "    a, b = RateLimiter(), RateLimiter()\n"
                '    a.record("t1", 1.0)\n'
                '    a.record("t1", 2.0)\n'
                '    assert a.count("t1") == 2\n'
                '    assert b.count("t1") == 0\n\n\n'
                "def test_independent_windows_per_tenant() -> None:\n"
                "    r = RateLimiter()\n"
                '    r.record("t1", 1.0)\n'
                '    r.record("t2", 1.0)\n'
                '    assert r.count("t1") == 1\n'
                '    assert r.count("t2") == 1\n'
                '    assert r.count("t3") == 0\n'
            ),
        },
        "The tests in test_limiter.py fail. Find the root cause before "
        "proposing any fix, then fix it and make the whole test suite green. "
        "Show the verification output.",
    ),
    _task(
        "tie-stable-heap",
        {
            "queue.py": (
                "import heapq\n\n\n"
                "class JobQueue:\n"
                '    """Priority queue: lower priority number runs first.\n\n'
                "    For EQUAL priorities jobs run in insertion order (FIFO),\n"
                '    never alphabetically."""\n\n'
                "    def __init__(self) -> None:\n"
                "        self._heap: list[tuple[int, str]] = []\n\n"
                "    def push(self, job: str, priority: int) -> None:\n"
                "        heapq.heappush(self._heap, (priority, job))\n\n"
                "    def pop(self) -> str:\n"
                "        return heapq.heappop(self._heap)[1]\n"
            ),
            "test_queue.py": (
                "from queue import JobQueue\n\n\n"
                "def test_equal_priority_runs_fifo() -> None:\n"
                "    q = JobQueue()\n"
                '    q.push("zeta", 1)\n'
                '    q.push("alpha", 1)\n'
                '    q.push("mid", 1)\n'
                '    assert [q.pop() for _ in range(3)] == ["zeta", "alpha", "mid"]\n\n\n'
                "def test_lower_priority_number_runs_first() -> None:\n"
                "    q = JobQueue()\n"
                '    q.push("low", 5)\n'
                '    q.push("high", 1)\n'
                '    assert q.pop() == "high"\n'
                '    assert q.pop() == "low"\n'
            ),
        },
        "The tests in test_queue.py fail. Find the root cause before proposing "
        "any fix, then fix it and make the whole test suite green. Show the "
        "verification output.",
    ),
    _task(
        "exception-translation",
        {
            "importer.py": (
                '"""Row importer — with a bug."""\n\n\n'
                "class Importer:\n"
                '    """Imports one row at a time.\n\n'
                "    Contract: malformed rows raise ValueError — never KeyError,\n"
                "    never a silent failure. Valid rows are recorded in\n"
                '    `cleaned`."""\n\n'
                "    def __init__(self) -> None:\n"
                "        self.cleaned: list[str] = []\n\n"
                "    def import_row(self, row: dict) -> str:\n"
                "        try:\n"
                '            record_id = row["id"]\n'
                "            if not isinstance(record_id, str) or not record_id:\n"
                '                raise ValueError(f"malformed id: {record_id!r}")\n'
                "            self.cleaned.append(record_id)\n"
                "            return record_id\n"
                "        except Exception:\n"
                '            self.cleaned.append("failed")\n'
                '            return "failed"\n'
            ),
            "test_importer.py": (
                "import pytest\n\n"
                "from importer import Importer\n\n\n"
                "def test_missing_id_raises_valueerror() -> None:\n"
                '    """A row without an id is malformed: ValueError, not KeyError,\n'
                '    and never a silent return."""\n'
                "    imp = Importer()\n"
                "    with pytest.raises(ValueError):\n"
                '        imp.import_row({"name": "x"})\n'
                "    assert imp.cleaned == []\n\n\n"
                "def test_malformed_id_raises_valueerror() -> None:\n"
                "    imp = Importer()\n"
                "    with pytest.raises(ValueError):\n"
                '        imp.import_row({"id": ""})\n'
                "    assert imp.cleaned == []\n\n\n"
                "def test_valid_row_imports() -> None:\n"
                "    imp = Importer()\n"
                '    assert imp.import_row({"id": "r-1"}) == "r-1"\n'
                '    assert imp.cleaned == ["r-1"]\n'
            ),
        },
        "The tests in test_importer.py fail. Find the root cause before "
        "proposing any fix, then fix it and make the whole test suite green. "
        "Show the verification output.",
    ),
    _task(
        "boundary-window",
        {
            "window.py": (
                '"""Retention window — with a bug."""\n\n\n'
                "class Retention:\n"
                '    """Matches event times in [start, end) — start INCLUSIVE,\n'
                '    end EXCLUSIVE."""\n\n'
                "    def __init__(self, start: int, end: int) -> None:\n"
                "        self._start = start\n"
                "        self._end = end\n\n"
                "    def contains(self, t: int) -> bool:\n"
                "        return self._start <= t <= self._end\n\n"
                "    def events_in(self, events: dict[int, str]) -> list[str]:\n"
                "        return [v for t, v in sorted(events.items()) if self.contains(t)]\n"
            ),
            "test_window.py": (
                "from window import Retention\n\n\n"
                "def test_end_boundary_is_exclusive() -> None:\n"
                "    r = Retention(10, 20)\n"
                "    assert r.contains(10) is True\n"
                "    assert r.contains(19) is True\n"
                "    assert r.contains(20) is False\n\n\n"
                "def test_events_in_respects_both_boundaries() -> None:\n"
                "    r = Retention(0, 3)\n"
                '    events = {3: "b", 0: "a", 2: "c", -1: "d"}\n'
                '    assert r.events_in(events) == ["a", "c"]\n'
            ),
        },
        "The tests in test_window.py fail. Find the root cause before "
        "proposing any fix, then fix it and make the whole test suite green. "
        "Show the verification output.",
    ),
    _task(
        "defensive-copy-registry",
        {
            "registry.py": (
                '"""Tenant registry — with a bug."""\n\n\n'
                "class Registry:\n"
                '    """Tracks names per tenant.\n\n'
                "    `names()` must NEVER expose the internal list — callers\n"
                '    get their own copy and cannot corrupt the registry."""\n\n'
                "    def __init__(self) -> None:\n"
                "        self._by_tenant: dict[str, list[str]] = {}\n\n"
                "    def add(self, tenant: str, name: str) -> None:\n"
                "        self._by_tenant.setdefault(tenant, []).append(name)\n\n"
                "    def names(self, tenant: str) -> list[str]:\n"
                "        return self._by_tenant.get(tenant, [])\n"
            ),
            "test_registry.py": (
                "from registry import Registry\n\n\n"
                "def test_names_is_defensive_copy() -> None:\n"
                "    r = Registry()\n"
                '    r.add("t1", "a")\n'
                '    got = r.names("t1")\n'
                '    got.append("HACKED")\n'
                '    assert r.names("t1") == ["a"]\n\n\n'
                "def test_unknown_tenant_is_empty_and_safe() -> None:\n"
                "    r = Registry()\n"
                '    assert r.names("nope") == []\n'
                '    r.names("nope").append("HACKED")\n'
                '    r.add("nope", "x")\n'
                '    assert r.names("nope") == ["x"]\n'
            ),
        },
        "The tests in test_registry.py fail. Find the root cause before "
        "proposing any fix, then fix it and make the whole test suite green. "
        "Show the verification output.",
    ),
]


def run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    """Run `cmd` in `cwd`.

    OpenCode resolves its project location from the **$PWD env var**, not
    getcwd() (verified live, v2.0.18): subprocess cwd alone leaves the
    inherited $PWD pointing at the parent process, so the session lands in
    the wrong project and .opencode/plugins is never discovered. Always
    export PWD matching cwd for child processes.
    """
    env = {**os.environ, "PWD": str(cwd)}
    proc = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def ensure_template(base_url: str) -> None:
    """Materialize the .opencode template (plugin + deps + skills catalog).

    Self-contained: no pre-existing template assumed. The per-project
    opencode.json points OpenCode's native skill catalog at the platform so
    injected skill ids resolve and lazy-load (README step 3) — the global
    user config stays untouched.
    """
    plugins = TEMPLATE / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PLUGIN_SRC, plugins / "aci-router.ts")
    (TEMPLATE / "opencode.json").write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "skills": [f"{base_url}/opencode/skills/"],
            }
        ),
        encoding="utf-8",
    )
    (TEMPLATE / "package.json").write_text(
        json.dumps({"dependencies": {"@opencode/plugin": "2.0.18", "@opencode/schema": "2.0.18"}}),
        encoding="utf-8",
    )
    if not (TEMPLATE / "node_modules/@opencode/plugin").exists():
        subprocess.run(
            [BUN, "install"],
            cwd=TEMPLATE,
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )


def acceptance(task_dir: Path) -> bool:
    code, _ = run([PYTEST, "-m", "pytest", "-q"], task_dir, 180)
    return code == 0


def reset(task_dir: Path) -> None:
    run(["git", "checkout", "--", "."], task_dir, 30)
    run(["git", "clean", "-fdq", "-e", ".opencode/"], task_dir, 30)


def latest_route(bundle_db: str, since: float) -> dict[str, str] | None:
    engine = create_engine(bundle_db)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT r.route_run_id, r.bundle_id FROM route_runs r "
                "WHERE r.principal_id = 'opencode' "
                "AND r.created_at >= :since ORDER BY r.created_at DESC LIMIT 1"
            ),
            {"since": datetime.fromtimestamp(since, tz=UTC)},
        ).fetchone()
    return {"route_run_id": str(row[0]), "bundle_id": str(row[1])} if row else None


def report_outcome(base_url: str, ids: dict[str, str], passed: bool) -> str | None:
    status = "success" if passed else "failure"
    body = {
        "route_run_id": ids["route_run_id"],
        "bundle_id": ids["bundle_id"],
        "verdicts": [
            {"source": "test_harness", "status": status, "confidence": "high"},
            {"source": "agent_self_report", "status": status, "confidence": "medium"},
        ],
        "client_status": "completed",
        "build_passed": passed,
        "human_corrected": False,
    }
    resp = httpx.post(f"{base_url}/v1/outcomes", json=body, timeout=10)
    if resp.status_code not in (200, 201):
        print(f"  outcome POST failed: {resp.status_code} {resp.text[:200]}")
        return None
    return resp.json()["outcome_id"]


def run_variant(
    task: dict[str, Any], task_dir: Path, variant: str, args: argparse.Namespace
) -> dict[str, Any]:
    with_plugin = variant == "E"
    if with_plugin:
        shutil.copytree(TEMPLATE, task_dir / ".opencode", dirs_exist_ok=True)
    started = time.time()
    code, output = run([OPENCODE, "run", "-m", args.model, task["prompt"]], task_dir, args.timeout)
    wall = round(time.time() - started, 1)
    passed = acceptance(task_dir)
    record: dict[str, Any] = {
        "variant": variant,
        "exit_code": code,
        "acceptance_pass": passed,
        "wall_seconds": wall,
        "output_tail": output[-1500:],
    }
    if with_plugin:
        ids = latest_route(args.database_url, started)
        record["routed"] = ids
        if ids:
            record["outcome_id"] = report_outcome(args.base_url, ids, passed)
        shutil.rmtree(task_dir / ".opencode", ignore_errors=True)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/opencode/proof-tasks"))
    parser.add_argument("--model", default="local-gateway/OneNexus/glm-5.3")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--database-url", default="postgresql+psycopg://aci:aci@localhost:5432/aci_bench"
    )
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--only", help="run a single task by name")
    parser.add_argument(
        "--level",
        choices=["smoke", "hard", "multi"],
        default="smoke",
        help="task set: smoke (5 easy bugs), hard (§80 value-boundary hunt), "
        "or multi (cross-module reasoning — the ceiling-breaker round)",
    )
    args = parser.parse_args()

    task_set = {"hard": HARD_TASKS, "multi": MULTI_TASKS}.get(args.level, TASKS)

    args.workdir.mkdir(parents=True, exist_ok=True)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    ensure_template(args.base_url)

    results: list[dict[str, Any]] = []
    for task in task_set:
        if args.only and task["name"] != args.only:
            continue
        name = task["name"]
        task_dir = args.workdir / name
        if task_dir.exists():
            shutil.rmtree(task_dir)
        task_dir.mkdir(parents=True)
        for rel, content in task["files"].items():
            (task_dir / rel).write_text(content, encoding="utf-8")
        run(["git", "init", "-q"], task_dir, 30)
        run(["git", "add", "-A"], task_dir, 30)
        run(["git", "commit", "-qm", "task fixture"], task_dir, 30)

        print(f"=== {name} ===")
        baseline = run_variant(task, task_dir, "A", args)
        print(f"  A: pass={baseline['acceptance_pass']} {baseline['wall_seconds']}s")
        reset(task_dir)
        routed = run_variant(task, task_dir, "E", args)
        print(
            f"  E: pass={routed['acceptance_pass']} {routed['wall_seconds']}s "
            f"routed={routed.get('routed')}"
        )
        reset(task_dir)
        results.append({"task": name, "A": baseline, "E": routed})

    a_pass = sum(1 for r in results if r["A"]["acceptance_pass"])
    e_pass = sum(1 for r in results if r["E"]["acceptance_pass"])
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "level": args.level,
        "task_count": len(results),
        "summary": {
            "A_acceptance": a_pass,
            "E_acceptance": e_pass,
            "note": (
                "§34: n is small, one model, author-selected tasks — directional "
                "evidence only; the only difference between variants is the "
                "platform's presence (plugin + routed skills)."
            ),
        },
        "results": results,
    }
    out = REPORT_ROOT / f"proof-loop-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nA acceptance: {a_pass}/{len(results)}  E acceptance: {e_pass}/{len(results)}")
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
