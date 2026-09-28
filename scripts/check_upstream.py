"""Check pinned corpus sources against upstream (V2 upstream watcher, plan §55).

The corpus pins every source repo at an exact commit (§21: identity is
capability_id + version, provenance carries the pin). Pins go stale as
upstream moves; this script makes staleness visible instead of letting the
catalog silently drift behind upstream.

For each distinct (source_repository, commit_sha) in ``source_records`` it
asks the remote for its current HEAD (``git ls-remote`` — read-only, no
clone) and reports:

    current  — the pin is the remote HEAD
    stale    — upstream moved; lists the affected capabilities and the new
               HEAD so a human can decide whether to re-review + re-ingest
               (ingestion is NEVER automatic: license detection §24 and
               the pattern scanner must re-run on the new content)

Exit code 1 when anything is stale (CI-able: schedule this script and let
a non-zero exit open a review ticket).

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/check_upstream.py
"""

import subprocess
import sys
from collections import defaultdict

from sqlalchemy import create_engine, text

from aci.config import Settings


def _remote_head(repo: str) -> str | None:
    """Current HEAD sha of the repo's default branch (read-only)."""
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


def main() -> int:
    settings = Settings()
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT source_repository, commit_sha, capability_id "
                "FROM source_records "
                "WHERE source_repository IS NOT NULL AND commit_sha IS NOT NULL "
                "ORDER BY source_repository, commit_sha"
            )
        ).fetchall()

    by_pin: dict[tuple[str, str], list[str]] = defaultdict(list)
    for repo, sha, cid in rows:
        by_pin[(repo, sha)].append(cid)

    print(f"{len(by_pin)} pinned source(s) across {len({r for r, _ in by_pin})} repo(s)\n")
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
        print(f"         affects: {', '.join(sorted(caps))}")
        print(
            "         re-review required before re-ingest: license detection (§24) "
            "and the pattern scanner re-run on new content — never auto-promote."
        )

    if stale:
        print(f"\n{stale} stale/unreachable source(s)", file=sys.stderr)
        return 1
    print("\nAll pinned sources are current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
