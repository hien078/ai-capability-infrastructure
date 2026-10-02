"""Human review queue for governance findings (V2, plan §55).

The automated gates (§24 license detection, pattern scanner) deliberately
never guess: unknown licenses and weak-signal findings are recorded and
left for a human. This script is that human's workflow:

    list                          what needs review, with evidence pointers
    show <capability_id>          full evidence: provenance, license, scan
    approve-license <cid> <ver> <spdx-id> --by NAME [--notes ...] [--promote]
                                  record a human license decision (append-only
                                  audit trail; the latest assessment wins) and
                                  optionally run the gated promotion

Assessments are append-only by assessment_id and the latest one wins, so a
human decision never erases the automated finding it overrides — the audit
trail keeps both. Promotion itself always goes through PromotionService
gates (provenance + license + security); nothing here bypasses a gate.
"""

import argparse
import sys
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.assessments import (
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.adapters.outbound.postgres.base import make_session_factory
from aci.adapters.outbound.postgres.repositories import (
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.source_records import SqlAlchemySourceRecordRepository
from aci.config import Settings
from aci.control_plane.promotion.service import PromotionService
from aci.domain.provenance.models import LicenseAssessment
from aci.providers.licensing import spdx_permissions
from aci.providers.licensing.detector import SPDX_PERMISSIONS


def _engine_and_factories() -> tuple[
    Engine,
    sessionmaker[Session],
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
    SqlAlchemySourceRecordRepository,
]:
    settings = Settings()
    engine = create_engine(settings.database_url)
    sessions = make_session_factory(engine)
    return (
        engine,
        sessions,
        SqlAlchemyLicenseAssessmentRepository(sessions),
        SqlAlchemySecurityAssessmentRepository(sessions),
        SqlAlchemySourceRecordRepository(sessions),
    )


def cmd_list() -> int:
    engine, _, licenses, securities, _ = _engine_and_factories()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.id, c.kind, v.version FROM capabilities c "
                "JOIN capability_versions v ON v.capability_id = c.id "
                "ORDER BY c.id, v.version"
            )
        ).fetchall()
        releases = conn.execute(
            text("SELECT capability_id, version, channel, status FROM capability_releases")
        ).fetchall()

    prod_active = {(r[0], r[1]) for r in releases if r[2] == "production" and r[3] == "active"}
    review: list[tuple[str, str, list[str]]] = []
    for cid, _kind, version in rows:
        reasons: list[str] = []
        lic = licenses.get_assessment(cid, version)
        sec = securities.get_assessment(cid, version)
        if lic is None:
            reasons.append("no-license-assessment")
        elif lic.permissions.unknown_requires_review:
            reasons.append(f"license-review:{lic.license_identifier}")
        elif not lic.permissions.can_redistribute:
            reasons.append(f"non-redistributable:{lic.license_identifier}")
        if sec is None:
            reasons.append("no-security-assessment")
        elif sec.scan_status != "passed":
            reasons.append(f"scan-review:{sec.scan_status}")
        elif sec.findings:
            reasons.append(f"scan-findings:{len(sec.findings)}")
        if (cid, version) not in prod_active:
            reasons.append("not-in-production")
        if reasons:
            review.append((cid, version, reasons))

    if not review:
        print("Review queue is empty — every capability has clean gates.")
        return 0
    print(f"{len(review)} item(s) need review:\n")
    for cid, version, reasons in review:
        print(f"  {cid}@{version}")
        for reason in reasons:
            print(f"    - {reason}")
        print(f"    -> inspect: review_queue.py show {cid}")
    return 0


