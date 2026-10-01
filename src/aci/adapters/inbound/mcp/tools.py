"""Stable MCP tools (plan §29.2; ADR-006): route, report, search — never one per skill.

The tool surface is small and stable by contract; skill *content* travels over
the Skills extension + ``resources/read`` (§29.3), never inside tool results.
Requests are stateless (§29.4): every call builds a fresh §11.1 envelope and
durable state lives in the registry under ``route_run_id``/``bundle_id``.

Domain errors surface as tool execution errors (SEP-1303) carrying the stable
§45 code in their text — the model reads them and self-corrects; the protocol
layer stays clean. Sync functions run on a worker thread (SDK contract), so
blocking SQLAlchemy calls are safe.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from aci.application.report_outcome import ReportOutcomeService, new_outcome_id
from aci.application.route_capabilities import RouteCapabilitiesService
from aci.application.search_capabilities import SearchCapabilitiesService
from aci.domain.capability.errors import DomainError
from aci.domain.capability.models import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    CapabilityKind,
    OutcomeEvidence,
    OutcomeVerdict,
    RouteCapabilitiesCommand,
    SearchCapabilitiesQuery,
    TaskContext,
    VerdictSource,
)
from aci.domain.policy.models import ClientDescriptor, ProtocolDescriptor, RequestContext
from aci.domain.routing.models import CapabilitySearchResult, RouteResult

# Tool argument bounds mirror the command/query models (§11.3) so the SDK's
# argument validation rejects bad input as a ToolError the model can act on.
_TASK_TEXT = Annotated[str, Field(min_length=1, max_length=8000)]
_MAX_ITEMS = Annotated[int, Field(ge=0, le=5)]
_MAX_TOKENS = Annotated[int, Field(ge=0)]
_SEARCH_LIMIT = Annotated[int, Field(ge=1, le=100)]
_QUERY_TEXT = Annotated[str, Field(max_length=2000)]


class VerdictInput(BaseModel):
    """One verdict as an MCP client reports it (§33: multi-source, never a lone flag)."""

    source: VerdictSource
    status: Literal["success", "failure", "unknown"]
    confidence: Literal["high", "medium", "low"] = "low"


_VERDICTS = Annotated[list[VerdictInput], Field(min_length=1)]


def _envelope() -> RequestContext:
    """Fresh §11.1 envelope per call — stateless; ids are the durable handle."""
    return RequestContext(
        request_id=f"req_{uuid4().hex}",
        trace_id=f"trc_{uuid4().hex}",
        principal_id="anonymous",  # V1 MCP is local/unauthenticated (§29.4)
        client=ClientDescriptor(type="mcp-client"),
        protocol=ProtocolDescriptor(type="mcp", version="1"),
    )


def _tool_error(exc: DomainError) -> ToolError:
    """DomainError → ToolError carrying the stable §45 code (SEP-1303)."""
    return ToolError(f"{exc.code.value}: {exc}")


def make_route_tool(service: RouteCapabilitiesService) -> Callable[..., RouteResult]:
    """``route_capabilities``: run the §14 pipeline, return the pinned bundle."""

    def route_capabilities(
        task_text: _TASK_TEXT,
        language: str | None = None,
        frameworks: list[str] | None = None,
        phase: str | None = None,
        max_items: _MAX_ITEMS = 5,
        max_context_tokens: _MAX_TOKENS = DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> RouteResult:
        """Route a task to 0-5 production skills.

        Returns the route run id, the pinned bundle (exact versions + digests),
        and the stage traces. An empty bundle is a valid success — do not
        force skills on low-confidence tasks.
        """
        task = TaskContext(language=language, frameworks=frameworks or [], phase=phase)
        command = RouteCapabilitiesCommand(
            task_text=task_text,
            context=task,
            max_items=max_items,
            max_context_tokens=max_context_tokens,
        )
        envelope = _envelope()
        try:
            return service.route(command, envelope.to_routing_context(task), request=envelope)
        except DomainError as exc:
            raise _tool_error(exc) from exc

    return route_capabilities


def make_search_tool(
    service: SearchCapabilitiesService,
) -> Callable[..., list[CapabilitySearchResult]]:
    """``search_capabilities``: discovery-only listing (§11.2), not routing."""

    def search_capabilities(
        query: _QUERY_TEXT = "",
        kinds: list[CapabilityKind] | None = None,
        domains: list[str] | None = None,
        limit: _SEARCH_LIMIT = 20,
    ) -> list[CapabilitySearchResult]:
        """Search production-active capabilities by kind/domain facets.

        Discovery only: the full eligibility policy runs in ``route_capabilities``.
        """
        kwargs: dict[str, Any] = {
            "query": query,
            "domains": list(domains or []),
            "limit": limit,
        }
        if kinds is not None:
            kwargs["kinds"] = list(kinds)
        try:
            return service.search(SearchCapabilitiesQuery(**kwargs))
        except DomainError as exc:
            raise _tool_error(exc) from exc

    return search_capabilities


def make_report_outcome_tool(service: ReportOutcomeService) -> Callable[..., OutcomeEvidence]:
    """``report_outcome``: attach multi-source evidence to a routed bundle (§33)."""

    def report_outcome(
        route_run_id: str,
        bundle_id: str,
        verdicts: _VERDICTS,
        tests_before: dict[str, Any] | None = None,
        tests_after: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        client_status: str | None = None,
        lint_passed: bool | None = None,
        build_passed: bool | None = None,
        changed_files: int | None = None,
        tool_calls: int | None = None,
        human_corrected: bool | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_usd: float | None = None,
    ) -> OutcomeEvidence:
        """Report what happened after a routed bundle ran.

        Verdicts are multi-source (test_harness, static_analysis, human_review,
        agent_self_report, …) with per-source confidence; ``unknown`` stays
        ``unknown`` — never collapse evidence to a single flag. Build/test/lint
        observations, the human correction flag, cost, and latency travel
        alongside (§33).
        """
        evidence = OutcomeEvidence(
            outcome_id=new_outcome_id(),
            route_run_id=route_run_id,
            bundle_id=bundle_id,
            received_at=datetime.now(UTC),
            verdicts=[
                OutcomeVerdict(source=v.source, status=v.status, confidence=v.confidence)
                for v in verdicts
            ],
            tests_before=dict(tests_before or {}),
            tests_after=dict(tests_after or {}),
            latency_ms=latency_ms,
            client_status=client_status,
            lint_passed=lint_passed,
            build_passed=build_passed,
            changed_files=changed_files,
            tool_calls=tool_calls,
            human_corrected=human_corrected,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_usd=estimated_usd,
        )
        try:
            return service.report(evidence)
        except DomainError as exc:
            raise _tool_error(exc) from exc

    return report_outcome
