"""Private-knowledge fixtures for the E2C skill axis (builder "a", 2026-10-02).

E2B (ADR-014 amendment 24) measured the private-knowledge skill axis on
two fixtures (atlas/meridian, scripts/private_tasks.py): the standard as
a plain repo file (arm F) passed 9/20, the same standard routed by ACI's
registry + §14 router and preloaded (arm Bp) passed 19/20. E2C
replicates that measurement on 7 NEW, varied fixtures (--set private2,
assembled by the integrating job from the per-builder modules): this
module is builder "a"'s two fixtures. Each standard below is INVENTED
with the fixture (it did not exist before today, so no model can carry
it), documented ONLY in the fixture's private SKILL.md, and pinned in
the workspace tests as sha256 DIGESTS over canonical traces - never
plaintext - so a naked model can derive nothing from the test names,
messages or the shipped code (the shipped code embodies a plausible but
WRONG convention).

Fixture -> private skill (the single source of the rules):

- private2-cairn-money -> cairn-money-standard (directness: named)
      the finance platform's currency classes (per-class decimals and
      rounding modes), amount rendering, and the fiscal calendar
      (period ids from timestamps).
- private2-drawbridge-rollout -> drawbridge-rollout-standard
      (directness: indirect) the release-engineering rollout recipe
      (the salted deterministic bucket mix) and the kill-switch /
      allow-list / percentage resolution order.

Digest design (the E2B §1.5 lesson: the meridian digests sat over a
small enumerable output space and models attempted to brute-force
them): every digest here is over a 280-560 char canonical trace that
combines 10-14 independent standard decisions per digest (rounding
mode x decimals x rendering, or verdict x bucket id per line), and the
bucket ids are unreachable without the standard's mixing token -
enumeration is not a viable strategy.

The prompt's DIRECTNESS is assigned per fixture: "named" names the
standard (a ticket would); "indirect" only says an internal policy
exists - the standard's name and title words appear NOWHERE in it.

§34 caveat applies to any round run on this set: small n,
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
    markers: list[str],
) -> dict[str, Any]:
    """A private-knowledge fixture: workspace files + task prompt + the
    private SKILL.md text (NOT a workspace file - the model must never
    see it on disk; the measurement arms serve it through the capability
    plane or write it into the workspace as a repo doc). ``directness``
    is the prompt's relationship to the standard ("named" names it;
    "indirect" only says an internal policy exists). ``markers`` are the
    standard's distinctive vocabulary - they must exist ONLY in the
    skill, never in the files or the prompt."""
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
    _private_task(
        "private2-cairn-money",
        {
            "cairn_money.py": (
                '"""Money helpers for the pricing service - render and round amounts.\n'
                "\n"
                "Every amount is rendered with two decimal places and thousands\n"
                "separators; ties round to the nearest even digit (banker's rounding),\n"
                "the usual convention for financial software.\n"
                '"""\n'
                "\n"
                "from decimal import Decimal\n"
                "\n"
                "\n"
                "def round_amount(currency, value):\n"
                '    """Round an amount to the customary two decimal places."""\n'
                "    return round(value, 2)\n"
                "\n"
                "\n"
                "def render_amount(currency, value):\n"
                '    """Render an amount with two decimals and thousands separators."""\n'
                '    return f"{round(value, 2):,.2f}"\n'
            ),
            "cairn_periods.py": (
                '"""Fiscal period ids - maps a timestamp to a period of its calendar year."""\n'
                "\n"
                "\n"
                "def fiscal_period_id(ts):\n"
                '    """The fiscal period id: the year and the calendar month."""\n'
                '    return f"FY{ts.year:04d}-P{ts.month:02d}"\n'
            ),
            "pricing_report.py": (
                '"""Pricing report lines - one statement line per charge.\n'
                "\n"
                "Composes the money helpers: the rendered amount and the fiscal period of\n"
                "the charge's timestamp.\n"
                '"""\n'
                "\n"
                "from datetime import datetime\n"
                "from decimal import Decimal\n"
                "\n"
                "from cairn_money import render_amount\n"
                "from cairn_periods import fiscal_period_id\n"
                "\n"
                "\n"
                "def statement_line(stamp, currency, raw):\n"
                '    """One report line: the rendered amount and the fiscal period."""\n'
                "    amount = render_amount(currency, Decimal(raw))\n"
                "    period = fiscal_period_id(datetime.fromisoformat(stamp))\n"
                '    return f"{amount} @{period}"\n'
            ),
            "test_cairn_convention.py": (
                '"""Contract tests for the pricing service against the INTERNAL money and\n'
                "fiscal-period standard. The standard is not public: each scenario's\n"
                "expected behavior is pinned as a sha256 DIGEST over the observed trace,\n"
                'so this file cannot become a copy of the standard."""\n'
                "\n"
                "import hashlib\n"
                "from datetime import datetime\n"
                "from decimal import Decimal\n"
                "\n"
                "import pytest\n"
                "\n"
                "from cairn_money import render_amount, round_amount\n"
                "from cairn_periods import fiscal_period_id\n"
                "from pricing_report import statement_line\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "def _paired(scenarios, digests):\n"
                '    """Scenario/digest pairs - the counts must match exactly."""\n'
                "    assert len(scenarios) == len(digests)\n"
                "    return list(zip(scenarios, digests))\n"
                "\n"
                "\n"
                "def money_line(currency, raw):\n"
                "    try:\n"
                "        rendered = render_amount(currency, Decimal(raw))\n"
                "        rounded = round_amount(currency, Decimal(raw))\n"
                "    except Exception as exc:\n"
                '        return f"{currency} {raw} !{type(exc).__name__}"\n'
                '    return f"{currency} {raw} -> {rendered} [{rounded}]"\n'
                "\n"
                "\n"
                "def period_line(stamp):\n"
                "    try:\n"
                "        period = fiscal_period_id(datetime.fromisoformat(stamp))\n"
                "    except Exception as exc:\n"
                '        return f"{stamp} !{type(exc).__name__}"\n'
                '    return f"{stamp} -> {period}"\n'
                "\n"
                "\n"
                "def ledger_line(stamp, currency, raw):\n"
                "    try:\n"
                "        line = statement_line(stamp, currency, raw)\n"
                "    except Exception as exc:\n"
                '        return f"{stamp}|{currency}|{raw} !{type(exc).__name__}"\n'
                '    return f"{stamp}|{currency}|{raw}|{line}"\n'
                "\n"
                "\n"
                "MONEY_SCENARIOS = [\n"
                "    [\n"
                '        ("USD", "1.005"),\n'
                '        ("USD", "1.004"),\n'
                '        ("USD", "0.125"),\n'
                '        ("EUR", "2.675"),\n'
                '        ("EUR", "-1.005"),\n'
                '        ("GBP", "10.245"),\n'
                '        ("CHF", "0.005"),\n'
                '        ("CHF", "-0.004"),\n'
                '        ("CAD", "1234.5"),\n'
                '        ("AUD", "999999.999"),\n'
                '        ("USD", "0.1"),\n'
                '        ("USD", "-2.5"),\n'
                "    ],\n"
                "    [\n"
                '        ("JPY", "1234.5"),\n'
                '        ("JPY", "1234.4"),\n'
                '        ("JPY", "2.5"),\n'
                '        ("JPY", "-2.5"),\n'
                '        ("KRW", "98765.5"),\n'
                '        ("VND", "25000"),\n'
                '        ("CLP", "0.5"),\n'
                '        ("TWD", "33.335"),\n'
                '        ("ZZZ", "12.34"),\n'
                '        ("usd", "1.005"),\n'
                '        ("JPY", "0.4"),\n'
                '        ("KRW", "-0.5"),\n'
                "    ],\n"
                "    [\n"
                '        ("XAU", "1.23455"),\n'
                '        ("XAU", "1.23456"),\n'
                '        ("XAU", "-1.23455"),\n'
                '        ("XAG", "0.00005"),\n'
                '        ("XAG", "19.98765"),\n'
                '        ("XAG", "-0.00004"),\n'
                '        ("XPT", "3.14159265"),\n'
                '        ("XPT", "1234.56789"),\n'
                '        ("XAU", "2"),\n'
                '        ("XAU", "0.000049"),\n'
                "    ],\n"
                "    [\n"
                '        ("BTC", "0.123456789"),\n'
                '        ("BTC", "0.123456785"),\n'
                '        ("BTC", "-0.123456789"),\n'
                '        ("ETH", "1.999999999"),\n'
                '        ("ETH", "2.000000004"),\n'
                '        ("SOL", "0.000000009"),\n'
                '        ("SOL", "-0.000000001"),\n'
                '        ("BTC", "3"),\n'
                '        ("ETH", "123.456789123"),\n'
                '        ("BTC", "0.500000000"),\n'
                "    ],\n"
                "]\n"
                "\n"
                "PERIOD_SCENARIOS = [\n"
                "    [\n"
                '        "2026-03-01T00:00:00+00:00",\n'
                '        "2026-02-28T23:59:59+00:00",\n'
                '        "2026-06-15T12:00:00+00:00",\n'
                '        "2025-12-31T23:59:59+00:00",\n'
                '        "2026-01-01T00:00:00+00:00",\n'
                '        "2026-02-01T00:00:00+00:00",\n'
                '        "2025-03-01T00:00:01+00:00",\n'
                '        "2024-11-30T06:30:00+00:00",\n'
                '        "2026-07-04T18:45:00+00:00",\n'
                '        "2026-09-30T23:00:00+00:00",\n'
                '        "2026-10-31T00:00:00+00:00",\n'
                '        "2026-12-25T00:00:00+00:00",\n'
                "    ],\n"
                "    [\n"
                '        "2024-02-29T00:00:00",\n'
                '        "2024-02-29T23:59:59",\n'
                '        "2024-03-01T00:00:00",\n'
                '        "2000-02-29T12:00:00",\n'
                '        "2000-03-01T00:00:00",\n'
                '        "1999-12-31T23:59:59",\n'
                '        "2023-01-15T00:00:00",\n'
                '        "2023-03-01T00:00:00",\n'
                '        "2026-05-31T13:59:59",\n'
                '        "2026-04-01T00:00:00",\n'
                '        "2026-08-16T09:30:00",\n'
                '        "2026-11-11T11:11:11",\n'
                "    ],\n"
                "    [\n"
                '        "2026-03-01T08:30:00+09:00",\n'
                '        "2026-02-28T19:00:00-05:00",\n'
                '        "2026-01-01T05:00:00+05:00",\n'
                '        "2025-12-31T20:30:00-03:30",\n'
                '        "2026-03-01T00:00:00+00:00",\n'
                '        "2026-02-28T23:59:59.999999+00:00",\n'
                '        "2027-03-01T00:00:00+00:00",\n'
                '        "2026-12-31T18:00:00-06:00",\n'
                '        "2026-06-15T22:00:00-02:00",\n'
                '        "2026-09-01T01:00:00+14:00",\n'
                '        "2026-08-31T10:00:00-11:30",\n'
                '        "2026-04-30T16:00:00+02:00",\n'
                "    ],\n"
                "]\n"
                "\n"
                "LEDGER_SCENARIOS = [\n"
                "    [\n"
                '        ("2026-03-01T00:00:00+00:00", "USD", "1.005"),\n'
                '        ("2026-02-28T23:59:59+00:00", "JPY", "1234.5"),\n'
                '        ("2026-06-15T12:00:00+00:00", "XAU", "1.23455"),\n'
                '        ("2026-01-01T00:00:00+00:00", "BTC", "0.123456789"),\n'
                '        ("2026-03-01T08:30:00+09:00", "EUR", "-1.005"),\n'
                '        ("2025-12-31T23:59:59+00:00", "CHF", "-0.004"),\n'
                '        ("2026-04-01T00:00:00", "ZZZ", "5.00"),\n'
                '        ("2024-02-29T12:00:00", "KRW", "98765.5"),\n'
                '        ("2026-12-25T00:00:00+00:00", "GBP", "10.245"),\n'
                '        ("2026-07-04T18:45:00+00:00", "SOL", "0.000000009"),\n'
                "    ],\n"
                "    [\n"
                '        ("2026-05-31T13:59:59", "USD", "999999.999"),\n'
                '        ("2026-11-11T11:11:11", "XAG", "19.98765"),\n'
                '        ("2026-08-16T09:30:00", "ETH", "2.000000004"),\n'
                '        ("2026-02-01T00:00:00+00:00", "VND", "25000"),\n'
                '        ("2026-10-31T00:00:00+00:00", "XPT", "3.14159265"),\n'
                '        ("2026-01-15T00:00:00", "JPY", "-2.5"),\n'
                '        ("2026-09-30T23:00:00+00:00", "CAD", "1234.5"),\n'
                '        ("2026-12-31T18:00:00-06:00", "USD", "0.125"),\n'
                '        ("2027-03-01T00:00:00+00:00", "BTC", "3"),\n'
                '        ("2026-06-15T22:00:00-02:00", "usd", "1.005"),\n'
                "    ],\n"
                "]\n"
                "\n"
                "\n"
                "def money_text(pairs):\n"
                '    return "\\n".join(money_line(currency, raw) for currency, raw in pairs)\n'
                "\n"
                "\n"
                "def period_text(stamps):\n"
                '    return "\\n".join(period_line(stamp) for stamp in stamps)\n'
                "\n"
                "\n"
                "def ledger_text(entries):\n"
                '    return "\\n".join(\n'
                "        ledger_line(stamp, currency, raw) for stamp, currency, raw in entries\n"
                "    )\n"
                "\n"
                "\n"
                "# sha256 of each scenario's canonical text, from the standard.\n"
                "MONEY_DIGESTS = [\n"
                '    "df0c4afffab0936119f30b02dcc1b3365324382d158c98be47c574d5a34ea277",\n'
                '    "776395198592ddcb0c6c10deb5634ca5cb9c099958c365153049a0e3e29cc722",\n'
                '    "5ae17fb25ccd95093da9bdd49637ed7dd47093902d1f882e03301fc24d68e6da",\n'
                '    "152523a82944767bd903e5b42fdf62f7bfd63a10d23067a00dfc71c071980640",\n'
                "]\n"
                "PERIOD_DIGESTS = [\n"
                '    "cda820972f059e2d21b0a0b055231f2999fb6ab0748cd2c707b2d8b31cf9a2a5",\n'
                '    "6b8a4339c6e03ab3e9dc7caa53cbd03e736cd6bab59384ce37516a5907959cac",\n'
                '    "5a66fcf8b432ab6fdf6cfd25eefea2cfcfee5a5cb2e48866b7fe261104ac66a1",\n'
                "]\n"
                "LEDGER_DIGESTS = [\n"
                '    "83ef7c121796f3d6e3732c7c88c417172376dcaeb22419dad939d31b46a30d03",\n'
                '    "c0e29bd7666d0883613bc84358b7f428b0be0dd40becbda95cd1ece2747d5dd2",\n'
                "]\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("pairs", "digest"),\n'
                "    _paired(MONEY_SCENARIOS, MONEY_DIGESTS),\n"
                '    ids=[f"m{i:02d}" for i in range(1, len(MONEY_SCENARIOS) + 1)],\n'
                ")\n"
                "def test_money(pairs, digest):\n"
                "    text = money_text(pairs)\n"
                "    assert _digest(text) == digest, text\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("stamps", "digest"),\n'
                "    _paired(PERIOD_SCENARIOS, PERIOD_DIGESTS),\n"
                '    ids=[f"p{i:02d}" for i in range(1, len(PERIOD_SCENARIOS) + 1)],\n'
                ")\n"
                "def test_period(stamps, digest):\n"
                "    text = period_text(stamps)\n"
                "    assert _digest(text) == digest, text\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("entries", "digest"),\n'
                "    _paired(LEDGER_SCENARIOS, LEDGER_DIGESTS),\n"
                '    ids=[f"l{i:02d}" for i in range(1, len(LEDGER_SCENARIOS) + 1)],\n'
                ")\n"
                "def test_ledger(entries, digest):\n"
                "    text = ledger_text(entries)\n"
                "    assert _digest(text) == digest, text\n"
            ),
        },
        "The tests in test_cairn_convention.py fail. This pricing service does not "
        "follow the Cairn money standard - how amounts are rounded per currency and "
        "how a timestamp maps to a fiscal period id is decided by that standard, "
        "not by guesswork. Find the root cause before proposing any fix, then fix "
        "it and make the whole test suite green.",
        "cairn-money-standard",
        """---
name: cairn-money-standard
description: Cairn money standard - currency classes, rounding, rendering, fiscal period ids.
version: 1.0.0
---

# Cairn Money Standard (internal)

How the finance platform rounds and renders every money amount, and how
a timestamp maps to a fiscal period id. Internal to the finance
platform team - not published anywhere.

## Currency classes (the Cairn currency table)

| Class | Codes | Decimals | Rounding |
|-------|-------|----------|----------|
| MAJOR | USD EUR GBP CHF CAD AUD | 2 | HALF_UP |
| UNIT | JPY KRW VND CLP TWD | 0 | HALF_UP |
| METAL | XAU XAG XPT | 4 | HALF_DOWN |
| CHAIN | BTC ETH SOL | 8 | DOWN (truncate) |

A code not in the table raises `CairnUnknownCurrency` (fail closed).
Codes are matched exactly as given - no case folding ("usd" is unknown).

## Amounts

- `round_amount(currency, value)` quantizes the Decimal to the class
  decimals with the class rounding mode.
- `render_amount(currency, value)` renders the ROUNDED amount with
  exactly the class decimals: plain ASCII digits, a `.` before the
  fractional digits (only when decimals > 0), `-` for negatives, no
  thousands separators. A negative that rounds to zero renders WITHOUT
  the sign (never `-0.00`).

## Fiscal periods

- The Cairn fiscal year starts March 1. Twelve calendar-month periods:
  P01 = March ... P12 = February.
- The fiscal year label is the calendar year in which the fiscal year
  STARTS (the March's year).
- `fiscal_period_id(ts)` returns `FY{yyyy}-P{pp}` with a zero-padded
  2-digit period, e.g. 2026-02-28 -> FY2025-P12, 2026-03-01 -> FY2026-P01.
- All timestamps are UTC: naive datetimes ARE UTC; aware ones are
  converted to UTC first. The period start instant is inclusive.

## Worked examples

    render_amount("USD", Decimal("1.005")) -> "1.01" (MAJOR, HALF_UP)
    render_amount("USD", Decimal("1.004")) -> "1.00"
    render_amount("JPY", Decimal("1234.5")) -> "1235" (UNIT, 0 decimals)
    render_amount("XAU", Decimal("1.23455")) -> "1.2345" (METAL, HALF_DOWN)
    render_amount("BTC", Decimal("0.123456789")) -> "0.12345678" (CHAIN, DOWN)
    render_amount("USD", Decimal("-0.001")) -> "0.00" (never -0.00)
    fiscal_period_id(datetime(2026, 2, 28, tzinfo=UTC)) -> "FY2025-P12"
    fiscal_period_id(datetime(2026, 3, 1, tzinfo=UTC)) -> "FY2026-P01"
        """,
        "named",
        ["CairnUnknownCurrency", "HALF_UP", "HALF_DOWN", "METAL", "CHAIN", "FY2025-P12"],
    ),
    _private_task(
        "private2-drawbridge-rollout",
        {
            "rollout_bucket.py": (
                '"""Rollout bucketing - a stable bucket per (flag, user).\n'
                "\n"
                "The bucket is the first 8 hex digits of the user:flag digest, mod 10000\n"
                '- good enough spread for percentage rollouts."""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "\n"
                "def bucket_of(flag_key, user_id):\n"
                '    """The user\'s rollout bucket id for the flag (B + 4 digits)."""\n'
                "    user = user_id.strip().lower()\n"
                '    digest = hashlib.sha256(f"{user}:{flag_key}".encode("utf-8")).hexdigest()\n'
                '    return f"B{int(digest[:8], 16) % 10_000:04d}"\n'
            ),
            "rollout_assign.py": (
                '"""Feature-flag assignment - is this user in the rollout?\n'
                "\n"
                "Internal testers (the allow-list) always keep access; everyone else is\n"
                'rolled out by percentage."""\n'
                "\n"
                "from rollout_bucket import bucket_of\n"
                "\n"
                "\n"
                "def assign(config, user_id):\n"
                '    """The assignment verdict for one user on one flag."""\n'
                "    user = user_id.strip().lower()\n"
                '    if user in [u.strip().lower() for u in config.get("allow", [])]:\n'
                '        return "in-allow"\n'
                '    if config.get("kill"):\n'
                '        return "out-kill"\n'
                '    bucket = int(bucket_of(config["key"], user)[1:])\n'
                '    if bucket <= config.get("percent", 0) * 100:\n'
                '        return "in"\n'
                '    return "out"\n'
            ),
            "rollout_report.py": (
                '"""Release dashboard lines - one line per user per flag.\n'
                "\n"
                "Composes the assignment verdict and the rollout bucket.\n"
                '"""\n'
                "\n"
                "from rollout_assign import assign\n"
                "from rollout_bucket import bucket_of\n"
                "\n"
                "\n"
                "def rollout_line(config, user_id):\n"
                '    """``flag#user=verdict@bucket`` - the dashboard line for one user."""\n'
                "    return (\n"
                "        f\"{config['key']}#{user_id}={assign(config, user_id)}\"\n"
                "        f\"@{bucket_of(config['key'], user_id)}\"\n"
                "    )\n"
            ),
            "test_rollout_convention.py": (
                '"""Contract tests for the rollout assignment against the INTERNAL\n'
                "release-engineering rollout standard. The standard is not public: each\n"
                "scenario's expected behavior is pinned as a sha256 DIGEST over the\n"
                'observed trace, so this file cannot become a copy of the standard."""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "import pytest\n"
                "\n"
                "from rollout_bucket import bucket_of\n"
                "from rollout_report import rollout_line\n"
                "\n"
                "\n"
                "def _digest(text):\n"
                '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
                "\n"
                "\n"
                "def _paired(scenarios, digests):\n"
                '    """Scenario/digest pairs - the counts must match exactly."""\n'
                "    assert len(scenarios) == len(digests)\n"
                "    return list(zip(scenarios, digests))\n"
                "\n"
                "\n"
                "def bucket_text(pairs):\n"
                "    lines = []\n"
                "    for flag, user in pairs:\n"
                "        try:\n"
                "            bucket = bucket_of(flag, user)\n"
                "        except Exception as exc:\n"
                '            lines.append(f"{flag}|{user} !{type(exc).__name__}")\n'
                "            continue\n"
                '        lines.append(f"{flag}|{user}|{bucket}")\n'
                '    return "\\n".join(lines)\n'
                "\n"
                "\n"
                "def rollout_text(config, users):\n"
                "    lines = []\n"
                "    for user in users:\n"
                "        try:\n"
                "            line = rollout_line(config, user)\n"
                "        except Exception as exc:\n"
                "            lines.append(f\"{config['key']}#{user} !{type(exc).__name__}\")\n"
                "            continue\n"
                "        lines.append(line)\n"
                '    return "\\n".join(lines)\n'
                "\n"
                "\n"
                "BUCKET_SCENARIOS = [\n"
                "    [\n"
                '        ("checkout-turbo", "u-1"),\n'
                '        ("checkout-turbo", "u-2"),\n'
                '        ("checkout-turbo", "u-3"),\n'
                '        ("checkout-turbo", "u-4"),\n'
                '        ("checkout-turbo", "u-5"),\n'
                '        ("checkout-turbo", "u-6"),\n'
                '        ("checkout-turbo", "u-7"),\n'
                '        ("checkout-turbo", "u-8"),\n'
                '        ("checkout-turbo", "u-9"),\n'
                '        ("checkout-turbo", "u-10"),\n'
                '        ("checkout-turbo", "u-11"),\n'
                '        ("checkout-turbo", "u-12"),\n'
                '        ("checkout-turbo", "u-13"),\n'
                '        ("checkout-turbo", "u-14"),\n'
                "    ],\n"
                "    [\n"
                '        ("search-beta", "u-1"),\n'
                '        ("search-beta", "u-2"),\n'
                '        ("search-beta", "u-3"),\n'
                '        ("search-beta", "u-4"),\n'
                '        ("search-beta", "Ada-L"),\n'
                '        ("search-beta", "ada-l"),\n'
                '        ("search-beta", "ADA-L"),\n'
                '        ("search-beta", "u-9"),\n'
                '        ("search-beta", "u-10"),\n'
                '        ("search-beta", "u-11"),\n'
                '        ("search-beta", "u-12"),\n'
                '        ("search-beta", "u-13"),\n'
                '        ("search-beta", "u-14"),\n'
                '        ("search-beta", "u-15"),\n'
                "    ],\n"
                "    [\n"
                '        ("nav-refresh", "u-100"),\n'
                '        ("nav-refresh", "u-101"),\n'
                '        ("nav-refresh", "u-102"),\n'
                '        ("nav-refresh", "u-103"),\n'
                '        ("nav-refresh", "u-104"),\n'
                '        ("nav-refresh", "u-105"),\n'
                '        ("nav-refresh", "u-106"),\n'
                '        ("nav-refresh", "u-107"),\n'
                '        ("nav-refresh", "u-108"),\n'
                '        ("nav-refresh", "u-109"),\n'
                '        ("nav-refresh", "u-110"),\n'
                '        ("nav-refresh", "u-111"),\n'
                '        ("nav-refresh", "u-112"),\n'
                '        ("nav-refresh", "u-113"),\n'
                "    ],\n"
                "    [\n"
                '        ("checkout-turbo", "Ada-L"),\n'
                '        ("checkout-turbo", "ada-l"),\n'
                '        ("checkout-turbo", "ADA-L"),\n'
                '        ("checkout-turbo", "u-7"),\n'
                '        ("checkout-turbo", "u-8"),\n'
                '        ("checkout-turbo", "u-9"),\n'
                '        ("checkout-turbo", "u-10"),\n'
                '        ("checkout-turbo", "u-11"),\n'
                '        ("checkout-turbo", "u-12"),\n'
                '        ("checkout-turbo", "u-13"),\n'
                '        ("checkout-turbo", "u-14"),\n'
                '        ("checkout-turbo", "u-15"),\n'
                "    ],\n"
                "]\n"
                "\n"
                "ROLLOUT_SCENARIOS = [\n"
                "    (\n"
                "        {\n"
                '            "key": "checkout-turbo",\n'
                '            "kill": False,\n'
                '            "allow": [],\n'
                '            "percent": 50,\n'
                "        },\n"
                "        [\n"
                '            "u-1",\n'
                '            "u-2",\n'
                '            "u-3",\n'
                '            "u-4",\n'
                '            "u-5",\n'
                '            "u-6",\n'
                '            "u-7",\n'
                '            "u-8",\n'
                '            "u-9",\n'
                '            "u-10",\n'
                '            "u-11",\n'
                '            "u-12",\n'
                "        ],\n"
                "    ),\n"
                "    (\n"
                "        {\n"
                '            "key": "checkout-turbo",\n'
                '            "kill": False,\n'
                '            "allow": ["u-3", "u-9"],\n'
                '            "percent": 10,\n'
                "        },\n"
                "        [\n"
                '            "u-1",\n'
                '            "u-2",\n'
                '            "u-3",\n'
                '            "u-4",\n'
                '            "u-5",\n'
                '            "u-6",\n'
                '            "u-7",\n'
                '            "u-8",\n'
                '            "u-9",\n'
                '            "u-10",\n'
                '            "u-11",\n'
                '            "u-12",\n'
                "        ],\n"
                "    ),\n"
                "    (\n"
                "        {\n"
                '            "key": "checkout-turbo",\n'
                '            "kill": True,\n'
                '            "allow": ["u-3", "u-9"],\n'
                '            "percent": 100,\n'
                "        },\n"
                "        [\n"
                '            "u-1",\n'
                '            "u-2",\n'
                '            "u-3",\n'
                '            "u-4",\n'
                '            "u-5",\n'
                '            "u-6",\n'
                '            "u-7",\n'
                '            "u-8",\n'
                '            "u-9",\n'
                '            "u-10",\n'
                '            "u-11",\n'
                '            "u-12",\n'
                "        ],\n"
                "    ),\n"
                "    (\n"
                "        {\n"
                '            "key": "search-beta",\n'
                '            "kill": False,\n'
                '            "allow": ["Ada-L"],\n'
                '            "percent": 0,\n'
                "        },\n"
                "        [\n"
                '            "u-1",\n'
                '            "u-2",\n'
                '            "u-3",\n'
                '            "u-4",\n'
                '            "u-5",\n'
                '            "u-6",\n'
                '            "u-7",\n'
                '            "u-8",\n'
                '            "u-9",\n'
                '            "u-10",\n'
                '            "Ada-L",\n'
                '            "ada-l",\n'
                "        ],\n"
                "    ),\n"
                "    (\n"
                "        {\n"
                '            "key": "nav-refresh",\n'
                '            "kill": False,\n'
                '            "allow": [],\n'
                '            "percent": 100,\n'
                "        },\n"
                "        [\n"
                '            "u-100",\n'
                '            "u-101",\n'
                '            "u-102",\n'
                '            "u-103",\n'
                '            "u-104",\n'
                '            "u-105",\n'
                '            "u-106",\n'
                '            "u-107",\n'
                '            "u-108",\n'
                '            "u-109",\n'
                '            "u-110",\n'
                '            "u-111",\n'
                "        ],\n"
                "    ),\n"
                "    (\n"
                "        {\n"
                '            "key": "nav-refresh",\n'
                '            "kill": False,\n'
                '            "allow": [],\n'
                '            "percent": 0,\n'
                "        },\n"
                "        [\n"
                '            "u-100",\n'
                '            "u-101",\n'
                '            "u-102",\n'
                '            "u-103",\n'
                '            "u-104",\n'
                '            "u-105",\n'
                '            "u-106",\n'
                '            "u-107",\n'
                '            "u-108",\n'
                '            "u-109",\n'
                '            "u-110",\n'
                '            "u-111",\n'
                "        ],\n"
                "    ),\n"
                "]\n"
                "\n"
                "# sha256 of each scenario's canonical text, from the standard.\n"
                "BUCKET_DIGESTS = [\n"
                '    "7bd51e87527c1eebd0ae5a5a459fed7e431b147c78902e7179a37d255bf616f7",\n'
                '    "9ded1563b0723bf003fadd2dc53104c1d6b0a99b90947455f3035c1d25ff80c9",\n'
                '    "061b2861016d91c3a24b5fbcbfd02f487262516865cb1b258da9ce232c69ab05",\n'
                '    "f607539de35494a2bd9dba4fc8b222ce1bbdd07af4e66a73beb3c29dec5c2245",\n'
                "]\n"
                "ROLLOUT_DIGESTS = [\n"
                '    "da4cd853fc67ad9f91925c4f9c3a5e737b3018d89d66d252dc639388e2e1b106",\n'
                '    "85621be07050aebcb055656a56e0e3e621c0887db194b52a544f75e586edd89e",\n'
                '    "1376766259d45a0e4ff70bfba3d181edbfe14a25f600f480ef23f12740657599",\n'
                '    "f58790e216148b6e183a55e0114722cef9c3169fba08fa28c17d2e929fafac0d",\n'
                '    "cbd5e2ef9dc31bc25857254856e0d79bcf36445f57645a67fb0d7be1daef4d18",\n'
                '    "7600592d075ad203d6d538aa75f018b06d63a9710023e68fb9062478de17e0aa",\n'
                "]\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("pairs", "digest"),\n'
                "    _paired(BUCKET_SCENARIOS, BUCKET_DIGESTS),\n"
                '    ids=[f"b{i:02d}" for i in range(1, len(BUCKET_SCENARIOS) + 1)],\n'
                ")\n"
                "def test_bucket(pairs, digest):\n"
                "    text = bucket_text(pairs)\n"
                "    assert _digest(text) == digest, text\n"
                "\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    ("scenario", "digest"),\n'
                "    _paired(ROLLOUT_SCENARIOS, ROLLOUT_DIGESTS),\n"
                '    ids=[f"r{i:02d}" for i in range(1, len(ROLLOUT_SCENARIOS) + 1)],\n'
                ")\n"
                "def test_rollout(scenario, digest):\n"
                "    config, users = scenario\n"
                "    text = rollout_text(config, users)\n"
                "    assert _digest(text) == digest, text\n"
            ),
        },
        "The tests in test_rollout_convention.py fail. This feature-flag rollout "
        "assignment doesn't follow how we decide rollout assignment internally - "
        "which users get a flag is decided by our internal rollout policy, not by "
        "guesswork. Find the root cause before proposing any fix, then fix it and "
        "make the whole test suite green.",
        "drawbridge-rollout-standard",
        """---
name: drawbridge-rollout-standard
description: Drawbridge rollout standard - feature-flag user bucketing and assignment precedence.
version: 1.0.0
---

# Drawbridge Rollout Standard (internal)

How the release-engineering team buckets every user for a feature-flag
rollout and resolves flag assignment. Internal to the release
engineering team - not published anywhere.

## Bucketing (the Drawbridge mix)

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

1. `kill` truthy -> `HELD-KILL` for EVERYONE - the allow-list included
   (the drawbridge is up; nothing crosses).
2. else the user id in `allow` (exact string match) -> `ENROLLED-ALLOW`
   (the allow-list bypasses the percentage).
3. else the bucket: bucket number < `percent` * 1000 -> `ENROLLED`,
   otherwise `HELD-BUCKET`.

`percent` is per-mille over the 100_000 buckets: percent=10 enrolls
buckets 0..9999; percent=100 enrolls everyone (max bucket 99999).

## Worked example

flag "checkout-turbo", user "u-42": the mix gives bucket `B37334`.

- kill=False, allow=[], percent=50: 37334 < 50000 -> `ENROLLED`.
- kill=False, allow=[], percent=10: 37334 >= 10000 -> `HELD-BUCKET`.
- kill=True, allow=["u-42"], percent=100: -> `HELD-KILL` (kill beats
  the allow-list).
- kill=False, allow=["u-42"], percent=0: -> `ENROLLED-ALLOW`.
        """,
        "indirect",
        [
            "Drawbridge",
            "db-mix-01",
            "ENROLLED",
            "ENROLLED-ALLOW",
            "HELD-KILL",
            "HELD-BUCKET",
            "B37334",
        ],
    ),
]

