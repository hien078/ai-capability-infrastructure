"""HarnessKernel ↔ registry wiring over the live DB (harness.md §11, ADR-014).

The REST Container wires a registry-backed ACIClient into every agent run:
a model CapabilityRequest goes through the SAME §14 routing use case
/v1/routes runs (route_runs telemetry included), the selected production
skill's SKILL.md is read from the content-addressed store, digest-verified,
and activated — CAPABILITY_LOADED reaches the run's event stream.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from aci.adapters.inbound.rest.agent_run_wiring import build_agent_run_service
from aci.adapters.inbound.rest.wiring import Container, agent_capability_policy
from aci.adapters.outbound.agent_capabilities import (
    CLIENT_TYPE,
    RegistryCapabilityClientFactory,
)
from aci.adapters.outbound.postgres.assessments import (
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.config import Settings
from aci.control_plane.promotion.service import PromotionService
from aci.domain.provenance.models import (
    LicenseAssessment,
    LicensePermissions,
    SecurityAssessment,
)
from aci.domain.runtime.actions import (
    CapabilityRequest,
    FinalCandidate,
    ToolCall,
    ToolCallBatchAction,
)
from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.events import EventEnvelope
from aci.domain.runtime.state import BudgetLedger, RunState, RuntimeStateSnapshot, TaskState
from aci.domain.runtime.stop_reason import RunStatus
from aci.domain.runtime.subtask import AcceptanceCriterion, SubtaskContract
from aci.providers.skills.ingestion import SkillIngestionService
from aci.runtime.capability_runtime import CapabilityRuntime
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.event_bus import CAPABILITY_LOADED, EventBus
from aci.runtime.model_gateway import FakeModelGateway
from aci.runtime.profiles import runtime_spec_for
from aci.runtime.run_controller import CAPABILITY_TOOL_ID

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 1, tzinfo=UTC)
DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def unique_token() -> str:
    """ONE routing token shared with nothing else in the DB. The shared live
    DB keeps every earlier run's production skill (blobs in THEIR tmp object
    stores); a ``word-<hex>`` token tokenizes into a word all those leftovers
    share plus a suffix, and once a leftover outranked this run's skill
    (route_run 2026-10-01: 0.500 vs 0.466) the 1-item bundle failed
    activation on its missing blob. No separator, no common words."""
    return f"qz{uuid.uuid4().hex}"


def ingest_and_promote(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo: SqlAlchemyLicenseAssessmentRepository,
    security_repo: SqlAlchemySecurityAssessmentRepository,
    tmp_path: Path,
    token: str,
) -> str:
    name = uid("kernel-skill")
    src = tmp_path / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {token} {token}\n"
        f"version: 1.0.0\n---\n\nsteps for {token}\n",
        encoding="utf-8",
    )
    cap = ingestion.ingest_local(src, now=NOW).capability_id
    license_repo.put_assessment(
        LicenseAssessment(
            assessment_id=uid("lic"),
            capability_id=cap,
            version="1.0.0",
            license_identifier="MIT",
            permissions=LicensePermissions(can_redistribute=True),
            assessed_at=NOW,
            assessed_by="kernel-wiring",
        )
    )
    security_repo.put_assessment(
        SecurityAssessment(
            assessment_id=uid("sec"),
            capability_id=cap,
            version="1.0.0",
            scan_status="passed",
            scanned_at=NOW,
            scanner_version="kernel-wiring",
        )
    )
    promotion.promote(cap, "1.0.0", "production", approved_by="kernel-wiring", now=NOW)
    return cap


@pytest.fixture()
def container(engine: object, tmp_path: Path) -> Container:
    """SAME object-store root the ingestion fixture writes blobs to."""
    return Container(Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects")))


@pytest.fixture()
def clients(container: Container) -> RegistryCapabilityClientFactory:
    """The Container's registry client, narrowed to a 1-item bundle: the shared
    live DB holds other tests' production skills whose blobs live in THEIR
    tmp object stores, so a wider bundle would (correctly) fail activation on
    a missing blob. The unique token ranks this test's skill first."""
    return RegistryCapabilityClientFactory(
        container.route_service,
        container.releases,
        container.capabilities,
        container.artifacts,
        container.objects,
        max_items=1,
        route_runs=container.route_runs,
    )


def _snapshot() -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id=uid("run"), status=RunStatus.RUNNING, created_at=NOW),
        task=TaskState(task_id="t", objective="o"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
    )


def test_container_wires_a_registry_capability_runtime(container: Container) -> None:
    """The REST composition root never leaves the kernel on the honest null."""
    runtime = container.agent_run_service._capability_factory.build()
    assert isinstance(runtime, CapabilityRuntime)
    assert isinstance(container.agent_capability_clients, RegistryCapabilityClientFactory)
    # The kernel-side selection policy comes from settings (ACI_AGENT_CAPABILITY_*).
    assert container.agent_capability_clients.selection_policy == agent_capability_policy(
        container.settings
    )


