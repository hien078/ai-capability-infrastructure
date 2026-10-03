"""Private-knowledge fixtures for the E3-dense skill axis (builder "b", 2026-10-03).

rc-bench (ADR-014 amendment 30) measured the REAL OpenCode client with
the ACI routing plugin vs OpenCode's NATIVE skills over a 45-skill
corpus: both arms passed 12/12 and native was cheaper. The open
question this corpus answers: does ACI's router win when the corpus is
LARGE and DENSE - many near-identical internal standards that differ
only in WHICH team/service they apply to? This module is builder b's
share of that dense corpus: TWO families x FIVE variants (10
fixtures). Each variant is a separate fictional internal standard of
ONE team, scoped to that team's services; the five variants of a
family share structure and vocabulary class and differ in >= 2
concrete decisions, so a sibling's rules FAIL this variant's tests
(the per-family confusion matrix in tests/unit/test_private3_tasks_b.py
proves it mechanically).

Family "api-version-retirement-headers" - which headers a response
carries while its API version is being retired:

- private3-bramble-retire  -> bramble-retirement-headers
      the Bramble commerce API group (cart, pricing):
      slash dates, client-visible-only decoration, the retirement
      link, the 410 replacement.
- private3-thistle-retire  -> thistle-retirement-headers
      the Thistle identity API group (auth, tokens):
      "DD Mon YYYY" dates, strictly-after deprecation boundary,
      headers on errors too, the migration guide.
- private3-vernier-retire  -> vernier-retirement-headers
      the Vernier metrics API group (fanout, rollup):
      unix-seconds dates, the 90-day notice floor, 2xx-only
      decoration, the 404 unknown-version replacement.
- private3-solstice-retire -> solstice-retirement-headers
      the Solstice media API group (clip, transcode):
      ISO dates, the grace-until date, version header on 2xx only,
      the docs-URL body.
- private3-trellis-retire  -> trellis-retirement-headers
      the Trellis cargo API group (shipapi, routes):
      dotted dates, the successor header, never on 5xx, the help
      link.

Family "log-field-redaction" - which record fields may reach a service
log line, and in what masked form:

- private3-kestrel-redact  -> kestrel-log-redaction
      the Kestrel support platform (tickets, helpdesk):
      CONTACT/PAN/CREDENTIAL/TRACE classes, sorted keys, space join.
- private3-obsidian-redact -> obsidian-log-redaction
      the Obsidian billing platform (invoices, dunning):
      ADDR/FUNDS/LOCK/TALLY classes, record order, " | " join.
- private3-mosaic-redact   -> mosaic-log-redaction
      the Mosaic analytics platform (events, insights):
      SUBJECT/SECRET/ROUTE classes, class-grouped order, TAB join.
- private3-capstan-redact  -> capstan-log-redaction
      the Capstan infrastructure observability services
      (deploys, audit): PASSKEY/IDBOX/REF/PLAIN classes,
      unknown fields dropped (fail closed), ";" join.
- private3-parallax-redact -> parallax-log-redaction
      the Parallax HR platform (payroll, onboard):
      RESTRICTED/PERSON/META classes, record order, the drop marker.

Same contract as scripts/private_tasks.py: each standard is INVENTED
with the fixture (it did not exist before today, so no model can
carry it), documented ONLY in the fixture's private SKILL.md (never a
workspace file), and pinned in the workspace tests as sha256 DIGESTS
over canonical traces - never plaintext, over a large output space
(the E2B section 1.5 lesson: each digest covers 3-6 lines that each
combine several standard-only decisions). The shipped code embodies a
PLAUSIBLE BUT WRONG public convention (the IETF Deprecation/Sunset
draft headers; [REDACTED] masking), so a naked guess fails. Every
prompt is INDIRECT: it names the SERVICE, never the standard, its
team title words or any rule. Fake PII (emails, phones, card/iban
numbers, SSNs, passwords, tokens) appears as test INPUT only - all
values invented for these fixtures.

§34 caveat applies to any round run on this set: small n,
author-built fixtures, one model - directional only.
"""

import sys
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
    """A private-knowledge fixture: workspace files + task prompt + the
    private SKILL.md text (NOT a workspace file - the model must never
    see it on disk; the measurement arms serve it through the capability
    plane) + the E3-dense metadata: ``family`` (the kind of standard the
    five near-identical siblings belong to) and ``markers`` (the
    standard's distinctive vocabulary - it must exist ONLY in the skill,
    never in any workspace file, any prompt or any SIBLING skill)."""
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


# ============================================================================
# Family 1: API version retirement headers - the Bramble commerce API group
# (cart). Shipped code guesses the IETF Deprecation/Sunset draft
# convention (ISO dates, Link rel="deprecation", 410 Gone).
# ============================================================================

_BR_LIFECYCLE = r'''"""Version lifecycle for the cart API.

A version is active until its deprecation date, deprecated until its
sunset date, sunset until its removal date, and removed afterwards.
"""


def state_of(version_info, now):
    """The version's phase: active, deprecated, sunset or removed."""
    deprecated_on = version_info.get("deprecated_on")
    sunset_on = version_info.get("sunset_on")
    removed_on = version_info.get("removed_on")
    if removed_on is not None and now >= removed_on:
        return "removed"
    if sunset_on is not None and now >= sunset_on:
        return "sunset"
    if deprecated_on is not None and now >= deprecated_on:
        return "deprecated"
    return "active"
'''

_BR_HEADERS = r'''"""Response decoration for cart - API version lifecycle headers.

Every response carries the API version; a deprecated or sunset version
adds the standard Deprecation and Sunset headers with ISO dates plus a
deprecation link; a removed version answers 410 Gone.
"""

from lifecycle import state_of


def decorate(response, version_info, now):
    """Return the response decorated for the version's phase."""
    decorated = dict(response)
    headers = dict(response.get("headers", {}))
    headers["X-Api-Version"] = version_info["version"]
    state = state_of(version_info, now)
    if state in ("deprecated", "sunset"):
        if version_info.get("deprecated_on") is not None:
            headers["Deprecation"] = version_info["deprecated_on"].isoformat()
        if version_info.get("sunset_on") is not None:
            headers["Sunset"] = version_info["sunset_on"].isoformat()
        headers["Link"] = (
            f'<https://docs.example.com/retirement/{version_info["version"]}>;'
            ' rel="deprecation"'
        )
    if state == "removed":
        decorated["status"] = 410
        decorated["body"] = ""
    decorated["headers"] = headers
    return decorated
'''

_BR_TEST = r'''"""Contract tests for the response middleware of this service against the
INTERNAL version retirement rules of its API group. The rules are not
public: each group's expected behavior is pinned as a sha256 DIGEST over
the canonical JSON rendering of the decorated responses, so this file
cannot become a copy of the rules."""

import hashlib
import json
from datetime import date

import pytest

from headers import decorate


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(version, introduced, deprecated, sunset, removed, successor=None):
    return {
        "version": version,
        "introduced_on": introduced,
        "deprecated_on": deprecated,
        "sunset_on": sunset,
        "removed_on": removed,
        "successor": successor,
    }


def _resp(status, headers=None, body=""):
    return {"status": status, "headers": dict(headers or {}), "body": body}


EA = _entry(
    "2024-11",
    date(2024, 11, 2),
    date(2026, 2, 1),
    date(2026, 8, 1),
    date(2027, 2, 1),
    "2025-06",
)
EB = _entry(
    "2025-03", date(2025, 3, 15), date(2026, 5, 20), date(2026, 11, 1), date(2027, 5, 10)
)
EC = _entry(
    "2023-07",
    date(2023, 7, 1),
    date(2025, 9, 1),
    date(2026, 3, 1),
    date(2026, 9, 15),
    "2024-11",
)
ED = _entry("2026-01", date(2026, 1, 5), None, None, None)
EE = _entry("2025-06", date(2025, 6, 1), date(2026, 1, 10), None, date(2027, 1, 10))
EF = _entry(
    "2024-05", date(2024, 5, 1), date(2025, 11, 1), date(2026, 5, 1), None, "2025-03"
)

R_OK = _resp(200, {"Content-Type": "application/json"}, '{"items": []}')
R_LIST = _resp(
    200,
    {"Content-Type": "application/json", "X-Request-Id": "req-3341"},
    '{"rows": 3}',
)
R_CREATED = _resp(201, {"Location": "/orders/ord-77"}, "")
R_MOVED = _resp(301, {"Location": "/v2/items"}, "")
R_MISSING = _resp(404, {"Content-Type": "text/plain"}, "no such item")
R_RATE = _resp(429, {"Retry-After": "30"}, "slow down")
R_BOOM = _resp(500, {"Content-Type": "text/plain"}, "internal error")

G1 = [
    (R_OK, ED, date(2026, 6, 1)),
    (R_LIST, EA, date(2025, 6, 15)),
    (R_MOVED, ED, date(2026, 2, 1)),
    (R_MISSING, EA, date(2025, 12, 1)),
    (R_BOOM, ED, date(2026, 3, 1)),
]
G2 = [
    (R_OK, EA, date(2026, 2, 1)),
    (R_LIST, EA, date(2026, 3, 10)),
    (R_CREATED, EB, date(2026, 6, 1)),
    (R_MOVED, EA, date(2026, 7, 4)),
    (R_MISSING, EA, date(2026, 4, 2)),
    (R_RATE, EB, date(2026, 7, 1)),
]
G3 = [
    (R_OK, EA, date(2026, 8, 1)),
    (R_LIST, EA, date(2026, 9, 20)),
    (R_MOVED, EC, date(2026, 4, 1)),
    (R_MISSING, EA, date(2026, 10, 31)),
    (R_OK, EE, date(2026, 12, 1)),
    (R_OK, EF, date(2026, 6, 1)),
]
G4 = [
    (R_OK, EC, date(2026, 9, 15)),
    (R_LIST, EC, date(2026, 10, 1)),
    (R_MOVED, EC, date(2026, 12, 25)),
    (R_MISSING, EC, date(2027, 1, 1)),
    (R_BOOM, EC, date(2027, 2, 1)),
]

GROUPS = {"g1": G1, "g2": G2, "g3": G3, "g4": G4}

DIGESTS = {
    "g1": "99aeec904ccab3b4fe3b267fdfe766ad939e2bc92b9ec1470f69929b0b612ef5",
    "g2": "f87a81c498876d4e667239cd32f3a837ff601bb69ffb4c8d2c9b3c64949fad2b",
    "g3": "44887758c82cdc1e505204eff7c2d22eb5a6784f687e128e63e2e911ffb9f5b7",
    "g4": "aa02533fcc8e46a86e49584192e8990059250e7f18e40f0b5336e3217d7a78e1",
}


def _lines(cases):
    out = []
    for response, entry, now in cases:
        decorated = decorate(response, entry, now)
        out.append(json.dumps(decorated, sort_keys=True))
    return out


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = _lines(GROUPS[group])
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''

_BR_SKILL = """---
name: bramble-retirement-headers
description: API version retirement headers standard for Bramble commerce services (cart, pricing).
version: 1.0.0
---

# Bramble Retirement Headers (internal)

How every response from a Bramble commerce API service (cart, pricing)
is decorated while its API version is on its way out. Internal to the
Bramble commerce API group - not published anywhere.

`decorate(response, version_info, now)` returns the decorated response.
`response` is `{"status": <int>, "headers": {name: value}, "body": <str>}`.
`version_info` is the version's registry entry: `version`,
`introduced_on`, `deprecated_on`, `sunset_on`, `removed_on` (dates or
None) and `successor` (ignored by this standard). `now` is the request
date.

## States

Boundaries are INCLUSIVE - a date counts from that day itself:

- `deprecated_on` unset, or `now < deprecated_on` -> FRESH.
- else `sunset_on` unset, or `now < sunset_on` -> WINDING.
- else `removed_on` unset, or `now < removed_on` -> COOLING.
- else -> EXPIRED.

## Decoration

- EVERY response, every state, every status gains
  `X-Bramble-Api-Version: <version>`.
- The retirement headers go ONLY on client-visible responses
  (`status < 400`); a 4xx/5xx answer carries nothing but the version
  header.
- WINDING adds `X-Bramble-Deprecation: <deprecated_on>` and
  `Link: <https://retire.bramble.example/v/<version>>; rel="retirement"`.
- COOLING adds `X-Bramble-Sunset: <sunset_on>`,
  `X-Bramble-Removal: <removed_on>` (only when `removed_on` is set)
  and the same `Link`. COOLING does NOT repeat the deprecation header.
- EXPIRED replaces the response entirely: status 410, headers exactly
  `X-Bramble-Api-Version` and `X-Bramble-Removal: <removed_on>`,
  empty body.

Every date in a header value is `YYYY/MM/DD` zero-padded, e.g.
2026/02/01.

## Worked example

    version_info = {"version": "2024-11", "introduced_on": date(2024, 11, 2),
                    "deprecated_on": date(2026, 2, 1), "sunset_on": date(2026, 8, 1),
                    "removed_on": date(2027, 2, 1), "successor": "2025-06"}

With `now = date(2026, 3, 10)` the state is WINDING; a 200 response
gains:

    X-Bramble-Api-Version: 2024-11
    X-Bramble-Deprecation: 2026/02/01
    Link: <https://retire.bramble.example/v/2024-11>; rel="retirement"

With `now = date(2026, 9, 1)` the state is COOLING; the same 200 gains
`X-Bramble-Api-Version`, `X-Bramble-Sunset: 2026/08/01`,
`X-Bramble-Removal: 2027/02/01` and the `Link`. A 404 on either date
gains only `X-Bramble-Api-Version`.
"""

# ============================================================================
# Family 1: API version retirement headers - the Thistle identity API group
# (auth). Shipped code guesses the IETF draft convention without a link.
# ============================================================================

_TH_LIFECYCLE = r'''"""Version lifecycle for the auth API.

A version is current until its deprecation date, deprecated until its
sunset date, sunset until its removal date, and removed afterwards.
"""


