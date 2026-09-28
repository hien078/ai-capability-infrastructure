"""Capability Gap Detector (auto.md §3, Level 0 automation — proposal-only).

Reads REAL runtime telemetry (route_runs + bundles) and answers the only
question that justifies looking for a new skill (§3 acceptance):

    "Tại sao chúng ta đang tìm skill mới?"

Never proposes because something "looks interesting": a gap needs
EVIDENCE — repeated routing demand in a task domain with no matching
production skill to serve it. Rules (V1 automation, no ML):

  R1 zero-bundle routes     — the router abstained (ADR-008) on real
                              demand: eligible pool existed but nothing
                              was relevant enough to compose.
  R2 domain-unserved demand  — task text clusters around vocabulary no
                              production skill's trusted document covers
                              (lexical keyword match against the corpus).
  R3 failure concentration  — bundles whose outcome evidence records
                              failure: the skills we DID route did not
                              help (quality gap, not absence gap).

Output: a YAML-ish proposal report to stdout — a HUMAN reads it and
decides whether to open acquisition (§22). This script never ingests,
never promotes, never writes to the registry (ADR-012).

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/detect_gaps.py \
        [--min-routes N] [--domain-topics K]
"""

import argparse
import re
import sys
from collections import Counter

from sqlalchemy import create_engine, text

STOP_VI = {
    "và",
    "của",
    "cho",
    "với",
    "không",
    "một",
    "các",
    "được",
    "trong",
    "từ",
    "này",
    "đó",
    "là",
    "có",
    "để",
    "khi",
    "về",
    "a",
    "an",
    "to",
    "of",
    "and",
    "or",
    "in",
    "is",
    "are",
    "for",
    "with",
    "that",
    "this",
    "it",
    "make",
    "fix",
    "the",
    "test",
    "tests",
    "task",
}
_WORD = re.compile(r"[a-zà-ỹ0-9]{3,}")


def _content_words(s: str) -> list[str]:
    return [w for w in _WORD.findall(s.lower()) if w not in STOP_VI]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default="postgresql+psycopg://aci:aci@localhost:5432/aci_bench"
    )
    parser.add_argument("--min-routes", type=int, default=3, help="min routes to call a gap")
    parser.add_argument("--domain-topics", type=int, default=8, help="top topics to report")
    args = parser.parse_args()

    engine = create_engine(args.database_url)
    with engine.connect() as c:
        # ---- R1: zero-bundle routes (router abstained on real demand) ----
        zero_routes = c.execute(
            text(
                "SELECT task_text, created_at FROM route_runs "
                "WHERE bundle_id IS NULL ORDER BY created_at DESC LIMIT 50"
            )
        ).fetchall()

        # ---- R2: vocabulary demand vs what the corpus can serve ----
        # Every routed task's content words, minus the words the production
        # corpus's trusted documents actually cover.
        routes = c.execute(
            text(
                "SELECT task_text, bundle_id FROM route_runs "
                "WHERE principal_id NOT IN ('benchmark', 'probe') "
                "ORDER BY created_at DESC LIMIT 200"
            )
        ).fetchall()
        corpus_texts = [
            row[0]
            for row in c.execute(
                text(
                    "SELECT ed.text FROM embedding_documents ed "
                    "JOIN capability_releases r ON r.capability_id = ed.capability_id "
                    "AND r.version = ed.version AND r.channel = 'production'"
                )
            )
        ]
        corpus_words: set[str] = set()
        for doc in corpus_texts:
            corpus_words.update(_content_words(doc))

        demand = Counter()
        for task_text, _bundle in routes:
            for w in set(_content_words(task_text)):
                demand[w] += 1
        # A word is "unserved" when it recurs in real tasks but no production
        # skill's trusted document contains it. Common English verbs leak in
        # (find/show/output) — require the word to be non-generic: it must not
        # appear in ANY task's boilerplate, so weight by distinctiveness is
        # approximated by the min-routes threshold plus corpus absence.
        unserved = {w: n for w, n in demand.items() if w not in corpus_words and n >= 2}

        # ---- R3: failure concentration on routed bundles ----
        failures = c.execute(
            text(
                "SELECT b.bundle_id, rr.task_text, count(*) AS fail_verdicts "
                "FROM bundles b JOIN route_runs rr ON rr.route_run_id = b.route_run_id "
                "JOIN outcome_events oe ON oe.bundle_id = b.bundle_id "
                "JOIN outcome_verdicts v ON v.outcome_id = oe.outcome_id "
                "WHERE v.status = 'failure' "
                "GROUP BY b.bundle_id, rr.task_text ORDER BY fail_verdicts DESC LIMIT 20"
            )
        ).fetchall()

    print("== CAPABILITY GAP DETECTOR (proposal-only, auto.md §3) ==")
    print(
        f"telemetry: {len(routes)} recent real-principal routes, "
        f"{len(zero_routes)} zero-bundle routes\n"
    )

    # R1 report
    print(f"-- R1: zero-bundle routes (router abstained): {len(zero_routes)}")
    if zero_routes:
        topics = Counter()
        for task_text, _ in zero_routes:
            for w in _content_words(task_text):
                topics[w] += 1
        print(
            "   abstained-demand vocabulary:",
            ", ".join(f"{w}({n})" for w, n in topics.most_common(args.domain_topics)),
        )
    else:
        print("   none — every real route composed a bundle")

    # R2 report
    print(f"\n-- R2: unserved vocabulary (in demand, not in corpus): {len(unserved)} words")
    for w, n in sorted(unserved.items(), key=lambda x: -x[1])[: args.domain_topics]:
        print(f"   {w}: routed in {n} task(s), no production skill covers it")

    # R3 report
    print(f"\n-- R3: failure concentration: {len(failures)} bundle(s) with failure verdicts")
    for bundle_id, task_text, n in failures[: args.domain_topics]:
        print(f"   {bundle_id}: {n} failure verdict(s) — task: {task_text[:80]!r}")

    # Proposal synthesis (§3: a gap needs evidence, never "looks interesting")
    print("\n== PROPOSAL ==")
    proposals: list[str] = []
    if len(zero_routes) >= args.min_routes:
        proposals.append(
            f"GAP-ABSENCE: {len(zero_routes)} real routes returned ZERO skills — "
            "the corpus has nothing relevant for this demand. "
            "Open acquisition (§22) for the top abstained vocabulary above."
        )
    if unserved:
        top = max(unserved.values())
        if top >= args.min_routes:
            proposals.append(
                f"GAP-VOCABULARY: {len(unserved)} content words recur in real tasks "
                f"but no production skill's trusted document covers them "
                f"(top: {sorted(unserved, key=lambda w: -unserved[w])[:5]}). "
                "Consider targeted acquisition for that domain."
            )
    if failures:
        proposals.append(
            f"GAP-QUALITY: {len(failures)} routed bundle(s) carry failure verdicts — "
            "the skills we DID select did not help. Review whether the routed "
            "skills fit these task families or a better skill exists upstream."
        )
    if not proposals:
        print(
            "no gap proposal — telemetry does not justify acquisition right now "
            "(§3: never propose because it looks interesting)."
        )
    for i, p in enumerate(proposals, 1):
        print(f"{i}. {p}")
    print(
        "\n(human decides: this detector never ingests/promotes/writes — "
        "acquisition stays manual per §22, ADR-012)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
