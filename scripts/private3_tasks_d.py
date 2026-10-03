"""Private-knowledge fixtures for the rc-bench DENSE corpus (builder "d").

rc-bench (ADR-014 amendment 30) measured the REAL OpenCode client with
the ACI plugin (the router injects skills) against OpenCode's NATIVE
skills (the model picks from a name+description list): both arms passed
12/12 with a 45-skill corpus, and native was cheaper. The open question
is whether ACI's router wins when the corpus is LARGE and DENSE - many
near-identical internal standards that differ only in WHICH team/service
they apply to. This module is one builder's share of that dense corpus:
NO measurement runs here (no runner changes, no product code).

TWO FAMILIES x FIVE VARIANTS (10 fixtures). A family is one KIND of
standard; each variant is that standard as owned by a DIFFERENT invented
team, scoped to that team's services. The five variants of a family
share the same structure and vocabulary class - their descriptions are
near-identical apart from the scope, which is the point: the list a model
reads is confusable - but they differ in >= 2 concrete decisions that
change EVERY test digest, so applying a SIBLING variant's rules fails the
variant's tests (pinned here by the confusion matrix).

Family "doc-id" - document identifier format (layout, alphabet, check
character, input normalization):

- private3-quarry-doc-id -> quarry-doc-id
      the Quarry records and compliance team (deed-index, title-vault):
      hyphen layout, uppercase base32 sequence, the weighted shelf mark.
- private3-marigold-doc-id -> marigold-doc-id
      the Marigold claims processing team (claim-intake, referral-desk):
      separator-free layout, base36 sequence, the luhn-fold claim digit.
- private3-cobalt-doc-id -> cobalt-doc-id
      the Cobalt docs team (spec-hub, drawing-store):
      lowercase layout, hex sequence, the position-sum hex pair.
- private3-tundra-doc-id -> tundra-doc-id
      the Tundra logistics manifests team (manifest-board,
      waybill-print): slash layout, decimal sequence, the mod-26 route
      letter.
- private3-gossamer-doc-id -> gossamer-doc-id
      the Gossamer legal contracts team (contract-draft, clause-library):
      separator-free lowercase layout, base32 sequence, the weave.

Family "rate-limit" - rate-limit response policy (quota window math,
header names/formats, status codes, retry-after computation):

- private3-beacon-rate-limit -> beacon-rate-limit
      the Beacon public gateway team (edge-gateway, status-page): fixed
      gate minute, X-RateLimit-* tide table, 429, seconds retry.
- private3-vervain-rate-limit -> vervain-rate-limit
      the Vervain search team (query-frontend, crawler): sliding
      drift pane, X-Quota-* search shelf, 429, quench second.
- private3-basalt-rate-limit -> basalt-rate-limit
      the Basalt media upload team (upload-relay, encode):
      fixed upload slot, X-Upload-* stone ledger, 503 cold aisle, epoch
      relay pause.
- private3-halcyon-rate-limit -> halcyon-rate-limit
      the Halcyon webhooks team (hook-dispatch, retry-relay): fixed hook
      beat, X-Hook-* dispatch ledger, 429 hook hold, grace second.
- private3-willow-rate-limit -> willow-rate-limit
      the Willow payments team (settle-api, payout-queue): sliding
      settle pane, the single quota line, 429 payout brake.

Same contract as scripts/private_tasks.py / private2_tasks_*.py: every
standard below is INVENTED with its fixture (the knowledge did not exist
before today, so no model can carry it), documented ONLY in the
fixture's private SKILL.md (served through the capability plane, never a
workspace file), and pinned in the workspace tests as sha256 DIGESTS
over canonical traces - never plaintext - so a naked model can derive
nothing from the test names, messages or the shipped code. The shipped
code embodies a PLAUSIBLE BUT WRONG convention, one that is close to a
SIBLING variant's rules (a model that loads the wrong sibling skill gets
plausibly-close-but-failing code). Every digest is over a LARGE output
space (the E2B section 1.5 lesson): a whole multi-line trace of
combined decisions per digest, so enumeration is not viable.

All prompts are INDIRECT: each names the SERVICE the code belongs to
(a ticket would) but never the standard, its team title words or any
rule. The markers (each standard's distinctive vocabulary) exist ONLY
in that variant's skill - not in its siblings, not in any file, not in
any prompt.

section 34 caveat applies to any round run on this set: small n,
author-built fixtures, one model - directional only.
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _private3_task(
    name: str,
    family: str,
    files: dict[str, str],
    prompt: str,
    skill_id: str,
    skill: str,
    markers: list[str],
) -> dict[str, Any]:
    """A dense-corpus private fixture: workspace files + ticket prompt +
    the private SKILL.md text (NOT a workspace file - the model must never
    see it on disk; the arms serve it through the capability plane only) +
    the family tag + the standard-only markers (the knowledge gate)."""
    return {
        "name": name,
        "family": family,
        "files": files,
        "prompt": prompt,
        "skill_id": skill_id,
        "skill": skill,
        "directness": "indirect",
        "markers": list(markers),
    }


# =====================================================================
# Family "doc-id" - shared workspace skeleton
# =====================================================================
# All five variants ship the SAME file names and the SAME public API
# (doc_id.format_doc_id / normalize_doc_id / validate_doc_id,
# doc_registry.assign_id / registry_key / same_document,
# service_report.report_line) - that is what makes the confusion matrix
# mechanical: FIX_i slots into workspace_j. The composer
# (service_report.py) only delegates and needs no fix.


def _doc_id_service_report_py(service: str) -> str:
    """The family composer - pure delegation, identical shape per variant
    (only the service name in the docstring differs)."""
    return (
        f'"""Report lines for the {service} document desk.\n'
        "\n"
        "One line per report entry: the assigned identifier, the alias's\n"
        "canonical form, the registry key and the validation verdict.\n"
        '"""\n'
        "\n"
        "from doc_id import normalize_doc_id, validate_doc_id\n"
        "from doc_registry import assign_id, registry_key\n"
        "\n"
        "\n"
        "def report_line(doc_type, seq, day, alias):\n"
        '    """``id|canonical|key|verdict`` - one report line for an entry."""\n'
        "    ident = assign_id(doc_type, seq, day)\n"
        "    canonical = normalize_doc_id(alias)\n"
        "    key = registry_key(alias)\n"
        "    verdict = validate_doc_id(alias)\n"
        '    return f"{ident}|{canonical}|{key}|{verdict}"\n'
    )


_DOC_ID_TEST_TEMPLATE = r'''"""Contract tests for the __SERVICE__ document id helpers against our
INTERNAL document identifier policy. The policy is not public: each
group's expected behavior is pinned as a sha256 DIGEST over the observed
trace, so this file cannot become a copy of the policy."""

import hashlib

import pytest

from doc_id import format_doc_id, normalize_doc_id, validate_doc_id
from doc_registry import assign_id, registry_key, same_document
from service_report import report_line


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _format_lines():
    lines = []
    for doc_type, seq, day in FORMAT_CASES:
        try:
            ident = format_doc_id(doc_type, seq, day)
        except Exception as exc:
            lines.append(f"{doc_type}|{seq}|{day} !{type(exc).__name__}")
            continue
        lines.append(f"{doc_type}|{seq}|{day}|{ident}")
    return lines


def _norm_lines():
    lines = []
    for raw in NORM_CASES:
        try:
            canon = normalize_doc_id(raw)
        except Exception as exc:
            lines.append(f"{raw} !{type(exc).__name__}")
            continue
        lines.append(f"{raw}|{canon}")
    return lines


def _validate_lines():
    lines = []
    for raw in VALIDATE_CASES:
        try:
            verdict = validate_doc_id(raw)
        except Exception as exc:
            lines.append(f"{raw} !{type(exc).__name__}")
            continue
        lines.append(f"{raw}|{verdict}")
    return lines


def _report_lines():
    lines = []
    for doc_type, seq, day, alias in REPORT_CASES:
        try:
            line = report_line(doc_type, seq, day, alias)
        except Exception as exc:
            lines.append(f"{doc_type}|{seq}|{day}|{alias} !{type(exc).__name__}")
            continue
        lines.append(line)
    return lines


def _same_lines():
    lines = []
    for one, other in SAME_CASES:
        try:
            same = same_document(one, other)
        except Exception as exc:
            lines.append(f"{one}~{other} !{type(exc).__name__}")
            continue
        lines.append(f"{one}~{other}|{same}")
    return lines


GROUPS = {
    "g1": _format_lines,
    "g2": _norm_lines,
    "g3": _validate_lines,
    "g4": _report_lines,
    "g5": _same_lines,
}

__CASES__

# sha256 of "\n".join(GROUPS[g]()) per group, from the policy.
DIGESTS = {
__DIGESTS__
}


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4", "g5"])
def test_group(group):
    lines = GROUPS[group]()
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''


def _doc_id_test_py(service: str, cases: str, digests: str) -> str:
    """Assemble the family test file: skeleton + the variant's cases + its
    digest pins."""
    return (
        _DOC_ID_TEST_TEMPLATE.replace("__SERVICE__", service)
        .replace("__CASES__", cases)
        .replace("__DIGESTS__", digests)
    )


def _doc_id_shipped_registry_py(service: str) -> str:
    """The family's shipped registry - the plausible-but-wrong public
    convention (the key is the canonical id itself, check included),
    identical shape per variant (only the service name differs)."""
    return (
        f'"""The document registry for the {service} service.\n'
        "\n"
        "Ids are assigned by the id helper; the registry key of an id is\n"
        "the id itself, check character included; two raw forms are the\n"
        "same document when they are equal after trimming and case\n"
        "folding.\n"
        '"""\n'
        "\n"
        "from doc_id import format_doc_id, normalize_doc_id\n"
        "\n"
        "\n"
        "def assign_id(doc_type, seq, day):\n"
        '    """The document id for a new registry entry."""\n'
        "    return format_doc_id(doc_type, seq, day)\n"
        "\n"
        "\n"
        "def registry_key(raw):\n"
        '    """The storage key of a raw id: the canonical id itself."""\n'
        "    return normalize_doc_id(raw)\n"
        "\n"
        "\n"
        "def same_document(raw_a, raw_b):\n"
        '    """Whether two raw forms name the same document."""\n'
        "    return normalize_doc_id(raw_a) == normalize_doc_id(raw_b)\n"
    )


def _doc_id_fixed_registry_py(service: str, key_doc: str, cut: str) -> str:
    """The family's fixed registry: the key never carries the check - it is
    the canonical id with the check part cut (`cut` is the slice)."""
    return (
        f'"""The document registry for the {service} service - the internal policy.\n'
        "\n"
        f"{key_doc}\n"
        '"""\n'
        "\n"
        "from doc_id import format_doc_id, normalize_doc_id\n"
        "\n"
        "\n"
        "def assign_id(doc_type, seq, day):\n"
        '    """The document id for a new registry entry."""\n'
        "    return format_doc_id(doc_type, seq, day)\n"
        "\n"
        "\n"
        "def registry_key(raw):\n"
        f'    """{key_doc}"""\n'
        f"    return normalize_doc_id(raw){cut}\n"
        "\n"
        "\n"
        "def same_document(raw_a, raw_b):\n"
        '    """Whether two raw forms name the same document."""\n'
        "    return normalize_doc_id(raw_a) == normalize_doc_id(raw_b)\n"
    )


# =====================================================================
# Family "doc-id", variant 1: Quarry (records & compliance)
# =====================================================================
# Shipped: the plausible public convention - decimal sequence, a Luhn
# check DIGIT, no check validation on normalize - close to sibling
# Marigold's digit check, wrong for Quarry's base32 shelf mark.

_K1_SHIPPED_DOC_ID_PY = r'''"""Document ids for the deed-index service.

An id is `<type>-<day>-<seq>-<check>`: the two-letter type, the day as
YYYYMMDD, the sequence zero-padded to six decimal digits, and one Luhn
check digit over the type letters, the day and the sequence digits.
Input is trimmed and uppercased; the hyphens are required.
"""

import re
from datetime import datetime

_TYPES = ("DR", "TD", "LN")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHAPE = re.compile(r"^([A-Z]{2})-(\d{8})-(\d{6})-(\d)$")


class DocIdError(Exception):
    """A raw identifier we cannot accept."""


def _check_digit(text):
    """One Luhn check digit over `text` (letters map A=10 .. Z=35)."""
    total = 0
    for i, ch in enumerate(reversed(text)):
        value = int(ch) if ch.isdigit() else ord(ch) - 55
        if i % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return str((10 - total % 10) % 10)


def format_doc_id(doc_type, seq, day):
    """The document id for `doc_type`, `seq` and `day` (YYYY-MM-DD)."""
    if doc_type not in _TYPES:
        raise DocIdError("type")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 10 ** 6:
        raise DocIdError("seq")
    if not _DAY.match(day):
        raise DocIdError("day")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise DocIdError("day") from None
    date = day.replace("-", "")
    digits = f"{seq:06d}"
    return f"{doc_type}-{date}-{digits}-{_check_digit(doc_type + date + digits)}"


def normalize_doc_id(raw):
    """The canonical form of a raw id: trimmed, uppercased."""
    text = str(raw).strip().upper()
    if not _SHAPE.match(text):
        raise DocIdError("shape")
    return text


def validate_doc_id(raw):
    """One verdict per raw id: ok / bad-check / bad-format."""
    text = str(raw).strip().upper()
    match = _SHAPE.match(text)
    if match is None:
        return "bad-format"
    doc_type, date, digits, check = match.groups()
    if doc_type not in _TYPES:
        return "bad-format"
    if _check_digit(doc_type + date + digits) != check:
        return "bad-check"
    return "ok"
'''

# Fixed: the Quarry standard - base32 sequence, the 3-1-7 weighted shelf
# mark, check-validated normalization, the key without the mark.
_K1_FIX_DOC_ID_PY = r'''"""Document ids for the deed-index service - the internal policy.

The layout, the sequence rendering, the shelf mark (the check
character) and input normalization. Fail closed on anything the policy
does not name.
"""

import re
from datetime import datetime

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_TYPES = ("DR", "TD", "LN")
_WEIGHTS = (3, 1, 7)
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHAPE = re.compile(r"^([A-Z]{2})-(\d{8})-([A-Z2-7]{6})-([A-Z2-7])$")


class QuarryIdError(Exception):
    """A raw identifier the policy rejects."""


def _value(ch):
    """The base-36 value of one character: a digit is its number, a letter
    is ord(c) - 55 (A=10 .. Z=35)."""
    return int(ch) if ch.isdigit() else ord(ch) - 55


def _shelf_mark(payload):
    """The shelf mark of a payload under the cycling 3-1-7 weights."""
    total = sum(_value(ch) * _WEIGHTS[i % 3] for i, ch in enumerate(payload))
    return _ALPHABET[total % 32]


def _render_seq(seq):
    """The sequence in the alphabet, six characters, most significant
    first, padded with the zero letter A."""
    digits = []
    for _ in range(6):
        digits.append(_ALPHABET[seq % 32])
        seq //= 32
    return "".join(reversed(digits))


def format_doc_id(doc_type, seq, day):
    """The document id: `<type>-<day>-<seq>-<mark>`."""
    if doc_type not in _TYPES:
        raise QuarryIdError("type")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 32 ** 6:
        raise QuarryIdError("seq")
    if not _DAY.match(day):
        raise QuarryIdError("day")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise QuarryIdError("day") from None
    date = day.replace("-", "")
    seq_text = _render_seq(seq)
    return f"{doc_type}-{date}-{seq_text}-{_shelf_mark(doc_type + date + seq_text)}"