def state_of(version_info, now):
    """The version's phase: current, deprecated, sunset or removed."""
    deprecated_on = version_info.get("deprecated_on")
    sunset_on = version_info.get("sunset_on")
    removed_on = version_info.get("removed_on")
    if removed_on is not None and now >= removed_on:
        return "removed"
    if sunset_on is not None and now >= sunset_on:
        return "sunset"
    if deprecated_on is not None and now >= deprecated_on:
        return "deprecated"
    return "current"
'''

_TH_HEADERS = r'''"""Response decoration for auth - API version lifecycle headers.

Every response carries the API version; a deprecated or sunset version
adds the standard Deprecation and Sunset headers with ISO dates; a
removed version answers 410 Gone.
"""

from lifecycle import state_of


def decorate(response, version_info, now):
    """Return the response decorated for the version's phase."""
    decorated = dict(response)
    headers = dict(response.get("headers", {}))
    headers["X-Version"] = version_info["version"]
    state = state_of(version_info, now)
    if state in ("deprecated", "sunset"):
        if version_info.get("deprecated_on") is not None:
            headers["Deprecation"] = version_info["deprecated_on"].isoformat()
        if version_info.get("sunset_on") is not None:
            headers["Sunset"] = version_info["sunset_on"].isoformat()
    if state == "removed":
        decorated["status"] = 410
        decorated["body"] = ""
    decorated["headers"] = headers
    return decorated
'''

_TH_TEST = r'''"""Contract tests for the response middleware of this service against the
INTERNAL version retirement rules of its API group. The rules are not
public: each group's expected behavior is pinned as a sha256 DIGEST over
the canonical JSON rendering of the decorated responses, so this file
cannot become a copy of the rules."""

import hashlib
import json
from datetime import date

import pytest

from headers import decorate


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(version, introduced, deprecated, sunset, removed, successor=None):
    return {
        "version": version,
        "introduced_on": introduced,
        "deprecated_on": deprecated,
        "sunset_on": sunset,
        "removed_on": removed,
        "successor": successor,
    }


def _resp(status, headers=None, body=""):
    return {"status": status, "headers": dict(headers or {}), "body": body}


EA = _entry(
    "v3", date(2025, 1, 15), date(2026, 2, 1), date(2026, 8, 1), date(2027, 2, 1)
)
EB = _entry(
    "v4", date(2025, 6, 20), date(2026, 1, 5), date(2026, 3, 1), date(2027, 1, 10), "v5"
)
EC = _entry(
    "v2", date(2024, 4, 1), date(2025, 6, 1), date(2025, 12, 1), date(2026, 5, 1), "v3"
)
ED = _entry("v5", date(2026, 3, 1), None, None, None)
EE = _entry("v4b", date(2025, 7, 1), date(2026, 1, 20), None, date(2027, 1, 20))
EF = _entry("v2b", date(2024, 5, 1), date(2025, 5, 1), date(2025, 11, 1), None)

R_OK = _resp(200, {"Content-Type": "application/json"}, '{"session": "ok"}')
R_LIST = _resp(
    200,
    {"Content-Type": "application/json", "X-Request-Id": "req-3341"},
    '{"sessions": 2}',
)
R_CREATED = _resp(201, {"Location": "/sessions/s-77"}, "")
R_MOVED = _resp(301, {"Location": "/v5/profile"}, "")
R_MISSING = _resp(404, {"Content-Type": "text/plain"}, "no such session")
R_RATE = _resp(429, {"Retry-After": "30"}, "slow down")
R_BOOM = _resp(500, {"Content-Type": "text/plain"}, "internal error")

G1 = [
    (R_OK, ED, date(2026, 6, 1)),
    (R_LIST, EA, date(2025, 12, 1)),
    (R_OK, EA, date(2026, 2, 1)),
    (R_MOVED, ED, date(2026, 1, 1)),
    (R_MISSING, EA, date(2025, 10, 1)),
    (R_BOOM, ED, date(2026, 4, 1)),
]
G2 = [
    (R_OK, EA, date(2026, 2, 2)),
    (R_LIST, EA, date(2026, 3, 10)),
    (R_CREATED, EB, date(2026, 2, 1)),
    (R_MOVED, EA, date(2026, 7, 4)),
    (R_MISSING, EA, date(2026, 4, 2)),
    (R_BOOM, EB, date(2026, 3, 1)),
]
G3 = [
    (R_OK, EA, date(2026, 8, 1)),
    (R_LIST, EA, date(2026, 9, 20)),
    (R_MOVED, EC, date(2026, 1, 1)),
    (R_MISSING, EA, date(2026, 10, 31)),
    (R_OK, EE, date(2026, 12, 1)),
    (R_OK, EF, date(2026, 2, 1)),
]
G4 = [
    (R_OK, EC, date(2026, 5, 1)),
    (R_LIST, EC, date(2026, 6, 1)),
    (R_MOVED, EC, date(2026, 7, 25)),
    (R_MISSING, EC, date(2026, 8, 1)),
    (R_BOOM, EC, date(2026, 9, 1)),
]

GROUPS = {"g1": G1, "g2": G2, "g3": G3, "g4": G4}

DIGESTS = {
    "g1": "17d81b4cf8773c3d29d68320ca936eeae3e61ed996a2f3adb4e4e820dfa4b3e3",
    "g2": "4ab286605ba72c13335c334c61c2899d2d7004726cd77c46c16cf0b511c79d6c",
    "g3": "08b8889cabfe63b2f32992929dc0f95547f8db3649f4ecbf7f759e5d8ed3876a",
    "g4": "8867319095b5da157ec4b51f0337495e80e573cea9b87bc10a4b34a862cfedaf",
}


def _lines(cases):
    out = []
    for response, entry, now in cases:
        decorated = decorate(response, entry, now)
        out.append(json.dumps(decorated, sort_keys=True))
    return out


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = _lines(GROUPS[group])
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''

_TH_SKILL = """---
name: thistle-retirement-headers
description: API version retirement headers standard for Thistle identity services (auth, tokens).
version: 1.0.0
---

# Thistle Retirement Headers (internal)

How every response from a Thistle identity API service (auth, tokens)
is decorated while its API version is being retired. Internal to the
Thistle identity API group - not published anywhere.

`decorate(response, version_info, now)` returns the decorated response.
`response` is `{"status": <int>, "headers": {name: value}, "body": <str>}`.
`version_info` is the registry entry: `version`, `introduced_on`,
`deprecated_on`, `sunset_on`, `removed_on` (dates or None) and
`successor` (ignored by this standard). `now` is the request date.

## States

- `deprecated_on` unset, or `now <= deprecated_on` -> SUPPORTED. The
  notice phase begins STRICTLY AFTER the deprecation date: a request
  ON that date is still served as supported.
- else `sunset_on` unset, or `now < sunset_on` -> NOTICE.
- else `removed_on` unset, or `now < removed_on` -> FINAL.
- else -> GONE.

## Decoration

The retirement headers go on EVERY response, errors included:

- EVERY response gains `X-Thistle-Version: <version>`.
- NOTICE adds `X-Thistle-Deprecating: <deprecated_on>` and
  `X-Thistle-Migration: https://identity.thistle.example/migrate/<version>`.
- FINAL adds `X-Thistle-Sunset: <sunset_on>`,
  `X-Thistle-Removed-After: <removed_on>` (only when set) and the same
  `X-Thistle-Migration`. FINAL does NOT repeat the deprecation header.
- GONE replaces the response entirely: status 410, headers exactly
  `X-Thistle-Version` and `X-Thistle-Removed-After: <removed_on>`,
  empty body.

Every date in a header value is `DD Mon YYYY` with the English month
abbreviation and a zero-padded day, e.g. 01 Feb 2026.

## Worked example

    version_info = {"version": "v3", "introduced_on": date(2025, 1, 15),
                    "deprecated_on": date(2026, 2, 1), "sunset_on": date(2026, 8, 1),
                    "removed_on": date(2027, 2, 1), "successor": None}

With `now = date(2026, 2, 1)` the state is still SUPPORTED (the notice
begins strictly after). With `now = date(2026, 2, 2)` it is NOTICE and
even a 404 response gains:

    X-Thistle-Version: v3
    X-Thistle-Deprecating: 01 Feb 2026
    X-Thistle-Migration: https://identity.thistle.example/migrate/v3
"""

# ============================================================================
# Family 1: API version retirement headers - the Vernier metrics API group
# (fanout). Shipped code guesses the IETF draft convention with a
# sunset link.
# ============================================================================

_VE_LIFECYCLE = r'''"""Version lifecycle for the fanout API.

A version is live until its deprecation date, deprecated until its
sunset date, sunset until its removal date, and removed afterwards.
"""


def state_of(version_info, now):
    """The version's phase: live, deprecated, sunset or removed."""
    deprecated_on = version_info.get("deprecated_on")
    sunset_on = version_info.get("sunset_on")
    removed_on = version_info.get("removed_on")
    if removed_on is not None and now >= removed_on:
        return "removed"
    if sunset_on is not None and now >= sunset_on:
        return "sunset"
    if deprecated_on is not None and now >= deprecated_on:
        return "deprecated"
    return "live"
'''

_VE_HEADERS = r'''"""Response decoration for fanout - API version lifecycle headers.

Every response carries the API version; a deprecated or sunset version
adds the standard Deprecation and Sunset headers with ISO dates plus a
sunset link; a removed version answers 410 Gone.
"""

from lifecycle import state_of


def decorate(response, version_info, now):
    """Return the response decorated for the version's phase."""
    decorated = dict(response)
    headers = dict(response.get("headers", {}))
    headers["X-Api"] = version_info["version"]
    state = state_of(version_info, now)
    if state in ("deprecated", "sunset"):
        if version_info.get("deprecated_on") is not None:
            headers["Deprecation"] = version_info["deprecated_on"].isoformat()
        if version_info.get("sunset_on") is not None:
            headers["Sunset"] = version_info["sunset_on"].isoformat()
        headers["Link"] = (
            f'<https://docs.example.com/sunset/{version_info["version"]}>;'
            ' rel="sunset"'
        )
    if state == "removed":
        decorated["status"] = 410
        decorated["body"] = ""
    decorated["headers"] = headers
    return decorated
'''

