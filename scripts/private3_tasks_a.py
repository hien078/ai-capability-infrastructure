"""Dense private-corpus fixtures for the rc-bench skill axis (builder "a", 2026-10-03).

rc-bench (ADR-014 amendment 30) measured the REAL OpenCode client with
the ACI plugin (the router injects skills) against OpenCode's NATIVE
skills (the model picks from a name+description list) on a 45-skill
corpus: 12/12 vs 12/12, native cheaper. The open question: does ACI's
router win when the corpus is LARGE and DENSE - many near-identical
internal standards that differ only in WHICH team/service they apply
to? This module is builder "a"'s TWO families of that dense corpus
(10 fixtures; other builders own the other families; a later job
integrates them - this module ships no runner changes and no product
code, and runs no measurement).

    family "rollout-bucketing" - 5 feature-flag rollout bucketing
    standards, one per team (Tessera, Kiln, Sundial, Thistle,
    Obsidian). Same structure and vocabulary class; the concrete
    decisions differ per variant: hash function, mixing token, hash
    material layout, digest slice, bucket count, bucket id format,
    user-id normalization, kill/allow/percentage resolution order,
    verdict vocabulary, percentage scaling.

    family "money-rounding" - 5 money rounding & fiscal period
    standards, one per team (Basalt, Taffeta, Juniper, Anvil,
    Lantern). Same structure; differing decisions: the currency class
    tables (codes, minor digits, rounding modes), unknown-code
    handling (case folding / trimming / the fail-closed exception),
    the rendered amount format (decimal separator, thousands
    separator, negative zero) and the fiscal calendar (start month,
    start/end-year label, monthly vs quarterly, period id format).

Every variant is a separate fictional internal standard of ONE team,
invented with this module (2026-10-03 - no model can carry it),
documented ONLY in the variant's private SKILL.md, and pinned in the
workspace tests as sha256 DIGESTS over canonical traces (the E2B
section 1.5 lesson: every digest spans 10-14 independent decisions
over a large output space - brute force is not viable). The shipped
code embodies the SAME plausible-but-wrong generic convention in
every variant of a family, so a model that loads a SIBLING variant's
skill gets plausibly-close-but-failing code: the confusion matrix
(FIX_i applied to workspace_j) must fail for every i != j and pass
for i = j - pinned by tests/unit/test_private3_tasks_a.py.

All prompts are INDIRECT: each names the SERVICE the code belongs to,
never the standard, its team, or any rule.

section 34 caveat applies to any round on this set: small n,
author-built fixtures, one model - directional only.
"""

import hashlib
import sys
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _private_task(
    name: str,
    family: str,
    files: dict[str, str],
    prompt: str,
    skill_id: str,
    skill: str,
    markers: list[str],
) -> dict[str, Any]:
    """A dense-corpus private fixture: workspace files + task prompt + the
    private SKILL.md text (NOT a workspace file - the model must never see
    it on disk; the capability plane serves it) + the knowledge-gate
    metadata. Every private3 prompt is INDIRECT: it names the SERVICE the
    code belongs to, never the standard, its team, or any rule."""
    return {
        "name": name,
        "family": family,
        "files": files,
        "prompt": prompt,
        "skill_id": skill_id,
        "skill": skill,
        "directness": "indirect",
        "markers": markers,
    }


# ===========================================================================
# Family 1: feature-flag rollout bucketing - 5 teams, 5 variants.
# ===========================================================================
#
# Every variant's standard defines the same three planes (the bucket mix,
# the assignment resolution order, the board line) and differs in >= 2
# concrete decisions that change EVERY test digest. The shipped workspace
# code is the SAME plausible-but-wrong generic convention in all five
# variants (sha256 of "user:flag", strip+lower, mod 10_000, B + 4 digits,
# allow -> kill -> percentage, "<="): close in shape to every sibling,
# correct in none.

_F1_VARIANTS: list[dict[str, Any]] = [
    {
        "team": "Tessera",
        "services": ["mosaic-ui", "grout-api"],
        "home": "grout-api",
        "slug": "tessera-rollout",
        "skill_id": "tessera-rollout-bucketing",
        "hash": "sha256",
        "material": "{flag}|{user}|{salt}",
        "salt": "tss-mix-07",
        "slice_dir": "first",
        "slice_len": 8,
        "buckets": 10_000,
        "prefix": "T",
        "digits": 4,
        "norm": "none",
        "order": ("kill", "allow", "percent"),
        "scale": 100,
        "verdicts": {
            "in": "PLACED",
            "allow": "PLACED-ROSTER",
            "kill": "WITHHELD-KILL",
            "out": "WITHHELD-RATE",
        },
        "flags": ["mosaic-checkout", "grout-search", "tile-picker"],
        "users": [f"t-{i}" for i in range(1, 15)],
        "case_user": "Mira-K",
    },
    {
        "team": "Kiln",
        "services": ["glaze-web", "bisque-api"],
        "home": "bisque-api",
        "slug": "kiln-rollout",
        "skill_id": "kiln-rollout-bucketing",
        "hash": "sha512",
        "material": "{salt}:{flag}:{user}",
        "salt": "kiln-salt-42",
        "slice_dir": "first",
        "slice_len": 13,
        "buckets": 100_000,
        "prefix": "K",
        "digits": 5,
        "norm": "lower",
        "order": ("allow", "kill", "percent"),
        "scale": 1000,
        "verdicts": {
            "in": "FIRED",
            "allow": "FIRED-SEED",
            "kill": "DAMPED-KILL",
            "out": "DAMPED-COOL",
        },
        "flags": ["glaze-preview", "bisque-checkout", "enamel-mixer"],
        "users": [f"k-{i}" for i in range(1, 15)],
        "case_user": "Owen-B",
    },
    {
        "team": "Sundial",
        "services": ["gnomon-api", "shadow-web"],
        "home": "gnomon-api",
        "slug": "sundial-rollout",
        "skill_id": "sundial-rollout-bucketing",
        "hash": "blake2b",
        "material": "{user}#{flag}#{salt}",
        "salt": "sundial-dial-03",
        "slice_dir": "first",
        "slice_len": 10,
        "buckets": 1_000,
        "prefix": "S",
        "digits": 3,
        "norm": "none",
        "order": ("kill", "percent", "allow"),
        "scale": 10,
        "verdicts": {
            "in": "LIT",
            "allow": "LIT-NOON",
            "kill": "ECLIPSED",
            "out": "SHADOWED",
        },
        "flags": ["gnomon-forecast", "shadow-timeline", "dial-pad"],
        "users": [f"sd-{i}" for i in range(1, 15)],
        "case_user": "Nadia-F",
    },
    {
        "team": "Thistle",
        "services": ["burr-api", "prairie-web"],
        "home": "burr-api",
        "slug": "thistle-rollout",
        "skill_id": "thistle-rollout-bucketing",
        "hash": "md5",
        "material": "{flag}/{user}/{salt}",
        "salt": "thistle-grit-11",
        "slice_dir": "last",
        "slice_len": 8,
        "buckets": 3_600,
        "prefix": "TH",
        "digits": 4,
        "norm": "strip-lower",
        "order": ("percent", "allow", "kill"),
        "scale": 36,
        "verdicts": {
            "in": "BLOOMED",
            "allow": "BLOOMED-ROOT",
            "kill": "PRUNED",
            "out": "BURRED",
        },
        "flags": ["burr-filter", "prairie-feed", "prairie-milk"],
        "users": [f"th-{i}" for i in range(1, 15)],
        "case_user": "Iris-P",
    },
    {
        "team": "Obsidian",
        "services": ["flake-api", "onyx-web"],
        "home": "flake-api",
        "slug": "obsidian-rollout",
        "skill_id": "obsidian-rollout-bucketing",
        "hash": "sha1",
        "material": "{salt}::{flag}::{user}",
        "salt": "obsidian-core-19",
        "slice_dir": "first",
        "slice_len": 16,
        "buckets": 50_400,
        "prefix": "O",
        "digits": 5,
        "norm": "strip",
        "order": ("allow", "percent", "kill"),
        "scale": 504,
        "verdicts": {
            "in": "CLEAVED",
            "allow": "CLEAVED-VEIN",
            "kill": "SHATTERED",
            "out": "UNCUT",
        },
        "flags": ["flake-render", "onyx-console", "shard-mirror"],
        "users": [f"ob-{i}" for i in range(1, 15)],
        "case_user": "Ravi-T",
    },
]


