"""The E2D skill-format experiment — the ACTIONABLE v2 texts (--set private2).

Job e2d-format (2026-10-02): does HOW a private standard is written change
outcomes — rules-as-prose (v1, the builder modules' ``skill``) vs the SAME
rules restructured for action (v2, this module)? Every v2 text carries
EXACTLY the same rules as its fixture's v1 (no rule added, removed or
changed — verified rule-by-rule by an independent reviewer subagent that
did NOT write the v2), restructured as:

1. a short "What to change" checklist — ordered, imperative implementation
   steps naming the workspace modules;
2. the rules as compact tables/pseudocode;
3. 2-4 worked examples input -> output whose INPUTS are DIFFERENT from
   every test input (never a test's expected value — the tests pin sha256
   digests over whole traces, so no example can shortcut one; every example
   OUTPUT below was computed against the fixture's known-good FIXES
   implementation, never hand-derived);
4. a "How to verify locally" line (run the test suite; do not brute-force
   the digests).

Same frontmatter ``name``/``description`` as v1; ``version: 2.0.0``; each
text ≤ ~1,800 tokens (chars/4 estimate, the kernel's own heuristic).

Scoping notes (pinned by tests/unit/test_e2d_format.py):
- Every standard-only MARKER of the fixture appears in its v2 (the markers
  are the standard's vocabulary; two markers pin the canonical v1 example —
  drawbridge's ``B37334`` and palisade's ``zip=60###`` — so those canonical
  examples are carried over verbatim from v1; their inputs are NOT test
  inputs: ``u-42`` is no test user, and none of the palisade example record's
  values appear in the test records).
- The no-test-input check covers the worked examples' SCENARIO DATA
  (mids, bodies, flag keys, user ids, serials, currency+amount pairs,
  timestamps, record values, config keys/values, dates). Exception class
  names in the ferry examples are RULE VOCABULARY, not scenario data: the
  family table itself names TimeoutError/ConnectionResetError (sputter) and
  ValueError/KeyError (spoiled), so any concrete ferry example must name
  one — the (message, script) test pairs are what the digests pin, and no
  v2 example pair equals a test pair.

§34 caveat applies to any round run with these texts: small n, one model,
author-written v2 — directional only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: The version the v2 texts carry (frontmatter + what arm R2 serves).
PRIVATE2_SKILL_V2_VERSION = "2.0.0"

_CAIRN_MONEY_V2 = """---
name: cairn-money-standard
description: Cairn money standard - currency classes, rounding, rendering, fiscal period ids.
version: 2.0.0
---

# Cairn Money Standard (internal)

How the finance platform rounds and renders every money amount, and how
a timestamp maps to a fiscal period id. Internal to the finance
platform team - not published anywhere.

## What to change

1. In `cairn_money.py`, add the Cairn currency table below. Look the code
   up EXACTLY as given - no case folding, no trimming; a code not in the
   table raises `CairnUnknownCurrency` (fail closed).
2. Implement `round_amount(currency, value)`: quantize the Decimal to
   the class decimals with the class rounding mode.
3. Implement `render_amount(currency, value)`: render the ROUNDED amount
   with exactly the class decimals - plain ASCII digits, a `.` before
   the fractional digits only when decimals > 0, `-` for negatives, no
   thousands separators. A negative that rounds to zero renders
   WITHOUT the sign (never `-0.00`).
4. In `cairn_periods.py`, rewrite `fiscal_period_id(ts)` per the fiscal
   calendar below.

## The Cairn currency table

| Class | Codes | Decimals | Rounding |
|-------|-------|----------|----------|
| MAJOR | USD EUR GBP CHF CAD AUD | 2 | HALF_UP |
| UNIT | JPY KRW VND CLP TWD | 0 | HALF_UP |
| METAL | XAU XAG XPT | 4 | HALF_DOWN |
| CHAIN | BTC ETH SOL | 8 | DOWN (truncate) |

