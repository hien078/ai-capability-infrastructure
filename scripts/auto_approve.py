"""Weekly acquisition cycle (user decision 2026-09-29): scheduled crawl +
tiered auto-approval with ONE remaining human gate.

The policy (negotiated, replaces "no human review"):

  TIER known (anthropics/superpowers/... — content-reviewed sources):
    crawler → auto source+fetch approve → fetcher → quarantine → gates
    → risk LOW → AUTO-STAGING. No human touch until promotion.

  TIER unreviewed (scout finds, unknown repos):
    EVERY gate human: capctl source approve + fetch approve + promotion
    approve. Nothing automatic.

  THE one human gate (both tiers): staging → production via
  capctl promotion approve — with the §24 evidence bundle in front of
  the human's eyes.

ADR-012 stays intact: automation may move a candidate THROUGH quarantine
and gates into STAGING (invisible to runtime), never into production.
Staging is not exposure: routing, catalog, MCP all read production only.

Usage:
    .venv/bin/python scripts/auto_approve.py            # process the store
    .venv/bin/python scripts/auto_approve.py --dry-run
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from aci.domain.acquisition.models import CandidateRecord, advance_candidate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_ROOT = REPO_ROOT / "data" / "candidates"
SOURCES_YAML = REPO_ROOT / "config" / "sources.yaml"


def _known_repos() -> set[str]:
    data = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8"))
    return {
        s["repo"]
        for s in data["sources"]
        if s.get("trust") == "known"
        and s.get("acquisition_policy", {}).get("require_human_source_approval") is False
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    known = _known_repos()
    now = datetime.now(UTC)
    processed = auto_staged = 0

    if not CANDIDATE_ROOT.is_dir():
        print("no candidate store")
        return 0

    for path in sorted(CANDIDATE_ROOT.glob("*.json")):
        record = CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if record.status != "proposed":
            continue
        processed += 1
        repo = record.proposal.source_repo
        is_known = repo in known

        if not is_known:
            print(
                f"  HOLD  {record.proposal.candidate_id}: unreviewed source — human gates required"
            )
            continue

        # Known tier: auto source_approved → approved_for_fetch.
        # The fetcher + gates + risk policy run in the ingestion pipeline;
        # this script only moves the CANDIDATE lifecycle to the fetch point.
        r = record
        for status in ("source_approved", "approved_for_fetch"):
            r = advance_candidate(
                r,
                status,
                decided_by="auto-approve:known-tier",
                decided_at=now,  # type: ignore[arg-type]
            )
        if args.dry_run:
            print(f"  WOULD {record.proposal.candidate_id}: -> approved_for_fetch (known tier)")
            continue
        path.write_text(r.model_dump_json(indent=2), encoding="utf-8")
        auto_staged += 1
        print(f"  AUTO  {record.proposal.candidate_id}: -> approved_for_fetch (known tier)")

    print(
        f"\n{processed} proposed candidate(s), {auto_staged} auto-approved for fetch (known tier)"
    )
    print("unreviewed sources stay HELD for capctl source approve --by <you>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
