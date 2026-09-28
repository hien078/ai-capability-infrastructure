"""Targeted Source Scout (auto2.md §20, §57-59, Phase 20): gap-driven discovery.

Receives a capability gap (from detect_gaps.py or a human) and searches
OUTSIDE the known-source registry for candidate skills. This is the
§3.2 reactive loop's discovery arm.

SECURITY BOUNDARY (§81) — enforced structurally, not by convention:
  scout MAY:     search public repos, read public metadata, emit proposals
  scout MAY NOT: write production DB, touch secrets, execute repo scripts,
                 approve anything, add a source to the trusted registry

BUDGET (§58) — hard stops, not suggestions:
  max_repositories, max_candidates, max_duration — the run ABORTS when
  exhausted; "never send obvious duplicates to expensive stages" (§90)
  applies before any LLM call.

The output is CandidateProposals with trust=unreviewed — the §12.2
lifecycle requires source_approved (human) before ANY fetch.

Usage:
    .venv/bin/python scripts/scout.py --topic "database migration debugging" \
        [--max-repos 10] [--max-candidates 3] [--dry-run]
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aci.domain.acquisition.models import CandidateProposal, CandidateRecord  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_ROOT = REPO_ROOT / "data" / "candidates"

#: §58 default budget — deliberately small: a scout run is a SEARCH,
#: not a crawl. Enough strong candidates found → stop early.
DEFAULT_BUDGET = {
    "max_repositories": 10,
    "max_candidates": 3,
    "max_duration_minutes": 15,
}


def _github_search(query: str, per_page: int = 10) -> list[dict]:
    """Read-only GitHub repository search via the unauthenticated REST
    endpoint (§81: public search only — no token, no secrets, no auth).
    Returns [] on any failure — the scout never crashes the pipeline."""
    url = "https://api.github.com/search/repositories"
    try:
        out = subprocess.run(
            [
                "curl",
                "-sS",
                "-m",
                "30",  # §85 timeout
                "-H",
                "Accept: application/vnd.github+json",
                "-H",
                "User-Agent: aci-scout",
                f"{url}?q={quote_plus(query)}&sort=stars&order=desc&per_page={per_page}",
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if out.returncode != 0:
            return []
        data = json.loads(out.stdout)
        return data.get("items", [])
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True, help="the capability gap topic to search for")
    parser.add_argument("--gap-id", default=None, help="link proposals to a gap record")
    parser.add_argument("--max-repos", type=int, default=DEFAULT_BUDGET["max_repositories"])
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_BUDGET["max_candidates"])
    parser.add_argument("--max-duration", type=int, default=DEFAULT_BUDGET["max_duration_minutes"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    started = time.monotonic()
    budget_seconds = args.max_duration * 60
    now = datetime.now(UTC)
    CANDIDATE_ROOT.mkdir(parents=True, exist_ok=True)

    # §59: search terms from the topic; stars are a SECONDARY signal —
    # we sort by them for determinism but never treat them as trust.
    query = f"{args.topic} agent skill"
    print(f"scout: searching GitHub for {query!r}")
    print(
        f"budget: max {args.max_repos} repos, {args.max_candidates} candidates, "
        f"{args.max_duration} min\n"
    )
    repos = _github_search(query, per_page=args.max_repos)
    if not repos:
        print("no search results (network unavailable or empty query) — scout aborts")
        return 0
    print(f"{len(repos)} repository result(s)\n")

    proposals: list[CandidateRecord] = []
    for repo in repos:
        if time.monotonic() - started > budget_seconds:
            print("budget: duration exhausted — stopping (§58)")
            break
        if len(proposals) >= args.max_candidates:
            print("budget: enough strong candidates found — stopping early (§58)")
            break
        full_name = repo.get("full_name", "")
        stars = repo.get("stargazers_count", 0)
        desc = (repo.get("description") or "")[:120]
        license_ = (repo.get("license") or {}).get("spdx_id") or "unknown"
        pushed = (repo.get("pushed_at") or "")[:10]
        # §59 ranking signals — maintenance + license clarity + relevance;
        # stars are recorded but NOT the decision (§59: secondary).
        print(f"  {full_name} ★{stars} license={license_} pushed={pushed}")
        print(f"    {desc}")
        # heuristic relevance: the topic words in name/description
        topic_words = {w for w in args.topic.lower().split() if len(w) > 3}
        text = f"{full_name} {desc}".lower()
        hits = sum(1 for w in topic_words if w in text)
        if hits == 0:
            print("    -> skipped: topic words absent (relevance floor, §59)")
            continue
        confidence = min(0.9, 0.3 + 0.2 * hits)
        proposal = CandidateProposal(
            candidate_id=f"cand-scout-{full_name.replace('/', '-')}-{now.strftime('%H%M%S')}",
            gap_id=args.gap_id,
            source_repo=repo.get("clone_url", f"https://github.com/{full_name}.git"),
            source_path=".",
            observed_revision=repo.get("pushed_at", "unknown"),
            reason=(
                f"scout search for gap {args.gap_id or '<manual>'}: topic "
                f"{args.topic!r} matches {hits}/{len(topic_words)} words in "
                f"{full_name!r} ({desc!r}); license {license_}; last push {pushed}"
            ),
            signals={
                "source_known": False,  # §12.2: unreviewed — needs source_approved
                "topic_relevant": True,
                "license_clear": license_ != "unknown",
                "maintained_recently": pushed >= "2026",
            },
            discovery_confidence=confidence,
            recommended_action="fetch_to_quarantine",
            proposed_at=now,
            proposed_by="targeted-scout",
        )
        record = CandidateRecord(proposal=proposal)
        proposals.append(record)
        print(
            f"    -> PROPOSED conf={confidence} "
            f"(unreviewed source — capctl source approve required before fetch)"
        )

    if args.dry_run:
        print(f"\n(dry-run: {len(proposals)} proposal(s) not written)")
        return 0
    written = 0
    for record in proposals:
        out = CANDIDATE_ROOT / f"{record.proposal.candidate_id}.json"
        if out.exists():
            continue
        out.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        written += 1
    print(f"\n{written} proposal(s) written to {CANDIDATE_ROOT}")
    print(
        "every proposal is UNREVIEWED (§12.2): capctl source approve <id> --by <you> "
        "is required before the fetcher may clone anything."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
