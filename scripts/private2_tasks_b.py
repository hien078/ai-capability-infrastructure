"""Private-knowledge fixtures for the E2C skill axis (2026-10-02) - builder b.

E2B (ADR-014 amendment 24) measured the private-skill axis on 2 fixtures
(atlas + meridian): the same standard as a plain repo file (arm F)
passed 9/20 where the registry + section-14 router preload (arm Bp)
passed 19/20. E2C replicates that measurement on 7 NEW, varied fixtures
(`--set private2`). This module is builder b's share of the set - 2
fixtures; the other builders' modules carry the rest, and a later job
integrates them and runs the measurement (this module ships NO runner
changes and no product code).

Same contract as private_tasks.py: each fixture's required knowledge
did NOT exist before today - a fictional internal standard invented for
this fixture - so no model can carry it. The rules are arbitrary but
precise, documented ONLY in the fixture's private SKILL.md (served
through the capability plane, never a workspace file), and pinned in the
tests as sha256 DIGESTS over the observed behavior - never plaintext.
The E2B section 1.5 lesson is applied: every digest is over a LARGE
output space (free-form strings plus several combined decisions per
line), unlike meridian's small violation-code strings.

Fixture -> private skill (the single source of the rules):

- private2-palisade-redaction -> palisade-log-hygiene (directness: named)
      the Palisade security team's log redaction rules: the field
      classes, the per-class modes (drop / mask / hash / coarsen /
      keep), the exact masked formats and the sorted-key line
      rendering.
- private2-cairn-sunset -> cairn-api-sunset-policy (directness: indirect)
      the Cairn platform API team's version-retirement rules: the
      date-derived states, the per-state response headers, the compact
      date format, the minimum-notice floor and the removed-version
      response replacement.

The prompt directness is ASSIGNED per fixture (E2C spec): the named
prompt names the standard and its owning team; the indirect prompt says
only "our internal policy" - neither the standard's name nor its title
words. Neither prompt reveals any rule.

section 34 caveat applies to any round run on this set: small n,
author-built fixtures, one model - directional only.
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _private_task(
    name: str,
    files: dict[str, str],
    prompt: str,
    skill_id: str,
    skill: str,
    directness: str,
    markers: tuple[str, ...],
) -> dict[str, Any]:
    """A private-knowledge fixture: workspace files + task prompt + the
    private SKILL.md text (NOT a workspace file - the model must never see
    it on disk; the capability plane serves it) + the E2C metadata
    (directness, knowledge-gate markers)."""
    return {
        "name": name,
        "files": files,
        "prompt": prompt,
        "skill_id": skill_id,
        "skill": skill,
        "directness": directness,
        "markers": list(markers),
    }


TASKS: list[dict[str, Any]] = [
    _private_task(
        "private2-palisade-redaction",
        {
            "field_classes.py": (
                '"""Field classification for the log filter.\n'
                "\n"
                "Which record fields carry personal data, and how each kind is treated\n"
                "before it reaches a log line.\n"
                '"""\n'
                "\n"
                "\n"
                "def classify(field):\n"
                "    \"\"\"Return the redaction kind: 'secret', 'personal' or 'plain'.\"\"\"\n"
                '    if field in ("token", "password"):\n'
                '        return "secret"\n'
                '    if field in ("email", "phone", "card"):\n'
                '        return "personal"\n'
                '    return "plain"\n'
            ),
            "log_filter.py": (
                '"""Log filter - redact personal data before a record is written to the\n'
                'service log."""\n'
                "\n"
                "from field_classes import classify\n"
                "\n"
                "\n"
                "def redact_record(record):\n"
                '    """Render one log line from a record with personal data redacted."""\n'
                "    parts = []\n"
                "    for key, value in record.items():\n"
                "        rendered = _redact(key, str(value))\n"
                "        if rendered is not None:\n"
                '            parts.append(f"{key}={rendered}")\n'
                '    return " ".join(parts)\n'
                "\n"
                "\n"
                "def _redact(key, value):\n"
                "    kind = classify(key)\n"
                '    if kind == "secret":\n'
                '        return "[REDACTED]"\n'
                '    if kind == "personal":\n'
                '        if key == "email":\n'
                '            local, _, domain = value.partition("@")\n'
                '            return f"{local[:1]}***@{domain}"\n'
                '        if key == "phone":\n'
                '            return f"***-***-{value[-4:]}"\n'
                '        return f"{value[:6]}****{value[-4:]}"\n'
                "    return value\n"
            ),
            "test_log_redaction.py": (
                '"""Contract tests for the log filter against the INTERNAL Palisade log\n'
                "redaction standard. The standard is not public: each sample record's\n"
                "expected log line is pinned as a sha256 DIGEST over the rendered line,\n"
                'so this file cannot become a copy of the standard."""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from log_filter import redact_record\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "RECORDS = [\n"
                "    {\n"
                '        "event": "login",\n'
                '        "email": "dana.kowalski@example.com",\n'
                '        "phone": "+1-415-555-0134",\n'
                '        "zip": "94107",\n'
                '        "note": "user logged in from the lobby kiosk",\n'
                "    },\n"
                "    {\n"
                '        "event": "checkout",\n'
                '        "card": "4242424242424242",\n'
                '        "token": "tok_9f8e7d6c5b4a",\n'
                '        "email": "mi.chen@example.org",\n'
                '        "zip": "10001",\n'
                "    },\n"
                "    {\n"
                '        "event": "support_call",\n'
                '        "phone": "555",\n'
                '        "password": "hunter2!",\n'
                '        "birth_year": "1984",\n'
                '        "note": "caller forgot the answer to their security question",\n'
                "    },\n"
                "    {\n"
                '        "request_id": "req-77ab",\n'
                '        "event": "page_view",\n'
                '        "email": "a@b.co",\n'
                '        "session": "sess-0099",\n'
                "    },\n"
                "    {\n"
                '        "event": "refund",\n'
                '        "card": "5555555555554444",\n'
                '        "birth_year": "1955",\n'
                '        "zip": "02138",\n'
                '        "note": "refunded twice, see ticket 4471",\n'
                "    },\n"
                "    {\n"
                '        "event": "token_rotate",\n'
                '        "token": "tok_deadbeef42",\n'
                '        "password": "x",\n'
                '        "email": "ops@internal.example.com",\n'
                "    },\n"
                "    {\n"
                '        "event": "geo_ping",\n'
                '        "zip": "94",\n'
                '        "phone": "41555",\n'
                '        "email": "a.b@example.com",\n'
                "    },\n"
                "]\n"
                "\n"
                "# sha256 of redact_record(record) per record, from the standard.\n"
                "DIGESTS = [\n"
                '    "fa891a7fc1c75fabbc3ee2f1f3587359871e727eb7be9a1efeef0034b6d15ede",\n'
                '    "fcc949f3375c1d6e89ea64e2d47e8382c14dd4265b78bfc719e13d2d56b453aa",\n'
                '    "a2ab63a581c3610fea264b5b234afaa4db6ff22650d1103fcf220c4c50c1d2d5",\n'
                '    "7dff6b644392b5b7bb3467e3cc34fc817352eb983317db58554014b5a1f2899f",\n'
                '    "6af973b8392b436a551a3386d08e6c1f4349dd719482bf404c10e11c23b62bec",\n'
                '    "62eac5d810119260b3b63c4820aa3b740d85f8cd177a73607ae79a19a131427d",\n'
                '    "f5f2d562a9342f4c45fc5cb205e0299521a420cb48f430c76abe3c25e695791c",\n'
                "]\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("record", "digest"),\n'
                "    zip(RECORDS, DIGESTS, strict=True),\n"
                '    ids=[f"r{i:02d}" for i in range(1, len(RECORDS) + 1)],\n'
                ")\n"
                "def test_line(record, digest):\n"
                "    line = redact_record(record)\n"
                "    assert _digest(line) == digest, line\n"
            ),
        },
        "The tests in test_log_redaction.py fail. This log filter does not follow the "
        "Palisade log redaction rules our security team owns: which record fields may "
        "reach a service log line, and in what rendered form, are decided by that "
        "internal standard, not by guesswork. Find the root cause before proposing "
        "any fix, then fix it and make the whole test suite green.",
        "palisade-log-hygiene",
        """---
name: palisade-log-hygiene
description: Palisade log redaction rules - field classes, redaction modes, exact formats.
version: 1.0.0
---

# Palisade Log Redaction Rules (internal)

How a service log line is rendered from a record before the line is
written. Internal to the Palisade security team - not published
anywhere.

`redact_record(record)` renders ONE line from a record: a mapping of
field name to string value.

## Field classes

Each field name has a class:

| Field      | Class |
|------------|-------|
| email      | IDENT |
| phone      | IDENT |
| card       | PAN   |
| token      | CRED  |
| password   | CRED  |
| zip        | QUASI |
| birth_year | QUASI |

Any other field name is class FREE.

## Modes

- CRED - DROP: the field is removed; neither key nor value is rendered.
- IDENT - MASK: the first 2 characters, then the ellipsis character
  `\u2026`, then the last 2 characters. A value shorter than 5
  characters is DROPped instead.
- PAN - HASH: `pan:` followed by the first 12 hex characters of the
  sha256 digest of the value (utf-8).
- QUASI - COARSEN: the first 2 characters, then one `#` per remaining
  character.
- FREE - KEEP: the value verbatim.

## Rendering

The kept fields are rendered in sorted key order, each as `key=value`,
joined by `" | "`.

## Worked example

    {"event": "login", "email": "jo.reyes@example.com",
     "phone": "+1-312-555-0177", "zip": "60614",
     "note": "signed in from mobile"}

renders (sorted keys: email, event, note, phone, zip):

    email=jo\u2026om | event=login | note=signed in from mobile | phone=+1\u202677 | zip=60###
""",
        "named",
        (
            "IDENT",
            "QUASI",
            "CRED",
            "PAN",
            "pan:",
            "\u2026",
            "zip=60###",
        ),
    ),
    _private_task(
        "private2-cairn-sunset",
        {
            "lifecycle.py": (
                '"""API version lifecycle - the phase of a version on a given date."""\n'
                "\n"
                "\n"
                "def state_of(version_info, now):\n"
                "    \"\"\"Return the version's phase: 'active', 'deprecated', 'sunset' or\n"
                '    \'removed\'."""\n'
                '    deprecated_on = version_info.get("deprecated_on")\n'
                '    sunset_on = version_info.get("sunset_on")\n'
                '    removed_on = version_info.get("removed_on")\n'
                "    if removed_on is not None and now >= removed_on:\n"
                '        return "removed"\n'
                "    if sunset_on is not None and now >= sunset_on:\n"
                '        return "sunset"\n'
                "    if deprecated_on is not None and now >= deprecated_on:\n"
                '        return "deprecated"\n'
                '    return "active"\n'
            ),
            "versioning.py": (
                '"""API versioning middleware - decorate responses with the version\'s\n'
                'lifecycle headers."""\n'
                "\n"
                "from lifecycle import state_of\n"
                "\n"
                "\n"
                "def decorate(response, version_info, now):\n"
                '    """Return the response decorated for the version\'s lifecycle phase."""\n'
                "    decorated = dict(response)\n"
                '    headers = dict(response.get("headers", {}))\n'
                '    headers["X-API-Version"] = version_info["version"]\n'
                "    state = state_of(version_info, now)\n"
                '    deprecated_on = version_info.get("deprecated_on")\n'
                '    sunset_on = version_info.get("sunset_on")\n'
                '    removed_on = version_info.get("removed_on")\n'
                '    if state in ("deprecated", "sunset") and deprecated_on is not None:\n'
                '        headers["Deprecation"] = deprecated_on.isoformat()\n'
                '    if state in ("deprecated", "sunset") and sunset_on is not None:\n'
                '        headers["Sunset"] = sunset_on.isoformat()\n'
                '    if state == "removed":\n'
                '        decorated["status"] = 410\n'
                '    decorated["headers"] = headers\n'
                "    return decorated\n"
            ),
            "test_version_headers.py": (
                '"""Contract tests for the response middleware against the INTERNAL\n'
                "versioning policy of the platform API team. The policy is not public:\n"
                "each sample request's expected decorated response is pinned as a\n"
                "sha256 DIGEST over the canonical JSON rendering, so this file cannot\n"
                'become a copy of the policy."""\n'
                "\n"
                "import hashlib\n"
                "import json\n"
                "from datetime import date\n"
                "\n"
                "import pytest\n"
                "\n"
                "from versioning import decorate\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "def _entry(version, introduced, deprecated, sunset, removed):\n"
                "    return {\n"
                '        "version": version,\n'
                '        "introduced_on": introduced,\n'
                '        "deprecated_on": deprecated,\n'
                '        "sunset_on": sunset,\n'
                '        "removed_on": removed,\n'
                "    }\n"
                "\n"
                "\n"
                "V2 = _entry(\n"
                '    "v2",\n'
                "    date(2025, 2, 10),\n"
                "    date(2026, 2, 1),\n"
                "    date(2026, 8, 1),\n"
                "    date(2027, 2, 1),\n"
                ")\n"
                "V5 = _entry(\n"
                '    "v5",\n'
                "    date(2025, 6, 20),\n"
                "    date(2026, 1, 5),\n"
                "    date(2026, 3, 1),\n"
                "    date(2027, 1, 10),\n"
                ")\n"
                "V7 = _entry(\n"
                '    "v7",\n'
                "    date(2024, 4, 1),\n"
                "    date(2025, 6, 1),\n"
                "    date(2025, 12, 1),\n"
                "    date(2026, 5, 1),\n"
                ")\n"
                'V9 = _entry("v9", date(2026, 3, 1), None, None, None)\n'
                "\n"
                "RESP_OK = {\n"
                '    "status": 200,\n'
                '    "headers": {"Content-Type": "application/json"},\n'
                "    'body': '{\"items\": []}',\n"
                "}\n"
                "RESP_LIST = {\n"
                '    "status": 200,\n'
                '    "headers": {"Content-Type": "application/json", "X-Request-Id": "req-3341"},\n'
                '    \'body\': \'{"rows": 3, "cursor": "next-page-token-9"}\',\n'
                "}\n"
                "RESP_ERR = {\n"
                '    "status": 500,\n'
                '    "headers": {"Content-Type": "text/plain", "Retry-After": "30"},\n'
                '    "body": "internal error",\n'
                "}\n"
                "\n"
                "CASES = [\n"
                "    (RESP_OK, V9, date(2026, 6, 1)),\n"
                "    (RESP_LIST, V2, date(2026, 2, 1)),\n"
                "    (RESP_LIST, V2, date(2026, 8, 1)),\n"
                "    (RESP_OK, V2, date(2027, 2, 1)),\n"
                "    (RESP_LIST, V5, date(2026, 4, 20)),\n"
                "    (RESP_OK, V5, date(2026, 8, 10)),\n"
                "    (RESP_ERR, V7, date(2026, 10, 1)),\n"
                "]\n"
                "\n"
                "# sha256 of json.dumps(decorate(response, entry, now), sort_keys=True) per\n"
                "# case, from the policy.\n"
                "DIGESTS = [\n"
                '    "faa8966c140d5c4ebc59936a93a1cbf366a974ac5708205e5d154505f19261b2",\n'
                '    "e7797377332d335ccf246bd33bb2aca06a3c1ae7d86e10f19c688fe1572d3e03",\n'
                '    "d622576c54204feebb613bdf0ba6fae8e37bdfae970613284f8435b2f36f150d",\n'
                '    "a063b61b258f11bd7951b44362b73c43e6459b456a6e6079a40fb17d61ddf15e",\n'
                '    "17a000916957ee25875704ac8372c59c5efd0dbadaa12153aed72f7e3e381735",\n'
                '    "584f50f63894c03176bf329f01e8c4a87549c0833fbdba7c75072e5606afecd1",\n'
                '    "77a443ef5d9b654a1f1f8d1de7eba56595a1c94177451347ce8034329449c709",\n'
                "]\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("case", "digest"),\n'
                "    zip(CASES, DIGESTS, strict=True),\n"
                '    ids=[f"c{i:02d}" for i in range(1, len(CASES) + 1)],\n'
                ")\n"
                "def test_decorated(case, digest):\n"
                "    response, entry, now = case\n"
                "    decorated = decorate(response, entry, now)\n"
                "    canonical = json.dumps(decorated, sort_keys=True)\n"
                "    assert _digest(canonical) == digest, canonical\n"
            ),
        },
        "The tests in test_version_headers.py fail. This response middleware does not "
        "follow how we handle retiring old API versions here: what a response must "
        "carry while a version is on its way out, and for how long, is decided by "
        "our internal policy, not by guesswork. Find the root cause before proposing "
        "any fix, then fix it and make the whole test suite green.",
        "cairn-api-sunset-policy",
        """---
name: cairn-api-sunset-policy
description: Cairn API sunset rules - version states, response headers, dates and the notice floor.
version: 1.0.0
---

# Cairn Sunset Rules (internal)

How every response from a versioned API must be decorated before it is
returned. Internal to the Cairn platform API team - not published
anywhere.

`decorate(response, version_info, now)` returns the decorated
response. `response` is `{"status": <int>, "headers": {name: value},
"body": <str>}`. `version_info` is the version's registry entry:
`version`, `introduced_on`, `deprecated_on`, `sunset_on`, `removed_on`
(dates; `None` = not set). `now` is the request date.

## States

The state follows `now` (every boundary is INCLUSIVE):

- `deprecated_on` unset, or `now < deprecated_on` -> STEADY.
- `deprecated_on <= now < sunset_eff` -> NOTICE.
- `sunset_eff <= now < removed_on` -> FINAL.
- `removed_on <= now` -> GONE.

`sunset_eff` is the EFFECTIVE sunset: `sunset_on`, but never earlier
than `deprecated_on` + 180 days (the minimum notice). A version with
`sunset_on` set but `deprecated_on` unset is malformed: treat it as
STEADY.

## Decoration

- EVERY state sets `X-Cairn-Version: <version>`.
- STEADY: nothing else; the response passes through unchanged.
- NOTICE: adds `X-Cairn-Deprecation: <deprecated_on>` and
  `X-Cairn-Sunset: <sunset_eff>`.
- FINAL: adds `X-Cairn-Sunset: <sunset_eff>` and
  `X-Cairn-Removal: <removed_on>`.
- GONE: the response is REPLACED: status 410, headers exactly
  `X-Cairn-Version` and `X-Cairn-Removal: <removed_on>`, empty body.

Every date in a header value is rendered as YYYYMMDD (compact, no
separators), e.g. 20260901.

## Worked example

    version_info = {"version": "v3",
                     "introduced_on": date(2025, 1, 15),
                     "deprecated_on": date(2026, 2, 1),
                     "sunset_on": date(2026, 8, 1),
                     "removed_on": date(2027, 2, 1)}

With `now = date(2026, 3, 10)`: 2026-08-01 is 181 days after
2026-02-01, so `sunset_eff` is 2026-08-01, and the state is NOTICE
(2026-02-01 <= 2026-03-10 < 2026-08-01). The response passes through
with its own status and gains:

    X-Cairn-Version: v3
    X-Cairn-Deprecation: 20260201
    X-Cairn-Sunset: 20260801

With `now = date(2026, 9, 2)`: the state is FINAL (2026-08-01 <=
2026-09-02 < 2027-02-01), and the response gains:

    X-Cairn-Version: v3
    X-Cairn-Sunset: 20260801
    X-Cairn-Removal: 20270201
""",
        "indirect",
        (
            "STEADY",
            "NOTICE",
            "FINAL",
            "GONE",
            "X-Cairn-Version",
            "X-Cairn-Deprecation",
            "X-Cairn-Sunset",
            "X-Cairn-Removal",
        ),
    ),
]