def _f1_norm(spec: dict[str, Any], user_id: str) -> str:
    if spec["norm"] == "lower":
        return user_id.lower()
    if spec["norm"] == "strip-lower":
        return user_id.strip().lower()
    if spec["norm"] == "strip":
        return user_id.strip()
    return user_id


def _f1_bucket_number(spec: dict[str, Any], flag_key: str, user_id: str) -> int:
    """The reference bucket recipe (the standard's mix), used to compute the
    canonical traces the workspace tests pin."""
    user = _f1_norm(spec, user_id)
    material = spec["material"].format(flag=flag_key, user=user, salt=spec["salt"])
    digest = getattr(hashlib, spec["hash"])(material.encode("utf-8")).hexdigest()
    if spec["slice_dir"] == "last":
        text = digest[-spec["slice_len"] :]
    else:
        text = digest[: spec["slice_len"]]
    return int(text, 16) % spec["buckets"]


def _f1_bucket_of(spec: dict[str, Any], flag_key: str, user_id: str) -> str:
    return f"{spec['prefix']}{_f1_bucket_number(spec, flag_key, user_id):0{spec['digits']}d}"


def _f1_assign(spec: dict[str, Any], config: dict[str, Any], user_id: str) -> str:
    """The reference resolution order: rules evaluated in the standard's
    order, the first that fires wins, the percentage rule fires only when
    the bucket qualifies, a user where no rule fires is the out verdict."""
    for step in spec["order"]:
        if step == "kill" and config.get("kill"):
            return spec["verdicts"]["kill"]
        if step == "allow" and user_id in config.get("allow", []):
            return spec["verdicts"]["allow"]
        if step == "percent" and (
            _f1_bucket_number(spec, config["key"], user_id)
            < config.get("percent", 0) * spec["scale"]
        ):
            return spec["verdicts"]["in"]
    return spec["verdicts"]["out"]


def _f1_bucket_scenarios(spec: dict[str, Any]) -> list[list[tuple[str, str]]]:
    flags = spec["flags"]
    users = spec["users"]
    case_user = spec["case_user"]
    padded = f" {users[6]} "
    return [
        [(flags[0], user) for user in users[:12]],
        [(flags[1], user) for user in users[:9]]
        + [(flags[1], case_user), (flags[1], case_user.lower()), (flags[1], case_user.upper())],
        [(flags[2], user) for user in users[2:14]],
        [
            (flags[0], case_user),
            (flags[0], case_user.lower()),
            (flags[0], padded),
            (flags[2], users[0]),
            (flags[2], users[1]),
            (flags[1], users[12]),
            (flags[1], users[13]),
            (flags[0], users[12]),
            (flags[0], users[13]),
            (flags[2], case_user.upper()),
            (flags[1], padded),
            (flags[2], users[6]),
        ],
    ]


def _f1_board_scenarios(spec: dict[str, Any]) -> list[tuple[dict[str, Any], list[str]]]:
    flags = spec["flags"]
    users = spec["users"]
    case_user = spec["case_user"]
    return [
        ({"key": flags[0], "kill": False, "allow": [], "percent": 50}, users[:10]),
        (
            {"key": flags[0], "kill": False, "allow": [users[2], users[8]], "percent": 10},
            users[:10],
        ),
        (
            {"key": flags[0], "kill": True, "allow": [users[2], users[8]], "percent": 100},
            users[:10],
        ),
        (
            {"key": flags[1], "kill": False, "allow": [case_user], "percent": 0},
            users[:8] + [case_user, case_user.lower()],
        ),
        ({"key": flags[2], "kill": False, "allow": [], "percent": 0}, users[:8]),
        ({"key": flags[2], "kill": False, "allow": [], "percent": 100}, users[:8]),
    ]


def _f1_bucket_text(spec: dict[str, Any], pairs: list[tuple[str, str]]) -> str:
    lines = []
    for flag, user in pairs:
        lines.append(f"{flag}|{user}|{_f1_bucket_of(spec, flag, user)}")
    return "\n".join(lines)


def _f1_board_text(spec: dict[str, Any], config: dict[str, Any], users: list[str]) -> str:
    lines = []
    for user in users:
        verdict = _f1_assign(spec, config, user)
        bucket = _f1_bucket_of(spec, config["key"], user)
        lines.append(f"{config['key']}#{user}={verdict}@{bucket}")
    return "\n".join(lines)


_F1_SHIPPED_BUCKETS = '''"""Rollout bucketing for the <HOME> service.

A stable bucket per (flag, user): the first 8 hex digits of the
user:flag digest, mod 10000 - the usual spread for percentage
rollouts.
"""

import hashlib


def bucket_of(flag_key, user_id):
    """The user's rollout bucket id for the flag (B + 4 digits)."""
    user = user_id.strip().lower()
    digest = hashlib.sha256(f"{user}:{flag_key}".encode("utf-8")).hexdigest()
    return f"B{int(digest[:8], 16) % 10_000:04d}"
'''

_F1_SHIPPED_ASSIGN = '''"""Feature-flag assignment for the <HOME> service.

Internal testers (the allow-list) always keep access; the kill-switch
drops everyone else; everyone else is rolled out by percentage.
"""

from rollout_buckets import bucket_of


def assign(config, user_id):
    """The assignment verdict for one user on one flag."""
    user = user_id.strip().lower()
    if user in [u.strip().lower() for u in config.get("allow", [])]:
        return "in-allow"
    if config.get("kill"):
        return "out-kill"
    bucket = int(bucket_of(config["key"], user)[1:])
    if bucket <= config.get("percent", 0) * 100:
        return "in"
    return "out"
'''

_F1_SHIPPED_BOARD = '''"""Release board lines for the <HOME> service - one line per user per
flag.

Composes the assignment verdict and the rollout bucket.
"""

from rollout_assign import assign
from rollout_buckets import bucket_of


def board_line(config, user_id):
    """``flag#user=verdict@bucket`` - the board line for one user."""
    return (
        f"{config['key']}#{user_id}={assign(config, user_id)}"
        f"@{bucket_of(config['key'], user_id)}"
    )
'''

_F1_TEST_TEMPLATE = '''"""Contract tests for the <HOME> rollout assignment against the INTERNAL
team standard. The standard is not public: each scenario's expected
behavior is pinned as a sha256 DIGEST over the observed trace, so this
file cannot become a copy of the standard."""

import hashlib

import pytest

from rollout_board import board_line
from rollout_buckets import bucket_of


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _paired(scenarios, digests):
    """Scenario/digest pairs - the counts must match exactly."""
    assert len(scenarios) == len(digests)
    return list(zip(scenarios, digests))


def bucket_text(pairs):
    lines = []
    for flag, user in pairs:
        try:
            bucket = bucket_of(flag, user)
        except Exception as exc:
            lines.append(f"{flag}|{user} !{type(exc).__name__}")
            continue
        lines.append(f"{flag}|{user}|{bucket}")
    return "\\n".join(lines)


def board_text(config, users):
    lines = []
    for user in users:
        try:
            line = board_line(config, user)
        except Exception as exc:
            lines.append(f"{config['key']}#{user} !{type(exc).__name__}")
            continue
        lines.append(line)
    return "\\n".join(lines)


BUCKET_SCENARIOS = [
<BUCKET_SCENARIOS>]

BOARD_SCENARIOS = [
<BOARD_SCENARIOS>]

# sha256 of each scenario's canonical text, from the standard.
BUCKET_DIGESTS = [
<BUCKET_DIGESTS>]
BOARD_DIGESTS = [
<BOARD_DIGESTS>]


@pytest.mark.parametrize(
    ("pairs", "digest"),
    _paired(BUCKET_SCENARIOS, BUCKET_DIGESTS),
    ids=[f"b{i:02d}" for i in range(1, len(BUCKET_SCENARIOS) + 1)],
)
def test_bucket(pairs, digest):
    text = bucket_text(pairs)
    assert _digest(text) == digest, text


@pytest.mark.parametrize(
    ("scenario", "digest"),
    _paired(BOARD_SCENARIOS, BOARD_DIGESTS),
    ids=[f"r{i:02d}" for i in range(1, len(BOARD_SCENARIOS) + 1)],
)
def test_board(scenario, digest):
    config, users = scenario
    text = board_text(config, users)
    assert _digest(text) == digest, text
'''