def normalize_doc_id(raw):
    """The canonical identifier: trimmed, uppercased, hyphens required at
    the canonical positions, the shelf mark recomputed and matched."""
    text = str(raw).strip().upper()
    match = _SHAPE.match(text)
    if match is None or match.group(1) not in _TYPES:
        raise QuarryIdError("shape")
    doc_type, date, seq_text, mark = match.groups()
    if _shelf_mark(doc_type + date + seq_text) != mark:
        raise QuarryIdError("mark")
    return text


def validate_doc_id(raw):
    """One verdict per raw id: ok / seal-broken / shape-foreign."""
    text = str(raw).strip().upper()
    match = _SHAPE.match(text)
    if match is None or match.group(1) not in _TYPES:
        return "shape-foreign"
    doc_type, date, seq_text, mark = match.groups()
    if _shelf_mark(doc_type + date + seq_text) != mark:
        return "seal-broken"
    return "ok"
'''

_K1_CASES = r"""FORMAT_CASES = [
    ("DR", 0, "2026-03-01"),
    ("TD", 7, "2026-06-15"),
    ("LN", 12345, "2026-03-01"),
    ("DR", 12345, "2026-03-01"),
    ("LN", 1073741823, "2024-02-29"),
    ("DR", 1073741824, "2026-03-01"),
    ("XX", 5, "2026-03-01"),
    ("dr", 5, "2026-03-01"),
    ("DR", -1, "2026-03-01"),
    ("DR", 5, "2026-02-30"),
    ("DR", 5, "2026-3-1"),
    ("TD", 999, "2025-12-31"),
]

NORM_CASES = [
    "DR-20260301-AAAMBZ-4",
    "dr-20260301-aaambz-4",
    "  DR-20260301-AAAMBZ-4  ",
    "TD-20260615-AAAAAH-L",
    "DR-20260301-AAAMBZ-5",
    "TD-20260615-AAAAAH-M",
    "DR 20260301 AAAMBZ 4",
    "DR-20260301-000042-7",
    "XX-20260301-AAAMBZ-4",
    "DR-20260301-AAAMBZ",
    "hello",
]

VALIDATE_CASES = [
    "DR-20260301-AAAMBZ-4",
    "dr-20260301-aaambz-4",
    "DR-20260301-AAAMBZ-5",
    "TD-20260615-AAAAAH-M",
    "DR 20260301 AAAMBZ 4",
    "DR-20260301-000042-7",
    "XX-20260301-AAAMBZ-4",
    "DR-20260301-AAAMBZ",
    "TD-20260615-AAAAAH-L",
    "LN-20240229-777777-H",
    "DR-2026030I-AAAMBZ-4",
]

REPORT_CASES = [
    ("DR", 12345, "2026-03-01", "dr-20260301-aaambz-4"),
    ("TD", 7, "2026-06-15", "TD-20260615-AAAAAH-L"),
    ("DR", 42, "2026-03-01", "DR-20260301-AAAMBZ-5"),
    ("LN", 3, "2024-02-29", "  LN-20240229-777777-H "),
    ("DR", 5, "2026-3-1", "DR-20260301-AAAMBZ-4"),
]

SAME_CASES = [
    ("DR-20260301-AAAMBZ-4", "dr-20260301-aaambz-4"),
    ("DR-20260301-AAAMBZ-4", "DR-20260301-AAAMBZ-4"),
    ("DR-20260301-AAAMBZ-4", "TD-20260615-AAAAAH-L"),
    ("DR-20260301-AAAMBZ-4", "DR-20260301-AAAMBZ-5"),
    ("DR-20260301-AAAMBZ-4", "hello"),
]
"""

_K1_SKILL = """---
name: quarry-doc-id
description: Document id standard for Quarry records services (deed-index, title-vault).
version: 1.0.0
---

# Quarry Document Identifier Standard (internal)

How every document identifier on the Quarry records services is laid
out, checked and normalized. Internal to the Quarry records and
compliance team - not published anywhere.

## Scope

The Quarry records services: deed-index and title-vault.

## Layout

An identifier is `<type>-<day>-<seq>-<mark>`:

- `<type>` - two uppercase letters from the type table: DR, TD, LN.
- `<day>` - the day as YYYYMMDD, eight digits.
- `<seq>` - the sequence number in the Quarry alphabet, exactly six
  characters, most significant first, padded with the zero letter A.
- `<mark>` - one character of the shelf mark (below).

The Quarry alphabet is the 32-character set
`ABCDEFGHIJKLMNOPQRSTUVWXYZ234567` (uppercase base32).

`format_doc_id(type, seq, day)` builds the identifier. The type must be
in the table EXACTLY as given (no case folding); the day must be a real
calendar day in strict `YYYY-MM-DD` form; the sequence must fit six
alphabet characters (0 through 1073741823). Anything else is refused.

## The shelf mark

The shelf mark is one alphabet character computed over the payload -
type + day + seq, sixteen characters, separators removed:

1. Each character counts its base-36 value: a digit is its number, a
   letter is `ord(c) - 55` (A=10 .. Z=35).
2. Each value is multiplied by the cycling weights 3-1-7: the first
   character gets 3, the second 1, the third 7, the fourth 3 again, and
   so on.
3. The total mod 32 reads the alphabet at that index.

## Normalization

`normalize_doc_id(raw)` returns the canonical identifier and nothing
else:

1. Strip surrounding whitespace, then uppercase the whole string -
   lowercase input is accepted and folded up.
2. The three hyphens are REQUIRED at exactly the canonical positions;
   internal whitespace is refused.
3. The type must be in the table, the day eight digits, the sequence
   six alphabet characters, the mark one alphabet character.
4. The shelf mark is recomputed from the payload and must match, or
   the input is refused with `QuarryIdError`.

## Validation verdicts

`validate_doc_id(raw)` never raises and returns exactly one verdict:

- `ok` - the identifier normalizes cleanly.
- `seal-broken` - the layout is right but the shelf mark is not the
  one the payload gives.
- `shape-foreign` - anything else: wrong layout, unknown type, a
  character outside the alphabet, a missing part.

## The registry key

`registry_key(raw)` is the canonical identifier with the trailing
`-<mark>` removed - the storage key never carries the shelf mark.
`same_document(a, b)` normalizes both and compares.

## Worked example

`format_doc_id("DR", 12345, "2026-03-01")` -> `DR-20260301-AAAMBZ-4`:
the sequence 12345 renders as `AAAMBZ`, and the payload
`DR20260301AAAMBZ` under the cycling weights 3-1-7 totals to the
alphabet index 28, which reads `4`.

`normalize_doc_id("dr-20260301-aaambz-4")` -> `DR-20260301-AAAMBZ-4`
(folded up, hyphens kept); `validate_doc_id("DR-20260301-AAAMBZ-5")`
-> `seal-broken`.
"""

_K1_PROMPT = (
    "The tests in test_doc_id_convention.py fail. This deed-index module "
    "doesn't follow how we put together document identifiers here: which "
    "characters an identifier is built from, and in what order, is decided "
    "by an internal policy of ours, not by guesswork. Find the root cause "
    "before proposing any fix, then fix it and make the whole test suite "
    "green."
)

_K1_MARKERS = [
    "shelf mark",
    "QuarryIdError",
    "seal-broken",
    "shape-foreign",
    "3-1-7",
]


# =====================================================================
# Family "doc-id", variant 2: Marigold (claims processing)
# =====================================================================
# Shipped: close to sibling Quarry's weighted-sum check - a base-36
# weighted check CHARACTER where the standard wants the luhn-fold
# claim DIGIT; normalize strips separators but never validates the check.

_K2_SHIPPED_DOC_ID_PY = r'''"""Document ids for the claim-intake service.

An id is `<type><date><seq><check>`: the two-letter type, the day as
YYMMDD, the sequence zero-padded to five base-36 digits, and one check
character - the base-36 character at the weighted sum of the payload
values mod 36. Input is trimmed, uppercased and stripped of separators.
"""

import re
from datetime import datetime

_TYPES = ("CL", "RF", "AP")
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHAPE = re.compile(r"^([A-Z]{2})(\d{6})([0-9A-Z]{5})([0-9A-Z])$")


class DocIdError(Exception):
    """A raw identifier we cannot accept."""


def _value(ch):
    """The base-36 value of one character."""
    return int(ch) if ch.isdigit() else ord(ch) - 55


def _check_char(payload):
    """One base-36 check character: the weighted sum mod 36."""
    total = 0
    for i, ch in enumerate(payload):
        total += _value(ch) * (i % 2 + 1)
    return _ALPHABET[total % 36]


def format_doc_id(doc_type, seq, day):
    """The document id for `doc_type`, `seq` and `day` (YYYY-MM-DD)."""
    if doc_type not in _TYPES:
        raise DocIdError("type")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 36 ** 5:
        raise DocIdError("seq")
    if not _DAY.match(day):
        raise DocIdError("day")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise DocIdError("day") from None
    date = day[2:].replace("-", "")
    digits = []
    for _ in range(5):
        digits.append(_ALPHABET[seq % 36])
        seq //= 36
    seq_text = "".join(reversed(digits))
    return f"{doc_type}{date}{seq_text}{_check_char(doc_type + date + seq_text)}"


def normalize_doc_id(raw):
    """The canonical form of a raw id: trimmed, uppercased, separators
    removed."""
    text = re.sub(r"[\s-]", "", str(raw)).upper()
    if not _SHAPE.match(text):
        raise DocIdError("shape")
    return text


def validate_doc_id(raw):
    """One verdict per raw id: ok / bad-check / bad-format."""
    text = re.sub(r"[\s-]", "", str(raw)).upper()
    match = _SHAPE.match(text)
    if match is None:
        return "bad-format"
    doc_type, date, seq_text, check = match.groups()
    if doc_type not in _TYPES:
        return "bad-format"
    if _check_char(doc_type + date + seq_text) != check:
        return "bad-check"
    return "ok"
'''

# Fixed: the Marigold standard - the luhn-fold claim digit, check-validated
# normalization, the key without the digit.
_K2_FIX_DOC_ID_PY = r'''"""Document ids for the claim-intake service - the internal policy.

The separator-free layout, the base-36 sequence, the claim digit (the
luhn-fold check) and input normalization. Fail closed on anything the
policy does not name.
"""

import re
from datetime import datetime

_TYPES = ("CL", "RF", "AP")
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHAPE = re.compile(r"^([A-Z]{2})(\d{6})([0-9A-Z]{5})(\d)$")


class MarigoldIdError(Exception):
    """A raw identifier the policy rejects."""


def _value(ch):
    """The base-36 value of one character."""
    return int(ch) if ch.isdigit() else ord(ch) - 55


def _claim_digit(payload):
    """The claim digit: the luhn-fold of the payload - walk right to left,
    double every second value and fold it back to one digit."""
    total = 0
    for i, ch in enumerate(reversed(payload)):
        value = _value(ch)
        if i % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return str((10 - total % 10) % 10)


def _render_seq(seq):
    """The sequence in base 36, five characters, most significant first."""
    digits = []
    for _ in range(5):
        digits.append(_ALPHABET[seq % 36])
        seq //= 36
    return "".join(reversed(digits))


def format_doc_id(doc_type, seq, day):
    """The document id: `<type><date><seq><digit>` - no separators."""
    if doc_type not in _TYPES:
        raise MarigoldIdError("type")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 36 ** 5:
        raise MarigoldIdError("seq")
    if not _DAY.match(day):
        raise MarigoldIdError("day")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise MarigoldIdError("day") from None
    date = day[2:].replace("-", "")
    seq_text = _render_seq(seq)
    return f"{doc_type}{date}{seq_text}{_claim_digit(doc_type + date + seq_text)}"


def normalize_doc_id(raw):
    """The canonical identifier: trimmed, uppercased, every space and
    hyphen removed, the claim digit recomputed and matched."""
    text = re.sub(r"[\s-]", "", str(raw)).upper()
    match = _SHAPE.match(text)
    if match is None or match.group(1) not in _TYPES:
        raise MarigoldIdError("shape")
    doc_type, date, seq_text, digit = match.groups()
    if _claim_digit(doc_type + date + seq_text) != digit:
        raise MarigoldIdError("digit")
    return text


def validate_doc_id(raw):
    """One verdict per raw id: ok / digit-off / layout-off."""
    text = re.sub(r"[\s-]", "", str(raw)).upper()
    match = _SHAPE.match(text)
    if match is None or match.group(1) not in _TYPES:
        return "layout-off"
    doc_type, date, seq_text, digit = match.groups()
    if _claim_digit(doc_type + date + seq_text) != digit:
        return "digit-off"
    return "ok"