# The known root-cause fix per fixture - whole-file replacements (the fix
# IS the module conforming to the fixture's private standard), the same
# format scripts/verify_private_fixtures.py applies: (file, None, content)
# replaces the file. The composer modules (pricing_report.py /
# rollout_report.py) only delegate to the rule-carrying modules and need
# no fix; the tests are never touched.
FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    "private2-cairn-money": [
        (
            "cairn_money.py",
            None,
            '"""Money helpers - implements the Cairn Money Standard (internal).\n'
            "\n"
            "Currency classes (the Cairn currency table), per-class rounding and\n"
            "rendering, and the fiscal period calendar. Fail closed on unknown\n"
            "currency codes.\n"
            '"""\n'
            "\n"
            "from decimal import ROUND_DOWN, ROUND_HALF_DOWN, ROUND_HALF_UP, Decimal\n"
            "\n"
            "\n"
            "class CairnUnknownCurrency(Exception):\n"
            '    """A currency code outside the Cairn currency table."""\n'
            "\n"
            "\n"
            "# code -> (decimals, rounding mode)\n"
            "_TABLE = {\n"
            '    "USD": (2, ROUND_HALF_UP),\n'
            '    "EUR": (2, ROUND_HALF_UP),\n'
            '    "GBP": (2, ROUND_HALF_UP),\n'
            '    "CHF": (2, ROUND_HALF_UP),\n'
            '    "CAD": (2, ROUND_HALF_UP),\n'
            '    "AUD": (2, ROUND_HALF_UP),\n'
            '    "JPY": (0, ROUND_HALF_UP),\n'
            '    "KRW": (0, ROUND_HALF_UP),\n'
            '    "VND": (0, ROUND_HALF_UP),\n'
            '    "CLP": (0, ROUND_HALF_UP),\n'
            '    "TWD": (0, ROUND_HALF_UP),\n'
            '    "XAU": (4, ROUND_HALF_DOWN),\n'
            '    "XAG": (4, ROUND_HALF_DOWN),\n'
            '    "XPT": (4, ROUND_HALF_DOWN),\n'
            '    "BTC": (8, ROUND_DOWN),\n'
            '    "ETH": (8, ROUND_DOWN),\n'
            '    "SOL": (8, ROUND_DOWN),\n'
            "}\n"
            "\n"
            "\n"
            "def _class_of(currency):\n"
            "    try:\n"
            "        return _TABLE[currency]\n"
            "    except KeyError:\n"
            "        raise CairnUnknownCurrency(currency) from None\n"
            "\n"
            "\n"
            "def round_amount(currency, value):\n"
            '    """Quantize the Decimal to the class decimals with the class mode."""\n'
            "    decimals, mode = _class_of(currency)\n"
            "    return value.quantize(Decimal(1).scaleb(-decimals), rounding=mode)\n"
            "\n"
            "\n"
            "def render_amount(currency, value):\n"
            '    """Render the ROUNDED amount: exactly the class decimals, plain ASCII\n'
            "    digits, ``.`` before the fractional digits (only when decimals > 0),\n"
            "    ``-`` for negatives, no thousands separators; a negative that rounds\n"
            '    to zero renders WITHOUT the sign."""\n'
            "    decimals, _mode = _class_of(currency)\n"
            "    rounded = round_amount(currency, value)\n"
            "    if not rounded:\n"
            "        rounded = abs(rounded)\n"
            "    if decimals == 0:\n"
            "        return str(int(rounded))\n"
            '    return f"{rounded:.{decimals}f}"\n',
        ),
        (
            "cairn_periods.py",
            None,
            '"""Fiscal period ids - implements the Cairn fiscal calendar (internal).\n'
            "\n"
            "The fiscal year starts March 1; twelve calendar-month periods P01..P12;\n"
            "the label is the calendar year of the fiscal-year START; all timestamps\n"
            "are UTC (naive datetimes ARE UTC, aware ones are converted first).\n"
            '"""\n'
            "\n"
            "from datetime import timezone\n"
            "\n"
            "\n"
            "def fiscal_period_id(ts):\n"
            '    """The fiscal period id of a timestamp, e.g. ``FY2026-P01``."""\n'
            "    if ts.tzinfo is not None:\n"
            "        ts = ts.astimezone(timezone.utc)\n"
            "    if ts.month >= 3:\n"
            "        fiscal_year = ts.year\n"
            "        period = ts.month - 2\n"
            "    else:\n"
            "        fiscal_year = ts.year - 1\n"
            "        period = ts.month + 10\n"
            '    return f"FY{fiscal_year:04d}-P{period:02d}"\n',
        ),
    ],
    "private2-drawbridge-rollout": [
        (
            "rollout_bucket.py",
            None,
            '"""Rollout bucketing - implements the Drawbridge mix (internal).\n'
            "\n"
            "The Drawbridge Rollout Standard's deterministic bucket recipe: the\n"
            "bucket id is B + 5 zero-padded digits from the salted flag/user digest.\n"
            '"""\n'
            "\n"
            "import hashlib\n"
            "\n"
            '_MIXING_TOKEN = "db-mix-01"\n'
            "_BUCKETS = 100_000\n"
            "\n"
            "\n"
            "def bucket_number(flag_key, user_id):\n"
            '    """The raw bucket number (0..99999) for one user on one flag.\n'
            "\n"
            "    Both the flag key and the user id are hashed byte-for-byte as given -\n"
            "    no trimming, no case folding.\n"
            '    """\n'
            '    material = f"{flag_key}::{user_id}::{_MIXING_TOKEN}"\n'
            '    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()\n'
            "    return int(digest[:13], 16) % _BUCKETS\n"
            "\n"
            "\n"
            "def bucket_of(flag_key, user_id):\n"
            '    """The user\'s bucket id for the flag: ``B`` + 5 zero-padded digits."""\n'
            '    return f"B{bucket_number(flag_key, user_id):05d}"\n',
        ),
        (
            "rollout_assign.py",
            None,
            '"""Feature-flag assignment - implements the Drawbridge resolution order\n'
            "(internal): kill-switch first (absolute, allow-list included), then the\n"
            "allow-list, then the percentage bucket.\n"
            '"""\n'
            "\n"
            "from rollout_bucket import bucket_number\n"
            "\n"
            "\n"
            "def assign(config, user_id):\n"
            '    """The assignment verdict for one user on one flag.\n'
            "\n"
            "    Resolution order: kill -> allow-list -> percentage bucket. Returns\n"
            "    exactly one verdict string.\n"
            '    """\n'
            '    if config.get("kill"):\n'
            '        return "HELD-KILL"\n'
            '    if user_id in config.get("allow", []):\n'
            '        return "ENROLLED-ALLOW"\n'
            '    if bucket_number(config["key"], user_id) < config.get("percent", 0) * 1000:\n'
            '        return "ENROLLED"\n'
            '    return "HELD-BUCKET"\n',
        ),
    ],
}
