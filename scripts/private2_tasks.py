"""The E2C private-knowledge fixture set (--set private2) — 7 fixtures.

E2B (ADR-014 amendment 24) measured the private-skill axis on 2 fixtures
(atlas/meridian, scripts/private_tasks.py): the standard as a plain repo
file (arm F) passed 9/20 where the registry + §14 router preload (arm Bp)
passed 19/20 — but 2 cases is one author's shape. E2C replicates the
measurement on 7 NEW, varied fixtures built by four parallel builders
(jobs e2c-build-a/b/c/d, merged into this branch):

    scripts/private2_tasks_a.py — cairn money (named), drawbridge rollout (indirect)
    scripts/private2_tasks_b.py — palisade redaction (named), cairn sunset (indirect)
    scripts/private2_tasks_c.py — vellum order ids (named), ferry consumer retry (indirect)
    scripts/private2_tasks_d.py — harbor infra config (indirect)

This module ONLY assembles them (the builder modules own the content);
run_hbench/verify_private_fixtures/e2b_setup_registry import it so no
shared file needs to know the builders' names. Same contract as
private_tasks.py: each fixture's required knowledge did NOT exist before
2026-10-02 (a fictional internal standard invented with the fixture), is
documented ONLY in the fixture's private SKILL.md (never a workspace
file), and is pinned in the tests as sha256 DIGESTS over canonical traces.
Each fixture additionally carries the E2C axis metadata:

    "directness" — "named" (the prompt names the standard, as a ticket
    would) or "indirect" (the prompt says only that an internal policy
    exists; the standard's name and title words appear NOWHERE in it).
    "markers" — the standard's distinctive vocabulary; it exists ONLY in
    the skill, never in the files or the prompt (the knowledge gate).

§34 caveat applies to any round on this set: small n, author-built
fixtures, one model — directional only.
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from private2_tasks_a import FIXES as _FIXES_A  # noqa: E402
from private2_tasks_a import TASKS as _TASKS_A  # noqa: E402
from private2_tasks_b import FIXES as _FIXES_B  # noqa: E402
from private2_tasks_b import TASKS as _TASKS_B  # noqa: E402
from private2_tasks_c import FIXES as _FIXES_C  # noqa: E402
from private2_tasks_c import TASKS as _TASKS_C  # noqa: E402
from private2_tasks_d import FIXES as _FIXES_D  # noqa: E402
from private2_tasks_d import TASKS as _TASKS_D  # noqa: E402

#: All 7 fixtures, in builder order (a, b, c, d) — the --set private2 pack.
PRIVATE2_TASKS: list[dict[str, Any]] = [*_TASKS_A, *_TASKS_B, *_TASKS_C, *_TASKS_D]

#: fixture name -> the private skill whose knowledge the fix needs.
PRIVATE2_INTENDED_SKILLS: dict[str, str] = {
    str(task["name"]): str(task["skill_id"]) for task in PRIVATE2_TASKS
}

#: fixture name -> the prompt's directness ("named" / "indirect") — the
#: E2C secondary axis: does routing survive a prompt that never names the
#: standard?
PRIVATE2_DIRECTNESS: dict[str, str] = {
    str(task["name"]): str(task["directness"]) for task in PRIVATE2_TASKS
}

#: The known root-cause fix per fixture (whole-file replacements, the
#: verify_private_fixtures.py (file, None, content) format) — merged from
#: the builder modules. Repo data, NEVER workspace files.
PRIVATE2_FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    **_FIXES_A,
    **_FIXES_B,
    **_FIXES_C,
    **_FIXES_D,
}

assert set(PRIVATE2_FIXES) == set(PRIVATE2_INTENDED_SKILLS), "every fixture needs a fix"
