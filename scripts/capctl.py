"""capctl — governance CLI for the capability supply chain (auto2.md §79, §113).

One tool for every human gate in the acquisition pipeline:

    capctl review list              — everything needing attention
    capctl review show <capability> — full evidence for one capability
    capctl candidates list          — raw candidates from the file store
    capctl candidates show <id>     — one candidate's proposal + lifecycle
    capctl source approve <cand>    — candidate → source_approved (§12.2)
    capctl source reject <cand>     — candidate → rejected_source
    capctl fetch approve <cand>     — candidate → approved_for_fetch
    capctl promotion propose <cap> <ver> — build the §24 evidence bundle
    capctl promotion approve <cap> <ver>  — human gate → production pointer
    capctl revoke <cap>             — flip a production release to revoked

Every mutating command records WHO decided (§80 audit) and goes through
the REAL services — PromotionService gates, the §12.2 lifecycle
validator — never around them (ADR-012).

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/capctl.py <command> ...
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_ROOT = REPO_ROOT / "data" / "candidates"

sys.path.insert(0, str(REPO_ROOT / "src"))

from aci.adapters.outbound.postgres.assessments import (  # noqa: E402
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.adapters.outbound.postgres.base import make_engine, make_session_factory  # noqa: E402
from aci.adapters.outbound.postgres.repositories import (  # noqa: E402
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.source_records import (  # noqa: E402
    SqlAlchemySourceRecordRepository,
)
from aci.application.propose_promotion import PromotionProposalBuilder  # noqa: E402
from aci.config import Settings  # noqa: E402
from aci.control_plane.promotion.service import PromotionService  # noqa: E402
from aci.domain.acquisition.models import CandidateRecord, advance_candidate  # noqa: E402
from aci.domain.capability.errors import DomainError  # noqa: E402


def _load_candidate(candidate_id: str) -> CandidateRecord:
    path = CANDIDATE_ROOT / f"{candidate_id}.json"
    if not path.is_file():
        print(f"candidate {candidate_id} not found in {CANDIDATE_ROOT}", file=sys.stderr)
        raise SystemExit(1)
    return CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))


def _save_candidate(record: CandidateRecord) -> None:
    path = CANDIDATE_ROOT / f"{record.proposal.candidate_id}.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# candidates (file store)
# ---------------------------------------------------------------------------


def cmd_candidates_list(_args: argparse.Namespace) -> int:
    if not CANDIDATE_ROOT.is_dir():
        print("no candidate store yet (data/candidates/ is empty)")
        return 0
    for path in sorted(CANDIDATE_ROOT.glob("*.json")):
        record = CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        print(
            f"{record.proposal.candidate_id:40s} [{record.status}] "
            f"{record.proposal.source_repo} @ {record.proposal.observed_revision[:8]}"
        )
    return 0


def cmd_candidates_show(args: argparse.Namespace) -> int:
    record = _load_candidate(args.candidate_id)
    print(json.dumps(json.loads(record.model_dump_json()), indent=2))
    return 0


# ---------------------------------------------------------------------------
# lifecycle transitions (§12.2) — through the domain validator
# ---------------------------------------------------------------------------


def _transition(args: argparse.Namespace, to_status: str) -> int:
    record = _load_candidate(args.candidate_id)
    now = datetime.now(UTC)
    try:
        advanced = advance_candidate(
            record,
            to_status,
            decided_by=args.by,
            decided_at=now,  # type: ignore[arg-type]
            rejection_reason=args.reason,
        )
    except ValueError as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1
    _save_candidate(advanced)
    print(f"{advanced.proposal.candidate_id}: {record.status} -> {advanced.status} (by {args.by})")
    return 0


# ---------------------------------------------------------------------------
# promotion (§24-25) — through the REAL gates
# ---------------------------------------------------------------------------


def _services() -> tuple:
    settings = Settings()
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    capabilities = SqlAlchemyCapabilityRepository(sessions)
    releases = SqlAlchemyReleaseRepository(sessions)
    source_records = SqlAlchemySourceRecordRepository(sessions)
    licenses = SqlAlchemyLicenseAssessmentRepository(sessions)
    securities = SqlAlchemySecurityAssessmentRepository(sessions)
    promotion = PromotionService(
        capabilities=capabilities,
        releases=releases,
        source_records=source_records,
        licenses=licenses,
        securities=securities,
    )
    return capabilities, promotion


def cmd_promotion_propose(args: argparse.Namespace) -> int:
    capabilities, promotion = _services()
    builder = PromotionProposalBuilder(capabilities, promotion)
    proposal = builder.build(args.capability, args.version)
    print(proposal.summary_line())
    print(json.dumps(json.loads(proposal.model_dump_json()), indent=2))
    return 0


def cmd_promotion_approve(args: argparse.Namespace) -> int:
    _, promotion = _services()
    try:
        release = promotion.promote(
            args.capability,
            args.version,
            "production",
            approved_by=args.by,
            now=datetime.now(UTC),
        )
    except DomainError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(
        f"promoted {args.capability}@{args.version} -> production "
        f"(approved_by={release.approved_by})"
    )
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    _, promotion = _services()
    try:
        release = promotion.revoke(args.capability, "production", approved_by=args.by)
    except DomainError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(f"revoked {args.capability} production release (status={release.status})")
    return 0


# ---------------------------------------------------------------------------
# review — delegate to review_queue.py (already built)
# ---------------------------------------------------------------------------


def cmd_review(args: argparse.Namespace) -> int:
    import subprocess

    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "review_queue.py"), *args.rest],
        check=False,
    ).returncode


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("candidates", help="raw candidate store (data/candidates/)")
    csub = p.add_subparsers(dest="candidates_command", required=True)
    csub.add_parser("list").set_defaults(func=cmd_candidates_list)
    cs = csub.add_parser("show")
    cs.add_argument("candidate_id")
    cs.set_defaults(func=cmd_candidates_show)

    for name, to_status, help_ in (
        ("source", "source_approved", "approve an UNREVIEWED source (§12.2)"),
        ("fetch", "approved_for_fetch", "unlock the deterministic fetcher"),
    ):
        p = sub.add_parser(name, help=help_)
        ap = p.add_subparsers(dest="action", required=True)
        app = ap.add_parser("approve")
        app.add_argument("candidate_id")
        app.add_argument("--by", required=True, help="who approves (§80 audit)")
        app.add_argument("--reason", default=None)
        app.set_defaults(func=lambda a, s=to_status: _transition(a, s))

    p = sub.add_parser("reject", help="reject a candidate (choose --kind)")
    pr = p.add_subparsers(dest="action", required=True)
    for kind in ("source", "license", "security", "schema", "duplicate", "quality"):
        rj = pr.add_parser(kind)
        rj.add_argument("candidate_id")
        rj.add_argument("--by", required=True)
        rj.add_argument("--reason", default=None)
        rj.set_defaults(
            func=lambda a, k=f"rejected_{kind}": _transition(a, k),
        )

    p = sub.add_parser("promotion", help="§24 proposal + §25 human gate")
    psub = p.add_subparsers(dest="promotion_command", required=True)
    pp = psub.add_parser("propose")
    pp.add_argument("capability")
    pp.add_argument("version")
    pp.set_defaults(func=cmd_promotion_propose)
    pa = psub.add_parser("approve")
    pa.add_argument("capability")
    pa.add_argument("version")
    pa.add_argument("--by", required=True)
    pa.set_defaults(func=cmd_promotion_approve)

    p = sub.add_parser("revoke", help="flip a production release to revoked")
    p.add_argument("capability")
    p.add_argument("--by", required=True)
    p.set_defaults(func=cmd_revoke)

    p = sub.add_parser("review", help="delegate to review_queue.py")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser(
        "week",
        help="the weekly human gate: everything auto-approved + everything held",
    )
    p.set_defaults(func=cmd_week)

    args = parser.parse_args()
    return args.func(args)


def cmd_week(_args: argparse.Namespace) -> int:
    """The ONE human gate of the weekly cycle: what the automation moved
    (known tier, approved_for_fetch), what it HELD for you (unreviewed
    sources), and what is sitting in staging awaiting promotion."""

    if not CANDIDATE_ROOT.is_dir():
        print("no candidate store")
        return 0
    auto = held = 0
    print("== WEEKLY REVIEW (the one human gate) ==")
    print("\n-- auto-approved for fetch (known tier — gates run in pipeline) --")
    for path in sorted(CANDIDATE_ROOT.glob("*.json")):
        record = CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if record.status == "approved_for_fetch":
            auto += 1
            print(
                f"  {record.proposal.candidate_id:44s} "
                f"{record.proposal.source_path} (by {record.decided_by})"
            )
    print("\n-- HELD: unreviewed sources (capctl source approve <id> --by you) --")
    for path in sorted(CANDIDATE_ROOT.glob("*.json")):
        record = CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if record.status == "proposed":
            held += 1
            print(
                f"  {record.proposal.candidate_id:44s} "
                f"{record.proposal.source_repo} conf={record.proposal.discovery_confidence}"
            )
    print(f"\n{auto} auto-approved, {held} held for human review")
    print("promote anything in staging: capctl promotion propose <cap> <ver>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