_VE_TEST = r'''"""Contract tests for the response middleware of this service against the
INTERNAL version retirement rules of its API group. The rules are not
public: each group's expected behavior is pinned as a sha256 DIGEST over
the canonical JSON rendering of the decorated responses, so this file
cannot become a copy of the rules."""

import hashlib
import json
from datetime import date

import pytest

from headers import decorate


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(version, introduced, deprecated, sunset, removed, successor=None):
    return {
        "version": version,
        "introduced_on": introduced,
        "deprecated_on": deprecated,
        "sunset_on": sunset,
        "removed_on": removed,
        "successor": successor,
    }


def _resp(status, headers=None, body=""):
    return {"status": status, "headers": dict(headers or {}), "body": body}


EA = _entry(
    "2022.09",
    date(2022, 9, 1),
    date(2026, 2, 1),
    date(2026, 8, 1),
    date(2027, 2, 1),
    None,
)
EB = _entry(
    "2023.04",
    date(2023, 4, 15),
    date(2026, 5, 20),
    date(2026, 11, 1),
    date(2027, 5, 10),
    "2024.10",
)
EC = _entry(
    "2021.06",
    date(2021, 6, 1),
    date(2025, 9, 1),
    date(2026, 3, 1),
    date(2026, 9, 15),
    None,
)
ED = _entry("2024.10", date(2024, 10, 1), None, None, None)
EE = _entry(
    "2023.11", date(2023, 11, 1), date(2026, 1, 10), date(2026, 2, 15), date(2027, 1, 10)
)
EF = _entry("2022.02", date(2022, 2, 1), date(2025, 11, 1), date(2026, 5, 1), None)

R_OK = _resp(200, {"Content-Type": "application/json"}, '{"samples": []}')
R_LIST = _resp(
    200,
    {"Content-Type": "application/json", "X-Request-Id": "req-3341"},
    '{"series": 3}',
)
R_CREATED = _resp(201, {"Location": "/series/s-77"}, "")
R_MOVED = _resp(301, {"Location": "/v2/rollup"}, "")
R_MISSING = _resp(404, {"Content-Type": "text/plain"}, "no such series")
R_RATE = _resp(429, {"Retry-After": "30"}, "slow down")
R_BOOM = _resp(500, {"Content-Type": "text/plain"}, "internal error")

G1 = [
    (R_OK, ED, date(2026, 6, 1)),
    (R_LIST, EA, date(2025, 6, 15)),
    (R_MOVED, ED, date(2026, 2, 1)),
    (R_MISSING, EA, date(2025, 12, 1)),
    (R_BOOM, ED, date(2026, 3, 1)),
]
G2 = [
    (R_OK, EA, date(2026, 2, 1)),
    (R_LIST, EA, date(2026, 3, 10)),
    (R_CREATED, EB, date(2026, 6, 1)),
    (R_MOVED, EA, date(2026, 7, 4)),
    (R_MISSING, EA, date(2026, 4, 2)),
    (R_OK, EE, date(2026, 3, 1)),
]
G3 = [
    (R_OK, EA, date(2026, 8, 1)),
    (R_LIST, EA, date(2026, 9, 20)),
    (R_OK, EE, date(2026, 4, 10)),
    (R_MOVED, EC, date(2026, 4, 1)),
    (R_MISSING, EC, date(2026, 5, 1)),
    (R_OK, EF, date(2026, 6, 1)),
]
G4 = [
    (R_OK, EC, date(2026, 9, 15)),
    (R_LIST, EC, date(2026, 10, 1)),
    (R_MOVED, EC, date(2026, 12, 25)),
    (R_MISSING, EC, date(2027, 1, 1)),
    (R_BOOM, EC, date(2027, 2, 1)),
]

GROUPS = {"g1": G1, "g2": G2, "g3": G3, "g4": G4}

DIGESTS = {
    "g1": "7a11f564afc646b18bb6115afa474d08d66207f4d00e863ac09e961f378b1367",
    "g2": "05d04c2084a6b4f0cea5a55157ed9a80c76ee17ddea6dbaa68bb100035c663da",
    "g3": "4755c8d5bf499eebb08e07cef41007e05d9d789ab51e809c1ac431e9e0895985",
    "g4": "2773b1e35760bf16d47f772997ce90c4a48c2fa604bf8c330e8545db7d67f242",
}


def _lines(cases):
    out = []
    for response, entry, now in cases:
        decorated = decorate(response, entry, now)
        out.append(json.dumps(decorated, sort_keys=True))
    return out


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = _lines(GROUPS[group])
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''

_VE_SKILL = """---
name: vernier-retirement-headers
description: API version retirement headers standard for Vernier metrics services (fanout, rollup).
version: 1.0.0
---

# Vernier Retirement Headers (internal)

How every response from a Vernier metrics API service (fanout,
rollup) is decorated while its API version is being retired. Internal
to the Vernier metrics API group - not published anywhere.

`decorate(response, version_info, now)` returns the decorated response.
`response` is `{"status": <int>, "headers": {name: value}, "body": <str>}`.
`version_info` is the registry entry: `version`, `introduced_on`,
`deprecated_on`, `sunset_on`, `removed_on` (dates or None) and
`successor` (ignored by this standard). `now` is the request date.

## The notice floor

The EFFECTIVE sunset is `sunset_on`, but never earlier than
`deprecated_on` + 90 days (the notice floor): a version is noticed for
at least 90 days. With `deprecated_on` unset the effective sunset is
`sunset_on` as given.

## States

Boundaries are INCLUSIVE:

- `deprecated_on` unset, or `now < deprecated_on` -> CURRENT.
- else `sunset_on` unset, or `now < effective sunset` -> DEPRECATED.
- else `removed_on` unset, or `now < removed_on` -> SUNSET.
- else -> REMOVED.

## Decoration

- EVERY response gains `X-Vernier-Api: <version>`.
- The retirement headers go ONLY on 2xx responses
  (200 <= status < 300); any other status carries nothing but the
  version header.
- DEPRECATED adds `X-Vernier-Deprecation-Unix: <deprecated_on>` and
  `Link: <https://docs.vernier.example/sunset/<version>>; rel="sunset"`.
- SUNSET adds `X-Vernier-Sunset-Unix: <effective sunset>`,
  `X-Vernier-Removal-Unix: <removed_on>` (only when set) and the same
  `Link`.
- REMOVED replaces the response entirely: status 404, headers exactly
  `X-Vernier-Api` and `Content-Type: application/json`, body
  `json.dumps({"error": "unknown-version", "version": <version>})`.

Every date in a header value is UNIX SECONDS since 1970-01-01 UTC
(e.g. 1769904000), rendered as a plain integer string.

## Worked example

    version_info = {"version": "2023.11", "introduced_on": date(2023, 11, 1),
                    "deprecated_on": date(2026, 1, 10), "sunset_on": date(2026, 2, 15),
                    "removed_on": date(2027, 1, 10), "successor": None}

The sunset (2026-02-15) is only 36 days after the deprecation, so the
notice floor lifts the effective sunset to 2026-04-10. With
`now = date(2026, 3, 1)` the state is still DEPRECATED; a 200 gains:

    X-Vernier-Api: 2023.11
    X-Vernier-Deprecation-Unix: 1763664000
    Link: <https://docs.vernier.example/sunset/2023.11>; rel="sunset"

With `now = date(2026, 4, 10)` the state is SUNSET and the 200 gains
`X-Vernier-Api`, `X-Vernier-Sunset-Unix: 1775731200`,
`X-Vernier-Removal-Unix: 1793587200` and the `Link`. A 301 on either
date gains only `X-Vernier-Api`.
"""

# ============================================================================
# Family 1: API version retirement headers - the Solstice media API group
# (clip). Shipped code guesses the IETF draft convention.
# ============================================================================

_SO_LIFECYCLE = r'''"""Version lifecycle for the clip API.

A version is serving until its deprecation date, deprecated until its
sunset date, sunset until its removal date, and removed afterwards.
"""


def state_of(version_info, now):
    """The version's phase: serving, deprecated, sunset or removed."""
    deprecated_on = version_info.get("deprecated_on")
    sunset_on = version_info.get("sunset_on")
    removed_on = version_info.get("removed_on")
    if removed_on is not None and now >= removed_on:
        return "removed"
    if sunset_on is not None and now >= sunset_on:
        return "sunset"
    if deprecated_on is not None and now >= deprecated_on:
        return "deprecated"
    return "serving"
'''

_SO_HEADERS = r'''"""Response decoration for clip - API version lifecycle headers.

Every response carries the API version; a deprecated or sunset version
adds the standard Deprecation and Sunset headers with ISO dates plus a
deprecation link; a removed version answers 410 Gone.
"""

from lifecycle import state_of


def decorate(response, version_info, now):
    """Return the response decorated for the version's phase."""
    decorated = dict(response)
    headers = dict(response.get("headers", {}))
    headers["X-Api-Version"] = version_info["version"]
    state = state_of(version_info, now)
    if state in ("deprecated", "sunset"):
        if version_info.get("deprecated_on") is not None:
            headers["Deprecation"] = version_info["deprecated_on"].isoformat()
        if version_info.get("sunset_on") is not None:
            headers["Sunset"] = version_info["sunset_on"].isoformat()
        headers["Link"] = (
            f'<https://docs.example.com/retirement/{version_info["version"]}>;'
            ' rel="deprecation"'
        )
    if state == "removed":
        decorated["status"] = 410
        decorated["body"] = "version removed"
    decorated["headers"] = headers
    return decorated
'''

_SO_TEST = r'''"""Contract tests for the response middleware of this service against the
INTERNAL version retirement rules of its API group. The rules are not
public: each group's expected behavior is pinned as a sha256 DIGEST over
the canonical JSON rendering of the decorated responses, so this file
cannot become a copy of the rules."""

import hashlib
import json
from datetime import date

import pytest

from headers import decorate


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(version, introduced, deprecated, sunset, removed, successor=None):
    return {
        "version": version,
        "introduced_on": introduced,
        "deprecated_on": deprecated,
        "sunset_on": sunset,
        "removed_on": removed,
        "successor": successor,
    }


def _resp(status, headers=None, body=""):
    return {"status": status, "headers": dict(headers or {}), "body": body}


EA = _entry(
    "m7", date(2024, 7, 1), date(2026, 2, 1), date(2026, 8, 1), date(2027, 2, 1)
)
EB = _entry(
    "m8", date(2025, 3, 15), date(2026, 5, 20), date(2026, 11, 1), date(2027, 5, 10), "m9"
)
EC = _entry(
    "m5", date(2023, 4, 1), date(2025, 6, 1), date(2025, 12, 1), date(2026, 5, 1)
)
ED = _entry("m9", date(2026, 1, 5), None, None, None)
EE = _entry("m6", date(2024, 8, 1), date(2026, 1, 10), None, date(2027, 1, 10))
EF = _entry("m4", date(2023, 1, 1), date(2025, 11, 1), date(2026, 5, 1), None)

R_OK = _resp(200, {"Content-Type": "application/json"}, '{"clips": []}')
R_LIST = _resp(
    200,
    {"Content-Type": "application/json", "X-Request-Id": "req-3341"},
    '{"clips": 3}',
)
R_CREATED = _resp(201, {"Location": "/clips/c-77"}, "")
R_MOVED = _resp(301, {"Location": "/m9/library"}, "")
R_MISSING = _resp(404, {"Content-Type": "text/plain"}, "no such clip")
R_RATE = _resp(429, {"Retry-After": "30"}, "slow down")
R_BOOM = _resp(500, {"Content-Type": "text/plain"}, "internal error")

G1 = [
    (R_OK, ED, date(2026, 6, 1)),
    (R_LIST, EA, date(2025, 6, 15)),
    (R_MOVED, ED, date(2026, 2, 1)),
    (R_MISSING, EA, date(2025, 12, 1)),
    (R_BOOM, ED, date(2026, 3, 1)),
]
G2 = [
    (R_OK, EA, date(2026, 2, 1)),
    (R_LIST, EA, date(2026, 3, 10)),
    (R_CREATED, EB, date(2026, 6, 1)),
    (R_MOVED, EA, date(2026, 7, 4)),
    (R_MISSING, EA, date(2026, 4, 2)),
    (R_OK, EE, date(2026, 12, 1)),
]
G3 = [
    (R_OK, EA, date(2026, 8, 1)),
    (R_LIST, EA, date(2026, 9, 20)),
    (R_MOVED, EC, date(2026, 1, 1)),
    (R_MISSING, EC, date(2026, 2, 1)),
    (R_OK, EF, date(2026, 6, 1)),
]
G4 = [
    (R_OK, EC, date(2026, 5, 1)),
    (R_LIST, EC, date(2026, 6, 1)),
    (R_MOVED, EC, date(2026, 7, 25)),
    (R_MISSING, EC, date(2026, 8, 1)),
    (R_BOOM, EC, date(2026, 9, 1)),
]

GROUPS = {"g1": G1, "g2": G2, "g3": G3, "g4": G4}

DIGESTS = {
    "g1": "599bf221e50e38ad518eeee736c8e3f1eb96cb114242e436494b7034c3ee4cf8",
    "g2": "f007d65f9f32a935e4bddffd76d77496af0948ecdd9698049233c1397f98136b",
    "g3": "5e7615220d0c8a75fff76cd2f45f3a8dfcbeb2e80080f0125f13cad2cad9019b",
    "g4": "00889950cad45e322d643315f27fe74321e4d139493e3f872ee5c4859c8a421c",
}


def _lines(cases):
    out = []
    for response, entry, now in cases:
        decorated = decorate(response, entry, now)
        out.append(json.dumps(decorated, sort_keys=True))
    return out


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = _lines(GROUPS[group])
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''

_SO_SKILL = """---
name: solstice-retirement-headers
description: API version retirement headers standard for Solstice media services (clip, transcode).
version: 1.0.0
---

# Solstice Retirement Headers (internal)

How every response from a Solstice media API service (clip,
transcode) is decorated while its API version is being retired.
Internal to the Solstice media API group - not published anywhere.

`decorate(response, version_info, now)` returns the decorated response.
`response` is `{"status": <int>, "headers": {name: value}, "body": <str>}`.
`version_info` is the registry entry: `version`, `introduced_on`,
`deprecated_on`, `sunset_on`, `removed_on` (dates or None) and
`successor` (ignored by this standard). `now` is the request date.

## States

Boundaries are INCLUSIVE:

- `deprecated_on` unset, or `now < deprecated_on` -> SERVING.
- else `sunset_on` unset, or `now < sunset_on` -> TWILIGHT.
- else `removed_on` unset, or `now < removed_on` -> ARCHIVE.
- else -> PURGED.

## Decoration

- `X-Solstice-Api-Version: <version>` goes ONLY on 2xx responses
  (200 <= status < 300) - never on a 3xx/4xx/5xx answer.
- The retirement headers go on EVERY response, whatever the status:
  - TWILIGHT adds `X-Solstice-Deprecation: <deprecated_on>` and
    `X-Solstice-Grace-Until: <removed_on - 30 days>` (the grace date;
    only when `removed_on` is set).
  - ARCHIVE adds `X-Solstice-Sunset: <sunset_on>`,
    `X-Solstice-Purge: <removed_on>` (only when set) and
    `X-Solstice-Docs: https://media.solstice.example/versions/<version>`.
- PURGED replaces the response entirely: status 410, headers exactly
  `X-Solstice-Purge: <removed_on>`, and the body IS the docs URL
  (`https://media.solstice.example/versions/<version>`).

Every date in a header value is ISO `YYYY-MM-DD`, e.g. 2026-08-01.

## Worked example

    version_info = {"version": "m7", "introduced_on": date(2024, 7, 1),
                    "deprecated_on": date(2026, 2, 1), "sunset_on": date(2026, 8, 1),
                    "removed_on": date(2027, 2, 1), "successor": None}

With `now = date(2026, 3, 10)` the state is TWILIGHT; a 200 gains:

    X-Solstice-Api-Version: m7
    X-Solstice-Deprecation: 2026-02-01
    X-Solstice-Grace-Until: 2027-01-02

(30 days before the removal). A 404 on the same date gains only the
TWILIGHT headers - no version header. With `now = date(2026, 9, 1)`
the state is ARCHIVE and the 200 gains `X-Solstice-Api-Version`,
`X-Solstice-Sunset: 2026-08-01`, `X-Solstice-Purge: 2027-02-01` and
`X-Solstice-Docs: https://media.solstice.example/versions/m7`.
"""

# ============================================================================
# Family 1: API version retirement headers - the Trellis cargo API group
# (shipapi). Shipped code guesses the IETF draft convention.
# ============================================================================

_TR_LIFECYCLE = r'''"""Version lifecycle for the shipapi API.

A version is open until its deprecation date, deprecated until its
sunset date, sunset until its removal date, and removed afterwards.
"""


def state_of(version_info, now):
    """The version's phase: open, deprecated, sunset or removed."""
    deprecated_on = version_info.get("deprecated_on")
    sunset_on = version_info.get("sunset_on")
    removed_on = version_info.get("removed_on")
    if removed_on is not None and now >= removed_on:
        return "removed"
    if sunset_on is not None and now >= sunset_on:
        return "sunset"
    if deprecated_on is not None and now >= deprecated_on:
        return "deprecated"
    return "open"
'''