def cmd_show(capability_id: str) -> int:
    engine, _, licenses, securities, source_records = _engine_and_factories()
    with engine.connect() as conn:
        versions = conn.execute(
            text("SELECT version FROM capability_versions WHERE capability_id = :c"),
            {"c": capability_id},
        ).fetchall()
        releases = conn.execute(
            text(
                "SELECT version, channel, status FROM capability_releases "
                "WHERE capability_id = :c ORDER BY version"
            ),
            {"c": capability_id},
        ).fetchall()
    if not versions:
        print(f"unknown capability {capability_id}", file=sys.stderr)
        return 1

    print(f"== {capability_id} ==")
    for (version,) in versions:
        print(f"\n-- version {version} --")
        for record in source_records.list_source_records(capability_id):
            if record.version != version:
                continue
            print(f"  provenance: {record.source_type} {record.source_path}")
            if record.source_repository:
                print(f"    repo: {record.source_repository} @ {record.commit_sha}")
            if record.source_url_reference:
                print(f"    url:  {record.source_url_reference}")
        lic = licenses.get_assessment(capability_id, version)
        if lic is None:
            print("  license: NO ASSESSMENT")
        else:
            perms = lic.permissions
            print(
                f"  license: {lic.license_identifier} "
                f"(redistribute={perms.can_redistribute}, "
                f"unknown_requires_review={perms.unknown_requires_review})"
            )
            print(f"    assessed_by: {lic.assessed_by}")
            print(f"    notes: {lic.notes or '-'}")
        sec = securities.get_assessment(capability_id, version)
        if sec is None:
            print("  security: NO ASSESSMENT")
        else:
            print(f"  security: {sec.scan_status} ({sec.scanner_version})")
            for finding in sec.findings:
                print(f"    - {finding}")
    print("\n-- releases --")
    for version, channel, status in releases:
        print(f"  {channel}/{status} @ {version}")
    return 0


def cmd_approve_license(
    capability_id: str, version: str, spdx_id: str, reviewer: str, notes: str, promote: bool
) -> int:
    # The human gate's own gate: spdx_permissions() falls back to UNKNOWN for
    # any unrecognized id (can_redistribute=False — promotion still fails
    # closed), but the append-only audit trail would carry a TYPO as if it
    # were a real human decision. Refuse ids the detector cannot resolve.
    if spdx_id not in SPDX_PERMISSIONS:
        known = ", ".join(sorted(SPDX_PERMISSIONS))
        print(
            f"refusing: {spdx_id!r} is not a known SPDX id (known: {known}); a typo "
            "would be recorded as the human decision and still block promotion "
            "(unknown license)",
            file=sys.stderr,
        )
        return 1
    _, sessions, licenses, securities, source_records = _engine_and_factories()
    permissions = spdx_permissions(spdx_id)
    now = datetime.now(UTC)
    licenses.put_assessment(
        LicenseAssessment(
            assessment_id=f"lic-{uuid4().hex[:12]}",
            capability_id=capability_id,
            version=version,
            license_identifier=spdx_id,
            permissions=permissions,
            assessed_at=now,
            assessed_by=f"human:{reviewer}",
            notes=notes or "human review decision",
        )
    )
    print(
        f"recorded human license decision: {capability_id}@{version} -> {spdx_id} "
        f"(redistribute={permissions.can_redistribute}, "
        f"unknown_requires_review={permissions.unknown_requires_review})"
    )
    if not promote:
        print("promotion not requested; run with --promote to attempt it.")
        return 0

    promotion = PromotionService(
        capabilities=SqlAlchemyCapabilityRepository(sessions),
        releases=SqlAlchemyReleaseRepository(sessions),
        source_records=source_records,
        licenses=licenses,
        securities=securities,
    )
    checks = promotion.prerequisites(capability_id, version, "production")
    print("\nPromotion gates:")
    failed = False
    for check in checks:
        mark = "PASS" if check.passed else "FAIL"
        failed = failed or not check.passed
        print(f"  [{mark}] {check.name}: {check.detail}")
    if failed:
        print("\nGates failed — not promoted.", file=sys.stderr)
        return 1
    promotion.promote(capability_id, version, "production", approved_by=f"human:{reviewer}")
    print(f"\nPromoted {capability_id}@{version} to production.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show everything needing review")

    show = sub.add_parser("show", help="full evidence for one capability")
    show.add_argument("capability_id")

    approve = sub.add_parser(
        "approve-license", help="record a human license decision (append-only)"
    )
    approve.add_argument("capability_id")
    approve.add_argument("version")
    approve.add_argument("spdx_id", help="SPDX id, e.g. MIT, Apache-2.0")
    approve.add_argument("--by", required=True, help="reviewer identity")
    approve.add_argument("--notes", default="")
    approve.add_argument("--promote", action="store_true", help="attempt gated promotion after")

    args = parser.parse_args()
    if args.command == "list":
        return cmd_list()
    if args.command == "show":
        return cmd_show(args.capability_id)
    return cmd_approve_license(
        args.capability_id, args.version, args.spdx_id, args.by, args.notes, args.promote
    )


if __name__ == "__main__":
    raise SystemExit(main())
