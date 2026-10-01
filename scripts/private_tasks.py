"""Private-knowledge fixtures for the E2 skill axis (2026-10-01).

The domain set measured skills whose knowledge is PUBLIC (brand values,
protocol conventions) — and the model already had it in training data
(ADR-014 amendment 17: brand-palette 3/3 naked). The private set is the E2
instrument (docs/plans/aci-improvement-2026-10.md §2 E2): each fixture's
required knowledge did NOT exist before today — a fictional internal
standard invented for this fixture — so no model can carry it. The rules
are arbitrary but precise, documented ONLY in the fixture's private
SKILL.md (served by H-bench arm R's local in-process handler, no registry,
no DB), and pinned in the tests as sha256 DIGESTS over the observed
behavior — never plaintext, so the test cannot become a copy of the
standard and a naked model cannot derive the rules from the tests' names,
messages or the shipped code (the shipped code embodies a WRONG policy).

Fixture → private skill (the single source of the rules):

- private-atlas-retry → atlas-error-standard
      the Atlas platform error-code classes (which codes retry, fall
      back or escalate), retry budgets, backoff, event-line formats and
      error type names.
- private-meridian-release → meridian-release-runbook
      the Meridian release gate: violation codes, rule order, skip rules.

§34 caveat applies to any round run on this set: small n, author-built
fixtures, one model — directional only.
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _private_task(
    name: str, files: dict[str, str], prompt: str, skill_id: str, skill: str
) -> dict[str, Any]:
    """A private-knowledge fixture: workspace files + task prompt + the
    private SKILL.md text (NOT a workspace file — the model must never see
    it on disk; arm R serves it through the capability plane only)."""
    return {
        "name": name,
        "files": files,
        "prompt": prompt,
        "skill_id": skill_id,
        "skill": skill,
    }


PRIVATE_INTENDED_SKILLS: dict[str, str] = {
    "private-atlas-retry": "atlas-error-standard",
    "private-meridian-release": "meridian-release-runbook",
}

PRIVATE_TASKS: list[dict[str, Any]] = [
    _private_task(
        "private-atlas-retry",
        {
            "atlas_client.py": (
                '"""Atlas service client.\n'
                "\n"
                "Retries any Atlas error up to three attempts with a fixed one-second\n"
                "backoff, then raises AtlasError. The ``fallback`` argument is accepted\n"
                "for compatibility but unused.\n"
                '"""\n'
                "\n"
                "\n"
                "class AtlasError(Exception):\n"
                '    """Raised when an Atlas call ultimately fails."""\n'
                "\n"
                "\n"
                "class AtlasClient:\n"
                "    def __init__(self, transport, *, clock=None, on_event=None):\n"
                "        self._transport = transport\n"
                "        self._clock = clock\n"
                "        self._on_event = on_event\n"
                "\n"
                "    def call(self, op, payload=None, *, fallback=None):\n"
                "        code = None\n"
                "        for attempt in (1, 2, 3):\n"
                "            response = self._transport.request(op, payload)\n"
                '            if response.startswith("OK "):\n'
                '                return response[len("OK ") :]\n'
                "            code = response.split()[-1]\n"
                '            self._emit(f"[atlas] retrying {code} after 1s (attempt {attempt})")\n'
                "            self._sleep(1)\n"
                '        raise AtlasError(f"atlas call failed with {code}")\n'
                "\n"
                "    def _emit(self, line):\n"
                "        if self._on_event:\n"
                "            self._on_event(line)\n"
                "\n"
                "    def _sleep(self, seconds):\n"
                "        if self._clock:\n"
                "            self._clock.sleep(seconds)\n"
            ),
            "test_atlas_convention.py": (
                '"""Contract tests for the Atlas service client against the INTERNAL\n'
                "Atlas error-handling standard. The standard is not public: each\n"
                "scenario's expected behavior is pinned as a sha256 DIGEST over the\n"
                "observed trace (event lines + outcome), so this file cannot become a\n"
                'copy of the standard."""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from atlas_client import AtlasClient\n"
                "\n"
                "\n"
                "class ScriptedTransport:\n"
                '    """Fake transport: the scripted responses in order; the last repeats."""\n'
                "\n"
                "    def __init__(self, responses):\n"
                "        self._responses = list(responses)\n"
                "\n"
                "    def request(self, op, payload=None):\n"
                "        if len(self._responses) > 1:\n"
                "            return self._responses.pop(0)\n"
                "        return self._responses[0]\n"
                "\n"
                "\n"
                "class FakeClock:\n"
                '    """Fake clock: sleep() is recorded, never really slept."""\n'
                "\n"
                "    def __init__(self):\n"
                "        self.sleeps = []\n"
                "\n"
                "    def sleep(self, seconds):\n"
                "        self.sleeps.append(seconds)\n"
                "\n"
                "\n"
                "def drive(responses, *, fallback=None):\n"
                '    """One scenario: the client\'s event lines, then the outcome line."""\n'
                "    trace = []\n"
                "    client = AtlasClient(\n"
                "        ScriptedTransport(responses), clock=FakeClock(), on_event=trace.append\n"
                "    )\n"
                "    try:\n"
                '        value = client.call("get_order", fallback=fallback)\n'
                "    except Exception as exc:  # the outcome line is part of the trace\n"
                '        trace.append(f"raise {type(exc).__name__}")\n'
                "    else:\n"
                '        trace.append(f"return {value!r}")\n'
                "    return trace\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "SCENARIOS = [\n"
                '    (["ERR ATL-E101", "ERR ATL-E101", "OK order-7"], None),\n'
                '    (["ERR ATL-E101"], None),\n'
                '    (["ERR ATL-E205"], None),\n'
                '    (["ERR ATL-E307", "ERR ATL-E307"], "cached-order-7"),\n'
                '    (["ERR ATL-E404"], None),\n'
                "]\n"
                "\n"
                '# sha256 of "\\n".join(trace) per scenario, from the standard.\n'
                "DIGESTS = [\n"
                '    "0ab4e55aeb18ce3d66e7d2bb69be2c2721a3f8c4f19b34696c237cecf5cbd777",\n'
                '    "aa0e1510e24b3a3fa7d58d58f486061c4c789672c00fd8201b47130fa1403b93",\n'
                '    "3bbe86e621f3a1da59d0a5cc7c546ede1d1c7ec6691c67839526242095df444c",\n'
                '    "0617d2ba256d04750bc5b8016b82e84fc643faf6ee1c3abc2c64037ce73cf71f",\n'
                '    "55aa1e7697b6763a764d0c217251bae78332a6822dac55cdf50eacebef9aa245",\n'
                "]\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("responses", "fallback", "digest"),\n'
                "    [(r, f, d) for (r, f), d in zip(SCENARIOS, DIGESTS, strict=True)],\n"
                '    ids=[f"s{i:02d}" for i in range(1, len(SCENARIOS) + 1)],\n'
                ")\n"
                "def test_scenario(responses, fallback, digest):\n"
                "    trace = drive(responses, fallback=fallback)\n"
                '    assert _digest("\\n".join(trace)) == digest, trace\n'
            ),
        },
        "The tests in test_atlas_convention.py fail. This Atlas service client does not "
        "follow our internal Atlas error-handling standard: how Atlas error codes are "
        "classified, and what the client must do for each class, is decided by the "
        "standard, not by guesswork. Find the root cause before proposing any fix, "
        "then fix it and make the whole test suite green.",
        "atlas-error-standard",
        """---
name: atlas-error-standard
description: Atlas platform error-handling standard - which codes retry, fall back, escalate.
version: 1.0.0
---

# Atlas Error Handling Standard (internal)

How every client of the Atlas platform must handle Atlas service
responses. Internal to the Atlas platform team - not published anywhere.

## Responses

An Atlas transport response is one of:

- `OK <payload>` - success; the payload is everything after `OK `.
- `ERR <code>` - failure with an Atlas error code, e.g. `ERR ATL-E101`.

## Error-code classes

The class of a code decides its handling. The known codes:

| Code     | Class        |
|----------|--------------|
| ATL-E101 | TRANSIENT    |
| ATL-E205 | CLIENT_FAULT |
| ATL-E307 | DEGRADED     |
| ATL-E404 | ESCALATE     |

A code not in the table is handled as ESCALATE (fail closed).

## Class behavior

- TRANSIENT - retry up to 3 attempts TOTAL. Before each retry, emit the
  retry line, then wait (failed attempt) squared seconds through the
  injected clock: after attempt 1 wait 1s, after attempt 2 wait 4s. If
  attempt 3 still fails, raise `AtlasTransientError`.
- CLIENT_FAULT - never retried: raise `AtlasClientError` immediately.
- DEGRADED - retry ONCE: emit the retry line, then wait a fixed 2s (NOT
  the transient backoff), then try again. If the retry succeeds, return
  its payload. If it fails: with a fallback provided, emit the degraded
  line and return the fallback; without one, raise
  `AtlasDegradedError`.
- ESCALATE - never retried: raise `AtlasEscalationError` immediately.

All four error types subclass `AtlasError`.

## Event lines

The client reports what it does through the injected `on_event`
callable, one line per event, exactly in these formats:

- retry: `[atlas] retry code=<code> attempt=<failed attempt> wait=<wait>s`
- degraded: `[atlas] degraded code=<code> fallback=<fallback repr>`

`<failed attempt>` is the 1-based number of the attempt that just
failed. `<fallback repr>` is the fallback value rendered with Python
`repr()`.

## Worked example

`call("get_order")` against a transport scripted `ERR ATL-E101`,
`ERR ATL-E101`, `OK order-7`:

    [atlas] retry code=ATL-E101 attempt=1 wait=1s
    [atlas] retry code=ATL-E101 attempt=2 wait=4s

and the call returns `order-7`.

`call("get_order", fallback="cached-order-7")` against a transport
scripted `ERR ATL-E307`, `ERR ATL-E307`:

    [atlas] retry code=ATL-E307 attempt=1 wait=2s
    [atlas] degraded code=ATL-E307 fallback='cached-order-7'

and the call returns `cached-order-7`.
""",
    ),
    _private_task(
        "private-meridian-release",
        {
            "release_gate.py": (
                '"""Meridian release gate - validate a release config before promotion."""\n'
                "\n"
                "\n"
                "def check_release(config):\n"
                '    """Return the violation codes for a release config (empty = compliant)."""\n'
                "    violations = []\n"
                '    version = str(config.get("version", ""))\n'
                '    parts = version.split(".")\n'
                "    if len(parts) != 3 or not all(p.isdigit() for p in parts):\n"
                '        violations.append("version")\n'
                '    channel = config.get("channel")\n'
                '    if channel not in ("stable", "rapid", "lts"):\n'
                '        violations.append("channel")\n'
                "    minor = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else None\n"
                '    if minor is not None and channel in ("stable", "rapid"):\n'
                "        if minor % 2 != 1:\n"
                '            violations.append("parity")\n'
                '    if channel == "lts" and minor is not None and minor % 2 != 0:\n'
                '        violations.append("parity")\n'
                '    if not config.get("environments"):\n'
                '        violations.append("envs")\n'
                '    if len(set(config.get("approvers", []))) < 2:\n'
                '        violations.append("approvers")\n'
                "    return violations\n"
            ),
            "test_release_gate.py": (
                '"""Contract tests for the release gate against the INTERNAL Meridian\n'
                "release standard. The standard is not public: each sample config's\n"
                "expected verdict is pinned as a sha256 DIGEST over the reported\n"
                "violation codes, so this file cannot become a copy of the standard.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from release_gate import check_release\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "CONFIGS = [\n"
                "    {\n"
                '        "name": "c01",\n'
                '        "version": "3.4.1",\n'
                '        "channel": "stable",\n'
                '        "environments": ["dev", "staging", "prod"],\n'
                '        "migrations": ["M0001", "M0004"],\n'
                '        "approvers": ["ana", "bo"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c02",\n'
                '        "version": "3.5.1",\n'
                '        "channel": "stable",\n'
                '        "environments": ["dev", "staging", "prod"],\n'
                '        "migrations": ["M0001", "M0004"],\n'
                '        "approvers": ["ana", "bo"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c03",\n'
                '        "version": "2.7.0",\n'
                '        "channel": "rapid",\n'
                '        "environments": ["dev", "staging"],\n'
                '        "migrations": [],\n'
                '        "approvers": ["cy"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c04",\n'
                '        "version": "2.8.0",\n'
                '        "channel": "rapid",\n'
                '        "environments": ["dev", "staging"],\n'
                '        "migrations": [],\n'
                '        "approvers": ["cy"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c05",\n'
                '        "version": "v1.2.0",\n'
                '        "channel": "stable",\n'
                '        "environments": ["dev", "staging", "prod"],\n'
                '        "migrations": ["M0001"],\n'
                '        "approvers": ["ana", "bo"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c06",\n'
                '        "version": "1.02.0",\n'
                '        "channel": "stable",\n'
                '        "environments": ["dev", "staging", "prod"],\n'
                '        "migrations": ["M0001"],\n'
                '        "approvers": ["ana", "bo"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c07",\n'
                '        "version": "1.2.0",\n'
                '        "channel": "beta",\n'
                '        "environments": ["dev", "staging"],\n'
                '        "migrations": ["M0001"],\n'
                '        "approvers": ["ana", "bo"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c08",\n'
                '        "version": "1.2.0",\n'
                '        "channel": "stable",\n'
                '        "environments": ["dev", "prod"],\n'
                '        "migrations": ["M0001"],\n'
                '        "approvers": ["ana", "bo"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c09",\n'
                '        "version": "1.2.0",\n'
                '        "channel": "stable",\n'
                '        "environments": ["dev", "staging", "prod"],\n'
                '        "migrations": ["M0009", "M0004"],\n'
                '        "approvers": ["ana", "bo"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c10",\n'
                '        "version": "1.2.0",\n'
                '        "channel": "lts",\n'
                '        "environments": ["dev", "staging", "prod"],\n'
                '        "migrations": ["M0002"],\n'
                '        "approvers": ["dee", "eli"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c11",\n'
                '        "version": "1.2.0",\n'
                '        "channel": "stable",\n'
                '        "environments": ["prod"],\n'
                '        "migrations": ["M0003", "M0001"],\n'
                '        "approvers": ["solo"],\n'
                "    },\n"
                "    {\n"
                '        "name": "c12",\n'
                '        "version": "1.2.0",\n'
                '        "channel": "lts",\n'
                '        "environments": ["dev", "staging", "prod"],\n'
                '        "migrations": ["M0002"],\n'
                '        "approvers": ["dee", "eli", "fay"],\n'
                "    },\n"
                "]\n"
                "\n"
                '# sha256 of ",".join(check_release(config)) per config, from the standard.\n'
                "DIGESTS = [\n"
                '    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n'
                '    "c6a5599775bfa2b5fa78fc1c871858503d23b84924bd670ee09e100cccea6ae9",\n'
                '    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n'
                '    "c6a5599775bfa2b5fa78fc1c871858503d23b84924bd670ee09e100cccea6ae9",\n'
                '    "e55ac6cc882b982c1d74e8914cd4e8d394da66546ff26479c8c01cebdb6f9d33",\n'
                '    "e55ac6cc882b982c1d74e8914cd4e8d394da66546ff26479c8c01cebdb6f9d33",\n'
                '    "f368c33ae1d3ce00f4ff28c239105e3140ff589807a3c673b0dd97adb14af1b6",\n'
                '    "3a9e889580d11aeaf35271d0837436b4edf2928784b19308f5c24864c1d75949",\n'
                '    "79c17af12eb082ad8e286bdc0f98a6d307919c9e37a455c17fb8b724088b80c9",\n'
                '    "23ada3b6a6ea509b6365bef5b3b18d81e928810fb15296eab9bdcce55ddc1cfe",\n'
                '    "63fb233b2405ad1e6545ec17a8d0ce49e18df9c6571410d95e753ac59d8cb9ad",\n'
                '    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n'
                "]\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("config", "digest"),\n'
                "    zip(CONFIGS, DIGESTS, strict=True),\n"
                '    ids=[c["name"] for c in CONFIGS],\n'
                ")\n"
                "def test_verdict(config, digest):\n"
                '    verdict = ",".join(check_release(config))\n'
                "    assert _digest(verdict) == digest, f\"{config['name']}: {verdict!r}\"\n"
            ),
        },
        "The tests in test_release_gate.py fail. This release gate must enforce our "
        "internal Meridian release standard: which release configs are blocked, and "
        "for what, is decided by the runbook's rules, not by guesswork. Find the root "
        "cause before proposing any fix, then fix it and make the whole test suite "
        "green.",
        "meridian-release-runbook",
        """---
name: meridian-release-runbook
description: Meridian release gate standard - rules, violation codes, rule order, skip rules.
version: 1.0.0
---

# Meridian Release Gate Standard (internal)

What `check_release(config)` must enforce before a release config may
be promoted. Internal to the Meridian platform team - not published.

A release config carries: `name`, `version`, `channel`,
`environments`, `migrations`, `approvers`. The gate returns the
violation code of every violated rule, in RULE ORDER; an empty list
means compliant.

## Rules, in rule order

1. `version-scheme` - the version must be `MAJOR.MINOR.PATCH`: three
   dot-separated parts, digits only, no leading zeros (a lone `0` is
   fine). A malformed version SKIPS rule 3.
2. `channel-unknown` - the channel must be one of `stable`, `rapid`,
   `lts`. An unknown channel SKIPS rules 3 and 5.
3. `minor-parity` - `stable` and `lts` require an EVEN minor; `rapid`
   requires an ODD minor.
4. `promotion-order` - `environments` must be exactly a prefix of
   `dev`, `staging`, `prod`, in that order: no skips, no duplicates,
   no extra names. An empty list is a valid prefix.
5. `approver-count` - the number of DISTINCT approvers: `stable` needs
   at least 2, `rapid` at least 1, `lts` at least 3.
6. `migration-order` - every migration id is `M` + 4 digits, and the
   ids are strictly ascending (a duplicate is a violation).

Each rule emits at most one violation code, even when broken several
ways in one config.

## Worked example

    {"version": "1.2.0", "channel": "stable",
     "environments": ["dev", "prod"],
     "migrations": ["M0009", "M0004"],
     "approvers": ["ana"]}

violates `promotion-order` (staging skipped), `approver-count` (stable
needs 2 distinct approvers) and `migration-order` (not ascending), and
nothing else. The gate reports:

    ["promotion-order", "approver-count", "migration-order"]
""",
    ),
]
