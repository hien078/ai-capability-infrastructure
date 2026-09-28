"""Upstream watcher v2 (auto.md §27): stale pins → candidate proposals.

Extends check_upstream.py from REPORT-ONLY to PROPOSAL-GENERATING (the
§27 loop): when a pinned source has moved upstream, the watcher creates
one CandidateRecord per affected skill — status ``proposed``,
recommended_action ``fetch_to_quarantine`` — into the candidate store.

What it still NEVER does (ADR-012, §27): fetch, overwrite production,
re-ingest, or promote. The new upstream HEAD is recorded as
``observed_revision`` on the proposal; the deterministic fetcher (§8)
only runs after a human approves the candidate (§7
approved_for_fetch).

Candidates are stored as JSON files under ``data/candidates/``
(gitignored) — auto.md §39: CLI + files are enough for V2, no
candidate DB until the volume demands it.

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/watch_upstream.py \
        [--dry-run]
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text

from aci.config import Settings
from aci.domain.acquisition.models import (
    CandidateProposal,
    CandidateRecord,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_ROOT = REPO_ROOT / "data" / "candidates"


def _remote_head(repo: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "ls-remote", repo, "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"ERROR cannot reach {repo}: {exc}", file=sys.stderr)
        return None
    return out.split("\t", 1)[0] if out else None


def _candidate_id(repo: str, cap: str, sha: str) -> str:
    return f"cand-{cap}-{sha[:8]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()

    settings = Settings()
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT source_repository, commit_sha, capability_id, source_path "
                "FROM source_records "
                "WHERE source_repository IS NOT NULL AND commit_sha IS NOT NULL "
                "ORDER BY source_repository, commit_sha"
            )
        ).fetchall()

    by_pin: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for repo, sha, cid, spath in rows:
        by_pin[(repo, sha)].append((cid, spath))

    print(f"{len(by_pin)} pinned source(s)\n")
    CANDIDATE_ROOT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    proposals: list[CandidateRecord] = []
    stale = 0

    for (repo, pinned), caps in sorted(by_pin.items()):
        remote = _remote_head(repo)
        if remote is None:
            stale += 1
            continue
        if remote == pinned:
            print(f"current  {repo} @ {pinned[:12]} ({len(caps)} capabilities)")
            continue
        stale += 1
        print(f"STALE    {repo}: pinned {pinned[:12]} -> upstream {remote[:12]}")
        for cid, spath in caps:
            proposal = CandidateProposal(
                candidate_id=_candidate_id(repo, cid, remote),
                gap_id=None,
                source_repo=repo,
                source_path=spath or cid,
                observed_revision=remote,
                reason=(
                    f"upstream moved: pinned {pinned[:12]} -> {remote[:12]}; "
                    f"existing production capability {cid} was ingested from "
                    "this repo — the new revision is a re-review candidate "
                    "(§27: new snapshot through the same pipeline, never an "
                    "in-place overwrite)"
                ),
                signals={"source_known": True, "upstream_changed": True},
                discovery_confidence=1.0,  # deterministic observation, not a guess
                recommended_action="fetch_to_quarantine",
                proposed_at=now,
                proposed_by="upstream-watcher",
            )
            record = CandidateRecord(proposal=proposal)
            proposals.append(record)
            print(f"  -> candidate {proposal.candidate_id} [{record.status}] fetch_to_quarantine")

    if args.dry_run:
        print(f"\n(dry-run: {len(proposals)} proposal(s) not written)")
        return 1 if stale else 0

    written = 0
    for record in proposals:
        out = CANDIDATE_ROOT / f"{proposal.candidate_id}.json"
        if out.exists():
            continue  # idempotent: same upstream sha -> same candidate id
        out.write_text(json.dumps(json.loads(record.model_dump_json()), indent=2), encoding="utf-8")
        written += 1
    print(f"\n{written} candidate proposal(s) written to {CANDIDATE_ROOT}")
    print(
        "next: a human reviews (capctl-style approve) -> approved_for_fetch -> "
        "the deterministic fetcher clones the NEW revision into quarantine; "
        "license §24 + scanner re-run on the new bytes; never auto-promote."
    )
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
