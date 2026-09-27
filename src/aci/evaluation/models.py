"""Benchmark domain models (plan §§34–35, 41, 70; §52 Phase 14).

A benchmark case is a *replayable fixture* (§35): task text plus relevance
annotations and budget. The harness executes each case through the §70
variant matrix (A–E) and stores one result per (case, variant) that pins the
exact router (reranker/composer) and capability (id/version/digest) versions
it used — so a report can always name the exact code and content behind a
number.

Metric associations are observational, not causal (§34): variants A/B are
baselines, and without controlled task execution the exported numbers do
not prove what caused what.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from aci.domain.capability.models import CapabilityKind

VariantId = Literal[
    "client_alone",
    "manual_baseline",
    "retrieval_only",
    "retrieval_rerank",
    "full_pipeline",
]

# §70 experiment matrix, in order: A alone, B manual baseline, C retrieval,
# D +rerank, E +composer. Every case runs through all five.
VARIANTS: list[VariantId] = [
    "client_alone",
    "manual_baseline",
    "retrieval_only",
    "retrieval_rerank",
    "full_pipeline",
]


class BenchmarkCase(BaseModel):
    """One replayable fixture (§35): task + annotations + budget.

    ``relevant_*`` annotate which capabilities would help evaluate routing,
    but must not define the only acceptable solution path (§35). The primary
    score comes from the case's acceptance criteria, which live with the
    fixture — the harness only measures the routing side.
    """

    model_config = {"frozen": True}

    case_id: str
    category: str
    fixture: str
    task_text: str = Field(min_length=1, max_length=8000)
    relevant_strong: list[str] = Field(default_factory=list)
    relevant_acceptable: list[str] = Field(default_factory=list)
    relevant_irrelevant: list[str] = Field(default_factory=list)
    acceptance_tests: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    # Variant B: the manually curated native baseline (§70).
    baseline_capability_ids: list[str] = Field(default_factory=list)
    max_cost_usd: float | None = None
    max_latency_ms: int | None = None


class BenchmarkRun(BaseModel):
    """One execution of a case set — the group results attach to (§41)."""

    model_config = {"frozen": True}

    run_id: str
    label: str
    created_at: datetime
    case_count: int


class SelectedCapability(BaseModel):
    """Exact version+digest reference for one selected capability (§52)."""

    model_config = {"frozen": True}

    capability_id: str
    version: str
    digest: str
    kind: CapabilityKind


class RouterVersions(BaseModel):
    """Which router implementations produced a result (§52 acceptance)."""

    model_config = {"frozen": True}

    reranker_implementation: str | None = None
    reranker_version: str | None = None
    composer_implementation: str | None = None
    composer_version: str | None = None


class BenchmarkResult(BaseModel):
    """One (case × variant) measurement (§41 ``benchmark_results``)."""

    model_config = {"frozen": True}

    result_id: str
    run_id: str
    case_id: str
    variant: VariantId
    created_at: datetime
    # Platform routing only ran for C/D/E — and only E leaves §36 telemetry
    # (a real route run); A/B are platform-free baselines.
    route_run_id: str | None = None
    bundle_id: str | None = None
    selected: list[SelectedCapability] = Field(default_factory=list)
    router: RouterVersions = Field(default_factory=RouterVersions)
    metrics: dict[str, Any] = Field(default_factory=dict)
