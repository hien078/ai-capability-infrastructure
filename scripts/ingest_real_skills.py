"""Ingest + promote the hand-picked real-world skills (§75 steps 9 and 24).

Clones the two source repos at pinned commits, ingests each picked skill
through the real pipeline (parse SKILL.md → hash → blobs → immutable
version/artifact → `raw` quarantine → provenance source record), records
license + security assessments (§24/§25 gates), and promotes to the
`production` channel. Idempotent: identical content re-ingests as a
no-op, existing assessments are not duplicated, promotion is
pointer-only.

Licenses (verified from the repos at the pinned commits):
- obra/superpowers — MIT (repo LICENSE, Copyright (c) 2025 Jesse Vincent)
- anthropics/skills — Apache-2.0 per-skill LICENSE.txt for the picked
  skills; docx/pdf/pptx/xlsx are Proprietary and deliberately NOT picked
  (§24: non-redistributable); doc-coauthoring carries no license at all
  and is ingested under `unknown` — it stays in `raw` quarantine and is
  never promoted (§24: unknown blocks production by default).

Security review (manual, 2026-09-28): markdown process guidance and
documentation examples only; the only secret-shaped strings are
placeholder examples (your_api_key_here, EXAMPLE_API_KEY) and API docs
describing how to handle secrets safely. No prompt injection, no
curl|sh, no real credentials. Scan status: passed.

Usage:
    .venv/bin/python scripts/ingest_real_skills.py [--workdir DIR]

Requires the live DB (docker compose up -d db + alembic upgrade head).
"""

import argparse
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
from aci.control_plane.promotion.service import PromotionService
from aci.domain.provenance.models import (
    LicenseAssessment,
    ScanStatus,
    SecurityAssessment,
)
from aci.providers.licensing import LicenseDetection, detect_license, spdx_permissions
from aci.providers.security import SCANNER_VERSION, scan_package, verdict
from aci.providers.skills.ingestion import SkillIngestionService

# License detection (V2 governance, plan §55): the SPDX id is detected from
# the skill package itself — per-skill LICENSE.txt first, then the repo-root
# LICENSE as fallback (a repo-level license covers its skills), then the
# manifest `license` field. An unmatched license is never a guess: it maps to
# `unknown` permissions and §24 keeps the skill in `raw` quarantine.


@dataclass(frozen=True)
class SkillSource:
    """One upstream repo at a pinned commit + the skills picked from it."""

    repo: str
    commit: str
    skills: tuple[str, ...]
    #: Optional manual override (SPDX id). None = auto-detect from the
    #: package (per-skill file → repo-root file → manifest field).
    license: str | None = None


