"""Set up the DENSE experiment registry (rc-bench v2, 2026-10-03).

rc-bench (ADR-014 amendment 30) tied the ACI plugin with OpenCode's native
skills on a 45-skill corpus. This builds the corpus for the follow-up: the
disposable registry copy (e.g. ``aci_e3`` = a pg_dump of ``aci_e2b``) plus

* the 40 dense private standards (``--set private3``: 8 families x 5
  team-scoped variants, scripts/private3_tasks.py), first-party MIT, and
* ~300 PUBLIC, redistributable skills gathered by job corpus-public
  (``--manifest`` + ``--public-dir``; each package gets its source repo's
  LICENSE file copied in from ``--license-dir`` so ``detect_license`` finds
  the evidence — never an override).

Every skill goes through the REAL paths of ``e2b_setup_registry.ingest_one``'s
pipeline: ingest_local (quarantined) -> license assessment (detected from the
package) -> pattern security scan -> ingestion gates -> staging ->
production. A public skill that fails detection, the scan or a gate is
SKIPPED and reported — never forced. Production promotion is lead-authorized
IN THE DISPOSABLE COPY ONLY (ADR-013 still holds for aci_bench).

REFUSES (exit 2) ``aci``, ``aci_bench`` and ``aci_e2b`` (the live E2 copy
other rounds still read).

Usage::

    .venv/bin/python scripts/e3_setup_dense_registry.py \
        --database-url postgresql+psycopg://aci:aci@127.0.0.1:5432/aci_e3 \
        --object-store-root ~/.cache/aci-rc/objects-e3 \
        --manifest data/aci-improvement/mac/corpus-public-manifest.json \
        --public-dir ~/.cache/aci-rc/public-selected \
        --license-dir ~/.cache/aci-rc/public-licenses
"""

import argparse
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2b_setup_registry as e2b  # noqa: E402
from private3_tasks import PRIVATE3_TASKS  # noqa: E402

from aci.domain.provenance.models import LicenseAssessment, SecurityAssessment  # noqa: E402

REFUSED_DATABASES = frozenset({"aci", "aci_bench", "aci_e2b"})
_APPROVED_BY = "e3-setup-dense-registry.py (lead-authorized: disposable experiment copy only)"


def refuse(url: str) -> str | None:
    name = e2b.database_name_from_url(url)
    if name in REFUSED_DATABASES:
        return f"refusing to write {name!r}: only a NEW disposable copy (e.g. aci_e3) is allowed"
    return None


def public_package(entry: dict[str, Any], public_dir: Path, license_dir: Path, work: Path) -> Path:
    """A copy of the selected skill dir with its source repo's LICENSE added
    (unless the skill dir already carries one)."""
    src = public_dir / str(entry["name"])
    package = work / str(entry["name"])
    shutil.copytree(src, package)
    if not any(
        p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING")) for p in package.iterdir()
    ):
        repo_dir = license_dir / str(entry["source_repo"]).replace("/", "_")
        lic = repo_dir / str(entry["license_path"])
        if not lic.is_file():
            raise FileNotFoundError(f"license file missing: {lic}")
        shutil.copyfile(lic, package / "LICENSE")
    return package


def ingest_public(
    entry: dict[str, Any], package: Path, svc: dict[str, Any], now: datetime
) -> tuple[str, str]:
    """The real ingestion path for one public skill. Returns (status, detail)."""
    det = e2b.detect_license(package)
    if det.spdx_id == "unknown":
        return "skip", f"license not detected ({det.method})"
    if det.spdx_id != entry["license_spdx"]:
        return "skip", f"license mismatch: detected {det.spdx_id}, manifest {entry['license_spdx']}"
    try:
        result = svc["ingestion"].ingest_local(
            package,
            license_identifier=det.spdx_id,
            source_repository=str(entry["source_repo"]),
            source_url_reference=f"https://github.com/{entry['source_repo']}/{entry['path_in_repo']}",
            commit_sha=str(entry["commit_sha"]),
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 — a rejected package is a reported skip
        return "skip", f"ingest rejected: {type(exc).__name__}: {str(exc)[:160]}"
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
                assessed_by="e3-setup:repo-license-file",
                notes=f"public skill from {entry['source_repo']}@{entry['commit_sha'][:12]}",
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
        return "skip", f"ingestion gates failed: {failed} (stays quarantined)"
    try:
        svc["promotion"].promote(cid, ver, "staging", approved_by=_APPROVED_BY, now=now)
        svc["promotion"].promote(cid, ver, "production", approved_by=_APPROVED_BY, now=now)
    except Exception as exc:  # noqa: BLE001
        return "skip", f"promotion refused: {str(exc)[:160]}"
    return "ok", f"{cid}@{ver} license={det.spdx_id}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--object-store-root", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--public-dir", type=Path, required=True)
    parser.add_argument("--license-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("data/rc-bench/e3-registry-setup.json"))
    args = parser.parse_args(argv)
    refusal = refuse(args.database_url)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2

    settings = e2b.Settings(
        database_url=args.database_url,
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
    report: dict[str, Any] = {"private3": [], "public": []}
    with tempfile.TemporaryDirectory(prefix="e3-packages-") as tmp:
        root = Path(tmp)
        for task in PRIVATE3_TASKS:
            rec = e2b.ingest_one(
                task,
                ingestion=svc["ingestion"],
                gates=svc["gates"],
                promotion=svc["promotion"],
                license_repo=licenses,
                security_repo=securities,
                capabilities=capabilities,
                workdir=root / "private3",
                now=now,
                source_repository="aci/e3-dense-private-standards",
                source_url_reference="first-party internal standard (e3 dense corpus)",
                experiment_label="e3",
            )
            report["private3"].append(rec["capability_id"])
        manifest = json.loads(args.manifest.read_text())
        for entry in manifest:
            try:
                package = public_package(
                    entry,
                    args.public_dir.expanduser(),
                    args.license_dir.expanduser(),
                    root / "public",
                )
                status, detail = ingest_public(entry, package, svc, now)
            except Exception as exc:  # noqa: BLE001
                status, detail = "skip", f"{type(exc).__name__}: {str(exc)[:160]}"
            report["public"].append({"name": entry["name"], "status": status, "detail": detail})
            print(f"{status:4s} {entry['name']}: {detail}")
    ok = sum(1 for r in report["public"] if r["status"] == "ok")
    print(
        f"\nprivate3 ingested+promoted: {len(report['private3'])}/40; public: {ok}/{len(manifest)}"
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