def _f1_render_bucket_scenarios(spec: dict[str, Any]) -> str:
    parts = []
    for pairs in _f1_bucket_scenarios(spec):
        parts.append("    [\n")
        for flag, user in pairs:
            parts.append(f'        ("{flag}", "{user}"),\n')
        parts.append("    ],\n")
    return "".join(parts)


def _f1_render_board_scenarios(spec: dict[str, Any]) -> str:
    parts = []
    for config, users in _f1_board_scenarios(spec):
        parts.append("    (\n")
        parts.append("        {\n")
        parts.append(f'            "key": "{config["key"]}",\n')
        parts.append(f'            "kill": {config["kill"]},\n')
        allow = ", ".join(f'"{user}"' for user in config["allow"])
        parts.append(f'            "allow": [{allow}],\n')
        parts.append(f'            "percent": {config["percent"]},\n')
        parts.append("        },\n")
        parts.append("        [\n")
        for user in users:
            parts.append(f'            "{user}",\n')
        parts.append("        ],\n")
        parts.append("    ),\n")
    return "".join(parts)


def _f1_render_digests(digests: list[str]) -> str:
    return "".join(f'    "{digest}",\n' for digest in digests)


def _f1_test_file(spec: dict[str, Any]) -> str:
    bucket_digests = [
        hashlib.sha256(_f1_bucket_text(spec, pairs).encode("utf-8")).hexdigest()
        for pairs in _f1_bucket_scenarios(spec)
    ]
    board_digests = [
        hashlib.sha256(_f1_board_text(spec, config, users).encode("utf-8")).hexdigest()
        for config, users in _f1_board_scenarios(spec)
    ]
    return (
        _F1_TEST_TEMPLATE.replace("<HOME>", spec["home"])
        .replace("<BUCKET_SCENARIOS>", _f1_render_bucket_scenarios(spec))
        .replace("<BOARD_SCENARIOS>", _f1_render_board_scenarios(spec))
        .replace("<BUCKET_DIGESTS>", _f1_render_digests(bucket_digests))
        .replace("<BOARD_DIGESTS>", _f1_render_digests(board_digests))
    )


_F1_FIX_BUCKETS_TEMPLATE = '''"""Rollout bucketing for the <HOME> service - the internal mix.

The deterministic bucket recipe: <PREFIX> + <DIGITS> zero-padded
digits from the salted flag/user digest.
"""

import hashlib

_MIXING_TOKEN = "<SALT>"
_BUCKETS = <BUCKETS>


def bucket_number(flag_key, user_id):
    """The raw bucket number (0..<BUCKETS_M1>) for one user on one flag.

<NORM_DOC>
    """
<NORM_LINE>    material = f"<MATERIAL>"
    digest = hashlib.<HASH>(material.encode("utf-8")).hexdigest()
    return int(<SLICE>, 16) % _BUCKETS


def bucket_of(flag_key, user_id):
    """The user's bucket id for the flag: <PREFIX> + zero-padded digits."""
    return f"<PREFIX>{bucket_number(flag_key, user_id):0<DIGITS>d}"
'''

_F1_FIX_ASSIGN_TEMPLATE = '''"""Feature-flag assignment for the <HOME> service - the internal order.

Rules run in the internal order; the first rule that fires wins; a
user where no rule fires is <OUT>.
"""

from rollout_buckets import bucket_number

_ORDER = (<ORDER>)
_SCALE = <SCALE>


def assign(config, user_id):
    """The assignment verdict for one user on one flag.

    The allow list is matched exactly as written - against the user id
    as given, no folding, no trimming.
    """
    for step in _ORDER:
        if step == "kill" and config.get("kill"):
            return "<KILL>"
        if step == "allow" and user_id in config.get("allow", []):
            return "<ALLOW>"
        if step == "percent" and (
            bucket_number(config["key"], user_id) < config.get("percent", 0) * _SCALE
        ):
            return "<IN>"
    return "<OUT>"
'''


def _f1_fix_buckets(spec: dict[str, Any]) -> str:
    norm_doc = {
        "none": (
            "    The flag key and the user id are hashed byte-for-byte as\n"
            "    given - no trimming, no case folding."
        ),
        "lower": (
            "    The user id is folded to lower case before hashing; the flag\n"
            "    key is hashed byte-for-byte as given."
        ),
        "strip-lower": (
            "    The user id is trimmed and folded to lower case before\n"
            "    hashing; the flag key is hashed byte-for-byte as given."
        ),
        "strip": (
            "    The user id is trimmed (case preserved) before hashing; the\n"
            "    flag key is hashed byte-for-byte as given."
        ),
    }[spec["norm"]]
    norm_line = {
        "none": "",
        "lower": "    user = user_id.lower()\n",
        "strip-lower": "    user = user_id.strip().lower()\n",
        "strip": "    user = user_id.strip()\n",
    }[spec["norm"]]
    user_ref = "user_id" if spec["norm"] == "none" else "user"
    material = (
        spec["material"]
        .replace("{flag}", "{flag_key}")
        .replace("{user}", "{" + user_ref + "}")
        .replace("{salt}", "{_MIXING_TOKEN}")
    )
    if spec["slice_dir"] == "last":
        slice_expr = f"digest[-{spec['slice_len']}:]"
    else:
        slice_expr = f"digest[:{spec['slice_len']}]"
    return (
        _F1_FIX_BUCKETS_TEMPLATE.replace("<HOME>", spec["home"])
        .replace("<SALT>", spec["salt"])
        .replace("<BUCKETS>", str(spec["buckets"]))
        .replace("<BUCKETS_M1>", str(spec["buckets"] - 1))
        .replace("<NORM_DOC>", norm_doc)
        .replace("<NORM_LINE>", norm_line)
        .replace("<MATERIAL>", material)
        .replace("<HASH>", spec["hash"])
        .replace("<SLICE>", slice_expr)
        .replace("<PREFIX>", spec["prefix"])
        .replace("<DIGITS>", str(spec["digits"]))
    )


def _f1_fix_assign(spec: dict[str, Any]) -> str:
    order = ", ".join(f'"{step}"' for step in spec["order"])
    return (
        _F1_FIX_ASSIGN_TEMPLATE.replace("<HOME>", spec["home"])
        .replace("<ORDER>", order)
        .replace("<SCALE>", str(spec["scale"]))
        .replace("<KILL>", spec["verdicts"]["kill"])
        .replace("<ALLOW>", spec["verdicts"]["allow"])
        .replace("<IN>", spec["verdicts"]["in"])
        .replace("<OUT>", spec["verdicts"]["out"])
    )


_F1_SKILL_TEMPLATE = """---
name: <SKILL_ID>
description: <DESCRIPTION>
version: 1.0.0
---

# <TEAM> Rollout Bucketing Standard (internal)

Scope: the <TEAM> team's rollout plane - the services <SERVICES>.
Internal to the <TEAM> team - not published anywhere.

## Bucketing (the <TEAM> mix)

`bucket_of(flag_key, user_id)` returns the user's bucket id for the
flag: `<PREFIX>` + <DIGITS> zero-padded digits (0..<BUCKETS_M1>).

The bucket number is:

    int(<HASH>(f"<MATERIAL>".encode("utf-8")).hexdigest()<SLICE>, 16) % <BUCKETS>

- The mixing token `<SALT>` is part of the recipe - fixed, never
  per-flag, never changed.
- <NORM_SKILL>

## Assignment (the resolution order)

`assign(config, user_id)` returns exactly one verdict string. The
config carries `key` (the flag key), `kill` (bool), `allow` (list of
user ids), `percent` (integer 0..100). The rules are evaluated in THIS
order; the FIRST rule that fires gives the verdict; the percentage
rule fires only when the bucket qualifies - a non-qualifying bucket
falls through to the next rule; a user where no rule fires is <OUT>:

<RULES>

`percent` scales as percent * <SCALE> over the <BUCKETS> buckets:
percent=10 enrolls buckets 0..<T10_M1>; percent=100 enrolls everyone
(the maximum bucket <BUCKETS_M1> qualifies).

The allow list is matched exactly as written - against the user id as
given, no folding, no trimming.

## Worked example

flag "<WFLAG>", user "<WUSER>": the mix gives bucket <WBUCKET>
(bucket number <WNUM>).

- kill=False, allow=[], percent=50: <WNUM> <CMP50> <T50> -> <V50>.
- kill=False, allow=[], percent=10: <WNUM> <CMP10> <T10> -> <V10>.
- kill=True, allow=["<WUSER>"], percent=100: -> <VKILL>.
- kill=False, allow=["<WUSER>"], percent=0: -> <VALLOW>.
"""


