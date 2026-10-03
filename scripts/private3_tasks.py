"""The dense private corpus (--set private3) — 40 fixtures, 8 families x 5 variants.

rc-bench (ADR-014 amendment 30) found the ACI plugin and OpenCode's native
skills tied at 12/12 on a 45-skill corpus. The open question is whether
ACI's router wins when the corpus is LARGE and DENSE: many near-identical
internal standards that differ only in WHICH team/service they apply to.
Four parallel builders (jobs p3-build-a/b/c/d) each built two families:

    scripts/private3_tasks_a.py — rollout bucketing, money rounding & fiscal periods
    scripts/private3_tasks_b.py — API version retirement headers, log field redaction
    scripts/private3_tasks_c.py — consumer retry policy, config precedence
    scripts/private3_tasks_d.py — document identifiers, rate-limit responses

Within a family the 5 skill descriptions are identical apart from the scope
(team + services); applying a SIBLING variant's rules fails the tests (each
builder pins a 5x5 confusion matrix). Prompts are INDIRECT and name only the
SERVICE the code belongs to. This module ONLY assembles the builders' content.
§34 caveat: author-built fixtures, one model per round — directional only.
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from private3_tasks_a import FIXES as _FIXES_A  # noqa: E402
from private3_tasks_a import TASKS as _TASKS_A  # noqa: E402
from private3_tasks_b import FIXES as _FIXES_B  # noqa: E402
from private3_tasks_b import TASKS as _TASKS_B  # noqa: E402
from private3_tasks_c import FIXES as _FIXES_C  # noqa: E402
from private3_tasks_c import TASKS as _TASKS_C  # noqa: E402
from private3_tasks_d import FIXES as _FIXES_D  # noqa: E402
from private3_tasks_d import TASKS as _TASKS_D  # noqa: E402

#: All 40 fixtures, in builder order (a, b, c, d) — the --set private3 pack.
PRIVATE3_TASKS: list[dict[str, Any]] = [*_TASKS_A, *_TASKS_B, *_TASKS_C, *_TASKS_D]

#: fixture name -> the private skill whose knowledge the fix needs.
PRIVATE3_INTENDED_SKILLS: dict[str, str] = {
    str(task["name"]): str(task["skill_id"]) for task in PRIVATE3_TASKS
}

#: fixture name -> its family (the 5 variants of one kind of standard).
PRIVATE3_FAMILIES: dict[str, str] = {
    str(task["name"]): str(task["family"]) for task in PRIVATE3_TASKS
}

#: The known root-cause fix per fixture (repo data, NEVER workspace files).
PRIVATE3_FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    **_FIXES_A,
    **_FIXES_B,
    **_FIXES_C,
    **_FIXES_D,
}
