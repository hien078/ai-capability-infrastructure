"""Private-knowledge fixtures for the DENSE-corpus skill axis (rc-bench
follow-up, ADR-014 amendment 30) - builder "c", 2026-10-03.

rc-bench measured the REAL OpenCode client: the ACI plugin (router
injects skills) and OpenCode's NATIVE skills (model picks from a
name+description list) both passed 12/12 on a 45-skill corpus, native
cheaper. The open question is whether ACI's router wins when the
corpus is LARGE and DENSE - many near-identical internal standards
that differ only in WHICH team/service they apply to. This module is
builder c's slice of that dense corpus: TWO families of FIVE variants
each (10 fixtures). Every standard below is INVENTED with the fixture
(it did not exist before today, so no model can carry it), documented
ONLY in the fixture's private SKILL.md, and pinned in the workspace
tests as sha256 DIGESTS over canonical traces - never plaintext - so
a naked model can derive nothing from the test names, messages or the
shipped code (the shipped code embodies a plausible but WRONG public
convention).

Family "consumer-retry" - message-consumer retry policy, 5 teams:

- private3-thistle-retry -> thistle-consumer-retry
      the Thistle commerce-events consumer retry policy: error
      classes, the quadratic backoff ladder with its jitter seed,
      the attempt budget, the outcome words and the dead-letter
      record format (scope: basket-worker, basket-archiver).
- private3-sable-retry -> sable-consumer-retry
      the Sable payments consumer retry policy (scope:
      ledger-ingestor, ledger-replayer).
- private3-juniper-retry -> juniper-consumer-retry
      the Juniper notifications consumer retry policy (scope:
      notify-fanout, notify-sender).
- private3-basalt-retry -> basalt-consumer-retry
      the Basalt search consumer retry policy (scope:
      index-feeder, index-compactor).
- private3-tundra-retry -> tundra-consumer-retry
      the Tundra identity consumer retry policy (scope:
      session-mirror, profile-sync).

Family "config-precedence" - configuration precedence resolution,
5 teams:

- private3-kestrel-config -> kestrel-config-precedence
      the Kestrel edge config precedence standard: source order, the
      env namespace, value coercion, the lock and the resolved-trace
      format (scope: edge-router, edge-auth).
- private3-marrow-config -> marrow-config-precedence
      the Marrow data config precedence standard (scope:
      lake-gateway, lake-loader).
- private3-sorrel-config -> sorrel-config-precedence
      the Sorrel billing config precedence standard (scope:
      billing-api, billing-jobs).
- private3-onyx-config -> onyx-config-precedence
      the Onyx media config precedence standard (scope:
      transcode-foreman, upload-warden).
- private3-wick-config -> wick-config-precedence
      the Wick sessions config precedence standard (scope:
      session-service, token-minter).

Within a family the five variants share structure and vocabulary
class - the descriptions differ ONLY in scope - but differ in at
least two concrete decisions each (class tables, backoff ladders,
jitter seeds, attempt budgets, outcome words, dead-letter formats /
source orders, env mappings, coercions, locks, trace formats), and
every decision changes EVERY test digest: applying a SIBLING
variant's fix to a workspace FAILS that workspace's tests (the
confusion matrix, pinned by tests/unit/test_private3_tasks_c.py).

Same contract as scripts/private_tasks.py: the rules live ONLY in the
fixture's private SKILL.md (served through the capability plane, never
a workspace file), pinned as sha256 DIGESTS over grouped
multi-decision traces - never plaintext, never one decision per digest
(the E2B 1.5 meridian lesson: small digest spaces are brute-forceable
in principle; every line here also carries standard-only vocabulary,
which kills offline enumeration). The shipped code embodies a
PLAUSIBLE BUT WRONG public convention (uniform exponential retry /
flags-over-env-over-file with no locks), so a competent engineer's
guess fails. The knowledge gate: each fixture's MARKERS (vocabulary
that exists only in the standard) appear in the skill and NOWHERE
else - not in the tests, not in the shipped code, not in the prompt,
not in any sibling skill. All prompts are INDIRECT: they name the
SERVICE the code belongs to and say only that an internal policy
exists - never the standard, its team title words or any rule.

No fake PII or secrets are carried: family A uses invented error
codes, family B invented hostnames/ports/modes.

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
    directness: str,
    markers: list[str],
) -> dict[str, Any]:
    """A private-knowledge fixture: workspace files + task prompt + the
    private SKILL.md text (NOT a workspace file - the model must never see
    it on disk; the capability plane serves it) + the family it belongs to
    (the dense-corpus axis: near-identical siblings) + the assigned
    directness + the standard-only markers (the knowledge gate)."""
    return {
        "name": name,
        "family": family,
        "files": files,
        "prompt": prompt,
        "skill_id": skill_id,
        "skill": skill,
        "directness": directness,
        "markers": markers,
    }


# --------------------------------------------------------------------------
# private3-thistle-retry - the Thistle commerce consumer retry
# policy (basket-worker, basket-archiver). The shipped code
# guesses the public convention (retry everything, exponential
# backoff, no jitter, generic envelope); the standard defines the
# class table, the ladder, the budget, the outcome words and the
# record format. INDIRECT: the prompt names the service only.
# --------------------------------------------------------------------------
_THISTLE_PROMPT = (
    "The tests in test_consumer_convention.py fail. This basket-worker consumer doesn't "
    "follow how we decide delivery retries here: which failures get another attempt, how long "
    "we wait between attempts, and when a delivery is written off is decided by our internal "
    "policy, not by guesswork. Find the root cause before proposing any fix, then fix it and "
    "make the whole test suite green."
)
_THISTLE_SKILL = """---
name: thistle-consumer-retry
description: Retry policy for the Thistle commerce services (basket-worker, basket-archiver).
version: 1.0.0
---

# Thistle Consumer Retry Policy (internal)

How a consumer of the Thistle commerce topics retries a failing
delivery and when it writes the message off. Internal to the Thistle
team - not published anywhere.

Scope: the basket-worker and basket-archiver services.

## Error classes

Every failure code has exactly one class:

| Class | Codes | Meaning |
|-------|-------|---------|
| TRANSIENT | B-503, B-509, B-429 | worth another attempt |
| POISON | B-400, B-411, B-422 | skipped without a retry |
| FATAL | B-451, B-507 | written off immediately |

A code outside the table is FATAL: written off on its first failure.

## The backoff ladder

After a failed attempt (1-based) in the TRANSIENT class, the
consumer waits before the next attempt:

    wait = the square of the attempt number + jitter

    jitter = int(sha256("thistle-jitter-07|<topic>|<msg_id>|<attempt>")
                 .hexdigest()[:8], 16) % 3

## The attempt budget

A TRANSIENT delivery gets at most 4 total attempts. A
delivery whose budget is spent is written off with the
RETRY-EXHAUSTED outcome.

## Outcome words

- succeeded on any attempt -> DELIVERED
- a POISON failure -> POISON-DROPPED (no retry, no record)
- budget spent -> RETRY-EXHAUSTED
- a FATAL failure -> FATAL-DEAD

## The dead-letter record

A written-off delivery (budget spent or a FATAL failure) is
recorded as:

    TH-DEAD|<topic>|<consumer>|<msg_id>|<attempts>|<code>

## Worked example

Delivery "m-001" on basket.events.v3 fails B-503 then succeeds:
    basket.events.v3#m-001|a1|B-503|w1
    basket.events.v3#m-001|a2|OK|w0
    basket.events.v3#m-001|done|DELIVERED

Delivery "m-002" on basket.archival fails B-451 on the first attempt:
    basket.archival#m-002|a1|B-451|w0
    basket.archival#m-002|done|FATAL-DEAD
    TH-DEAD|basket.archival|basket-worker|m-002|1|B-451

Delivery "m-003" on basket.events.v3 fails B-503 for all 4 attempts:
    basket.events.v3#m-003|a1|B-503|w2
    basket.events.v3#m-003|a2|B-503|w5
    basket.events.v3#m-003|a3|B-503|w9
    basket.events.v3#m-003|a4|B-503|w0
    basket.events.v3#m-003|done|RETRY-EXHAUSTED
    TH-DEAD|basket.events.v3|basket-worker|m-003|4|B-503\n"""

# --------------------------------------------------------------------------
# private3-sable-retry - the Sable payments consumer retry
# policy (ledger-ingestor, ledger-replayer). The shipped code
# guesses the public convention (retry everything, exponential
# backoff, no jitter, generic envelope); the standard defines the
# class table, the ladder, the budget, the outcome words and the
# record format. INDIRECT: the prompt names the service only.
# --------------------------------------------------------------------------
_SABLE_PROMPT = (
    "The tests in test_consumer_convention.py fail. This ledger-ingestor consumer doesn't "
    "follow how we decide delivery retries here: which failures get another attempt, how long "
    "we wait between attempts, and when a delivery is written off is decided by our internal "
    "policy, not by guesswork. Find the root cause before proposing any fix, then fix it and "
    "make the whole test suite green."
)
_SABLE_SKILL = """---
name: sable-consumer-retry
description: Retry policy for the Sable payments services (ledger-ingestor, ledger-replayer).
version: 1.0.0
---

# Sable Consumer Retry Policy (internal)

How a consumer of the Sable payments topics retries a failing
delivery and when it writes the message off. Internal to the Sable
team - not published anywhere.

Scope: the ledger-ingestor and ledger-replayer services.

## Error classes

Every failure code has exactly one class:

| Class | Codes | Meaning |
|-------|-------|---------|
| FLAKY | S-502, S-503, S-504 | worth another attempt |
| UNRECOVERABLE | S-400, S-404 | skipped without a retry |
| BURY | S-412, S-599 | written off immediately |

A code outside the table is FLAKY: retried like any other flaky code.

## The backoff ladder

After a failed attempt (1-based) in the FLAKY class, the
consumer waits before the next attempt:

    wait = two to the power of the attempt number + jitter

    jitter = int(sha256("sable-seed-19::<topic>::<msg_id>::<attempt>")
                 .hexdigest()[:6], 16) % 2

## The attempt budget

A FLAKY delivery gets at most 5 total attempts. A
delivery whose budget is spent is written off with the
FLAKY-SPENT outcome.

## Outcome words

- succeeded on any attempt -> SETTLED
- a UNRECOVERABLE failure -> SKIPPED-UNRECOVERABLE (no retry, no record)
- budget spent -> FLAKY-SPENT
- a BURY failure -> BURIED

## The dead-letter record

A written-off delivery (budget spent or a BURY failure) is
recorded as:

    sable-dead://<consumer>/<topic>/<msg_id>?c=<code>&t=<attempts>

## Worked example

Delivery "m-001" on ledger.txns.v2 fails S-502 then succeeds:
    ledger.txns.v2#m-001|a1|S-502|w3
    ledger.txns.v2#m-001|a2|OK|w0
    ledger.txns.v2#m-001|done|SETTLED

Delivery "m-002" on ledger.replay fails S-412 on the first attempt:
    ledger.replay#m-002|a1|S-412|w0
    ledger.replay#m-002|done|BURIED
    sable-dead://ledger-ingestor/ledger.replay/m-002?c=S-412&t=1

Delivery "m-003" on ledger.txns.v2 fails S-502 for all 5 attempts:
    ledger.txns.v2#m-003|a1|S-502|w3
    ledger.txns.v2#m-003|a2|S-502|w4
    ledger.txns.v2#m-003|a3|S-502|w9
    ledger.txns.v2#m-003|a4|S-502|w17
    ledger.txns.v2#m-003|a5|S-502|w0
    ledger.txns.v2#m-003|done|FLAKY-SPENT
    sable-dead://ledger-ingestor/ledger.txns.v2/m-003?c=S-502&t=5\n"""

# --------------------------------------------------------------------------
# private3-juniper-retry - the Juniper notifications consumer retry
# policy (notify-fanout, notify-sender). The shipped code
# guesses the public convention (retry everything, exponential
# backoff, no jitter, generic envelope); the standard defines the
# class table, the ladder, the budget, the outcome words and the
# record format. INDIRECT: the prompt names the service only.
# --------------------------------------------------------------------------
_JUNIPER_PROMPT = (
    "The tests in test_consumer_convention.py fail. This notify-fanout consumer doesn't "
    "follow how we decide delivery retries here: which failures get another attempt, how long "
    "we wait between attempts, and when a delivery is written off is decided by our internal "
    "policy, not by guesswork. Find the root cause before proposing any fix, then fix it and "
    "make the whole test suite green."
)
_JUNIPER_SKILL = """---
name: juniper-consumer-retry
description: Retry policy for the Juniper notifications services (notify-fanout, notify-sender).
version: 1.0.0
---

# Juniper Consumer Retry Policy (internal)

How a consumer of the Juniper notifications topics retries a failing
delivery and when it writes the message off. Internal to the Juniper
team - not published anywhere.

Scope: the notify-fanout and notify-sender services.

## Error classes

Every failure code has exactly one class:

| Class | Codes | Meaning |
|-------|-------|---------|
| SOFT | J-201, J-202, J-429 | worth another attempt |
| SKIP | J-204, J-205 | skipped without a retry |
| HARD | J-500, J-501 | written off immediately |

A code outside the table is HARD: written off on its first failure.

## The backoff ladder

After a failed attempt (1-based) in the SOFT class, the
consumer waits before the next attempt:

    wait = three times the attempt number + jitter

    jitter = int(sha256("juniper-mix-23~<topic>~<msg_id>~<attempt>")
                 .hexdigest()[:7], 16) % 4

## The attempt budget

A SOFT delivery gets at most 3 total attempts. A
delivery whose budget is spent is written off with the
SOFT-SPENT outcome.

## Outcome words

- succeeded on any attempt -> LANDED
- a SKIP failure -> SOFT-SKIPPED (no retry, no record)
- budget spent -> SOFT-SPENT
- a HARD failure -> HARD-FAILED

## The dead-letter record

A written-off delivery (budget spent or a HARD failure) is
recorded as:

    JUNIPER_DLQ(<topic>,<consumer>,<msg_id>,<code>,<attempts>)

## Worked example

Delivery "m-001" on notify.fanout.v1 fails J-201 then succeeds:
    notify.fanout.v1#m-001|a1|J-201|w6
    notify.fanout.v1#m-001|a2|OK|w0
    notify.fanout.v1#m-001|done|LANDED

Delivery "m-002" on notify.send fails J-500 on the first attempt:
    notify.send#m-002|a1|J-500|w0
    notify.send#m-002|done|HARD-FAILED
    JUNIPER_DLQ(notify.send,notify-fanout,m-002,J-500,1)

Delivery "m-003" on notify.fanout.v1 fails J-201 for all 3 attempts:
    notify.fanout.v1#m-003|a1|J-201|w4
    notify.fanout.v1#m-003|a2|J-201|w7
    notify.fanout.v1#m-003|a3|J-201|w0
    notify.fanout.v1#m-003|done|SOFT-SPENT
    JUNIPER_DLQ(notify.fanout.v1,notify-fanout,m-003,J-201,3)\n"""

# --------------------------------------------------------------------------
# private3-basalt-retry - the Basalt search consumer retry
# policy (index-feeder, index-compactor). The shipped code
# guesses the public convention (retry everything, exponential
# backoff, no jitter, generic envelope); the standard defines the
# class table, the ladder, the budget, the outcome words and the
# record format. INDIRECT: the prompt names the service only.
# --------------------------------------------------------------------------
_BASALT_PROMPT = (
    "The tests in test_consumer_convention.py fail. This index-feeder consumer doesn't follow "
    "how we decide delivery retries here: which failures get another attempt, how long we "
    "wait between attempts, and when a delivery is written off is decided by our internal "
    "policy, not by guesswork. Find the root cause before proposing any fix, then fix it and "
    "make the whole test suite green."
)
_BASALT_SKILL = """---
name: basalt-consumer-retry
description: Retry policy for the Basalt search services (index-feeder, index-compactor).
version: 1.0.0
---

# Basalt Consumer Retry Policy (internal)

How a consumer of the Basalt search topics retries a failing
delivery and when it writes the message off. Internal to the Basalt
team - not published anywhere.

Scope: the index-feeder and index-compactor services.

## Error classes

Every failure code has exactly one class:

| Class | Codes | Meaning |
|-------|-------|---------|
| RETRYABLE | K-503, K-504, K-505 | worth another attempt |
| DISCARD | K-400, K-401 | skipped without a retry |
| TERMINAL | K-455, K-502 | written off immediately |

A code outside the table is DISCARD: skipped without a retry.

## The backoff ladder

After a failed attempt (1-based) in the RETRYABLE class, the
consumer waits before the next attempt:

    wait = the ladder slot 2, 7, 17 (clamped at 17) + jitter

    jitter = int(sha256("basalt-salt-31.<topic>.<msg_id>.<attempt>")
                 .hexdigest()[:8], 16) % 5

## The attempt budget

A RETRYABLE delivery gets at most 4 total attempts. A
delivery whose budget is spent is written off with the
RETRYABLE-SPENT outcome.

## Outcome words

- succeeded on any attempt -> CLEARED
- a DISCARD failure -> DISCARDED-POISON (no retry, no record)
- budget spent -> RETRYABLE-SPENT
- a TERMINAL failure -> TERMINAL-ENTOMBED

## The dead-letter record

A written-off delivery (budget spent or a TERMINAL failure) is
recorded as:

    <topic>!<consumer>!<msg_id>!<code>!<attempts>!basalt-dlq

## Worked example

Delivery "m-001" on index.feeds.v4 fails K-503 then succeeds:
    index.feeds.v4#m-001|a1|K-503|w3
    index.feeds.v4#m-001|a2|OK|w0
    index.feeds.v4#m-001|done|CLEARED

Delivery "m-002" on index.compact fails K-455 on the first attempt:
    index.compact#m-002|a1|K-455|w0
    index.compact#m-002|done|TERMINAL-ENTOMBED
    index.compact!index-feeder!m-002!K-455!1!basalt-dlq

Delivery "m-003" on index.feeds.v4 fails K-503 for all 4 attempts:
    index.feeds.v4#m-003|a1|K-503|w3
    index.feeds.v4#m-003|a2|K-503|w11
    index.feeds.v4#m-003|a3|K-503|w17
    index.feeds.v4#m-003|a4|K-503|w0
    index.feeds.v4#m-003|done|RETRYABLE-SPENT
    index.feeds.v4!index-feeder!m-003!K-503!4!basalt-dlq\n"""