def _f1_rules_text(spec: dict[str, Any]) -> str:
    lines = []
    for index, step in enumerate(spec["order"], 1):
        if step == "kill":
            lines.append(f"{index}. `kill` truthy -> {spec['verdicts']['kill']}.")
        elif step == "allow":
            verdict = spec["verdicts"]["allow"]
            lines.append(f"{index}. the user id in `allow` (exact match) -> {verdict}.")
        else:
            verdict = spec["verdicts"]["in"]
            lines.append(f"{index}. the bucket number < `percent` * {spec['scale']} -> {verdict}.")
    return "\n".join(lines)


def _f1_skill(spec: dict[str, Any]) -> str:
    flag = spec["flags"][0]
    user = spec["users"][6]
    number = _f1_bucket_number(spec, flag, user)
    bucket = _f1_bucket_of(spec, flag, user)
    t50 = 50 * spec["scale"]
    t10 = 10 * spec["scale"]
    v50 = _f1_assign(spec, {"key": flag, "kill": False, "allow": [], "percent": 50}, user)
    v10 = _f1_assign(spec, {"key": flag, "kill": False, "allow": [], "percent": 10}, user)
    vkill = _f1_assign(spec, {"key": flag, "kill": True, "allow": [user], "percent": 100}, user)
    vallow = _f1_assign(spec, {"key": flag, "kill": False, "allow": [user], "percent": 0}, user)
    norm_skill = {
        "none": (
            "Both the flag key and the user id are hashed byte-for-byte\n"
            "  as given - no trimming, no case folding."
        ),
        "lower": (
            "The user id is folded to lower case before hashing; the flag\n"
            "  key is hashed byte-for-byte as given."
        ),
        "strip-lower": (
            "The user id is trimmed and folded to lower case before\n"
            "  hashing; the flag key is hashed byte-for-byte as given."
        ),
        "strip": (
            "The user id is trimmed (case preserved) before hashing; the\n"
            "  flag key is hashed byte-for-byte as given."
        ),
    }[spec["norm"]]
    material = (
        spec["material"]
        .replace("{flag}", "{flag_key}")
        .replace("{user}", "{user_id}")
        .replace("{salt}", spec["salt"])
    )
    if spec["slice_dir"] == "last":
        slice_expr = f"[-{spec['slice_len']}:]"
    else:
        slice_expr = f"[:{spec['slice_len']}]"
    description = (
        f"Rollout bucketing standard for the {spec['team']} platform services "
        f"({spec['services'][0]}, {spec['services'][1]})."
    )
    return (
        _F1_SKILL_TEMPLATE.replace("<SKILL_ID>", spec["skill_id"])
        .replace("<DESCRIPTION>", description)
        .replace("<TEAM>", spec["team"])
        .replace("<SERVICES>", f"{spec['services'][0]} and {spec['services'][1]}")
        .replace("<PREFIX>", spec["prefix"])
        .replace("<DIGITS>", str(spec["digits"]))
        .replace("<BUCKETS>", str(spec["buckets"]))
        .replace("<BUCKETS_M1>", str(spec["buckets"] - 1))
        .replace("<HASH>", spec["hash"])
        .replace("<MATERIAL>", material)
        .replace("<SLICE>", slice_expr)
        .replace("<SALT>", spec["salt"])
        .replace("<NORM_SKILL>", norm_skill)
        .replace("<RULES>", _f1_rules_text(spec))
        .replace("<SCALE>", str(spec["scale"]))
        .replace("<T10>", str(t10))
        .replace("<T10_M1>", str(t10 - 1))
        .replace("<T50>", str(t50))
        .replace("<WFLAG>", flag)
        .replace("<WUSER>", user)
        .replace("<WBUCKET>", bucket)
        .replace("<WNUM>", str(number))
        .replace("<CMP50>", "<" if number < t50 else ">=")
        .replace("<CMP10>", "<" if number < t10 else ">=")
        .replace("<V50>", v50)
        .replace("<V10>", v10)
        .replace("<VKILL>", vkill)
        .replace("<VALLOW>", vallow)
        .replace("<OUT>", spec["verdicts"]["out"])
    )


def _f1_prompt(spec: dict[str, Any]) -> str:
    return (
        f"The tests in test_rollout_convention.py fail. This {spec['home']} "
        "feature-flag rollout assignment doesn't follow how we decide rollout "
        "assignment internally - which users get a flag is decided by our "
        "internal policy, not by guesswork. Find the root cause before "
        "proposing any fix, then fix it and make the whole test suite green."
    )


def _f1_fixture(spec: dict[str, Any]) -> dict[str, Any]:
    markers = [
        spec["salt"],
        spec["verdicts"]["in"],
        spec["verdicts"]["allow"],
        spec["verdicts"]["kill"],
        spec["verdicts"]["out"],
        _f1_bucket_of(spec, spec["flags"][0], spec["users"][6]),
    ]
    files = {
        **_f1_shipped_files(spec),
        "test_rollout_convention.py": _f1_test_file(spec),
    }
    return _private_task(
        f"private3-{spec['slug']}",
        "rollout-bucketing",
        files,
        _f1_prompt(spec),
        spec["skill_id"],
        _f1_skill(spec),
        markers,
    )


def _f1_shipped_files(spec: dict[str, Any]) -> dict[str, str]:
    home = spec["home"]
    return {
        "rollout_buckets.py": _F1_SHIPPED_BUCKETS.replace("<HOME>", home),
        "rollout_assign.py": _F1_SHIPPED_ASSIGN.replace("<HOME>", home),
        "rollout_board.py": _F1_SHIPPED_BOARD.replace("<HOME>", home),
    }


def _f1_fix(spec: dict[str, Any]) -> list[tuple[str, str | None, str]]:
    return [
        ("rollout_buckets.py", None, _f1_fix_buckets(spec)),
        ("rollout_assign.py", None, _f1_fix_assign(spec)),
    ]


# ===========================================================================
# Family 2: money rounding & fiscal periods - 5 teams, 5 variants.
# ===========================================================================
#
# Every variant's standard defines the same three planes (the currency
# unit table, the rendered amount, the fiscal calendar) and differs in
# the class tables, the unknown-code handling, the render format and the
# calendar. The shipped code is the SAME plausible-but-wrong generic
# convention in all five variants (round(value, 2) half-to-even, comma
# thousands separators, calendar year/month "FY{y}-P{m}").