## The fiscal calendar

- The Cairn fiscal year starts March 1. Twelve calendar-month periods:
  P01 = March ... P12 = February.
- The fiscal year label is the calendar year in which the fiscal year
  STARTS (the March's year).
- `fiscal_period_id(ts)` returns `FY{yyyy}-P{pp}` with a zero-padded
  2-digit period.
- All timestamps are UTC: naive datetimes ARE UTC; aware ones are
  converted to UTC first. The period start instant is inclusive.

    month >= 3  ->  period = month - 2,  fiscal year = ts.year
    month <= 2  ->  period = month + 10, fiscal year = ts.year - 1

## Worked examples

    render_amount("EUR", Decimal("5.678")) -> "5.68"      (MAJOR, 2, HALF_UP)
    render_amount("JPY", Decimal("88.5")) -> "89"          (UNIT, 0 decimals)
    render_amount("XAG", Decimal("0.12345")) -> "0.1234"  (METAL, HALF_DOWN at the tie)
    render_amount("BTC", Decimal("1.234567891")) -> "1.23456789"  (CHAIN, truncate)
    render_amount("USD", Decimal("-0.002")) -> "0.00"      (negative rounding to zero: no sign)
    render_amount("XYZ", Decimal("2.00")) -> raises CairnUnknownCurrency
    fiscal_period_id(datetime(2026, 8, 9, tzinfo=UTC)) -> "FY2026-P06"
    fiscal_period_id(datetime(2025, 1, 20, tzinfo=UTC)) -> "FY2024-P11"
    fiscal_period_id(datetime(2026, 2, 14, tzinfo=UTC)) -> "FY2025-P12"

## How to verify locally

Run the test suite (`python -m pytest -q`). The tests pin sha256 digests
over whole traces - implement the rules above; do not brute-force the
digests.
"""

_DRAWBRIDGE_V2 = """---
name: drawbridge-rollout-standard
description: Drawbridge rollout standard - feature-flag user bucketing and assignment precedence.
version: 2.0.0
---

# Drawbridge Rollout Standard (internal)

How the release-engineering team buckets every user for a feature-flag
rollout and resolves flag assignment. Internal to the release
engineering team - not published anywhere.

## What to change

1. In `rollout_bucket.py`, implement the Drawbridge mix (below) in
   `bucket_of(flag_key, user_id)`.
2. In `rollout_assign.py`, rewrite `assign(config, user_id)` to resolve
   in THIS order: the kill-switch first (absolute), then the allow-list
   (exact string match), then the percentage bucket.

## The Drawbridge mix (bucketing)

`bucket_of(flag_key, user_id)` returns the user's bucket id for the
flag: `B` + 5 zero-padded digits (0..99999). The bucket number is:

    int(sha256(f"{flag_key}::{user_id}::db-mix-01".encode("utf-8"))
        .hexdigest()[:13], 16) % 100_000

- The mixing token `db-mix-01` is part of the recipe - fixed, never
  per-flag, never changed.
- Both the flag key and the user id are hashed byte-for-byte as given:
  no trimming, no case folding ("Ada-L" and "ada-l" are different
  users).

## Assignment (resolution order)

`assign(config, user_id)` returns exactly one verdict string. The
config carries `key` (the flag key), `kill` (bool), `allow` (list of
user ids), `percent` (integer 0..100). Resolve in THIS order:

    1. kill truthy             -> HELD-KILL      (EVERYONE - the allow-list
                                                     included; the drawbridge
                                                     is up, nothing crosses)
    2. user_id in allow        -> ENROLLED-ALLOW (exact string match; the
                                                     allow-list bypasses the
                                                     percentage)
    3. bucket < percent * 1000 -> ENROLLED
       otherwise               -> HELD-BUCKET

`percent` is per-mille over the 100_000 buckets: percent=10 enrolls
buckets 0..9999; percent=100 enrolls everyone (max bucket 99999).

## Worked examples

    bucket_of("checkout-turbo", "u-42") -> "B37334"
    bucket_of("billing-spark", "u-77") -> "B27586"

    flag "billing-spark", user "u-77" (bucket number 27586):
    - kill=True,  allow=["u-77"], percent=100 -> HELD-KILL (kill beats the allow-list)
    - kill=False, allow=["u-77"], percent=0   -> ENROLLED-ALLOW
    - kill=False, allow=[],       percent=50  -> ENROLLED     (27586 < 50000)
    - kill=False, allow=[],       percent=10  -> HELD-BUCKET  (27586 >= 10000)

## How to verify locally

Run the test suite (`python -m pytest -q`). The tests pin sha256 digests
over whole traces - implement the rules above; do not brute-force the
digests.
"""

_PALISADE_V2 = """---
name: palisade-log-hygiene
description: Palisade log redaction rules - field classes, redaction modes, exact formats.
version: 2.0.0
---

# Palisade Log Redaction Rules (internal)

How a service log line is rendered from a record before the line is
written. Internal to the Palisade security team - not published
anywhere.

`redact_record(record)` renders ONE line from a record: a mapping of
field name to string value.

## What to change

1. In `field_classes.py`, classify each field name per the class table
   below (any other field name is class FREE).
2. In `log_filter.py`, apply the per-class mode to each value, drop the
   dropped fields, then render the kept fields in sorted key order.

## Field classes and modes

| Field      | Class | Mode    |
|------------|-------|---------|
| email      | IDENT | MASK    |
| phone      | IDENT | MASK    |
| card       | PAN   | HASH    |
| token      | CRED  | DROP    |
| password   | CRED  | DROP    |
| zip        | QUASI | COARSEN |
| birth_year | QUASI | COARSEN |
| (other)    | FREE  | KEEP    |

The modes, exactly:

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

## Worked examples

    {"event": "login", "email": "jo.reyes@example.com",
     "phone": "+1-312-555-0177", "zip": "60614",
     "note": "signed in from mobile"}
    -> "email=jo\u2026om | event=login | note=signed in from mobile | phone=+1\u202677 | zip=60###"

    {"card": "6011111111111117", "event": "charge"}
    -> "card=pan:19ff47cc8024 | event=charge"

    {"token": "tok_q9w8", "phone": "9x7", "event": "rotate"}
    -> "event=rotate"     (token DROPped by class; phone shorter than 5 chars DROPped)

    {"birth_year": "2001", "event": "signup"}
    -> "birth_year=20## | event=signup"

## How to verify locally

Run the test suite (`python -m pytest -q`). The tests pin sha256 digests
over whole rendered lines - implement the rules above; do not
brute-force the digests.
"""

_CAIRN_SUNSET_V2 = """---
name: cairn-api-sunset-policy
description: Cairn API sunset rules - version states, response headers, dates and the notice floor.
version: 2.0.0
---

# Cairn Sunset Rules (internal)

How every response from a versioned API must be decorated before it is
returned. Internal to the Cairn platform API team - not published
anywhere.

`decorate(response, version_info, now)` returns the decorated response.
`response` is `{"status": <int>, "headers": {name: value}, "body": <str>}`.
`version_info` is the version's registry entry: `version`,
`introduced_on`, `deprecated_on`, `sunset_on`, `removed_on` (dates;
`None` = not set). `now` is the request date.

## What to change

1. In `lifecycle.py`, compute the state from `now` (every boundary
   INCLUSIVE) with the minimum-notice floor.
2. In `versioning.py`, decorate the response per the state table below;
   render every date in a header value as YYYYMMDD (compact, no
   separators), e.g. 20260901.

## States

    deprecated_on unset, or now < deprecated_on  -> STEADY
    deprecated_on <= now < sunset_eff           -> NOTICE
    sunset_eff <= now < removed_on              -> FINAL
    removed_on <= now                           -> GONE

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

## Worked examples

    version_info = {"version": "v4",
                    "introduced_on": date(2025, 4, 12),
                    "deprecated_on": date(2026, 4, 1),
                    "sunset_on": date(2026, 12, 1),
                    "removed_on": date(2027, 6, 1)}

    now = date(2026, 3, 2): STEADY (before the deprecation) - the
    response passes through with its own status and gains only
    X-Cairn-Version: v4.

    now = date(2026, 5, 5): NOTICE (2026-04-01 <= now < 2026-12-01;
    sunset_eff = 2026-12-01, later than the 2026-09-28 floor). The
    response gains:

        X-Cairn-Version: v4
        X-Cairn-Deprecation: 20260401
        X-Cairn-Sunset: 20261201

    now = date(2027, 1, 7): FINAL (2026-12-01 <= now < 2027-06-01).
    The response gains:

        X-Cairn-Version: v4
        X-Cairn-Sunset: 20261201
        X-Cairn-Removal: 20270601

    now = date(2027, 7, 4): GONE (>= 2027-06-01) - the response is
    REPLACED: status 410, headers exactly X-Cairn-Version: v4 and
    X-Cairn-Removal: 20270601, empty body.

    The floor binding: with deprecated_on 2026-04-01 and sunset_on
    2026-06-15, sunset_eff is 2026-09-28 (deprecated_on + 180 days is
    later than sunset_on) - at now = date(2026, 7, 1) the state is
    NOTICE and the header is X-Cairn-Sunset: 20260928.

## How to verify locally

Run the test suite (`python -m pytest -q`). The tests pin sha256
digests over whole decorated responses - implement the rules above;
do not brute-force the digests.
"""

_VELLUM_V2 = """---
name: vellum-order-id-standard
description: Vellum Commerce order document identifiers - region layouts and the seal-letter check.
version: 2.0.0
---

# Vellum Order Identifier Standard (internal)

How every order document ID at Vellum Commerce is built and checked.
Internal to the commerce team - not published anywhere.

## What to change

1. In `order_ids.py`, replace the Luhn-style check digit with the Vellum
   seal letter (below) and the single universal layout with the
   per-region layouts.
2. `format_order_id(region, serial)`: zero-pad the serial to the
   region's width, assemble the region's layout, append the seal
   letter. The region code is uppercase.
3. `validate(text)`: match the text against a region layout EXACTLY,
   compute the seal, and return the (ok, reason) pair per the fault
   rules below.

## Layouts

| Region | Serial width | Layout               |
|--------|--------------|----------------------|
| NA     | 7 digits     | `NA-<serial>-<seal>` |
| EU     | 8 digits     | `EU<serial><seal>`   |
| AP     | 6 digits     | `AP-<serial><seal>`  |

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

## Worked examples

    format_order_id("NA", 8642):
        digits 0 0 0 8 6 4 2 with weights 3 7 9 3 7 9 3
        0*3 + 0*7 + 0*9 + 8*3 + 6*7 + 4*9 + 2*3 = 108
        total = 4 + 108 = 112; 112 % 23 = 20; alphabet[20] = "X"
        -> "NA-0008642-X"

    format_order_id("EU", 130579) -> "EU00130579X"
    format_order_id("AP", 42)     -> "AP-000042U"

    validate("NA-0008642-K") -> (False, "seal-fault"): the shape is
    right and K is in the alphabet, but the seal for serial 0008642
    is X.
    validate("NA-0008642-I") -> (False, "shape-fault"): I is retired,
    not part of the alphabet, so the shape is wrong.

## How to verify locally

Run the test suite (`python -m pytest -q`). The tests pin sha256
digests over whole traces - implement the rules above; do not
brute-force the digests.
"""

_FERRY_V2 = """---
name: ferry-consumer-retry-policy
description: Ferry consumer retry and dead-letter policy - families, budgets, ladder, DLQ.
version: 2.0.0
---

# Ferry Consumer Retry Policy (internal)

How every consumer of a Ferry queue retries a failing message and
when it writes the message off. Internal to the messaging team, and
not published anywhere.

The consumer takes a queue name, a handler and three injected
collaborators: a `clock` (with `sleep(seconds)`), an `on_event` line
sink and a `dead_letters` envelope sink. `process(message)` handles
ONE message; `message` is a dict with at least `mid` and `body`.

## What to change

1. Classify every failure by the raised exception (isinstance) into its
   family (the table below).
2. Apply the per-family attempt budget and wait (attempts are 1-based;
   the FAILED attempt decides).
3. Emit the event lines in the exact formats below; `process` returns
   "landed" on success and "buried" when the message is dead-lettered.
4. Append the dead-letter envelope with the fields in order; a
   `spoiled` envelope carries one extra field.

## Error families

| Raised                 | Family  |
|------------------------|---------|
| `TimeoutError`         | sputter |
| `ConnectionResetError` | sputter |
| `ValueError`           | spoiled |
| `KeyError`             | spoiled |
| anything else          | wild    |

## Attempt budgets and the Ferry backoff ladder

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

## The dead-letter envelope

Appended to the `dead_letters` sink, fields in this order: `queue`,
`mid`, `family`, `tries`, `raised`. A `spoiled` envelope carries one
extra field, `sample`: the first 12 characters of the message body.

## Worked examples

    process({"mid": "m-77", "body": "settle-9"}), handler raising
    TimeoutError twice then succeeding:

        [ferry] retry m-77 family=sputter wait=2s
        [ferry] retry m-77 family=sputter wait=8s
        [ferry] landed m-77 tries=3
    -> "landed"; the clock sleeps [2, 8]; no envelope.

    process({"mid": "m-78", "body": "probe-2"}), handler raising
    ValueError once (spoiled - the poison rule, no retry):

        [ferry] dead m-78 family=spoiled tries=1 raised=ValueError
    -> "buried"; envelope {"queue": ..., "mid": "m-78", "family":
    "spoiled", "tries": 1, "raised": "ValueError", "sample":
    "probe-2"}.

    process({"mid": "m-79", "body": "audit-3"}), handler raising
    RuntimeError once then succeeding (wild - only attempt 1
    retries, fixed 5s):

        [ferry] retry m-79 family=wild wait=5s
        [ferry] landed m-79 tries=2
    -> "landed"; the clock sleeps [5].

    process({"mid": "m-80", "body": "claim-4"}), handler raising
    ConnectionResetError three times (a sputter failure at attempt
    3 is dead-lettered):

        [ferry] retry m-80 family=sputter wait=2s
        [ferry] retry m-80 family=sputter wait=8s
        [ferry] dead m-80 family=sputter tries=3 raised=ConnectionResetError
    -> "buried"; envelope WITHOUT `sample` (only spoiled carries it).

## How to verify locally

Run the test suite (`python -m pytest -q`). The tests pin sha256
digests over whole traces - implement the rules above; do not
brute-force the digests.
"""

_HARBOR_V2 = """---
name: harbor-config-resolution
description: Harbor infra config resolution - precedence, key normalization, env prefixing, secrets.
version: 2.0.0
---

# Harbor Config Resolution Standard (internal)

How every service on the Harbor infra team resolves configuration.
Internal to the Harbor infra team - not published anywhere.

A resolver merges four sources, by name: `defaults`, `file`, `profile`,
`env`. Values are compared and reported as strings (a non-string is
rendered with `str()`).

## What to change

1. In `sources.py`, replace the constants with the standard's: the
   mooring order (below) as ascending precedence, the gangway prefix
   `HBR_`, the sealed segments and the sealed render `[hbr-redacted]`.
2. In `config_resolver.py`, fold every key from
   `defaults`/`file`/`profile` to keel form before any comparison,
   merge or report; map environment variables by the gangway rule;
   merge by the mooring order; render the manifest lines with the
   sealed values replaced.

## The mooring order (precedence)

Highest first: **profile, env, file, defaults**. A key's value is the
one from its highest source. A profile overlay outranks the
environment: a profile is the deploy-time override of record, and
environment variables are a developer convenience.

## Keel form (key normalization)

Before any comparison, merge or report, every key from
`defaults`/`file`/`profile` is folded to keel form:

- lowercase (ASCII),
- every `_` and every `.` becomes `-`,
- runs of consecutive separators collapse to one,
- leading and trailing separators are stripped.

`Server.Host_Name`, `SERVER__HOST_NAME__` and `server-host-name` are
one key. A key that folds to the empty string is dropped. Keel form
never contains `_`, `.` or an edge separator.

## The gangway rule (environment variables)

An environment variable belongs to the config iff its name starts
with the uppercase prefix `HBR_` (case-sensitive). Take the name
after the prefix, lowercase it, and turn every `__` (double
underscore) into one `-`. If a `_` remains, the variable is not ours
and is ignored. Then the same collapse-and-strip cleanup as keel
form applies; a result that folds to empty is dropped.

`HBR_API__TOKEN` -> `api-token`. `HBR_API_TOKEN` is ignored (a lone
`_` is not a separator). `hbr_api__token` is ignored (the prefix is
case-sensitive).

## Sealed keys (secrets)

A key is sealed iff its FINAL segment (after the last `-`) is `token`
or `secret`. `password` is not sealed: passwords are owned by the
auth subsystem, and a `password` key in config is a local dev
placeholder, not a credential. Plurals are not sealed either:
`api-tokens` and `rotation-secrets` are reported in the clear.

A sealed value is never reported: the manifest line shows the
literal `[hbr-redacted]` as the value.

## The manifest line (report)

The report is one line per resolved key, keys in ascending
lexicographic order, each line exactly:

    <key>=<value>@<source>

`<source>` is the winning source's name (`defaults`, `file`,
`profile`, `env`). Sealed keys report `[hbr-redacted]` as the value;
the source is still shown.