# --------------------------------------------------------------------------
# private3-tundra-retry - the Tundra identity consumer retry
# policy (session-mirror, profile-sync). The shipped code
# guesses the public convention (retry everything, exponential
# backoff, no jitter, generic envelope); the standard defines the
# class table, the ladder, the budget, the outcome words and the
# record format. INDIRECT: the prompt names the service only.
# --------------------------------------------------------------------------
_TUNDRA_PROMPT = (
    "The tests in test_consumer_convention.py fail. This session-mirror consumer doesn't "
    "follow how we decide delivery retries here: which failures get another attempt, how long "
    "we wait between attempts, and when a delivery is written off is decided by our internal "
    "policy, not by guesswork. Find the root cause before proposing any fix, then fix it and "
    "make the whole test suite green."
)
_TUNDRA_SKILL = """---
name: tundra-consumer-retry
description: Retry policy for the Tundra identity services (session-mirror, profile-sync).
version: 1.0.0
---

# Tundra Consumer Retry Policy (internal)

How a consumer of the Tundra identity topics retries a failing
delivery and when it writes the message off. Internal to the Tundra
team - not published anywhere.

Scope: the session-mirror and profile-sync services.

## Error classes

Every failure code has exactly one class:

| Class | Codes | Meaning |
|-------|-------|---------|
| BLIP | T-301, T-302, T-429 | worth another attempt |
| JUNK | T-303, T-304 | skipped without a retry |
| DOOM | T-500, T-502 | written off immediately |

A code outside the table is BLIP: retried like any other blip.

## The backoff ladder

After a failed attempt (1-based) in the BLIP class, the
consumer waits before the next attempt:

    wait = the odd seconds 1, 3, 5, ... + jitter

    jitter = int(sha256("tundra-ice-11/<topic>/<msg_id>/<attempt>")
                 .hexdigest()[:6], 16) % 2

## The attempt budget

A BLIP delivery gets at most 5 total attempts. A
delivery whose budget is spent is written off with the
BLIP-SPENT outcome.

## Outcome words

- succeeded on any attempt -> HOME
- a JUNK failure -> JUNK-DROPPED (no retry, no record)
- budget spent -> BLIP-SPENT
- a DOOM failure -> DOOMED

## The dead-letter record

A written-off delivery (budget spent or a DOOM failure) is
recorded as:

    tundra:dead:<consumer>:<topic>:<msg_id>:<code>:<attempts>

## Worked example

Delivery "m-001" on sessions.mirror.v2 fails T-301 then succeeds:
    sessions.mirror.v2#m-001|a1|T-301|w1
    sessions.mirror.v2#m-001|a2|OK|w0
    sessions.mirror.v2#m-001|done|HOME

Delivery "m-002" on profiles.sync fails T-500 on the first attempt:
    profiles.sync#m-002|a1|T-500|w0
    profiles.sync#m-002|done|DOOMED
    tundra:dead:session-mirror:profiles.sync:m-002:T-500:1

Delivery "m-003" on sessions.mirror.v2 fails T-301 for all 5 attempts:
    sessions.mirror.v2#m-003|a1|T-301|w2
    sessions.mirror.v2#m-003|a2|T-301|w4
    sessions.mirror.v2#m-003|a3|T-301|w6
    sessions.mirror.v2#m-003|a4|T-301|w8
    sessions.mirror.v2#m-003|a5|T-301|w0
    sessions.mirror.v2#m-003|done|BLIP-SPENT
    tundra:dead:session-mirror:sessions.mirror.v2:m-003:T-301:5\n"""

# --------------------------------------------------------------------------
# private3-kestrel-config - the Kestrel edge config precedence
# standard (edge-router, edge-auth). The shipped code
# guesses the public convention (flags > env > file > defaults, no
# locks, one coercion, one trace format); the standard defines the
# source order, the env namespace, the coercions, the lock and the
# trace format. INDIRECT: the prompt names the service only.
# --------------------------------------------------------------------------
_KESTREL_PROMPT = (
    "The tests in test_config_convention.py fail. This edge-router configuration doesn't "
    "follow how we decide which value wins here: the order our sources override each other, "
    "how environment variables map to keys, and how values are parsed is decided by our "
    "internal standard, not by guesswork. Find the root cause before proposing any fix, then "
    "fix it and make the whole test suite green."
)
_KESTREL_SKILL = """---
name: kestrel-config-precedence
description: Config precedence for the Kestrel edge services (edge-router, edge-auth).
version: 1.0.0
---

# Kestrel Config Precedence Standard (internal)

How the edge-router and edge-auth services decide which value wins for a
configuration key, how environment variables map to keys, and how
values are parsed. Internal to the Kestrel team - not published
anywhere.

Scope: the edge-router and edge-auth services.

## Source order

Low to high, a higher source overrides a lower one:

    defaults < file < env < flags

## Environment variables

A variable named KESTREL__SECTION__KEY maps to the key
section.key: strip the KESTREL__ prefix, split the rest on
double underscores, lowercase every part, join with dots. Any
other name is ignored.

## Value coercion

- bools: on/off (case-insensitive)
- ints: plus-tolerant - int() accepts a leading + and thousand underscores
- lists: comma-split, each item stripped
- a parse that fails: the value is absent (the next lower source wins)

## Locks

edge.tls_mode is locked: env cannot override it. A
locked key whose blocked source carries a value is annotated
!locked in the trace.

## The resolved trace

One line per key, keys sorted alphabetically:

    key=value@source (bools render on/off, lists render [a, b])

## Worked example

Resolving the defaults alone, the first two lines of the trace:
    auth.allow_debug=off@defaults
    auth.token_ttl=3600@defaults

With a flags value on the locked key edge.tls_mode, the line:
    edge.tls_mode=on@defaults\n"""

# --------------------------------------------------------------------------
# private3-marrow-config - the Marrow data config precedence
# standard (lake-gateway, lake-loader). The shipped code
# guesses the public convention (flags > env > file > defaults, no
# locks, one coercion, one trace format); the standard defines the
# source order, the env namespace, the coercions, the lock and the
# trace format. INDIRECT: the prompt names the service only.
# --------------------------------------------------------------------------
_MARROW_PROMPT = (
    "The tests in test_config_convention.py fail. This lake-gateway configuration doesn't "
    "follow how we decide which value wins here: the order our sources override each other, "
    "how environment variables map to keys, and how values are parsed is decided by our "
    "internal standard, not by guesswork. Find the root cause before proposing any fix, then "
    "fix it and make the whole test suite green."
)
_MARROW_SKILL = """---
name: marrow-config-precedence
description: Config precedence for the Marrow data services (lake-gateway, lake-loader).
version: 1.0.0
---

# Marrow Config Precedence Standard (internal)

How the lake-gateway and lake-loader services decide which value wins for a
configuration key, how environment variables map to keys, and how
values are parsed. Internal to the Marrow team - not published
anywhere.

Scope: the lake-gateway and lake-loader services.

## Source order

Low to high, a higher source overrides a lower one:

    defaults < env < file < flags

## Environment variables

A variable named MARROW_SECTION_KEY maps to the key
section.key: strip the MARROW_ prefix, split the rest on
underscores, lowercase every part, join with dots. Keys have
no underscores. Any other name is ignored.

## Value coercion

- bools: true/false (case-insensitive)
- ints: plain-decimal - digits and an optional leading minus only
- lists: semicolon-separated, each item stripped
- a parse that fails: the value is absent (the next lower source wins)

## Locks

lake.mode is locked: flags cannot override it. A
locked key whose blocked source carries a value is annotated
#ignored in the trace.

## The resolved trace

One line per key, keys sorted alphabetically:

    source:key -> value (bools render true/false, lists render a;b)

## Worked example

Resolving the defaults alone, the first two lines of the trace:
    defaults:gateway.follow -> false
    defaults:gateway.host -> localhost

With a flags value on the locked key lake.mode, the line:
    defaults:lake.mode -> batch #ignored\n"""

# --------------------------------------------------------------------------
# private3-sorrel-config - the Sorrel billing config precedence
# standard (billing-api, billing-jobs). The shipped code
# guesses the public convention (flags > env > file > defaults, no
# locks, one coercion, one trace format); the standard defines the
# source order, the env namespace, the coercions, the lock and the
# trace format. INDIRECT: the prompt names the service only.
# --------------------------------------------------------------------------
_SORREL_PROMPT = (
    "The tests in test_config_convention.py fail. This billing-api configuration doesn't "
    "follow how we decide which value wins here: the order our sources override each other, "
    "how environment variables map to keys, and how values are parsed is decided by our "
    "internal standard, not by guesswork. Find the root cause before proposing any fix, then "
    "fix it and make the whole test suite green."
)
_SORREL_SKILL = """---
name: sorrel-config-precedence
description: Config precedence for the Sorrel billing services (billing-api, billing-jobs).
version: 1.0.0
---

# Sorrel Config Precedence Standard (internal)

How the billing-api and billing-jobs services decide which value wins for a
configuration key, how environment variables map to keys, and how
values are parsed. Internal to the Sorrel team - not published
anywhere.

Scope: the billing-api and billing-jobs services.

## Source order

Low to high, a higher source overrides a lower one:

    defaults < flags < file < env

## Environment variables

A variable named sorrel.key maps to the key verbatim: strip
the lowercase sorrel. prefix and keep the rest exactly as
written. The prefix is case-sensitive - SORREL_ is not ours.

## Value coercion

- bools: yes/no (case-insensitive)
- ints: thousand-underscored in groups of three (1_000 is 1000; 10_00 fails)
- lists: whitespace-separated
- a parse that fails: the raw text is kept as the value

## Locks

billing.currency is locked: env and flags cannot override it. A
locked key whose blocked source carries a value is annotated
[locked] in the trace.

## The resolved trace

One line per key, keys sorted alphabetically:

    RESOLVE key = value (source) (bools render yes/no, lists render a b)

## Worked example

Resolving the defaults alone, the first two lines of the trace:
    RESOLVE api.host = billing.internal (defaults)
    RESOLVE api.port = 443 (defaults)

With a flags value on the locked key billing.currency, the line:
    RESOLVE billing.currency = USD (defaults) [locked]\n"""

# --------------------------------------------------------------------------
# private3-onyx-config - the Onyx media config precedence
# standard (transcode-foreman, upload-warden). The shipped code
# guesses the public convention (flags > env > file > defaults, no
# locks, one coercion, one trace format); the standard defines the
# source order, the env namespace, the coercions, the lock and the
# trace format. INDIRECT: the prompt names the service only.
# --------------------------------------------------------------------------
_ONYX_PROMPT = (
    "The tests in test_config_convention.py fail. This transcode-foreman configuration "
    "doesn't follow how we decide which value wins here: the order our sources override each "
    "other, how environment variables map to keys, and how values are parsed is decided by "
    "our internal standard, not by guesswork. Find the root cause before proposing any fix, "
    "then fix it and make the whole test suite green."
)
_ONYX_SKILL = """---
name: onyx-config-precedence
description: Config precedence for the Onyx media services (transcode-foreman, upload-warden).
version: 1.0.0
---

# Onyx Config Precedence Standard (internal)

How the transcode-foreman and upload-warden services decide which value wins for a
configuration key, how environment variables map to keys, and how
values are parsed. Internal to the Onyx team - not published
anywhere.

Scope: the transcode-foreman and upload-warden services.

## Source order

Low to high, a higher source overrides a lower one:

    defaults < file < flags < env

## Environment variables

A variable named onyx__section__key maps to the key
section.key: strip the lowercase onyx__ prefix, split on
double underscores, lowercase every part, join with dots. The
prefix is case-sensitive - ONYX__ is not ours.

## Value coercion

- bools: 1/0
- ints: hex-friendly - a 0x prefix parses as hex (0x10 is 16)
- lists: comma-separated, items NOT stripped
- a parse that fails: the value is absent (the next lower source wins)

## Locks

media.codec is locked: env, flags and the file cannot override it. A
locked key whose blocked source carries a value is annotated
!override-blocked in the trace.

## The resolved trace

One line per key, keys sorted alphabetically:

    key@source => value (bools render 1/0, lists render a,b)

## Worked example

Resolving the defaults alone, the first two lines of the trace:
    foreman.queue@defaults => transcode
    media.codec@defaults => h264

With a flags value on the locked key media.codec, the line:
    media.codec@defaults => h264!override-blocked\n"""

# --------------------------------------------------------------------------
# private3-wick-config - the Wick sessions config precedence
# standard (session-service, token-minter). The shipped code
# guesses the public convention (flags > env > file > defaults, no
# locks, one coercion, one trace format); the standard defines the
# source order, the env namespace, the coercions, the lock and the
# trace format. INDIRECT: the prompt names the service only.
# --------------------------------------------------------------------------
_WICK_PROMPT = (
    "The tests in test_config_convention.py fail. This session-service configuration doesn't "
    "follow how we decide which value wins here: the order our sources override each other, "
    "how environment variables map to keys, and how values are parsed is decided by our "
    "internal standard, not by guesswork. Find the root cause before proposing any fix, then "
    "fix it and make the whole test suite green."
)
_WICK_SKILL = """---
name: wick-config-precedence
description: Config precedence for the Wick sessions services (session-service, token-minter).
version: 1.0.0
---

# Wick Config Precedence Standard (internal)

How the session-service and token-minter services decide which value wins for a
configuration key, how environment variables map to keys, and how
values are parsed. Internal to the Wick team - not published
anywhere.

Scope: the session-service and token-minter services.

## Source order

Low to high, a higher source overrides a lower one:

    env < defaults < file < flags

## Environment variables

A variable named WICK_SECTION_KEY maps to the key
section.key: strip the WICK_ prefix, split the rest on the
first underscore only, lowercase both parts. Underscores
after the first underscore are part of the key.

## Value coercion

- bools: enabled/disabled (case-insensitive)
- ints: plain int() - a leading + and thousand underscores parse
- lists: pipe-separated, each item stripped
- a parse that fails: the raw text is kept as the value

## Locks

tokens.algo is locked: env and flags cannot override it. A
locked key whose blocked source carries a value is annotated
(pinned) in the trace.

## The resolved trace

One line per key, keys sorted alphabetically:

    [source] key: value (bools render enabled/disabled, lists render a|b)

## Worked example

Resolving the defaults alone, the first two lines of the trace:
    [defaults] sessions.fallbacks: redis|disk
    [defaults] sessions.max_concurrent: 64

With a flags value on the locked key tokens.algo, the line:
    [defaults] tokens.algo: hs256 (pinned)\n"""