_F2_VARIANTS: list[dict[str, Any]] = [
    {
        "team": "Basalt",
        "services": ["strata-ledger", "column-api"],
        "home": "strata-ledger",
        "slug": "basalt-money",
        "skill_id": "basalt-money-rounding",
        "classes": [
            ("IGNEOUS", ["USD", "EUR", "GBP", "CHF"], 2, ROUND_HALF_UP),
            ("SEDIMENT", ["JPY", "KRW", "VND"], 0, ROUND_HALF_EVEN),
            ("SEAM", ["XAU", "XAG"], 4, ROUND_HALF_DOWN),
        ],
        "fold": "none",
        "unknown_exc": "BasaltUnknownUnit",
        "decimal_sep": ".",
        "thousands": "",
        "neg_zero": "unsigned",
        "fy_start": 7,
        "label": "start",
        "gran": "month",
        "fmt": "FY{year:04d}-P{unit:02d}",
        "period_letter": "P",
    },
    {
        "team": "Taffeta",
        "services": ["loom-api", "selvage-web"],
        "home": "loom-api",
        "slug": "taffeta-money",
        "skill_id": "taffeta-money-rounding",
        "classes": [
            ("WARP", ["USD", "CAD", "AUD"], 2, ROUND_HALF_UP),
            ("WEFT", ["JPY", "CLP", "TWD"], 0, ROUND_DOWN),
            ("BOLT", ["BTC", "ETH", "SOL"], 6, ROUND_HALF_DOWN),
        ],
        "fold": "upper",
        "unknown_exc": "TaffetaUnknownUnit",
        "decimal_sep": ",",
        "thousands": "",
        "neg_zero": "unsigned",
        "fy_start": 4,
        "label": "start",
        "gran": "quarter",
        "fmt": "FY{year:04d}-Q{unit}",
        "period_letter": "Q",
    },
    {
        "team": "Juniper",
        "services": ["orchard-api", "bract-web"],
        "home": "orchard-api",
        "slug": "juniper-money",
        "skill_id": "juniper-money-rounding",
        "classes": [
            ("BERRY", ["USD", "EUR", "SEK"], 2, ROUND_HALF_UP),
            ("POM", ["JPY", "KRW"], 0, ROUND_HALF_UP),
            ("CONE", ["BTC", "XRP"], 8, ROUND_HALF_EVEN),
        ],
        "fold": "strip",
        "unknown_exc": "JuniperUnknownUnit",
        "decimal_sep": ".",
        "thousands": " ",
        "neg_zero": "unsigned",
        "fy_start": 2,
        "label": "start",
        "gran": "month",
        "fmt": "FY{year:04d}P{unit:02d}",
        "period_letter": "P",
    },
    {
        "team": "Anvil",
        "services": ["crucible-api", "bellows-web"],
        "home": "crucible-api",
        "slug": "anvil-money",
        "skill_id": "anvil-money-rounding",
        "classes": [
            ("FORGE", ["USD", "GBP"], 2, ROUND_HALF_UP),
            ("HAMMER", ["JPY", "KRW", "CLP"], 0, ROUND_HALF_EVEN),
            ("INGOT", ["XAU", "XPT"], 3, ROUND_HALF_UP),
        ],
        "fold": "none",
        "unknown_exc": "AnvilUnknownUnit",
        "decimal_sep": ".",
        "thousands": "",
        "neg_zero": "keep",
        "fy_start": 10,
        "label": "end",
        "gran": "month",
        "fmt": "FY{year:04d}-M{unit:02d}",
        "period_letter": "M",
    },
    {
        "team": "Lantern",
        "services": ["glimmer-api", "mantle-web"],
        "home": "glimmer-api",
        "slug": "lantern-money",
        "skill_id": "lantern-money-rounding",
        "classes": [
            ("WICK", ["USD", "EUR"], 2, ROUND_HALF_UP),
            ("GLASS", ["JPY", "HUF"], 0, ROUND_HALF_UP),
            ("BEAM", ["BTC", "ETH"], 5, ROUND_HALF_DOWN),
        ],
        "fold": "none",
        "unknown_exc": "LanternUnknownUnit",
        "decimal_sep": ".",
        "thousands": "'",
        "neg_zero": "unsigned",
        "fy_start": 1,
        "label": "start",
        "gran": "quarter",
        "fmt": "CY{year:04d}-Q{unit}",
        "period_letter": "Q",
    },
]


def _f2_table(spec: dict[str, Any]) -> dict[str, tuple[int, str]]:
    return {
        code: (digits, mode) for _name, codes, digits, mode in spec["classes"] for code in codes
    }


def _f2_fold(spec: dict[str, Any], currency: str) -> str:
    if spec["fold"] == "upper":
        return currency.upper()
    if spec["fold"] == "strip":
        return currency.strip()
    return currency


def _f2_group(text: str, sep: str) -> str:
    sign = ""
    if text.startswith("-"):
        sign, text = "-", text[1:]
    whole, dot, frac = text.partition(".")
    grouped = f"{int(whole):,}".replace(",", sep)
    return sign + grouped + (dot + frac if dot else "")


def _f2_render_amount(spec: dict[str, Any], digits: int, rounded: Decimal) -> str:
    if spec["neg_zero"] == "unsigned" and not rounded:
        rounded = abs(rounded)
    text = f"{rounded:.{digits}f}"
    if spec["thousands"]:
        text = _f2_group(text, spec["thousands"])
    if spec["decimal_sep"] == ",":
        text = text.replace(".", ",")
    return text


def _f2_money_line(spec: dict[str, Any], currency: str, raw: str) -> str:
    code = _f2_fold(spec, currency)
    table = _f2_table(spec)
    if code not in table:
        return f"{currency} {raw} !{spec['unknown_exc']}"
    digits, mode = table[code]
    rounded = Decimal(raw).quantize(Decimal(1).scaleb(-digits), rounding=mode)
    rendered = _f2_render_amount(spec, digits, rounded)
    return f"{currency} {raw} -> {rendered} [{rounded}]"


def _f2_period_id(spec: dict[str, Any], ts: datetime) -> str:
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC)
    start = spec["fy_start"]
    if ts.month >= start:
        offset, year = ts.month - start, ts.year
    else:
        offset, year = ts.month - start + 12, ts.year - 1
    if spec["label"] == "end":
        year += 1
    if spec["gran"] == "quarter":
        unit = offset // 3 + 1
    else:
        unit = offset + 1
    return spec["fmt"].format(year=year, unit=unit)


def _f2_period_line(spec: dict[str, Any], stamp: str) -> str:
    return f"{stamp} -> {_f2_period_id(spec, datetime.fromisoformat(stamp))}"


def _f2_statement_line(spec: dict[str, Any], stamp: str, currency: str, raw: str) -> str:
    code = _f2_fold(spec, currency)
    table = _f2_table(spec)
    if code not in table:
        return f"{stamp}|{currency}|{raw} !{spec['unknown_exc']}"
    digits, mode = table[code]
    rounded = Decimal(raw).quantize(Decimal(1).scaleb(-digits), rounding=mode)
    amount = _f2_render_amount(spec, digits, rounded)
    period = _f2_period_id(spec, datetime.fromisoformat(stamp))
    return f"{stamp}|{currency}|{raw}|{amount} @{period}"


# The period scenarios are generic ISO stamps (the fiscal calendars differ,
# the inputs do not need to); the money scenarios are rendered per variant
# from the variant's own class tables.

_F2_PERIOD_SCENARIOS: list[list[str]] = [
    [
        "2026-07-01T00:00:00+00:00",
        "2026-06-30T23:59:59+00:00",
        "2026-07-15T12:00:00+00:00",
        "2027-01-01T00:00:00+00:00",
        "2025-12-31T23:59:59+00:00",
        "2026-12-25T00:00:00+00:00",
        "2026-02-28T12:00:00+00:00",
        "2024-02-29T00:00:00+00:00",
        "2026-10-31T00:00:00+00:00",
        "2026-05-31T13:59:59+00:00",
    ],
    [
        "2026-07-01T00:00:00",
        "2026-06-30T23:59:59",
        "2026-01-15T08:30:00",
        "2025-03-01T00:00:00",
        "2026-11-11T11:11:11",
        "2024-02-29T06:30:00",
        "2000-02-29T12:00:00",
        "1999-12-31T23:59:59",
        "2026-09-30T23:00:00",
        "2026-04-01T00:00:00",
    ],
    [
        "2026-07-01T00:30:00+09:00",
        "2026-06-30T20:00:00-05:00",
        "2026-01-01T05:00:00+05:00",
        "2025-12-31T20:30:00-03:30",
        "2026-02-28T23:59:59.999999+00:00",
        "2027-03-01T00:00:00+00:00",
        "2026-12-31T18:00:00-06:00",
        "2026-06-15T22:00:00-02:00",
        "2026-09-01T01:00:00+14:00",
        "2026-08-31T10:00:00-11:30",
    ],
]

_F2_STATEMENT_STAMPS = [
    "2026-07-01T00:00:00+00:00",
    "2026-06-30T23:59:59+00:00",
    "2026-08-15T12:00:00+00:00",
    "2026-01-01T00:00:00+00:00",
    "2026-12-25T00:00:00+00:00",
    "2024-02-29T12:00:00",
    "2026-05-31T13:59:59",
    "2026-11-11T11:11:11",
    "2026-10-31T00:00:00+00:00",
    "2026-09-30T23:00:00+00:00",
]

_F2_STATEMENT_STAMPS_2 = [
    "2026-04-01T00:00:00+00:00",
    "2025-12-31T23:59:59+00:00",
    "2026-03-01T00:00:00",
    "2026-02-28T12:00:00+00:00",
    "2026-11-30T06:30:00",
    "2026-07-01T00:30:00+09:00",
    "2026-06-15T22:00:00-02:00",
    "2026-10-01T00:00:00+00:00",
    "2026-01-15T08:30:00",
    "2026-09-01T01:00:00+14:00",
]

_F2_STATEMENT_RAW = [
    "1.005",
    "1234.5",
    "1.23455",
    "-2.675",
    "0.005",
    "98765.5",
    "25000",
    "10.245",
    "19.98765",
    "5.00",
]


