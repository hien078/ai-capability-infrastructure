"""E2D format-arm tests (job e2d-format) — the v2 skill texts + arm R2 pins.

The E2D question: does HOW the private standard is written change outcomes?
Arm R2 serves each private2 fixture's v2 skill text (scripts/
private2_skill_v2.py — the SAME rules as the builder module's v1,
restructured for action); arm R serves the v1 prose. These pins keep the
comparison honest WITHOUT a DB, network or model:

* Every v2 carries EXACTLY the same rules as its v1 — verified rule-by-rule
  by an independent reviewer subagent that did NOT write the v2 (recorded
  in the job result; 80 example checks against the fixtures' known-good
  FIXES, all agreeing). Pinned here: the frontmatter contract (same
  name/description, version 2.0.0), the size cap, all standard-only
  MARKERS, and the exact worked-example lines the reviewer verified.
* NO worked example's input equals any test input. The test files carry
  their INPUT scenarios in plaintext (the model can read them in the
  workspace); only the expected OUTPUTS are sha256 digests — so the leak
  to prevent is an example whose input coincides with a test scenario
  (its printed output would be a line of the digested trace). The check
  is SCENARIO-level: no v2 example (currency, amount) pair / (flag, user)
  pair / record / version entry / (region, serial) / (message, script) /
  config scenario equals a test scenario, and no v2 example datetime
  equals a test timestamp. Atoms that already appear in the v1 skill text
  (rule vocabulary: currency codes, field names, the canonical example's
  tokens) are not new leaks — v1 carries them too.
* The v2 worked examples' OUTPUTS are correct per the standard (a wrong
  example would be a changed rule): pinned against the fixtures' known
  good FIXES implementations.
* Arm R2 = arm R exactly, but serving the v2 text at version 2.0.0 —
  and NOTHING else moves: R's dispatch stays byte-identical (no new
  kwargs), R2 is refused outside --set private2, and the served text +
  version are the only differences.
"""

import ast
import contextlib
import hashlib
import importlib
import json
import re
import sys
import types
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_hbench  # noqa: E402
from private2_skill_v2 import (  # noqa: E402
    PRIVATE2_SKILL_V2,
    PRIVATE2_SKILL_V2_VERSION,
)
from private2_tasks import PRIVATE2_FIXES, PRIVATE2_TASKS  # noqa: E402

from aci.domain.runtime.actions import ContinueAction  # noqa: E402
from aci.runtime.model_gateway import FakeModelGateway  # noqa: E402
from tests.sandbox_support import available_sandbox  # noqa: E402

BY_NAME = {t["name"]: t for t in PRIVATE2_TASKS}

#: The size cap: ~1,800 tokens at the kernel's own chars/4 heuristic.
SIZE_CAP_CHARS = 7_200

#: A zero-job round: --cases names no real fixture, so main() validates
#: everything (arms, set, flags) and writes a report WITHOUT any model
#: call, DB connection or sandbox dependency.
ZERO_JOB = ["--cases", "no-such-fixture", "--sandbox", "none"]

#: The exact worked-example OUTPUT fragments each v2 must carry — the
#: lines the independent rule-equivalence review verified against the
#: fixtures' known-good FIXES implementations (80/80 agreeing). Binding
#: them here keeps the served text from silently drifting away from the
#: verified examples (a wrong example would be a changed rule).
VERIFIED_EXAMPLE_FRAGMENTS: dict[str, list[str]] = {
    "private2-cairn-money": [
        'render_amount("EUR", Decimal("5.678")) -> "5.68"',
        'render_amount("JPY", Decimal("88.5")) -> "89"',
        'render_amount("XAG", Decimal("0.12345")) -> "0.1234"',
        'render_amount("BTC", Decimal("1.234567891")) -> "1.23456789"',
        'render_amount("USD", Decimal("-0.002")) -> "0.00"',
        "raises CairnUnknownCurrency",
        '-> "FY2026-P06"',
        '-> "FY2024-P11"',
        '-> "FY2025-P12"',
    ],
    "private2-drawbridge-rollout": [
        'bucket_of("checkout-turbo", "u-42") -> "B37334"',
        'bucket_of("billing-spark", "u-77") -> "B27586"',
        "HELD-KILL (kill beats the allow-list)",
        "(27586 < 50000)",
        "(27586 >= 10000)",
    ],
    "private2-palisade-redaction": [
        "email=jo\u2026om | event=login | note=signed in from mobile | "
        "phone=+1\u202677 | zip=60###",
        "card=pan:19ff47cc8024 | event=charge",
        '-> "event=rotate"',
        "birth_year=20## | event=signup",
    ],
    "private2-cairn-sunset": [
        "X-Cairn-Deprecation: 20260401",
        "X-Cairn-Sunset: 20261201",
        "X-Cairn-Removal: 20270601",
        "X-Cairn-Sunset: 20260928",
    ],
    "private2-vellum-order-id": [
        '-> "NA-0008642-X"',
        'format_order_id("EU", 130579) -> "EU00130579X"',
        'format_order_id("AP", 42)     -> "AP-000042U"',
        'validate("NA-0008642-K") -> (False, "seal-fault")',
        'validate("NA-0008642-I") -> (False, "shape-fault")',
    ],
    "private2-queue-consumer-retry": [
        "[ferry] retry m-77 family=sputter wait=2s",
        "[ferry] retry m-77 family=sputter wait=8s",
        "[ferry] landed m-77 tries=3",
        "[ferry] dead m-78 family=spoiled tries=1 raised=ValueError",
        '"sample":',
        '"probe-2"',
        "[ferry] retry m-79 family=wild wait=5s",
        "[ferry] landed m-79 tries=2",
        "[ferry] dead m-80 family=sputter tries=3 raised=ConnectionResetError",
    ],
    "private2-infra-config": [
        "pool-size=45@profile",
        "admin-password=pw-1@defaults",
        "api-token=[hbr-redacted]@defaults",
        "session-tokens=st-1@defaults",
        "vault-secret=[hbr-redacted]@defaults",
    ],
}


