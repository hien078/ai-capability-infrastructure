"""Known-source crawler (auto2.md §18, Phase 18): find NEW skills in trusted repos.

The watcher (watch_upstream.py) tracks pins of ALREADY-INGESTED skills.
This crawler scans the KNOWN sources (config/sources.yaml — trust level
`known`, §4) for artifact groups that are NOT yet in the registry, and
proposes them as candidates — the §3.1 proactive loop at its safest
setting: only trusted sources, only proposals, never ingestion.

Per-source policy (§4): auto_fetch=true means the crawler may clone and
scan; the OUTPUT is still just CandidateProposals in data/candidates/.
Nothing is ingested, nothing is promoted — a human runs capctl.

Usage:
    .venv/bin/python scripts/crawl_known_sources.py [--dry-run] [--source ID]
"""

import argparse
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aci.application.extract_candidates import (  # noqa: E402
    HeuristicBoundaryDetector,
    find_artifact_groups,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_YAML = REPO_ROOT / "config" / "sources.yaml"
CANDIDATE_ROOT = REPO_ROOT / "data" / "candidates"


def _clone(repo: str, commit: str, workdir: Path) -> Path:
    name = repo.rsplit("/", 1)[-1].removesuffix(".git")
    target = workdir / name
    if not (target / ".git").exists():
        subprocess.run(
            ["git", "clone", "--quiet", repo, str(target)],
            check=True,
            capture_output=True,
            timeout=300,  # §85 network timeout
        )
    subprocess.run(
        ["git", "-C", str(target), "checkout", "--quiet", commit],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return target


def _existing_skill_paths() -> set[str]:
    """Skill source-paths already ingested (from the declarative registry
    — the crawler must not re-propose what production already has)."""
    data = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8"))
    paths: set[str] = set()
    for source in data["sources"]:
        for skill_path in source["skills"]:
            paths.add(skill_path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source", help="only scan this source id")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "opencode" / "aci-crawler",
    )
    args = parser.parse_args()

    data = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8"))
    sources = [s for s in data["sources"] if s.get("trust") == "known"]
    if args.source:
        sources = [s for s in sources if s["id"] == args.source]
    existing = _existing_skill_paths()

    args.workdir.mkdir(parents=True, exist_ok=True)
    CANDIDATE_ROOT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    detector = HeuristicBoundaryDetector()
    proposals_written = 0
    scanned = 0

    for source in sources:
        print(f"=== {source['id']} ({source['repo']}) ===")
        tree = _clone(source["repo"], source["commit"], args.workdir)
        scanned += 1
        groups = find_artifact_groups(
            tree,
            source_repo=source["repo"],
            source_path=".",
            observed_revision=source["commit"],
        )
        new_count = 0
        for group in groups:
            # only groups with an entrypoint are boundary-confident (§11);
            # loose files stay for the LLM detector later.
            cands = detector.detect(group, now=now)
            for cand in cands:
                if cand.boundary_confidence < 0.9:
                    continue  # heuristic floor proposes only entrypoint-grade finds
                # is this skill path already ingested?
                rel_dir = cand.primary_files[0].path.rsplit("/", 1)[0]
                if rel_dir in existing:
                    continue
                existing.add(rel_dir)
                new_count += 1
                record_path = CANDIDATE_ROOT / f"{cand.candidate_id}.json"
                if record_path.exists():
                    continue  # idempotent per digest (§74)
                from aci.domain.acquisition.models import (
                    CandidateProposal,
                    CandidateRecord,
                )

                proposal = CandidateProposal(
                    candidate_id=cand.candidate_id,
                    source_repo=source["repo"],
                    source_path=rel_dir,
                    observed_revision=source["commit"],
                    reason=(
                        f"known-source crawl found an entrypoint-grade skill not "
                        f"in the registry: {cand.proposed_name} "
                        f"({len(cand.supporting_files)} supporting files)"
                    ),
                    signals={"source_known": True, "entrypoint_present": True},
                    discovery_confidence=0.9,  # deterministic entrypoint observation
                    recommended_action="fetch_to_quarantine",
                    proposed_at=now,
                    proposed_by="known-source-crawler",
                )
                record = CandidateRecord(proposal=proposal)
                if args.dry_run:
                    print(f"  NEW (dry-run) {cand.proposed_name}: {rel_dir}")
                else:
                    record_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
                    print(f"  NEW {cand.proposed_name}: {rel_dir} -> {record_path.name}")
                    proposals_written += 1
        if new_count == 0:
            print("  no new entrypoint-grade skills")

    print(f"\n{scanned} source(s) scanned, {proposals_written} proposal(s) written")
    print("next: capctl source approve / fetch approve — a human gates every step")
    return 0


if __name__ == "__main__":
    sys.exit(main())