_TR_HEADERS = r'''"""Response decoration for shipapi - API version lifecycle headers.

Every response carries the API level; a deprecated or sunset version
adds the standard Deprecation and Sunset headers with ISO dates plus a
help link; a removed version answers 410 Gone.
"""

from lifecycle import state_of


def decorate(response, version_info, now):
    """Return the response decorated for the version's phase."""
    decorated = dict(response)
    headers = dict(response.get("headers", {}))
    headers["X-Api-Level"] = version_info["version"]
    state = state_of(version_info, now)
    if state in ("deprecated", "sunset"):
        if version_info.get("deprecated_on") is not None:
            headers["Deprecation"] = version_info["deprecated_on"].isoformat()
        if version_info.get("sunset_on") is not None:
            headers["Sunset"] = version_info["sunset_on"].isoformat()
        headers["Link"] = (
            f'<https://docs.example.com/retirement/{version_info["version"]}>;'
            ' rel="help"'
        )
    if state == "removed":
        decorated["status"] = 410
        decorated["body"] = ""
    decorated["headers"] = headers
    return decorated
'''

_TR_TEST = r'''"""Contract tests for the response middleware of this service against the
INTERNAL version retirement rules of its API group. The rules are not
public: each group's expected behavior is pinned as a sha256 DIGEST over
the canonical JSON rendering of the decorated responses, so this file
cannot become a copy of the rules."""

import hashlib
import json
from datetime import date

import pytest

from headers import decorate


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(version, introduced, deprecated, sunset, removed, successor=None):
    return {
        "version": version,
        "introduced_on": introduced,
        "deprecated_on": deprecated,
        "sunset_on": sunset,
        "removed_on": removed,
        "successor": successor,
    }


def _resp(status, headers=None, body=""):
    return {"status": status, "headers": dict(headers or {}), "body": body}


EA = _entry(
    "r4", date(2024, 4, 1), date(2026, 2, 1), date(2026, 8, 1), date(2027, 2, 1), "r5"
)
EB = _entry(
    "r3", date(2023, 3, 15), date(2026, 5, 20), date(2026, 11, 1), date(2027, 5, 10)
)
EC = _entry(
    "r2", date(2022, 4, 1), date(2025, 6, 1), date(2025, 12, 1), date(2026, 5, 1), "r3"
)
ED = _entry("r5", date(2026, 1, 5), None, None, None)
EE = _entry("r3b", date(2023, 6, 1), date(2026, 1, 10), None, date(2027, 1, 10), "r4")
EF = _entry("r2b", date(2022, 5, 1), date(2025, 5, 1), date(2025, 11, 1), None)

R_OK = _resp(200, {"Content-Type": "application/json"}, '{"shipments": []}')
R_LIST = _resp(
    200,
    {"Content-Type": "application/json", "X-Request-Id": "req-3341"},
    '{"shipments": 3}',
)
R_CREATED = _resp(201, {"Location": "/shipments/sh-77"}, "")
R_MOVED = _resp(301, {"Location": "/r5/quotes"}, "")
R_MISSING = _resp(404, {"Content-Type": "text/plain"}, "no such shipment")
R_RATE = _resp(429, {"Retry-After": "30"}, "slow down")
R_BOOM = _resp(500, {"Content-Type": "text/plain"}, "internal error")

G1 = [
    (R_OK, ED, date(2026, 6, 1)),
    (R_LIST, EA, date(2025, 6, 15)),
    (R_MOVED, ED, date(2026, 2, 1)),
    (R_MISSING, EA, date(2025, 12, 1)),
    (R_BOOM, ED, date(2026, 3, 1)),
]
G2 = [
    (R_OK, EA, date(2026, 2, 1)),
    (R_LIST, EA, date(2026, 3, 10)),
    (R_CREATED, EB, date(2026, 6, 1)),
    (R_MOVED, EA, date(2026, 7, 4)),
    (R_MISSING, EA, date(2026, 4, 2)),
    (R_BOOM, EA, date(2026, 5, 1)),
]
G3 = [
    (R_OK, EA, date(2026, 8, 1)),
    (R_LIST, EA, date(2026, 9, 20)),
    (R_MOVED, EC, date(2026, 1, 1)),
    (R_MISSING, EC, date(2026, 2, 1)),
    (R_OK, EE, date(2026, 12, 1)),
    (R_OK, EF, date(2026, 6, 1)),
]
G4 = [
    (R_OK, EC, date(2026, 5, 1)),
    (R_LIST, EC, date(2026, 6, 1)),
    (R_MOVED, EB, date(2027, 6, 1)),
    (R_MISSING, EC, date(2026, 8, 1)),
    (R_BOOM, EC, date(2026, 9, 1)),
]

GROUPS = {"g1": G1, "g2": G2, "g3": G3, "g4": G4}

DIGESTS = {
    "g1": "a523653c73512afad5f6a8114e9905ce3f024626f82e263be665590ea5ddfd54",
    "g2": "b541ebf814caa2809658c3873e6ad6f60728fae09dcbe41d2a8b6bf099061a35",
    "g3": "c42b255fe76dc434044edfe5c1f2056bacafd70e99e74f1ee953cdf5ed88a83f",
    "g4": "aa75681b9af4796d8cfd24037f38225e749980a203fd4a1ac97f43df77a379e3",
}


def _lines(cases):
    out = []
    for response, entry, now in cases:
        decorated = decorate(response, entry, now)
        out.append(json.dumps(decorated, sort_keys=True))
    return out


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = _lines(GROUPS[group])
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''

_TR_SKILL = """---
name: trellis-retirement-headers
description: API version retirement headers standard for Trellis cargo services (shipapi, routes).
version: 1.0.0
---

# Trellis Retirement Headers (internal)

How every response from a Trellis cargo API service (shipapi,
routes) is decorated while its API version is being retired. Internal
to the Trellis cargo API group - not published anywhere.

`decorate(response, version_info, now)` returns the decorated response.
`response` is `{"status": <int>, "headers": {name: value}, "body": <str>}`.
`version_info` is the registry entry: `version`, `introduced_on`,
`deprecated_on`, `sunset_on`, `removed_on` (dates or None) and
`successor` (the next version id, or None). `now` is the request date.

## States

Boundaries are INCLUSIVE:

- `deprecated_on` unset, or `now < deprecated_on` -> OPEN.
- else `sunset_on` unset, or `now < sunset_on` -> CLOSING.
- else `removed_on` unset, or `now < removed_on` -> SEALED.
- else -> WITHDRAWN.

## Decoration

- EVERY response gains `X-Trellis-Api-Level: <version>`.
- The retirement headers go on every response EXCEPT a 5xx
  (500 <= status): a server error carries nothing but the version
  header.
- CLOSING adds `X-Trellis-Deprecation: <deprecated_on>`,
  `X-Trellis-Successor: <successor>` (only when set) and
  `Link: <https://ship.trellis.example/docs/<version>/retirement>; rel="help"`.
- SEALED adds `X-Trellis-Sunset: <sunset_on>`, the same
  `X-Trellis-Successor` and the same `Link`. SEALED does NOT repeat
  the deprecation header.
- WITHDRAWN replaces the response entirely: status 410, empty body,
  headers exactly `X-Trellis-Api-Level`,
  `X-Trellis-Removed: <removed_on>` and `X-Trellis-Successor`
  (only when set).

Every date in a header value is `DD.MM.YYYY` zero-padded, e.g.
01.02.2026.

## Worked example

    version_info = {"version": "r4", "introduced_on": date(2024, 4, 1),
                    "deprecated_on": date(2026, 2, 1), "sunset_on": date(2026, 8, 1),
                    "removed_on": date(2027, 2, 1), "successor": "r5"}

With `now = date(2026, 3, 10)` the state is CLOSING; a 200 gains:

    X-Trellis-Api-Level: r4
    X-Trellis-Deprecation: 01.02.2026
    X-Trellis-Successor: r5
    Link: <https://ship.trellis.example/docs/r4/retirement>; rel="help"

A 500 on the same date gains only `X-Trellis-Api-Level`.
"""

# ============================================================================
# Family 2: Log field redaction - the Kestrel support platform
# (tickets). Shipped code guesses the common masking convention.
# ============================================================================

_KE_FIELDS = r'''"""Field classification for the tickets log filter.

Which record fields carry personal or secret data, and how each kind is
treated before it reaches a service log line.
"""


def classify(field):
    """Return the redaction kind: 'secret', 'personal' or 'plain'."""
    if field in ("token", "password", "api_key"):
        return "secret"
    if field in ("email", "phone", "card", "ip"):
        return "personal"
    return "plain"
'''

_KE_RENDER = r'''"""Log line rendering for tickets - personal data is masked,
secrets are redacted, everything else passes through.

Fields are rendered in record order, one ``key=value`` pair per field,
joined by a single space.
"""

from fields import classify


def render_line(record):
    """Render one log line from a record."""
    parts = []
    for key, value in record.items():
        rendered = _redact(key, str(value))
        if rendered is not None:
            parts.append(f"{key}={rendered}")
    return " ".join(parts)


def _redact(key, value):
    kind = classify(key)
    if kind == "secret":
        return "[REDACTED]"
    if kind == "personal":
        if key == "email":
            local, _, domain = value.partition("@")
            return f"{local[:1]}***@{domain}"
        if key == "phone":
            return f"***-***-{value[-4:]}"
        if key == "card":
            return f"{value[:6]}****{value[-4:]}"
        return value
    return value
'''

_KE_TEST = r'''"""Contract tests for the log line renderer of this service against the
INTERNAL log field rules of its platform. The rules are not public: each
group's expected behavior is pinned as a sha256 DIGEST over the rendered
log lines, so this file cannot become a copy of the rules."""

import hashlib

import pytest

from render import render_line


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(records):
    return [render_line(record) for record in records]


G1 = [
    {
        "event": "login",
        "email": "dana.kowalski@example.com",
        "phone": "+14155550134",
        "ticket_id": "tkt-4471",
        "status": "open",
    },
    {
        "event": "checkout",
        "card": "4242424242424242",
        "email": "mi.chen@example.org",
        "ip": "203.0.113.7",
        "note": "paid by phone",
    },
    {
        "event": "call",
        "phone": "555",
        "password": "hunter2!",
        "api_key": "ak-9931",
        "zip": "94107",
    },
    {
        "event": "page_view",
        "ip": "198.51.100.22",
        "token": "tok_9f8e7d6c",
        "email": "a@b.co",
    },
]
G2 = [
    {"email": "jo@example.com", "event": "signup"},
    {"email": "x@example.io", "phone": "41555", "event": "retry"},
    {"card": "411", "event": "declined", "ip": "127.0.0.1"},
    {"phone": "55", "email": "ab", "event": "short"},
]
G3 = [
    {
        "status": "closed",
        "event": "refund",
        "card": "5555555555554444",
        "email": "ops@internal.example.com",
        "ip": "10.0.0.5",
    },
    {
        "token": "tok_deadbeef42",
        "password": "x",
        "event": "token_rotate",
        "phone": "+442079460958",
    },
    {
        "api_key": "key-773",
        "event": "hook",
        "card": "374245815262437",
        "note": "see ticket 4471",
    },
]
G4 = [
    {
        "event": "geo_ping",
        "zip": "94",
        "phone": "41555",
        "email": "a.b@example.com",
        "ip": "192.0.2.99",
    },
    {
        "event": "escalate",
        "email": "first.last@sub.example.co.uk",
        "card": "6011111111111117",
        "token": "t-1",
        "ip": "198.51.100.254",
        "ticket_id": "tkt-1",
    },
    {
        "password": "s3cr3t-aa17",
        "event": "rotate",
        "phone": "+14155550134",
        "api_key": "ak-0000",
    },
]

GROUPS = {"g1": G1, "g2": G2, "g3": G3, "g4": G4}

DIGESTS = {
    "g1": "897e8b660ac9e5f0fab50a7ca136059fc3dc09a30ffff42b94ee3c8c3bc8a662",
    "g2": "e57ae0fecf212771f603e9fe01867200b4c91e1ce72c3ee64333980b127199ba",
    "g3": "7b872efa0f2d672c2ec13b604ea60d5370de73f9114e2d1cc4352ce7537731a9",
    "g4": "ce43b2099e3c58e6d9f6c094b35323620ca1042b145f92516610b99cdcb2b297",
}


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = _lines(GROUPS[group])
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''

_KE_SKILL = """---
name: kestrel-log-redaction
description: Log field redaction standard for Kestrel support services (tickets, helpdesk).
version: 1.0.0
---

# Kestrel Log Redaction (internal)

How a service log line is rendered from a record before it is written
by a Kestrel support platform service (tickets, helpdesk). Internal
to the Kestrel support platform team - not published anywhere.

`render_line(record)` renders ONE line from a record: a mapping of
field name to string value.

## Field classes

| Field    | Class     |
|----------|-----------|
| email    | CONTACT   |
| phone    | CONTACT   |
| card     | PAN       |
| token    | CREDENTIAL |
| password | CREDENTIAL |
| api_key  | CREDENTIAL |
| ip       | TRACE     |

Any other field name is class CLEAR.

## Modes

- CREDENTIAL - DROP: neither key nor value is rendered.
- CONTACT email - MASK: the first 2 characters of the local part, then
  the ellipsis character `\u2026`, then `@` and the full domain. A
  value without `@` renders as its first 2 characters plus `\u2026`.
- CONTACT phone - MASK: one `*` per character except the last 4,
  which stay visible (a value shorter than 4 becomes all `*`).
- PAN - MASK: `card:` then one `X` per hidden character, the last 4
  visible (shorter than 4 -> all `X`).
- TRACE - HASH: `ip#` then the first 10 hex characters of the sha256
  digest of the value (utf-8).
- CLEAR - KEEP: the value verbatim.

## Rendering

The kept fields are rendered in sorted key order, each as `key=value`,
joined by a single space.

## Worked example

    {"event": "login", "email": "dana.kowalski@example.com",
     "phone": "+14155550134", "token": "tok-1", "zip": "94107"}

renders (sorted keys: email, event, phone, zip):

    email=da\u2026@example.com event=login phone=********0134 zip=94107
"""

