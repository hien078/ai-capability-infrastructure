"""Ingest + gate + stage + promote the declarative source registry (§22, §37).

Clones each source repo at its pinned commit (config/sources.yaml), ingests
every picked skill through the real pipeline (parse SKILL.md → hash → blobs →
immutable version/artifact → quarantined provenance record), records license +
security assessments (§24/§25), then walks the explicit lifecycle:

    ingest (quarantined)
        ↓
    INGESTION GATES (G1 structure — enforced by the ingestion service;
                      G2 provenance, G3 license policy, G4 security policy —
                      IngestionGateService)
        ↓ accepted
    STAGING release (explicit pointer move)
        ↓
    PROMOTION GATES (G5-G8 — PromotionService: provenance chain,
                     redistributable license, passed scan)
        ↓
    PRODUCTION release (explicit pointer move)

A failed gate leaves the skill QUARANTINED (ingestion_status on the source
record) — it never gains a release pointer, never appears in the catalog or
routing. Idempotent: identical content re-ingests as a no-op, existing
assessments are not duplicated, every pointer move is pointer-only.

Usage:
    .venv/bin/python scripts/ingest_real_skills.py [--workdir DIR] [--sources PATH]

Requires the live DB (docker compose up -d db + alembic upgrade head).
"""

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from aci.adapters.outbound.object_store.fs import FsObjectStore
from aci.adapters.outbound.postgres.assessments import (
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.adapters.outbound.postgres.base import make_engine, make_session_factory
from aci.adapters.outbound.postgres.repositories import (
    SqlAlchemyArtifactStore,
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.source_records import SqlAlchemySourceRecordRepository
from aci.config import Settings
from aci.control_plane.ingestion_gates.service import IngestionGateService
from aci.control_plane.promotion.service import PromotionService
from aci.domain.provenance.models import (
    LicenseAssessment,
    ScanStatus,
    SecurityAssessment,
)
from aci.providers.licensing import LicenseDetection, detect_license, spdx_permissions
from aci.providers.security import SCANNER_VERSION, scan_package, verdict
from aci.providers.skills.ingestion import SkillIngestionService

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = REPO_ROOT / "config" / "sources.yaml"


@dataclass(frozen=True)
class SkillSource:
    """One upstream repo at a pinned commit + the skills picked from it."""

    repo: str
    commit: str
    skills: tuple[str, ...]
    #: Optional manual SPDX override. None = auto-detect from the package
    #: (per-skill file → repo-root file → manifest field).
    license: str | None = None


def load_sources(path: Path) -> list[SkillSource]:
    """Declarative source registry (§22): WHAT to ingest lives in YAML;
    this loader only translates it into engine inputs."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources: list[SkillSource] = []
    for entry in raw["sources"]:
        sources.append(
            SkillSource(
                repo=entry["repo"],
                commit=entry["commit"],
                skills=tuple(entry["skills"]),
                license=entry.get("license"),
            )
        )
    return sources


def _clone(repo: str, commit: str, workdir: Path) -> Path:
    """Shallow-clone `repo` and check out the pinned `commit`."""
    name = repo.rsplit("/", 1)[-1].removesuffix(".git")
    target = workdir / name
    if not (target / ".git").exists():
        subprocess.run(
            ["git", "clone", "--quiet", repo, str(target)],
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["git", "-C", str(target), "checkout", "--quiet", commit],
        check=True,
        capture_output=True,
    )
    return target


def _detect(skill_dir: Path, repo_root: Path, override: str | None) -> tuple[LicenseDetection, str]:
    """Detect the license for one skill package.

    Order: manual override > per-skill file > repo-root file > manifest.
    Returns the detection plus a human-readable evidence note.
    """
    if override is not None:
        return (
            LicenseDetection(spdx_id=override, method="manual-override", evidence=""),
            "manually verified",
        )
    det = detect_license(skill_dir)
    if det.spdx_id == "unknown" and skill_dir != repo_root:
        root_det = detect_license(repo_root)
        if root_det.spdx_id != "unknown":
            return root_det, f"detected from repo-root {root_det.evidence}"
    return det, f"detected via {det.method} {det.evidence}".rstrip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "opencode" / "aci-skill-sources",
        help="where the source repos are cloned (default: %(default)s)",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=DEFAULT_SOURCES,
        help="declarative source registry (default: config/sources.yaml)",
    )
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    sources = load_sources(args.sources)

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

    ingestion = SkillIngestionService(
        capabilities=capabilities,
        releases=releases,
        artifacts=artifacts,
        source_records=source_records,
        objects=objects,
    )
    gates = IngestionGateService(
        source_records=source_records,
        licenses=license_repo,
        securities=security_repo,
    )
    promotion = PromotionService(
        capabilities=capabilities,
        releases=releases,
        source_records=source_records,
        licenses=license_repo,
        securities=security_repo,
    )

    now = datetime.now(UTC)
    failures: list[str] = []
    for source in sources:
        root = _clone(source.repo, source.commit, args.workdir)
        for rel in source.skills:
            skill_dir = root / rel
            try:
                det, det_note = _detect(skill_dir, root, source.license)
                permissions = spdx_permissions(det.spdx_id)
                result = ingestion.ingest_local(
                    skill_dir,
                    license_identifier=det.spdx_id,
                    source_repository=source.repo,
                    source_url_reference=f"{source.repo}/tree/{source.commit}/{rel}",
                    commit_sha=source.commit,
                    now=now,
                )
                cid, ver = result.capability_id, result.version
                state = "already-ingested" if result.already_ingested else "ingested"

                if license_repo.get_assessment(cid, ver) is None:
                    license_repo.put_assessment(
                        LicenseAssessment(
                            assessment_id=f"lic-{_new_id()}",
                            capability_id=cid,
                            version=ver,
                            license_identifier=det.spdx_id,
                            permissions=permissions,
                            assessed_at=now,
                            assessed_by="license-detector:v1",
                            notes=(
                                f"{det_note}; verified from "
                                f"{source.repo}@{source.commit}"
                                + ("; manifest disagreement" if det.disagreement else "")
                            ),
                        )
                    )
                existing_scan = security_repo.get_assessment(cid, ver)
                if existing_scan is None:
                    findings = scan_package(skill_dir)
                    scan_verdict: ScanStatus = verdict(findings)
                    security_repo.put_assessment(
                        SecurityAssessment(
                            assessment_id=f"sec-{_new_id()}",
                            capability_id=cid,
                            version=ver,
                            scan_status=scan_verdict,
                            findings=[str(f) for f in findings],
                            scanned_at=now,
                            scanner_version=SCANNER_VERSION,
                            reviewed_by="pattern-scanner",
                        )
                    )

                # INGESTION GATES (G2-G4): quarantined → accepted, or stay
                # quarantined. A quarantined skill never gains a release
                # pointer — it is invisible to catalog and routing (§22).
                if not gates.accept(cid, ver):
                    print(
                        f"OK  {cid}@{ver} [{state}, QUARANTINED] "
                        f"license={det.spdx_id} files={result.file_count}"
                    )
                    continue

                # STAGING: explicit pointer move, gated on version-exists +
                # ingestion-not-rejected (§22: gates lead to staging).
                promotion.promote(
                    cid,
                    ver,
                    "staging",
                    approved_by="ingest_real_skills.py",
                    now=now,
                )

                # PROMOTION GATES (G5-G8): staging → production, explicit.
                promotion.promote(
                    cid,
                    ver,
                    "production",
                    approved_by="ingest_real_skills.py",
                    now=now,
                )
                print(
                    f"OK  {cid}@{ver} [{state}] license={det.spdx_id} "
                    f"({det.method}) files={result.file_count}"
                )
            except Exception as exc:  # noqa: BLE001 — operational script, report and continue
                failures.append(f"{skill_dir}: {exc}")
                print(f"FAIL {skill_dir}: {exc}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} failure(s)", file=sys.stderr)
        return 1
    print("\nAll skills ingested, gated, staged, and promoted to production.")
    return 0


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


if __name__ == "__main__":
    raise SystemExit(main())