# The known root-cause fix per fixture - whole-file replacements (the fix
# IS the filter/middleware conforming to the fixture's private standard),
# in the same format verify_private_fixtures.PRIVATE_FIXES uses:
# (file, None, content) replaces the file; (file, old, new) splices.
FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    # The Palisade Log Redaction Rules: the field-class table, the
    # per-class modes (CRED drop, IDENT first-2 + ellipsis + last-2 with
    # the short-value drop, PAN pan: + 12 hex of sha256, QUASI first-2 +
    # #-fill), FREE keep, sorted-key order and the " | " joiner.
    "private2-palisade-redaction": [
        (
            "field_classes.py",
            None,
            '"""Field classification for the log filter - the Palisade Log Redaction\n'
            "Rules (internal): the class of each known field name.\n"
            '"""\n'
            "\n"
            "_CLASSES = {\n"
            '    "email": "IDENT",\n'
            '    "phone": "IDENT",\n'
            '    "card": "PAN",\n'
            '    "token": "CRED",\n'
            '    "password": "CRED",\n'
            '    "zip": "QUASI",\n'
            '    "birth_year": "QUASI",\n'
            "}\n"
            "\n"
            "\n"
            "def classify(field):\n"
            '    """Return the field\'s class; any other field name is FREE."""\n'
            '    return _CLASSES.get(field, "FREE")\n',
        ),
        (
            "log_filter.py",
            None,
            '"""Log filter - implements the Palisade Log Redaction Rules (internal)."""\n'
            "\n"
            "import hashlib\n"
            "\n"
            "from field_classes import classify\n"
            "\n"
            "\n"
            "def redact_record(record):\n"
            '    """Render one log line from a record, redacted per the rules."""\n'
            "    rendered = []\n"
            "    for key in sorted(record):\n"
            "        value = str(record[key])\n"
            "        klass = classify(key)\n"
            '        if klass == "CRED":\n'
            "            continue\n"
            '        if klass == "IDENT":\n'
            "            if len(value) < 5:\n"
            "                continue\n"
            '            value = value[:2] + "\u2026" + value[-2:]\n'
            '        elif klass == "PAN":\n'
            '            value = "pan:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]\n'
            '        elif klass == "QUASI":\n'
            '            value = value[:2] + "#" * max(0, len(value) - 2)\n'
            '        rendered.append(f"{key}={value}")\n'
            '    return " | ".join(rendered)\n',
        ),
    ],
    # The Cairn Sunset Rules: the date-derived states with inclusive
    # boundaries and the 180-day minimum-notice floor, the per-state
    # header sets, the compact YYYYMMDD date format and the GONE
    # response replacement (410, only the two headers, empty body).
    "private2-cairn-sunset": [
        (
            "lifecycle.py",
            None,
            '"""API version lifecycle - the Cairn Sunset Rules (internal): the state\n'
            "of a version on a given date, with the minimum-notice floor.\n"
            '"""\n'
            "\n"
            "from datetime import timedelta\n"
            "\n"
            "MIN_NOTICE_DAYS = 180\n"
            "\n"
            "\n"
            "def sunset_eff(version_info):\n"
            '    """The effective sunset: never earlier than deprecated_on + 180 days."""\n'
            '    deprecated_on = version_info.get("deprecated_on")\n'
            '    sunset_on = version_info.get("sunset_on")\n'
            "    if deprecated_on is None or sunset_on is None:\n"
            "        return sunset_on\n"
            "    return max(sunset_on, deprecated_on + timedelta(days=MIN_NOTICE_DAYS))\n"
            "\n"
            "\n"
            "def state_of(version_info, now):\n"
            '    """Return the version\'s state: STEADY, NOTICE, FINAL or GONE."""\n'
            '    deprecated_on = version_info.get("deprecated_on")\n'
            '    sunset_on = version_info.get("sunset_on")\n'
            '    removed_on = version_info.get("removed_on")\n'
            "    if deprecated_on is None or sunset_on is None:\n"
            '        return "STEADY"\n'
            "    if removed_on is not None and now >= removed_on:\n"
            '        return "GONE"\n'
            "    if now >= sunset_eff(version_info):\n"
            '        return "FINAL"\n'
            "    if now >= deprecated_on:\n"
            '        return "NOTICE"\n'
            '    return "STEADY"\n',
        ),
        (
            "versioning.py",
            None,
            '"""API versioning middleware - implements the Cairn Sunset Rules\n'
            '(internal): the per-state response decoration."""\n'
            "\n"
            "from lifecycle import state_of, sunset_eff\n"
            "\n"
            "\n"
            "def _ymd(day):\n"
            '    return f"{day.year:04d}{day.month:02d}{day.day:02d}"\n'
            "\n"
            "\n"
            "def decorate(response, version_info, now):\n"
            '    """Return the response decorated for the version\'s state."""\n'
            "    state = state_of(version_info, now)\n"
            '    version = version_info["version"]\n'
            '    if state == "GONE":\n'
            "        return {\n"
            '            "status": 410,\n'
            '            "headers": {\n'
            '                "X-Cairn-Version": version,\n'
            '                "X-Cairn-Removal": _ymd(version_info["removed_on"]),\n'
            "            },\n"
            '            "body": "",\n'
            "        }\n"
            "    decorated = dict(response)\n"
            '    headers = dict(response.get("headers", {}))\n'
            '    headers["X-Cairn-Version"] = version\n'
            '    if state == "NOTICE":\n'
            '        headers["X-Cairn-Deprecation"] = _ymd(version_info["deprecated_on"])\n'
            '        headers["X-Cairn-Sunset"] = _ymd(sunset_eff(version_info))\n'
            '    elif state == "FINAL":\n'
            '        headers["X-Cairn-Sunset"] = _ymd(sunset_eff(version_info))\n'
            '        headers["X-Cairn-Removal"] = _ymd(version_info["removed_on"])\n'
            '    decorated["headers"] = headers\n'
            "    return decorated\n",
        ),
    ],
}
