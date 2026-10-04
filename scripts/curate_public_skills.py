"""Ingest a hand-picked set of PUBLIC skills into a registry — up to STAGING.

The user-curated path for the operational corpus (2026-10-04): skills from
the corpus-public manifest (job corpus-public: redistributable licenses,
source repo + commit pinned) are ingested through the REAL pipeline — package
(+ the source repo's LICENSE as evidence) -> ingest_local (quarantined) ->
license assessment (detected from the package, must equal the manifest) ->
pattern security scan -> ingestion gates -> STAGING. It stops there:
production is the human gate (ADR-013) — run
``scripts/capctl.py promotion approve <cap> <ver> --by <you>`` per skill.

Usage::

    ACI_DATABASE_URL=... .venv/bin/python scripts/curate_public_skills.py \
        --names a,b,c --manifest data/aci-improvement/mac/corpus-public-manifest.json \
        --public-dir ~/.cache/aci-rc/public-selected --license-dir ~/.cache/aci-rc/public-licenses \
        --object-store-root data/objects
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2b_setup_registry as e2b  # noqa: E402
from e3_setup_dense_registry import public_package  # noqa: E402

from aci.domain.provenance.models import LicenseAssessment, SecurityAssessment  # noqa: E402

_STAGED_BY = "curate_public_skills.py (staging only; production = capctl human gate)"


def stage_one(entry: dict[str, Any], package: Path, svc: dict[str, Any], now: datetime) -> str:
    """Ingest + gate one public skill up to STAGING. Returns ``cap@ver``."""
    det = e2b.detect_license(package)
    if det.spdx_id != entry["license_spdx"]:
        raise SystemExit(
            f"FAIL {entry['name']}: license detected {det.spdx_id!r} != manifest "
            f"{entry['license_spdx']!r} — refusing to guess"
        )
    result = svc["ingestion"].ingest_local(
        package,
        license_identifier=det.spdx_id,
        source_repository=str(entry["source_repo"]),
        source_url_reference=f"https://github.com/{entry['source_repo']}/{entry['path_in_repo']}",
        commit_sha=str(entry["commit_sha"]),
        now=now,
    )
    cid, ver = result.capability_id, result.version
    if svc["licenses"].get_assessment(cid, ver) is None:
        svc["licenses"].put_assessment(
            LicenseAssessment(
                assessment_id=f"lic-{e2b._new_id()}",
                capability_id=cid,
                version=ver,
                license_identifier=det.spdx_id,
                permissions=e2b.spdx_permissions(det.spdx_id),
                assessed_at=now,
                assessed_by="curate-public:repo-license-file",
                notes=f"public skill {entry['source_repo']}@{str(entry['commit_sha'])[:12]}",
            )
        )
    if svc["securities"].get_assessment(cid, ver) is None:
        findings = e2b.scan_package(package)
        svc["securities"].put_assessment(
            SecurityAssessment(
                assessment_id=f"sec-{e2b._new_id()}",
                capability_id=cid,
                version=ver,
                scan_status=e2b.verdict(findings),
                findings=[str(f) for f in findings],
                scanned_at=now,
                scanner_version=e2b.SCANNER_VERSION,
                reviewed_by="pattern-scanner",
            )
        )
    if not svc["gates"].accept(cid, ver):
        failed = [c.name for c in svc["gates"].evaluate(cid, ver)[0] if not c.passed]
        raise SystemExit(f"FAIL {cid}@{ver}: ingestion gates failed {failed} — stays quarantined")
    svc["promotion"].promote(cid, ver, "staging", approved_by=_STAGED_BY, now=now)
    return f"{cid}@{ver}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--public-dir", type=Path, required=True)
    parser.add_argument("--license-dir", type=Path, required=True)
    parser.add_argument("--object-store-root", required=True)
    args = parser.parse_args(argv)
    url = os.environ.get("ACI_DATABASE_URL")
    if not url:
        print("set ACI_DATABASE_URL", file=sys.stderr)
        return 2
    manifest = {e["name"]: e for e in json.loads(args.manifest.read_text())}
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    missing = [n for n in names if n not in manifest]
    if missing:
        print(f"not in manifest: {missing}", file=sys.stderr)
        return 2
    settings = e2b.Settings(
        database_url=url,
        object_store_root=str(Path(args.object_store_root).expanduser()),
        embedder="fastembed",
    )
    sessions = e2b.make_session_factory(e2b.make_engine(settings.database_url))
    capabilities = e2b.SqlAlchemyCapabilityRepository(sessions)
    releases = e2b.SqlAlchemyReleaseRepository(sessions)
    source_records = e2b.SqlAlchemySourceRecordRepository(sessions)
    licenses = e2b.SqlAlchemyLicenseAssessmentRepository(sessions)
    securities = e2b.SqlAlchemySecurityAssessmentRepository(sessions)
    svc: dict[str, Any] = {
        "ingestion": e2b.SkillIngestionService(
            capabilities=capabilities,
            releases=releases,
            artifacts=e2b.SqlAlchemyArtifactStore(sessions),
            source_records=source_records,
            objects=e2b.FsObjectStore(Path(settings.object_store_root)),
        ),
        "gates": e2b.IngestionGateService(
            source_records=source_records, licenses=licenses, securities=securities
        ),
        "promotion": e2b.PromotionService(
            capabilities=capabilities,
            releases=releases,
            source_records=source_records,
            licenses=licenses,
            securities=securities,
        ),
        "licenses": licenses,
        "securities": securities,
    }
    now = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="curate-") as tmp:
        for name in names:
            package = public_package(
                manifest[name],
                args.public_dir.expanduser(),
                args.license_dir.expanduser(),
                Path(tmp),
            )
            print(f"STAGED {stage_one(manifest[name], package, svc, now)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
