"""Declare curated corpus relations (V2 richer relations, plan §55).

Measured trigger (dev-31 run `dev-31-corpus36-semantic`, 2026-09-28): 4/31
full_pipeline bundles co-selected a near-duplicate skill pair, and in 3 of
those the bundle was FULL (5/5 items) while an annotated-relevant skill
missed the cut — redundancy occupied budget that relevant content could
have used. That is the evidence the deferral was waiting for.

Near-duplicates are interchangeable methodology docs from different
sources — semantic cosine over the TRUSTED routing docs (bge-small, the
router's own signal) separates them cleanly from cross-domain noise
(lexical jaccard could not: the true pair tdd~test-driven-development sat
at 0.250, BELOW unrelated pairs):

    0.855  debugging             ~ systematic-debugging
    0.851  debugging             ~ diagnosing-bugs
    0.836  tdd                   ~ test-driven-development
    0.809  diagnosing-bugs        ~ systematic-debugging

Pairs that are similar but NOT interchangeable are deliberately NOT
declared (receiving- vs requesting-code-review are distinct workflows;
writing-plans vs writing-skills are distinct artifacts). Curation judgment
+ measured evidence, recorded per pair in the relation metadata.

CONFLICTS_WITH semantics (§18): the resolver drops the lower-ranked side,
freeing a bundle slot for the next-ranked candidate. The reranker's
ordering stays authoritative; resolution never reorders, only drops.

Idempotent: deterministic relation_ids, safe to re-run. Version
constraints are None (= any version) — the conflict is between the
capabilities' interchangeable docs, not specific releases.

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/declare_relations.py [--dry-run]
"""

import argparse
import sys

from sqlalchemy import create_engine

from aci.adapters.outbound.postgres.base import make_session_factory
from aci.adapters.outbound.postgres.relations import SqlAlchemyRelationRepository
from aci.config import Settings
from aci.domain.capability.models import CapabilityRelation

# (a, b, cosine, why) — one declared relation per pair; the resolver checks
# both directions, so a single row covers either ranking order.
CONFLICT_PAIRS: list[tuple[str, str, float, str]] = [
    (
        "debugging",
        "systematic-debugging",
        0.855,
        "interchangeable debugging-methodology docs (seb1n vs superpowers)",
    ),
    (
        "debugging",
        "diagnosing-bugs",
        0.851,
        "interchangeable debugging-methodology docs (seb1n vs ok-skills)",
    ),
    (
        "tdd",
        "test-driven-development",
        0.836,
        "interchangeable TDD-discipline docs (ok-skills vs superpowers)",
    ),
    (
        "diagnosing-bugs",
        "systematic-debugging",
        0.809,
        "interchangeable debugging-methodology docs (ok-skills vs superpowers)",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print without writing")
    parser.add_argument(
        "--remove",
        action="store_true",
        help="remove the curated relations instead (revert the experiment)",
    )
    args = parser.parse_args()

    sessions = make_session_factory(create_engine(Settings().database_url))
    repo = SqlAlchemyRelationRepository(sessions)

    if args.remove:
        return _remove(repo, args.dry_run)

    existing = {(r.source_capability_id, r.target_capability_id) for r in _all_relations(repo)}
    for a, b, cosine, why in CONFLICT_PAIRS:
        relation_id = f"rel-conflict-{a}-{b}"
        if (a, b) in existing or (b, a) in existing:
            print(f"exists   {a} conflicts_with {b}")
            continue
        relation = CapabilityRelation(
            relation_id=relation_id,
            source_capability_id=a,
            target_capability_id=b,
            relation="conflicts_with",
            metadata={
                "curated_by": "declare_relations.py",
                "evidence": f"semantic cosine {cosine} over trusted routing docs",
                "why": why,
                "trigger": "dev-31-corpus36-semantic: 4/31 bundles co-selected a "
                "near-dup pair; 3 were full bundles with a missed relevant skill",
            },
        )
        if args.dry_run:
            print(f"would add {a} conflicts_with {b} ({cosine}, {why})")
            continue
        repo.put_relation(relation)
        print(f"added    {a} conflicts_with {b} ({cosine}, {why})")
    return 0


def _remove(repo: SqlAlchemyRelationRepository, dry_run: bool) -> int:
    """Delete exactly the curated relation_ids (never touches other rows)."""
    from sqlalchemy import delete

    from aci.adapters.outbound.postgres.orm import CapabilityRelationRow

    sessions = repo._sessions  # noqa: SLF001 - operational revert, same session factory
    removed = 0
    with sessions() as session, session.begin():
        for a, b, _cosine, _why in CONFLICT_PAIRS:
            relation_id = f"rel-conflict-{a}-{b}"
            if dry_run:
                print(f"would remove {relation_id}")
                continue
            session.execute(
                delete(CapabilityRelationRow).where(
                    CapabilityRelationRow.relation_id == relation_id
                )
            )
            removed += 1
            print(f"removed  {relation_id}")
    if not dry_run:
        print(f"\n{removed} curated relation(s) removed; routing returns to pre-experiment state.")
    return 0


def _all_relations(repo: SqlAlchemyRelationRepository) -> list[CapabilityRelation]:
    """The repo lists per-source; iterate over the curated sources."""
    seen: dict[str, CapabilityRelation] = {}
    sources = {p[0] for p in CONFLICT_PAIRS} | {p[1] for p in CONFLICT_PAIRS}
    for source in sources:
        for relation in repo.list_relations(source):
            seen[relation.relation_id] = relation
    return list(seen.values())


if __name__ == "__main__":
    sys.exit(main())