## Worked examples

    defaults {"Pool_Size": "15"}, file {"pool.size": "30"},
    profile {"POOL_SIZE": "45"}, env {"HBR_POOL__SIZE": "60",
    "HBR_POOL_SIZE": "7", "hbr_pool__size": "8"}:

    - keel form: the first three spellings all fold to `pool-size`;
    - gangway: only HBR_POOL__SIZE is ours (a lone `_` remains in
      HBR_POOL_SIZE; the lowercase prefix is not `HBR_`);
    - mooring order: profile (45) outranks env (60);
    - the report is exactly one line: `pool-size=45@profile`.

    defaults {"admin_password": "pw-1", "api_token": "tok-1",
              "session_tokens": "st-1", "vault_secret": "vs-1"}:

    admin-password=pw-1@defaults          (password is NOT sealed)
    api-token=[hbr-redacted]@defaults    (final segment "token")
    session-tokens=st-1@defaults         (plural - not sealed)
    vault-secret=[hbr-redacted]@defaults  (final segment "secret")

## How to verify locally

Run the test suite (`python -m pytest -q`). The tests pin sha256
digests over whole reports - implement the rules above; do not
brute-force the digests.
"""

#: fixture name -> its v2 skill text (the same standard, restructured for
#: action; arm R2 serves this, arm R serves the builder module's v1).
PRIVATE2_SKILL_V2: dict[str, str] = {
    "private2-cairn-money": _CAIRN_MONEY_V2,
    "private2-drawbridge-rollout": _DRAWBRIDGE_V2,
    "private2-palisade-redaction": _PALISADE_V2,
    "private2-cairn-sunset": _CAIRN_SUNSET_V2,
    "private2-vellum-order-id": _VELLUM_V2,
    "private2-queue-consumer-retry": _FERRY_V2,
    "private2-infra-config": _HARBOR_V2,
}