# ---------------------------------------------------------------------------
# The fixture test files' INPUT scenarios (exec'd with the sibling workspace
# modules stubbed — the scenarios are plaintext in the workspace; only the
# expected outputs are digests).
# ---------------------------------------------------------------------------


def _test_file(fixture: dict) -> tuple[str, str]:
    """(filename, source) of the fixture's contract test file."""
    for rel, content in fixture["files"].items():
        if rel.startswith("test_"):
            return rel, content
    raise AssertionError(f"{fixture['name']}: no test file")


class _Stub:
    """A stand-in for a sibling workspace module's attribute."""

    def __call__(self, *args: object, **kwargs: object) -> Any:  # noqa: ARG002
        return _Stub()

    def __getattr__(self, name: str) -> Any:  # noqa: ARG002
        return _Stub()


def _scenario_namespace(fixture: dict) -> dict[str, Any]:
    """Exec the fixture's test file with its sibling workspace-module
    imports stubbed, and return the namespace (the scenario data). The
    stubs are removed from sys.modules afterwards so fixtures cannot leak
    into each other."""
    _rel, source = _test_file(fixture)
    tree = ast.parse(source)
    stubs: dict[str, types.ModuleType] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        try:
            importlib.import_module(node.module)
        except ImportError:
            stub = types.ModuleType(node.module)
            for alias in node.names:
                setattr(stub, alias.name, _Stub())
            stubs[node.module] = stub
    saved = {name: mod for name, mod in sys.modules.items() if name in stubs}
    sys.modules.update(stubs)
    try:
        namespace: dict[str, Any] = {}
        exec(compile(source, f"<{fixture['name']} test>", "exec"), namespace)  # noqa: S102
        return namespace
    finally:
        for name in stubs:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _atoms(obj: Any) -> set[str]:
    """Every string/number atom in a parsed scenario structure."""
    if isinstance(obj, str):
        return {obj}
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return {str(obj)}
    if isinstance(obj, dict):
        return {a for key, value in obj.items() for a in _atoms(key) | _atoms(value)}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return {a for item in obj for a in _atoms(item)}
    return set()


def _test_atoms(fixture: dict) -> set[str]:
    """Every data atom in the fixture's test-file INPUT scenarios (the
    UPPERCASE module-level data structures the parametrized tests drive)."""
    return _atoms({k: v for k, v in _scenario_namespace(fixture).items() if k.isupper()})


def _text_atoms(text: str) -> set[str]:
    """The quoted strings and bare numbers in a slab of skill text."""
    quoted = set(re.findall(r'"([^"\n]{1,80})"', text))
    numbers = set(re.findall(r"(?<![\w.])\d[\d._]*(?![\w.])", text))
    return quoted | numbers


# ---------------------------------------------------------------------------
# The v2 worked examples' input scenarios, per fixture (mirrors the
# "Worked examples" section of each v2 text in private2_skill_v2.py).
# ---------------------------------------------------------------------------

#: cairn-money: (currency, raw) pairs + (y, m, d) datetimes.
CAIRN_MONEY_PAIRS = [
    ("EUR", "5.678"),
    ("JPY", "88.5"),
    ("XAG", "0.12345"),
    ("BTC", "1.234567891"),
    ("USD", "-0.002"),
    ("XYZ", "2.00"),
]
CAIRN_MONEY_DATES = [(2026, 8, 9), (2025, 1, 20), (2026, 2, 14)]

#: drawbridge: (flag, user) pairs + the example config scenarios.
DRAWBRIDGE_PAIRS = [("checkout-turbo", "u-42"), ("billing-spark", "u-77")]
DRAWBRIDGE_CONFIGS = [
    {"key": "billing-spark", "kill": True, "allow": ["u-77"], "percent": 100},
    {"key": "billing-spark", "kill": False, "allow": ["u-77"], "percent": 0},
    {"key": "billing-spark", "kill": False, "allow": [], "percent": 50},
    {"key": "billing-spark", "kill": False, "allow": [], "percent": 10},
]