# ============================================================================
# Family 2: Log field redaction - the Obsidian billing platform
# (invoices). Shipped code guesses the common masking convention.
# ============================================================================

_OB_FIELDS = r'''"""Field classification for the invoices log filter.

Which record fields carry personal or payment data, and how each kind is
treated before it reaches a service log line.
"""


def classify(field):
    """Return the redaction kind: 'secret', 'personal' or 'plain'."""
    if field in ("password", "token"):
        return "secret"
    if field in ("email", "card", "iban"):
        return "personal"
    return "plain"
'''

_OB_RENDER = r'''"""Log line rendering for invoices - personal data is masked,
secrets are redacted, everything else passes through.

Fields are rendered in record order, one ``key=value`` pair per field,
joined by ``" | "``.
"""

from fields import classify


def render_line(record):
    """Render one log line from a record."""
    parts = []
    for key, value in record.items():
        rendered = _redact(key, str(value))
        if rendered is not None:
            parts.append(f"{key}={rendered}")
    return " | ".join(parts)


def _redact(key, value):
    kind = classify(key)
    if kind == "secret":
        return "[REDACTED]"
    if kind == "personal":
        if key == "email":
            local, _, domain = value.partition("@")
            return f"{local[:1]}***@{domain}"
        return f"****{value[-4:]}"
    return value
'''

_OB_TEST = r'''"""Contract tests for the log line renderer of this service against the
INTERNAL log field rules of its platform. The rules are not public: each
group's expected behavior is pinned as a sha256 DIGEST over the rendered
log lines, so this file cannot become a copy of the rules."""

import hashlib

import pytest

from render import render_line


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(records):
    return [render_line(record) for record in records]


G1 = [
    {
        "event": "render",
        "email": "billing@example.com",
        "card": "4242424242424242",
        "amount": "1250.00",
        "currency": "USD",
    },
    {
        "event": "dunning",
        "iban": "DE89370400440532013000",
        "token": "tok-7731",
        "note": "second notice",
    },
    {
        "event": "pay",
        "email": "a.varga@example.org",
        "amount": "49.99",
        "password": "pw-2026",
    },
    {
        "event": "void",
        "card": "374245815262437",
        "currency": "EUR",
        "invoice_id": "inv-913",
    },
]
G2 = [
    {"email": "o@example.io", "event": "signup"},
    {"email": "ops@internal.example.com", "iban": "GB29NWBK60161331926819", "event": "audit"},
    {"card": "411", "event": "declined", "amount": "0.50"},
    {"email": "ab", "event": "short"},
]
G3 = [
    {
        "currency": "CHF",
        "event": "refund",
        "card": "5555555555554444",
        "email": "ops@internal.example.com",
        "amount": "10.00",
    },
    {"token": "tok_deadbeef42", "password": "x", "event": "rotate", "amount": "1.00"},
    {
        "event": "hook",
        "iban": "FR1420041010050000013M02606",
        "note": "see ticket 4471",
        "amount": "2.00",
    },
]
G4 = [
    {"event": "geo", "zip": "94", "email": "a.b@example.com", "amount": "99.00"},
    {
        "event": "escalate",
        "email": "first.last@sub.example.co.uk",
        "card": "6011111111111117",
        "token": "t-1",
        "invoice_id": "inv-77",
    },
    {
        "password": "s3cr3t-aa17",
        "event": "rotate",
        "iban": "DE44500105175401324231",
        "amount": "3.50",
    },
]

GROUPS = {"g1": G1, "g2": G2, "g3": G3, "g4": G4}

DIGESTS = {
    "g1": "f0701b99b235cbdcf9214537040cb2dacbf2da498e42fd6ed8694c618883100e",
    "g2": "6dd74555272afbd190b81258e40d8501e7faec0967fa19f64b703a5175679519",
    "g3": "5884533ddea6d7118fe6c26c68f62323cd8b3dc4cf4147ad8307569172270565",
    "g4": "9b674485d06b9d51e80db7b37e50143eb080ac2751d8c9cc854943c2810f58e5",
}


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = _lines(GROUPS[group])
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''

_OB_SKILL = """---
name: obsidian-log-redaction
description: Log field redaction standard for Obsidian billing services (invoices, dunning).
version: 1.0.0
---

# Obsidian Log Redaction (internal)

How a service log line is rendered from a record before it is written
by an Obsidian billing platform service (invoices, dunning). Internal
to the Obsidian billing platform team - not published anywhere.

`render_line(record)` renders ONE line from a record: a mapping of
field name to string value.

## Field classes

| Field    | Class |
|----------|-------|
| email    | ADDR  |
| card     | FUNDS |
| iban     | FUNDS |
| password | LOCK  |
| token    | LOCK  |
| amount   | TALLY |
| currency | TALLY |

Any other field name is class OPEN.

## Modes

- LOCK - DROP: neither key nor value is rendered.
- ADDR email - MASK: the first character of the local part, then three
  dots `...`, then `@` and the full domain. A value without `@`
  renders as its first character plus `...`.
- FUNDS - HASH: `pay:` then the first 16 hex characters of the sha256
  digest of the value (utf-8). Nothing of a card or iban is ever
  visible.
- TALLY - KEEP: money fields are safe as given.
- OPEN - KEEP: the value verbatim.

## Rendering

The kept fields are rendered in RECORD order (the order they appear in
the record), each as `key=value`, joined by `" | "`.

## Worked example

    {"event": "render", "email": "billing@example.com",
     "card": "4242424242424242", "amount": "1250.00"}

renders (record order):

    event=render | email=b...@example.com | card=pay:477bba133c182267 | amount=1250.00
"""

# ============================================================================
# Family 2: Log field redaction - the Mosaic analytics platform
# (events). Shipped code guesses the common masking convention.
# ============================================================================

_MO_FIELDS = r'''"""Field classification for the events log filter.

Which record fields carry personal or secret data, and how each kind is
treated before it reaches a service log line.
"""


def classify(field):
    """Return the redaction kind: 'secret', 'personal' or 'plain'."""
    if field in ("token", "password"):
        return "secret"
    if field in ("user_id", "email", "url"):
        return "personal"
    return "plain"
'''

_MO_RENDER = r'''"""Log line rendering for events - personal data is masked,
secrets are redacted, everything else passes through.

Fields are rendered in sorted key order, one ``key=value`` pair per
field, joined by a single space.
"""

from fields import classify


def render_line(record):
    """Render one log line from a record."""
    parts = []
    for key in sorted(record):
        rendered = _redact(key, str(record[key]))
        if rendered is not None:
            parts.append(f"{key}={rendered}")
    return " ".join(parts)


def _redact(key, value):
    kind = classify(key)
    if kind == "secret":
        return "[REDACTED]"
    if kind == "personal":
        if key == "email":
            local, _, domain = value.partition("@")
            return f"{local[:1]}***@{domain}"
        if key == "user_id":
            return f"{value[:2]}**"
        return value
    return value
'''

_MO_TEST = r'''"""Contract tests for the log line renderer of this service against the
INTERNAL log field rules of its platform. The rules are not public: each
group's expected behavior is pinned as a sha256 DIGEST over the rendered
log lines, so this file cannot become a copy of the rules."""

import hashlib

import pytest

from render import render_line


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(records):
    return [render_line(record) for record in records]


G1 = [
    {
        "event": "track",
        "user_id": "u-8841",
        "email": "dana.kowalski@example.com",
        "url": "https://insight.example.com/report/q?range=7d",
        "page": "report",
    },
    {
        "event": "identify",
        "user_id": "u-12",
        "token": "tok_9f8e7d6c",
        "url": "https://cdn.example.net/assets/logo.svg",
        "ts": "2026-06-01T00:00:00Z",
    },
    {
        "event": "click",
        "email": "mi.chen@example.org",
        "url": "http://tracker.example.io/pixel?id=9",
        "button": "signup",
    },
    {
        "event": "view",
        "user_id": "guest-773",
        "password": "hunter2!",
        "path": "/home",
    },
]
G2 = [
    {"user_id": "u-1", "event": "short"},
    {"email": "ab@cd.io", "event": "edge"},
    {"url": "ftp://files.example.com/x/y.zip", "event": "fetch"},
    {"url": "not-a-url", "event": "bad"},
]
G3 = [
    {
        "page": "checkout",
        "user_id": "u-9931",
        "url": "https://shop.example.com/cart",
        "email": "ops@internal.example.com",
        "event": "funnel",
    },
    {
        "token": "tok_deadbeef42",
        "password": "x",
        "event": "rotate",
        "user_id": "u-42",
        "url": "https://api.example.com/v2/session",
    },
    {
        "event": "hook",
        "url": "https://hooks.example.org/cb/9f31?sig=abc",
        "email": "a@b.co",
        "status": "200",
    },
]
G4 = [
    {
        "event": "geo",
        "user_id": "u-100",
        "url": "https://example.com/a/b/c?x=1&y=2",
        "email": "first.last@sub.example.co.uk",
        "ip": "192.0.2.99",
    },
    {
        "event": "escalate",
        "email": "a.b@example.com",
        "user_id": "u-7",
        "url": "https://docs.example.dev/guide",
        "note": "see ticket 4471",
    },
    {
        "password": "s3cr3t-aa17",
        "event": "rotate",
        "user_id": "u-9",
        "url": "https://example.com",
    },
]

GROUPS = {"g1": G1, "g2": G2, "g3": G3, "g4": G4}

DIGESTS = {
    "g1": "e2dc4ef0b16733eb9a6f58e62b4f97777dd8f2902ae1fe5d696f604c5901e918",
    "g2": "88175393c639cc104fbcaa733299b5c797f80d94b3db18f886b1cc18c7810250",
    "g3": "59038a1e1ac3bc3cac3c00ed0e8bc75938d07f87e29eaa33b65e5d2536b28baf",
    "g4": "ea1c7f3715db914feb4e5af5e043790bc90ce253bf2013992de0d66c41f77138",
}


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = _lines(GROUPS[group])
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''

_MO_SKILL = """---
name: mosaic-log-redaction
description: Log field redaction standard for Mosaic analytics services (events, insights).
version: 1.0.0
---

# Mosaic Log Redaction (internal)

How a service log line is rendered from a record before it is written
by a Mosaic analytics platform service (events, insights). Internal to
the Mosaic analytics platform team - not published anywhere.

`render_line(record)` renders ONE line from a record: a mapping of
field name to string value.

## Field classes

| Field    | Class  |
|----------|--------|
| user_id  | SUBJECT |
| email    | SUBJECT |
| token    | SECRET |
| password | SECRET |
| url      | ROUTE  |

Any other field name is class BARE.

## Modes

- SECRET - DROP: neither key nor value is rendered.
- SUBJECT - MASK: the first 2 characters, then `~`, then the last
  character. A value shorter than 4 characters is DROPped (too short
  to mask).
- ROUTE - COARSEN: the scheme and host survive, the path and query do
  not: `<scheme>://<host>/\u2026`. A value without `://` is DROPped (an
  unparseable origin is never logged).
- BARE - KEEP: the value verbatim.

## Rendering

The kept fields are rendered grouped by class in the fixed order
SUBJECT, ROUTE, BARE (SECRET is gone); inside a group by sorted key;
each as `key=value`, joined by a TAB character (`\\t`).

## Worked example

    {"event": "track", "user_id": "u-8841",
     "email": "dana.kowalski@example.com",
     "url": "https://insight.example.com/report/q?range=7d"}

renders (SUBJECT first, email before user_id; then ROUTE; then BARE):

    email=da~m\\tuser_id=u-~1\\turl=https://insight.example.com/\u2026\\tevent=track

(`\\t` = one TAB character.)
"""

# ============================================================================
# Family 2: Log field redaction - the Capstan infrastructure observability
# services (deploys). Shipped code guesses the common masking
# convention.
# ============================================================================

_CA_FIELDS = r'''"""Field classification for the deploys log filter.

Which record fields carry personal or secret data, and how each kind is
treated before it reaches a service log line.
"""


def classify(field):
    """Return the redaction kind: 'secret', 'personal' or 'plain'."""
    if field in ("token", "password", "private_key"):
        return "secret"
    if field in ("email", "host"):
        return "personal"
    return "plain"
'''

_CA_RENDER = r'''"""Log line rendering for deploys - personal data is masked,
secrets are redacted, everything else passes through.

Fields are rendered in sorted key order, one ``key=value`` pair per
field, joined by a semicolon.
"""

from fields import classify


def render_line(record):
    """Render one log line from a record."""
    parts = []
    for key in sorted(record):
        rendered = _redact(key, str(record[key]))
        if rendered is not None:
            parts.append(f"{key}={rendered}")
    return ";".join(parts)


def _redact(key, value):
    kind = classify(key)
    if kind == "secret":
        return "[REDACTED]"
    if kind == "personal":
        if key == "email":
            local, _, domain = value.partition("@")
            return f"{local[:1]}***@{domain}"
        return f"{value[:2]}***"
    return value
'''

