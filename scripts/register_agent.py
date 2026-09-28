"""Register a FIRST-PARTY kind=agent capability through the real gates (V3 §56).

An agent is a capability (§8/§32): identity lives in the canonical registry,
not in a parallel agent registry (§10). This script registers one end to end
through the SAME gates third-party content passes (ADR-012):

    package files -> content-addressed blobs -> immutable version (AgentSpec)
    -> artifact manifest -> source record (provenance §23)
    -> license assessment (§24) -> pattern security scan (§25)
    -> release pointer `raw` -> PromotionService.promote(production)

Nothing is bypassed: an unknown license or a failed scan quarantines the
capability in `raw` exactly like third-party content, and promotion runs
through PromotionService so every gate is evaluated and recorded.

Usage:
    .venv/bin/python scripts/register_agent.py \
        --capability-id aci-coder \
        --display-name "ACI Coder" \
        --description "First-party delegated coding agent." \
        --provides python,debugging,testing \
        --package-dir agents/aci-coder

Requires the DB (ACI_DATABASE_URL) at the current migration head.
"""

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from aci.adapters.outbound.object_store.fs import FsObjectStore  # noqa: E402
from aci.adapters.outbound.postgres.assessments import (  # noqa: E402
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.adapters.outbound.postgres.base import (  # noqa: E402
    make_engine,
    make_session_factory,
)
from aci.adapters.outbound.postgres.repositories import (  # noqa: E402
    SqlAlchemyArtifactStore,
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.source_records import (  # noqa: E402
    SqlAlchemySourceRecordRepository,
)
from aci.config import Settings  # noqa: E402
from aci.control_plane.promotion.service import PromotionService  # noqa: E402
from aci.domain.capability.models import (  # noqa: E402
    AgentSpec,
    ArtifactFile,
    Capability,
    CapabilityArtifact,
    CapabilityRelease,
    CapabilityVersion,
)
from aci.domain.provenance.models import LicenseAssessment, SecurityAssessment  # noqa: E402
from aci.domain.skills.models import SourceProvenance  # noqa: E402
from aci.providers.licensing.detector import spdx_permissions  # noqa: E402
from aci.providers.security.scanner import (  # noqa: E402
    SCANNER_VERSION,
    scan_package,
    verdict,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capability-id", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--provides", default="", help="comma-separated provides list")
    parser.add_argument("--package-dir", type=Path, required=True, help="first-party package dir")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--license", default="MIT", help="SPDX id of the package license")
    parser.add_argument(
        "--assessed-by", default="platform-owner", help="who vouches for the license"
    )
    args = parser.parse_args()

    package_dir: Path = args.package_dir
    files = sorted(p for p in package_dir.rglob("*") if p.is_file())
    if not files:
        print(f"FAIL {package_dir}: package has no files", file=sys.stderr)
        return 1

    settings = Settings()
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    capabilities = SqlAlchemyCapabilityRepository(sessions)
    releases = SqlAlchemyReleaseRepository(sessions)
    artifacts = SqlAlchemyArtifactStore(sessions)
    source_records = SqlAlchemySourceRecordRepository(sessions)
    objects = FsObjectStore(Path(settings.object_store_root))
    license_repo = SqlAlchemyLicenseAssessmentRepository(sessions)
    security_repo = SqlAlchemySecurityAssessmentRepository(sessions)
    promotion = PromotionService(
        capabilities=capabilities,
        releases=releases,
        source_records=source_records,
        licenses=license_repo,
        securities=security_repo,
    )

    cid, ver = args.capability_id, args.version
    now = datetime.now(UTC)

    if capabilities.get_version(cid, ver) is not None:
        print(f"OK  {cid}@{ver} [already-registered]")
        return 0

    # 1. package -> content-addressed blobs + manifest (§39)
    entries: list[ArtifactFile] = []
    for path in files:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        objects.put(digest, data)
        entries.append(
            ArtifactFile(
                path=path.relative_to(package_dir).as_posix(),
                sha256=digest,
                size_bytes=len(data),
            )
        )
    package_digest = hashlib.sha256(
        "\n".join(f"{f.path}:{f.sha256}" for f in entries).encode()
    ).hexdigest()

    # 2. registry identity: capability + immutable version (AgentSpec, §8)
    if capabilities.get_capability(cid) is None:
        capabilities.create_capability(Capability(id=cid, kind="agent", created_at=now))
    capabilities.create_version(
        CapabilityVersion(
            capability_id=cid,
            version=ver,
            kind="agent",
            content_digest=f"sha256:{package_digest}",
            created_at=now,
            display_name=args.display_name,
            description=args.description,
            spec=AgentSpec(provides=[p.strip() for p in args.provides.split(",") if p.strip()]),
        )
    )
    artifacts.put_artifact(
        CapabilityArtifact(
            capability_id=cid,
            version=ver,
            package_digest=f"sha256:{package_digest}",
            manifest={"entrypoint": "AGENT.md"},
            files=entries,
        )
    )

    # 3. provenance (§23) — first-party source record
    source_records.add_source_record(
        SourceProvenance(
            record_id=f"src-{uuid4().hex[:12]}",
            capability_id=cid,
            version=ver,
            source_type="first-party",
            source_path=str(package_dir),
            raw_snapshot_digest=f"sha256:{package_digest}",
            license_identifier=args.license,
            ingested_at=now,
            ingestion_tool_version="register_agent.py",
        )
    )

    # 4. license assessment (§24) — first-party content, human-vouched
    permissions = spdx_permissions(args.license)
    license_repo.put_assessment(
        LicenseAssessment(
            assessment_id=f"lic-{uuid4().hex[:12]}",
            capability_id=cid,
            version=ver,
            license_identifier=args.license,
            permissions=permissions,
            assessed_at=now,
            assessed_by=args.assessed_by,
            notes="first-party agent capability, authored in this repository",
        )
    )

    # 5. security scan (§25) — the same pattern scanner third-party content gets
    findings = scan_package(package_dir)
    scan_verdict = verdict(findings)
    security_repo.put_assessment(
        SecurityAssessment(
            assessment_id=f"sec-{uuid4().hex[:12]}",
            capability_id=cid,
            version=ver,
            scan_status=scan_verdict,
            findings=[str(f) for f in findings],
            scanned_at=now,
            scanner_version=SCANNER_VERSION,
            reviewed_by="pattern-scanner",
        )
    )

    # 6. release pointer `raw` (quarantine default), then the REAL gates
    releases.set_release(
        CapabilityRelease(capability_id=cid, version=ver, channel="raw", status="active")
    )
    if not permissions.can_redistribute:
        print(f"OK  {cid}@{ver} [QUARANTINED] license={args.license} not redistributable")
        return 0
    if scan_verdict != "passed":
        print(f"OK  {cid}@{ver} [QUARANTINED] scan={scan_verdict}")
        return 0
    for check in promotion.prerequisites(cid, ver, "production"):
        print(f"gate {check.name}: {'PASS' if check.passed else 'FAIL'} — {check.detail}")
    promotion.promote(cid, ver, "production", approved_by="register_agent.py", now=now)
    print(f"OK  {cid}@{ver} [production] license={args.license} scan={scan_verdict}")
    print(
        "\nSample ACI_AGENT_PROFILES record (wire the executor with "
        "ACI_AGENT_MODEL_BASE_URL):\n"
        f'{{"profiles": [{{"profile_id": "{cid}-default", "version": "1", '
        f'"model_profile": "<model-id>", "skill_policy": {{"required": []}}, '
        '"budget": {"max_tokens": 2048, "max_wall_time_seconds": 300}, '
        '"execution_policy": {"side_effect_class": "read_only"}, '
        f'"created_at": "{now.isoformat()}"}}]}}'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