def test_registry_client_routes_resolves_and_activates_production_skill(
    container: Container,
    clients: RegistryCapabilityClientFactory,
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo: SqlAlchemyLicenseAssessmentRepository,
    security_repo: SqlAlchemySecurityAssessmentRepository,
    tmp_path: Path,
) -> None:
    token = unique_token()
    cap = ingest_and_promote(ingestion, promotion, license_repo, security_repo, tmp_path, token)

    client = clients()
    selections = client.search(CapabilityRequest(objective=token))
    picked = {s.capability_id: s for s in selections}
    assert cap in picked
    sel = picked[cap]

    # Same telemetry stream as /v1/routes: the route run is durable + attributed.
    run = container.route_runs.get_route_run(cast(Any, sel).route_run_id)
    assert run is not None
    assert run.client_type == CLIENT_TYPE
    assert run.task_text == token
    # The kernel's selection decision reads the rerank score back from the
    # persisted route run (live DB JSON round-trip).
    (decision,) = client.decisions
    assert decision.scores_available
    kept = next(e for e in decision.kept if e.capability_id == cap)
    assert kept.score == next(
        r["score"] for r in run.stages["reranked"] if r["capability_id"] == cap
    )

    # resolve → bytes from the content-addressed store; the digest matches
    # the pinned manifest entry, so activation succeeds.
    data, digest = client.resolve(cap, "1.0.0")
    assert f"steps for {token}".encode() in data
    assert digest == sel.digest

    runtime = CapabilityRuntime(clients())
    activations = runtime.handle_request(CapabilityRequest(objective=token), _snapshot())
    assert cap in {a.capability_id for a in activations}
    # The digest-verified SKILL.md text rides in the activation (run state).
    loaded = next(a for a in activations if a.capability_id == cap)
    assert f"steps for {token}" in loaded.instructions


@pytest.mark.parametrize("via", ["json_action", "function_call"])
def test_kernel_run_emits_capability_loaded_from_registry(
    via: str,
    clients: RegistryCapabilityClientFactory,
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo: SqlAlchemyLicenseAssessmentRepository,
    security_repo: SqlAlchemySecurityAssessmentRepository,
    tmp_path: Path,
) -> None:
    """End to end through the kernel: a scripted model asks for a capability
    — as the text-JSON action, or (what real function-calling models do,
    2026-10-01) by calling the request_capability tool; the REST wiring's
    capability factory serves it from the registry."""
    token = unique_token()
    cap = ingest_and_promote(ingestion, promotion, license_repo, security_repo, tmp_path, token)

    class _Factory:
        def __init__(self, obj: object) -> None:
            self._obj = obj

        def build(self) -> object:
            return self._obj

    class _NullTools:
        def handle_request(self, request: object, snapshot: object) -> list[object]:
            return []

    ask: CapabilityRequest | ToolCallBatchAction = CapabilityRequest(objective=token)
    if via == "function_call":
        ask = ToolCallBatchAction(
            calls=[
                ToolCall(
                    call_id="call_cap",
                    tool_id=CAPABILITY_TOOL_ID,
                    arguments={"objective": token},
                )
            ]
        )
    model = FakeModelGateway([ask, FinalCandidate(summary="done")])
    bus = EventBus()
    seen: list[EventEnvelope] = []
    bus.subscribe(seen.append)  # the service frees bus history at run end
    service = AgentRunService(
        model_gateway_factory=cast(
            ModelGatewayFactory,
            _Factory(model),
        ),
        tool_executor_factory=cast(ModelGatewayFactory, _Factory(_NullTools())),
        capability_runtime_factory=build_agent_run_service(
            Settings(database_url=DB_URL), capability_client_factory=clients
        )._capability_factory,
        context_engine_factory=cast(
            ModelGatewayFactory, _Factory(ContextEngine(ContextBudget(total_tokens=60_000)))
        ),
        workspace_root=None,
        runs_root=tmp_path / "runs",
        process_prefixes=[],
        event_bus=bus,
    )
    contract = SubtaskContract(
        task_id=uid("run"),
        objective="recalibrate",
        global_context="",
        constraints=[],
        acceptance_criteria=[AcceptanceCriterion(criterion_id="ac-1", description="done")],
        requested_profile="researcher",
        budget=None,
        created_at=datetime.now(UTC),
    )
    result = service.run(contract, runtime_spec_for("researcher"), max_turns=2)

    loaded = [
        e.payload["capability_id"]
        for e in seen
        if e.run_id == result.run_id and e.event_type == CAPABILITY_LOADED
    ]
    assert loaded and cap in loaded
    # The registry skill's SKILL.md text reaches the NEXT model request,
    # framed as third-party reference material (harness.md §11.5).
    assert len(model.requests) >= 2
    assert f"steps for {token}" not in "\n".join(m.content for m in model.requests[0].messages)
    block = next(m.content for m in model.requests[1].messages if f"steps for {token}" in m.content)
    assert f"<<<BEGIN SKILL REFERENCE {cap}@1.0.0" in block
    # The registry plane is wired, so the function tool is offered.
    assert CAPABILITY_TOOL_ID in {t.tool_id for t in model.requests[0].tools}
    if via == "function_call":
        tool_results = {
            m.tool_call_id: m.content for m in model.requests[1].messages if m.role == "tool"
        }
        assert tool_results["call_cap"].startswith(f"Capability request result: loaded {cap}")