'''

_K2_CASES = r"""FORMAT_CASES = [
    ("CL", 0, "2026-03-01"),
    ("RF", 7, "2026-06-15"),
    ("AP", 4242, "2026-03-01"),
    ("CL", 4242, "2026-03-01"),
    ("AP", 60466175, "2024-02-29"),
    ("CL", 60466176, "2026-03-01"),
    ("ZZ", 5, "2026-03-01"),
    ("cl", 5, "2026-03-01"),
    ("CL", -3, "2026-03-01"),
    ("CL", 5, "2026-04-31"),
    ("CL", 5, "26-03-01"),
    ("RF", 999, "2025-12-31"),
]

NORM_CASES = [
    "CL2603010039U0",
    "cl2603010039u0",
    "  CL2603010039U0 ",
    "RF260615000002",
    "CL-260301-0039U-0",
    "CL 260301 0039U 0",
    "CL2603010039U1",
    "RF260615000007",
    "ZZ2603010039U0",
    "CL2603010039U",
    "CL2603010o39U0",
]

VALIDATE_CASES = [
    "CL2603010039U0",
    "cl2603010039u0",
    "CL-260301-0039U-0",
    "CL2603010039U1",
    "RF260615000007",
    "RF260615000002",
    "ZZ2603010039U0",
    "CL2603010039U",
    "CL 260301 0039U 0",
    "AP240229ZZZZZ7",
    "CL2603O10039U0",
]

REPORT_CASES = [
    ("CL", 4242, "2026-03-01", "cl2603010039u0"),
    ("RF", 7, "2026-06-15", "RF260615000002"),
    ("AP", 11, "2026-03-01", "CL-260301-0039U-0"),
    ("RF", 3, "2024-02-29", "  RF260615000002 "),
    ("CL", 5, "2026-04-31", "CL2603010039U0"),
]

SAME_CASES = [
    ("CL2603010039U0", "cl2603010039u0"),
    ("CL2603010039U0", "CL-260301-0039U-0"),
    ("CL2603010039U0", "CL2603010039U0"),
    ("CL2603010039U0", "RF260615000002"),
    ("CL2603010039U0", "CL2603010039U1"),
]
"""

_K2_SKILL = """---
name: marigold-doc-id
description: Document id standard for Marigold claims services (claim-intake, referral-desk).
version: 1.0.0
---

# Marigold Document Identifier Standard (internal)

How every document identifier on the Marigold claims services is built,
checked and normalized. Internal to the Marigold claims processing
team - not published anywhere.

## Scope

The Marigold claims services: claim-intake and referral-desk.

## Layout

An identifier is `<type><date><seq><digit>` - one run of characters, NO
separators:

- `<type>` - two uppercase letters from the type table: CL, RF, AP.
- `<date>` - the day as YYMMDD, six digits (the two-digit year).
- `<seq>` - the sequence number in base 36
  (`0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ`), exactly five characters,
  most significant first, padded with the zero digit `0`.
- `<digit>` - one claim digit (below).

`format_doc_id(type, seq, day)` builds the identifier. The type must
be in the table EXACTLY as given (no case folding); the day must be a
real calendar day in strict `YYYY-MM-DD` form (only the last six digits
are carried); the sequence must fit five base-36 characters (0 through
60466175). Anything else is refused.

## The claim digit

The claim digit is ONE DECIMAL DIGIT computed over the payload - type +
date + seq, thirteen characters - by the luhn-fold:

1. Each character counts its base-36 value: a digit is its number, a
   letter is `ord(c) - 55` (A=10 .. Z=35).
2. Walk the payload RIGHT to LEFT. Every SECOND character (the
   second from the right, the fourth, and so on) is doubled; a doubled
   value above 9 is folded back by subtracting 9.
3. The claim digit is `(10 - total % 10) % 10`, as a string.

## Normalization

`normalize_doc_id(raw)` returns the canonical identifier and nothing
else:

1. Strip surrounding whitespace, remove EVERY hyphen and space, then
   uppercase the whole string - lowercase input is accepted and folded
   up.
2. The type must be in the table, the date six digits, the sequence
   five base-36 characters, the digit one decimal digit.
3. The claim digit is recomputed from the payload and must match, or
   the input is refused with `MarigoldIdError`.

## Validation verdicts

`validate_doc_id(raw)` never raises and returns exactly one verdict:

- `ok` - the identifier normalizes cleanly.
- `digit-off` - the layout is right but the claim digit is not the
  one the luhn-fold gives.
- `layout-off` - anything else: wrong layout, unknown type, a
  character outside the alphabet, a missing part.

## The registry key

`registry_key(raw)` is the canonical identifier with the final digit
removed - the storage key never carries the claim digit.
`same_document(a, b)` normalizes both and compares.

## Worked example

`format_doc_id("CL", 4242, "2026-03-01")` -> `CL2603010039U0`: the
sequence 4242 renders as `0039U`, and the payload `CL2603010039U`
under the luhn-fold totals to 100, so the claim digit is
`(10 - 100 % 10) % 10 = 0`.

`normalize_doc_id("CL-260301-0039U-0")` -> `CL2603010039U0` (separators
removed, folded up); `validate_doc_id("CL2603010039U1")` -> `digit-off`.
"""

_K2_PROMPT = (
    "The tests in test_doc_id_convention.py fail. This claim-intake module "
    "doesn't follow how we build document identifiers here: what an "
    "identifier is made of, and how it is checked, is decided by an internal "
    "policy of ours, not by guesswork. Find the root cause before proposing "
    "any fix, then fix it and make the whole test suite green."
)

_K2_MARKERS = [
    "claim digit",
    "MarigoldIdError",
    "digit-off",
    "layout-off",
    "luhn-fold",
]


# =====================================================================
# Family "doc-id", variant 3: Cobalt (engineering docs)
# =====================================================================
# Shipped: the plausible public convention - a SINGLE check hex digit
# (byte sum mod 16) where the standard wants the two-digit position-sum
# hex pair; normalize folds down but never validates the check.

_K3_SHIPPED_DOC_ID_PY = r'''"""Document ids for the spec-hub service.

An id is `<type>-<date>-<seq>-<check>`: the three-letter type, the day
as YYMMDD, the sequence zero-padded to four hex digits, and one check
hex digit - the sum of the payload bytes mod 16. Input is trimmed and
lowercased; the hyphens are required.
"""

import re
from datetime import datetime

_TYPES = ("spe", "dwr", "cad")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHAPE = re.compile(r"^([a-z]{3})-(\d{6})-([0-9a-f]{4})-([0-9a-f])$")


class DocIdError(Exception):
    """A raw identifier we cannot accept."""


def _check_digit(payload):
    """One check hex digit: the byte sum mod 16."""
    total = sum(ord(ch) for ch in payload)
    return f"{total % 16:x}"


def format_doc_id(doc_type, seq, day):
    """The document id for `doc_type`, `seq` and `day` (YYYY-MM-DD)."""
    if doc_type not in _TYPES:
        raise DocIdError("type")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 16 ** 4:
        raise DocIdError("seq")
    if not _DAY.match(day):
        raise DocIdError("day")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise DocIdError("day") from None
    date = day[2:].replace("-", "")
    seq_text = f"{seq:04x}"
    return f"{doc_type}-{date}-{seq_text}-{_check_digit(doc_type + date + seq_text)}"


def normalize_doc_id(raw):
    """The canonical form of a raw id: trimmed, lowercased."""
    text = str(raw).strip().lower()
    if not _SHAPE.match(text):
        raise DocIdError("shape")
    return text


def validate_doc_id(raw):
    """One verdict per raw id: ok / bad-check / bad-format."""
    text = str(raw).strip().lower()
    match = _SHAPE.match(text)
    if match is None:
        return "bad-format"
    doc_type, date, seq_text, check = match.groups()
    if doc_type not in _TYPES:
        return "bad-format"
    if _check_digit(doc_type + date + seq_text) != check:
        return "bad-check"
    return "ok"
'''

# Fixed: the Cobalt standard - the position-sum hex pair, check-validated
# normalization, the key without the pair.
_K3_FIX_DOC_ID_PY = r'''"""Document ids for the spec-hub service - the internal policy.

The lowercase layout, the hex sequence, the hex pair (the position-sum
check) and input normalization. Fail closed on anything the policy does
not name.
"""

import re
from datetime import datetime

_TYPES = ("spe", "dwr", "cad")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHAPE = re.compile(r"^([a-z]{3})-(\d{6})-([0-9a-f]{4})-([0-9a-f]{2})$")


class CobaltIdError(Exception):
    """A raw identifier the policy rejects."""


def _hex_pair(payload):
    """The hex pair: the position sum (each character's code times its
    1-based place) mod 256, as two lowercase hex digits."""
    total = sum(ord(ch) * (i + 1) for i, ch in enumerate(payload))
    return f"{total % 256:02x}"


def format_doc_id(doc_type, seq, day):
    """The document id: `<type>-<date>-<seq>-<pair>`."""
    if doc_type not in _TYPES:
        raise CobaltIdError("type")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 16 ** 4:
        raise CobaltIdError("seq")
    if not _DAY.match(day):
        raise CobaltIdError("day")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise CobaltIdError("day") from None
    date = day[2:].replace("-", "")
    seq_text = f"{seq:04x}"
    return f"{doc_type}-{date}-{seq_text}-{_hex_pair(doc_type + date + seq_text)}"


def normalize_doc_id(raw):
    """The canonical identifier: trimmed, lowercased, hyphens required at
    the canonical positions, the hex pair recomputed and matched."""
    text = str(raw).strip().lower()
    match = _SHAPE.match(text)
    if match is None or match.group(1) not in _TYPES:
        raise CobaltIdError("shape")
    doc_type, date, seq_text, pair = match.groups()
    if _hex_pair(doc_type + date + seq_text) != pair:
        raise CobaltIdError("pair")
    return text


def validate_doc_id(raw):
    """One verdict per raw id: ok / crc-off / frame-off."""
    text = str(raw).strip().lower()
    match = _SHAPE.match(text)
    if match is None or match.group(1) not in _TYPES:
        return "frame-off"
    doc_type, date, seq_text, pair = match.groups()
    if _hex_pair(doc_type + date + seq_text) != pair:
        return "crc-off"
    return "ok"
'''

_K3_CASES = r"""FORMAT_CASES = [
    ("spe", 0, "2026-03-01"),
    ("dwr", 0, "2026-06-15"),
    ("spe", 4095, "2026-03-01"),
    ("cad", 65535, "2024-02-29"),
    ("cad", 65536, "2026-03-01"),
    ("xyz", 5, "2026-03-01"),
    ("SPE", 5, "2026-03-01"),
    ("spe", -2, "2026-03-01"),
    ("spe", 5, "2026-11-31"),
    ("spe", 5, "2026-3-1"),
    ("dwr", 999, "2025-12-31"),
]

NORM_CASES = [
    "spe-260301-0fff-4e",
    "SPE-260301-0FFF-4E",
    "  spe-260301-0fff-4e ",
    "dwr-260615-0000-1d",
    "spe-260301-0fff-4f",
    "cad-240229-ffff-80",
    "spe 260301 0fff 4e",
    "spe-260301-zzzz-4e",
    "xyz-260301-0fff-4e",
    "spe-260301-0fff",
    "SPE-260301-0fff-4e",
]

VALIDATE_CASES = [
    "spe-260301-0fff-4e",
    "SPE-260301-0FFF-4E",
    "spe-260301-0fff-4f",
    "dwr-260615-0000-1d",
    "spe 260301 0fff 4e",
    "spe-260301-zzzz-4e",
    "xyz-260301-0fff-4e",
    "spe-260301-0fff",
    "cad-240229-ffff-80",
    "spe-260301-0ffg-4e",
    "SPE-260301-0fff-4e",
]

REPORT_CASES = [
    ("spe", 4095, "2026-03-01", "SPE-260301-0FFF-4E"),
    ("dwr", 0, "2026-06-15", "dwr-260615-0000-1d"),
    ("spe", 77, "2026-03-01", "spe-260301-0fff-4f"),
    ("cad", 9, "2024-02-29", "  cad-240229-ffff-80  "),
    ("spe", 5, "2026-11-31", "spe-260301-0fff-4e"),
]

SAME_CASES = [
    ("spe-260301-0fff-4e", "SPE-260301-0FFF-4E"),
    ("spe-260301-0fff-4e", "spe-260301-0fff-4e"),
    ("spe-260301-0fff-4e", "dwr-260615-0000-1d"),
    ("spe-260301-0fff-4e", "spe-260301-0fff-4f"),
    ("spe-260301-0fff-4e", "SPE-260301-0FFF-4F"),
]
"""

_K3_SKILL = """---
name: cobalt-doc-id
description: Document id standard for Cobalt docs services (spec-hub, drawing-store).
version: 1.0.0
---

# Cobalt Document Identifier Standard (internal)

How every document identifier on the Cobalt engineering-docs services
is laid out, checked and normalized. Internal to the Cobalt
engineering docs team - not published anywhere.

## Scope

The Cobalt docs services: spec-hub and drawing-store.

## Layout

An identifier is `<type>-<date>-<seq>-<pair>`, all lowercase:

- `<type>` - three lowercase letters from the type table: spe, dwr,
  cad.
- `<date>` - the day as YYMMDD, six digits (the two-digit year).
- `<seq>` - the sequence number in lowercase hex, exactly four
  digits, most significant first, padded with `0000`.
- `<pair>` - two lowercase hex digits: the hex pair (below).

`format_doc_id(type, seq, day)` builds the identifier. The type must
be in the table EXACTLY as given (no case folding); the day must be a
real calendar day in strict `YYYY-MM-DD` form (only the last six
digits are carried); the sequence must fit four hex digits (0 through
65535). Anything else is refused.

## The hex pair

The hex pair is TWO lowercase hex digits computed over the payload -
type + date + seq, thirteen characters - by the position sum:

1. Each character counts its character code.
2. Each code is multiplied by its 1-based place in the payload (the
   first character counts once, the second twice, and so on).
3. The total mod 256 is written as two lowercase hex digits.

## Normalization

`normalize_doc_id(raw)` returns the canonical identifier and nothing
else:

1. Strip surrounding whitespace, then lowercase the whole string -
   uppercase input is accepted and folded down.
2. The three hyphens are REQUIRED at exactly the canonical positions;
   internal whitespace is refused.
3. The type must be in the table, the date six digits, the sequence
   four hex digits, the pair two hex digits.
4. The hex pair is recomputed from the payload and must match, or
   the input is refused with `CobaltIdError`.

## Validation verdicts

`validate_doc_id(raw)` never raises and returns exactly one verdict:

- `ok` - the identifier normalizes cleanly.
- `crc-off` - the layout is right but the hex pair is not the one
  the position sum gives.
- `frame-off` - anything else: wrong layout, unknown type, a
  character outside the alphabet, a missing part.

## The registry key

`registry_key(raw)` is the canonical identifier with the trailing
`-<pair>` removed - the storage key never carries the hex pair.
`same_document(a, b)` normalizes both and compares.

## Worked example

`format_doc_id("spe", 4095, "2026-03-01")` -> `spe-260301-0fff-4e`:
the sequence 4095 renders as `0fff`, and the payload `spe2603010fff`
under the position sum totals to 6734; 6734 mod 256 is 78, which
writes as the hex pair `4e`.

`normalize_doc_id("SPE-260301-0FFF-4E")` -> `spe-260301-0fff-4e`
(folded down, hyphens kept); `validate_doc_id("spe-260301-0fff-4f")`
-> `crc-off`.
"""

_K3_PROMPT = (
    "The tests in test_doc_id_convention.py fail. This spec-hub module "
    "doesn't follow how we shape document identifiers here: the pieces an "
    "identifier is built from, and how it is checked, are decided by an "
    "internal policy of ours, not by guesswork. Find the root cause before "
    "proposing any fix, then fix it and make the whole test suite green."
)

_K3_MARKERS = [
    "hex pair",
    "CobaltIdError",
    "crc-off",
    "frame-off",
    "position sum",
]


# =====================================================================
# Family "doc-id", variant 4: Tundra (logistics manifests)
# =====================================================================
# Shipped: close to sibling Marigold's digit check - a Luhn DIGIT where
# the standard wants the mod-26 route LETTER; normalize never validates
# the check.

_K4_SHIPPED_DOC_ID_PY = r'''"""Document ids for the manifest-board service.

An id is `<type>/<date>/<seq>-<check>`: the three-letter type, the day
as YYMMDD, the sequence zero-padded to four decimal digits, and one
Luhn check digit over the type letters, the date and the sequence
digits. Input is trimmed and uppercased; the separators are required.
"""

import re
from datetime import datetime

_TYPES = ("MAN", "WAY", "BOL")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHAPE = re.compile(r"^([A-Z]{3})/(\d{6})/(\d{4})-(\d)$")


class DocIdError(Exception):
    """A raw identifier we cannot accept."""


def _check_digit(text):
    """One Luhn check digit over `text` (letters map A=10 .. Z=35)."""
    total = 0
    for i, ch in enumerate(reversed(text)):
        value = int(ch) if ch.isdigit() else ord(ch) - 55
        if i % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return str((10 - total % 10) % 10)


def format_doc_id(doc_type, seq, day):
    """The document id for `doc_type`, `seq` and `day` (YYYY-MM-DD)."""
    if doc_type not in _TYPES:
        raise DocIdError("type")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 10 ** 4:
        raise DocIdError("seq")
    if not _DAY.match(day):
        raise DocIdError("day")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise DocIdError("day") from None
    date = day[2:].replace("-", "")
    digits = f"{seq:04d}"
    return f"{doc_type}/{date}/{digits}-{_check_digit(doc_type + date + digits)}"


def normalize_doc_id(raw):
    """The canonical form of a raw id: trimmed, uppercased."""
    text = str(raw).strip().upper()
    if not _SHAPE.match(text):
        raise DocIdError("shape")
    return text


def validate_doc_id(raw):
    """One verdict per raw id: ok / bad-check / bad-format."""
    text = str(raw).strip().upper()
    match = _SHAPE.match(text)
    if match is None:
        return "bad-format"
    doc_type, date, digits, check = match.groups()
    if doc_type not in _TYPES:
        return "bad-format"
    if _check_digit(doc_type + date + digits) != check:
        return "bad-check"
    return "ok"