TASKS: list[dict[str, Any]] = [
    _private_task(
        "private3-thistle-retry",
        "consumer-retry",
        {
            "consumer_policy.py": (
                '"""Retry policy for the basket-worker consumer.\n'
                "\n"
                "A delivery fails with an error code. Failures worth retrying get\n"
                "exponential backoff (1s, 2s, 4s, ...) and up to three attempts;\n"
                "permanent failures are skipped without a retry; a delivery that runs\n"
                "out of budget is written off with its topic, id and error.\n"
                '"""\n'
                "\n"
                '_PERMANENT = ("B-400", "B-411", "B-429")\n'
                "\n"
                "_MAX_ATTEMPTS = 3\n"
                "\n"
                "\n"
                "def classify(code):\n"
                '    """\'retryable\' or \'permanent\' - whether a failure is worth retrying."""\n'
                '    return "permanent" if code in _PERMANENT else "retryable"\n'
                "\n"
                "\n"
                "def action(code):\n"
                '    """What a failing attempt means: retry or drop."""\n'
                '    return "drop" if classify(code) == "permanent" else "retry"\n'
                "\n"
                "\n"
                "def retry_wait(attempt, topic, msg_id):\n"
                '    """Seconds to wait after failed `attempt` (1-based): exponential."""\n'
                "    return 2 ** (attempt - 1)\n"
                "\n"
                "\n"
                "def max_attempts():\n"
                '    """The total attempt budget for one delivery."""\n'
                "    return _MAX_ATTEMPTS\n"
                "\n"
                "\n"
                "def verdict_delivered():\n"
                '    return "ok"\n'
                "\n"
                "\n"
                "def verdict_dropped():\n"
                '    return "skipped"\n'
                "\n"
                "\n"
                "def verdict_exhausted():\n"
                '    return "failed"\n'
                "\n"
                "\n"
                "def verdict_dead():\n"
                '    return "dead"\n'
            ),
            "dead_letter.py": (
                '"""Dead-letter records for the basket-worker - written-off deliveries."""\n'
                "\n"
                "\n"
                "def record(topic, consumer, msg_id, code, attempts):\n"
                '    """The dead-letter record: topic, id, error and attempt count."""\n'
                '    return f"DLQ|{topic}|{msg_id}|{code}|{attempts}"\n'
            ),
            "consumer_run.py": (
                '"""Consumer run for the basket-worker - one delivery through the policy.\n'
                "\n"
                "The run is a trace: one line per attempt, the disposition line, and -\n"
                "when the policy writes the message off - the dead-letter record.\n"
                '"""\n'
                "\n"
                "from consumer_policy import (\n"
                "    action,\n"
                "    max_attempts,\n"
                "    retry_wait,\n"
                "    verdict_dead,\n"
                "    verdict_delivered,\n"
                "    verdict_dropped,\n"
                "    verdict_exhausted,\n"
                ")\n"
                "from dead_letter import record\n"
                "\n"
                "\n"
                "def run_delivery(topic, consumer, msg_id, outcomes):\n"
                '    """The trace lines for one delivery against its observed outcomes."""\n'
                "    lines = []\n"
                "    attempt = 0\n"
                "    while attempt < len(outcomes):\n"
                "        code = outcomes[attempt]\n"
                "        attempt += 1\n"
                '        if code == "OK":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|OK|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_delivered()}")\n'
                "            return lines\n"
                "        what = action(code)\n"
                '        if what == "drop":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_dropped()}")\n'
                "            return lines\n"
                '        if what == "dead":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_dead()}")\n'
                "            lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "            return lines\n"
                "        if attempt >= max_attempts():\n"
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_exhausted()}")\n'
                "            lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "            return lines\n"
                "        wait = retry_wait(attempt, topic, msg_id)\n"
                '        lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w{wait}")\n'
                "    if not outcomes:\n"
                "        return lines\n"
                "    # the observed outcomes ended mid-retry: the last failure stands\n"
                '    lines.append(f"{topic}#{msg_id}|done|{verdict_exhausted()}")\n'
                "    lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "    return lines\n"
            ),
            "test_consumer_convention.py": (
                '"""Contract tests for the basket-worker consumer against our INTERNAL\n'
                "retry and dead-letter policy. The policy is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the observed trace\n"
                "(attempt lines, the disposition, the waits, the dead-letter records),\n"
                "so this file cannot become a copy of the policy.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from consumer_run import run_delivery\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                'CONSUMER = "basket-worker"\n'
                "\n"
                "GROUPS = [\n"
                "    [\n"
                '        ("basket.events.v3", "m-101", ["OK"]),\n'
                '        ("basket.events.v3", "m-102", ["B-503", "OK"]),\n'
                '        ("basket.events.v3", "m-103", ["B-400"]),\n'
                '        ("basket.events.v3", "m-104", ["B-451"]),\n'
                '        ("basket.events.v3", "m-105", ["B-503", "B-503", "OK"]),\n'
                '        ("basket.events.v3", "m-106", ["B-509", "OK"]),\n'
                '        ("basket.events.v3", "m-107", ["B-900"]),\n'
                '        ("basket.events.v3", "m-108", ["B-503", "B-411"]),\n'
                '        ("basket.events.v3", "m-109", ["B-503", "B-507"]),\n'
                '        ("basket.events.v3", "m-110", ["B-400", "B-503"]),\n'
                "    ],\n"
                "    [\n"
                '        ("basket.events.v3", "m-201", ["B-503", "B-503", "B-503", "B-503"]),\n'
                '        ("basket.events.v3", "m-202", ["B-503", "B-503", "B-503", "OK"]),\n'
                '        ("basket.events.v3", "m-203", \n'
                '         ["B-503", "B-503", "B-503", "B-503", "B-503", "B-503"]),\n'
                '        ("basket.events.v3", "m-204", ["B-509", "B-503", "B-509", "OK"]),\n'
                '        ("basket.events.v3", "m-205", ["B-451", "B-503"]),\n'
                '        ("basket.events.v3", "m-206", ["B-503", "B-503", "B-400"]),\n'
                '        ("basket.events.v3", "m-207", ["B-900", "OK"]),\n'
                '        ("basket.events.v3", "m-208", ["B-503", "B-900"]),\n'
                '        ("basket.events.v3", "m-209", \n'
                '         ["B-429", "B-429", "B-429", "B-429", "B-429"]),\n'
                '        ("basket.events.v3", "m-210", ["OK"]),\n'
                "    ],\n"
                "    [\n"
                '        ("basket.archival", "m-301", ["B-503", "OK"]),\n'
                '        ("basket.archival", "m-302", ["B-400"]),\n'
                '        ("basket.archival", "m-303", \n'
                '         ["B-503", "B-503", "B-503", "B-503", "OK"]),\n'
                '        ("basket.archival", "m-304", ["B-451"]),\n'
                '        ("basket.archival", "m-305", ["B-509", "B-400"]),\n'
                '        ("basket.archival", "m-306", \n'
                '         ["B-503", "B-509", "B-503", "B-509", "B-503", "B-509"]),\n'
                '        ("basket.archival", "m-307", ["B-900", "B-900"]),\n'
                '        ("basket.archival", "m-308", ["B-503", "OK"]),\n'
                '        ("basket.archival", "m-309", ["B-411", "B-411"]),\n'
                '        ("basket.archival", "m-310", ["B-503", "B-503", "B-451"]),\n'
                "    ],\n"
                "]\n"
                "\n"
                '# sha256 of "\\n".join(all run_delivery lines) per group, from the policy.\n'
                "DIGESTS = [\n"
                '    "a80c568cb7ff5f0885972cc603d8555f90f9f1a18bb020d4196b5fc33866bee9",\n'
                '    "94faeabe0767562e2586b5e5a8f55461bad28b964862dbfa10459c01b86df4a7",\n'
                '    "8e40930b9913137d96908411318be7f9ef307544c30adb9fd8704f51d2fd0873",\n'
                "]\n"
                "\n"
                "assert len(GROUPS) == len(DIGESTS)\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("deliveries", "digest"),\n'
                "    list(zip(GROUPS, DIGESTS)),\n"
                '    ids=[f"g{i:02d}" for i in range(1, len(GROUPS) + 1)],\n'
                ")\n"
                "def test_trace(deliveries, digest):\n"
                "    lines = []\n"
                "    for topic, msg_id, outcomes in deliveries:\n"
                "        lines.extend(run_delivery(topic, CONSUMER, msg_id, outcomes))\n"
                '    text = "\\n".join(lines)\n'
                "    assert _digest(text) == digest, text\n"
            ),
        },
        _THISTLE_PROMPT,
        "thistle-consumer-retry",
        _THISTLE_SKILL,
        "indirect",
        [
            "Thistle Consumer Retry Policy",
            "thistle-jitter-07",
            "TH-DEAD",
            "RETRY-EXHAUSTED",
            "POISON-DROPPED",
            "FATAL-DEAD",
        ],
    ),
    _private_task(
        "private3-sable-retry",
        "consumer-retry",
        {
            "consumer_policy.py": (
                '"""Retry policy for the ledger-ingestor consumer.\n'
                "\n"
                "A delivery fails with an error code. Failures worth retrying get\n"
                "exponential backoff (1s, 2s, 4s, ...) and up to three attempts;\n"
                "permanent failures are skipped without a retry; a delivery that runs\n"
                "out of budget is written off with its topic, id and error.\n"
                '"""\n'
                "\n"
                '_PERMANENT = ("S-400", "S-404", "S-503")\n'
                "\n"
                "_MAX_ATTEMPTS = 3\n"
                "\n"
                "\n"
                "def classify(code):\n"
                '    """\'retryable\' or \'permanent\' - whether a failure is worth retrying."""\n'
                '    return "permanent" if code in _PERMANENT else "retryable"\n'
                "\n"
                "\n"
                "def action(code):\n"
                '    """What a failing attempt means: retry or drop."""\n'
                '    return "drop" if classify(code) == "permanent" else "retry"\n'
                "\n"
                "\n"
                "def retry_wait(attempt, topic, msg_id):\n"
                '    """Seconds to wait after failed `attempt` (1-based): exponential."""\n'
                "    return 2 ** (attempt - 1)\n"
                "\n"
                "\n"
                "def max_attempts():\n"
                '    """The total attempt budget for one delivery."""\n'
                "    return _MAX_ATTEMPTS\n"
                "\n"
                "\n"
                "def verdict_delivered():\n"
                '    return "ok"\n'
                "\n"
                "\n"
                "def verdict_dropped():\n"
                '    return "skipped"\n'
                "\n"
                "\n"
                "def verdict_exhausted():\n"
                '    return "failed"\n'
                "\n"
                "\n"
                "def verdict_dead():\n"
                '    return "dead"\n'
            ),
            "dead_letter.py": (
                '"""Dead-letter records for the ledger-ingestor - written-off deliveries."""\n'
                "\n"
                "\n"
                "def record(topic, consumer, msg_id, code, attempts):\n"
                '    """The dead-letter record: topic, id, error and attempt count."""\n'
                '    return f"DLQ|{topic}|{msg_id}|{code}|{attempts}"\n'
            ),
            "consumer_run.py": (
                '"""Consumer run for the ledger-ingestor - one delivery through the policy.\n'
                "\n"
                "The run is a trace: one line per attempt, the disposition line, and -\n"
                "when the policy writes the message off - the dead-letter record.\n"
                '"""\n'
                "\n"
                "from consumer_policy import (\n"
                "    action,\n"
                "    max_attempts,\n"
                "    retry_wait,\n"
                "    verdict_dead,\n"
                "    verdict_delivered,\n"
                "    verdict_dropped,\n"
                "    verdict_exhausted,\n"
                ")\n"
                "from dead_letter import record\n"
                "\n"
                "\n"
                "def run_delivery(topic, consumer, msg_id, outcomes):\n"
                '    """The trace lines for one delivery against its observed outcomes."""\n'
                "    lines = []\n"
                "    attempt = 0\n"
                "    while attempt < len(outcomes):\n"
                "        code = outcomes[attempt]\n"
                "        attempt += 1\n"
                '        if code == "OK":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|OK|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_delivered()}")\n'
                "            return lines\n"
                "        what = action(code)\n"
                '        if what == "drop":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_dropped()}")\n'
                "            return lines\n"
                '        if what == "dead":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_dead()}")\n'
                "            lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "            return lines\n"
                "        if attempt >= max_attempts():\n"
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_exhausted()}")\n'
                "            lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "            return lines\n"
                "        wait = retry_wait(attempt, topic, msg_id)\n"
                '        lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w{wait}")\n'
                "    if not outcomes:\n"
                "        return lines\n"
                "    # the observed outcomes ended mid-retry: the last failure stands\n"
                '    lines.append(f"{topic}#{msg_id}|done|{verdict_exhausted()}")\n'
                "    lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "    return lines\n"
            ),
            "test_consumer_convention.py": (
                '"""Contract tests for the ledger-ingestor consumer against our INTERNAL\n'
                "retry and dead-letter policy. The policy is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the observed trace\n"
                "(attempt lines, the disposition, the waits, the dead-letter records),\n"
                "so this file cannot become a copy of the policy.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from consumer_run import run_delivery\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                'CONSUMER = "ledger-ingestor"\n'
                "\n"
                "GROUPS = [\n"
                "    [\n"
                '        ("ledger.txns.v2", "m-101", ["OK"]),\n'
                '        ("ledger.txns.v2", "m-102", ["S-502", "OK"]),\n'
                '        ("ledger.txns.v2", "m-103", ["S-400"]),\n'
                '        ("ledger.txns.v2", "m-104", ["S-412"]),\n'
                '        ("ledger.txns.v2", "m-105", ["S-502", "S-502", "OK"]),\n'
                '        ("ledger.txns.v2", "m-106", ["S-503", "OK"]),\n'
                '        ("ledger.txns.v2", "m-107", ["S-777"]),\n'
                '        ("ledger.txns.v2", "m-108", ["S-502", "S-404"]),\n'
                '        ("ledger.txns.v2", "m-109", ["S-502", "S-599"]),\n'
                '        ("ledger.txns.v2", "m-110", ["S-400", "S-502"]),\n'
                "    ],\n"
                "    [\n"
                '        ("ledger.txns.v2", "m-201", \n'
                '         ["S-502", "S-502", "S-502", "S-502", "S-502"]),\n'
                '        ("ledger.txns.v2", "m-202", \n'
                '         ["S-502", "S-502", "S-502", "S-502", "OK"]),\n'
                '        ("ledger.txns.v2", "m-203", \n'
                '         ["S-502", "S-502", "S-502", "S-502", "S-502", "S-502", "S-502"]),\n'
                '        ("ledger.txns.v2", "m-204", ["S-503", "S-502", "S-503", "OK"]),\n'
                '        ("ledger.txns.v2", "m-205", ["S-412", "S-502"]),\n'
                '        ("ledger.txns.v2", "m-206", ["S-502", "S-502", "S-400"]),\n'
                '        ("ledger.txns.v2", "m-207", ["S-777", "OK"]),\n'
                '        ("ledger.txns.v2", "m-208", ["S-502", "S-777"]),\n'
                '        ("ledger.txns.v2", "m-209", \n'
                '         ["S-504", "S-504", "S-504", "S-504", "S-504"]),\n'
                '        ("ledger.txns.v2", "m-210", ["OK"]),\n'
                "    ],\n"
                "    [\n"
                '        ("ledger.replay", "m-301", ["S-502", "OK"]),\n'
                '        ("ledger.replay", "m-302", ["S-400"]),\n'
                '        ("ledger.replay", "m-303", \n'
                '         ["S-502", "S-502", "S-502", "S-502", "OK"]),\n'
                '        ("ledger.replay", "m-304", ["S-412"]),\n'
                '        ("ledger.replay", "m-305", ["S-503", "S-400"]),\n'
                '        ("ledger.replay", "m-306", \n'
                '         ["S-502", "S-503", "S-502", "S-503", "S-502", "S-503"]),\n'
                '        ("ledger.replay", "m-307", ["S-777", "S-777"]),\n'
                '        ("ledger.replay", "m-308", ["S-502", "OK"]),\n'
                '        ("ledger.replay", "m-309", ["S-404", "S-404"]),\n'
                '        ("ledger.replay", "m-310", ["S-502", "S-502", "S-412"]),\n'
                "    ],\n"
                "]\n"
                "\n"
                '# sha256 of "\\n".join(all run_delivery lines) per group, from the policy.\n'
                "DIGESTS = [\n"
                '    "990b9615a8c150ee74bfaa2a1a43da31fe7beae864baa382ec6339c2ddaceba1",\n'
                '    "b2c8227d4c7ca07fb46ab322d5c706b5bf76f2f47ee4b02d13978e6d89dd1b5c",\n'
                '    "f765820469d9fc732ba28abdd7f04dc613ebef3fce40bcf9be36fedad927e60f",\n'
                "]\n"
                "\n"
                "assert len(GROUPS) == len(DIGESTS)\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("deliveries", "digest"),\n'
                "    list(zip(GROUPS, DIGESTS)),\n"
                '    ids=[f"g{i:02d}" for i in range(1, len(GROUPS) + 1)],\n'
                ")\n"
                "def test_trace(deliveries, digest):\n"
                "    lines = []\n"
                "    for topic, msg_id, outcomes in deliveries:\n"
                "        lines.extend(run_delivery(topic, CONSUMER, msg_id, outcomes))\n"
                '    text = "\\n".join(lines)\n'
                "    assert _digest(text) == digest, text\n"
            ),
        },
        _SABLE_PROMPT,
        "sable-consumer-retry",
        _SABLE_SKILL,
        "indirect",
        [
            "Sable Consumer Retry Policy",
            "sable-seed-19",
            "sable-dead",
            "FLAKY-SPENT",
            "SKIPPED-UNRECOVERABLE",
            "BURIED",
        ],
    ),
    _private_task(
        "private3-juniper-retry",
        "consumer-retry",
        {
            "consumer_policy.py": (
                '"""Retry policy for the notify-fanout consumer.\n'
                "\n"
                "A delivery fails with an error code. Failures worth retrying get\n"
                "exponential backoff (1s, 2s, 4s, ...) and up to three attempts;\n"
                "permanent failures are skipped without a retry; a delivery that runs\n"
                "out of budget is written off with its topic, id and error.\n"
                '"""\n'
                "\n"
                '_PERMANENT = ("J-204", "J-205", "J-429")\n'
                "\n"
                "_MAX_ATTEMPTS = 3\n"
                "\n"
                "\n"
                "def classify(code):\n"
                '    """\'retryable\' or \'permanent\' - whether a failure is worth retrying."""\n'
                '    return "permanent" if code in _PERMANENT else "retryable"\n'
                "\n"
                "\n"
                "def action(code):\n"
                '    """What a failing attempt means: retry or drop."""\n'
                '    return "drop" if classify(code) == "permanent" else "retry"\n'
                "\n"
                "\n"
                "def retry_wait(attempt, topic, msg_id):\n"
                '    """Seconds to wait after failed `attempt` (1-based): exponential."""\n'
                "    return 2 ** (attempt - 1)\n"
                "\n"
                "\n"
                "def max_attempts():\n"
                '    """The total attempt budget for one delivery."""\n'
                "    return _MAX_ATTEMPTS\n"
                "\n"
                "\n"
                "def verdict_delivered():\n"
                '    return "ok"\n'
                "\n"
                "\n"
                "def verdict_dropped():\n"
                '    return "skipped"\n'
                "\n"
                "\n"
                "def verdict_exhausted():\n"
                '    return "failed"\n'
                "\n"
                "\n"
                "def verdict_dead():\n"
                '    return "dead"\n'
            ),
            "dead_letter.py": (
                '"""Dead-letter records for the notify-fanout - written-off deliveries."""\n'
                "\n"
                "\n"
                "def record(topic, consumer, msg_id, code, attempts):\n"
                '    """The dead-letter record: topic, id, error and attempt count."""\n'
                '    return f"DLQ|{topic}|{msg_id}|{code}|{attempts}"\n'
            ),
            "consumer_run.py": (
                '"""Consumer run for the notify-fanout - one delivery through the policy.\n'
                "\n"
                "The run is a trace: one line per attempt, the disposition line, and -\n"
                "when the policy writes the message off - the dead-letter record.\n"
                '"""\n'
                "\n"
                "from consumer_policy import (\n"
                "    action,\n"
                "    max_attempts,\n"
                "    retry_wait,\n"
                "    verdict_dead,\n"
                "    verdict_delivered,\n"
                "    verdict_dropped,\n"
                "    verdict_exhausted,\n"
                ")\n"
                "from dead_letter import record\n"
                "\n"
                "\n"
                "def run_delivery(topic, consumer, msg_id, outcomes):\n"
                '    """The trace lines for one delivery against its observed outcomes."""\n'
                "    lines = []\n"
                "    attempt = 0\n"
                "    while attempt < len(outcomes):\n"
                "        code = outcomes[attempt]\n"
                "        attempt += 1\n"
                '        if code == "OK":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|OK|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_delivered()}")\n'
                "            return lines\n"
                "        what = action(code)\n"
                '        if what == "drop":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_dropped()}")\n'
                "            return lines\n"
                '        if what == "dead":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_dead()}")\n'
                "            lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "            return lines\n"
                "        if attempt >= max_attempts():\n"
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_exhausted()}")\n'
                "            lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "            return lines\n"
                "        wait = retry_wait(attempt, topic, msg_id)\n"
                '        lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w{wait}")\n'
                "    if not outcomes:\n"
                "        return lines\n"
                "    # the observed outcomes ended mid-retry: the last failure stands\n"
                '    lines.append(f"{topic}#{msg_id}|done|{verdict_exhausted()}")\n'
                "    lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "    return lines\n"
            ),
            "test_consumer_convention.py": (
                '"""Contract tests for the notify-fanout consumer against our INTERNAL\n'
                "retry and dead-letter policy. The policy is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the observed trace\n"
                "(attempt lines, the disposition, the waits, the dead-letter records),\n"
                "so this file cannot become a copy of the policy.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from consumer_run import run_delivery\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                'CONSUMER = "notify-fanout"\n'
                "\n"
                "GROUPS = [\n"
                "    [\n"
                '        ("notify.fanout.v1", "m-101", ["OK"]),\n'
                '        ("notify.fanout.v1", "m-102", ["J-201", "OK"]),\n'
                '        ("notify.fanout.v1", "m-103", ["J-204"]),\n'
                '        ("notify.fanout.v1", "m-104", ["J-500"]),\n'
                '        ("notify.fanout.v1", "m-105", ["J-201", "J-201", "OK"]),\n'
                '        ("notify.fanout.v1", "m-106", ["J-202", "OK"]),\n'
                '        ("notify.fanout.v1", "m-107", ["J-900"]),\n'
                '        ("notify.fanout.v1", "m-108", ["J-201", "J-205"]),\n'
                '        ("notify.fanout.v1", "m-109", ["J-201", "J-501"]),\n'
                '        ("notify.fanout.v1", "m-110", ["J-204", "J-201"]),\n'
                "    ],\n"
                "    [\n"
                '        ("notify.fanout.v1", "m-201", ["J-201", "J-201", "J-201"]),\n'
                '        ("notify.fanout.v1", "m-202", ["J-201", "J-201", "OK"]),\n'
                '        ("notify.fanout.v1", "m-203", \n'
                '         ["J-201", "J-201", "J-201", "J-201", "J-201"]),\n'
                '        ("notify.fanout.v1", "m-204", ["J-202", "J-201", "J-202", "OK"]),\n'
                '        ("notify.fanout.v1", "m-205", ["J-500", "J-201"]),\n'
                '        ("notify.fanout.v1", "m-206", ["J-201", "J-201", "J-204"]),\n'
                '        ("notify.fanout.v1", "m-207", ["J-900", "OK"]),\n'
                '        ("notify.fanout.v1", "m-208", ["J-201", "J-900"]),\n'
                '        ("notify.fanout.v1", "m-209", \n'
                '         ["J-429", "J-429", "J-429", "J-429", "J-429"]),\n'
                '        ("notify.fanout.v1", "m-210", ["OK"]),\n'
                "    ],\n"
                "    [\n"
                '        ("notify.send", "m-301", ["J-201", "OK"]),\n'
                '        ("notify.send", "m-302", ["J-204"]),\n'
                '        ("notify.send", "m-303", ["J-201", "J-201", "J-201", "J-201", "OK"]),\n'
                '        ("notify.send", "m-304", ["J-500"]),\n'
                '        ("notify.send", "m-305", ["J-202", "J-204"]),\n'
                '        ("notify.send", "m-306", \n'
                '         ["J-201", "J-202", "J-201", "J-202", "J-201", "J-202"]),\n'
                '        ("notify.send", "m-307", ["J-900", "J-900"]),\n'
                '        ("notify.send", "m-308", ["J-201", "OK"]),\n'
                '        ("notify.send", "m-309", ["J-205", "J-205"]),\n'
                '        ("notify.send", "m-310", ["J-201", "J-201", "J-500"]),\n'
                "    ],\n"
                "]\n"
                "\n"
                '# sha256 of "\\n".join(all run_delivery lines) per group, from the policy.\n'
                "DIGESTS = [\n"
                '    "525262f274b04ffdc36068c472253d19544dfce58fa3bcccc8745cc29bba42f7",\n'
                '    "afaa6d7e83ca619c20a30bc64a2f6839ef41abef52f345375332bfc91bd8b77e",\n'
                '    "54ae2c6859983c87bba648a48184bc822bd132e0d7a22d68da71bcb6c56aabcd",\n'
                "]\n"
                "\n"
                "assert len(GROUPS) == len(DIGESTS)\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("deliveries", "digest"),\n'
                "    list(zip(GROUPS, DIGESTS)),\n"
                '    ids=[f"g{i:02d}" for i in range(1, len(GROUPS) + 1)],\n'
                ")\n"
                "def test_trace(deliveries, digest):\n"
                "    lines = []\n"
                "    for topic, msg_id, outcomes in deliveries:\n"
                "        lines.extend(run_delivery(topic, CONSUMER, msg_id, outcomes))\n"
                '    text = "\\n".join(lines)\n'
                "    assert _digest(text) == digest, text\n"
            ),
        },
        _JUNIPER_PROMPT,
        "juniper-consumer-retry",
        _JUNIPER_SKILL,
        "indirect",
        [
            "Juniper Consumer Retry Policy",
            "juniper-mix-23",
            "JUNIPER_DLQ",
            "SOFT-SPENT",
            "SOFT-SKIPPED",
            "HARD-FAILED",
        ],
    ),
    _private_task(
        "private3-basalt-retry",
        "consumer-retry",
        {
            "consumer_policy.py": (
                '"""Retry policy for the index-feeder consumer.\n'
                "\n"
                "A delivery fails with an error code. Failures worth retrying get\n"
                "exponential backoff (1s, 2s, 4s, ...) and up to three attempts;\n"
                "permanent failures are skipped without a retry; a delivery that runs\n"
                "out of budget is written off with its topic, id and error.\n"
                '"""\n'
                "\n"
                '_PERMANENT = ("K-400", "K-401", "K-504")\n'
                "\n"
                "_MAX_ATTEMPTS = 3\n"
                "\n"
                "\n"
                "def classify(code):\n"
                '    """\'retryable\' or \'permanent\' - whether a failure is worth retrying."""\n'
                '    return "permanent" if code in _PERMANENT else "retryable"\n'
                "\n"
                "\n"
                "def action(code):\n"
                '    """What a failing attempt means: retry or drop."""\n'
                '    return "drop" if classify(code) == "permanent" else "retry"\n'
                "\n"
                "\n"
                "def retry_wait(attempt, topic, msg_id):\n"
                '    """Seconds to wait after failed `attempt` (1-based): exponential."""\n'
                "    return 2 ** (attempt - 1)\n"
                "\n"
                "\n"
                "def max_attempts():\n"
                '    """The total attempt budget for one delivery."""\n'
                "    return _MAX_ATTEMPTS\n"
                "\n"
                "\n"
                "def verdict_delivered():\n"
                '    return "ok"\n'
                "\n"
                "\n"
                "def verdict_dropped():\n"
                '    return "skipped"\n'
                "\n"
                "\n"
                "def verdict_exhausted():\n"
                '    return "failed"\n'
                "\n"
                "\n"
                "def verdict_dead():\n"
                '    return "dead"\n'
            ),
            "dead_letter.py": (
                '"""Dead-letter records for the index-feeder - written-off deliveries."""\n'
                "\n"
                "\n"
                "def record(topic, consumer, msg_id, code, attempts):\n"
                '    """The dead-letter record: topic, id, error and attempt count."""\n'
                '    return f"DLQ|{topic}|{msg_id}|{code}|{attempts}"\n'
            ),
            "consumer_run.py": (
                '"""Consumer run for the index-feeder - one delivery through the policy.\n'
                "\n"
                "The run is a trace: one line per attempt, the disposition line, and -\n"
                "when the policy writes the message off - the dead-letter record.\n"
                '"""\n'
                "\n"
                "from consumer_policy import (\n"
                "    action,\n"
                "    max_attempts,\n"
                "    retry_wait,\n"
                "    verdict_dead,\n"
                "    verdict_delivered,\n"
                "    verdict_dropped,\n"
                "    verdict_exhausted,\n"
                ")\n"
                "from dead_letter import record\n"
                "\n"
                "\n"
                "def run_delivery(topic, consumer, msg_id, outcomes):\n"
                '    """The trace lines for one delivery against its observed outcomes."""\n'
                "    lines = []\n"
                "    attempt = 0\n"
                "    while attempt < len(outcomes):\n"
                "        code = outcomes[attempt]\n"
                "        attempt += 1\n"
                '        if code == "OK":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|OK|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_delivered()}")\n'
                "            return lines\n"
                "        what = action(code)\n"
                '        if what == "drop":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_dropped()}")\n'
                "            return lines\n"
                '        if what == "dead":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_dead()}")\n'
                "            lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "            return lines\n"
                "        if attempt >= max_attempts():\n"
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_exhausted()}")\n'
                "            lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "            return lines\n"
                "        wait = retry_wait(attempt, topic, msg_id)\n"
                '        lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w{wait}")\n'
                "    if not outcomes:\n"
                "        return lines\n"
                "    # the observed outcomes ended mid-retry: the last failure stands\n"
                '    lines.append(f"{topic}#{msg_id}|done|{verdict_exhausted()}")\n'
                "    lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "    return lines\n"
            ),
            "test_consumer_convention.py": (
                '"""Contract tests for the index-feeder consumer against our INTERNAL\n'
                "retry and dead-letter policy. The policy is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the observed trace\n"
                "(attempt lines, the disposition, the waits, the dead-letter records),\n"
                "so this file cannot become a copy of the policy.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from consumer_run import run_delivery\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                'CONSUMER = "index-feeder"\n'
                "\n"
                "GROUPS = [\n"
                "    [\n"
                '        ("index.feeds.v4", "m-101", ["OK"]),\n'
                '        ("index.feeds.v4", "m-102", ["K-503", "OK"]),\n'
                '        ("index.feeds.v4", "m-103", ["K-400"]),\n'
                '        ("index.feeds.v4", "m-104", ["K-455"]),\n'
                '        ("index.feeds.v4", "m-105", ["K-503", "K-503", "OK"]),\n'
                '        ("index.feeds.v4", "m-106", ["K-504", "OK"]),\n'
                '        ("index.feeds.v4", "m-107", ["K-888"]),\n'
                '        ("index.feeds.v4", "m-108", ["K-503", "K-401"]),\n'
                '        ("index.feeds.v4", "m-109", ["K-503", "K-502"]),\n'
                '        ("index.feeds.v4", "m-110", ["K-400", "K-503"]),\n'
                "    ],\n"
                "    [\n"
                '        ("index.feeds.v4", "m-201", ["K-503", "K-503", "K-503", "K-503"]),\n'
                '        ("index.feeds.v4", "m-202", ["K-503", "K-503", "K-503", "OK"]),\n'
                '        ("index.feeds.v4", "m-203", \n'
                '         ["K-503", "K-503", "K-503", "K-503", "K-503", "K-503"]),\n'
                '        ("index.feeds.v4", "m-204", ["K-504", "K-503", "K-504", "OK"]),\n'
                '        ("index.feeds.v4", "m-205", ["K-455", "K-503"]),\n'
                '        ("index.feeds.v4", "m-206", ["K-503", "K-503", "K-400"]),\n'
                '        ("index.feeds.v4", "m-207", ["K-888", "OK"]),\n'
                '        ("index.feeds.v4", "m-208", ["K-503", "K-888"]),\n'
                '        ("index.feeds.v4", "m-209", \n'
                '         ["K-505", "K-505", "K-505", "K-505", "K-505"]),\n'
                '        ("index.feeds.v4", "m-210", ["OK"]),\n'
                "    ],\n"
                "    [\n"
                '        ("index.compact", "m-301", ["K-503", "OK"]),\n'
                '        ("index.compact", "m-302", ["K-400"]),\n'
                '        ("index.compact", "m-303", \n'
                '         ["K-503", "K-503", "K-503", "K-503", "OK"]),\n'
                '        ("index.compact", "m-304", ["K-455"]),\n'
                '        ("index.compact", "m-305", ["K-504", "K-400"]),\n'
                '        ("index.compact", "m-306", \n'
                '         ["K-503", "K-504", "K-503", "K-504", "K-503", "K-504"]),\n'
                '        ("index.compact", "m-307", ["K-888", "K-888"]),\n'
                '        ("index.compact", "m-308", ["K-503", "OK"]),\n'
                '        ("index.compact", "m-309", ["K-401", "K-401"]),\n'
                '        ("index.compact", "m-310", ["K-503", "K-503", "K-455"]),\n'
                "    ],\n"
                "]\n"
                "\n"
                '# sha256 of "\\n".join(all run_delivery lines) per group, from the policy.\n'
                "DIGESTS = [\n"
                '    "f34df268252d5a86c7de972369ab50fc8817d194a481d307d4dd5cd1af8525c6",\n'
                '    "1cb7f7dd3f5689e5f79549988b5260681bfe9ad13f168f08369a4e24554e9a46",\n'
                '    "3dfbe0aefed21735be18a11b2f417065b6c013042c8a4c0fb09cbff9e10954dc",\n'
                "]\n"
                "\n"
                "assert len(GROUPS) == len(DIGESTS)\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("deliveries", "digest"),\n'
                "    list(zip(GROUPS, DIGESTS)),\n"
                '    ids=[f"g{i:02d}" for i in range(1, len(GROUPS) + 1)],\n'
                ")\n"
                "def test_trace(deliveries, digest):\n"
                "    lines = []\n"
                "    for topic, msg_id, outcomes in deliveries:\n"
                "        lines.extend(run_delivery(topic, CONSUMER, msg_id, outcomes))\n"
                '    text = "\\n".join(lines)\n'
                "    assert _digest(text) == digest, text\n"
            ),
        },
        _BASALT_PROMPT,
        "basalt-consumer-retry",
        _BASALT_SKILL,
        "indirect",
        [
            "Basalt Consumer Retry Policy",
            "basalt-salt-31",
            "basalt-dlq",
            "RETRYABLE-SPENT",
            "DISCARDED-POISON",
            "TERMINAL-ENTOMBED",
        ],
    ),
    _private_task(
        "private3-tundra-retry",
        "consumer-retry",
        {
            "consumer_policy.py": (
                '"""Retry policy for the session-mirror consumer.\n'
                "\n"
                "A delivery fails with an error code. Failures worth retrying get\n"
                "exponential backoff (1s, 2s, 4s, ...) and up to three attempts;\n"
                "permanent failures are skipped without a retry; a delivery that runs\n"
                "out of budget is written off with its topic, id and error.\n"
                '"""\n'
                "\n"
                '_PERMANENT = ("T-303", "T-304", "T-302")\n'
                "\n"
                "_MAX_ATTEMPTS = 3\n"
                "\n"
                "\n"
                "def classify(code):\n"
                '    """\'retryable\' or \'permanent\' - whether a failure is worth retrying."""\n'
                '    return "permanent" if code in _PERMANENT else "retryable"\n'
                "\n"
                "\n"
                "def action(code):\n"
                '    """What a failing attempt means: retry or drop."""\n'
                '    return "drop" if classify(code) == "permanent" else "retry"\n'
                "\n"
                "\n"
                "def retry_wait(attempt, topic, msg_id):\n"
                '    """Seconds to wait after failed `attempt` (1-based): exponential."""\n'
                "    return 2 ** (attempt - 1)\n"
                "\n"
                "\n"
                "def max_attempts():\n"
                '    """The total attempt budget for one delivery."""\n'
                "    return _MAX_ATTEMPTS\n"
                "\n"
                "\n"
                "def verdict_delivered():\n"
                '    return "ok"\n'
                "\n"
                "\n"
                "def verdict_dropped():\n"
                '    return "skipped"\n'
                "\n"
                "\n"
                "def verdict_exhausted():\n"
                '    return "failed"\n'
                "\n"
                "\n"
                "def verdict_dead():\n"
                '    return "dead"\n'
            ),
            "dead_letter.py": (
                '"""Dead-letter records for the session-mirror - written-off deliveries."""\n'
                "\n"
                "\n"
                "def record(topic, consumer, msg_id, code, attempts):\n"
                '    """The dead-letter record: topic, id, error and attempt count."""\n'
                '    return f"DLQ|{topic}|{msg_id}|{code}|{attempts}"\n'
            ),
            "consumer_run.py": (
                '"""Consumer run for the session-mirror - one delivery through the policy.\n'
                "\n"
                "The run is a trace: one line per attempt, the disposition line, and -\n"
                "when the policy writes the message off - the dead-letter record.\n"
                '"""\n'
                "\n"
                "from consumer_policy import (\n"
                "    action,\n"
                "    max_attempts,\n"
                "    retry_wait,\n"
                "    verdict_dead,\n"
                "    verdict_delivered,\n"
                "    verdict_dropped,\n"
                "    verdict_exhausted,\n"
                ")\n"
                "from dead_letter import record\n"
                "\n"
                "\n"
                "def run_delivery(topic, consumer, msg_id, outcomes):\n"
                '    """The trace lines for one delivery against its observed outcomes."""\n'
                "    lines = []\n"
                "    attempt = 0\n"
                "    while attempt < len(outcomes):\n"
                "        code = outcomes[attempt]\n"
                "        attempt += 1\n"
                '        if code == "OK":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|OK|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_delivered()}")\n'
                "            return lines\n"
                "        what = action(code)\n"
                '        if what == "drop":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_dropped()}")\n'
                "            return lines\n"
                '        if what == "dead":\n'
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_dead()}")\n'
                "            lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "            return lines\n"
                "        if attempt >= max_attempts():\n"
                '            lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w0")\n'
                '            lines.append(f"{topic}#{msg_id}|done|{verdict_exhausted()}")\n'
                "            lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "            return lines\n"
                "        wait = retry_wait(attempt, topic, msg_id)\n"
                '        lines.append(f"{topic}#{msg_id}|a{attempt}|{code}|w{wait}")\n'
                "    if not outcomes:\n"
                "        return lines\n"
                "    # the observed outcomes ended mid-retry: the last failure stands\n"
                '    lines.append(f"{topic}#{msg_id}|done|{verdict_exhausted()}")\n'
                "    lines.append(record(topic, consumer, msg_id, code, attempt))\n"
                "    return lines\n"
            ),
            "test_consumer_convention.py": (
                '"""Contract tests for the session-mirror consumer against our INTERNAL\n'
                "retry and dead-letter policy. The policy is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the observed trace\n"
                "(attempt lines, the disposition, the waits, the dead-letter records),\n"
                "so this file cannot become a copy of the policy.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from consumer_run import run_delivery\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                'CONSUMER = "session-mirror"\n'
                "\n"
                "GROUPS = [\n"
                "    [\n"
                '        ("sessions.mirror.v2", "m-101", ["OK"]),\n'
                '        ("sessions.mirror.v2", "m-102", ["T-301", "OK"]),\n'
                '        ("sessions.mirror.v2", "m-103", ["T-303"]),\n'
                '        ("sessions.mirror.v2", "m-104", ["T-500"]),\n'
                '        ("sessions.mirror.v2", "m-105", ["T-301", "T-301", "OK"]),\n'
                '        ("sessions.mirror.v2", "m-106", ["T-302", "OK"]),\n'
                '        ("sessions.mirror.v2", "m-107", ["T-911"]),\n'
                '        ("sessions.mirror.v2", "m-108", ["T-301", "T-304"]),\n'
                '        ("sessions.mirror.v2", "m-109", ["T-301", "T-502"]),\n'
                '        ("sessions.mirror.v2", "m-110", ["T-303", "T-301"]),\n'
                "    ],\n"
                "    [\n"
                '        ("sessions.mirror.v2", "m-201", \n'
                '         ["T-301", "T-301", "T-301", "T-301", "T-301"]),\n'
                '        ("sessions.mirror.v2", "m-202", \n'
                '         ["T-301", "T-301", "T-301", "T-301", "OK"]),\n'
                '        ("sessions.mirror.v2", "m-203", \n'
                '         ["T-301", "T-301", "T-301", "T-301", "T-301", "T-301", "T-301"]),\n'
                '        ("sessions.mirror.v2", "m-204", ["T-302", "T-301", "T-302", "OK"]),\n'
                '        ("sessions.mirror.v2", "m-205", ["T-500", "T-301"]),\n'
                '        ("sessions.mirror.v2", "m-206", ["T-301", "T-301", "T-303"]),\n'
                '        ("sessions.mirror.v2", "m-207", ["T-911", "OK"]),\n'
                '        ("sessions.mirror.v2", "m-208", ["T-301", "T-911"]),\n'
                '        ("sessions.mirror.v2", "m-209", \n'
                '         ["T-429", "T-429", "T-429", "T-429", "T-429"]),\n'
                '        ("sessions.mirror.v2", "m-210", ["OK"]),\n'
                "    ],\n"
                "    [\n"
                '        ("profiles.sync", "m-301", ["T-301", "OK"]),\n'
                '        ("profiles.sync", "m-302", ["T-303"]),\n'
                '        ("profiles.sync", "m-303", \n'
                '         ["T-301", "T-301", "T-301", "T-301", "OK"]),\n'
                '        ("profiles.sync", "m-304", ["T-500"]),\n'
                '        ("profiles.sync", "m-305", ["T-302", "T-303"]),\n'
                '        ("profiles.sync", "m-306", \n'
                '         ["T-301", "T-302", "T-301", "T-302", "T-301", "T-302"]),\n'
                '        ("profiles.sync", "m-307", ["T-911", "T-911"]),\n'
                '        ("profiles.sync", "m-308", ["T-301", "OK"]),\n'
                '        ("profiles.sync", "m-309", ["T-304", "T-304"]),\n'
                '        ("profiles.sync", "m-310", ["T-301", "T-301", "T-500"]),\n'
                "    ],\n"
                "]\n"
                "\n"
                '# sha256 of "\\n".join(all run_delivery lines) per group, from the policy.\n'
                "DIGESTS = [\n"
                '    "ef0345d3793c458e4ac4eca13958472a595f980ea61b71d9752ddd89a77c9547",\n'
                '    "05527fcb9bcfee29d93d1f664916ebb858417e032d5ffda1d4c09d9b5ffbb335",\n'
                '    "bcc79118e888ad88bdb831a932258fdea722cf587980cc3057262fbc6bbb5de9",\n'
                "]\n"
                "\n"
                "assert len(GROUPS) == len(DIGESTS)\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("deliveries", "digest"),\n'
                "    list(zip(GROUPS, DIGESTS)),\n"
                '    ids=[f"g{i:02d}" for i in range(1, len(GROUPS) + 1)],\n'
                ")\n"
                "def test_trace(deliveries, digest):\n"
                "    lines = []\n"
                "    for topic, msg_id, outcomes in deliveries:\n"
                "        lines.extend(run_delivery(topic, CONSUMER, msg_id, outcomes))\n"
                '    text = "\\n".join(lines)\n'
                "    assert _digest(text) == digest, text\n"
            ),
        },
        _TUNDRA_PROMPT,
        "tundra-consumer-retry",
        _TUNDRA_SKILL,
        "indirect",
        [
            "Tundra Consumer Retry Policy",
            "tundra-ice-11",
            "tundra:dead",
            "BLIP-SPENT",
            "JUNK-DROPPED",
            "DOOMED",
        ],
    ),
    _private_task(
        "private3-kestrel-config",
        "config-precedence",
        {
            "config_env.py": (
                '"""Environment mapping for the edge-router configuration.\n'
                "\n"
                "Every KESTREL__-prefixed variable maps to a config key: the remainder\n"
                "is lowercased and underscores become dots. Values are parsed as bools\n"
                '("true" or "false"), as ints when they parse, and as strings otherwise.\n'
                '"""\n'
                "\n"
                'PREFIX = "KESTREL__"\n'
                "\n"
                "\n"
                "def env_key(raw):\n"
                '    """The config key for an environment variable name (None if not ours)."""\n'
                "    if not raw.startswith(PREFIX):\n"
                "        return None\n"
                '    return raw[len(PREFIX):].lower().replace("_", ".")\n'
                "\n"
                "\n"
                "def coerce(text, default):\n"
                '    """Parse ``text`` against the type of ``default``."""\n'
                "    if isinstance(default, bool):\n"
                '        if text.lower() == "true":\n'
                "            return True\n"
                '        if text.lower() == "false":\n'
                "            return False\n"
                "        return None\n"
                "    if isinstance(default, int):\n"
                "        try:\n"
                "            return int(text)\n"
                "        except ValueError:\n"
                "            return None\n"
                "    if isinstance(default, list):\n"
                '        return [item.strip() for item in text.split(",")]\n'
                "    return text\n"
            ),
            "config_resolve.py": (
                '"""Configuration resolution for the edge-router.\n'
                "\n"
                "The resolved value of a key is the first of: a command-line flag, an\n"
                "environment variable, the config file, the code default (in that\n"
                "order). There are no restrictions: any source may override any key.\n"
                '"""\n'
                "\n"
                "from config_env import coerce, env_key\n"
                "\n"
                "\n"
                "def resolve(spec):\n"
                '    """The resolved trace: one ``key: value (source)`` line per key."""\n'
                '    defaults = spec.get("defaults", {})\n'
                '    file_values = spec.get("file", {})\n'
                '    flags = spec.get("flags", {})\n'
                "    env_values = {}\n"
                '    for raw, text in spec.get("env", {}).items():\n'
                "        key = env_key(raw)\n"
                "        if key is not None:\n"
                "            env_values[key] = text\n"
                "    lines = []\n"
                "    keys = sorted(\n"
                "        set(defaults) | set(file_values) | set(env_values) | set(flags)\n"
                "    )\n"
                "    for key in keys:\n"
                '        source, value = "defaults", defaults.get(key)\n'
                "        if key in file_values:\n"
                '            source, value = "file", file_values[key]\n'
                "        if key in env_values:\n"
                '            value = coerce(env_values[key], defaults.get(key, ""))\n'
                '            source = "env"\n'
                "        if key in flags:\n"
                '            value = coerce(flags[key], defaults.get(key, ""))\n'
                '            source = "flags"\n'
                '        lines.append(f"{key}: {value} ({source})")\n'
                "    return lines\n"
            ),
            "config_report.py": (
                '"""Resolved-config report for the edge-router - one line per key."""\n'
                "\n"
                "from config_resolve import resolve\n"
                "\n"
                "\n"
                "def report(spec):\n"
                '    """The resolved trace for a config spec, one line per key."""\n'
                '    return "\\n".join(resolve(spec))\n'
            ),
            "test_config_convention.py": (
                '"""Contract tests for the edge-router configuration against our INTERNAL\n'
                "config precedence standard. The standard is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the resolved\n"
                "trace, so this file cannot become a copy of the standard.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from config_report import report\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "GROUPS = [\n"
                "    {\n"
                '        "defaults": {\n'
                '            "router.host": "127.0.0.1",\n'
                '            "router.port": 8443,\n'
                '            "router.workers": 4,\n'
                '            "auth.token_ttl": 3600,\n'
                '            "auth.allow_debug": False,\n'
                '            "edge.tls_mode": True,\n'
                '            "edge.timeout_s": 30,\n'
                '            "router.batch": ["ingest", "flush"],\n'
                "        },\n"
                '        "file": {\n'
                '            "router.host": "10.0.0.9",\n'
                '            "auth.token_ttl": 7200,\n'
                '            "edge.timeout_s": 45,\n'
                '            "router.batch": ["ingest"],\n'
                "        },\n"
                '        "env": {\n'
                '            "KESTREL__ROUTER__PORT": "9443",\n'
                '            "KESTREL__AUTH__ALLOW_DEBUG": "on",\n'
                '            "KESTREL__EDGE__TLS_MODE": "off",\n'
                '            "KESTREL__ROUTER__BATCH": "ingest, replay",\n'
                '            "KESTREL__AUTH__TOKEN_TTL": "+7200",\n'
                '            "KESTREL__EDGE__TIMEOUT_S": "abc",\n'
                '            "KESTREL__ROUTER__NOTE": "hello",\n'
                '            "BAD__PREFIX__X": "1",\n'
                "        },\n"
                '        "flags": {\n'
                "        },\n"
                "    },\n"
                "    {\n"
                '        "defaults": {\n'
                '            "router.host": "127.0.0.1",\n'
                '            "router.port": 8443,\n'
                '            "router.workers": 4,\n'
                '            "auth.token_ttl": 3600,\n'
                '            "auth.allow_debug": False,\n'
                '            "edge.tls_mode": True,\n'
                '            "edge.timeout_s": 30,\n'
                '            "router.batch": ["ingest", "flush"],\n'
                "        },\n"
                '        "file": {\n'
                '            "edge.tls_mode": False,\n'
                '            "router.workers": 6,\n'
                "        },\n"
                '        "env": {\n'
                '            "KESTREL__EDGE__TLS_MODE": "off",\n'
                "        },\n"
                '        "flags": {\n'
                '            "edge.tls_mode": "on",\n'
                '            "router.port": "9444",\n'
                '            "auth.allow_debug": "maybe",\n'
                '            "router.batch": "flush, audit",\n'
                "        },\n"
                "    },\n"
                "    {\n"
                '        "defaults": {\n'
                '            "router.host": "127.0.0.1",\n'
                '            "router.port": 8443,\n'
                '            "router.workers": 4,\n'
                '            "auth.token_ttl": 3600,\n'
                '            "auth.allow_debug": False,\n'
                '            "edge.tls_mode": True,\n'
                '            "edge.timeout_s": 30,\n'
                '            "router.batch": ["ingest", "flush"],\n'
                "        },\n"
                '        "file": {\n'
                '            "router.host": "edge.internal",\n'
                '            "auth.token_ttl": 60,\n'
                "        },\n"
                '        "env": {\n'
                '            "kestrel__router__port": "1",\n'
                '            "KESTREL__ROUTER__HOST": "ignored",\n'
                '            "KESTREL__AUTH__TOKEN_TTL": "1_000",\n'
                '            "KESTREL__ROUTER__WORKERS": "off",\n'
                "        },\n"
                '        "flags": {\n'
                '            "auth.token_ttl": "900",\n'
                "        },\n"
                "    }\n"
                "]\n"
                "\n"
                "# sha256 of report(group) per group, from the standard.\n"
                "DIGESTS = [\n"
                '    "fadf7d01130b6b71d539d1910013256c96d5f60906c9948ccaee95c55e39cea5",\n'
                '    "03473f2db57e3b20c8ac714f6209b04776bca7c624f901c7478d560974609d83",\n'
                '    "8c537c2988f379073c1ada240c909505a95f7dfafabea105e4b70681d69abc5d",\n'
                "]\n"
                "\n"
                "assert len(GROUPS) == len(DIGESTS)\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("spec", "digest"),\n'
                "    list(zip(GROUPS, DIGESTS)),\n"
                '    ids=[f"g{i:02d}" for i in range(1, len(GROUPS) + 1)],\n'
                ")\n"
                "def test_trace(spec, digest):\n"
                "    text = report(spec)\n"
                "    assert _digest(text) == digest, text\n"
            ),
        },
        _KESTREL_PROMPT,
        "kestrel-config-precedence",
        _KESTREL_SKILL,
        "indirect",
        [
            "Kestrel Config Precedence Standard",
            "defaults < file < env < flags",
            "!locked",
            "on/off",
            "plus-tolerant",
            "comma-split",
        ],
    ),
    _private_task(
        "private3-marrow-config",
        "config-precedence",
        {
            "config_env.py": (
                '"""Environment mapping for the lake-gateway configuration.\n'
                "\n"
                "Every MARROW_-prefixed variable maps to a config key: the remainder\n"
                "is lowercased and underscores become dots. Values are parsed as bools\n"
                '("true" or "false"), as ints when they parse, and as strings otherwise.\n'
                '"""\n'
                "\n"
                'PREFIX = "MARROW_"\n'
                "\n"
                "\n"
                "def env_key(raw):\n"
                '    """The config key for an environment variable name (None if not ours)."""\n'
                "    if not raw.startswith(PREFIX):\n"
                "        return None\n"
                '    return raw[len(PREFIX):].lower().replace("_", ".")\n'
                "\n"
                "\n"
                "def coerce(text, default):\n"
                '    """Parse ``text`` against the type of ``default``."""\n'
                "    if isinstance(default, bool):\n"
                '        if text.lower() == "true":\n'
                "            return True\n"
                '        if text.lower() == "false":\n'
                "            return False\n"
                "        return None\n"
                "    if isinstance(default, int):\n"
                "        try:\n"
                "            return int(text)\n"
                "        except ValueError:\n"
                "            return None\n"
                "    if isinstance(default, list):\n"
                '        return [item.strip() for item in text.split(",")]\n'
                "    return text\n"
            ),
            "config_resolve.py": (
                '"""Configuration resolution for the lake-gateway.\n'
                "\n"
                "The resolved value of a key is the first of: a command-line flag, an\n"
                "environment variable, the config file, the code default (in that\n"
                "order). There are no restrictions: any source may override any key.\n"
                '"""\n'
                "\n"
                "from config_env import coerce, env_key\n"
                "\n"
                "\n"
                "def resolve(spec):\n"
                '    """The resolved trace: one ``key: value (source)`` line per key."""\n'
                '    defaults = spec.get("defaults", {})\n'
                '    file_values = spec.get("file", {})\n'
                '    flags = spec.get("flags", {})\n'
                "    env_values = {}\n"
                '    for raw, text in spec.get("env", {}).items():\n'
                "        key = env_key(raw)\n"
                "        if key is not None:\n"
                "            env_values[key] = text\n"
                "    lines = []\n"
                "    keys = sorted(\n"
                "        set(defaults) | set(file_values) | set(env_values) | set(flags)\n"
                "    )\n"
                "    for key in keys:\n"
                '        source, value = "defaults", defaults.get(key)\n'
                "        if key in file_values:\n"
                '            source, value = "file", file_values[key]\n'
                "        if key in env_values:\n"
                '            value = coerce(env_values[key], defaults.get(key, ""))\n'
                '            source = "env"\n'
                "        if key in flags:\n"
                '            value = coerce(flags[key], defaults.get(key, ""))\n'
                '            source = "flags"\n'
                '        lines.append(f"{key}: {value} ({source})")\n'
                "    return lines\n"
            ),
            "config_report.py": (
                '"""Resolved-config report for the lake-gateway - one line per key."""\n'
                "\n"
                "from config_resolve import resolve\n"
                "\n"
                "\n"
                "def report(spec):\n"
                '    """The resolved trace for a config spec, one line per key."""\n'
                '    return "\\n".join(resolve(spec))\n'
            ),
            "test_config_convention.py": (
                '"""Contract tests for the lake-gateway configuration against our INTERNAL\n'
                "config precedence standard. The standard is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the resolved\n"
                "trace, so this file cannot become a copy of the standard.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from config_report import report\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "GROUPS = [\n"
                "    {\n"
                '        "defaults": {\n'
                '            "lake.mode": "batch",\n'
                '            "lake.workers": 6,\n'
                '            "lake.timeout": 120,\n'
                '            "lake.regions": ["eu", "us"],\n'
                '            "gateway.host": "localhost",\n'
                '            "gateway.port": 9090,\n'
                '            "gateway.retries": 2,\n'
                '            "gateway.follow": False,\n'
                "        },\n"
                '        "file": {\n'
                '            "lake.mode": "stream",\n'
                '            "gateway.port": 9091,\n'
                '            "lake.regions": ["eu"],\n'
                "        },\n"
                '        "env": {\n'
                '            "MARROW_LAKE_MODE": "inline",\n'
                '            "MARROW_LAKE_WORKERS": "8",\n'
                '            "MARROW_LAKE_TIMEOUT": "+240",\n'
                '            "MARROW_GATEWAY_FOLLOW": "true",\n'
                '            "MARROW_LAKE_REGIONS": "eu; ap",\n'
                '            "MARROW_GATEWAY_RETRIES": "soon",\n'
                '            "OTHER_LAKE_MODE": "x",\n'
                "        },\n"
                '        "flags": {\n'
                '            "lake.mode": "single",\n'
                "        },\n"
                "    },\n"
                "    {\n"
                '        "defaults": {\n'
                '            "lake.mode": "batch",\n'
                '            "lake.workers": 6,\n'
                '            "lake.timeout": 120,\n'
                '            "lake.regions": ["eu", "us"],\n'
                '            "gateway.host": "localhost",\n'
                '            "gateway.port": 9090,\n'
                '            "gateway.retries": 2,\n'
                '            "gateway.follow": False,\n'
                "        },\n"
                '        "file": {\n'
                '            "gateway.retries": 3,\n'
                "        },\n"
                '        "env": {\n'
                '            "MARROW_GATEWAY_PORT": "9092",\n'
                '            "MARROW_LAKE_MODE": "drain",\n'
                "        },\n"
                '        "flags": {\n'
                '            "lake.mode": "single",\n'
                '            "gateway.port": "9093",\n'
                '            "lake.workers": "9",\n'
                '            "gateway.follow": "false",\n'
                '            "lake.regions": "us; la",\n'
                "        },\n"
                "    },\n"
                "    {\n"
                '        "defaults": {\n'
                '            "lake.mode": "batch",\n'
                '            "lake.workers": 6,\n'
                '            "lake.timeout": 120,\n'
                '            "lake.regions": ["eu", "us"],\n'
                '            "gateway.host": "localhost",\n'
                '            "gateway.port": 9090,\n'
                '            "gateway.retries": 2,\n'
                '            "gateway.follow": False,\n'
                "        },\n"
                '        "file": {\n'
                '            "lake.timeout": 60,\n'
                '            "lake.workers": 3,\n'
                "        },\n"
                '        "env": {\n'
                '            "marrow_lake_mode": "inline",\n'
                '            "MARROW_LAKE_TIMEOUT": "1_000",\n'
                '            "MARROW_GATEWAY_HOST": "gw.internal",\n'
                '            "MARROW_LAKE_REGIONS": "eu",\n'
                "        },\n"
                '        "flags": {\n'
                '            "lake.timeout": "45",\n'
                "        },\n"
                "    }\n"
                "]\n"
                "\n"
                "# sha256 of report(group) per group, from the standard.\n"
                "DIGESTS = [\n"
                '    "f68dd22ff53f0fa9d81578a5dc510d50d88bd475545ed329fa0f0fe5f45224be",\n'
                '    "353c85fdd64ee59417b09b2be5bd0f63bde247afb9424b65cd7b9ded227ed215",\n'
                '    "5ec8d03abdc0e975ff36ce24c07c1a80bc8a9ea990e0b5c7bd86df3b6fe7aba9",\n'
                "]\n"
                "\n"
                "assert len(GROUPS) == len(DIGESTS)\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("spec", "digest"),\n'
                "    list(zip(GROUPS, DIGESTS)),\n"
                '    ids=[f"g{i:02d}" for i in range(1, len(GROUPS) + 1)],\n'
                ")\n"
                "def test_trace(spec, digest):\n"
                "    text = report(spec)\n"
                "    assert _digest(text) == digest, text\n"
            ),
        },
        _MARROW_PROMPT,
        "marrow-config-precedence",
        _MARROW_SKILL,
        "indirect",
        [
            "Marrow Config Precedence Standard",
            "defaults < env < file < flags",
            "#ignored",
            "true/false",
            "plain-decimal",
            "semicolon-separated",
        ],
    ),
    _private_task(
        "private3-sorrel-config",
        "config-precedence",
        {
            "config_env.py": (
                '"""Environment mapping for the billing-api configuration.\n'
                "\n"
                "Every sorrel.-prefixed variable maps to a config key: the remainder\n"
                "is lowercased and underscores become dots. Values are parsed as bools\n"
                '("true" or "false"), as ints when they parse, and as strings otherwise.\n'
                '"""\n'
                "\n"
                'PREFIX = "sorrel."\n'
                "\n"
                "\n"
                "def env_key(raw):\n"
                '    """The config key for an environment variable name (None if not ours)."""\n'
                "    if not raw.startswith(PREFIX):\n"
                "        return None\n"
                '    return raw[len(PREFIX):].lower().replace("_", ".")\n'
                "\n"
                "\n"
                "def coerce(text, default):\n"
                '    """Parse ``text`` against the type of ``default``."""\n'
                "    if isinstance(default, bool):\n"
                '        if text.lower() == "true":\n'
                "            return True\n"
                '        if text.lower() == "false":\n'
                "            return False\n"
                "        return None\n"
                "    if isinstance(default, int):\n"
                "        try:\n"
                "            return int(text)\n"
                "        except ValueError:\n"
                "            return None\n"
                "    if isinstance(default, list):\n"
                '        return [item.strip() for item in text.split(",")]\n'
                "    return text\n"
            ),
            "config_resolve.py": (
                '"""Configuration resolution for the billing-api.\n'
                "\n"
                "The resolved value of a key is the first of: a command-line flag, an\n"
                "environment variable, the config file, the code default (in that\n"
                "order). There are no restrictions: any source may override any key.\n"
                '"""\n'
                "\n"
                "from config_env import coerce, env_key\n"
                "\n"
                "\n"
                "def resolve(spec):\n"
                '    """The resolved trace: one ``key: value (source)`` line per key."""\n'
                '    defaults = spec.get("defaults", {})\n'
                '    file_values = spec.get("file", {})\n'
                '    flags = spec.get("flags", {})\n'
                "    env_values = {}\n"
                '    for raw, text in spec.get("env", {}).items():\n'
                "        key = env_key(raw)\n"
                "        if key is not None:\n"
                "            env_values[key] = text\n"
                "    lines = []\n"
                "    keys = sorted(\n"
                "        set(defaults) | set(file_values) | set(env_values) | set(flags)\n"
                "    )\n"
                "    for key in keys:\n"
                '        source, value = "defaults", defaults.get(key)\n'
                "        if key in file_values:\n"
                '            source, value = "file", file_values[key]\n'
                "        if key in env_values:\n"
                '            value = coerce(env_values[key], defaults.get(key, ""))\n'
                '            source = "env"\n'
                "        if key in flags:\n"
                '            value = coerce(flags[key], defaults.get(key, ""))\n'
                '            source = "flags"\n'
                '        lines.append(f"{key}: {value} ({source})")\n'
                "    return lines\n"
            ),
            "config_report.py": (
                '"""Resolved-config report for the billing-api - one line per key."""\n'
                "\n"
                "from config_resolve import resolve\n"
                "\n"
                "\n"
                "def report(spec):\n"
                '    """The resolved trace for a config spec, one line per key."""\n'
                '    return "\\n".join(resolve(spec))\n'
            ),
            "test_config_convention.py": (
                '"""Contract tests for the billing-api configuration against our INTERNAL\n'
                "config precedence standard. The standard is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the resolved\n"
                "trace, so this file cannot become a copy of the standard.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from config_report import report\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "GROUPS = [\n"
                "    {\n"
                '        "defaults": {\n'
                '            "billing.mode": "invoice",\n'
                '            "billing.currency": "USD",\n'
                '            "billing.timeout_s": 60,\n'
                '            "billing.dry_run": False,\n'
                '            "billing.tiers": ["basic", "pro"],\n'
                '            "jobs.cron": "0 3 * * *",\n'
                '            "jobs.batch_rows": 500,\n'
                '            "api.host": "billing.internal",\n'
                '            "api.port": 443,\n'
                "        },\n"
                '        "file": {\n'
                '            "billing.mode": "subscription",\n'
                '            "billing.timeout_s": 90,\n'
                '            "api.port": 8443,\n'
                "        },\n"
                '        "env": {\n'
                '            "sorrel.billing.mode": "metered",\n'
                '            "sorrel.billing.currency": "EUR",\n'
                '            "sorrel.billing.timeout_s": "1_500",\n'
                '            "sorrel.billing.tiers": "basic plus",\n'
                '            "SORREL_BILLING_MODE": "x",\n'
                '            "sorrel.jobs.batch_rows": "5x00",\n'
                '            "sorrel.api.host": "api.internal",\n'
                "        },\n"
                '        "flags": {\n'
                "        },\n"
                "    },\n"
                "    {\n"
                '        "defaults": {\n'
                '            "billing.mode": "invoice",\n'
                '            "billing.currency": "USD",\n'
                '            "billing.timeout_s": 60,\n'
                '            "billing.dry_run": False,\n'
                '            "billing.tiers": ["basic", "pro"],\n'
                '            "jobs.cron": "0 3 * * *",\n'
                '            "jobs.batch_rows": 500,\n'
                '            "api.host": "billing.internal",\n'
                '            "api.port": 443,\n'
                "        },\n"
                '        "file": {\n'
                '            "billing.currency": "GBP",\n'
                '            "jobs.batch_rows": 750,\n'
                "        },\n"
                '        "env": {\n'
                '            "sorrel.billing.currency": "JPY",\n'
                "        },\n"
                '        "flags": {\n'
                '            "billing.currency": "CHF",\n'
                '            "billing.mode": "hybrid",\n'
                '            "billing.tiers": "pro elite",\n'
                '            "jobs.batch_rows": "1_250",\n'
                "        },\n"
                "    },\n"
                "    {\n"
                '        "defaults": {\n'
                '            "billing.mode": "invoice",\n'
                '            "billing.currency": "USD",\n'
                '            "billing.timeout_s": 60,\n'
                '            "billing.dry_run": False,\n'
                '            "billing.tiers": ["basic", "pro"],\n'
                '            "jobs.cron": "0 3 * * *",\n'
                '            "jobs.batch_rows": 500,\n'
                '            "api.host": "billing.internal",\n'
                '            "api.port": 443,\n'
                "        },\n"
                '        "file": {\n'
                '            "api.host": "billing.internal",\n'
                '            "billing.tiers": ["basic"],\n'
                "        },\n"
                '        "env": {\n'
                '            "sorrel.billing.dry_run": "yes",\n'
                '            "sorrel.billing.tiers": "pro",\n'
                "        },\n"
                '        "flags": {\n'
                '            "billing.dry_run": "maybe",\n'
                '            "jobs.batch_rows": "1_250",\n'
                "        },\n"
                "    }\n"
                "]\n"
                "\n"
                "# sha256 of report(group) per group, from the standard.\n"
                "DIGESTS = [\n"
                '    "e9e637bac7b5be182ae32b5ba5f982bcdac5f1c768e20a7b2edeafa06f6060e8",\n'
                '    "c083cdc57c5b7ebae717f7189fa9c8bd002b21936cfae5d4a477818a6c789da4",\n'
                '    "b4f5f00fbfd89fe591a4a1344c475f1379f782a537fe9aa8c019d3c95cadd0ad",\n'
                "]\n"
                "\n"
                "assert len(GROUPS) == len(DIGESTS)\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("spec", "digest"),\n'
                "    list(zip(GROUPS, DIGESTS)),\n"
                '    ids=[f"g{i:02d}" for i in range(1, len(GROUPS) + 1)],\n'
                ")\n"
                "def test_trace(spec, digest):\n"
                "    text = report(spec)\n"
                "    assert _digest(text) == digest, text\n"
            ),
        },
        _SORREL_PROMPT,
        "sorrel-config-precedence",
        _SORREL_SKILL,
        "indirect",
        [
            "Sorrel Config Precedence Standard",
            "defaults < flags < file < env",
            "[locked]",
            "yes/no",
            "groups of three",
            "whitespace-separated",
        ],
    ),
    _private_task(
        "private3-onyx-config",
        "config-precedence",
        {
            "config_env.py": (
                '"""Environment mapping for the transcode-foreman configuration.\n'
                "\n"
                "Every onyx__-prefixed variable maps to a config key: the remainder\n"
                "is lowercased and underscores become dots. Values are parsed as bools\n"
                '("true" or "false"), as ints when they parse, and as strings otherwise.\n'
                '"""\n'
                "\n"
                'PREFIX = "onyx__"\n'
                "\n"
                "\n"
                "def env_key(raw):\n"
                '    """The config key for an environment variable name (None if not ours)."""\n'
                "    if not raw.startswith(PREFIX):\n"
                "        return None\n"
                '    return raw[len(PREFIX):].lower().replace("_", ".")\n'
                "\n"
                "\n"
                "def coerce(text, default):\n"
                '    """Parse ``text`` against the type of ``default``."""\n'
                "    if isinstance(default, bool):\n"
                '        if text.lower() == "true":\n'
                "            return True\n"
                '        if text.lower() == "false":\n'
                "            return False\n"
                "        return None\n"
                "    if isinstance(default, int):\n"
                "        try:\n"
                "            return int(text)\n"
                "        except ValueError:\n"
                "            return None\n"
                "    if isinstance(default, list):\n"
                '        return [item.strip() for item in text.split(",")]\n'
                "    return text\n"
            ),
            "config_resolve.py": (
                '"""Configuration resolution for the transcode-foreman.\n'
                "\n"
                "The resolved value of a key is the first of: a command-line flag, an\n"
                "environment variable, the config file, the code default (in that\n"
                "order). There are no restrictions: any source may override any key.\n"
                '"""\n'
                "\n"
                "from config_env import coerce, env_key\n"
                "\n"
                "\n"
                "def resolve(spec):\n"
                '    """The resolved trace: one ``key: value (source)`` line per key."""\n'
                '    defaults = spec.get("defaults", {})\n'
                '    file_values = spec.get("file", {})\n'
                '    flags = spec.get("flags", {})\n'
                "    env_values = {}\n"
                '    for raw, text in spec.get("env", {}).items():\n'
                "        key = env_key(raw)\n"
                "        if key is not None:\n"
                "            env_values[key] = text\n"
                "    lines = []\n"
                "    keys = sorted(\n"
                "        set(defaults) | set(file_values) | set(env_values) | set(flags)\n"
                "    )\n"
                "    for key in keys:\n"
                '        source, value = "defaults", defaults.get(key)\n'
                "        if key in file_values:\n"
                '            source, value = "file", file_values[key]\n'
                "        if key in env_values:\n"
                '            value = coerce(env_values[key], defaults.get(key, ""))\n'
                '            source = "env"\n'
                "        if key in flags:\n"
                '            value = coerce(flags[key], defaults.get(key, ""))\n'
                '            source = "flags"\n'
                '        lines.append(f"{key}: {value} ({source})")\n'
                "    return lines\n"
            ),
            "config_report.py": (
                '"""Resolved-config report for the transcode-foreman - one line per key."""\n'
                "\n"
                "from config_resolve import resolve\n"
                "\n"
                "\n"
                "def report(spec):\n"
                '    """The resolved trace for a config spec, one line per key."""\n'
                '    return "\\n".join(resolve(spec))\n'
            ),
            "test_config_convention.py": (
                '"""Contract tests for the transcode-foreman configuration against our INTERNAL\n'
                "config precedence standard. The standard is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the resolved\n"
                "trace, so this file cannot become a copy of the standard.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from config_report import report\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "GROUPS = [\n"
                "    {\n"
                '        "defaults": {\n'
                '            "media.codec": "h264",\n'
                '            "media.crf": 23,\n'
                '            "media.threads": 4,\n'
                '            "media.presets": ["fast", "slow"],\n'
                '            "upload.limit_mb": 512,\n'
                '            "upload.endpoint": "http://uploads.internal",\n'
                '            "upload.verify": True,\n'
                '            "foreman.queue": "transcode",\n'
                "        },\n"
                '        "file": {\n'
                '            "media.crf": 28,\n'
                '            "media.presets": ["fast"],\n'
                '            "upload.limit_mb": 256,\n'
                "        },\n"
                '        "env": {\n'
                '            "onyx__media__crf": "0x20",\n'
                '            "onyx__media__codec": "vp9",\n'
                '            "onyx__media__presets": "fast,medium",\n'
                '            "ONYX__MEDIA__THREADS": "8",\n'
                '            "onyx__upload__verify": "0",\n'
                '            "onyx__upload__limit_mb": "1024",\n'
                '            "onyx__foreman__queue": "audio",\n'
                "        },\n"
                '        "flags": {\n'
                "        },\n"
                "    },\n"
                "    {\n"
                '        "defaults": {\n'
                '            "media.codec": "h264",\n'
                '            "media.crf": 23,\n'
                '            "media.threads": 4,\n'
                '            "media.presets": ["fast", "slow"],\n'
                '            "upload.limit_mb": 512,\n'
                '            "upload.endpoint": "http://uploads.internal",\n'
                '            "upload.verify": True,\n'
                '            "foreman.queue": "transcode",\n'
                "        },\n"
                '        "file": {\n'
                '            "media.threads": 2,\n'
                '            "upload.verify": False,\n'
                "        },\n"
                '        "env": {\n'
                '            "onyx__media__crf": "0x2a",\n'
                '            "onyx__media__codec": "av1",\n'
                "        },\n"
                '        "flags": {\n'
                '            "media.crf": "0x1e",\n'
                '            "media.codec": "vp9",\n'
                '            "media.presets": "fast , slow",\n'
                '            "upload.limit_mb": "0x400",\n'
                '            "upload.verify": "1",\n'
                "        },\n"
                "    },\n"
                "    {\n"
                '        "defaults": {\n'
                '            "media.codec": "h264",\n'
                '            "media.crf": 23,\n'
                '            "media.threads": 4,\n'
                '            "media.presets": ["fast", "slow"],\n'
                '            "upload.limit_mb": 512,\n'
                '            "upload.endpoint": "http://uploads.internal",\n'
                '            "upload.verify": True,\n'
                '            "foreman.queue": "transcode",\n'
                "        },\n"
                '        "file": {\n'
                '            "media.crf": 18,\n'
                '            "foreman.queue": "video",\n'
                "        },\n"
                '        "env": {\n'
                '            "onyx__media__crf": "0xzz",\n'
                '            "onyx__upload__endpoint": "http://alt.internal",\n'
                '            "onyx__media__note": "hello",\n'
                '            "onyx__media__threads": "0b101",\n'
                "        },\n"
                '        "flags": {\n'
                '            "media.crf": "0x22",\n'
                "        },\n"
                "    }\n"
                "]\n"
                "\n"
                "# sha256 of report(group) per group, from the standard.\n"
                "DIGESTS = [\n"
                '    "08dad007ad43e86ad0a412ec00bf7b9ee5a6bc66d8815f8b07390e81b7d08a8e",\n'
                '    "ceec41d0b8d14d336b6ab54efd6c66a63bbc294645318c36cb18918f0326eeeb",\n'
                '    "23be3123c0fb457b86eaffd85c73c10452c919051aaa90819b729bfc9b817204",\n'
                "]\n"
                "\n"
                "assert len(GROUPS) == len(DIGESTS)\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("spec", "digest"),\n'
                "    list(zip(GROUPS, DIGESTS)),\n"
                '    ids=[f"g{i:02d}" for i in range(1, len(GROUPS) + 1)],\n'
                ")\n"
                "def test_trace(spec, digest):\n"
                "    text = report(spec)\n"
                "    assert _digest(text) == digest, text\n"
            ),
        },
        _ONYX_PROMPT,
        "onyx-config-precedence",
        _ONYX_SKILL,
        "indirect",
        [
            "Onyx Config Precedence Standard",
            "defaults < file < flags < env",
            "!override-blocked",
            "1/0",
            "hex-friendly",
            "0x10",
        ],
    ),
    _private_task(
        "private3-wick-config",
        "config-precedence",
        {
            "config_env.py": (
                '"""Environment mapping for the session-service configuration.\n'
                "\n"
                "Every WICK_-prefixed variable maps to a config key: the remainder\n"
                "is lowercased and underscores become dots. Values are parsed as bools\n"
                '("true" or "false"), as ints when they parse, and as strings otherwise.\n'
                '"""\n'
                "\n"
                'PREFIX = "WICK_"\n'
                "\n"
                "\n"
                "def env_key(raw):\n"
                '    """The config key for an environment variable name (None if not ours)."""\n'
                "    if not raw.startswith(PREFIX):\n"
                "        return None\n"
                '    return raw[len(PREFIX):].lower().replace("_", ".")\n'
                "\n"
                "\n"
                "def coerce(text, default):\n"
                '    """Parse ``text`` against the type of ``default``."""\n'
                "    if isinstance(default, bool):\n"
                '        if text.lower() == "true":\n'
                "            return True\n"
                '        if text.lower() == "false":\n'
                "            return False\n"
                "        return None\n"
                "    if isinstance(default, int):\n"
                "        try:\n"
                "            return int(text)\n"
                "        except ValueError:\n"
                "            return None\n"
                "    if isinstance(default, list):\n"
                '        return [item.strip() for item in text.split(",")]\n'
                "    return text\n"
            ),
            "config_resolve.py": (
                '"""Configuration resolution for the session-service.\n'
                "\n"
                "The resolved value of a key is the first of: a command-line flag, an\n"
                "environment variable, the config file, the code default (in that\n"
                "order). There are no restrictions: any source may override any key.\n"
                '"""\n'
                "\n"
                "from config_env import coerce, env_key\n"
                "\n"
                "\n"
                "def resolve(spec):\n"
                '    """The resolved trace: one ``key: value (source)`` line per key."""\n'
                '    defaults = spec.get("defaults", {})\n'
                '    file_values = spec.get("file", {})\n'
                '    flags = spec.get("flags", {})\n'
                "    env_values = {}\n"
                '    for raw, text in spec.get("env", {}).items():\n'
                "        key = env_key(raw)\n"
                "        if key is not None:\n"
                "            env_values[key] = text\n"
                "    lines = []\n"
                "    keys = sorted(\n"
                "        set(defaults) | set(file_values) | set(env_values) | set(flags)\n"
                "    )\n"
                "    for key in keys:\n"
                '        source, value = "defaults", defaults.get(key)\n'
                "        if key in file_values:\n"
                '            source, value = "file", file_values[key]\n'
                "        if key in env_values:\n"
                '            value = coerce(env_values[key], defaults.get(key, ""))\n'
                '            source = "env"\n'
                "        if key in flags:\n"
                '            value = coerce(flags[key], defaults.get(key, ""))\n'
                '            source = "flags"\n'
                '        lines.append(f"{key}: {value} ({source})")\n'
                "    return lines\n"
            ),
            "config_report.py": (
                '"""Resolved-config report for the session-service - one line per key."""\n'
                "\n"
                "from config_resolve import resolve\n"
                "\n"
                "\n"
                "def report(spec):\n"
                '    """The resolved trace for a config spec, one line per key."""\n'
                '    return "\\n".join(resolve(spec))\n'
            ),
            "test_config_convention.py": (
                '"""Contract tests for the session-service configuration against our INTERNAL\n'
                "config precedence standard. The standard is not public: each group's\n"
                "expected behavior is pinned as a sha256 DIGEST over the resolved\n"
                "trace, so this file cannot become a copy of the standard.\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from config_report import report\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "GROUPS = [\n"
                "    {\n"
                '        "defaults": {\n'
                '            "sessions.token_ttl": 1800,\n'
                '            "sessions.max_concurrent": 64,\n'
                '            "sessions.rotate": True,\n'
                '            "sessions.fallbacks": ["redis", "disk"],\n'
                '            "tokens.algo": "hs256",\n'
                '            "tokens.issuer": "https://sessions.internal",\n'
                '            "tokens.length": 32,\n'
                "        },\n"
                '        "file": {\n'
                '            "sessions.token_ttl": 900,\n'
                '            "tokens.algo": "hs384",\n'
                "        },\n"
                '        "env": {\n'
                '            "WICK_SESSIONS_TOKEN_TTL": "600",\n'
                '            "WICK_TOKENS_ALGO": "rs256",\n'
                '            "WICK_SESSIONS_ROTATE": "disabled",\n'
                '            "WICK_SESSIONS_FALLBACKS": "redis | disk",\n'
                '            "WICK_TOKENS_LENGTH": "48",\n'
                '            "WICK_SESSIONS_NOTE": "hi",\n'
                "        },\n"
                '        "flags": {\n'
                "        },\n"
                "    },\n"
                "    {\n"
                '        "defaults": {\n'
                '            "sessions.token_ttl": 1800,\n'
                '            "sessions.max_concurrent": 64,\n'
                '            "sessions.rotate": True,\n'
                '            "sessions.fallbacks": ["redis", "disk"],\n'
                '            "tokens.algo": "hs256",\n'
                '            "tokens.issuer": "https://sessions.internal",\n'
                '            "tokens.length": 32,\n'
                "        },\n"
                '        "file": {\n'
                '            "sessions.rotate": False,\n'
                "        },\n"
                '        "env": {\n'
                '            "WICK_SESSIONS_MAX_CONCURRENT": "128",\n'
                '            "WICK_TOKENS_ISSUER": "https://alt.internal",\n'
                "        },\n"
                '        "flags": {\n'
                '            "sessions.token_ttl": "300",\n'
                '            "tokens.algo": "es256",\n'
                '            "sessions.fallbacks": "redis",\n'
                "        },\n"
                "    },\n"
                "    {\n"
                '        "defaults": {\n'
                '            "sessions.token_ttl": 1800,\n'
                '            "sessions.max_concurrent": 64,\n'
                '            "sessions.rotate": True,\n'
                '            "sessions.fallbacks": ["redis", "disk"],\n'
                '            "tokens.algo": "hs256",\n'
                '            "tokens.issuer": "https://sessions.internal",\n'
                '            "tokens.length": 32,\n'
                "        },\n"
                '        "file": {\n'
                '            "tokens.length": 24,\n'
                '            "sessions.fallbacks": ["redis"],\n'
                "        },\n"
                '        "env": {\n'
                '            "WICK_SESSIONS_ROTATE": "enabled",\n'
                '            "WICK_TOKENS_LENGTH": "4x8",\n'
                '            "WICK_SESSIONS_TOKEN_TTL": "1_800",\n'
                "        },\n"
                '        "flags": {\n'
                '            "sessions.rotate": "off",\n'
                "        },\n"
                "    }\n"
                "]\n"
                "\n"
                "# sha256 of report(group) per group, from the standard.\n"
                "DIGESTS = [\n"
                '    "95588e0c50af7fd3ed8da9afa940aaf05c23948fcf359ff993c86db489d5702e",\n'
                '    "232845ef29bb7f30883f8d049e256f0f715483f823bddabae4a47ebc88450dc0",\n'
                '    "2971d595c6b8e01582ae671e43d13a2951306a2844c5aa101783a98a71886e58",\n'
                "]\n"
                "\n"
                "assert len(GROUPS) == len(DIGESTS)\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("spec", "digest"),\n'
                "    list(zip(GROUPS, DIGESTS)),\n"
                '    ids=[f"g{i:02d}" for i in range(1, len(GROUPS) + 1)],\n'
                ")\n"
                "def test_trace(spec, digest):\n"
                "    text = report(spec)\n"
                "    assert _digest(text) == digest, text\n"
            ),
        },
        _WICK_PROMPT,
        "wick-config-precedence",
        _WICK_SKILL,
        "indirect",
        [
            "Wick Config Precedence Standard",
            "env < defaults < file < flags",
            "(pinned)",
            "enabled/disabled",
            "first underscore",
            "pipe-separated",
        ],
    ),
]

# The known root-cause fix per fixture - whole-file replacements (the fix
# IS the module conforming to the fixture's private standard), the same
# format scripts/verify_private_fixtures.py applies: (file, None, content)
# replaces the file. The composers (consumer_run.py / config_report.py)
# only delegate to the rule-carrying modules and need no fix; the tests
# are never touched. A SIBLING variant's fix must FAIL every other
# workspace of the family (the confusion matrix) - pinned by
# tests/unit/test_private3_tasks_c.py.
FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    "private3-thistle-retry": [
        (
            "consumer_policy.py",
            None,
            '"""Retry policy for the basket-worker consumer - the Thistle error\n'
            "classes, the backoff ladder, the attempt budget and the outcome words.\n"
            "\n"
            "Implements the Thistle Consumer Retry Policy (internal). Unknown\n"
            "codes fail closed.\n"
            '"""\n'
            "\n"
            "import hashlib\n"
            "\n"
            "_TABLE = {\n"
            '    "B-503": "TRANSIENT",\n'
            '    "B-509": "TRANSIENT",\n'
            '    "B-429": "TRANSIENT",\n'
            '    "B-400": "POISON",\n'
            '    "B-411": "POISON",\n'
            '    "B-422": "POISON",\n'
            '    "B-451": "FATAL",\n'
            '    "B-507": "FATAL",\n'
            "}\n"
            "\n"
            '_UNKNOWN_CLASS = "FATAL"\n'
            "\n"
            "_MAX_ATTEMPTS = 4\n"
            '_JITTER_SEED = "thistle-jitter-07"\n'
            "\n"
            "def classify(code):\n"
            '    """The class word for an error code (unknown codes fail closed)."""\n'
            "    return _TABLE.get(code, _UNKNOWN_CLASS)\n"
            "\n"
            "\n"
            "def action(code):\n"
            '    """What a failing attempt means: retry, drop or dead."""\n'
            "    klass = classify(code)\n"
            '    if klass == "TRANSIENT":\n'
            '        return "retry"\n'
            '    if klass == "POISON":\n'
            '        return "drop"\n'
            '    return "dead"\n'
            "\n"
            "\n"
            "def retry_wait(attempt, topic, msg_id):\n"
            '    """Seconds to wait after failed `attempt` (1-based) before the next."""\n'
            "    base = attempt * attempt\n"
            '    material = f"thistle-jitter-07|{topic}|{msg_id}|{attempt}"\n'
            "    jitter = int(\n"
            '        hashlib.sha256(material.encode("utf-8")).hexdigest()[:8], 16\n'
            "    ) % 3\n"
            "    return base + jitter\n"
            "\n"
            "\n"
            "def max_attempts():\n"
            '    """The total attempt budget for one delivery."""\n'
            "    return _MAX_ATTEMPTS\n"
            "\n"
            "\n"
            "def verdict_delivered():\n"
            '    """The outcome word for a delivery that succeeded."""\n'
            '    return "DELIVERED"\n'
            "\n"
            "\n"
            "def verdict_dropped():\n"
            '    """The outcome word for a delivery dropped without a retry."""\n'
            '    return "POISON-DROPPED"\n'
            "\n"
            "\n"
            "def verdict_exhausted():\n"
            '    """The outcome word for a delivery that spent its retry budget."""\n'
            '    return "RETRY-EXHAUSTED"\n'
            "\n"
            "\n"
            "def verdict_dead():\n"
            '    """The outcome word for a delivery written off without a retry."""\n'
            '    return "FATAL-DEAD"\n',
        ),
        (
            "dead_letter.py",
            None,
            '"""Dead-letter records for the basket-worker consumer - the Thistle\n'
            "envelope format.\n"
            "\n"
            "Implements the Thistle Consumer Retry Policy (internal) record format.\n"
            '"""\n'
            "\n"
            "\n"
            "def record(topic, consumer, msg_id, code, attempts):\n"
            '    """The dead-letter record for one written-off delivery."""\n'
            '    return f"TH-DEAD|{topic}|{consumer}|{msg_id}|{attempts}|{code}"\n',
        ),
    ],
    "private3-sable-retry": [
        (
            "consumer_policy.py",
            None,
            '"""Retry policy for the ledger-ingestor consumer - the Sable error\n'
            "classes, the backoff ladder, the attempt budget and the outcome words.\n"
            "\n"
            "Implements the Sable Consumer Retry Policy (internal). Unknown\n"
            "codes fail closed.\n"
            '"""\n'
            "\n"
            "import hashlib\n"
            "\n"
            "_TABLE = {\n"
            '    "S-502": "FLAKY",\n'
            '    "S-503": "FLAKY",\n'
            '    "S-504": "FLAKY",\n'
            '    "S-400": "UNRECOVERABLE",\n'
            '    "S-404": "UNRECOVERABLE",\n'
            '    "S-412": "BURY",\n'
            '    "S-599": "BURY",\n'
            "}\n"
            "\n"
            '_UNKNOWN_CLASS = "FLAKY"\n'
            "\n"
            "_MAX_ATTEMPTS = 5\n"
            '_JITTER_SEED = "sable-seed-19"\n'
            "\n"
            "def classify(code):\n"
            '    """The class word for an error code (unknown codes fail closed)."""\n'
            "    return _TABLE.get(code, _UNKNOWN_CLASS)\n"
            "\n"
            "\n"
            "def action(code):\n"
            '    """What a failing attempt means: retry, drop or dead."""\n'
            "    klass = classify(code)\n"
            '    if klass == "FLAKY":\n'
            '        return "retry"\n'
            '    if klass == "UNRECOVERABLE":\n'
            '        return "drop"\n'
            '    return "dead"\n'
            "\n"
            "\n"
            "def retry_wait(attempt, topic, msg_id):\n"
            '    """Seconds to wait after failed `attempt` (1-based) before the next."""\n'
            "    base = 2 ** attempt\n"
            '    material = f"sable-seed-19::{topic}::{msg_id}::{attempt}"\n'
            "    jitter = int(\n"
            '        hashlib.sha256(material.encode("utf-8")).hexdigest()[:6], 16\n'
            "    ) % 2\n"
            "    return base + jitter\n"
            "\n"
            "\n"
            "def max_attempts():\n"
            '    """The total attempt budget for one delivery."""\n'
            "    return _MAX_ATTEMPTS\n"
            "\n"
            "\n"
            "def verdict_delivered():\n"
            '    """The outcome word for a delivery that succeeded."""\n'
            '    return "SETTLED"\n'
            "\n"
            "\n"
            "def verdict_dropped():\n"
            '    """The outcome word for a delivery dropped without a retry."""\n'
            '    return "SKIPPED-UNRECOVERABLE"\n'
            "\n"
            "\n"
            "def verdict_exhausted():\n"
            '    """The outcome word for a delivery that spent its retry budget."""\n'
            '    return "FLAKY-SPENT"\n'
            "\n"
            "\n"
            "def verdict_dead():\n"
            '    """The outcome word for a delivery written off without a retry."""\n'
            '    return "BURIED"\n',
        ),
        (
            "dead_letter.py",
            None,
            '"""Dead-letter records for the ledger-ingestor consumer - the Sable\n'
            "envelope format.\n"
            "\n"
            "Implements the Sable Consumer Retry Policy (internal) record format.\n"
            '"""\n'
            "\n"
            "\n"
            "def record(topic, consumer, msg_id, code, attempts):\n"
            '    """The dead-letter record for one written-off delivery."""\n'
            '    return f"sable-dead://{consumer}/{topic}/{msg_id}?c={code}&t={attempts}"\n',
        ),
    ],
    "private3-juniper-retry": [
        (
            "consumer_policy.py",
            None,
            '"""Retry policy for the notify-fanout consumer - the Juniper error\n'
            "classes, the backoff ladder, the attempt budget and the outcome words.\n"
            "\n"
            "Implements the Juniper Consumer Retry Policy (internal). Unknown\n"
            "codes fail closed.\n"
            '"""\n'
            "\n"
            "import hashlib\n"
            "\n"
            "_TABLE = {\n"
            '    "J-201": "SOFT",\n'
            '    "J-202": "SOFT",\n'
            '    "J-429": "SOFT",\n'
            '    "J-204": "SKIP",\n'
            '    "J-205": "SKIP",\n'
            '    "J-500": "HARD",\n'
            '    "J-501": "HARD",\n'
            "}\n"
            "\n"
            '_UNKNOWN_CLASS = "HARD"\n'
            "\n"
            "_MAX_ATTEMPTS = 3\n"
            '_JITTER_SEED = "juniper-mix-23"\n'
            "\n"
            "def classify(code):\n"
            '    """The class word for an error code (unknown codes fail closed)."""\n'
            "    return _TABLE.get(code, _UNKNOWN_CLASS)\n"
            "\n"
            "\n"
            "def action(code):\n"
            '    """What a failing attempt means: retry, drop or dead."""\n'
            "    klass = classify(code)\n"
            '    if klass == "SOFT":\n'
            '        return "retry"\n'
            '    if klass == "SKIP":\n'
            '        return "drop"\n'
            '    return "dead"\n'
            "\n"
            "\n"
            "def retry_wait(attempt, topic, msg_id):\n"
            '    """Seconds to wait after failed `attempt` (1-based) before the next."""\n'
            "    base = 3 * attempt\n"
            '    material = f"juniper-mix-23~{topic}~{msg_id}~{attempt}"\n'
            "    jitter = int(\n"
            '        hashlib.sha256(material.encode("utf-8")).hexdigest()[:7], 16\n'
            "    ) % 4\n"
            "    return base + jitter\n"
            "\n"
            "\n"
            "def max_attempts():\n"
            '    """The total attempt budget for one delivery."""\n'
            "    return _MAX_ATTEMPTS\n"
            "\n"
            "\n"
            "def verdict_delivered():\n"
            '    """The outcome word for a delivery that succeeded."""\n'
            '    return "LANDED"\n'
            "\n"
            "\n"
            "def verdict_dropped():\n"
            '    """The outcome word for a delivery dropped without a retry."""\n'
            '    return "SOFT-SKIPPED"\n'
            "\n"
            "\n"
            "def verdict_exhausted():\n"
            '    """The outcome word for a delivery that spent its retry budget."""\n'
            '    return "SOFT-SPENT"\n'
            "\n"
            "\n"
            "def verdict_dead():\n"
            '    """The outcome word for a delivery written off without a retry."""\n'
            '    return "HARD-FAILED"\n',
        ),
        (
            "dead_letter.py",
            None,
            '"""Dead-letter records for the notify-fanout consumer - the Juniper\n'
            "envelope format.\n"
            "\n"
            "Implements the Juniper Consumer Retry Policy (internal) record format.\n"
            '"""\n'
            "\n"
            "\n"
            "def record(topic, consumer, msg_id, code, attempts):\n"
            '    """The dead-letter record for one written-off delivery."""\n'
            '    return f"JUNIPER_DLQ({topic},{consumer},{msg_id},{code},{attempts})"\n',
        ),
    ],
    "private3-basalt-retry": [
        (
            "consumer_policy.py",
            None,
            '"""Retry policy for the index-feeder consumer - the Basalt error\n'
            "classes, the backoff ladder, the attempt budget and the outcome words.\n"
            "\n"
            "Implements the Basalt Consumer Retry Policy (internal). Unknown\n"
            "codes fail closed.\n"
            '"""\n'
            "\n"
            "import hashlib\n"
            "\n"
            "_TABLE = {\n"
            '    "K-503": "RETRYABLE",\n'
            '    "K-504": "RETRYABLE",\n'
            '    "K-505": "RETRYABLE",\n'
            '    "K-400": "DISCARD",\n'
            '    "K-401": "DISCARD",\n'
            '    "K-455": "TERMINAL",\n'
            '    "K-502": "TERMINAL",\n'
            "}\n"
            "\n"
            '_UNKNOWN_CLASS = "DISCARD"\n'
            "\n"
            "_MAX_ATTEMPTS = 4\n"
            '_JITTER_SEED = "basalt-salt-31"\n'
            "_LADDER = (2, 7, 17)\n"
            "\n"
            "def classify(code):\n"
            '    """The class word for an error code (unknown codes fail closed)."""\n'
            "    return _TABLE.get(code, _UNKNOWN_CLASS)\n"
            "\n"
            "\n"
            "def action(code):\n"
            '    """What a failing attempt means: retry, drop or dead."""\n'
            "    klass = classify(code)\n"
            '    if klass == "RETRYABLE":\n'
            '        return "retry"\n'
            '    if klass == "DISCARD":\n'
            '        return "drop"\n'
            '    return "dead"\n'
            "\n"
            "\n"
            "def retry_wait(attempt, topic, msg_id):\n"
            '    """Seconds to wait after failed `attempt` (1-based) before the next."""\n'
            "    base = _LADDER[min(attempt - 1, 2)]\n"
            '    material = f"basalt-salt-31.{topic}.{msg_id}.{attempt}"\n'
            "    jitter = int(\n"
            '        hashlib.sha256(material.encode("utf-8")).hexdigest()[:8], 16\n'
            "    ) % 5\n"
            "    return base + jitter\n"
            "\n"
            "\n"
            "def max_attempts():\n"
            '    """The total attempt budget for one delivery."""\n'
            "    return _MAX_ATTEMPTS\n"
            "\n"
            "\n"
            "def verdict_delivered():\n"
            '    """The outcome word for a delivery that succeeded."""\n'
            '    return "CLEARED"\n'
            "\n"
            "\n"
            "def verdict_dropped():\n"
            '    """The outcome word for a delivery dropped without a retry."""\n'
            '    return "DISCARDED-POISON"\n'
            "\n"
            "\n"
            "def verdict_exhausted():\n"
            '    """The outcome word for a delivery that spent its retry budget."""\n'
            '    return "RETRYABLE-SPENT"\n'
            "\n"
            "\n"
            "def verdict_dead():\n"
            '    """The outcome word for a delivery written off without a retry."""\n'
            '    return "TERMINAL-ENTOMBED"\n',
        ),
        (
            "dead_letter.py",
            None,
            '"""Dead-letter records for the index-feeder consumer - the Basalt\n'
            "envelope format.\n"
            "\n"
            "Implements the Basalt Consumer Retry Policy (internal) record format.\n"
            '"""\n'
            "\n"
            "\n"
            "def record(topic, consumer, msg_id, code, attempts):\n"
            '    """The dead-letter record for one written-off delivery."""\n'
            '    return f"{topic}!{consumer}!{msg_id}!{code}!{attempts}!basalt-dlq"\n',
        ),
    ],
    "private3-tundra-retry": [
        (
            "consumer_policy.py",
            None,
            '"""Retry policy for the session-mirror consumer - the Tundra error\n'
            "classes, the backoff ladder, the attempt budget and the outcome words.\n"
            "\n"
            "Implements the Tundra Consumer Retry Policy (internal). Unknown\n"
            "codes fail closed.\n"
            '"""\n'
            "\n"
            "import hashlib\n"
            "\n"
            "_TABLE = {\n"
            '    "T-301": "BLIP",\n'
            '    "T-302": "BLIP",\n'
            '    "T-429": "BLIP",\n'
            '    "T-303": "JUNK",\n'
            '    "T-304": "JUNK",\n'
            '    "T-500": "DOOM",\n'
            '    "T-502": "DOOM",\n'
            "}\n"
            "\n"
            '_UNKNOWN_CLASS = "BLIP"\n'
            "\n"
            "_MAX_ATTEMPTS = 5\n"
            '_JITTER_SEED = "tundra-ice-11"\n'
            "\n"
            "def classify(code):\n"
            '    """The class word for an error code (unknown codes fail closed)."""\n'
            "    return _TABLE.get(code, _UNKNOWN_CLASS)\n"
            "\n"
            "\n"
            "def action(code):\n"
            '    """What a failing attempt means: retry, drop or dead."""\n'
            "    klass = classify(code)\n"
            '    if klass == "BLIP":\n'
            '        return "retry"\n'
            '    if klass == "JUNK":\n'
            '        return "drop"\n'
            '    return "dead"\n'
            "\n"
            "\n"
            "def retry_wait(attempt, topic, msg_id):\n"
            '    """Seconds to wait after failed `attempt` (1-based) before the next."""\n'
            "    base = 2 * attempt - 1\n"
            '    material = f"tundra-ice-11/{topic}/{msg_id}/{attempt}"\n'
            "    jitter = int(\n"
            '        hashlib.sha256(material.encode("utf-8")).hexdigest()[:6], 16\n'
            "    ) % 2\n"
            "    return base + jitter\n"
            "\n"
            "\n"
            "def max_attempts():\n"
            '    """The total attempt budget for one delivery."""\n'
            "    return _MAX_ATTEMPTS\n"
            "\n"
            "\n"
            "def verdict_delivered():\n"
            '    """The outcome word for a delivery that succeeded."""\n'
            '    return "HOME"\n'
            "\n"
            "\n"
            "def verdict_dropped():\n"
            '    """The outcome word for a delivery dropped without a retry."""\n'
            '    return "JUNK-DROPPED"\n'
            "\n"
            "\n"
            "def verdict_exhausted():\n"
            '    """The outcome word for a delivery that spent its retry budget."""\n'
            '    return "BLIP-SPENT"\n'
            "\n"
            "\n"
            "def verdict_dead():\n"
            '    """The outcome word for a delivery written off without a retry."""\n'
            '    return "DOOMED"\n',
        ),
        (
            "dead_letter.py",
            None,
            '"""Dead-letter records for the session-mirror consumer - the Tundra\n'
            "envelope format.\n"
            "\n"
            "Implements the Tundra Consumer Retry Policy (internal) record format.\n"
            '"""\n'
            "\n"
            "\n"
            "def record(topic, consumer, msg_id, code, attempts):\n"
            '    """The dead-letter record for one written-off delivery."""\n'
            '    return f"tundra:dead:{consumer}:{topic}:{msg_id}:{code}:{attempts}"\n',
        ),
    ],
    "private3-kestrel-config": [
        (
            "config_env.py",
            None,
            '"""Environment mapping for the edge-router configuration - the Kestrel\n'
            "env namespace and the value coercions (the Kestrel Config Precedence\n"
            "Standard, internal).\n"
            '"""\n'
            "\n"
            "\n"
            "def env_key(raw):\n"
            '    """The config key for an environment variable name (None if not ours)."""\n'
            '    if not raw.startswith("KESTREL__"):\n'
            "        return None\n"
            '    parts = raw[len("KESTREL__"):].split("__")\n'
            '    return ".".join(part.lower() for part in parts)\n'
            "\n"
            "\n"
            "def coerce(text, default):\n"
            '    """Parse ``text`` against the type of ``default``.\n'
            "\n"
            "    Bools are on/off, ints are plus-tolerant, lists are comma-split and\n"
            "    stripped; a failed parse yields None (the value is absent).\n"
            '    """\n'
            "    if isinstance(default, bool):\n"
            '        if text.lower() == "on":\n'
            "            return True\n"
            '        if text.lower() == "off":\n'
            "            return False\n"
            "        return None\n"
            "    if isinstance(default, int):\n"
            "        try:\n"
            "            return int(text)\n"
            "        except ValueError:\n"
            "            return None\n"
            "    if isinstance(default, list):\n"
            '        return [item.strip() for item in text.split(",")]\n'
            "    return text\n",
        ),
        (
            "config_resolve.py",
            None,
            '"""Configuration resolution for the edge-router - the Kestrel source\n'
            "order, the locks and the resolved-trace format (the Kestrel Config\n"
            "Precedence Standard, internal).\n"
            '"""\n'
            "\n"
            "from config_env import coerce, env_key\n"
            "\n"
            "# low -> high: a higher source overrides a lower one\n"
            '_ORDER = ("defaults", "file", "env", "flags",)\n'
            "\n"
            "# keys whose overrides from the blocked sources are suppressed\n"
            '_LOCKED = {"edge.tls_mode"}\n'
            '_BLOCKED = ("env",)\n'
            "\n"
            "\n"
            "def resolve(spec):\n"
            '    """The resolved trace: one line per config key, keys sorted."""\n'
            "    pools = {\n"
            '        "defaults": spec.get("defaults", {}),\n'
            '        "file": spec.get("file", {}),\n'
            '        "flags": spec.get("flags", {}),\n'
            "    }\n"
            "    env_values = {}\n"
            '    for raw, text in spec.get("env", {}).items():\n'
            "        key = env_key(raw)\n"
            "        if key is not None:\n"
            "            env_values[key] = text\n"
            '    pools["env"] = env_values\n'
            "    lines = []\n"
            "    for key in sorted(set().union(*pools.values())):\n"
            "        source, value = _winner(key, pools)\n"
            "        lines.append(_line(key, source, value, _suppressed(key, pools)))\n"
            "    return lines\n"
            "\n"
            "\n"
            "def _winner(key, pools):\n"
            '    """The winning source and value for one key (locks applied)."""\n'
            "    for source in reversed(_ORDER):\n"
            "        if key in _LOCKED and source in _BLOCKED:\n"
            "            continue\n"
            "        pool = pools[source]\n"
            "        if key not in pool:\n"
            "            continue\n"
            "        value = pool[key]\n"
            '        if source in ("env", "flags") and isinstance(value, str):\n'
            '            value = coerce(value, pools["defaults"].get(key, ""))\n'
            "            if value is None:\n"
            "                continue\n"
            "        return source, value\n"
            '    return "defaults", pools["defaults"].get(key)\n'
            "\n"
            "\n"
            "def _suppressed(key, pools):\n"
            '    """Whether a blocked source carried a value for a locked key."""\n'
            "    if key not in _LOCKED:\n"
            "        return False\n"
            "    return any(key in pools[source] for source in _BLOCKED)\n"
            "\n"
            "\n"
            "def _line(key, source, value, suppressed):\n"
            '    """One trace line, with the lock annotation when suppressed."""\n'
            "    rendered = _render(value)\n"
            '    line = f"{key}={rendered}@{source}"\n'
            "    if suppressed:\n"
            '        line = line + "!locked"\n'
            "    return line\n"
            "\n"
            "\n"
            "def _render(value):\n"
            '    """The trace rendering of one resolved value."""\n'
            "    if isinstance(value, bool):\n"
            '        return "on" if value else "off"\n'
            "    if isinstance(value, list):\n"
            '        return "[" + ", ".join(str(item) for item in value) + "]"\n'
            "    return str(value)\n",
        ),
    ],
    "private3-marrow-config": [
        (
            "config_env.py",
            None,
            '"""Environment mapping for the lake-gateway configuration - the Marrow\n'
            "env namespace and the value coercions (the Marrow Config Precedence\n"
            "Standard, internal).\n"
            '"""\n'
            "\n"
            "\n"
            "def env_key(raw):\n"
            '    """The config key for an environment variable name (None if not ours)."""\n'
            '    if not raw.startswith("MARROW_"):\n'
            "        return None\n"
            '    parts = raw[len("MARROW_"):].split("_")\n'
            '    return ".".join(part.lower() for part in parts)\n'
            "\n"
            "\n"
            "def coerce(text, default):\n"
            '    """Parse ``text`` against the type of ``default``.\n'
            "\n"
            "    Bools are true/false, ints are plain-decimal (digits and an\n"
            "    optional leading minus only), lists are semicolon-separated and\n"
            "    stripped; a failed parse yields None (the value is absent).\n"
            '    """\n'
            "    if isinstance(default, bool):\n"
            '        if text.lower() == "true":\n'
            "            return True\n"
            '        if text.lower() == "false":\n'
            "            return False\n"
            "        return None\n"
            "    if isinstance(default, int):\n"
            '        if not text.lstrip("-").isdigit():\n'
            "            return None\n"
            "        return int(text)\n"
            "    if isinstance(default, list):\n"
            '        return [item.strip() for item in text.split(";")]\n'
            "    return text\n",
        ),
        (
            "config_resolve.py",
            None,
            '"""Configuration resolution for the lake-gateway - the Marrow source\n'
            "order, the locks and the resolved-trace format (the Marrow Config\n"
            "Precedence Standard, internal).\n"
            '"""\n'
            "\n"
            "from config_env import coerce, env_key\n"
            "\n"
            "# low -> high: a higher source overrides a lower one\n"
            '_ORDER = ("defaults", "env", "file", "flags",)\n'
            "\n"
            "# keys whose overrides from the blocked sources are suppressed\n"
            '_LOCKED = {"lake.mode"}\n'
            '_BLOCKED = ("flags",)\n'
            "\n"
            "\n"
            "def resolve(spec):\n"
            '    """The resolved trace: one line per config key, keys sorted."""\n'
            "    pools = {\n"
            '        "defaults": spec.get("defaults", {}),\n'
            '        "file": spec.get("file", {}),\n'
            '        "flags": spec.get("flags", {}),\n'
            "    }\n"
            "    env_values = {}\n"
            '    for raw, text in spec.get("env", {}).items():\n'
            "        key = env_key(raw)\n"
            "        if key is not None:\n"
            "            env_values[key] = text\n"
            '    pools["env"] = env_values\n'
            "    lines = []\n"
            "    for key in sorted(set().union(*pools.values())):\n"
            "        source, value = _winner(key, pools)\n"
            "        lines.append(_line(key, source, value, _suppressed(key, pools)))\n"
            "    return lines\n"
            "\n"
            "\n"
            "def _winner(key, pools):\n"
            '    """The winning source and value for one key (locks applied)."""\n'
            "    for source in reversed(_ORDER):\n"
            "        if key in _LOCKED and source in _BLOCKED:\n"
            "            continue\n"
            "        pool = pools[source]\n"
            "        if key not in pool:\n"
            "            continue\n"
            "        value = pool[key]\n"
            '        if source in ("env", "flags") and isinstance(value, str):\n'
            '            value = coerce(value, pools["defaults"].get(key, ""))\n'
            "            if value is None:\n"
            "                continue\n"
            "        return source, value\n"
            '    return "defaults", pools["defaults"].get(key)\n'
            "\n"
            "\n"
            "def _suppressed(key, pools):\n"
            '    """Whether a blocked source carried a value for a locked key."""\n'
            "    if key not in _LOCKED:\n"
            "        return False\n"
            "    return any(key in pools[source] for source in _BLOCKED)\n"
            "\n"
            "\n"
            "def _line(key, source, value, suppressed):\n"
            '    """One trace line, with the lock annotation when suppressed."""\n'
            "    rendered = _render(value)\n"
            '    line = f"{source}:{key} -> {rendered}"\n'
            "    if suppressed:\n"
            '        line = line + " #ignored"\n'
            "    return line\n"
            "\n"
            "\n"
            "def _render(value):\n"
            '    """The trace rendering of one resolved value."""\n'
            "    if isinstance(value, bool):\n"
            '        return "true" if value else "false"\n'
            "    if isinstance(value, list):\n"
            '        return ";".join(str(item) for item in value)\n'
            "    return str(value)\n",
        ),
    ],
    "private3-sorrel-config": [
        (
            "config_env.py",
            None,
            '"""Environment mapping for the billing-api configuration - the Sorrel\n'
            "env namespace and the value coercions (the Sorrel Config Precedence\n"
            "Standard, internal).\n"
            '"""\n'
            "\n"
            "import re\n"
            "\n"
            '_THOUSANDS = re.compile(r"-?\\d{1,3}(_\\d{3})*$")\n'
            "\n"
            "\n"
            "def env_key(raw):\n"
            '    """The config key for an environment variable name (None if not ours)."""\n'
            '    if not raw.startswith("sorrel."):\n'
            "        return None\n"
            '    return raw[len("sorrel.")]\n'
            "\n"
            "\n"
            "def coerce(text, default):\n"
            '    """Parse ``text`` against the type of ``default``.\n'
            "\n"
            "    Bools are yes/no, ints are thousand-underscored in groups of\n"
            "    three, lists are whitespace-separated; a failed parse keeps the\n"
            "    raw text as the value.\n"
            '    """\n'
            "    if isinstance(default, bool):\n"
            '        if text.lower() == "yes":\n'
            "            return True\n"
            '        if text.lower() == "no":\n'
            "            return False\n"
            "        return text\n"
            "    if isinstance(default, int):\n"
            "        if not _THOUSANDS.match(text):\n"
            "            return text\n"
            "        return int(text)\n"
            "    if isinstance(default, list):\n"
            "        return text.split()\n"
            "    return text\n",
        ),
        (
            "config_resolve.py",
            None,
            '"""Configuration resolution for the billing-api - the Sorrel source\n'
            "order, the locks and the resolved-trace format (the Sorrel Config\n"
            "Precedence Standard, internal).\n"
            '"""\n'
            "\n"
            "from config_env import coerce, env_key\n"
            "\n"
            "# low -> high: a higher source overrides a lower one\n"
            '_ORDER = ("defaults", "flags", "file", "env",)\n'
            "\n"
            "# keys whose overrides from the blocked sources are suppressed\n"
            '_LOCKED = {"billing.currency"}\n'
            '_BLOCKED = ("env", "flags",)\n'
            "\n"
            "\n"
            "def resolve(spec):\n"
            '    """The resolved trace: one line per config key, keys sorted."""\n'
            "    pools = {\n"
            '        "defaults": spec.get("defaults", {}),\n'
            '        "file": spec.get("file", {}),\n'
            '        "flags": spec.get("flags", {}),\n'
            "    }\n"
            "    env_values = {}\n"
            '    for raw, text in spec.get("env", {}).items():\n'
            "        key = env_key(raw)\n"
            "        if key is not None:\n"
            "            env_values[key] = text\n"
            '    pools["env"] = env_values\n'
            "    lines = []\n"
            "    for key in sorted(set().union(*pools.values())):\n"
            "        source, value = _winner(key, pools)\n"
            "        lines.append(_line(key, source, value, _suppressed(key, pools)))\n"
            "    return lines\n"
            "\n"
            "\n"
            "def _winner(key, pools):\n"
            '    """The winning source and value for one key (locks applied)."""\n'
            "    for source in reversed(_ORDER):\n"
            "        if key in _LOCKED and source in _BLOCKED:\n"
            "            continue\n"
            "        pool = pools[source]\n"
            "        if key not in pool:\n"
            "            continue\n"
            "        value = pool[key]\n"
            '        if source in ("env", "flags") and isinstance(value, str):\n'
            '            value = coerce(value, pools["defaults"].get(key, ""))\n'
            "            if value is None:\n"
            "                continue\n"
            "        return source, value\n"
            '    return "defaults", pools["defaults"].get(key)\n'
            "\n"
            "\n"
            "def _suppressed(key, pools):\n"
            '    """Whether a blocked source carried a value for a locked key."""\n'
            "    if key not in _LOCKED:\n"
            "        return False\n"
            "    return any(key in pools[source] for source in _BLOCKED)\n"
            "\n"
            "\n"
            "def _line(key, source, value, suppressed):\n"
            '    """One trace line, with the lock annotation when suppressed."""\n'
            "    rendered = _render(value)\n"
            '    line = f"RESOLVE {key} = {rendered} ({source})"\n'
            "    if suppressed:\n"
            '        line = line + " [locked]"\n'
            "    return line\n"
            "\n"
            "\n"
            "def _render(value):\n"
            '    """The trace rendering of one resolved value."""\n'
            "    if isinstance(value, bool):\n"
            '        return "yes" if value else "no"\n'
            "    if isinstance(value, list):\n"
            '        return " ".join(str(item) for item in value)\n'
            "    return str(value)\n",
        ),
    ],
    "private3-onyx-config": [
        (
            "config_env.py",
            None,
            '"""Environment mapping for the transcode-foreman configuration - the\n'
            "Onyx env namespace and the value coercions (the Onyx Config\n"
            "Precedence Standard, internal).\n"
            '"""\n'
            "\n"
            "\n"
            "def env_key(raw):\n"
            '    """The config key for an environment variable name (None if not ours)."""\n'
            '    if not raw.startswith("onyx__"):\n'
            "        return None\n"
            '    parts = raw[len("onyx__"):].split("__")\n'
            '    return ".".join(part.lower() for part in parts)\n'
            "\n"
            "\n"
            "def coerce(text, default):\n"
            '    """Parse ``text`` against the type of ``default``.\n'
            "\n"
            "    Bools are 1/0, ints are hex-friendly (a 0x prefix parses as hex),\n"
            "    lists are comma-separated and NOT stripped; a failed parse yields\n"
            "    None (the value is absent).\n"
            '    """\n'
            "    if isinstance(default, bool):\n"
            '        if text == "1":\n'
            "            return True\n"
            '        if text == "0":\n'
            "            return False\n"
            "        return None\n"
            "    if isinstance(default, int):\n"
            '        if text.lower().startswith("0x"):\n'
            "            try:\n"
            "                return int(text, 16)\n"
            "            except ValueError:\n"
            "                return None\n"
            "        try:\n"
            "            return int(text)\n"
            "        except ValueError:\n"
            "            return None\n"
            "    if isinstance(default, list):\n"
            '        return text.split(",")\n'
            "    return text\n",
        ),
        (
            "config_resolve.py",
            None,
            '"""Configuration resolution for the transcode-foreman - the Onyx source\n'
            "order, the locks and the resolved-trace format (the Onyx Config\n"
            "Precedence Standard, internal).\n"
            '"""\n'
            "\n"
            "from config_env import coerce, env_key\n"
            "\n"
            "# low -> high: a higher source overrides a lower one\n"
            '_ORDER = ("defaults", "file", "flags", "env",)\n'
            "\n"
            "# keys whose overrides from the blocked sources are suppressed\n"
            '_LOCKED = {"media.codec"}\n'
            '_BLOCKED = ("env", "flags", "file",)\n'
            "\n"
            "\n"
            "def resolve(spec):\n"
            '    """The resolved trace: one line per config key, keys sorted."""\n'
            "    pools = {\n"
            '        "defaults": spec.get("defaults", {}),\n'
            '        "file": spec.get("file", {}),\n'
            '        "flags": spec.get("flags", {}),\n'
            "    }\n"
            "    env_values = {}\n"
            '    for raw, text in spec.get("env", {}).items():\n'
            "        key = env_key(raw)\n"
            "        if key is not None:\n"
            "            env_values[key] = text\n"
            '    pools["env"] = env_values\n'
            "    lines = []\n"
            "    for key in sorted(set().union(*pools.values())):\n"
            "        source, value = _winner(key, pools)\n"
            "        lines.append(_line(key, source, value, _suppressed(key, pools)))\n"
            "    return lines\n"
            "\n"
            "\n"
            "def _winner(key, pools):\n"
            '    """The winning source and value for one key (locks applied)."""\n'
            "    for source in reversed(_ORDER):\n"
            "        if key in _LOCKED and source in _BLOCKED:\n"
            "            continue\n"
            "        pool = pools[source]\n"
            "        if key not in pool:\n"
            "            continue\n"
            "        value = pool[key]\n"
            '        if source in ("env", "flags") and isinstance(value, str):\n'
            '            value = coerce(value, pools["defaults"].get(key, ""))\n'
            "            if value is None:\n"
            "                continue\n"
            "        return source, value\n"
            '    return "defaults", pools["defaults"].get(key)\n'
            "\n"
            "\n"
            "def _suppressed(key, pools):\n"
            '    """Whether a blocked source carried a value for a locked key."""\n'
            "    if key not in _LOCKED:\n"
            "        return False\n"
            "    return any(key in pools[source] for source in _BLOCKED)\n"
            "\n"
            "\n"
            "def _line(key, source, value, suppressed):\n"
            '    """One trace line, with the lock annotation when suppressed."""\n'
            "    rendered = _render(value)\n"
            '    line = f"{key}@{source} => {rendered}"\n'
            "    if suppressed:\n"
            '        line = line + "!override-blocked"\n'
            "    return line\n"
            "\n"
            "\n"
            "def _render(value):\n"
            '    """The trace rendering of one resolved value."""\n'
            "    if isinstance(value, bool):\n"
            '        return "1" if value else "0"\n'
            "    if isinstance(value, list):\n"
            '        return ",".join(str(item) for item in value)\n'
            "    return str(value)\n",
        ),
    ],
    "private3-wick-config": [
        (
            "config_env.py",
            None,
            '"""Environment mapping for the session-service configuration - the\n'
            "Wick env namespace and the value coercions (the Wick Config Precedence\n"
            "Standard, internal).\n"
            '"""\n'
            "\n"
            "\n"
            "def env_key(raw):\n"
            '    """The config key for an environment variable name (None if not ours)."""\n'
            '    if not raw.startswith("WICK_"):\n'
            "        return None\n"
            '    rest = raw[len("WICK_"):]\n'
            '    if "_" not in rest:\n'
            "        return None\n"
            '    section, _, key = rest.partition("_")\n'
            '    return f"{section.lower()}.{key.lower()}"\n'
            "\n"
            "\n"
            "def coerce(text, default):\n"
            '    """Parse ``text`` against the type of ``default``.\n'
            "\n"
            "    Bools are enabled/disabled, ints are plain int(), lists are\n"
            "    pipe-separated and stripped; a failed parse keeps the raw text as\n"
            "    the value.\n"
            '    """\n'
            "    if isinstance(default, bool):\n"
            '        if text.lower() == "enabled":\n'
            "            return True\n"
            '        if text.lower() == "disabled":\n'
            "            return False\n"
            "        return text\n"
            "    if isinstance(default, int):\n"
            "        try:\n"
            "            return int(text)\n"
            "        except ValueError:\n"
            "            return text\n"
            "    if isinstance(default, list):\n"
            '        return [item.strip() for item in text.split("|")]\n'
            "    return text\n",
        ),
        (
            "config_resolve.py",
            None,
            '"""Configuration resolution for the session-service - the Wick source\n'
            "order, the locks and the resolved-trace format (the Wick Config\n"
            "Precedence Standard, internal).\n"
            '"""\n'
            "\n"
            "from config_env import coerce, env_key\n"
            "\n"
            "# low -> high: a higher source overrides a lower one\n"
            '_ORDER = ("env", "defaults", "file", "flags",)\n'
            "\n"
            "# keys whose overrides from the blocked sources are suppressed\n"
            '_LOCKED = {"tokens.algo"}\n'
            '_BLOCKED = ("env", "flags",)\n'
            "\n"
            "\n"
            "def resolve(spec):\n"
            '    """The resolved trace: one line per config key, keys sorted."""\n'
            "    pools = {\n"
            '        "defaults": spec.get("defaults", {}),\n'
            '        "file": spec.get("file", {}),\n'
            '        "flags": spec.get("flags", {}),\n'
            "    }\n"
            "    env_values = {}\n"
            '    for raw, text in spec.get("env", {}).items():\n'
            "        key = env_key(raw)\n"
            "        if key is not None:\n"
            "            env_values[key] = text\n"
            '    pools["env"] = env_values\n'
            "    lines = []\n"
            "    for key in sorted(set().union(*pools.values())):\n"
            "        source, value = _winner(key, pools)\n"
            "        lines.append(_line(key, source, value, _suppressed(key, pools)))\n"
            "    return lines\n"
            "\n"
            "\n"
            "def _winner(key, pools):\n"
            '    """The winning source and value for one key (locks applied)."""\n'
            "    for source in reversed(_ORDER):\n"
            "        if key in _LOCKED and source in _BLOCKED:\n"
            "            continue\n"
            "        pool = pools[source]\n"
            "        if key not in pool:\n"
            "            continue\n"
            "        value = pool[key]\n"
            '        if source in ("env", "flags") and isinstance(value, str):\n'
            '            value = coerce(value, pools["defaults"].get(key, ""))\n'
            "            if value is None:\n"
            "                continue\n"
            "        return source, value\n"
            '    return "defaults", pools["defaults"].get(key)\n'
            "\n"
            "\n"
            "def _suppressed(key, pools):\n"
            '    """Whether a blocked source carried a value for a locked key."""\n'
            "    if key not in _LOCKED:\n"
            "        return False\n"
            "    return any(key in pools[source] for source in _BLOCKED)\n"
            "\n"
            "\n"
            "def _line(key, source, value, suppressed):\n"
            '    """One trace line, with the lock annotation when suppressed."""\n'
            "    rendered = _render(value)\n"
            '    line = f"[{source}] {key}: {rendered}"\n'
            "    if suppressed:\n"
            '        line = line + " (pinned)"\n'
            "    return line\n"
            "\n"
            "\n"
            "def _render(value):\n"
            '    """The trace rendering of one resolved value."""\n'
            "    if isinstance(value, bool):\n"
            '        return "enabled" if value else "disabled"\n'
            "    if isinstance(value, list):\n"
            '        return "|".join(str(item) for item in value)\n'
            "    return str(value)\n",
        ),
    ],
}
