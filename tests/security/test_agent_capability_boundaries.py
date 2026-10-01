"""HarnessKernel capability boundary (harness.md §11, ADR-009/012/013).

A model inside an agent run can only ASK for capabilities (CapabilityRequest);
what loads is decided by the registry. Through the kernel's registry-backed
ACIClient, a capability that is quarantined (never promoted), staged only,
revoked, or production-active but ineligible (license-blocked) must never be
activated — not via search, not via a direct resolve, and not via a forged
selection that carries the correct digest. A positive control proves the
probes are not vacuous.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aci.adapters.inbound.rest.wiring import Container
from aci.adapters.outbound.agent_capabilities import (
    RegistryCapabilityClientFactory,
    RegistrySelection,
)
from aci.adapters.outbound.postgres.assessments import (
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.adapters.outbound.postgres.repositories import SqlAlchemyArtifactStore
from aci.config import Settings
from aci.control_plane.promotion.service import PromotionService
from aci.domain.capability.errors import DomainError
from aci.domain.provenance.models import (
    LicenseAssessment,
    LicensePermissions,
    SecurityAssessment,
)
from aci.domain.runtime.actions import CapabilityRequest
from aci.providers.skills.ingestion import SkillIngestionService
from aci.runtime.capability_runtime import CapabilityRuntime

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 1, tzinfo=UTC)
DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Registry:
    def __init__(
        self,
        ingestion: SkillIngestionService,
        promotion: PromotionService,
        licenses: SqlAlchemyLicenseAssessmentRepository,
        securities: SqlAlchemySecurityAssessmentRepository,
        artifacts: SqlAlchemyArtifactStore,
        tmp_path: Path,
    ) -> None:
        self.ingestion = ingestion
        self.promotion = promotion
        self.licenses = licenses
        self.securities = securities
        self.artifacts = artifacts
        self.tmp_path = tmp_path

    def ingest(self, token: str) -> str:
        name = uid("sec-kernel")
        src = self.tmp_path / name
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {token} {token} boundary probe\n"
            f"version: 1.0.0\n---\n\nbody {token}\n",
            encoding="utf-8",
        )
        return self.ingestion.ingest_local(src, now=NOW).capability_id

    def open_gates(self, cap: str) -> None:
        self.licenses.put_assessment(
            LicenseAssessment(
                assessment_id=uid("lic"),
                capability_id=cap,
                version="1.0.0",
                license_identifier="MIT",
                permissions=LicensePermissions(can_redistribute=True),
                assessed_at=NOW,
                assessed_by="sec-suite",
            )
        )
        self.securities.put_assessment(
            SecurityAssessment(
                assessment_id=uid("sec"),
                capability_id=cap,
                version="1.0.0",
                scan_status="passed",
                scanned_at=NOW,
                scanner_version="sec-suite",
            )
        )

    def promote(self, cap: str, channel: str = "production") -> None:
        self.open_gates(cap)
        self.promotion.promote(cap, "1.0.0", channel, approved_by="sec-suite", now=NOW)  # type: ignore[arg-type]

    def forged_selection(self, cap: str) -> RegistrySelection:
        """A selection with the CORRECT entry digest — only the gate can stop it."""
        artifact = self.artifacts.get_artifact(cap, "1.0.0")
        assert artifact is not None
        entry = next(f for f in artifact.files if f.path == "SKILL.md")
        return RegistrySelection(
            capability_id=cap,
            version="1.0.0",
            payload_ref=f"skill://{cap}@1.0.0/SKILL.md",
            digest=f"sha256:{entry.sha256}",
            estimated_context_tokens=10,
            route_run_id="forged",
            bundle_id="forged",
        )


@pytest.fixture()
def registry(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo: SqlAlchemyLicenseAssessmentRepository,
    security_repo: SqlAlchemySecurityAssessmentRepository,
    artifact_store: SqlAlchemyArtifactStore,
    tmp_path: Path,
) -> Registry:
    return Registry(ingestion, promotion, license_repo, security_repo, artifact_store, tmp_path)


@pytest.fixture()
def clients(engine: object, tmp_path: Path) -> RegistryCapabilityClientFactory:
    """The Container's own registry client deps; 1-item bundles so other
    tests' production skills (blobs in THEIR tmp stores) never crowd in."""
    container = Container(
        Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects"))
    )
    return RegistryCapabilityClientFactory(
        container.route_service,
        container.releases,
        container.capabilities,
        container.artifacts,
        container.objects,
        max_items=1,
    )


