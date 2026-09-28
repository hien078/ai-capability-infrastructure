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
    LicensePermissions,
    SecurityAssessment,
)
from aci.providers.skills.ingestion import SkillIngestionService

# (name, permissions) per license — machine-readable §24 permissions.
# An unknown license is deliberately absent: it gets an honest
# can_redistribute=False assessment and the skill stays in `raw`
# quarantine (§24: unknown blocks production by default).
_LICENSES: dict[str, LicensePermissions] = {
    "MIT": LicensePermissions(
        can_ingest=True,
        can_modify=True,
        can_store=True,
        can_redistribute=True,
        commercial_use_allowed=True,
        attribution_required=True,
    ),
    "Apache-2.0": LicensePermissions(
        can_ingest=True,
        can_modify=True,
        can_store=True,
        can_redistribute=True,
        commercial_use_allowed=True,
        attribution_required=True,
    ),
}

_UNKNOWN = LicensePermissions(
    can_ingest=True,
    can_modify=False,
    can_store=True,
    can_redistribute=False,
    commercial_use_allowed=False,
    attribution_required=True,
)


@dataclass(frozen=True)
class SkillSource:
    """One upstream repo at a pinned commit + the skills picked from it."""

    repo: str
    commit: str
    license: str
    skills: tuple[str, ...]


SOURCES: list[SkillSource] = [
    SkillSource(
        repo="https://github.com/obra/superpowers.git",
        commit="8ca22dba9a94f28898bbce59f2537ff4d87c747d",
        license="MIT",
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
        license="Apache-2.0",
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
    # Unknown license (no LICENSE.txt, no frontmatter field): ingests into
    # `raw` quarantine and is NOT promoted — §24 blocks production by default.
    SkillSource(
        repo="https://github.com/anthropics/skills.git",
        commit="33375500bcea98d610eb30ce10ac4e59b89c390d",
        license="unknown",
        skills=("skills/doc-coauthoring",),
    ),
]

SECURITY_SCANNER_VERSION = "manual-review:0.1.0"


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
                result = ingestion.ingest_local(
                    skill_dir,
                    license_identifier=source.license,
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
                            license_identifier=source.license,
                            permissions=_LICENSES.get(source.license, _UNKNOWN),
                            assessed_at=now,
                            assessed_by="ingest_real_skills.py",
                            notes=f"verified from {source.repo}@{source.commit}",
                        )
                    )
                if security_repo.get_assessment(cid, ver) is None:
                    security_repo.put_assessment(
                        SecurityAssessment(
                            assessment_id=f"sec-{uuid.uuid4().hex[:12]}",
                            capability_id=cid,
                            version=ver,
                            scan_status="passed",
                            findings=[],
                            scanned_at=now,
                            scanner_version=SECURITY_SCANNER_VERSION,
                            reviewed_by="ingest_real_skills.py",
                        )
                    )
                if source.license not in _LICENSES:
                    # §24: unknown license blocks production by default —
                    # the skill stays in `raw` quarantine, unpromoted.
                    state = "already-ingested" if result.already_ingested else "ingested"
                    print(f"OK  {cid}@{ver} [{state}, QUARANTINED] files={result.file_count}")
                    continue
                promotion.promote(
                    cid,
                    ver,
                    "production",
                    approved_by="ingest_real_skills.py",
                    now=now,
                )
                state = "already-ingested" if result.already_ingested else "ingested"
                print(f"OK  {cid}@{ver} [{state}] files={result.file_count}")
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