_CA_TEST = r'''"""Contract tests for the log line renderer of this service against the
INTERNAL log field rules of its platform. The rules are not public: each
group's expected behavior is pinned as a sha256 DIGEST over the rendered
log lines, so this file cannot become a copy of the rules."""

import hashlib

import pytest

from render import render_line


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(records):
    return [render_line(record) for record in records]


G1 = [
    {
        "event": "deploy",
        "repo": "cart",
        "commit": "9f31ab2",
        "host": "hooks-01.internal",
        "status": "ok",
    },
    {
        "event": "rollback",
        "repo": "pricing",
        "token": "tok-7731",
        "host": "hooks-02.internal",
        "duration": "12s",
    },
    {
        "event": "hook",
        "request_id": "req-3341",
        "email": "ops@internal.example.com",
        "commit": "c0ffee3",
        "status": "200",
    },
    {
        "event": "audit",
        "repo": "ledger-sync",
        "password": "hunter2!",
        "host": "audit.internal",
        "note": "see ticket 4471",
    },
]
G2 = [
    {"event": "short", "email": "a@b.co", "host": "h.internal"},
    {"email": "ab", "event": "edge", "host": "hooks-03"},
    {
        "event": "rotate",
        "private_key": "-----BEGIN KEY-----",
        "host": "x",
        "repo": "cart",
    },
    {"event": "fail", "host": "q", "commit": "ab12", "status": "500"},
]
G3 = [
    {
        "status": "ok",
        "event": "deploy",
        "repo": "shipapi",
        "commit": "7731aa2",
        "host": "hooks-01.internal",
        "duration": "9s",
    },
    {
        "token": "tok_deadbeef42",
        "password": "x",
        "event": "rotate",
        "host": "hooks-02.internal",
        "repo": "pricing",
    },
    {
        "event": "hook",
        "repo": "cart",
        "commit": "5150c0d",
        "request_id": "req-9f31",
        "email": "dev@internal.example.com",
    },
]
G4 = [
    {
        "event": "geo",
        "user_id": "u-100",
        "repo": "clip",
        "host": "hooks-04.internal",
        "ip": "192.0.2.99",
    },
    {
        "event": "escalate",
        "email": "a.b@example.com",
        "repo": "transcode",
        "commit": "0fface",
        "status": "429",
    },
    {
        "password": "s3cr3t-aa17",
        "event": "rotate",
        "host": "audit.internal",
        "repo": "shipapi",
        "note": "see rung 4",
    },
]

GROUPS = {"g1": G1, "g2": G2, "g3": G3, "g4": G4}

DIGESTS = {
    "g1": "ddf02f6f80b1dc0fab7ff0d85a4ded9335a050bc76417856aab0f771a7a767b1",
    "g2": "6af79967a5d3dd26bb42eb8669bdc78abdc0ed8cb23c7f531871e929b7b52da0",
    "g3": "018c96485cbe3856254778e4fb686fad5c76decea35ac13065db612ebdcfae62",
    "g4": "db83c7f80717ce62e42e2aee38c48c8126aa6360656a1d08b9cdbdb56fc83513",
}


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = _lines(GROUPS[group])
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''

_CA_SKILL = """---
name: capstan-log-redaction
description: Log field redaction standard for Capstan infra services (deploys, audit).
version: 1.0.0
---

# Capstan Log Redaction (internal)

How a service log line is rendered from a record before it is written
by a Capstan infra observability service (deploys, audit). Internal to
the Capstan infra observability team - not published anywhere.

`render_line(record)` renders ONE line from a record: a mapping of
field name to string value.

## Field classes

| Field       | Class  |
|-------------|--------|
| token       | PASSKEY |
| password    | PASSKEY |
| private_key | PASSKEY |
| email       | IDBOX  |
| host        | IDBOX  |
| repo        | REF    |
| commit      | REF    |
| event       | PLAIN  |
| request_id  | PLAIN  |
| status      | PLAIN  |
| duration    | PLAIN  |

ANY OTHER field name is class UNKNOWN.

## Modes

- PASSKEY - DROP: neither key nor value is rendered.
- UNKNOWN - DROP: fail closed - a field this standard does not know
  never reaches a Capstan line.
- IDBOX - MASK: the first character, then the block character
  `\u2593`, then the last character. A value shorter than 3 characters is
  DROPped.
- REF - KEEP: the value verbatim.
- PLAIN - KEEP: the value verbatim.

## Rendering

The kept fields are rendered in sorted key order, each as `key=value`,
joined by `;` (no space).

## Worked example

    {"event": "deploy", "repo": "cart", "commit": "9f31ab2",
     "host": "hooks-01.internal", "token": "tok-1", "zip": "94107"}

renders (sorted keys; token PASSKEY-dropped, zip UNKNOWN-dropped):

    commit=9f31ab2;event=deploy;host=h\u2593l;repo=cart
"""

# ============================================================================
# Family 2: Log field redaction - the Parallax HR platform (payroll).
# Shipped code guesses the common masking convention.
# ============================================================================

_PA_FIELDS = r'''"""Field classification for the payroll log filter.

Which record fields carry personal or sensitive data, and how each kind
is treated before it reaches a service log line.
"""


def classify(field):
    """Return the redaction kind: 'secret', 'personal' or 'plain'."""
    if field in ("ssn", "salary"):
        return "secret"
    if field in ("name", "email", "address"):
        return "personal"
    return "plain"
'''

_PA_RENDER = r'''"""Log line rendering for payroll - personal data is masked,
secrets are redacted, everything else passes through.

Fields are rendered in record order, one ``key=value`` pair per field,
joined by a single space.
"""

from fields import classify


def render_line(record):
    """Render one log line from a record."""
    parts = []
    for key, value in record.items():
        rendered = _redact(key, str(value))
        if rendered is not None:
            parts.append(f"{key}={rendered}")
    return " ".join(parts)


def _redact(key, value):
    kind = classify(key)
    if kind == "secret":
        return "[REDACTED]"
    if kind == "personal":
        if key == "email":
            local, _, domain = value.partition("@")
            return f"{local[:1]}***@{domain}"
        if key == "name":
            return f"{value[:1]}***"
        return f"{value[:6]}****"
    return value
'''

_PA_TEST = r'''"""Contract tests for the log line renderer of this service against the
INTERNAL log field rules of its platform. The rules are not public: each
group's expected behavior is pinned as a sha256 DIGEST over the rendered
log lines, so this file cannot become a copy of the rules."""

import hashlib

import pytest

from render import render_line


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(records):
    return [render_line(record) for record in records]


G1 = [
    {
        "event": "hire",
        "name": "Dana Kowalski",
        "email": "dana.kowalski@example.com",
        "dept": "support",
        "manager": "R. Varga",
    },
    {
        "event": "raise",
        "ssn": "900-12-3456",
        "salary": "92500",
        "name": "Mi Chen",
        "country": "US",
    },
    {
        "event": "move",
        "email": "a.varga@example.org",
        "address": "12 Rue Centrale, Lyon",
        "dept": "billing",
        "manager": "P. Ito",
    },
    {
        "event": "exit",
        "name": "Jo Reyes",
        "ssn": "900-77-0001",
        "salary": "101000",
        "country": "US",
    },
]
G2 = [
    {"name": "Al", "event": "short"},
    {"email": "ab@cd.io", "event": "edge", "dept": "ops"},
    {"name": "Bo", "salary": "10", "event": "tiny", "manager": "C. Dee"},
    {"address": "flat 9", "event": "move2", "country": "GB"},
]
G3 = [
    {
        "dept": "support",
        "event": "review",
        "name": "Ravi Patel",
        "email": "r.patel@example.com",
        "manager": "S. Roy",
    },
    {
        "ssn": "900-55-4321",
        "salary": "77000",
        "event": "audit",
        "name": "Ana Cruz",
        "country": "US",
    },
    {
        "event": "hook",
        "manager": "T. Ng",
        "email": "ops@internal.example.com",
        "address": "9 Elm Street",
        "dept": "hr",
    },
]
G4 = [
    {
        "event": "geo",
        "name": "Kim Novak",
        "dept": "payroll",
        "ssn": "900-99-1234",
        "ip": "192.0.2.99",
    },
    {
        "event": "escalate",
        "email": "a.b@example.com",
        "name": "Lee Marsh",
        "salary": "88000",
        "manager": "H. Park",
    },
    {
        "event": "rotate",
        "name": "Ivy Lang",
        "ssn": "900-00-7788",
        "country": "US",
        "ext": "2140",
    },
]

GROUPS = {"g1": G1, "g2": G2, "g3": G3, "g4": G4}

DIGESTS = {
    "g1": "aa505e26f0f58a09d555e202f1f9598789ea6943c073b020b96a4cd6af6bfc23",
    "g2": "bd7b89cdbca087cea6b245a86faf511ee319d3d09d67a4ba7d133d9aa90c3f25",
    "g3": "1dd09579f997771d0c28f6780c1928631e37350b3f406382fdb9e4dc3b9886dd",
    "g4": "f7e9c29f94a4717871bd9f77511b9c8aafef0ef6bdd21cc4b1ba060cddfe4461",
}


@pytest.mark.parametrize("group", ["g1", "g2", "g3", "g4"])
def test_group(group):
    lines = _lines(GROUPS[group])
    assert _digest("\n".join(lines)) == DIGESTS[group], lines
