"""Builder C's private-knowledge fixtures for the E2C skill axis (--set private2).

E2B (ADR-014 amendment 24) measured the private-skill axis on 2 fixtures
(atlas/meridian); E2C replicates the measurement on 7 NEW, varied fixtures
built by three parallel builders (a/b/c). This module is builder C's two
(each fixture is a NEW fictional internal standard, invented with the
fixture — the knowledge did not exist before today, so no model can
carry it):

- private2-vellum-order-id → vellum-order-id-standard (directness: named)
      the Vellum Commerce order-identifier standard: per-region ID
      layouts and the seal letter (a custom check character — not
      Luhn, not ISO 7064).
- private2-queue-consumer-retry → ferry-consumer-retry-policy (directness: indirect)
      the Ferry messaging platform's consumer retry policy: error
      families, per-family attempt budgets, the backoff ladder, the
      poison rule and the dead-letter envelope.

Same contract as scripts/private_tasks.py: the rules live ONLY in the
fixture's private SKILL.md (served through the capability plane, never
a workspace file), pinned in the tests as sha256 DIGESTS over grouped
multi-decision traces — never plaintext, never one decision per digest
(the E2B §1.5 meridian lesson: small digest spaces are brute-forceable
in principle; every line here also carries standard-only vocabulary,
which kills offline enumeration). The shipped code embodies a
PLAUSIBLE BUT WRONG public convention (Luhn check digits / uniform
exponential retry), so a competent engineer's guess fails.

The knowledge gate: each fixture's MARKERS (vocabulary that exists only
in the standard) appear in the skill and NOWHERE else — not in the
tests, not in the shipped code, not in the prompt. Directness is
assigned per fixture: `named` = the prompt names the standard;
`indirect` = the prompt says only "our internal policy", naming
neither the standard nor its title words.

§34 caveat applies to any round run on this set: small n, author-built
fixtures, one model — directional only.
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
    markers: list[str],
) -> dict[str, Any]:
    """A private-knowledge fixture: workspace files + task prompt + the
    private SKILL.md text (NOT a workspace file — the model must never see
    it on disk; the capability plane serves it) + the assigned directness
    + the standard-only markers (the knowledge gate)."""
    return {
        "name": name,
        "files": files,
        "prompt": prompt,
        "skill_id": skill_id,
        "skill": skill,
        "directness": directness,
        "markers": markers,
    }


TASKS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # private2-vellum-order-id — the Vellum Commerce order-identifier
    # standard (commerce team). The shipped code guesses the public
    # convention (Luhn-style check DIGIT, one universal dashed layout);
    # the standard defines per-region layouts and a weighted seal
    # LETTER over a 23-letter alphabet. NAMED: the prompt names the
    # standard.
    # ------------------------------------------------------------------
    _private_task(
        "private2-vellum-order-id",
        {
            "order_ids.py": (
                '"""Order document IDs at Vellum Commerce.\n'
                "\n"
                "An order ID is `<REGION>-<serial>-<check>`: the two-letter region\n"
                "code, the serial zero-padded to seven digits, and one Luhn-style\n"
                "check digit computed over the region code letters (A=10 .. Z=35)\n"
                "followed by the serial digits.\n"
                '"""\n'
                "\n"
                "import re\n"
                "\n"
                '_SHAPE = re.compile(r"^([A-Z]{2})-(\\d{7})-(\\d)$")\n'
                "\n"
                "\n"
                "def _check_digit(text):\n"
                '    """Luhn-style check digit over `text` (letters map A=10 .. Z=35)."""\n'
                "    total = 0\n"
                "    for i, ch in enumerate(reversed(text)):\n"
                "        value = int(ch) if ch.isdigit() else (ord(ch) - 55)\n"
                "        if i % 2 == 1:\n"
                "            value *= 2\n"
                "            if value > 9:\n"
                "                value -= 9\n"
                "        total += value\n"
                "    return str((10 - total % 10) % 10)\n"
                "\n"
                "\n"
                "def format_order_id(region, serial):\n"
                '    """The order ID for `region` and `serial` (zero-padded to 7 digits)."""\n'
                '    digits = f"{serial:07d}"\n'
                '    return f"{region}-{digits}-{_check_digit(region + digits)}"\n'
                "\n"
                "\n"
                "def validate(text):\n"
                '    """(ok, reason) for a candidate order ID string."""\n'
                "    match = _SHAPE.match(text)\n"
                "    if not match:\n"
                '        return (False, "malformed")\n'
                "    region, digits, check = match.groups()\n"
                "    if _check_digit(region + digits) != check:\n"
                '        return (False, "checksum")\n'
                "    return (True, None)\n"
            ),
            "order_docs.py": (
                '"""Order documents and their references (uses order_ids for ID layout)."""\n'
                "\n"
                "from order_ids import format_order_id, validate\n"
                "\n"
                "\n"
                "class OrderDocument:\n"
                '    """One order document: its home region, its serial, its line items."""\n'
                "\n"
                "    def __init__(self, region, serial, items):\n"
                "        self.region = region\n"
                "        self.serial = serial\n"
                "        self.items = list(items)\n"
                "\n"
                "    def header(self):\n"
                '        """The canonical header line for logs and printouts."""\n'
                "        ident = format_order_id(self.region, self.serial)\n"
                '        return f"[order {ident}] items={len(self.items)}"\n'
                "\n"
                "\n"
                "def check_reference(text):\n"
                '    """What the reference desk says about a candidate order ID."""\n'
                "    ok, reason = validate(text)\n"
                "    if ok:\n"
                '        return "accepted"\n'
                '    return f"rejected:{reason}"\n'
            ),
            "test_order_ids.py": (
                '"""Contract tests for order document IDs against the INTERNAL Vellum\n'
                "order identifier standard. The standard is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the observed\n"
                "trace, so this file cannot become a copy of the standard.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from order_docs import OrderDocument, check_reference\n"
                "from order_ids import format_order_id, validate\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "FORMAT_CASES = [\n"
                '    ("NA", 4137902),\n'
                '    ("EU", 4137),\n'
                '    ("AP", 591),\n'
                '    ("NA", 0),\n'
                '    ("EU", 99999999),\n'
                '    ("AP", 777777),\n'
                '    ("NA", 1205206),\n'
                '    ("EU", 50505050),\n'
                "]\n"
                "\n"
                "CANDIDATES = [\n"
                '    "NA-4137902-Q",\n'
                '    "NA-4137902-I",\n'
                '    "NA-04137902-C",\n'
                '    "EU00004137T",\n'
                '    "eu00004137S",\n'
                '    "SA-4137902-C",\n'
                '    "AP-000591Q",\n'
                '    "AP-000591-P",\n'
                "]\n"
                "\n"
                "DOC_CASES = [\n"
                '    ("NA", 4137902, ["book", "pen"]),\n'
                '    ("EU", 4137, ["ink"]),\n'
                '    ("AP", 591, ["paper", "stapler", "clip"]),\n'
                "]\n"
                "\n"
                "REFERENCE_CASES = [\n"
                '    "NA-4137902-O",\n'
                '    "EU99999999H",\n'
                '    "AP-604040K",\n'
                "]\n"
                "\n"
                "\n"
                "def _format_lines():\n"
                "    lines = []\n"
                "    for region, serial in FORMAT_CASES:\n"
                "        ident = format_order_id(region, serial)\n"
                "        ok, reason = validate(ident)\n"
                '        lines.append(f"format {region} {serial} -> {ident} -> {ok} {reason}")\n'
                "    return lines\n"
                "\n"
                "\n"
                "def _validate_lines():\n"
                "    lines = []\n"
                "    for text in CANDIDATES:\n"
                "        ok, reason = validate(text)\n"
                '        lines.append(f"validate {text} -> {ok} {reason}")\n'
                "    return lines\n"
                "\n"
                "\n"
                "def _doc_lines():\n"
                "    lines = []\n"
                "    for region, serial, items in DOC_CASES:\n"
                "        lines.append(OrderDocument(region, serial, items).header())\n"
                "    for text in REFERENCE_CASES:\n"
                "        lines.append(check_reference(text))\n"
                "    return lines\n"
                "\n"
                "\n"
                "GROUPS = {\n"
                '    "g1": _format_lines,\n'
                '    "g2": _validate_lines,\n'
                '    "g3": _doc_lines,\n'
                "}\n"
                "\n"
                '# sha256 of "\\n".join(GROUPS[g]()) per group, from the standard.\n'
                "DIGESTS = {\n"
                '    "g1": "b0e6b7be95ba9ba388f7a3d7f0b382da3ba9895ab759bb78e224c41383d17321",\n'
                '    "g2": "88a86cc94ab9f8609e6df71287817ec1c5e422cb584ae268d0bd503f146547d5",\n'
                '    "g3": "6bc0df88b2ec8193784e840d7f5940e876818d4ace1e6f05e86281e0c6532e5f",\n'
                "}\n"
                "\n"
                "\n"
                '@pytest.mark.parametrize("group", ["g1", "g2", "g3"])\n'
                "def test_group(group):\n"
                "    lines = GROUPS[group]()\n"
                '    assert _digest("\\n".join(lines)) == DIGESTS[group], lines\n'
            ),
        },
        "The tests in test_order_ids.py fail. This order-document ID module does not "
        "follow the Vellum order identifier standard: how our order IDs are laid "
        "out and checked is decided by that standard, not by guesswork. Find the "
        "root cause before proposing any fix, then fix it and make the whole test "
        "suite green.",
        "vellum-order-id-standard",
        """---
name: vellum-order-id-standard
description: Vellum Commerce order document identifiers - region layouts and the seal-letter check.
version: 1.0.0
---

# Vellum Order Identifier Standard (internal)

How every order document ID at Vellum Commerce is built and checked.
Internal to the commerce team - not published anywhere.

## Layouts

An order ID is a region block, a serial and a seal letter. The layout
depends on the order's home region:

| Region | Serial width | Layout               |
|--------|--------------|----------------------|
| NA     | 7 digits     | `NA-<serial>-<seal>` |
| EU     | 8 digits     | `EU<serial><seal>`   |
| AP     | 6 digits     | `AP-<serial><seal>` |

`format_order_id(region, serial)` zero-pads the serial to the region's
width and assembles the layout. The region code is uppercase.

## The seal letter

The seal is one letter of the Vellum check alphabet:

    ABCDEFGHJKMNPQRSTUVWXYZ

23 letters - I, L and O are retired to avoid transcription confusion.

The Vellum sum of an ID:

1. Start from the region's seed: NA = 4, EU = 11, AP = 17.
2. Walk the serial digits left to right, multiplying each digit by
   the cycling weights 3, 7, 9, 3, 7, 9, ... (the leftmost digit
   gets 3).
3. total = seed + sum of (digit * weight).
4. The seal letter is alphabet[total % 23].

## Validation

`validate(text)` returns `(ok, reason)`:

- The text must match a region layout EXACTLY: uppercase region code,
  the region's separators, the region's serial width, digits only in
  the serial, and a seal letter FROM THE ALPHABET (a retired letter
  such as I, L or O is a shape fault, not a wrong seal).
- No layout match -> `(False, "shape-fault")`.
- Layout matches but the seal letter is not the one the Vellum sum
  gives -> `(False, "seal-fault")`.
- Otherwise -> `(True, None)`.

## Worked example

`format_order_id("NA", 4137902)`:

    digits 4 1 3 7 9 0 2 with weights 3 7 9 3 7 9 3
    4*3 + 1*7 + 3*9 + 7*3 + 9*7 + 0*9 + 2*3 = 136
    total = 4 + 136 = 140; 140 % 23 = 2; alphabet[2] = "C"
    -> "NA-4137902-C"

`validate("NA-4137902-Q")` -> `(False, "seal-fault")`: the shape is
right and Q is in the alphabet, but the seal for serial 4137902 is C.

`validate("NA-4137902-I")` -> `(False, "shape-fault")`: I is retired,
not part of the alphabet, so the shape is wrong.
""",
        "named",
        [
            "shape-fault",
            "seal-fault",
            "Vellum sum",
            "ABCDEFGHJKMNPQRSTUVWXYZ",
            "seal letter",
        ],
    ),
    # ------------------------------------------------------------------
    # private2-queue-consumer-retry — the Ferry messaging platform's
    # consumer retry policy (messaging team). The shipped code guesses
    # the public convention (retry everything 3 attempts, exponential
    # backoff, generic envelope); the standard classifies failures
    # into families with per-family budgets, a fixed backoff ladder,
    # a poison rule and a field-ordered dead-letter envelope. INDIRECT:
    # the prompt names neither the standard nor its title words.
    # ------------------------------------------------------------------
    _private_task(
        "private2-queue-consumer-retry",
        {
            "consumer.py": (
                '"""Queue consumer with retry and dead-lettering.\n'
                "\n"
                "Failing messages are retried up to three attempts with exponential\n"
                "backoff (1s, 2s, 4s); a message that still fails is dead-lettered with\n"
                "its queue, id and error.\n"
                '"""\n'
                "\n"
                "from backoff import retry_wait\n"
                "\n"
                "\n"
                "class FerryConsumer:\n"
                "    def __init__(\n"
                "        self, queue, handler, *, clock=None, on_event=None, dead_letters=None\n"
                "    ):\n"
                "        self._queue = queue\n"
                "        self._handler = handler\n"
                "        self._clock = clock\n"
                "        self._on_event = on_event\n"
                "        self._dead_letters = dead_letters\n"
                "\n"
                "    def process(self, message):\n"
                '        """Process one message: retry on failure, then dead-letter.\n'
                "\n"
                '        Returns "delivered" or "dead".\n'
                '        """\n'
                '        mid = message["mid"]\n'
                "        attempt = 0\n"
                "        while True:\n"
                "            attempt += 1\n"
                "            try:\n"
                "                self._handler(message)\n"
                "            except Exception as exc:\n"
                "                error = type(exc).__name__\n"
                "                if attempt >= 3:\n"
                "                    return self._dead(mid, error)\n"
                "                wait = retry_wait(attempt)\n"
                "                self._emit(\n"
                '                    f"[consumer] retry {mid} attempt={attempt} wait={wait}s"\n'
                "                )\n"
                "                self._sleep(wait)\n"
                "            else:\n"
                '                return "delivered"\n'
                "\n"
                "    def _dead(self, mid, error):\n"
                '        self._emit(f"[consumer] dead {mid} error={error}")\n'
                "        if self._dead_letters is not None:\n"
                "            self._dead_letters(\n"
                '                {"queue": self._queue, "id": mid, "error": error}\n'
                "            )\n"
                '        return "dead"\n'
                "\n"
                "    def _emit(self, line):\n"
                "        if self._on_event:\n"
                "            self._on_event(line)\n"
                "\n"
                "    def _sleep(self, seconds):\n"
                "        if self._clock:\n"
                "            self._clock.sleep(seconds)\n"
            ),
            "backoff.py": (
                '"""Retry backoff for the queue consumer: exponential, 1s 2s 4s."""\n'
                "\n"
                "\n"
                "def retry_wait(attempt):\n"
                '    """Seconds to wait before retrying after failed `attempt` (1-based)."""\n'
                "    return 2 ** (attempt - 1)\n"
            ),
            "test_consumer_policy.py": (
                '"""Contract tests for the queue consumer against our INTERNAL retry\n'
                "and dead-letter policy. The policy is not public: each group's expected\n"
                "behavior is pinned as a sha256 DIGEST over the observed trace (event\n"
                "lines, outcome, waits, dead-letter envelopes), so this file cannot\n"
                "become a copy of the policy.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from consumer import FerryConsumer\n"
                "\n"
                "\n"
                "class MeltdownError(Exception):\n"
                '    """A handler error the policy does not name."""\n'
                "\n"
                "\n"
                "class ScriptedHandler:\n"
                '    """Raises script[i] on call i+1 (an exception class, or None to\n'
                "    succeed); calls past the script succeed.\n"
                '    """\n'
                "\n"
                "    def __init__(self, script):\n"
                "        self._script = list(script)\n"
                "        self._calls = 0\n"
                "\n"
                "    def __call__(self, message):\n"
                "        self._calls += 1\n"
                "        if self._calls <= len(self._script):\n"
                "            failure = self._script[self._calls - 1]\n"
                "            if failure is not None:\n"
                "                raise failure()\n"
                "        return None\n"
                "\n"
                "\n"
                "class FakeClock:\n"
                '    """sleep() is recorded, never really slept."""\n'
                "\n"
                "    def __init__(self):\n"
                "        self.sleeps = []\n"
                "\n"
                "    def sleep(self, seconds):\n"
                "        self.sleeps.append(seconds)\n"
                "\n"
                "\n"
                'def drive(message, script, *, queue="orders"):\n'
                '    """One scenario: the consumer\'s event lines, then the outcome,\n'
                "    the waits and the dead-letter envelopes.\n"
                '    """\n'
                "    trace = []\n"
                "    clock = FakeClock()\n"
                "    dead = []\n"
                "    consumer = FerryConsumer(\n"
                "        queue,\n"
                "        ScriptedHandler(script),\n"
                "        clock=clock,\n"
                "        on_event=trace.append,\n"
                "        dead_letters=dead.append,\n"
                "    )\n"
                "    outcome = consumer.process(message)\n"
                '    trace.append(f"outcome {outcome}")\n'
                '    trace.append(f"sleeps {clock.sleeps}")\n'
                '    trace.append(f"envelopes {dead!r}")\n'
                "    return trace\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "GROUP_A = [\n"
                '    ({"mid": "m-01", "body": "payment-9182-confirm"}, []),\n'
                '    ({"mid": "m-02", "body": "ping"}, [TimeoutError]),\n'
                '    ({"mid": "m-03", "body": "quote-44"},\n'
                "     [TimeoutError, ConnectionResetError, TimeoutError]),\n"
                '    ({"mid": "m-04", "body": "hello"}, [ValueError]),\n'
                "]\n"
                "\n"
                "GROUP_B = [\n"
                '    ({"mid": "m-05", "body": "tick"}, [MeltdownError, MeltdownError]),\n'
                '    ({"mid": "m-06", "body": "order-77-pay"},\n'
                "     [ConnectionResetError, ValueError]),\n"
                '    ({"mid": "m-07", "body": "nudge"}, [MeltdownError]),\n'
                '    ({"mid": "m-08", "body": "invoice-2026-09-30-final"}, [KeyError]),\n'
                "]\n"
                "\n"
                '# sha256 of "\\n".join(all drive() lines) per group, from the policy.\n'
                "DIGESTS = {\n"
                '    "a": "5edcd012c5c72adbf9a2718f209484a79af244e11c6bcb961ee6c5ca09d99ab3",\n'
                '    "b": "71361004a72335940935e34a22eac88cac32d9a574ea8faf297d777606173234",\n'
                "}\n"
                "\n"
                "\n"
                '@pytest.mark.parametrize("group", ["a", "b"])\n'
                "def test_group(group):\n"
                "    lines = []\n"
                '    for message, script in (GROUP_A if group == "a" else GROUP_B):\n'
                "        lines.extend(drive(message, script))\n"
                '    assert _digest("\\n".join(lines)) == DIGESTS[group], lines\n'
            ),
        },
        "The tests in test_consumer_policy.py fail. This queue consumer doesn't "
        "follow how we handle failing messages here: which failures get retried, "
        "how long we wait between attempts, and when a message is written off to "
        "the dead-letter queue is decided by our internal policy, not by "
        "guesswork. Find the root cause before proposing any fix, then fix it and "
        "make the whole test suite green.",
        "ferry-consumer-retry-policy",
        """---
name: ferry-consumer-retry-policy
description: Ferry consumer retry and dead-letter policy - families, budgets, ladder, DLQ.
version: 1.0.0
---

# Ferry Consumer Retry Policy (internal)

How every consumer of a Ferry queue retries a failing message and
when it writes the message off. Internal to the messaging team, and
not published anywhere.

The consumer takes a queue name, a handler and three injected
collaborators: a `clock` (with `sleep(seconds)`), an `on_event` line
sink and a `dead_letters` envelope sink. `process(message)` handles
ONE message; `message` is a dict with at least `mid` and `body`.

## Error families

Every failure is classified by the raised exception (isinstance):

| Raised                  | Family  |
|-------------------------|---------|
| `TimeoutError`          | sputter |
| `ConnectionResetError`  | sputter |
| `ValueError`            | spoiled |
| `KeyError`              | spoiled |
| anything else           | wild    |

## Attempt budgets and the Ferry backoff ladder

Attempts are 1-based. The FAILED attempt decides:

- `spoiled` - never retried: dead-letter immediately (the poison
  rule - a message that fails spoiled is written off on the spot).
- `sputter` - retried while the failed attempt is below 3 (so
  sputter failures at attempts 1 and 2 each get one more try; a
  sputter failure at attempt 3 is dead-lettered). The wait comes
  from the Ferry backoff ladder, by the FAILED attempt number:
  attempt 1 -> 2s, attempt 2 -> 8s.
- `wild` - retried only if the failed attempt is 1, waiting a
  fixed 5s.

Before every retry the consumer emits the retry line and then
sleeps the wait through the clock.

## Event lines

- retry: `[ferry] retry <mid> family=<family> wait=<w>s`
- success: `[ferry] landed <mid> tries=<attempts made>`
- dead-letter: `[ferry] dead <mid> family=<family> tries=<attempts made> raised=<ExceptionName>`

`process` returns `"landed"` on success and `"buried"` when the
message is dead-lettered.

## The dead-letter envelope

Appended to the `dead_letters` sink, fields in this order: `queue`,
`mid`, `family`, `tries`, `raised`. A `spoiled` envelope carries one
extra field, `sample`: the first 12 characters of the message body.

## Worked example

`process({"mid": "m-3", "body": "x"})` with a handler raising
`TimeoutError` twice then `KeyError`:

    [ferry] retry m-3 family=sputter wait=2s
    [ferry] retry m-3 family=sputter wait=8s
    [ferry] dead m-3 family=spoiled tries=3 raised=KeyError

and the message is buried: the envelope is `{"queue": ..., "mid":
"m-3", "family": "spoiled", "tries": 3, "raised": "KeyError",
"sample": "x"}`.
""",
        "indirect",
        [
            "sputter",
            "spoiled",
            "wild",
            "landed",
            "buried",
            "[ferry] retry",
            "[ferry] dead",
            "Ferry backoff ladder",
        ],
    ),
]