def _f2_money_scenarios(spec: dict[str, Any]) -> list[list[tuple[str, str]]]:
    """Four scenario lists over the variant's OWN class tables: one list per
    class (ties, negatives, a >= 1000 value) plus the unknown/fold list."""
    class_codes = [codes for _name, codes, _digits, _mode in spec["classes"]]
    raws_by_class = [
        [
            "1.005",
            "1.004",
            "2.675",
            "-2.675",
            "10.245",
            "0.005",
            "-0.004",
            "1234.5",
            "-1234.5",
            "99999.999",
        ],
        ["1234.5", "1234.4", "2.5", "-2.5", "98765.5", "25000", "0.5", "-0.5", "1234.56", "3.5"],
        [
            "1.23455",
            "1.23456",
            "-1.23455",
            "19.98765",
            "0.00005",
            "-0.00004",
            "12345.67891",
            "2",
            "0.000049",
            "999.99995",
        ],
    ]
    scenarios = []
    for codes_i, raws in zip(class_codes, raws_by_class, strict=True):
        pairs = [(codes_i[index % len(codes_i)], raw) for index, raw in enumerate(raws)]
        scenarios.append(pairs)
    unknown = [
        ("ZZZ", "12.34"),
        ("usd", "1.005"),
        (" USD", "2.5"),
        ("XX", "1"),
        ("USDx", "1.005"),
        ("jpy", "3.5"),
        ("UsD", "1.005"),
        ("USD", "0"),
        ("ZZZ", "-1"),
        ("usd", "-0.004"),
    ]
    scenarios.append(unknown)
    return scenarios


def _f2_statement_scenarios(spec: dict[str, Any]) -> list[list[tuple[str, str, str]]]:
    codes = [code for _name, cs, _d, _m in spec["classes"] for code in cs]
    first = [
        (stamp, codes[index % len(codes)], raw)
        for index, (stamp, raw) in enumerate(
            zip(_F2_STATEMENT_STAMPS, _F2_STATEMENT_RAW, strict=True)
        )
    ]
    second = [
        (stamp, codes[(index + 3) % len(codes)], raw)
        for index, (stamp, raw) in enumerate(
            zip(_F2_STATEMENT_STAMPS_2, _F2_STATEMENT_RAW, strict=True)
        )
    ]
    first[-1] = (first[-1][0], "ZZZ", first[-1][2])
    second[-1] = (second[-1][0], "ZZZ", second[-1][2])
    return [first, second]


_F2_SHIPPED_UNITS = '''"""Money helpers for the <HOME> service - render and round amounts.

Amounts render with two decimal places and thousands separators; ties
round half-to-even, the common default in financial code.
"""

from decimal import Decimal


def round_amount(currency, value):
    """Round an amount to two decimal places (half-to-even)."""
    return round(value, 2)


def render_amount(currency, value):
    """Render an amount with two decimals and thousands separators."""
    return f"{round(value, 2):,.2f}"
'''

_F2_SHIPPED_WINDOWS = '''"""Fiscal period ids for the <HOME> service - maps a timestamp to a
period of its calendar year."""


def fiscal_period_id(ts):
    """The fiscal period id: the year and the calendar month."""
    return f"FY{ts.year:04d}-P{ts.month:02d}"
'''

_F2_SHIPPED_STATEMENT = '''"""Statement lines for the <HOME> service - one line per charge.

Composes the money helpers: the rendered amount and the fiscal period
of the charge's timestamp.
"""

from datetime import datetime
from decimal import Decimal

from fiscal_windows import fiscal_period_id
from money_units import render_amount


def statement_line(stamp, currency, raw):
    """One statement line: the rendered amount and the fiscal period."""
    amount = render_amount(currency, Decimal(raw))
    period = fiscal_period_id(datetime.fromisoformat(stamp))
    return f"{amount} @{period}"
'''

_F2_TEST_TEMPLATE = '''"""Contract tests for the <HOME> money and fiscal-period handling
against the INTERNAL team standard. The standard is not public: each
scenario's expected behavior is pinned as a sha256 DIGEST over the
observed trace, so this file cannot become a copy of the standard."""

import hashlib
from datetime import datetime
from decimal import Decimal

import pytest

from fiscal_windows import fiscal_period_id
from money_statement import statement_line
from money_units import render_amount, round_amount


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _paired(scenarios, digests):
    """Scenario/digest pairs - the counts must match exactly."""
    assert len(scenarios) == len(digests)
    return list(zip(scenarios, digests))


def money_line(currency, raw):
    try:
        rendered = render_amount(currency, Decimal(raw))
        rounded = round_amount(currency, Decimal(raw))
    except Exception as exc:
        return f"{currency} {raw} !{type(exc).__name__}"
    return f"{currency} {raw} -> {rendered} [{rounded}]"


def period_line(stamp):
    try:
        period = fiscal_period_id(datetime.fromisoformat(stamp))
    except Exception as exc:
        return f"{stamp} !{type(exc).__name__}"
    return f"{stamp} -> {period}"


def statement_text_line(stamp, currency, raw):
    try:
        line = statement_line(stamp, currency, raw)
    except Exception as exc:
        return f"{stamp}|{currency}|{raw} !{type(exc).__name__}"
    return f"{stamp}|{currency}|{raw}|{line}"


MONEY_SCENARIOS = [
<MONEY_SCENARIOS>]

PERIOD_SCENARIOS = [
<PERIOD_SCENARIOS>]

STATEMENT_SCENARIOS = [
<STATEMENT_SCENARIOS>]

# sha256 of each scenario's canonical text, from the standard.
MONEY_DIGESTS = [
<MONEY_DIGESTS>]
PERIOD_DIGESTS = [
<PERIOD_DIGESTS>]
STATEMENT_DIGESTS = [
<STATEMENT_DIGESTS>]


@pytest.mark.parametrize(
    ("pairs", "digest"),
    _paired(MONEY_SCENARIOS, MONEY_DIGESTS),
    ids=[f"m{i:02d}" for i in range(1, len(MONEY_SCENARIOS) + 1)],
)
def test_money(pairs, digest):
    text = "\\n".join(money_line(currency, raw) for currency, raw in pairs)
    assert _digest(text) == digest, text


@pytest.mark.parametrize(
    ("stamps", "digest"),
    _paired(PERIOD_SCENARIOS, PERIOD_DIGESTS),
    ids=[f"p{i:02d}" for i in range(1, len(PERIOD_SCENARIOS) + 1)],
)
def test_period(stamps, digest):
    text = "\\n".join(period_line(stamp) for stamp in stamps)
    assert _digest(text) == digest, text


@pytest.mark.parametrize(
    ("entries", "digest"),
    _paired(STATEMENT_SCENARIOS, STATEMENT_DIGESTS),
    ids=[f"s{i:02d}" for i in range(1, len(STATEMENT_SCENARIOS) + 1)],
)
def test_statement(entries, digest):
    text = "\\n".join(
        statement_text_line(stamp, currency, raw) for stamp, currency, raw in entries
    )
    assert _digest(text) == digest, text
'''


def _f2_render_money_scenarios(spec: dict[str, Any]) -> str:
    parts = []
    for pairs in _f2_money_scenarios(spec):
        parts.append("    [\n")
        for currency, raw in pairs:
            parts.append(f'        ("{currency}", "{raw}"),\n')
        parts.append("    ],\n")
    return "".join(parts)


def _f2_render_period_scenarios() -> str:
    parts = []
    for stamps in _F2_PERIOD_SCENARIOS:
        parts.append("    [\n")
        for stamp in stamps:
            parts.append(f'        "{stamp}",\n')
        parts.append("    ],\n")
    return "".join(parts)


def _f2_render_statement_scenarios(spec: dict[str, Any]) -> str:
    parts = []
    for entries in _f2_statement_scenarios(spec):
        parts.append("    [\n")
        for stamp, currency, raw in entries:
            parts.append(f'        ("{stamp}", "{currency}", "{raw}"),\n')
        parts.append("    ],\n")
    return "".join(parts)