#: palisade: the example records.
PALISADE_RECORDS = [
    {
        "event": "login",
        "email": "jo.reyes@example.com",
        "phone": "+1-312-555-0177",
        "zip": "60614",
        "note": "signed in from mobile",
    },
    {"card": "6011111111111117", "event": "charge"},
    {"token": "tok_q9w8", "phone": "9x7", "event": "rotate"},
    {"birth_year": "2001", "event": "signup"},
]

#: cairn-sunset: the example version entries + now dates.
SUNSET_ENTRY = {
    "version": "v4",
    "introduced_on": date(2025, 4, 12),
    "deprecated_on": date(2026, 4, 1),
    "sunset_on": date(2026, 12, 1),
    "removed_on": date(2027, 6, 1),
}
SUNSET_FLOOR_ENTRY = {
    "version": "v8",
    "introduced_on": date(2025, 4, 12),
    "deprecated_on": date(2026, 4, 1),
    "sunset_on": date(2026, 6, 15),
    "removed_on": date(2027, 3, 1),
}
SUNSET_NOWS = [
    date(2026, 3, 2),
    date(2026, 5, 5),
    date(2027, 1, 7),
    date(2027, 7, 4),
    date(2026, 7, 1),
]

#: vellum: (region, serial) pairs + candidate strings.
VELLUM_PAIRS = [("NA", 8642), ("EU", 130579), ("AP", 42)]
VELLUM_CANDIDATES = ["NA-0008642-K", "NA-0008642-I"]

#: ferry: (mid, body) pairs (the exception class names in the v2 examples
#: are RULE VOCABULARY — the family table names them; the test INPUT is the
#: (message, script) pair, and no example pair equals a test pair).
FERRY_PAIRS = [("m-77", "settle-9"), ("m-78", "probe-2"), ("m-79", "audit-3"), ("m-80", "claim-4")]

#: harbor: the example config scenarios (keys and values).
HARBOR_SCENARIOS = [
    {
        "defaults": {"Pool_Size": "15"},
        "file": {"pool.size": "30"},
        "profile": {"POOL_SIZE": "45"},
        "env": {"HBR_POOL__SIZE": "60", "HBR_POOL_SIZE": "7", "hbr_pool__size": "8"},
    },
    {
        "defaults": {
            "admin_password": "pw-1",
            "api_token": "tok-1",
            "session_tokens": "st-1",
            "vault_secret": "vs-1",
        }
    },
]


class TestV2Contract:
    """The v2 texts' structural contract: same standard, same frontmatter,
    version 2.0.0, size cap, all markers present, the actionable shape, the
    verified example lines, and no digest leak."""

    def test_the_v2_map_covers_exactly_the_private2_fixtures(self) -> None:
        assert set(PRIVATE2_SKILL_V2) == {t["name"] for t in PRIVATE2_TASKS}
        assert len(PRIVATE2_SKILL_V2) == 7

    def test_frontmatter_name_and_description_match_v1_version_2(self) -> None:
        for task in PRIVATE2_TASKS:
            v1, v2 = task["skill"], PRIVATE2_SKILL_V2[task["name"]]
            assert v2.startswith("---\n"), task["name"]
            name1 = re.search(r"^name: (.+)$", v1, re.M).group(1)
            name2 = re.search(r"^name: (.+)$", v2, re.M).group(1)
            desc1 = re.search(r"^description: (.+)$", v1, re.M).group(1)
            desc2 = re.search(r"^description: (.+)$", v2, re.M).group(1)
            assert name1 == name2 == task["skill_id"], task["name"]
            assert desc1 == desc2, task["name"]
            assert re.search(r"^version: 2\.0\.0$", v2, re.M), task["name"]
        assert PRIVATE2_SKILL_V2_VERSION == "2.0.0"

    def test_every_v2_is_under_the_size_cap(self) -> None:
        """≤ ~1,800 tokens at the kernel's own chars/4 heuristic."""
        for task in PRIVATE2_TASKS:
            v2 = PRIVATE2_SKILL_V2[task["name"]]
            assert len(v2) <= SIZE_CAP_CHARS, (task["name"], len(v2))
            # ...and it is a real rewrite, not a stub.
            assert len(v2) > 1_000, task["name"]

    def test_every_v2_carries_all_standard_only_markers(self) -> None:
        for task in PRIVATE2_TASKS:
            v2 = PRIVATE2_SKILL_V2[task["name"]]
            for marker in task["markers"]:
                assert marker in v2, (task["name"], marker)

    def test_every_v2_has_the_actionable_sections(self) -> None:
        """The E2D format: checklist + worked examples + a verify line —
        every v2 carries all of them (the compact rules live in the
        tables/pseudocode the checklist references)."""
        for task in PRIVATE2_TASKS:
            v2 = PRIVATE2_SKILL_V2[task["name"]]
            assert "## What to change" in v2, task["name"]
            assert "## Worked examples" in v2, task["name"]
            assert "## How to verify locally" in v2, task["name"]
            assert "python -m pytest -q" in v2, task["name"]
            assert "brute-force" in v2, task["name"]

    def test_every_v2_carries_the_verified_example_lines(self) -> None:
        """The exact worked-example outputs the independent rule-equivalence
        review verified against the FIXES (80/80 agreeing) — binding them
        keeps the served text from drifting away from the verified examples."""
        for task in PRIVATE2_TASKS:
            v2 = PRIVATE2_SKILL_V2[task["name"]]
            for fragment in VERIFIED_EXAMPLE_FRAGMENTS[task["name"]]:
                assert fragment in v2, (task["name"], fragment)

    def test_no_v2_leaks_a_test_digest(self) -> None:
        """The tests' expected outputs are sha256 digests — NONE of them may
        appear in any v2 text (that would be the leak the digests exist to
        prevent)."""
        for task in PRIVATE2_TASKS:
            v2 = PRIVATE2_SKILL_V2[task["name"]]
            for atom in _test_atoms(task):
                if re.fullmatch(r"[0-9a-f]{64}", atom):
                    assert atom not in v2, (task["name"], atom)
            # ...and no 64-hex blob at all (defense in depth).
            assert not re.search(r"\b[0-9a-f]{64}\b", v2), task["name"]

    def test_no_v2_is_a_workspace_file(self) -> None:
        """The v2 texts are skill metadata, NEVER shipped in a workspace."""
        for task in PRIVATE2_TASKS:
            v2 = PRIVATE2_SKILL_V2[task["name"]]
            assert v2 not in task["files"].values(), task["name"]
            assert task["skill_id"] not in task["files"], task["name"]