'''

_PA_SKILL = """---
name: parallax-log-redaction
description: Log field redaction standard for Parallax HR services (payroll, onboard).
version: 1.0.0
---

# Parallax Log Redaction (internal)

How a service log line is rendered from a record before it is written
by a Parallax HR platform service (payroll, onboard). Internal to the
Parallax HR platform team - not published anywhere.

`render_line(record)` renders ONE line from a record: a mapping of
field name to string value.

## Field classes

| Field   | Class     |
|---------|-----------|
| ssn     | RESTRICTED |
| salary  | RESTRICTED |
| name    | PERSON |
| email   | PERSON |
| address | PERSON |
| manager | META |
| dept    | META |
| country | META |

Any other field name is class PUBLIC.

## Modes

- RESTRICTED - DROP: neither key nor value is rendered.
- PERSON - MASK: the first 2 characters, then one `\u00d7` (the
  multiplication sign) per middle character, then the last 2
  characters. A value shorter than 6 characters is DROPped (too
  short to mask).
- META - KEEP: the value verbatim.
- PUBLIC - KEEP: the value verbatim.

## Rendering

The kept fields are rendered in RECORD order (the order they appear in
the record), each as `key=value`, joined by a single space. When ANY
field was dropped (a RESTRICTED field, or a too-short PERSON value),
the line ends with the drop marker: one space, then `\u00abredacted\u00bb`
(in guillemets).

## Worked example

    {"event": "hire", "name": "Ana Cruz", "ssn": "900-12-3456",
     "manager": "S. Roy"}

renders (record order; ssn dropped, so the line is marked):

    event=hire name=An\u00d7\u00d7\u00d7\u00d7uz manager=S. Roy \u00abredacted\u00bb
"""

# ============================================================================
# The ten fixtures.
# ============================================================================

TASKS: list[dict[str, Any]] = [
    _private_task(
        "private3-bramble-retire",
        "api-version-retirement-headers",
        {
            "lifecycle.py": _BR_LIFECYCLE,
            "headers.py": _BR_HEADERS,
            "test_retire_headers.py": _BR_TEST,
        },
        "The tests in test_retire_headers.py fail. This cart response middleware "
        "doesn't follow how we decorate responses for API versions that are on their way "
        "out here: which headers a response must carry while a version is being "
        "retired, and on which responses, is decided by an internal policy of our API "
        "group, not by guesswork. Find the root cause before proposing any fix, then "
        "fix it and make the whole test suite green.",
        "bramble-retirement-headers",
        _BR_SKILL,
        [
            "X-Bramble-Api-Version",
            "X-Bramble-Deprecation",
            "X-Bramble-Sunset",
            "X-Bramble-Removal",
            "WINDING",
            "COOLING",
            "retire.bramble.example",
        ],
    ),
    _private_task(
        "private3-thistle-retire",
        "api-version-retirement-headers",
        {
            "lifecycle.py": _TH_LIFECYCLE,
            "headers.py": _TH_HEADERS,
            "test_retire_headers.py": _TH_TEST,
        },
        "The tests in test_retire_headers.py fail. This auth response middleware "
        "doesn't follow how we decorate responses for API versions that are on their "
        "way out here: which headers a response must carry while a version is being "
        "retired, and on which responses, is decided by an internal policy of our API "
        "group, not by guesswork. Find the root cause before proposing any fix, then "
        "fix it and make the whole test suite green.",
        "thistle-retirement-headers",
        _TH_SKILL,
        [
            "X-Thistle-Version",
            "X-Thistle-Deprecating",
            "X-Thistle-Migration",
            "X-Thistle-Removed-After",
            "identity.thistle.example",
        ],
    ),
    _private_task(
        "private3-vernier-retire",
        "api-version-retirement-headers",
        {
            "lifecycle.py": _VE_LIFECYCLE,
            "headers.py": _VE_HEADERS,
            "test_retire_headers.py": _VE_TEST,
        },
        "The tests in test_retire_headers.py fail. This fanout response "
        "middleware doesn't follow how we decorate responses for API versions that are "
        "on their way out here: which headers a response must carry while a version is "
        "being retired, and on which responses, is decided by an internal policy of our "
        "API group, not by guesswork. Find the root cause before proposing any fix, "
        "then fix it and make the whole test suite green.",
        "vernier-retirement-headers",
        _VE_SKILL,
        [
            "X-Vernier-Api",
            "X-Vernier-Deprecation-Unix",
            "X-Vernier-Sunset-Unix",
            "X-Vernier-Removal-Unix",
            "notice floor",
            "docs.vernier.example",
        ],
    ),
    _private_task(
        "private3-solstice-retire",
        "api-version-retirement-headers",
        {
            "lifecycle.py": _SO_LIFECYCLE,
            "headers.py": _SO_HEADERS,
            "test_retire_headers.py": _SO_TEST,
        },
        "The tests in test_retire_headers.py fail. This clip response middleware "
        "doesn't follow how we decorate responses for API versions that are on their "
        "way out here: which headers a response must carry while a version is being "
        "retired, and on which responses, is decided by an internal policy of our API "
        "group, not by guesswork. Find the root cause before proposing any fix, then "
        "fix it and make the whole test suite green.",
        "solstice-retirement-headers",
        _SO_SKILL,
        [
            "X-Solstice-Api-Version",
            "X-Solstice-Deprecation",
            "X-Solstice-Grace-Until",
            "X-Solstice-Purge",
            "X-Solstice-Docs",
            "TWILIGHT",
            "media.solstice.example",
        ],
    ),
    _private_task(
        "private3-trellis-retire",
        "api-version-retirement-headers",
        {
            "lifecycle.py": _TR_LIFECYCLE,
            "headers.py": _TR_HEADERS,
            "test_retire_headers.py": _TR_TEST,
        },
        "The tests in test_retire_headers.py fail. This shipapi response "
        "middleware doesn't follow how we decorate responses for API versions that are "
        "on their way out here: which headers a response must carry while a version is "
        "being retired, and on which responses, is decided by an internal policy of our "
        "API group, not by guesswork. Find the root cause before proposing any fix, "
        "then fix it and make the whole test suite green.",
        "trellis-retirement-headers",
        _TR_SKILL,
        [
            "X-Trellis-Api-Level",
            "X-Trellis-Deprecation",
            "X-Trellis-Successor",
            "X-Trellis-Sunset",
            "X-Trellis-Removed",
            "ship.trellis.example",
        ],
    ),
    _private_task(
        "private3-kestrel-redact",
        "log-field-redaction",
        {
            "fields.py": _KE_FIELDS,
            "render.py": _KE_RENDER,
            "test_log_lines.py": _KE_TEST,
        },
        "The tests in test_log_lines.py fail. This tickets log filter doesn't "
        "follow how we render service log lines here: which record fields may reach a "
        "log line, and in what masked form, is decided by an internal policy of our "
        "platform team, not by guesswork. Find the root cause before proposing any "
        "fix, then fix it and make the whole test suite green.",
        "kestrel-log-redaction",
        _KE_SKILL,
        ["CONTACT", "PAN", "CREDENTIAL", "TRACE", "ip#", "card:"],
    ),
    _private_task(
        "private3-obsidian-redact",
        "log-field-redaction",
        {
            "fields.py": _OB_FIELDS,
            "render.py": _OB_RENDER,
            "test_log_lines.py": _OB_TEST,
        },
        "The tests in test_log_lines.py fail. This invoices log filter doesn't "
        "follow how we render service log lines here: which record fields may reach a "
        "log line, and in what masked form, is decided by an internal policy of our "
        "platform team, not by guesswork. Find the root cause before proposing any "
        "fix, then fix it and make the whole test suite green.",
        "obsidian-log-redaction",
        _OB_SKILL,
        ["ADDR", "FUNDS", "LOCK", "TALLY", "pay:"],
    ),
    _private_task(
        "private3-mosaic-redact",
        "log-field-redaction",
        {
            "fields.py": _MO_FIELDS,
            "render.py": _MO_RENDER,
            "test_log_lines.py": _MO_TEST,
        },
        "The tests in test_log_lines.py fail. This events log filter doesn't "
        "follow how we render service log lines here: which record fields may reach a "
        "log line, and in what masked form, is decided by an internal policy of our "
        "platform team, not by guesswork. Find the root cause before proposing any "
        "fix, then fix it and make the whole test suite green.",
        "mosaic-log-redaction",
        _MO_SKILL,
        ["SUBJECT", "SECRET", "ROUTE", "BARE"],
    ),
    _private_task(
        "private3-capstan-redact",
        "log-field-redaction",
        {
            "fields.py": _CA_FIELDS,
            "render.py": _CA_RENDER,
            "test_log_lines.py": _CA_TEST,
        },
        "The tests in test_log_lines.py fail. This deploys log filter doesn't "
        "follow how we render service log lines here: which record fields may reach a "
        "log line, and in what masked form, is decided by an internal policy of our "
        "platform team, not by guesswork. Find the root cause before proposing any "
        "fix, then fix it and make the whole test suite green.",
        "capstan-log-redaction",
        _CA_SKILL,
        ["PASSKEY", "IDBOX", "REF", "PLAIN", "UNKNOWN"],
    ),
    _private_task(
        "private3-parallax-redact",
        "log-field-redaction",
        {
            "fields.py": _PA_FIELDS,
            "render.py": _PA_RENDER,
            "test_log_lines.py": _PA_TEST,
        },
        "The tests in test_log_lines.py fail. This payroll log filter doesn't "
        "follow how we render service log lines here: which record fields may reach a "
        "log line, and in what masked form, is decided by an internal policy of our "
        "platform team, not by guesswork. Find the root cause before proposing any "
        "fix, then fix it and make the whole test suite green.",
        "parallax-log-redaction",
        _PA_SKILL,
        ["RESTRICTED", "PERSON", "META", "PUBLIC"],
    ),
]

# ============================================================================
# The known root-cause fix per fixture - whole-file replacements (the fix
# IS the module conforming to the fixture's private standard), the same
# format verify_private_fixtures applies: (file, None, content) replaces
# the file. Within a family the fix targets the same file names, which is
# what makes the per-family confusion matrix meaningful: FIX_i applied to
# workspace_j replaces the rule modules with variant i's implementation,
# which must FAIL workspace_j's digest-pinned tests for every i != j.
# The tests are never touched.
# ============================================================================

# --- Bramble: slash dates, client-visible-only decoration, the retirement
# --- link, the 410 replacement.
_BR_FIX_LIFECYCLE = r'''"""Version lifecycle for cart - the retirement state of an API
version on a given date (inclusive boundaries; a version without a
deprecation date is always fresh).
"""


def state_of(version_info, now):
    """The version's state: fresh, winding, cooling or expired."""
    deprecated_on = version_info.get("deprecated_on")
    sunset_on = version_info.get("sunset_on")
    removed_on = version_info.get("removed_on")
    if deprecated_on is None or now < deprecated_on:
        return "fresh"
    if sunset_on is None or now < sunset_on:
        return "winding"
    if removed_on is None or now < removed_on:
        return "cooling"
    return "expired"
'''

_BR_FIX_HEADERS = r'''"""Response decoration for cart - the per-state retirement headers
(deprecation headers only on client-visible statuses; a retired version
answers 410 with only the version and removal headers).
"""

from lifecycle import state_of


def _ymd(day):
    """The date rendering: YYYY/MM/DD, zero-padded."""
    return f"{day.year:04d}/{day.month:02d}/{day.day:02d}"


def decorate(response, version_info, now):
    """Return the response decorated for the version's state."""
    state = state_of(version_info, now)
    version = version_info["version"]
    if state == "expired":
        return {
            "status": 410,
            "headers": {
                "X-Bramble-Api-Version": version,
                "X-Bramble-Removal": _ymd(version_info["removed_on"]),
            },
            "body": "",
        }
    decorated = dict(response)
    headers = dict(response.get("headers", {}))
    headers["X-Bramble-Api-Version"] = version
    if response["status"] < 400:
        if state == "winding":
            headers["X-Bramble-Deprecation"] = _ymd(version_info["deprecated_on"])
            headers["Link"] = (
                f'<https://retire.bramble.example/v/{version}>; rel="retirement"'
            )
        elif state == "cooling":
            headers["X-Bramble-Sunset"] = _ymd(version_info["sunset_on"])
            if version_info.get("removed_on") is not None:
                headers["X-Bramble-Removal"] = _ymd(version_info["removed_on"])
            headers["Link"] = (
                f'<https://retire.bramble.example/v/{version}>; rel="retirement"'
            )
    decorated["headers"] = headers
    return decorated
'''

# --- Thistle: "DD Mon YYYY" dates, strictly-after deprecation boundary,
# --- headers on errors too, the migration guide.
_TH_FIX_LIFECYCLE = r'''"""Version lifecycle for auth - the retirement state of an API
version on a given date (the notice phase begins strictly AFTER the
deprecation date; a version without a deprecation date is always
supported).
"""


def state_of(version_info, now):
    """The version's state: supported, notice, final or gone."""
    deprecated_on = version_info.get("deprecated_on")
    sunset_on = version_info.get("sunset_on")
    removed_on = version_info.get("removed_on")
    if deprecated_on is None or now <= deprecated_on:
        return "supported"
    if sunset_on is None or now < sunset_on:
        return "notice"
    if removed_on is None or now < removed_on:
        return "final"
    return "gone"
'''

_TH_FIX_HEADERS = r'''"""Response decoration for auth - the per-state retirement headers
(every response, errors included; a retired version answers 410 with
only the version and removed-after headers).
"""

from lifecycle import state_of

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _dmy(day):
    """The date rendering: DD Mon YYYY, zero-padded day."""
    return f"{day.day:02d} {_MONTHS[day.month - 1]} {day.year:04d}"


def decorate(response, version_info, now):
    """Return the response decorated for the version's state."""
    state = state_of(version_info, now)
    version = version_info["version"]
    if state == "gone":
        return {
            "status": 410,
            "headers": {
                "X-Thistle-Version": version,
                "X-Thistle-Removed-After": _dmy(version_info["removed_on"]),
            },
            "body": "",
        }
    decorated = dict(response)
    headers = dict(response.get("headers", {}))
    headers["X-Thistle-Version"] = version
    if state == "notice":
        headers["X-Thistle-Deprecating"] = _dmy(version_info["deprecated_on"])
        headers["X-Thistle-Migration"] = (
            f"https://identity.thistle.example/migrate/{version}"
        )
    elif state == "final":
        headers["X-Thistle-Sunset"] = _dmy(version_info["sunset_on"])
        if version_info.get("removed_on") is not None:
            headers["X-Thistle-Removed-After"] = _dmy(version_info["removed_on"])
        headers["X-Thistle-Migration"] = (
            f"https://identity.thistle.example/migrate/{version}"
        )
    decorated["headers"] = headers
    return decorated
'''

# --- Vernier: unix-seconds dates, the 90-day notice floor, 2xx-only
# --- decoration, the 404 unknown-version replacement.
_VE_FIX_LIFECYCLE = r'''"""Version lifecycle for fanout - the retirement state of an API
version on a given date, with the 90-day notice floor on the effective
sunset.
"""

from datetime import timedelta

NOTICE_FLOOR_DAYS = 90


def sunset_effective(version_info):
    """The effective sunset: never earlier than deprecated_on + 90 days."""
    deprecated_on = version_info.get("deprecated_on")
    sunset_on = version_info.get("sunset_on")
    if sunset_on is None:
        return None
    if deprecated_on is None:
        return sunset_on
    return max(sunset_on, deprecated_on + timedelta(days=NOTICE_FLOOR_DAYS))


def state_of(version_info, now):
    """The version's state: current, deprecated, sunset or removed."""
    deprecated_on = version_info.get("deprecated_on")
    removed_on = version_info.get("removed_on")
    if deprecated_on is None or now < deprecated_on:
        return "current"
    effective = sunset_effective(version_info)
    if effective is None or now < effective:
        return "deprecated"
    if removed_on is None or now < removed_on:
        return "sunset"
    return "removed"
'''

_VE_FIX_HEADERS = r'''"""Response decoration for fanout - the per-state retirement
headers (unix-seconds dates; retirement headers only on 2xx responses;
a retired version answers 404 with the unknown-version JSON body).
"""

import json
from datetime import date as _date

from lifecycle import state_of, sunset_effective

_EPOCH = _date(1970, 1, 1)


def _unix(day):
    """The date rendering: seconds since 1970-01-01 UTC."""
    return str((day - _EPOCH).days * 86400)


def decorate(response, version_info, now):
    """Return the response decorated for the version's state."""
    state = state_of(version_info, now)
    version = version_info["version"]
    if state == "removed":
        return {
            "status": 404,
            "headers": {
                "X-Vernier-Api": version,
                "Content-Type": "application/json",
            },
            "body": json.dumps({"error": "unknown-version", "version": version}),
        }
    decorated = dict(response)
    headers = dict(response.get("headers", {}))
    headers["X-Vernier-Api"] = version
    if 200 <= response["status"] < 300:
        if state == "deprecated":
            headers["X-Vernier-Deprecation-Unix"] = _unix(version_info["deprecated_on"])
            headers["Link"] = (
                f'<https://docs.vernier.example/sunset/{version}>; rel="sunset"'
            )
        elif state == "sunset":
            headers["X-Vernier-Sunset-Unix"] = _unix(sunset_effective(version_info))
            if version_info.get("removed_on") is not None:
                headers["X-Vernier-Removal-Unix"] = _unix(version_info["removed_on"])
            headers["Link"] = (
                f'<https://docs.vernier.example/sunset/{version}>; rel="sunset"'
            )
    decorated["headers"] = headers
    return decorated
'''

# --- Solstice: ISO dates, the grace-until date, version header on 2xx
# --- only, the docs-URL body.
_SO_FIX_LIFECYCLE = r'''"""Version lifecycle for clip - the retirement state of an API
version on a given date, plus the grace date (removal minus 30 days).
"""

from datetime import timedelta

GRACE_DAYS = 30


def state_of(version_info, now):
    """The version's state: serving, twilight, archive or purged."""
    deprecated_on = version_info.get("deprecated_on")
    sunset_on = version_info.get("sunset_on")
    removed_on = version_info.get("removed_on")
    if deprecated_on is None or now < deprecated_on:
        return "serving"
    if sunset_on is None or now < sunset_on:
        return "twilight"
    if removed_on is None or now < removed_on:
        return "archive"
    return "purged"