def _f2_test_file(spec: dict[str, Any]) -> str:
    money_digests = []
    for pairs in _f2_money_scenarios(spec):
        text = "\n".join(_f2_money_line(spec, currency, raw) for currency, raw in pairs)
        money_digests.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
    period_digests = []
    for stamps in _F2_PERIOD_SCENARIOS:
        text = "\n".join(_f2_period_line(spec, stamp) for stamp in stamps)
        period_digests.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
    statement_digests = []
    for entries in _f2_statement_scenarios(spec):
        text = "\n".join(
            _f2_statement_line(spec, stamp, currency, raw) for stamp, currency, raw in entries
        )
        statement_digests.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
    return (
        _F2_TEST_TEMPLATE.replace("<HOME>", spec["home"])
        .replace("<MONEY_SCENARIOS>", _f2_render_money_scenarios(spec))
        .replace("<PERIOD_SCENARIOS>", _f2_render_period_scenarios())
        .replace("<STATEMENT_SCENARIOS>", _f2_render_statement_scenarios(spec))
        .replace("<MONEY_DIGESTS>", _f1_render_digests(money_digests))
        .replace("<PERIOD_DIGESTS>", _f1_render_digests(period_digests))
        .replace("<STATEMENT_DIGESTS>", _f1_render_digests(statement_digests))
    )


_F2_FIX_UNITS_TEMPLATE = '''"""Money helpers for the <HOME> service - the internal unit table.

Rounding and rendering per currency class; unknown codes fail closed.
"""

from decimal import <MODES>, Decimal


class <UNKNOWN_EXC>(Exception):
    """A currency code outside the internal unit table."""


# code -> (minor digits, rounding mode)
_TABLE = {
<TABLE>}


def _class_of(currency):
    <FOLD_LINE>try:
        return _TABLE[<CODE_REF>]
    except KeyError:
        raise <UNKNOWN_EXC>(currency) from None


def round_amount(currency, value):
    """Quantize the Decimal to the class minor digits with the class mode."""
    digits, mode = _class_of(currency)
    return value.quantize(Decimal(1).scaleb(-digits), rounding=mode)


def render_amount(currency, value):
    """Render the ROUNDED amount: exactly the class minor digits,
    <RENDER_DOC>"""
    digits, _mode = _class_of(currency)
    rounded = round_amount(currency, value)
<RENDER_BODY>'''


def _f2_fix_units(spec: dict[str, Any]) -> str:
    modes = sorted({mode for _name, _codes, _digits, mode in spec["classes"]})
    table_lines = []
    for _name, codes, digits, mode in spec["classes"]:
        for code in codes:
            table_lines.append(f'    "{code}": ({digits}, {mode}),\n')
    fold_line = {
        "none": "",
        "upper": "code = currency.upper()\n    ",
        "strip": "code = currency.strip()\n    ",
    }[spec["fold"]]
    code_ref = "currency" if spec["fold"] == "none" else "code"
    render_doc = {
        ("", ".", "unsigned"): (
            "plain ASCII digits, a `.` before the fractional digits, no"
            " thousands separators; a negative that rounds to zero renders"
            " WITHOUT the sign."
        ),
        ("", ",", "unsigned"): (
            "plain ASCII digits, a `,` before the fractional digits (no"
            " thousands separators); a negative that rounds to zero renders"
            " WITHOUT the sign."
        ),
        (" ", ".", "unsigned"): (
            "plain ASCII digits, a `.` before the fractional digits, space"
            " thousands separators; a negative that rounds to zero renders"
            " WITHOUT the sign."
        ),
        ("'", ".", "unsigned"): (
            "plain ASCII digits, a `.` before the fractional digits,"
            " apostrophe thousands separators; a negative that rounds to"
            " zero renders WITHOUT the sign."
        ),
        ("", ".", "keep"): (
            "plain ASCII digits, a `.` before the fractional digits, no"
            " thousands separators; a negative that rounds to zero KEEPS its"
            " sign."
        ),
    }[(spec["thousands"], spec["decimal_sep"], spec["neg_zero"])]
    body = ['    text = f"{rounded:.{digits}f}"']
    if spec["neg_zero"] == "unsigned":
        body.insert(0, "    if not rounded:\n        rounded = abs(rounded)")
    if spec["thousands"]:
        sep = spec["thousands"]
        body.append(f'    text = _grouped(text, "{sep}")')
    if spec["decimal_sep"] == ",":
        body.append('    return text.replace(".", ",")')
    else:
        body.append("    return text")
    render_body = "\n".join(body)
    grouped_helper = ""
    if spec["thousands"]:
        grouped_helper = '''

def _grouped(text, sep):
    """Group the whole part of a rendered amount in 3s with ``sep``."""
    sign = ""
    if text.startswith("-"):
        sign, text = "-", text[1:]
    whole, dot, frac = text.partition(".")
    grouped = f"{int(whole):,}".replace(",", sep)
    return sign + grouped + (dot + frac if dot else "")
'''
    return (
        _F2_FIX_UNITS_TEMPLATE.replace("<HOME>", spec["home"])
        .replace("<MODES>", ", ".join(modes))
        .replace("<UNKNOWN_EXC>", spec["unknown_exc"])
        .replace("<TABLE>", "".join(table_lines))
        .replace("<FOLD_LINE>", fold_line)
        .replace("<CODE_REF>", code_ref)
        .replace("<RENDER_DOC>", render_doc)
        .replace("<RENDER_BODY>", render_body)
        + grouped_helper
    )


_F2_FIX_WINDOWS_TEMPLATE = '''"""Fiscal period ids for the <HOME> service - the internal calendar.

<CAL_DOC>
"""

from datetime import timezone


def fiscal_period_id(ts):
    """The fiscal period id of a timestamp, e.g. ``<EXAMPLE>``."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc)
<FISCAL_BODY>'''


def _f2_fiscal_body(spec: dict[str, Any]) -> str:
    start = spec["fy_start"]
    if spec["gran"] == "quarter" and start == 1:
        ret = (
            '    return f"'
            + spec["fmt"].replace("{year:04d}", "{ts.year:04d}").replace("{unit}", "{quarter}")
            + '"\n'
        )
        return f"    quarter = (ts.month - 1) // 3 + 1\n{ret}"
    if spec["label"] == "end":
        ge_year, lt_year = "ts.year + 1", "ts.year"
    else:
        ge_year, lt_year = "ts.year", "ts.year - 1"
    if spec["gran"] == "quarter":
        ge = f"fiscal_year, offset = {ge_year}, ts.month - {start}"
        lt = f"fiscal_year, offset = {lt_year}, ts.month - {start} + 12"
        ret = (
            '    return f"'
            + spec["fmt"]
            .replace("{year:04d}", "{fiscal_year:04d}")
            .replace("{unit}", "{offset // 3 + 1}")
            + '"\n'
        )
    else:
        ge = f"fiscal_year, period = {ge_year}, ts.month - {start - 1}"
        lt = f"fiscal_year, period = {lt_year}, ts.month + {12 - start + 1}"
        ret = (
            '    return f"'
            + spec["fmt"]
            .replace("{year:04d}", "{fiscal_year:04d}")
            .replace("{unit:02d}", "{period:02d}")
            + '"\n'
        )
    return f"    if ts.month >= {start}:\n        {ge}\n    else:\n        {lt}\n{ret}"


def _f2_cal_doc(spec: dict[str, Any]) -> str:
    start = spec["fy_start"]
    month = datetime(2026, start, 1).strftime("%B")
    if spec["gran"] == "quarter":
        gran = (
            f"The fiscal year starts {month} 1 and runs twelve calendar\n"
            "months grouped into four quarter periods: Q1 = the first three\n"
            "fiscal months, Q2 = the next three, Q3 and Q4 in order."
        )
    else:
        gran = (
            f"The fiscal year starts {month} 1 and has twelve calendar-month\n"
            f"periods: {spec['period_letter']}01 = {month}, "
            f"{spec['period_letter']}02 = the next month, ... in order."
        )
    if spec["label"] == "end":
        end_month = datetime(2026, start - 1 if start > 1 else 12, 1).strftime("%B")
        label = (
            "The fiscal year label is the calendar year in which the fiscal\n"
            f"year ENDS (the {end_month}'s year): the fiscal year starting\n"
            f"{month} 1 is labeled with the FOLLOWING calendar year."
        )
    else:
        label = (
            "The fiscal year label is the calendar year in which the fiscal\n"
            f"year STARTS (the {month}'s year)."
        )
    return f"{gran}\n{label}"


def _f2_fix_windows(spec: dict[str, Any]) -> str:
    start_month = spec["fy_start"]
    example = _f2_period_id(spec, datetime(2026, start_month, 15, tzinfo=UTC))
    return (
        _F2_FIX_WINDOWS_TEMPLATE.replace("<HOME>", spec["home"])
        .replace("<CAL_DOC>", _f2_cal_doc(spec))
        .replace("<EXAMPLE>", example)
        .replace("<FISCAL_BODY>", _f2_fiscal_body(spec))
    )


