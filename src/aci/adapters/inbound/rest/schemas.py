"""REST wire schemas (plan §§11, 43).

Transport DTOs only — the domain models stay the source of truth. The
adapter translates DTO -> typed commands/queries and domain results back;
no business logic lives here (§13 edge responsibilities).
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from aci.domain.capability.models import CapabilityKind, TaskContext, VerdictSource


def _default_kinds() -> list[CapabilityKind]:
    return ["skill"]


class TaskContextIn(BaseModel):
    """Minimal routing context (§25.4): hints, never repo dumps or secrets."""

    language: str | None = None
    frameworks: list[str] = Field(default_factory=list)
    phase: str | None = None
    file_hints: list[str] = Field(default_factory=list)
    repository_summary: str = Field(default="", max_length=2000)

    def to_domain(self) -> TaskContext:
        return TaskContext(
            language=self.language,
            frameworks=list(self.frameworks),
            phase=self.phase,
            file_hints=list(self.file_hints),
            repository_summary=self.repository_summary,
        )


class TaskIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class RouteConstraintsIn(BaseModel):
    max_items: int = Field(default=5, ge=0, le=5)
    max_context_tokens: int = Field(default=6000, ge=0)
    allowed_kinds: list[CapabilityKind] = Field(default_factory=_default_kinds)


class RouteRequest(BaseModel):
    """POST /v1/routes body (§11.3)."""

    task: TaskIn
    context: TaskContextIn = Field(default_factory=TaskContextIn)
    constraints: RouteConstraintsIn = Field(default_factory=RouteConstraintsIn)
    principal_id: str = "anonymous"
    organization_id: str | None = None
    workspace_id: str | None = None
    client_type: str = "rest-client"
    client_version: str | None = None


class SearchFiltersIn(BaseModel):
    kinds: list[CapabilityKind] = Field(default_factory=_default_kinds)
    domains: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    """POST /v1/capabilities/search body (§11.2)."""

    query: str = Field(default="", max_length=2000)
    filters: SearchFiltersIn = Field(default_factory=SearchFiltersIn)
    limit: int = Field(default=20, ge=1, le=100)


class OutcomeVerdictIn(BaseModel):
    """One verdict from one source (§33); never a lone success flag."""

    source: VerdictSource
    status: Literal["success", "failure", "unknown"]
    confidence: Literal["high", "medium", "low"] = "low"


class OutcomeRequest(BaseModel):
    """POST /v1/outcomes body (§33)."""

    route_run_id: str = Field(min_length=1)
    bundle_id: str = Field(min_length=1)
    verdicts: list[OutcomeVerdictIn] = Field(min_length=1)
    tests_before: dict[str, Any] = Field(default_factory=dict)
    tests_after: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    # §33 evidence envelope (Phase 13): client completion, build/test/lint
    # observations, human correction flag, and cost — all optional.
    client_status: str | None = None
    lint_passed: bool | None = None
    build_passed: bool | None = None
    changed_files: int | None = None
    tool_calls: int | None = None
    human_corrected: bool | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_usd: float | None = None


class CapabilityDetail(BaseModel):
    """GET /v1/capabilities/{id}: identity + immutable version history."""

    capability: dict[str, Any]
    versions: list[dict[str, Any]]


class EvaluationRubricIn(BaseModel):
    """Declarative rubric for the evaluation service (V4 §57)."""

    criteria: list[str] = Field(min_length=1)
    aggregation: Literal["all", "any", "majority"] = "all"


class EvaluationRequestIn(BaseModel):
    """POST /v1/evaluations body (V4 §57; §33.1 external_evaluator)."""

    route_run_id: str = Field(min_length=1)
    bundle_id: str = Field(min_length=1)
    task_summary: str = Field(min_length=1, max_length=8000)
    rubric: EvaluationRubricIn