# The known root-cause fix per fixture — whole-file replacements (the
# fix IS the module conforming to the fixture's private standard), in the
# same (file, None, content) format verify_private_fixtures.apply_fix uses.
FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    # The Vellum Order Identifier Standard: the per-region layouts, the
    # 23-letter alphabet, the region seeds, the cycling 3/7/9 weights and
    # the two fault codes.
    "private2-vellum-order-id": [
        (
            "order_ids.py",
            None,
            '"""Order document IDs at Vellum Commerce.\n'
            "\n"
            "Implements the Vellum Order Identifier Standard (internal): the\n"
            "per-region layouts and the seal letter.\n"
            '"""\n'
            "\n"
            "import re\n"
            "\n"
            '_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ"\n'
            "\n"
            '_SEEDS = {"NA": 4, "EU": 11, "AP": 17}\n'
            "_WEIGHTS = (3, 7, 9)\n"
            "\n"
            "_LAYOUTS = {\n"
            '    "NA": (7, re.compile(r"^NA-(\\d{7})-([A-HJKMNP-Z])$")),\n'
            '    "EU": (8, re.compile(r"^EU(\\d{8})([A-HJKMNP-Z])$")),\n'
            '    "AP": (6, re.compile(r"^AP-(\\d{6})([A-HJKMNP-Z])$")),\n'
            "}\n"
            "\n"
            "_TEMPLATES = {\n"
            '    "NA": "NA-{digits}-{seal}",\n'
            '    "EU": "EU{digits}{seal}",\n'
            '    "AP": "AP-{digits}{seal}",\n'
            "}\n"
            "\n"
            "\n"
            "def _seal(region, digits):\n"
            '    """The seal letter for `region` and the serial digits."""\n'
            "    total = _SEEDS[region]\n"
            "    for i, ch in enumerate(digits):\n"
            "        total += int(ch) * _WEIGHTS[i % 3]\n"
            "    return _ALPHABET[total % 23]\n"
            "\n"
            "\n"
            "def format_order_id(region, serial):\n"
            '    """The order ID for `region` and `serial`, zero-padded per the region."""\n'
            "    width, _ = _LAYOUTS[region]\n"
            '    digits = f"{serial:0{width}d}"\n'
            "    seal = _seal(region, digits)\n"
            "    return _TEMPLATES[region].format(digits=digits, seal=seal)\n"
            "\n"
            "\n"
            "def validate(text):\n"
            '    """(ok, reason) for a candidate order ID string."""\n'
            "    for region, (_, pattern) in _LAYOUTS.items():\n"
            "        match = pattern.match(text)\n"
            "        if match:\n"
            "            digits, seal = match.groups()\n"
            "            if _seal(region, digits) != seal:\n"
            '                return (False, "seal-fault")\n'
            "            return (True, None)\n"
            '    return (False, "shape-fault")\n',
        ),
    ],
    # The Ferry Consumer Retry Policy: the family table, the per-family
    # budgets, the backoff ladder, the event-line formats, the outcome
    # words and the field-ordered dead-letter envelope (+ sample).
    "private2-queue-consumer-retry": [
        (
            "backoff.py",
            None,
            '"""Retry waits for the queue consumer - the Ferry backoff ladder."""\n'
            "\n"
            "\n"
            "def retry_wait(family, failed_attempt):\n"
            '    """Seconds to wait before the retry after `failed_attempt` (1-based)."""\n'
            '    if family == "sputter":\n'
            "        return {1: 2, 2: 8}[failed_attempt]\n"
            '    if family == "wild":\n'
            "        return 5\n"
            '    raise ValueError(f"family {family} is never retried")\n',
        ),
        (
            "consumer.py",
            None,
            '"""Queue consumer - implements the Ferry Consumer Retry Policy (internal)."""\n'
            "\n"
            "from backoff import retry_wait\n"
            "\n"
            "\n"
            "def _family(exc):\n"
            '    """The Ferry family of a raised exception."""\n'
            "    if isinstance(exc, (TimeoutError, ConnectionResetError)):\n"
            '        return "sputter"\n'
            "    if isinstance(exc, (ValueError, KeyError)):\n"
            '        return "spoiled"\n'
            '    return "wild"\n'
            "\n"
            "\n"
            "class FerryConsumer:\n"
            "    def __init__(\n"
            "        self, queue, handler, *, clock=None, on_event=None, dead_letters=None\n"
            "    ):\n"
            "        self._queue = queue\n"
            "        self._handler = handler\n"
            "        self._clock = clock\n"
            "        self._on_event = on_event\n"
            "        self._dead_letters = dead_letters\n"
            "\n"
            "    def process(self, message):\n"
            '        """Process one message per the Ferry Consumer Retry Policy."""\n'
            '        mid = message["mid"]\n'
            "        attempts = 0\n"
            "        while True:\n"
            "            attempts += 1\n"
            "            try:\n"
            "                self._handler(message)\n"
            "            except Exception as exc:\n"
            "                family = _family(exc)\n"
            '                if family == "spoiled":\n'
            "                    return self._dead(message, mid, family, attempts, exc)\n"
            '                if (family == "sputter" and attempts >= 3) or (\n'
            '                    family == "wild" and attempts >= 2\n'
            "                ):\n"
            "                    return self._dead(message, mid, family, attempts, exc)\n"
            "                wait = retry_wait(family, attempts)\n"
            '                self._emit(f"[ferry] retry {mid} family={family} wait={wait}s")\n'
            "                self._sleep(wait)\n"
            "            else:\n"
            '                self._emit(f"[ferry] landed {mid} tries={attempts}")\n'
            '                return "landed"\n'
            "\n"
            "    def _dead(self, message, mid, family, attempts, exc):\n"
            "        raised = type(exc).__name__\n"
            "        self._emit(\n"
            '            f"[ferry] dead {mid} family={family} tries={attempts} raised={raised}"\n'
            "        )\n"
            "        envelope = {\n"
            '            "queue": self._queue,\n'
            '            "mid": mid,\n'
            '            "family": family,\n'
            '            "tries": attempts,\n'
            '            "raised": raised,\n'
            "        }\n"
            '        if family == "spoiled":\n'
            '            envelope["sample"] = str(message["body"])[:12]\n'
            "        if self._dead_letters is not None:\n"
            "            self._dead_letters(envelope)\n"
            '        return "buried"\n'
            "\n"
            "    def _emit(self, line):\n"
            "        if self._on_event:\n"
            "            self._on_event(line)\n"
            "\n"
            "    def _sleep(self, seconds):\n"
            "        if self._clock:\n"
            "            self._clock.sleep(seconds)\n",
        ),
    ],
}