_F2_SKILL_TEMPLATE = """---
name: <SKILL_ID>
description: <DESCRIPTION>
version: 1.0.0
---

# <TEAM> Money Rounding Standard (internal)

Scope: the <TEAM> team's money plane - the services <SERVICES>.
Internal to the <TEAM> team - not published anywhere.

## Currency classes (the internal unit table)

| Class | Codes | Minor digits | Rounding |
|-------|-------|---------------|----------|
<TABLE>

A code not in the table raises `<UNKNOWN_EXC>` (fail closed).
<FOLD_DOC>

## Amounts

- `round_amount(currency, value)` quantizes the Decimal to the class
  minor digits with the class rounding mode.
- `render_amount(currency, value)` renders the ROUNDED amount with
  exactly the class minor digits: <RENDER_DOC>

## Fiscal periods

- <CAL_DOC>
- The period id format is `<FMT>`<FMT_DOC>
- All timestamps are UTC: naive datetimes ARE UTC; aware ones are
  converted to UTC first. The period start instant is inclusive.

## Worked examples

<MONEY_EXAMPLES>

    fiscal_period_id(datetime(2026, <START>, 15, tzinfo=UTC)) -> "<P_EX_A>"
    fiscal_period_id(datetime(2026, <START_M1>, 15, tzinfo=UTC)) -> "<P_EX_B>"
"""


def _f2_skill(spec: dict[str, Any]) -> str:
    table_lines = []
    for name, codes, digits, mode in spec["classes"]:
        table_lines.append(f"| {name} | {' '.join(codes)} | {digits} | {mode} |")
    fold_doc = {
        "none": (
            'Codes are matched exactly as given - no case folding, no trimming ("usd" is unknown).'
        ),
        "upper": 'Codes are matched after folding to UPPER case ("usd" is USD).',
        "strip": ("Codes are matched after trimming surrounding whitespace\n(case preserved)."),
    }[spec["fold"]]
    render_doc = {
        ("", ".", "unsigned"): (
            "plain ASCII digits, a `.` before the fractional digits (only"
            " when minor digits > 0), no thousands separators; a negative"
            " that rounds to zero renders WITHOUT the sign (never `-0.00`)."
        ),
        ("", ",", "unsigned"): (
            "plain ASCII digits, a `,` before the fractional digits (only"
            " when minor digits > 0), no thousands separators; a negative"
            " that rounds to zero renders WITHOUT the sign (never `-0,00`)."
        ),
        (" ", ".", "unsigned"): (
            "plain ASCII digits, a `.` before the fractional digits, SPACE"
            " thousands separators (`12 345.67`); a negative that rounds to"
            " zero renders WITHOUT the sign."
        ),
        ("'", ".", "unsigned"): (
            "plain ASCII digits, a `.` before the fractional digits,"
            " APOSTROPHE thousands separators (`1'234.56`); a negative that"
            " rounds to zero renders WITHOUT the sign."
        ),
        ("", ".", "keep"): (
            "plain ASCII digits, a `.` before the fractional digits, no"
            " thousands separators; a negative that rounds to zero KEEPS"
            " its sign (`-0.00`)."
        ),
    }[(spec["thousands"], spec["decimal_sep"], spec["neg_zero"])]
    class3_digits = spec["classes"][2][2]
    class3_tie = {
        3: "1.2345",
        4: "1.23455",
        5: "1.23455",
        6: "1.234555",
        8: "1.23455555",
    }[class3_digits]
    example_pairs = [
        (spec["classes"][0][1][0], "1.005"),
        (spec["classes"][0][1][1], "1234.5"),
        (spec["classes"][1][1][0], "2.5"),
        (spec["classes"][1][1][1], "-2.5"),
        (spec["classes"][2][1][0], class3_tie),
        (spec["classes"][2][1][1], "19.98765"),
    ]
    money_examples = []
    for currency, raw in example_pairs:
        line = _f2_money_line(spec, currency, raw)
        rendered = line.split(" -> ")[1].split(" [")[0]
        money_examples.append(f'    render_amount("{currency}", Decimal("{raw}")) -> "{rendered}"')
    start = spec["fy_start"]
    start_m1 = start - 1 if start > 1 else 12
    p_ex_a = _f2_period_id(spec, datetime(2026, start, 15, tzinfo=UTC))
    p_ex_b = _f2_period_id(spec, datetime(2026, start_m1, 15, tzinfo=UTC))
    fmt_doc = (
        " with a zero-padded 4-digit year and a zero-padded 2-digit period."
        if spec["gran"] == "month"
        else " with a zero-padded 4-digit year; quarter ids are Q1..Q4\n(not zero-padded)."
    )
    description = (
        f"Money rounding and fiscal period standard for the {spec['team']} platform services "
        f"({spec['services'][0]}, {spec['services'][1]})."
    )
    return (
        _F2_SKILL_TEMPLATE.replace("<SKILL_ID>", spec["skill_id"])
        .replace("<DESCRIPTION>", description)
        .replace("<TEAM>", spec["team"])
        .replace("<SERVICES>", f"{spec['services'][0]} and {spec['services'][1]}")
        .replace("<TABLE>", "\n".join(table_lines))
        .replace("<UNKNOWN_EXC>", spec["unknown_exc"])
        .replace("<FOLD_DOC>", fold_doc)
        .replace("<RENDER_DOC>", render_doc)
        .replace("<CAL_DOC>", _f2_cal_doc(spec))
        .replace("<FMT>", spec["fmt"])
        .replace("<FMT_DOC>", fmt_doc)
        .replace("<MONEY_EXAMPLES>", "\n".join(money_examples))
        .replace("<START>", str(start))
        .replace("<START_M1>", str(start_m1))
        .replace("<P_EX_A>", p_ex_a)
        .replace("<P_EX_B>", p_ex_b)
    )


def _f2_prompt(spec: dict[str, Any]) -> str:
    return (
        f"The tests in test_money_convention.py fail. This {spec['home']} money "
        "handling doesn't follow how we round amounts and map timestamps to "
        "fiscal periods internally - that is decided by our internal policy, "
        "not by guesswork. Find the root cause before proposing any fix, then "
        "fix it and make the whole test suite green."
    )


def _f2_fixture(spec: dict[str, Any]) -> dict[str, Any]:
    start = spec["fy_start"]
    markers = [
        spec["unknown_exc"],
        spec["classes"][0][0],
        spec["classes"][1][0],
        spec["classes"][2][0],
        _f2_period_id(spec, datetime(2026, start, 15, tzinfo=UTC)),
        _f2_period_id(spec, datetime(2026, start - 1 if start > 1 else 12, 15, tzinfo=UTC)),
    ]
    files = {
        "money_units.py": _F2_SHIPPED_UNITS.replace("<HOME>", spec["home"]),
        "fiscal_windows.py": _F2_SHIPPED_WINDOWS.replace("<HOME>", spec["home"]),
        "money_statement.py": _F2_SHIPPED_STATEMENT.replace("<HOME>", spec["home"]),
        "test_money_convention.py": _f2_test_file(spec),
    }
    return _private_task(
        f"private3-{spec['slug']}",
        "money-rounding",
        files,
        _f2_prompt(spec),
        spec["skill_id"],
        _f2_skill(spec),
        markers,
    )


def _f2_fix(spec: dict[str, Any]) -> list[tuple[str, str | None, str]]:
    return [
        ("money_units.py", None, _f2_fix_units(spec)),
        ("fiscal_windows.py", None, _f2_fix_windows(spec)),
    ]


# ===========================================================================
# Assembly.
# ===========================================================================

TASKS: list[dict[str, Any]] = [
    *(_f1_fixture(spec) for spec in _F1_VARIANTS),
    *(_f2_fixture(spec) for spec in _F2_VARIANTS),
]

#: The known root-cause fix per fixture - whole-file replacements in the
#: verify_private_fixtures.py (file, None, content) format. The fix IS the
#: module conforming to the fixture's private standard; the composer
#: modules (rollout_board.py / money_statement.py) only delegate to the
#: rule-carrying modules and need no fix; the tests are never touched.
FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    **{f"private3-{spec['slug']}": _f1_fix(spec) for spec in _F1_VARIANTS},
    **{f"private3-{spec['slug']}": _f2_fix(spec) for spec in _F2_VARIANTS},
}

assert set(FIXES) == {str(task["name"]) for task in TASKS}, "every fixture needs a fix"