class TestNoExampleInputIsATestInput:
    """No worked example's input equals any test input (scenario-level).

    The test files carry their INPUT scenarios in plaintext (the model can
    read them in the workspace); only the expected OUTPUTS are sha256
    digests. The leak to prevent is an example whose input coincides with
    a test scenario — its printed output would be a line of the digested
    trace. Tokens that already appear in the v1 skill text (rule
    vocabulary: currency codes, field names, the canonical example's
    tokens) are not new leaks: v1 carries them too."""

    def test_cairn_money_examples_never_hit_a_test_scenario(self) -> None:
        ns = _scenario_namespace(BY_NAME["private2-cairn-money"])
        money_pairs = {p for group in ns["MONEY_SCENARIOS"] for p in group}
        ledger_pairs = {(cur, raw) for group in ns["LEDGER_SCENARIOS"] for _s, cur, raw in group}
        for pair in CAIRN_MONEY_PAIRS:
            assert pair not in money_pairs, pair
            assert pair not in ledger_pairs, pair
        stamp_dates = set()
        for group in ns["PERIOD_SCENARIOS"]:
            for stamp in group:
                dt = datetime.fromisoformat(stamp)
                if dt.tzinfo is not None:
                    dt = dt.astimezone(UTC)
                stamp_dates.add((dt.year, dt.month, dt.day))
        for y, m, d in CAIRN_MONEY_DATES:
            assert (y, m, d) not in stamp_dates, (y, m, d)

    def test_drawbridge_examples_never_hit_a_test_scenario(self) -> None:
        ns = _scenario_namespace(BY_NAME["private2-drawbridge-rollout"])
        bucket_pairs = {p for group in ns["BUCKET_SCENARIOS"] for p in group}
        for pair in DRAWBRIDGE_PAIRS:
            assert pair not in bucket_pairs, pair
        test_configs = [config for config, _users in ns["ROLLOUT_SCENARIOS"]]
        for config in DRAWBRIDGE_CONFIGS:
            assert config not in test_configs, config

    def test_palisade_examples_never_hit_a_test_scenario(self) -> None:
        ns = _scenario_namespace(BY_NAME["private2-palisade-redaction"])
        v1 = BY_NAME["private2-palisade-redaction"]["skill"]
        for record in PALISADE_RECORDS:
            assert record not in ns["RECORDS"], record
            # Field-wise: no example value coincides with a test record's
            # value for the same field (those fields are what the digests
            # render) — except tokens the v1 skill text already carries
            # (the canonical marker example's "login" event, the field
            # names' vocabulary): v1 ships those too, so they are not
            # new leaks.
            for field, value in record.items():
                if value not in v1:
                    assert value not in {r.get(field) for r in ns["RECORDS"]}, (field, value)

    def test_cairn_sunset_examples_never_hit_a_test_scenario(self) -> None:
        ns = _scenario_namespace(BY_NAME["private2-cairn-sunset"])
        test_entries = [entry for _resp, entry, _now in ns["CASES"]]
        for entry in (SUNSET_ENTRY, SUNSET_FLOOR_ENTRY):
            assert entry not in test_entries, entry
        test_nows = [now for _resp, _entry, now in ns["CASES"]]
        for now in SUNSET_NOWS:
            assert now not in test_nows, now
        # No example date (any entry field) coincides with any test date.
        test_dates = {
            d for entry in test_entries for d in entry.values() if isinstance(d, date)
        } | set(test_nows)
        for entry in (SUNSET_ENTRY, SUNSET_FLOOR_ENTRY):
            for d in entry.values():
                assert d not in test_dates, d
        for now in SUNSET_NOWS:
            assert now not in test_dates, now

    def test_vellum_examples_never_hit_a_test_scenario(self) -> None:
        ns = _scenario_namespace(BY_NAME["private2-vellum-order-id"])
        for pair in VELLUM_PAIRS:
            assert pair not in ns["FORMAT_CASES"], pair
            assert pair not in ns["DOC_CASES"], pair
        for text in VELLUM_CANDIDATES:
            assert text not in ns["CANDIDATES"], text
            assert text not in ns["REFERENCE_CASES"], text

    def test_ferry_examples_never_hit_a_test_scenario(self) -> None:
        ns = _scenario_namespace(BY_NAME["private2-queue-consumer-retry"])
        test_pairs = {
            (m["mid"], m["body"]) for group in (ns["GROUP_A"], ns["GROUP_B"]) for m, _s in group
        }
        for pair in FERRY_PAIRS:
            assert pair not in test_pairs, pair

    def test_harbor_examples_never_hit_a_test_scenario(self) -> None:
        ns = _scenario_namespace(BY_NAME["private2-infra-config"])
        v1 = BY_NAME["private2-infra-config"]["skill"]
        test_atoms = _atoms(ns["SCENARIOS"])
        for scenario in HARBOR_SCENARIOS:
            for atom in _atoms(scenario):
                # The four source names (defaults/file/profile/env) are the
                # standard's own vocabulary — v1 carries them, so they are
                # not new leaks; every OTHER atom must be fresh.
                if atom not in v1:
                    assert atom not in test_atoms, atom

    def test_example_atoms_are_rule_vocabulary_or_new(self) -> None:
        """Every quoted string / number in a v2's worked-examples section
        that DOES appear in the fixture's test-file scenarios must already
        appear in the v1 skill text (rule vocabulary carried from v1 — the
        currency codes, the field names, the canonical example's tokens).
        A NEW atom (one v1 never carried) must not be a test atom: that
        would be an example input copied from a test scenario."""
        for task in PRIVATE2_TASKS:
            v1, v2 = task["skill"], PRIVATE2_SKILL_V2[task["name"]]
            examples = v2.split("## Worked examples", 1)[1]
            test_atoms = _test_atoms(task)
            for atom in _text_atoms(examples):
                if atom in test_atoms:
                    assert atom in v1, (task["name"], atom)