'''

# Fixed: the Tundra standard - the mod-26 route letter, check-validated
# normalization, the key without the letter.
_K4_FIX_DOC_ID_PY = r'''"""Document ids for the manifest-board service - the internal policy.

The slash layout, the decimal sequence, the route letter (the mod-26
check) and input normalization. Fail closed on anything the policy does
not name.
"""

import re
from datetime import datetime

_TYPES = ("MAN", "WAY", "BOL")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHAPE = re.compile(r"^([A-Z]{3})/(\d{6})/(\d{4})-([A-Z])$")


class TundraIdError(Exception):
    """A raw identifier the policy rejects."""


def _route_letter(payload):
    """The route letter: the ordinal sum (each character's code times its
    1-based place) mod 26, read from A."""
    total = sum(ord(ch) * (i + 1) for i, ch in enumerate(payload))
    return chr(ord("A") + total % 26)


def format_doc_id(doc_type, seq, day):
    """The document id: `<type>/<date>/<seq>-<letter>`."""
    if doc_type not in _TYPES:
        raise TundraIdError("type")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 10 ** 4:
        raise TundraIdError("seq")
    if not _DAY.match(day):
        raise TundraIdError("day")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise TundraIdError("day") from None
    date = day[2:].replace("-", "")
    digits = f"{seq:04d}"
    return f"{doc_type}/{date}/{digits}-{_route_letter(doc_type + date + digits)}"


def normalize_doc_id(raw):
    """The canonical identifier: trimmed, uppercased, the slash and the
    hyphen required at the canonical positions, the route letter
    recomputed and matched."""
    text = str(raw).strip().upper()
    match = _SHAPE.match(text)
    if match is None or match.group(1) not in _TYPES:
        raise TundraIdError("shape")
    doc_type, date, digits, letter = match.groups()
    if _route_letter(doc_type + date + digits) != letter:
        raise TundraIdError("letter")
    return text


def validate_doc_id(raw):
    """One verdict per raw id: ok / letter-off / route-off."""
    text = str(raw).strip().upper()
    match = _SHAPE.match(text)
    if match is None or match.group(1) not in _TYPES:
        return "route-off"
    doc_type, date, digits, letter = match.groups()
    if _route_letter(doc_type + date + digits) != letter:
        return "letter-off"
    return "ok"
'''

_K4_CASES = r"""FORMAT_CASES = [
    ("MAN", 0, "2026-03-01"),
    ("WAY", 0, "2026-06-15"),
    ("MAN", 4242, "2026-03-01"),
    ("BOL", 9999, "2024-02-29"),
    ("BOL", 10000, "2026-03-01"),
    ("FRM", 5, "2026-03-01"),
    ("man", 5, "2026-03-01"),
    ("MAN", -4, "2026-03-01"),
    ("MAN", 5, "2026-02-29"),
    ("MAN", 5, "2026-3-1"),
    ("WAY", 777, "2025-12-31"),
]

NORM_CASES = [
    "MAN/260301/4242-T",
    "man/260301/4242-t",
    "  MAN/260301/4242-T ",
    "WAY/260615/0000-R",
    "MAN/260301/4242-U",
    "BOL/240229/9999-P",
    "MAN 260301 4242 T",
    "MAN-260301-4242-T",
    "FRM/260301/4242-T",
    "MAN/260301/4242",
    "MAN/260301/42O2-T",
]

VALIDATE_CASES = [
    "MAN/260301/4242-T",
    "man/260301/4242-t",
    "MAN/260301/4242-U",
    "WAY/260615/0000-R",
    "MAN 260301 4242 T",
    "MAN-260301-4242-T",
    "FRM/260301/4242-T",
    "MAN/260301/4242",
    "BOL/240229/9999-P",
    "WAY/260615/0000-A",
    "MAN/26O301/4242-T",
]

REPORT_CASES = [
    ("MAN", 4242, "2026-03-01", "man/260301/4242-t"),
    ("WAY", 0, "2026-06-15", "WAY/260615/0000-R"),
    ("BOL", 12, "2024-02-29", "  BOL/240229/9999-P "),
    ("MAN", 34, "2026-03-01", "MAN/260301/4242-U"),
    ("MAN", 5, "2026-02-29", "MAN/260301/4242-T"),
]

SAME_CASES = [
    ("MAN/260301/4242-T", "man/260301/4242-t"),
    ("MAN/260301/4242-T", "MAN/260301/4242-T"),
    ("MAN/260301/4242-T", "WAY/260615/0000-R"),
    ("MAN/260301/4242-T", "MAN/260301/4242-U"),
    ("MAN/260301/4242-T", "MAN-260301-4242-T"),
]
"""

_K4_SKILL = """---
name: tundra-doc-id
description: Document id standard for Tundra logistics services (manifest-board, waybill-print).
version: 1.0.0
---

# Tundra Document Identifier Standard (internal)

How every document identifier on the Tundra logistics services is laid
out, checked and normalized. Internal to the Tundra logistics
manifests team - not published anywhere.

## Scope

The Tundra logistics services: manifest-board and waybill-print.

## Layout

An identifier is `<type>/<date>/<seq>-<letter>`:

- `<type>` - three uppercase letters from the type table: MAN, WAY,
  BOL.
- `<date>` - the day as YYMMDD, six digits (the two-digit year).
- `<seq>` - the sequence number in plain decimal, exactly four
  digits, most significant first, padded with `0000`.
- `<letter>` - one uppercase letter: the route letter (below).

`format_doc_id(type, seq, day)` builds the identifier. The type must
be in the table EXACTLY as given (no case folding); the day must be a
real calendar day in strict `YYYY-MM-DD` form (only the last six digits
are carried); the sequence must fit four digits (0 through 9999).
Anything else is refused.

## The route letter

The route letter is one uppercase letter computed over the payload -
type + date + seq, thirteen characters - by the ordinal sum:

1. Each character counts its character code.
2. Each code is multiplied by its 1-based place in the payload (the
   first character counts once, the second twice, and so on).
3. The total mod 26 is read from A: 0 -> A, 1 -> B, ..., 25 -> Z.

## Normalization

`normalize_doc_id(raw)` returns the canonical identifier and nothing
else:

1. Strip surrounding whitespace, then uppercase the whole string -
   lowercase input is accepted and folded up.
2. The two slashes and the one hyphen are REQUIRED at exactly the
   canonical positions; internal whitespace is refused.
3. The type must be in the table, the date six digits, the sequence
   four digits, the letter one uppercase letter.
4. The route letter is recomputed from the payload and must match,
   or the input is refused with `TundraIdError`.

## Validation verdicts

`validate_doc_id(raw)` never raises and returns exactly one verdict:

- `ok` - the identifier normalizes cleanly.
- `letter-off` - the layout is right but the route letter is not the
  one the ordinal sum gives.
- `route-off` - anything else: wrong layout, unknown type, a
  character outside the alphabet, a missing part.

## The registry key

`registry_key(raw)` is the canonical identifier with the trailing
`-<letter>` removed - the storage key never carries the route letter.
`same_document(a, b)` normalizes both and compares.

## Worked example

`format_doc_id("MAN", 4242, "2026-03-01")` -> `MAN/260301/4242-T`:
the sequence 4242 renders as `4242`, and the payload `MAN2603014242`
under the ordinal sum totals to 4725, and 4725 mod 26 is 19, which
reads `T`.

`normalize_doc_id("man/260301/4242-t")` -> `MAN/260301/4242-T`
(folded up, separators kept); `validate_doc_id("MAN/260301/4242-U")`
-> `letter-off`.
"""

_K4_PROMPT = (
    "The tests in test_doc_id_convention.py fail. This manifest-board module "
    "doesn't follow how we assemble document identifiers here: what goes "
    "where, and how it is checked, is decided by an internal policy of "
    "ours, not by guesswork. Find the root cause before proposing any fix, "
    "then fix it and make the whole test suite green."
)

_K4_MARKERS = [
    "route letter",
    "TundraIdError",
    "letter-off",
    "route-off",
    "ordinal sum",
]


# =====================================================================
# Family "doc-id", variant 5: Gossamer (legal contracts)
# =====================================================================
# Shipped: close to sibling Marigold's weighted base-36 check CHARACTER
# where the standard wants the 1-3 weighted weave over base32; normalize
# strips separators but never validates the check.

_K5_SHIPPED_DOC_ID_PY = r'''"""Document ids for the contract-draft service.

An id is `<type><date><seq><check>`: the two-letter type, the day as
YYMMDD, the sequence zero-padded to seven base-36 digits, and one check
character - the base-36 character at the weighted sum of the payload
values mod 36. Input is trimmed, lowercased and stripped of separators.
"""

import re
from datetime import datetime

_TYPES = ("ct", "am", "nd")
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHAPE = re.compile(r"^([a-z]{2})(\d{6})([0-9a-z]{7})([0-9a-z])$")


class DocIdError(Exception):
    """A raw identifier we cannot accept."""


def _value(ch):
    """The base-36 value of one character."""
    return int(ch) if ch.isdigit() else ord(ch) - 87


def _check_char(payload):
    """One base-36 check character: the weighted sum mod 36."""
    total = 0
    for i, ch in enumerate(payload):
        total += _value(ch) * (i % 2 + 1)
    return _ALPHABET[total % 36]


def format_doc_id(doc_type, seq, day):
    """The document id for `doc_type`, `seq` and `day` (YYYY-MM-DD)."""
    if doc_type not in _TYPES:
        raise DocIdError("type")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 36 ** 7:
        raise DocIdError("seq")
    if not _DAY.match(day):
        raise DocIdError("day")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise DocIdError("day") from None
    date = day[2:].replace("-", "")
    digits = []
    for _ in range(7):
        digits.append(_ALPHABET[seq % 36])
        seq //= 36
    seq_text = "".join(reversed(digits))
    return f"{doc_type}{date}{seq_text}{_check_char(doc_type + date + seq_text)}"


def normalize_doc_id(raw):
    """The canonical form of a raw id: trimmed, lowercased, separators
    removed."""
    text = re.sub(r"[\s-]", "", str(raw)).lower()
    if not _SHAPE.match(text):
        raise DocIdError("shape")
    return text


def validate_doc_id(raw):
    """One verdict per raw id: ok / bad-check / bad-format."""
    text = re.sub(r"[\s-]", "", str(raw)).lower()
    match = _SHAPE.match(text)
    if match is None:
        return "bad-format"
    doc_type, date, seq_text, check = match.groups()
    if doc_type not in _TYPES:
        return "bad-format"
    if _check_char(doc_type + date + seq_text) != check:
        return "bad-check"
    return "ok"
'''

# Fixed: the Gossamer standard - the 1-3 weighted weave over base32,
# check-validated normalization, the key without the weave.
_K5_FIX_DOC_ID_PY = r'''"""Document ids for the contract-draft service - the internal policy.

The separator-free lowercase layout, the base-32 sequence, the weave
(the 1-3 weighted check) and input normalization. Fail closed on
anything the policy does not name.
"""

import re
from datetime import datetime

_TYPES = ("ct", "am", "nd")
_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHAPE = re.compile(r"^([a-z]{2})(\d{6})([a-z2-7]{7})([a-z2-7])$")


class GossamerIdError(Exception):
    """A raw identifier the policy rejects."""


def _value(ch):
    """The base-36 value of one character: a digit is its number, a letter
    is ord(c) - 87 (a=10 .. z=35)."""
    return int(ch) if ch.isdigit() else ord(ch) - 87