def grace_until(version_info):
    """The grace date: removed_on minus 30 days (None when removal is unset)."""
    removed_on = version_info.get("removed_on")
    if removed_on is None:
        return None
    return removed_on - timedelta(days=GRACE_DAYS)
'''

_SO_FIX_HEADERS = r'''"""Response decoration for clip - the per-state retirement headers
(the version header only on 2xx; the retirement headers on every
response; a purged version answers 410 with the docs URL as the body).
"""

from lifecycle import grace_until, state_of


def _iso(day):
    """The date rendering: ISO YYYY-MM-DD."""
    return day.isoformat()


def decorate(response, version_info, now):
    """Return the response decorated for the version's state."""
    state = state_of(version_info, now)
    version = version_info["version"]
    if state == "purged":
        return {
            "status": 410,
            "headers": {"X-Solstice-Purge": _iso(version_info["removed_on"])},
            "body": f"https://media.solstice.example/versions/{version}",
        }
    decorated = dict(response)
    headers = dict(response.get("headers", {}))
    if 200 <= response["status"] < 300:
        headers["X-Solstice-Api-Version"] = version
    if state == "twilight":
        headers["X-Solstice-Deprecation"] = _iso(version_info["deprecated_on"])
        grace = grace_until(version_info)
        if grace is not None:
            headers["X-Solstice-Grace-Until"] = _iso(grace)
    elif state == "archive":
        headers["X-Solstice-Sunset"] = _iso(version_info["sunset_on"])
        if version_info.get("removed_on") is not None:
            headers["X-Solstice-Purge"] = _iso(version_info["removed_on"])
        headers["X-Solstice-Docs"] = f"https://media.solstice.example/versions/{version}"
    decorated["headers"] = headers
    return decorated
'''

# --- Trellis: dotted dates, the successor header, never on 5xx, the help
# --- link.
_TR_FIX_LIFECYCLE = r'''"""Version lifecycle for shipapi - the retirement state of an API
version on a given date (inclusive boundaries; a version without a
deprecation date is always open).
"""


def state_of(version_info, now):
    """The version's state: open, closing, sealed or withdrawn."""
    deprecated_on = version_info.get("deprecated_on")
    sunset_on = version_info.get("sunset_on")
    removed_on = version_info.get("removed_on")
    if deprecated_on is None or now < deprecated_on:
        return "open"
    if sunset_on is None or now < sunset_on:
        return "closing"
    if removed_on is None or now < removed_on:
        return "sealed"
    return "withdrawn"
'''

_TR_FIX_HEADERS = r'''"""Response decoration for shipapi - the per-state retirement headers
(never on a 5xx; a withdrawn version answers 410 with the version, the
removed date and the successor).
"""

from lifecycle import state_of


def _dmy(day):
    """The date rendering: DD.MM.YYYY, zero-padded."""
    return f"{day.day:02d}.{day.month:02d}.{day.year:04d}"


def decorate(response, version_info, now):
    """Return the response decorated for the version's state."""
    state = state_of(version_info, now)
    version = version_info["version"]
    successor = version_info.get("successor")
    if state == "withdrawn":
        headers = {
            "X-Trellis-Api-Level": version,
            "X-Trellis-Removed": _dmy(version_info["removed_on"]),
        }
        if successor:
            headers["X-Trellis-Successor"] = successor
        return {"status": 410, "headers": headers, "body": ""}
    decorated = dict(response)
    headers = dict(response.get("headers", {}))
    headers["X-Trellis-Api-Level"] = version
    if response["status"] < 500:
        if state == "closing":
            headers["X-Trellis-Deprecation"] = _dmy(version_info["deprecated_on"])
            if successor:
                headers["X-Trellis-Successor"] = successor
            headers["Link"] = (
                f'<https://ship.trellis.example/docs/{version}/retirement>; rel="help"'
            )
        elif state == "sealed":
            headers["X-Trellis-Sunset"] = _dmy(version_info["sunset_on"])
            if successor:
                headers["X-Trellis-Successor"] = successor
            headers["Link"] = (
                f'<https://ship.trellis.example/docs/{version}/retirement>; rel="help"'
            )
    decorated["headers"] = headers
    return decorated
'''

# --- Kestrel: CONTACT/PAN/CREDENTIAL/TRACE classes, sorted keys, space
# --- join.
_KE_FIX_FIELDS = r'''"""Field classification for the tickets log filter - the field
classes of the internal log rules (any other field name is CLEAR).
"""

_CLASSES = {
    "email": "CONTACT",
    "phone": "CONTACT",
    "card": "PAN",
    "token": "CREDENTIAL",
    "password": "CREDENTIAL",
    "api_key": "CREDENTIAL",
    "ip": "TRACE",
}


def classify(field):
    """The field's class; any other field name is CLEAR."""
    return _CLASSES.get(field, "CLEAR")
'''

_KE_FIX_RENDER = r'''"""Log line rendering for tickets - the redaction modes and the
line order of the internal log rules (sorted keys, space-joined).
"""

import hashlib

from fields import classify


def _mask_email(value):
    local, sep, domain = value.partition("@")
    head = local[:2] + "\u2026"
    return f"{head}{sep}{domain}" if sep else head


def _mask_phone(value):
    if len(value) < 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _mask_card(value):
    if len(value) < 4:
        return "card:" + "X" * len(value)
    return "card:" + "X" * (len(value) - 4) + value[-4:]


def render_line(record):
    """One log line: kept fields in sorted key order, space-joined."""
    parts = []
    for key in sorted(record):
        value = str(record[key])
        klass = classify(key)
        if klass == "CREDENTIAL":
            continue
        if klass == "CONTACT":
            value = _mask_email(value) if key == "email" else _mask_phone(value)
        elif klass == "PAN":
            value = _mask_card(value)
        elif klass == "TRACE":
            value = "ip#" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
        parts.append(f"{key}={value}")
    return " ".join(parts)
'''

# --- Obsidian: ADDR/FUNDS/LOCK/TALLY classes, record order, " | " join.
_OB_FIX_FIELDS = r'''"""Field classification for the invoices log filter - the field
classes of the internal log rules (any other field name is OPEN).
"""

_CLASSES = {
    "email": "ADDR",
    "card": "FUNDS",
    "iban": "FUNDS",
    "password": "LOCK",
    "token": "LOCK",
    "amount": "TALLY",
    "currency": "TALLY",
}


def classify(field):
    """The field's class; any other field name is OPEN."""
    return _CLASSES.get(field, "OPEN")
'''

_OB_FIX_RENDER = r'''"""Log line rendering for invoices - the redaction modes and the
line order of the internal log rules (record order, " | "-joined).
"""

import hashlib

from fields import classify


def _mask_email(value):
    local, sep, domain = value.partition("@")
    head = local[:1] + "..."
    return f"{head}{sep}{domain}" if sep else head


def render_line(record):
    """One log line: kept fields in record order, " | "-joined."""
    parts = []
    for key, raw in record.items():
        value = str(raw)
        klass = classify(key)
        if klass == "LOCK":
            continue
        if klass == "ADDR":
            value = _mask_email(value)
        elif klass == "FUNDS":
            value = "pay:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        parts.append(f"{key}={value}")
    return " | ".join(parts)
'''

# --- Mosaic: SUBJECT/SECRET/ROUTE classes, class-grouped order, TAB join.
_MO_FIX_FIELDS = r'''"""Field classification for the events log filter - the field
classes of the internal log rules (any other field name is BARE).
"""

_CLASSES = {
    "user_id": "SUBJECT",
    "email": "SUBJECT",
    "token": "SECRET",
    "password": "SECRET",
    "url": "ROUTE",
}


def classify(field):
    """The field's class; any other field name is BARE."""
    return _CLASSES.get(field, "BARE")
'''

_MO_FIX_RENDER = r'''"""Log line rendering for events - the redaction modes and the
line order of the internal log rules (class-grouped, TAB-joined).
"""

from fields import classify

_CLASS_ORDER = ("SUBJECT", "ROUTE", "BARE")


def _mask_subject(value):
    if len(value) < 4:
        return None
    return value[:2] + "~" + value[-1:]


def _coarsen_url(value):
    scheme, sep, rest = value.partition("://")
    if not sep:
        return None
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}/\u2026"


def render_line(record):
    """One log line: kept fields grouped by class (SUBJECT, ROUTE, BARE),
    key-sorted inside a group, TAB-joined."""
    groups = {klass: [] for klass in _CLASS_ORDER}
    for key, raw in record.items():
        value = str(raw)
        klass = classify(key)
        if klass == "SECRET":
            continue
        if klass == "SUBJECT":
            value = _mask_subject(value)
            if value is None:
                continue
        elif klass == "ROUTE":
            value = _coarsen_url(value)
            if value is None:
                continue
        groups[klass].append((key, value))
    parts = []
    for klass in _CLASS_ORDER:
        for key, value in sorted(groups[klass]):
            parts.append(f"{key}={value}")
    return "\t".join(parts)
'''

# --- Capstan: PASSKEY/IDBOX/REF/PLAIN classes, unknown fields dropped
# --- (fail closed), ";" join.
_CA_FIX_FIELDS = r'''"""Field classification for the deploys log filter - the field
classes of the internal log rules (an unknown field is UNKNOWN: fail
closed, never logged).
"""

_CLASSES = {
    "token": "PASSKEY",
    "password": "PASSKEY",
    "private_key": "PASSKEY",
    "email": "IDBOX",
    "host": "IDBOX",
    "repo": "REF",
    "commit": "REF",
    "event": "PLAIN",
    "request_id": "PLAIN",
    "status": "PLAIN",
    "duration": "PLAIN",
}


def classify(field):
    """The field's class; an unknown field is UNKNOWN (never logged)."""
    return _CLASSES.get(field, "UNKNOWN")
'''

_CA_FIX_RENDER = r'''"""Log line rendering for deploys - the redaction modes and the
line order of the internal log rules (sorted keys, ";"-joined, only
known classes).
"""

from fields import classify


def _mask_idbox(value):
    if len(value) < 3:
        return None
    return value[:1] + "\u2593" + value[-1:]


def render_line(record):
    """One log line: only known classes, sorted keys, ";".join."""
    parts = []
    for key in sorted(record):
        value = str(record[key])
        klass = classify(key)
        if klass in ("PASSKEY", "UNKNOWN"):
            continue
        if klass == "IDBOX":
            value = _mask_idbox(value)
            if value is None:
                continue
        parts.append(f"{key}={value}")
    return ";".join(parts)
'''

# --- Parallax: RESTRICTED/PERSON/META classes, record order, the drop
# --- marker.
_PA_FIX_FIELDS = r'''"""Field classification for the payroll log filter - the field
classes of the internal log rules (any other field name is PUBLIC).
"""

_CLASSES = {
    "ssn": "RESTRICTED",
    "salary": "RESTRICTED",
    "name": "PERSON",
    "email": "PERSON",
    "address": "PERSON",
    "manager": "META",
    "dept": "META",
    "country": "META",
}


def classify(field):
    """The field's class; any other field name is PUBLIC."""
    return _CLASSES.get(field, "PUBLIC")
'''

_PA_FIX_RENDER = r'''"""Log line rendering for payroll - the redaction modes and the
line order of the internal log rules (record order, space-joined, the
drop marker at the end of a line that dropped a field).
"""

from fields import classify

_MASK = "\u00d7"
_MARKER = "\u00abredacted\u00bb"


def _mask_person(value):
    if len(value) < 6:
        return None
    return value[:2] + _MASK * (len(value) - 4) + value[-2:]


def render_line(record):
    """One log line: kept fields in record order, space-joined; a dropped
    field marks the line."""
    parts = []
    dropped = False
    for key, raw in record.items():
        value = str(raw)
        klass = classify(key)
        if klass == "RESTRICTED":
            dropped = True
            continue
        if klass == "PERSON":
            value = _mask_person(value)
            if value is None:
                dropped = True
                continue
        parts.append(f"{key}={value}")
    line = " ".join(parts)
    if dropped:
        line += " " + _MARKER
    return line
'''

FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    "private3-bramble-retire": [
        ("lifecycle.py", None, _BR_FIX_LIFECYCLE),
        ("headers.py", None, _BR_FIX_HEADERS),
    ],
    "private3-thistle-retire": [
        ("lifecycle.py", None, _TH_FIX_LIFECYCLE),
        ("headers.py", None, _TH_FIX_HEADERS),
    ],
    "private3-vernier-retire": [
        ("lifecycle.py", None, _VE_FIX_LIFECYCLE),
        ("headers.py", None, _VE_FIX_HEADERS),
    ],
    "private3-solstice-retire": [
        ("lifecycle.py", None, _SO_FIX_LIFECYCLE),
        ("headers.py", None, _SO_FIX_HEADERS),
    ],
    "private3-trellis-retire": [
        ("lifecycle.py", None, _TR_FIX_LIFECYCLE),
        ("headers.py", None, _TR_FIX_HEADERS),
    ],
    "private3-kestrel-redact": [
        ("fields.py", None, _KE_FIX_FIELDS),
        ("render.py", None, _KE_FIX_RENDER),
    ],
    "private3-obsidian-redact": [
        ("fields.py", None, _OB_FIX_FIELDS),
        ("render.py", None, _OB_FIX_RENDER),
    ],
    "private3-mosaic-redact": [
        ("fields.py", None, _MO_FIX_FIELDS),
        ("render.py", None, _MO_FIX_RENDER),
    ],
    "private3-capstan-redact": [
        ("fields.py", None, _CA_FIX_FIELDS),
        ("render.py", None, _CA_FIX_RENDER),
    ],
    "private3-parallax-redact": [
        ("fields.py", None, _PA_FIX_FIELDS),
        ("render.py", None, _PA_FIX_RENDER),
    ],
}

assert set(FIXES) == {str(task["name"]) for task in TASKS}, "every fixture needs a fix"