# ---------------------------------------------------------------------------
# The v2 worked examples' OUTPUTS are correct per the standard (a wrong
# example would be a changed rule): checked against the fixtures' known
# good FIXES implementations.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _fixed_modules(fixture_name: str, tmp_root: Path):
    """Materialize a fixture's shipped files, apply its known root-cause
    fix (the standard-conforming implementation), import the fixed
    modules, and clean sys.path/sys.modules afterwards."""
    import verify_private_fixtures as vpf

    fixture = BY_NAME[fixture_name]
    task_dir = vpf.materialize(fixture, tmp_root / fixture_name)
    vpf.apply_fix(task_dir, PRIVATE2_FIXES[fixture_name])
    saved_path = list(sys.path)
    sys.path.insert(0, str(task_dir))
    try:
        yield task_dir
    finally:
        sys.path[:] = saved_path
        for name, mod in list(sys.modules.items()):
            mod_file = getattr(mod, "__file__", None)
            if mod_file and mod_file.startswith(str(task_dir)):
                del sys.modules[name]


class TestV2ExamplesAreCorrect:
    """Every v2 worked example's output matches the standard's own
    known-good implementation (the FIXES) — an incorrect example would be
    a changed rule, breaking the E2D isolation (FORMAT is the only
    difference between R2 and R)."""

    def test_cairn_money_examples(self, tmp_path: Path) -> None:
        with _fixed_modules("private2-cairn-money", tmp_path):
            import cairn_money
            import cairn_periods

            assert cairn_money.render_amount("EUR", Decimal("5.678")) == "5.68"
            assert cairn_money.render_amount("JPY", Decimal("88.5")) == "89"
            assert cairn_money.render_amount("XAG", Decimal("0.12345")) == "0.1234"
            assert cairn_money.render_amount("BTC", Decimal("1.234567891")) == "1.23456789"
            assert cairn_money.render_amount("USD", Decimal("-0.002")) == "0.00"
            with pytest.raises(cairn_money.CairnUnknownCurrency):
                cairn_money.render_amount("XYZ", Decimal("2.00"))
            assert cairn_periods.fiscal_period_id(datetime(2026, 8, 9, tzinfo=UTC)) == "FY2026-P06"
            assert cairn_periods.fiscal_period_id(datetime(2025, 1, 20, tzinfo=UTC)) == "FY2024-P11"
            assert cairn_periods.fiscal_period_id(datetime(2026, 2, 14, tzinfo=UTC)) == "FY2025-P12"

    def test_drawbridge_examples(self, tmp_path: Path) -> None:
        with _fixed_modules("private2-drawbridge-rollout", tmp_path):
            import rollout_assign
            import rollout_bucket

            assert rollout_bucket.bucket_of("checkout-turbo", "u-42") == "B37334"
            assert rollout_bucket.bucket_of("billing-spark", "u-77") == "B27586"
            verdicts = [rollout_assign.assign(config, "u-77") for config in DRAWBRIDGE_CONFIGS]
            assert verdicts == ["HELD-KILL", "ENROLLED-ALLOW", "ENROLLED", "HELD-BUCKET"]

    def test_palisade_examples(self, tmp_path: Path) -> None:
        with _fixed_modules("private2-palisade-redaction", tmp_path):
            import log_filter

            assert (
                log_filter.redact_record(
                    {
                        "event": "login",
                        "email": "jo.reyes@example.com",
                        "phone": "+1-312-555-0177",
                        "zip": "60614",
                        "note": "signed in from mobile",
                    }
                )
                == "email=jo\u2026om | event=login | note=signed in from mobile | "
                "phone=+1\u202677 | zip=60###"
            )
            assert (
                log_filter.redact_record({"card": "6011111111111117", "event": "charge"})
                == "card=pan:19ff47cc8024 | event=charge"
            )
            assert (
                log_filter.redact_record({"token": "tok_q9w8", "phone": "9x7", "event": "rotate"})
                == "event=rotate"
            )
            assert (
                log_filter.redact_record({"birth_year": "2001", "event": "signup"})
                == "birth_year=20## | event=signup"
            )

    def test_cairn_sunset_examples(self, tmp_path: Path) -> None:
        with _fixed_modules("private2-cairn-sunset", tmp_path):
            import versioning

            resp = {"status": 200, "headers": {"Content-Type": "application/json"}, "body": "{}"}
            steady = versioning.decorate(resp, SUNSET_ENTRY, date(2026, 3, 2))
            assert steady["headers"]["X-Cairn-Version"] == "v4"
            assert "X-Cairn-Deprecation" not in steady["headers"]
            notice = versioning.decorate(resp, SUNSET_ENTRY, date(2026, 5, 5))
            assert notice["headers"]["X-Cairn-Deprecation"] == "20260401"
            assert notice["headers"]["X-Cairn-Sunset"] == "20261201"
            final = versioning.decorate(resp, SUNSET_ENTRY, date(2027, 1, 7))
            assert final["headers"]["X-Cairn-Sunset"] == "20261201"
            assert final["headers"]["X-Cairn-Removal"] == "20270601"
            gone = versioning.decorate(resp, SUNSET_ENTRY, date(2027, 7, 4))
            assert gone == {
                "status": 410,
                "headers": {"X-Cairn-Version": "v4", "X-Cairn-Removal": "20270601"},
                "body": "",
            }
            floor = versioning.decorate(resp, SUNSET_FLOOR_ENTRY, date(2026, 7, 1))
            assert floor["headers"]["X-Cairn-Sunset"] == "20260928"

    def test_vellum_examples(self, tmp_path: Path) -> None:
        with _fixed_modules("private2-vellum-order-id", tmp_path):
            import order_ids

            assert order_ids.format_order_id("NA", 8642) == "NA-0008642-X"
            assert order_ids.format_order_id("EU", 130579) == "EU00130579X"
            assert order_ids.format_order_id("AP", 42) == "AP-000042U"
            assert order_ids.validate("NA-0008642-K") == (False, "seal-fault")
            assert order_ids.validate("NA-0008642-I") == (False, "shape-fault")

    def test_ferry_examples(self, tmp_path: Path) -> None:
        with _fixed_modules("private2-queue-consumer-retry", tmp_path):
            from consumer import FerryConsumer

            def drive(mid: str, body: str, script: list[type[Exception]]) -> dict:
                class _Scripted:
                    def __init__(self) -> None:
                        self._script = list(script)
                        self._calls = 0

                    def __call__(self, message: object) -> None:
                        self._calls += 1
                        if self._calls <= len(self._script):
                            raise self._script[self._calls - 1]()

                class _Clock:
                    def __init__(self) -> None:
                        self.sleeps: list[float] = []

                    def sleep(self, seconds: float) -> None:
                        self.sleeps.append(seconds)

                trace: list[str] = []
                clock = _Clock()
                dead: list[dict] = []
                consumer = FerryConsumer(
                    "orders",
                    _Scripted(),
                    clock=clock,
                    on_event=trace.append,
                    dead_letters=dead.append,
                )
                outcome = consumer.process({"mid": mid, "body": body})
                return {"trace": trace, "outcome": outcome, "sleeps": clock.sleeps, "dead": dead}

            r = drive("m-77", "settle-9", [TimeoutError, TimeoutError])
            assert r["trace"] == [
                "[ferry] retry m-77 family=sputter wait=2s",
                "[ferry] retry m-77 family=sputter wait=8s",
                "[ferry] landed m-77 tries=3",
            ]
            assert r["outcome"] == "landed" and r["sleeps"] == [2, 8] and not r["dead"]

            r = drive("m-78", "probe-2", [ValueError])
            assert r["trace"] == ["[ferry] dead m-78 family=spoiled tries=1 raised=ValueError"]
            assert r["outcome"] == "buried" and r["sleeps"] == []
            assert r["dead"] == [
                {
                    "queue": "orders",
                    "mid": "m-78",
                    "family": "spoiled",
                    "tries": 1,
                    "raised": "ValueError",
                    "sample": "probe-2",
                }
            ]

            r = drive("m-79", "audit-3", [RuntimeError])
            assert r["trace"] == [
                "[ferry] retry m-79 family=wild wait=5s",
                "[ferry] landed m-79 tries=2",
            ]
            assert r["outcome"] == "landed" and r["sleeps"] == [5]

            r = drive("m-80", "claim-4", [ConnectionResetError] * 3)
            assert r["trace"] == [
                "[ferry] retry m-80 family=sputter wait=2s",
                "[ferry] retry m-80 family=sputter wait=8s",
                "[ferry] dead m-80 family=sputter tries=3 raised=ConnectionResetError",
            ]
            assert r["outcome"] == "buried"
            assert r["dead"] == [
                {
                    "queue": "orders",
                    "mid": "m-80",
                    "family": "sputter",
                    "tries": 3,
                    "raised": "ConnectionResetError",
                }
            ]

    def test_harbor_examples(self, tmp_path: Path) -> None:
        with _fixed_modules("private2-infra-config", tmp_path):
            from config_resolver import ConfigResolver

            resolver = ConfigResolver(**HARBOR_SCENARIOS[0])
            assert resolver.report() == ["pool-size=45@profile"]
            resolver = ConfigResolver(**HARBOR_SCENARIOS[1])
            assert resolver.report() == [
                "admin-password=pw-1@defaults",
                "api-token=[hbr-redacted]@defaults",
                "session-tokens=st-1@defaults",
                "vault-secret=[hbr-redacted]@defaults",
            ]