def _weave(payload):
    """The weave of a payload under the cycling 1-3 weights."""
    total = sum(_value(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(payload))
    return _ALPHABET[total % 32]


def _render_seq(seq):
    """The sequence in the alphabet, seven characters, most significant
    first, padded with the zero letter a."""
    digits = []
    for _ in range(7):
        digits.append(_ALPHABET[seq % 32])
        seq //= 32
    return "".join(reversed(digits))


def format_doc_id(doc_type, seq, day):
    """The document id: `<type><date><seq><weave>` - no separators."""
    if doc_type not in _TYPES:
        raise GossamerIdError("type")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 32 ** 7:
        raise GossamerIdError("seq")
    if not _DAY.match(day):
        raise GossamerIdError("day")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise GossamerIdError("day") from None
    date = day[2:].replace("-", "")
    seq_text = _render_seq(seq)
    return f"{doc_type}{date}{seq_text}{_weave(doc_type + date + seq_text)}"


def normalize_doc_id(raw):
    """The canonical identifier: trimmed, lowercased, every space and
    hyphen removed, the weave recomputed and matched."""
    text = re.sub(r"[\s-]", "", str(raw)).lower()
    match = _SHAPE.match(text)
    if match is None or match.group(1) not in _TYPES:
        raise GossamerIdError("shape")
    doc_type, date, seq_text, mark = match.groups()
    if _weave(doc_type + date + seq_text) != mark:
        raise GossamerIdError("mark")
    return text


def validate_doc_id(raw):
    """One verdict per raw id: ok / weave-off / form-off."""
    text = re.sub(r"[\s-]", "", str(raw)).lower()
    match = _SHAPE.match(text)
    if match is None or match.group(1) not in _TYPES:
        return "form-off"
    doc_type, date, seq_text, mark = match.groups()
    if _weave(doc_type + date + seq_text) != mark:
        return "weave-off"
    return "ok"
'''

_K5_CASES = r"""FORMAT_CASES = [
    ("ct", 0, "2026-03-01"),
    ("am", 0, "2026-06-15"),
    ("ct", 9876543, "2026-03-01"),
    ("nd", 34359738367, "2024-02-29"),
    ("nd", 34359738368, "2026-03-01"),
    ("zz", 5, "2026-03-01"),
    ("CT", 5, "2026-03-01"),
    ("ct", -9, "2026-03-01"),
    ("ct", 5, "2026-13-01"),
    ("ct", 5, "2026-3-1"),
    ("am", 4242, "2025-12-31"),
]

NORM_CASES = [
    "ct260301aajnnb7c",
    "CT260301AAJNNB7C",
    "  ct260301aajnnb7c ",
    "am260615aaaaaaae",
    "ct-260301-aajnnb7-c",
    "ct 260301 aajnnb7 c",
    "ct260301aajnnb7d",
    "am260615aaaaaaaf",
    "zz260301aajnnb7c",
    "ct260301aajnnb7",
    "ct26030100jnnb7c",
]

VALIDATE_CASES = [
    "ct260301aajnnb7c",
    "CT260301AAJNNB7C",
    "ct-260301-aajnnb7-c",
    "ct260301aajnnb7d",
    "am260615aaaaaaaf",
    "am260615aaaaaaae",
    "zz260301aajnnb7c",
    "ct260301aajnnb7",
    "ct 260301 aajnnb7 c",
    "nd2402297777777k",
    "ct2603010ajnnb7c",
]

REPORT_CASES = [
    ("ct", 9876543, "2026-03-01", "CT260301AAJNNB7C"),
    ("am", 0, "2026-06-15", "am260615aaaaaaae"),
    ("nd", 21, "2024-02-29", "  nd2402297777777k "),
    ("ct", 55, "2026-03-01", "ct-260301-aajnnb7-c"),
    ("ct", 5, "2026-13-01", "ct260301aajnnb7c"),
]

SAME_CASES = [
    ("ct260301aajnnb7c", "CT260301AAJNNB7C"),
    ("ct260301aajnnb7c", "ct-260301-aajnnb7-c"),
    ("ct260301aajnnb7c", "ct260301aajnnb7c"),
    ("ct260301aajnnb7c", "am260615aaaaaaae"),
    ("ct260301aajnnb7c", "ct260301aajnnb7d"),
]
"""

_K5_SKILL = """---
name: gossamer-doc-id
description: Document id standard for Gossamer legal services (contract-draft, clause-library).
version: 1.0.0
---

# Gossamer Document Identifier Standard (internal)

How every document identifier on the Gossamer legal services is built,
checked and normalized. Internal to the Gossamer legal contracts team -
not published anywhere.

## Scope

The Gossamer legal services: contract-draft and clause-library.

## Layout

An identifier is `<type><date><seq><weave>` - one run of characters, NO
separators, all lowercase:

- `<type>` - two lowercase letters from the type table: ct, am, nd.
- `<date>` - the day as YYMMDD, six digits (the two-digit year).
- `<seq>` - the sequence number in the Gossamer alphabet, exactly
  seven characters, most significant first, padded with the zero
  letter a.
- `<weave>` - one character of the weave (below).

The Gossamer alphabet is the 32-character set
`abcdefghijklmnopqrstuvwxyz234567` (lowercase base32).

`format_doc_id(type, seq, day)` builds the identifier. The type must
be in the table EXACTLY as given (no case folding); the day must be a
real calendar day in strict `YYYY-MM-DD` form (only the last six
digits are carried); the sequence must fit seven alphabet characters
(0 through 34359738367). Anything else is refused.

## The weave

The weave is one alphabet character computed over the payload - type +
date + seq, fifteen characters - under the cycling 1-3 weights:

1. Each character counts its base-36 value: a digit is its number, a
   letter is `ord(c) - 87` (a=10 .. z=35).
2. Each value is multiplied by the weights cycle 1-3: the first
   character gets 1, the second 3, the third 1 again, and so on.
3. The total mod 32 reads the alphabet at that index.

## Normalization

`normalize_doc_id(raw)` returns the canonical identifier and nothing
else:

1. Strip surrounding whitespace, remove EVERY hyphen and space, then
   lowercase the whole string - uppercase input is accepted and folded
   down.
2. The type must be in the table, the date six digits, the sequence
   seven alphabet characters, the weave one alphabet character.
3. The weave is recomputed from the payload and must match, or the
   input is refused with `GossamerIdError`.

## Validation verdicts

`validate_doc_id(raw)` never raises and returns exactly one verdict:

- `ok` - the identifier normalizes cleanly.
- `weave-off` - the layout is right but the weave is not the one
  the payload gives.
- `form-off` - anything else: wrong layout, unknown type, a
  character outside the alphabet, a missing part.

## The registry key

`registry_key(raw)` is the canonical identifier with the final weave
character removed - the storage key never carries the weave.
`same_document(a, b)` normalizes both and compares.

## Worked example

`format_doc_id("ct", 9876543, "2026-03-01")` -> `ct260301aajnnb7c`:
the sequence 9876543 renders as `aajnnb7`, and the payload
`ct260301aajnnb7` under the cycling 1-3 weights totals to the
alphabet index 2, which reads `c`.

`normalize_doc_id("CT-260301-AAJNNB7-C")` -> `ct260301aajnnb7c`
(separators removed, folded down); `validate_doc_id("ct260301aajnnb7d")`
-> `weave-off`.
"""

_K5_PROMPT = (
    "The tests in test_doc_id_convention.py fail. This contract-draft module "
    "doesn't follow how we form document identifiers here: the pieces and "
    "their order, and how an identifier is checked, are decided by an "
    "internal policy of ours, not by guesswork. Find the root cause before "
    "proposing any fix, then fix it and make the whole test suite green."
)

_K5_MARKERS = [
    "weave",
    "GossamerIdError",
    "weave-off",
    "form-off",
    "cycle 1-3",
]


# The digest pins per doc-id variant - sha256 over each group's canonical
# trace, computed from the FIXED implementations (the standard) and
# verified by tests/unit/test_private3_tasks_d.py.
_K1_DIGESTS = """    "g1": "80ea7292541def5094ba2f55230a913b80fc3c8c21d01304b90a3c2b342f1221",
    "g2": "36f4b90d1be0a678b3e6852324aeda17a57ef369ccc3dcc8d0f252dbdadb7b2a",
    "g3": "f8d60c6737a267736689450a28856661925f1d1004f35c705cf5de247f6bdb7d",
    "g4": "94d9a10440fd66096ea97153aec6413cc17eded3428689c841d29d1d6da7e100",
    "g5": "4c177dc2343f117db36aedac06e4f57531ffc380b90e1b8cd3d915891d263781"
"""
_K2_DIGESTS = """    "g1": "d32686f0f589371b9b16bbc1ae509695e5e4648b3ec42aa997a58601fccf0f28",
    "g2": "7b443aae01cbda78674fbf5415242439f23d0198534c8c762898ac9128f58614",
    "g3": "fd2e41bf2096d7b5c33bc844a90dedefbd04bd1317cb636765b9ffa8d71f847a",
    "g4": "228c8fa7bca9b7b1b0ccdfaa19e99b6adba468529a09d05c7a5c1127eb26e131",
    "g5": "786f89e2550c5886126ddd930eeb48e1cc8674a6fb0ad4a5513b2c9295385254"
"""
_K3_DIGESTS = """    "g1": "612b282e3d93df044dcb9764dc2440c72c08ece7bf52ef3836122b4ee6e01000",
    "g2": "5a38815b40801373237c908e01872ed1fc2b513ea215350977ba359e0646c52b",
    "g3": "b793085b8ec3cf58f9d84c414f0e1baa3acc5c7cff6f3b04e636e9f254a2395d",
    "g4": "3dcc8318eae011b7e04ecbd0a4b70d02b83eb9882c1faad4189b6c1af77a259a",
    "g5": "f0af7fe27c8fbc6102006862ba84d833d4bd4c0228c1d4078cb5ddbe8621c1db"
"""
_K4_DIGESTS = """    "g1": "a008085551c75358af20f44899461e6c89a7557bac62a783bc7af84889b930af",
    "g2": "c4a4d42a349706018f6edeee0106cf07835d94097e7657939c2e621801504def",
    "g3": "37b91393bad8e2637ca178db09fb6c3f15c059766e30c8cbbac8027703ac04a6",
    "g4": "4f79b9951f207c4bbd642fdf74ff27dbb353212a46da13799dc785f0eecb0387",
    "g5": "20e09b5eb2592cc812cd95eb39a3c063e70db4a1cbc9e401fe2d79e18d765b21"
"""
_K5_DIGESTS = """    "g1": "c60c1addf8ae4fb9f66918184c6847d5ab689c434dc74e187c3a3128b7e9feed",
    "g2": "2a1339ccf6b82a59a4eb6559a15619a85eec9f846f19c2d91a16c947efb44d82",
    "g3": "88877d6ba86f8c2a3b01a479b62e3c73194ab5c7cee3445a0034dbf9349bf7ec",
    "g4": "9f8a590a70f7b1141a9fece52de24a11662e7f9fdfa5990fdd46c5715c2ed5ab",
    "g5": "1eba5f5e71ace2909338f7000834c1e1010b879022fa14ffb397c0eba70f6cec"
"""


# --- the family "doc-id" task entries ---------------------------------
_DOC_ID_TASKS: list[dict[str, Any]] = [
    _private3_task(
        "private3-quarry-doc-id",
        "doc-id",
        {
            "doc_id.py": _K1_SHIPPED_DOC_ID_PY,
            "doc_registry.py": _doc_id_shipped_registry_py("deed-index"),
            "service_report.py": _doc_id_service_report_py("deed-index"),
            "test_doc_id_convention.py": _doc_id_test_py("deed-index", _K1_CASES, _K1_DIGESTS),
        },
        _K1_PROMPT,
        "quarry-doc-id",
        _K1_SKILL,
        _K1_MARKERS,
    ),
    _private3_task(
        "private3-marigold-doc-id",
        "doc-id",
        {
            "doc_id.py": _K2_SHIPPED_DOC_ID_PY,
            "doc_registry.py": _doc_id_shipped_registry_py("claim-intake"),
            "service_report.py": _doc_id_service_report_py("claim-intake"),
            "test_doc_id_convention.py": _doc_id_test_py("claim-intake", _K2_CASES, _K2_DIGESTS),
        },
        _K2_PROMPT,
        "marigold-doc-id",
        _K2_SKILL,
        _K2_MARKERS,
    ),
    _private3_task(
        "private3-cobalt-doc-id",
        "doc-id",
        {
            "doc_id.py": _K3_SHIPPED_DOC_ID_PY,
            "doc_registry.py": _doc_id_shipped_registry_py("spec-hub"),
            "service_report.py": _doc_id_service_report_py("spec-hub"),
            "test_doc_id_convention.py": _doc_id_test_py("spec-hub", _K3_CASES, _K3_DIGESTS),
        },
        _K3_PROMPT,
        "cobalt-doc-id",
        _K3_SKILL,
        _K3_MARKERS,
    ),
    _private3_task(
        "private3-tundra-doc-id",
        "doc-id",
        {
            "doc_id.py": _K4_SHIPPED_DOC_ID_PY,
            "doc_registry.py": _doc_id_shipped_registry_py("manifest-board"),
            "service_report.py": _doc_id_service_report_py("manifest-board"),
            "test_doc_id_convention.py": _doc_id_test_py("manifest-board", _K4_CASES, _K4_DIGESTS),
        },
        _K4_PROMPT,
        "tundra-doc-id",
        _K4_SKILL,
        _K4_MARKERS,
    ),
    _private3_task(
        "private3-gossamer-doc-id",
        "doc-id",
        {
            "doc_id.py": _K5_SHIPPED_DOC_ID_PY,
            "doc_registry.py": _doc_id_shipped_registry_py("contract-draft"),
            "service_report.py": _doc_id_service_report_py("contract-draft"),
            "test_doc_id_convention.py": _doc_id_test_py("contract-draft", _K5_CASES, _K5_DIGESTS),
        },
        _K5_PROMPT,
        "gossamer-doc-id",
        _K5_SKILL,
        _K5_MARKERS,
    ),
]

# --- the family "doc-id" fixes ----------------------------------------
# Whole-file replacements (the verify_private_fixtures (file, None,
# content) format): the fix IS the module conforming to the variant's
# private standard. The composer (service_report.py) only delegates and
# needs no fix; the tests are never touched.
_DOC_ID_FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    "private3-quarry-doc-id": [
        ("doc_id.py", None, _K1_FIX_DOC_ID_PY),
        (
            "doc_registry.py",
            None,
            _doc_id_fixed_registry_py(
                "deed-index",
                "The storage key: the canonical id minus the trailing -<mark>.",
                "[:-2]",
            ),
        ),
    ],
    "private3-marigold-doc-id": [
        ("doc_id.py", None, _K2_FIX_DOC_ID_PY),
        (
            "doc_registry.py",
            None,
            _doc_id_fixed_registry_py(
                "claim-intake",
                "The storage key: the canonical id minus the claim digit.",
                "[:-1]",
            ),
        ),
    ],
    "private3-cobalt-doc-id": [
        ("doc_id.py", None, _K3_FIX_DOC_ID_PY),
        (
            "doc_registry.py",
            None,
            _doc_id_fixed_registry_py(
                "spec-hub",
                "The storage key: the canonical id minus the trailing -<pair>.",
                "[:-3]",
            ),
        ),
    ],
    "private3-tundra-doc-id": [
        ("doc_id.py", None, _K4_FIX_DOC_ID_PY),
        (
            "doc_registry.py",
            None,
            _doc_id_fixed_registry_py(
                "manifest-board",
                "The storage key: the canonical id minus the trailing -<letter>.",
                "[:-2]",
            ),
        ),
    ],
    "private3-gossamer-doc-id": [
        ("doc_id.py", None, _K5_FIX_DOC_ID_PY),
        (
            "doc_registry.py",
            None,
            _doc_id_fixed_registry_py(
                "contract-draft",
                "The storage key: the canonical id minus the final weave character.",
                "[:-1]",
            ),
        ),
    ],
}


# =====================================================================
# Family "rate-limit" - shared workspace skeleton
# =====================================================================
# All five variants ship the SAME file names and the SAME public API
# (quota_window.window_of / used_in_window / reset_at,
# limit_headers.quota_headers, limit_response.respond) - that is what
# makes the confusion matrix mechanical: FIX_i slots into workspace_j.

_RATE_LIMIT_TEST_TEMPLATE = r'''"""Contract tests for the __SERVICE__ quota responder against our
INTERNAL rate-limit response policy. The policy is not public: each
group's expected behavior is pinned as a sha256 DIGEST over the observed
trace, so this file cannot become a copy of the policy."""

import hashlib

import pytest

from limit_headers import quota_headers
from limit_response import respond
from quota_window import reset_at, used_in_window, window_of


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _req_text(requests):
    return ",".join(str(t) for t in requests)


def _render_headers(headers):
    return "|".join(f"{k}={v}" for k, v in sorted(headers.items()))


def _window_lines():
    lines = []
    for requests, now in WINDOW_CASES:
        start, end = window_of(now)
        used = used_in_window(requests, now)
        reset = reset_at(requests, now)
        lines.append(f"{_req_text(requests)}|{now}|{start},{end}|{used}|{reset}")
    return lines


def _header_lines():
    lines = []
    for limit, used, reset, now in HEADER_CASES:
        rendered = _render_headers(quota_headers(limit, used, reset, now))
        lines.append(f"{limit}|{used}|{reset}|{now}|{rendered}")
    return lines


def _respond_lines(cases):
    lines = []
    for limit, requests, now in cases:
        status, headers = respond(limit, requests, now)
        rendered = _render_headers(headers)
        lines.append(f"{limit}|{_req_text(requests)}|{now}|{status}|{rendered}")
    return lines


GROUPS = {
    "g1": _window_lines,
    "g2": _header_lines,
    "g3": lambda: _respond_lines(RESPOND_CASES),
    "g4": lambda: _respond_lines(EDGE_CASES),
}

__CASES__

# sha256 of "\n".join(GROUPS[g]()) per group, from the policy.
DIGESTS = {
__DIGESTS__
}


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = GROUPS[group]()
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''


def _rate_limit_test_py(service: str, cases: str, digests: str) -> str:
    """Assemble the family test file: skeleton + the variant's cases + its
    digest pins."""
    return (
        _RATE_LIMIT_TEST_TEMPLATE.replace("__SERVICE__", service)
        .replace("__CASES__", cases)
        .replace("__DIGESTS__", digests)
    )


# =====================================================================
# Family "rate-limit", variant 1: Beacon (public API gateway)
# =====================================================================
# Shipped: the plausible public convention - unclamped remaining, a
# +1 safety margin on Retry-After (close to sibling Halcyon's grace
# second) - wrong for the exact tide-table seconds.

_R1_SHIPPED_QUOTA_WINDOW_PY = r'''"""Quota windows for the edge-gateway service.

A fixed 60-second window anchored at the epoch minute; a request counts
while it is inside the current window; the quota resets at the window
end.
"""

WINDOW = 60


def window_of(now):
    """(start, end) of the window containing `now`."""
    start = (now // WINDOW) * WINDOW
    return (start, start + WINDOW)


def used_in_window(requests, now):
    """How many requests sit inside the current window."""
    start, end = window_of(now)
    return sum(1 for t in requests if start <= t < end)


def reset_at(requests, now):
    """When the quota resets: the current window's end."""
    return window_of(now)[1]
'''

_R1_SHIPPED_LIMIT_HEADERS_PY = r'''"""Rate-limit response headers for the edge-gateway service."""


def quota_headers(limit, used, reset, now):
    """The response headers: limit, remaining, reset (epoch seconds)."""
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(limit - used),
        "X-RateLimit-Reset": str(reset),
    }
'''

_R1_SHIPPED_LIMIT_RESPONSE_PY = r'''"""The rate-limit response for the edge-gateway service.

A request under the limit is served 200 with the quota headers; a
request at or over the limit is refused 429 with the quota headers and
`Retry-After`: the seconds until the window ends, plus one for safety.
"""

from limit_headers import quota_headers
from quota_window import reset_at, used_in_window


def respond(limit, requests, now):
    """(status, headers) for one request at `now`."""
    used = used_in_window(requests, now)
    reset = reset_at(requests, now)
    headers = quota_headers(limit, used, reset, now)
    if used < limit:
        return (200, headers)
    headers["Retry-After"] = str(reset - now + 1)
    return (429, headers)
'''

# Fixed: the Beacon standard - the clamped tide table, the exact seconds.
_R1_FIX_QUOTA_WINDOW_PY = r'''"""Quota windows for the edge-gateway service - the internal policy.

The gate minute: a fixed 60-second window anchored at the epoch minute.
A request counts while it sits inside the current gate minute; the
quota resets at the window end - the beacon tick - whatever the
requests.
"""

WINDOW = 60


def window_of(now):
    """(start, end) of the gate minute containing `now`."""
    start = (now // WINDOW) * WINDOW
    return (start, start + WINDOW)


def used_in_window(requests, now):
    """How many requests sit inside the current gate minute."""
    start, end = window_of(now)
    return sum(1 for t in requests if start <= t < end)


def reset_at(requests, now):
    """The beacon tick: the current gate minute's end."""
    return window_of(now)[1]
'''

_R1_FIX_LIMIT_HEADERS_PY = r'''"""The tide table - the edge-gateway response headers."""


def quota_headers(limit, used, reset, now):
    """The tide table: limit, remaining (never negative), reset (epoch)."""
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(0, limit - used)),
        "X-RateLimit-Reset": str(reset),
    }
'''

_R1_FIX_LIMIT_RESPONSE_PY = r'''"""The edge-gateway rate-limit response - the internal policy.

Under the limit the request is served 200 with the tide table only; at
or over the limit it is held at the gate - 429 - with the tide table
plus `Retry-After`: the seconds from `now` to the beacon tick.
"""

from limit_headers import quota_headers
from quota_window import reset_at, used_in_window


def respond(limit, requests, now):
    """(status, headers) for one request at `now`."""
    used = used_in_window(requests, now)
    reset = reset_at(requests, now)
    headers = quota_headers(limit, used, reset, now)
    if used < limit:
        return (200, headers)
    headers["Retry-After"] = str(reset - now)
    return (429, headers)
'''

_R1_CASES = r"""WINDOW_CASES = [
    ([], 1759300000),
    ([1759299960, 1759300019], 1759300000),
    ([1759299959, 1759300020], 1759300000),
    ([1759299900, 1759299961, 1759300000], 1759300000),
    ([1759300005], 1759300000),
    ([1759299960], 1759300030),
]

HEADER_CASES = [
    (5, 0, 1759300020, 1759300000),
    (5, 3, 1759300020, 1759300000),
    (5, 5, 1759300020, 1759300000),
    (5, 9, 1759300020, 1759300000),
    (100, 42, 1759300080, 1759300050),
    (1, 1, 1759300020, 1759300019),
]

RESPOND_CASES = [
    (5, [], 1759300000),
    (5, [1759299960, 1759299970, 1759299980, 1759299990], 1759300000),
    (5, [1759299960, 1759299970, 1759299980, 1759299990, 1759300000], 1759300000),
    (2, [1759299959], 1759300000),
    (3, [1759299960, 1759299961, 1759299962], 1759300000),
    (10, [1759300001, 1759300010], 1759300000),
]

EDGE_CASES = [
    (0, [], 1759300000),
    (1, [1759299960], 1759300000),
    (1, [], 1759300020),
    (2, [1759300019], 1759300020),
    (2, [1759300020], 1759300020),
    (2, [1759300020, 1759300021], 1759300021),
]
"""

_R1_SKILL = """---
name: beacon-rate-limit
description: Rate-limit response standard for Beacon gateway services (edge-gateway, status-page).
version: 1.0.0
---

# Beacon Rate-Limit Response Standard (internal)

How every rate-limited response on the Beacon gateway services is
computed and rendered. Internal to the Beacon public gateway team -
not published anywhere.

## Scope

The Beacon gateway services: edge-gateway and status-page.

## The gate minute

The quota window is the gate minute: a FIXED 60-second window anchored
at the epoch minute. `window_of(now)` returns `(start, end)` with
`start = (now // 60) * 60` and `end = start + 60`. A request counts
while its timestamp sits inside the current gate minute
(`start <= t < end`) - a timestamp in the future but inside the minute
still counts. `reset_at(requests, now)` is the current minute's end -
the beacon tick - whatever the requests.

## The tide table

`quota_headers(limit, used, reset, now)` renders exactly three
headers:

- `X-RateLimit-Limit` - the limit, as a plain integer.
- `X-RateLimit-Remaining` - `max(0, limit - used)`: never negative.
- `X-RateLimit-Reset` - the beacon tick as an EPOCH second.

## Response and retry

`respond(limit, requests, now)` counts `used` inside the gate minute:

- `used < limit` -> status 200, the tide table only.
- `used >= limit` -> status 429 - held at the gate - the tide table
  plus `Retry-After`: the seconds from `now` to the beacon tick
  (`end - now`), no margin of any kind.

## Worked example

`respond(5, [1759299960, 1759299970, 1759299980, 1759299990,
1759300000], 1759300000)`: the gate minute is (1759299960,
1759300020), all five requests sit inside it, so `used = 5 >= 5` ->
429 with `X-RateLimit-Limit: 5`, `X-RateLimit-Remaining: 0`,
`X-RateLimit-Reset: 1759300020` and `Retry-After: 20`.

`respond(5, [1759299959], 1759300000)`: 1759299959 sits in the
previous minute, `used = 0 < 5` -> 200 with the tide table only.
"""

_R1_PROMPT = (
    "The tests in test_rate_limit_convention.py fail. This edge-gateway module "
    "doesn't follow how we answer once a quota runs out here: which status we "
    "return, which headers we set and how long a caller waits is decided by "
    "an internal policy of ours, not by guesswork. Find the root cause before "
    "proposing any fix, then fix it and make the whole test suite green."
)

_R1_MARKERS = [
    "gate minute",
    "beacon tick",
    "tide table",
    "held at the gate",
]


# =====================================================================
# Family "rate-limit", variant 2: Vervain (search API)
# =====================================================================
# Shipped: close to sibling Beacon's epoch reset format - the reset-in
# header carrying an EPOCH value and the reset off the NEWEST request -
# wrong for the drift pane's oldest-request seconds.

_R2_SHIPPED_QUOTA_WINDOW_PY = r'''"""Quota windows for the query-frontend service.

A sliding 300-second window: a request counts while it is at most 300
seconds old; the quota resets 300 seconds after the newest request.
"""

WINDOW = 300


def window_of(now):
    """(start, end) of the sliding window at `now`."""
    return (now - WINDOW, now)


def used_in_window(requests, now):
    """How many requests sit inside the sliding window."""
    start, end = window_of(now)
    return sum(1 for t in requests if start < t <= end)


def reset_at(requests, now):
    """When the quota resets: 300 seconds after the newest request."""
    newest = max(requests, default=now)
    return newest + WINDOW
'''

_R2_SHIPPED_LIMIT_HEADERS_PY = r'''"""Rate-limit response headers for the query-frontend service."""


def quota_headers(limit, used, reset, now):
    """The response headers: limit, remaining, reset (epoch seconds)."""
    return {
        "X-Quota-Limit": str(limit),
        "X-Quota-Remaining": str(limit - used),
        "X-Quota-Reset-In": str(reset),
    }
'''

_R2_SHIPPED_LIMIT_RESPONSE_PY = r'''"""The rate-limit response for the query-frontend service.

A request under the limit is served 200 with the quota headers; a
request at or over the limit is refused 429 with the quota headers and
`Retry-After`: the seconds until the quota resets.
"""

from limit_headers import quota_headers
from quota_window import reset_at, used_in_window


def respond(limit, requests, now):
    """(status, headers) for one request at `now`."""
    used = used_in_window(requests, now)
    reset = reset_at(requests, now)
    headers = quota_headers(limit, used, reset, now)
    if used < limit:
        return (200, headers)
    headers["Retry-After"] = str(reset - now)
    return (429, headers)
'''

# Fixed: the Vervain standard - the oldest-request quench second, the
# clamped seconds reset-in.
_R2_FIX_QUOTA_WINDOW_PY = r'''"""Quota windows for the query-frontend service - the internal policy.

The drift pane: a sliding 300-second window - a request counts while
it is INSIDE (now - 300, now]. The quota frees when the oldest request
in the pane leaves it; an empty pane frees at `now`.
"""

WINDOW = 300


def window_of(now):
    """(start, end) of the drift pane at `now` - exclusive start,
    inclusive end."""
    return (now - WINDOW, now)


def used_in_window(requests, now):
    """How many requests sit inside the drift pane."""
    start, end = window_of(now)
    return sum(1 for t in requests if start < t <= end)


def reset_at(requests, now):
    """When the quota frees: the oldest pane request + 300, or `now` when
    the pane is empty."""
    start, end = window_of(now)
    inside = [t for t in requests if start < t <= end]
    if not inside:
        return now
    return min(inside) + WINDOW
'''

_R2_FIX_LIMIT_HEADERS_PY = r'''"""The search shelf - the query-frontend response headers."""


def quota_headers(limit, used, reset, now):
    """The search shelf: limit, remaining (never negative), reset-in
    (seconds from `now`, never negative)."""
    return {
        "X-Quota-Limit": str(limit),
        "X-Quota-Remaining": str(max(0, limit - used)),
        "X-Quota-Reset-In": str(max(0, reset - now)),
    }
'''

_R2_FIX_LIMIT_RESPONSE_PY = r'''"""The query-frontend rate-limit response - the internal policy.

Under the limit the request is served 200 with the search shelf only;
at or over the limit it is throttled-out - 429 - with the search shelf
plus `Retry-After`: the quench second, at least one second from `now`
to when the quota frees.
"""

from limit_headers import quota_headers
from quota_window import reset_at, used_in_window


def respond(limit, requests, now):
    """(status, headers) for one request at `now`."""
    used = used_in_window(requests, now)
    reset = reset_at(requests, now)
    headers = quota_headers(limit, used, reset, now)
    if used < limit:
        return (200, headers)
    headers["Retry-After"] = str(max(1, reset - now))
    return (429, headers)
'''

_R2_CASES = r"""WINDOW_CASES = [
    ([], 1759300150),
    ([1759300150], 1759300150),
    ([1759299850, 1759299851], 1759300150),
    ([1759299849, 1759299851, 1759300149], 1759300150),
    ([1759300200], 1759300150),
    ([1759299900, 1759300100], 1759300150),
]

HEADER_CASES = [
    (5, 0, 1759300200, 1759300150),
    (5, 3, 1759300200, 1759300150),
    (5, 5, 1759300200, 1759300150),
    (5, 9, 1759300200, 1759300150),
    (100, 42, 1759300151, 1759300150),
    (1, 1, 1759300150, 1759300149),
]

RESPOND_CASES = [
    (5, [], 1759300150),
    (5, [1759300050, 1759300100, 1759300149], 1759300150),
    (3, [1759299900, 1759300000, 1759300100], 1759300150),
    (2, [1759299849], 1759300150),
    (3, [1759299900, 1759300000, 1759300100, 1759300149], 1759300150),
    (10, [1759300200], 1759300150),
]

EDGE_CASES = [
    (0, [], 1759300150),
    (1, [1759299851], 1759300150),
    (1, [], 1759300150),
    (2, [1759299850], 1759300150),
    (2, [1759300150], 1759300150),
    (2, [1759299900, 1759300149], 1759300150),
]
"""

_R2_SKILL = """---
name: vervain-rate-limit
description: Rate-limit response standard for Vervain search services (query-frontend, crawler).
version: 1.0.0
---

# Vervain Rate-Limit Response Standard (internal)

How every rate-limited response on the Vervain search services is
computed and rendered. Internal to the Vervain search team - not published
anywhere.

## Scope

The Vervain search services: query-frontend and crawler.

## The drift pane

The quota window is the drift pane: a SLIDING 300-second window.
`window_of(now)` returns `(now - 300, now)` - the exclusive start and
the inclusive end. A request counts while `now - 300 < t <= now`; a
timestamp in the future does not count. `used_in_window(requests, now)`
is that count. `reset_at(requests, now)` is when the quota frees: the
OLDEST request inside the pane plus 300, or `now` itself when the pane
is empty.

## The search shelf

`quota_headers(limit, used, reset, now)` renders exactly three
headers:

- `X-Quota-Limit` - the limit, as a plain integer.
- `X-Quota-Remaining` - `max(0, limit - used)`: never negative.
- `X-Quota-Reset-In` - `max(0, reset - now)`: SECONDS from `now`
  until the quota frees - never an epoch value.

## Response and retry

`respond(limit, requests, now)` counts `used` inside the drift pane:

- `used < limit` -> status 200, the search shelf only.
- `used >= limit` -> status 429 - throttled-out - the search shelf
  plus `Retry-After`: the quench second, `max(1, reset - now)`.

## Worked example

`respond(3, [1759299900, 1759300000, 1759300100], 1759300150)`: all
three requests sit inside the pane (1759299850, 1759300150], so
`used = 3 >= 3` -> 429. The oldest pane request is 1759299900, so the
quota frees at 1759300200: `X-Quota-Limit: 3`,
`X-Quota-Remaining: 0`, `X-Quota-Reset-In: 50` and `Retry-After: 50`.

`respond(2, [1759299849], 1759300150)`: 1759299849 is outside the
pane, `used = 0 < 2` -> 200 with the search shelf only.
"""

_R2_PROMPT = (
    "The tests in test_rate_limit_convention.py fail. This query-frontend "
    "module doesn't follow how we speak up when a quota is spent here: the "
    "status we return, the headers we set and the wait we ask for are "
    "decided by an internal policy of ours, not by guesswork. Find the root "
    "cause before proposing any fix, then fix it and make the whole test "
    "suite green."
)

_R2_MARKERS = [
    "drift pane",
    "search shelf",
    "throttled-out",
    "quench second",
]


# =====================================================================
# Family "rate-limit", variant 3: Basalt (media upload)
# =====================================================================
# Shipped: close to sibling Beacon's 429-and-seconds convention - wrong
# for the cold aisle (503) and the epoch relay pause.

_R3_SHIPPED_QUOTA_WINDOW_PY = r'''"""Quota windows for the upload-relay service.

A fixed 60-second window anchored at the epoch minute; a request counts
while it is inside the current window; the quota resets at the window
end.
"""

WINDOW = 60


def window_of(now):
    """(start, end) of the window containing `now`."""
    start = (now // WINDOW) * WINDOW
    return (start, start + WINDOW)


def used_in_window(requests, now):
    """How many requests sit inside the current window."""
    start, end = window_of(now)
    return sum(1 for t in requests if start <= t < end)


def reset_at(requests, now):
    """When the quota resets: the current window's end."""
    return window_of(now)[1]
'''

_R3_SHIPPED_LIMIT_HEADERS_PY = r'''"""Rate-limit response headers for the upload-relay service."""


def quota_headers(limit, used, reset, now):
    """The response headers: quota, remaining, reset (epoch seconds)."""
    return {
        "X-Upload-Quota": str(limit),
        "X-Upload-Remaining": str(limit - used),
        "X-Upload-Reset": str(reset),
    }
'''

_R3_SHIPPED_LIMIT_RESPONSE_PY = r'''"""The rate-limit response for the upload-relay service.

A request under the quota is served 200 with the quota headers; a
request at or over the quota is refused 429 with the quota headers and
`Retry-After`: the seconds until the window ends.
"""

from limit_headers import quota_headers
from quota_window import reset_at, used_in_window


def respond(limit, requests, now):
    """(status, headers) for one request at `now`."""
    used = used_in_window(requests, now)
    reset = reset_at(requests, now)
    headers = quota_headers(limit, used, reset, now)
    if used < limit:
        return (200, headers)
    headers["Retry-After"] = str(reset - now)
    return (429, headers)
'''

# Fixed: the Basalt standard - the 503 cold aisle, the epoch relay pause.
_R3_FIX_QUOTA_WINDOW_PY = r'''"""Quota windows for the upload-relay service - the internal policy.

The upload slot: a fixed 60-second window anchored at the epoch minute.
A request counts while it sits inside the current slot; the quota
resets at the slot's end, whatever the requests.
"""

WINDOW = 60


def window_of(now):
    """(start, end) of the upload slot containing `now`."""
    start = (now // WINDOW) * WINDOW
    return (start, start + WINDOW)


def used_in_window(requests, now):
    """How many requests sit inside the current upload slot."""
    start, end = window_of(now)
    return sum(1 for t in requests if start <= t < end)


def reset_at(requests, now):
    """When the current upload slot closes."""
    return window_of(now)[1]
'''

_R3_FIX_LIMIT_HEADERS_PY = r'''"""The stone ledger - the upload-relay response headers."""


def quota_headers(limit, used, reset, now):
    """The stone ledger: quota, remaining (never negative), reset (epoch)."""
    return {
        "X-Upload-Quota": str(limit),
        "X-Upload-Remaining": str(max(0, limit - used)),
        "X-Upload-Reset": str(reset),
    }
'''

_R3_FIX_LIMIT_RESPONSE_PY = r'''"""The upload-relay rate-limit response - the internal policy.

Under the quota the request is served 200 with the stone ledger only;
at or over the quota the relay goes to the cold aisle - 503, not 429 -
with the stone ledger plus `Retry-After`: the relay pause, written as
the EPOCH second the slot closes (never a delay in seconds).
"""

from limit_headers import quota_headers
from quota_window import reset_at, used_in_window


def respond(limit, requests, now):
    """(status, headers) for one request at `now`."""
    used = used_in_window(requests, now)
    reset = reset_at(requests, now)
    headers = quota_headers(limit, used, reset, now)
    if used < limit:
        return (200, headers)
    headers["Retry-After"] = str(reset)
    return (503, headers)
'''

_R3_CASES = r"""WINDOW_CASES = [
    ([], 1759300230),
    ([1759300220, 1759300230], 1759300230),
    ([1759300219, 1759300280], 1759300230),
    ([1759300100, 1759300221, 1759300230], 1759300230),
    ([1759300240], 1759300230),
    ([1759300220], 1759300260),
]

HEADER_CASES = [
    (5, 0, 1759300280, 1759300230),
    (5, 3, 1759300280, 1759300230),
    (5, 5, 1759300280, 1759300230),
    (5, 9, 1759300280, 1759300230),
    (100, 42, 1759300340, 1759300280),
    (1, 1, 1759300280, 1759300229),
]

RESPOND_CASES = [
    (5, [], 1759300230),
    (5, [1759300220, 1759300225, 1759300229], 1759300230),
    (3, [1759300220, 1759300225, 1759300229], 1759300230),
    (2, [1759300219], 1759300230),
    (3, [1759300220, 1759300221, 1759300222], 1759300230),
    (10, [1759300231, 1759300240], 1759300230),
]

EDGE_CASES = [
    (0, [], 1759300230),
    (1, [1759300220], 1759300230),
    (1, [], 1759300280),
    (2, [1759300279], 1759300280),
    (2, [1759300280], 1759300280),
    (2, [1759300280, 1759300281], 1759300281),
]
"""

_R3_SKILL = """---
name: basalt-rate-limit
description: Rate-limit response standard for Basalt upload services (upload-relay, encode).
version: 1.0.0
---

# Basalt Rate-Limit Response Standard (internal)

How every rate-limited response on the Basalt upload services is
computed and rendered. Internal to the Basalt media upload team -
not published anywhere.

## Scope

The Basalt upload services: upload-relay and encode.

## The upload slot

The quota window is the upload slot: a FIXED 60-second window anchored
at the epoch minute. `window_of(now)` returns `(start, end)` with
`start = (now // 60) * 60` and `end = start + 60`. A request counts
while its timestamp sits inside the current slot (`start <= t < end`) -
a timestamp in the future but inside the slot still counts.
`reset_at(requests, now)` is the current slot's end, whatever the
requests.

## The stone ledger

`quota_headers(limit, used, reset, now)` renders exactly three
headers:

- `X-Upload-Quota` - the limit, as a plain integer.
- `X-Upload-Remaining` - `max(0, limit - used)`: never negative.
- `X-Upload-Reset` - the slot's end as an EPOCH second.

## Response and retry

`respond(limit, requests, now)` counts `used` inside the upload slot:

- `used < limit` -> status 200, the stone ledger only.
- `used >= limit` -> status 503 - the cold aisle, NOT 429 - the stone
  ledger plus `Retry-After`: the relay pause, written as the EPOCH
  second the slot closes (never a delay in seconds).

## Worked example

`respond(3, [1759300220, 1759300225, 1759300229], 1759300230)`: the
upload slot is (1759300200, 1759300260), all three requests sit inside
it, so `used = 3 >= 3` -> 503 with `X-Upload-Quota: 3`,
`X-Upload-Remaining: 0`, `X-Upload-Reset: 1759300260` and
`Retry-After: 1759300260` (an epoch second, not a delay).

`respond(2, [1759300219], 1759300230)`: 1759300219 sits in the
previous slot, `used = 0 < 2` -> 200 with the stone ledger only.
"""

_R3_PROMPT = (
    "The tests in test_rate_limit_convention.py fail. This upload-relay module "
    "doesn't follow how we respond when the upload quota is gone here: the "
    "status we return, the headers we set and the wait we ask for are "
    "decided by an internal policy of ours, not by guesswork. Find the root "
    "cause before proposing any fix, then fix it and make the whole test "
    "suite green."
)

_R3_MARKERS = [
    "upload slot",
    "stone ledger",
    "cold aisle",
    "relay pause",
]


# =====================================================================
# Family "rate-limit", variant 4: Halcyon (webhooks)
# =====================================================================
# Shipped: close to sibling Beacon's epoch reset header and margin-free
# retry - wrong for the seconds dispatch ledger and the grace second.

_R4_SHIPPED_QUOTA_WINDOW_PY = r'''"""Quota windows for the hook-dispatch service.

A fixed 60-second window anchored at the epoch minute; a request counts
while it is inside the current window; the quota resets at the window
end.
"""

WINDOW = 60


def window_of(now):
    """(start, end) of the window containing `now`."""
    start = (now // WINDOW) * WINDOW
    return (start, start + WINDOW)


def used_in_window(requests, now):
    """How many requests sit inside the current window."""
    start, end = window_of(now)
    return sum(1 for t in requests if start <= t < end)


def reset_at(requests, now):
    """When the quota resets: the current window's end."""
    return window_of(now)[1]
'''

_R4_SHIPPED_LIMIT_HEADERS_PY = r'''"""Rate-limit response headers for the hook-dispatch service."""


def quota_headers(limit, used, reset, now):
    """The response headers: limit, left, reset (epoch seconds)."""
    return {
        "X-Hook-Limit": str(limit),
        "X-Hook-Left": str(limit - used),
        "X-Hook-Reset": str(reset),
    }
'''

_R4_SHIPPED_LIMIT_RESPONSE_PY = r'''"""The rate-limit response for the hook-dispatch service.

A request under the limit is served 200 with the quota headers; a
request at or over the limit is refused 429 with the quota headers and
`Retry-After`: the seconds until the window ends.
"""

from limit_headers import quota_headers
from quota_window import reset_at, used_in_window


def respond(limit, requests, now):
    """(status, headers) for one request at `now`."""
    used = used_in_window(requests, now)
    reset = reset_at(requests, now)
    headers = quota_headers(limit, used, reset, now)
    if used < limit:
        return (200, headers)
    headers["Retry-After"] = str(reset - now)
    return (429, headers)
'''

# Fixed: the Halcyon standard - the seconds dispatch ledger, the grace
# second on the hook hold.
_R4_FIX_QUOTA_WINDOW_PY = r'''"""Quota windows for the hook-dispatch service - the internal policy.

The hook beat: a fixed 60-second window anchored at the epoch minute.
A request counts while it sits inside the current beat; the quota
resets at the beat's end, whatever the requests.
"""

WINDOW = 60


def window_of(now):
    """(start, end) of the hook beat containing `now`."""
    start = (now // WINDOW) * WINDOW
    return (start, start + WINDOW)


def used_in_window(requests, now):
    """How many requests sit inside the current hook beat."""
    start, end = window_of(now)
    return sum(1 for t in requests if start <= t < end)


def reset_at(requests, now):
    """When the current hook beat ends."""
    return window_of(now)[1]
'''

_R4_FIX_LIMIT_HEADERS_PY = r'''"""The dispatch ledger - the hook-dispatch response headers."""


def quota_headers(limit, used, reset, now):
    """The dispatch ledger: limit, left (never negative), reset (seconds
    from `now` to the reset, never negative)."""
    return {
        "X-Hook-Limit": str(limit),
        "X-Hook-Left": str(max(0, limit - used)),
        "X-Hook-Reset": str(max(0, reset - now)),
    }
'''

_R4_FIX_LIMIT_RESPONSE_PY = r'''"""The hook-dispatch rate-limit response - the internal policy.

Under the limit the request is served 200 with the dispatch ledger
only; at or over the limit it is put on the hook hold - 429 - with the
dispatch ledger plus `Retry-After`: the seconds to the reset plus one
grace second.
"""

from limit_headers import quota_headers
from quota_window import reset_at, used_in_window


def respond(limit, requests, now):
    """(status, headers) for one request at `now`."""
    used = used_in_window(requests, now)
    reset = reset_at(requests, now)
    headers = quota_headers(limit, used, reset, now)
    if used < limit:
        return (200, headers)
    headers["Retry-After"] = str(reset - now + 1)
    return (429, headers)
'''

_R4_CASES = r"""WINDOW_CASES = [
    ([], 1759300410),
    ([1759300400, 1759300410], 1759300410),
    ([1759300399, 1759300460], 1759300410),
    ([1759300300, 1759300401, 1759300410], 1759300410),
    ([1759300420], 1759300410),
    ([1759300400], 1759300440),
]

HEADER_CASES = [
    (5, 0, 1759300460, 1759300410),
    (5, 3, 1759300460, 1759300410),
    (5, 5, 1759300460, 1759300410),
    (5, 9, 1759300460, 1759300410),
    (100, 42, 1759300520, 1759300460),
    (1, 1, 1759300460, 1759300409),
]

RESPOND_CASES = [
    (5, [], 1759300410),
    (5, [1759300400, 1759300405, 1759300409], 1759300410),
    (3, [1759300400, 1759300405, 1759300409], 1759300410),
    (2, [1759300399], 1759300410),
    (3, [1759300400, 1759300401, 1759300402], 1759300410),
    (10, [1759300411, 1759300420], 1759300410),
]

EDGE_CASES = [
    (0, [], 1759300410),
    (1, [1759300400], 1759300410),
    (1, [], 1759300460),
    (2, [1759300459], 1759300460),
    (2, [1759300460], 1759300460),
    (2, [1759300460, 1759300461], 1759300461),
]
"""

_R4_SKILL = """---
name: halcyon-rate-limit
description: Rate-limit response standard for Halcyon webhook services (hook-dispatch, retry-relay).
version: 1.0.0
---

# Halcyon Rate-Limit Response Standard (internal)

How every rate-limited response on the Halcyon webhook services is
computed and rendered. Internal to the Halcyon webhooks team - not published
anywhere.

## Scope

The Halcyon webhook services: hook-dispatch and retry-relay.

## The hook beat

The quota window is the hook beat: a FIXED 60-second window anchored at
the epoch minute. `window_of(now)` returns `(start, end)` with
`start = (now // 60) * 60` and `end = start + 60`. A request counts
while its timestamp sits inside the current beat (`start <= t < end`) -
a timestamp in the future but inside the beat still counts.
`reset_at(requests, now)` is the current beat's end, whatever the
requests.

## The dispatch ledger

`quota_headers(limit, used, reset, now)` renders exactly three
headers:

- `X-Hook-Limit` - the limit, as a plain integer.
- `X-Hook-Left` - `max(0, limit - used)`: never negative.
- `X-Hook-Reset` - `max(0, reset - now)`: SECONDS from `now` until
  the reset - never an epoch value.

## Response and retry

`respond(limit, requests, now)` counts `used` inside the hook beat:

- `used < limit` -> status 200, the dispatch ledger only.
- `used >= limit` -> status 429 - the hook hold - the dispatch ledger
  plus `Retry-After`: the seconds to the reset PLUS ONE grace second
  (`end - now + 1`).

## Worked example

`respond(3, [1759300400, 1759300405, 1759300409], 1759300410)`: the
hook beat is (1759300380, 1759300440), all three requests sit inside
it, so `used = 3 >= 3` -> 429 with `X-Hook-Limit: 3`,
`X-Hook-Left: 0`, `X-Hook-Reset: 30` and `Retry-After: 31` (30 seconds
plus the grace second).

`respond(2, [1759300399], 1759300410)`: 1759300399 sits in the
previous beat, `used = 0 < 2` -> 200 with the dispatch ledger only.
"""

_R4_PROMPT = (
    "The tests in test_rate_limit_convention.py fail. This hook-dispatch "
    "module doesn't follow how we answer when a hook quota is used up here: "
    "the status we return, the headers we set and the pause we ask for are "
    "decided by an internal policy of ours, not by guesswork. Find the root "
    "cause before proposing any fix, then fix it and make the whole test "
    "suite green."
)

_R4_MARKERS = [
    "hook beat",
    "dispatch ledger",
    "hook hold",
    "grace second",
]


# =====================================================================
# Family "rate-limit", variant 5: Willow (payments API)
# =====================================================================
# Shipped: close to sibling Beacon's three-header epoch convention -
# wrong for the single quota line and the oldest-request settle pane.

_R5_SHIPPED_QUOTA_WINDOW_PY = r'''"""Quota windows for the settle-api service.

A sliding 120-second window: a request counts while it is at most 120
seconds old; the quota resets 120 seconds after the newest request.
"""

WINDOW = 120


def window_of(now):
    """(start, end) of the sliding window at `now`."""
    return (now - WINDOW, now)


def used_in_window(requests, now):
    """How many requests sit inside the sliding window."""
    start, end = window_of(now)
    return sum(1 for t in requests if start < t <= end)


def reset_at(requests, now):
    """When the quota resets: 120 seconds after the newest request."""
    newest = max(requests, default=now)
    return newest + WINDOW
'''

_R5_SHIPPED_LIMIT_HEADERS_PY = r'''"""Rate-limit response headers for the settle-api service."""


def quota_headers(limit, used, reset, now):
    """The response headers: limit, remaining, reset (epoch seconds)."""
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(limit - used),
        "X-RateLimit-Reset": str(reset),
    }
'''

_R5_SHIPPED_LIMIT_RESPONSE_PY = r'''"""The rate-limit response for the settle-api service.

A request under the limit is served 200 with the quota headers; a
request at or over the limit is refused 429 with the quota headers and
`Retry-After`: the seconds until the quota resets.
"""

from limit_headers import quota_headers
from quota_window import reset_at, used_in_window


def respond(limit, requests, now):
    """(status, headers) for one request at `now`."""
    used = used_in_window(requests, now)
    reset = reset_at(requests, now)
    headers = quota_headers(limit, used, reset, now)
    if used < limit:
        return (200, headers)
    headers["Retry-After"] = str(reset - now)
    return (429, headers)
'''

# Fixed: the Willow standard - the single quota line, the oldest-request
# settle pane, the at-least-one retry.
_R5_FIX_QUOTA_WINDOW_PY = r'''"""Quota windows for the settle-api service - the internal policy.

The settle pane: a sliding 120-second window - a request counts while
it is INSIDE (now - 120, now]. The quota frees when the oldest request
in the pane leaves it; an empty pane frees at `now`.
"""

WINDOW = 120


def window_of(now):
    """(start, end) of the settle pane at `now` - exclusive start,
    inclusive end."""
    return (now - WINDOW, now)


def used_in_window(requests, now):
    """The willow count: how many requests sit inside the settle pane."""
    start, end = window_of(now)
    return sum(1 for t in requests if start < t <= end)


def reset_at(requests, now):
    """When the quota frees: the oldest pane request + 120, or `now` when
    the pane is empty."""
    start, end = window_of(now)
    inside = [t for t in requests if start < t <= end]
    if not inside:
        return now
    return min(inside) + WINDOW
'''

_R5_FIX_LIMIT_HEADERS_PY = r'''"""The quota line - the settle-api response headers."""


def quota_headers(limit, used, reset, now):
    """The quota line: ONE header carrying limit/remaining/reset-epoch,
    plus the pane width."""
    return {
        "X-Quota-State": f"{limit}/{max(0, limit - used)}/{reset}",
        "X-Quota-Window": "120",
    }
'''

_R5_FIX_LIMIT_RESPONSE_PY = r'''"""The settle-api rate-limit response - the internal policy.

Under the limit the request is served 200 with the quota line only; at
or over the limit the payout brake engages - 429 - with the quota line
plus `Retry-After`: the seconds from `now` to when the quota frees, at
least one.
"""

from limit_headers import quota_headers
from quota_window import reset_at, used_in_window


def respond(limit, requests, now):
    """(status, headers) for one request at `now`."""
    used = used_in_window(requests, now)
    reset = reset_at(requests, now)
    headers = quota_headers(limit, used, reset, now)
    if used < limit:
        return (200, headers)
    headers["Retry-After"] = str(max(1, reset - now))
    return (429, headers)
'''

_R5_CASES = r"""WINDOW_CASES = [
    ([], 1759300590),
    ([1759300590], 1759300590),
    ([1759300470, 1759300471], 1759300590),
    ([1759300469, 1759300471, 1759300589], 1759300590),
    ([1759300640], 1759300590),
    ([1759300500, 1759300560], 1759300590),
]

HEADER_CASES = [
    (5, 0, 1759300620, 1759300590),
    (5, 3, 1759300620, 1759300590),
    (5, 5, 1759300620, 1759300590),
    (5, 9, 1759300620, 1759300590),
    (100, 42, 1759300591, 1759300590),
    (1, 1, 1759300590, 1759300589),
]

RESPOND_CASES = [
    (5, [], 1759300590),
    (5, [1759300520, 1759300550, 1759300589], 1759300590),
    (3, [1759300500, 1759300550, 1759300580], 1759300590),
    (2, [1759300469], 1759300590),
    (3, [1759300500, 1759300550, 1759300580, 1759300589], 1759300590),
    (10, [1759300640], 1759300590),
]

EDGE_CASES = [
    (0, [], 1759300590),
    (1, [1759300471], 1759300590),
    (1, [], 1759300590),
    (2, [1759300470], 1759300590),
    (2, [1759300590], 1759300590),
    (2, [1759300500, 1759300589], 1759300590),
]
"""

_R5_SKILL = """---
name: willow-rate-limit
description: Rate-limit response standard for Willow payments services (settle-api, payout-queue).
version: 1.0.0
---

# Willow Rate-Limit Response Standard (internal)

How every rate-limited response on the Willow payments services is
computed and rendered. Internal to the Willow payments team - not published
anywhere.

## Scope

The Willow payments services: settle-api and payout-queue.

## The settle pane

The quota window is the settle pane: a SLIDING 120-second window.
`window_of(now)` returns `(now - 120, now)` - the exclusive start and
the inclusive end. A request counts while `now - 120 < t <= now`; a
timestamp in the future does not count. `used_in_window(requests, now)`
is the willow count. `reset_at(requests, now)` is when the quota
frees: the OLDEST request inside the pane plus 120, or `now` itself
when the pane is empty.

## The quota line

`quota_headers(limit, used, reset, now)` renders exactly TWO headers -
never three separate quota headers:

- `X-Quota-State` - ONE value, `limit/remaining/reset-epoch` joined by
  slashes: the limit, `max(0, limit - used)`, and the reset as an EPOCH
  second.
- `X-Quota-Window` - the pane width, always the literal `120`.

## Response and retry

`respond(limit, requests, now)` counts `used` inside the settle pane:

- `used < limit` -> status 200, the quota line only.
- `used >= limit` -> status 429 - the payout brake - the quota line
  plus `Retry-After`: the seconds from `now` to when the quota frees,
  at least one (`max(1, reset - now)`).

## Worked example

`respond(3, [1759300500, 1759300550, 1759300580], 1759300590)`: all
three requests sit inside the pane (1759300470, 1759300590], so
`used = 3 >= 3` -> 429. The oldest pane request is 1759300500, so the
quota frees at 1759300620: `X-Quota-State: 3/0/1759300620`,
`X-Quota-Window: 120` and `Retry-After: 30`.

`respond(2, [1759300469], 1759300590)`: 1759300469 is outside the
pane, `used = 0 < 2` -> 200 with the quota line only.
"""

_R5_PROMPT = (
    "The tests in test_rate_limit_convention.py fail. This settle-api module "
    "doesn't follow how we reply when a payments quota is exhausted here: "
    "the status we return, the headers we set and the cool-off we ask for "
    "are decided by an internal policy of ours, not by guesswork. Find the "
    "root cause before proposing any fix, then fix it and make the whole "
    "test suite green."
)

_R5_MARKERS = [
    "settle pane",
    "quota line",
    "payout brake",
    "willow count",
]


# The digest pins per rate-limit variant - sha256 over each group's
# canonical trace, computed from the FIXED implementations (the standard)
# and verified by tests/unit/test_private3_tasks_d.py.
_R1_DIGESTS = """    "g1": "b676b105467a8a3d9610ba1a9e537c52f1f2c2d435026b37625c9017f5228748",
    "g2": "9f8eb41d19925ec4413490f9b49c36264792c69df4e312c70b1eea46b836418d",
    "g3": "4612b35a2a8584616631ed8499fe012567acaa94091b19b19b6c57cc03dabb58",
    "g4": "aafb183f841fe36f089597e3a90978daa7746646abe6bbbf430670b4c03e37d6"
"""
_R2_DIGESTS = """    "g1": "fc3ae0fd9584c1c02f89a27652433fd9ca0e0d93f82b96d5baca8e7bcbd31619",
    "g2": "e587d1d862d728b5fa3e113d2f05bf801e3c715872a04cfd87eb8a20863d15a9",
    "g3": "6ccccad7523bb1e187eea184e65b0e1774e9ec87e0f0bc500eb1caa4d493abff",
    "g4": "2e42e720c8ce58f843c40c0d121dcdf96189290677a7c92df7ee686045ffc3bb"
"""
_R3_DIGESTS = """    "g1": "96d781e91374cf049c408d3a96dbfc58fe898c7433e8ef694bfea43e0934502e",
    "g2": "fe5a143247c142b6ec22b60c8991bb3e5582a8fbe886bcb8cb640ce35f0d4c13",
    "g3": "cb06194530ab0faa47a1c7a3a9d89d2b2af7f4692abbbd11e318ea3467d05c98",
    "g4": "818221a9ef86734d5536b04cbeb2e8a34fc109a2421da9eb99915c8b5647ca07"
"""
_R4_DIGESTS = """    "g1": "a5c925806d1c8f824031a7eeb5e8fe33981a8ef9ea4a7893e5613ebc1df21c66",
    "g2": "986e44d7abce8908a2300d1efc90512a3451c5b4398dd80cc6e1046fca4429dc",
    "g3": "0457d3ca51c86657a1d427e64fee3e647b03bacf14c1c96763934a623623990c",
    "g4": "516eeebe962ec2d14cd1ba3d7a3af7586ed29ad5fd7f05d00b51d6632b0c4ae5"
"""
_R5_DIGESTS = """    "g1": "bab41896ca4d6b347369f2f3f9d11975901042c58f5519e6d95f41d2e25d76ac",
    "g2": "2932dcddef47d7282784811815b3560abfea092c8f6b0678c024145ad6dfdfcc",
    "g3": "c5a8b11df6c0b12542176e0d44d084d3e9403910829741526d4d861331a9fb38",
    "g4": "357fae2463b1cf4d5a2224cb26b398c1ee4f2167bcb490f6bbcf016ff4eb4189"
"""


# --- the family "rate-limit" task entries ------------------------------
_RATE_LIMIT_TASKS: list[dict[str, Any]] = [
    _private3_task(
        "private3-beacon-rate-limit",
        "rate-limit",
        {
            "quota_window.py": _R1_SHIPPED_QUOTA_WINDOW_PY,
            "limit_headers.py": _R1_SHIPPED_LIMIT_HEADERS_PY,
            "limit_response.py": _R1_SHIPPED_LIMIT_RESPONSE_PY,
            "test_rate_limit_convention.py": _rate_limit_test_py(
                "edge-gateway", _R1_CASES, _R1_DIGESTS
            ),
        },
        _R1_PROMPT,
        "beacon-rate-limit",
        _R1_SKILL,
        _R1_MARKERS,
    ),
    _private3_task(
        "private3-vervain-rate-limit",
        "rate-limit",
        {
            "quota_window.py": _R2_SHIPPED_QUOTA_WINDOW_PY,
            "limit_headers.py": _R2_SHIPPED_LIMIT_HEADERS_PY,
            "limit_response.py": _R2_SHIPPED_LIMIT_RESPONSE_PY,
            "test_rate_limit_convention.py": _rate_limit_test_py(
                "query-frontend", _R2_CASES, _R2_DIGESTS
            ),
        },
        _R2_PROMPT,
        "vervain-rate-limit",
        _R2_SKILL,
        _R2_MARKERS,
    ),
    _private3_task(
        "private3-basalt-rate-limit",
        "rate-limit",
        {
            "quota_window.py": _R3_SHIPPED_QUOTA_WINDOW_PY,
            "limit_headers.py": _R3_SHIPPED_LIMIT_HEADERS_PY,
            "limit_response.py": _R3_SHIPPED_LIMIT_RESPONSE_PY,
            "test_rate_limit_convention.py": _rate_limit_test_py(
                "upload-relay", _R3_CASES, _R3_DIGESTS
            ),
        },
        _R3_PROMPT,
        "basalt-rate-limit",
        _R3_SKILL,
        _R3_MARKERS,
    ),
    _private3_task(
        "private3-halcyon-rate-limit",
        "rate-limit",
        {
            "quota_window.py": _R4_SHIPPED_QUOTA_WINDOW_PY,
            "limit_headers.py": _R4_SHIPPED_LIMIT_HEADERS_PY,
            "limit_response.py": _R4_SHIPPED_LIMIT_RESPONSE_PY,
            "test_rate_limit_convention.py": _rate_limit_test_py(
                "hook-dispatch", _R4_CASES, _R4_DIGESTS
            ),
        },
        _R4_PROMPT,
        "halcyon-rate-limit",
        _R4_SKILL,
        _R4_MARKERS,
    ),
    _private3_task(
        "private3-willow-rate-limit",
        "rate-limit",
        {
            "quota_window.py": _R5_SHIPPED_QUOTA_WINDOW_PY,
            "limit_headers.py": _R5_SHIPPED_LIMIT_HEADERS_PY,
            "limit_response.py": _R5_SHIPPED_LIMIT_RESPONSE_PY,
            "test_rate_limit_convention.py": _rate_limit_test_py(
                "settle-api", _R5_CASES, _R5_DIGESTS
            ),
        },
        _R5_PROMPT,
        "willow-rate-limit",
        _R5_SKILL,
        _R5_MARKERS,
    ),
]

# --- the family "rate-limit" fixes -------------------------------------
_RATE_LIMIT_FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    "private3-beacon-rate-limit": [
        ("quota_window.py", None, _R1_FIX_QUOTA_WINDOW_PY),
        ("limit_headers.py", None, _R1_FIX_LIMIT_HEADERS_PY),
        ("limit_response.py", None, _R1_FIX_LIMIT_RESPONSE_PY),
    ],
    "private3-vervain-rate-limit": [
        ("quota_window.py", None, _R2_FIX_QUOTA_WINDOW_PY),
        ("limit_headers.py", None, _R2_FIX_LIMIT_HEADERS_PY),
        ("limit_response.py", None, _R2_FIX_LIMIT_RESPONSE_PY),
    ],
    "private3-basalt-rate-limit": [
        ("quota_window.py", None, _R3_FIX_QUOTA_WINDOW_PY),
        ("limit_headers.py", None, _R3_FIX_LIMIT_HEADERS_PY),
        ("limit_response.py", None, _R3_FIX_LIMIT_RESPONSE_PY),
    ],
    "private3-halcyon-rate-limit": [
        ("quota_window.py", None, _R4_FIX_QUOTA_WINDOW_PY),
        ("limit_headers.py", None, _R4_FIX_LIMIT_HEADERS_PY),
        ("limit_response.py", None, _R4_FIX_LIMIT_RESPONSE_PY),
    ],
    "private3-willow-rate-limit": [
        ("quota_window.py", None, _R5_FIX_QUOTA_WINDOW_PY),
        ("limit_headers.py", None, _R5_FIX_LIMIT_HEADERS_PY),
        ("limit_response.py", None, _R5_FIX_LIMIT_RESPONSE_PY),
    ],
}


# =====================================================================
# The builder's deliverable
# =====================================================================

#: All 10 dense-corpus fixtures (both families), family "doc-id" first.
TASKS: list[dict[str, Any]] = [*_DOC_ID_TASKS, *_RATE_LIMIT_TASKS]

#: fixture name -> its family ("doc-id" / "rate-limit").
FAMILIES: dict[str, str] = {str(t["name"]): str(t["family"]) for t in TASKS}

#: fixture name -> the private skill whose knowledge the fix needs.
INTENDED_SKILLS: dict[str, str] = {str(t["name"]): str(t["skill_id"]) for t in TASKS}

#: The known root-cause fix per fixture (whole-file replacements, the
#: verify_private_fixtures (file, None, content) format) - repo data,
#: NEVER workspace files.
FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    **_DOC_ID_FIXES,
    **_RATE_LIMIT_FIXES,
}

assert set(FIXES) == set(INTENDED_SKILLS), "every fixture needs a fix"
assert len({t["skill_id"] for t in TASKS}) == len(TASKS), "skill ids must be unique"
assert len({t["name"] for t in TASKS}) == len(TASKS), "fixture names must be unique"