def _assert_never_activated(
    clients: RegistryCapabilityClientFactory, registry: Registry, cap: str, token: str
) -> None:
    client = clients()
    selected = {s.capability_id for s in client.search(CapabilityRequest(objective=token))}
    assert cap not in selected, f"{cap} was routed to the kernel"
    with pytest.raises(DomainError):
        client.resolve(cap, "1.0.0")
    runtime = CapabilityRuntime(clients())
    with pytest.raises(DomainError):
        runtime.activate(registry.forged_selection(cap), run_id=uid("run"))


def test_positive_control_eligible_production_skill_activates(
    clients: RegistryCapabilityClientFactory, registry: Registry
) -> None:
    token = uid("glimmerwick")
    cap = registry.ingest(token)
    registry.promote(cap)
    client = clients()
    selections = client.search(CapabilityRequest(objective=token))
    assert [s.capability_id for s in selections] == [cap]
    runtime = CapabilityRuntime(client)
    activation, payload = runtime.activate(selections[0], run_id=uid("run"))
    assert activation.capability_id == cap
    assert activation.digest == f"sha256:{hashlib.sha256(payload).hexdigest()}"


def test_quarantined_capability_is_never_activated(
    clients: RegistryCapabilityClientFactory, registry: Registry
) -> None:
    token = uid("quarantok")
    cap = registry.ingest(token)  # ingested = quarantined, no release pointer
    _assert_never_activated(clients, registry, cap, token)


def test_staging_only_capability_is_never_activated(
    clients: RegistryCapabilityClientFactory, registry: Registry
) -> None:
    token = uid("stagetok")
    cap = registry.ingest(token)
    registry.promote(cap, "staging")  # ADR-013: automation's ceiling, never served
    _assert_never_activated(clients, registry, cap, token)


def test_revoked_capability_is_never_activated(
    clients: RegistryCapabilityClientFactory, registry: Registry
) -> None:
    token = uid("revoketok")
    cap = registry.ingest(token)
    registry.promote(cap)
    registry.promotion.revoke(cap, "production", approved_by="sec-suite")
    _assert_never_activated(clients, registry, cap, token)


def test_license_blocked_production_capability_is_never_activated(
    clients: RegistryCapabilityClientFactory, registry: Registry
) -> None:
    """Production-ACTIVE but ineligible: eligibility runs before retrieval
    (ADR-009), so the kernel can never be handed it."""
    token = uid("licblock")
    cap = registry.ingest(token)
    registry.promote(cap)
    registry.licenses.put_assessment(
        LicenseAssessment(
            assessment_id=uid("lic"),
            capability_id=cap,
            version="1.0.0",
            license_identifier="LicenseRef-proprietary",
            permissions=LicensePermissions(can_redistribute=False),
            assessed_at=NOW + timedelta(days=1),  # latest wins
            assessed_by="sec-suite",
        )
    )
    _assert_never_activated(clients, registry, cap, token)


def test_revocation_after_selection_blocks_activation(
    clients: RegistryCapabilityClientFactory, registry: Registry
) -> None:
    """A selection issued before revocation cannot load after it (§46: live)."""
    token = uid("latervoke")
    cap = registry.ingest(token)
    registry.promote(cap)
    client = clients()
    selections = client.search(CapabilityRequest(objective=token))
    assert [s.capability_id for s in selections] == [cap]
    registry.promotion.revoke(cap, "production", approved_by="sec-suite")
    with pytest.raises(DomainError):
        CapabilityRuntime(client).activate(selections[0], run_id=uid("run"))