# ---------------------------------------------------------------------------
# Arm R2 — the runner wiring (additive; every existing arm byte-identical).
# ---------------------------------------------------------------------------


class TestArmR2:
    """R2 = arm R exactly, but serving the fixture's v2 skill text at
    version 2.0.0. The set check, the dispatch, the served text and the
    existing arms' byte-identity are all pinned WITHOUT a DB or model."""

    def test_r2_passes_the_set_check_on_private2(self, tmp_path: Path) -> None:
        """A zero-job round returns 0 — the set check passed (it would exit
        2 otherwise) and no model/DB was touched."""
        out = tmp_path / "r2.json"
        argv = ["--api-key", "k", "--set", "private2", "--arms", "R2", *ZERO_JOB, "--out", str(out)]
        assert run_hbench.main(argv) == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["fixture_set"] == "private2"
        assert report["arms"] == ["R2"]

    def test_r2_refused_outside_private2(self) -> None:
        """The v2 texts exist ONLY for the 7 private2 fixtures — every
        other set (private included) is refused loudly BEFORE any run."""
        for set_name in ("verified", "domain", "private", "horizon"):
            assert run_hbench.main(["--api-key", "k", "--set", set_name, "--arms", "R2"]) == 2

    def test_r_still_passes_the_set_check_on_private(self, tmp_path: Path) -> None:
        """The pre-existing R behavior is unchanged: R stays valid on BOTH
        private sets (R2 is the private2-only arm)."""
        out = tmp_path / "r.json"
        argv = ["--api-key", "k", "--set", "private", "--arms", "R", *ZERO_JOB, "--out", str(out)]
        assert run_hbench.main(argv) == 0
        assert json.loads(out.read_text(encoding="utf-8"))["arms"] == ["R"]

    def test_dispatch_r2_serves_v2_r_stays_byte_identical(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """`_run_one` routes BOTH R and R2 through run_private_arm. R's
        dispatch passes NEITHER skill_text NOR arm (the pre-R2 call shape,
        byte-identical); R2's passes exactly those two kwargs — the served
        v2 text and the arm label — and nothing else differs."""
        calls: list[dict[str, Any]] = []

        def _record_call(*args: object, **kwargs: object) -> dict[str, Any]:
            calls.append({"args": args, "kwargs": kwargs})
            return {}

        monkeypatch.setattr(run_hbench, "run_private_arm", _record_call)
        fixture = PRIVATE2_TASKS[0]
        for arm in ("R", "R2"):
            run_hbench._run_one(
                fixture,
                arm,
                "http://gateway.invalid/v1",
                "some-model",
                "key",
                tmp_path / "src",
                tmp_path / "runs",
                max_turns=3,
            )
        (r_call, r2_call) = calls
        # R: the pre-R2 dispatch — NEITHER kwarg is passed (the defaults
        # serve the v1 text under the arm label R).
        assert "skill_text" not in r_call["kwargs"]
        assert "arm" not in r_call["kwargs"]
        # R2: the fixture's v2 text + the arm label — nothing else differs.
        assert r2_call["kwargs"]["skill_text"] == PRIVATE2_SKILL_V2[fixture["name"]]
        assert r2_call["kwargs"]["arm"] == "R2"
        assert set(r2_call["kwargs"]) - {"skill_text", "arm"} == set(r_call["kwargs"])
        for key, value in r_call["kwargs"].items():
            assert r2_call["kwargs"][key] == value, key

    def test_private_client_serves_v2_at_2_0_0_and_v1_at_1_0_0(self) -> None:
        """The local handler is honest about WHICH text it serves: R's
        selection carries the v1 text at version 1.0.0, R2's the v2 text
        at 2.0.0 — each with its own REAL sha256."""
        fixture = PRIVATE2_TASKS[0]
        v2 = PRIVATE2_SKILL_V2[fixture["name"]]
        r_client = run_hbench._PrivateSkillACIClient(
            skill_id=str(fixture["skill_id"]), skill_text=str(fixture["skill"])
        )
        r2_client = run_hbench._PrivateSkillACIClient(
            skill_id=str(fixture["skill_id"]), skill_text=v2, version="2.0.0"
        )
        for client, text, version in (
            (r_client, fixture["skill"], "1.0.0"),
            (r2_client, v2, "2.0.0"),
        ):
            (payload, digest) = client.resolve(fixture["skill_id"], version)
            assert payload == text.encode("utf-8")
            assert digest == hashlib.sha256(payload).hexdigest()
            selection = client.search(object())[0]
            assert selection.capability_id == fixture["skill_id"]
            assert selection.version == version
            assert selection.digest == digest
            assert selection.estimated_context_tokens >= 1

    def test_r2_first_request_carries_the_v2_text_r_carries_v1(self, tmp_path: Path) -> None:
        """The whole R2−R difference, on the wire: R2's FIRST ModelRequest
        contains the v2 text (its 'What to change' section, at version
        2.0.0); R's contains the v1 text at 1.0.0 and no v2 section."""
        fixture = PRIVATE2_TASKS[0]
        sources = tmp_path / "src"
        (sources / fixture["name"]).mkdir(parents=True)
        for rel, content in fixture["files"].items():
            (sources / fixture["name"] / rel).write_text(content, encoding="utf-8")
        contract, spec = run_hbench._contract_spec(fixture)
        records: dict[str, dict] = {}
        for arm in ("R", "R2"):
            gateway = FakeModelGateway([ContinueAction()])
            records[arm] = run_hbench.run_private_arm(
                fixture,
                contract,
                spec,
                gateway,  # type: ignore[arg-type]
                sources,
                tmp_path / f"runs-{arm}",
                max_turns=1,
                sandbox=available_sandbox(),
                skill_text=PRIVATE2_SKILL_V2[fixture["name"]] if arm == "R2" else None,
                arm=arm,
            )
            records[arm]["first"] = gateway.requests[0].messages
        r_first, r2_first = records["R"]["first"], records["R2"]["first"]
        # R2: the v2 text is in context from the first request on, at 2.0.0.
        assert any("What to change" in m.content for m in r2_first)
        assert any(f"{fixture['skill_id']}@2.0.0" in m.content for m in r2_first)
        # R: the v1 text at 1.0.0 — and NO v2 section.
        assert all("What to change" not in m.content for m in r_first)
        assert any(f"{fixture['skill_id']}@1.0.0" in m.content for m in r_first)
        # The v2 rules ARE the v1 rules: the standard's title rides along.
        assert any("Cairn Money Standard" in m.content for m in r2_first)
        # The counters: both arms preload exactly the one skill (a preload
        # is a loaded skill, NOT a model request).
        assert records["R"]["arm"] == "R"
        assert records["R"]["skills_preloaded"] == [f"{fixture['skill_id']}@1.0.0"]
        assert records["R"]["capability_requests"] == 0
        assert records["R2"]["arm"] == "R2"
        assert records["R2"]["skills_preloaded"] == [f"{fixture['skill_id']}@2.0.0"]
        assert records["R2"]["capability_requests"] == 0