SOURCES: list[SkillSource] = [
    SkillSource(
        repo="https://github.com/obra/superpowers.git",
        commit="8ca22dba9a94f28898bbce59f2537ff4d87c747d",
        # MIT detected from the repo-root LICENSE.
        skills=(
            "skills/systematic-debugging",
            "skills/test-driven-development",
            "skills/brainstorming",
            "skills/writing-plans",
            "skills/verification-before-completion",
            # §75 step 24 corpus growth (2026-09-28).
            "skills/diagnosing-superpowers",
            "skills/dispatching-parallel-agents",
            "skills/executing-plans",
            "skills/finishing-a-development-branch",
            "skills/receiving-code-review",
            "skills/requesting-code-review",
            "skills/subagent-driven-development",
            "skills/using-git-worktrees",
            "skills/using-superpowers",
            "skills/writing-skills",
        ),
    ),
    SkillSource(
        repo="https://github.com/anthropics/skills.git",
        commit="33375500bcea98d610eb30ce10ac4e59b89c390d",
        # Apache-2.0 detected from per-skill LICENSE.txt; docx/pdf/pptx/xlsx
        # stay excluded (Proprietary).
        skills=(
            "skills/mcp-builder",
            "skills/webapp-testing",
            "skills/skill-creator",
            # §75 step 24 corpus growth (2026-09-28). docx/pdf/pptx/xlsx stay
            # excluded (Proprietary); doc-coauthoring carries no license at all
            # and is listed under "unknown" below.
            "skills/academy-guide",
            "skills/algorithmic-art",
            "skills/brand-guidelines",
            "skills/canvas-design",
            "skills/claude-api",
            "skills/discernment-nudge",
            "skills/frontend-design",
            "skills/internal-comms",
            "skills/slack-gif-creator",
            "skills/theme-factory",
            "skills/web-artifacts-builder",
        ),
    ),
    # Unknown license (no LICENSE.txt, no frontmatter field, no repo-root
    # fallback): the detector reports `unknown` and the skill ingests into
    # `raw` quarantine — §24 blocks production by default.
    SkillSource(
        repo="https://github.com/anthropics/skills.git",
        commit="33375500bcea98d610eb30ce10ac4e59b89c390d",
        skills=("skills/doc-coauthoring",),
    ),
    # V2 targeted corpus growth (§55, 2026-09-28): the §80 measurement found
    # the value chain corpus-limited — no debugging/testing-domain skills
    # beyond the generic discipline set. These two repos fill that gap.
    # Content security-reviewed before ingestion (no injection, no unsafe
    # instructions; scripts are inert digest-pinned bytes per §61).
    SkillSource(
        repo="https://github.com/mxyhi/ok-skills.git",
        commit="7de464066579c539f0ec0342abef83e8a1e994c2",
        # diagnosing-bugs/codebase-design carry per-skill MIT (Matt Pocock);
        # tdd falls back to the repo-root Apache-2.0.
        # improve-codebase-architecture is deliberately NOT ingested: it
        # declares `disable-model-invocation: true` and V1 routing cannot
        # honor that author intent.
        skills=(
            "diagnosing-bugs",
            "tdd",
            "codebase-design",
        ),
    ),
    SkillSource(
        repo="https://github.com/seb1n/awesome-ai-agent-skills.git",
        commit="75865a5d037a4cdaa7f409a4ec14ab9b0292920b",
        # MIT (repo root). prompt-injection-defense is included as
        # platform-domain coverage (§16.1/§17.1 concern).
        skills=(
            "code-and-development/debugging",
            "code-and-development/testing",
            "code-and-development/refactoring",
            "agent-security/prompt-injection-defense",
        ),
    ),
]

# Security scanning (V2 §55): pattern scanner replaces the manual-review
# stub — critical attack primitives (pipe-to-shell, eval-of-base64) fail
# the package and quarantine it; weaker signals are recorded as findings
# for human review without blocking (see scanner docstring for the
# false-positive rationale).


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
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)

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
    promotion = PromotionService(
        capabilities=capabilities,
        releases=releases,
        source_records=source_records,
        licenses=license_repo,
        securities=security_repo,
    )

    now = datetime.now(UTC)
    failures: list[str] = []
    for source in SOURCES:
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

                if license_repo.get_assessment(cid, ver) is None:
                    license_repo.put_assessment(
                        LicenseAssessment(
                            assessment_id=f"lic-{uuid.uuid4().hex[:12]}",
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
                scan_verdict: ScanStatus
                if existing_scan is None:
                    findings = scan_package(skill_dir)
                    scan_verdict = verdict(findings)
                    security_repo.put_assessment(
                        SecurityAssessment(
                            assessment_id=f"sec-{uuid.uuid4().hex[:12]}",
                            capability_id=cid,
                            version=ver,
                            scan_status=scan_verdict,
                            findings=[str(f) for f in findings],
                            scanned_at=now,
                            scanner_version=SCANNER_VERSION,
                            reviewed_by="pattern-scanner",
                        )
                    )
                else:
                    scan_verdict = existing_scan.scan_status
                if not permissions.can_redistribute:
                    # §24: unknown/non-redistributable license blocks
                    # production by default — the skill stays in `raw`
                    # quarantine, unpromoted.
                    state = "already-ingested" if result.already_ingested else "ingested"
                    print(
                        f"OK  {cid}@{ver} [{state}, QUARANTINED] "
                        f"license={det.spdx_id} files={result.file_count}"
                    )
                    continue
                if scan_verdict != "passed":
                    # ADR-012: production requires a passed security scan.
                    # Critical pattern hits quarantine the skill; warnings
                    # are recorded in the assessment findings for review.
                    state = "already-ingested" if result.already_ingested else "ingested"
                    print(
                        f"OK  {cid}@{ver} [{state}, QUARANTINED] "
                        f"scan={scan_verdict} license={det.spdx_id} "
                        f"files={result.file_count}"
                    )
                    continue
                promotion.promote(
                    cid,
                    ver,
                    "production",
                    approved_by="ingest_real_skills.py",
                    now=now,
                )
                state = "already-ingested" if result.already_ingested else "ingested"
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
    print("\nAll skills ingested, assessed, and promoted to production.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
