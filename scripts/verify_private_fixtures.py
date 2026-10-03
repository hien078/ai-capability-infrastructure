"""Verify the private-knowledge fixtures before any agent run (§34 discipline).

Same contract as verify_domain_fixtures.py: each fixture must be FAILING in
its shipped (buggy) state AND PASSING after the known root-cause fix —
otherwise the acceptance signal is meaningless. The private fixtures carry
their fix as a WHOLE-FILE replacement (the fix is a reimplementation of the
client/gate against the fixture's private standard, not a one-line splice):
a step of (file, None, content) replaces the file; (file, old, new) splices
like the multi verifier.

Sets (--set): 'private3' = the 40 dense-corpus fixtures
(scripts/private3_tasks.py); 'private' (default) = the 2 E2/E2B fixtures
(scripts/private_tasks.py, fixes in PRIVATE_FIXES below); 'private2' = the
7 E2C fixtures (scripts/private2_tasks.py, fixes in the builder modules,
assembled by the private2 aggregator); 'all' = both.

Usage:
    .venv/bin/python scripts/verify_private_fixtures.py [--set private|private2|all]
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from private2_tasks import PRIVATE2_FIXES, PRIVATE2_TASKS  # noqa: E402
from private3_tasks import PRIVATE3_FIXES, PRIVATE3_TASKS  # noqa: E402
from private_tasks import PRIVATE_TASKS  # noqa: E402

PYTEST = str(Path(__file__).resolve().parent.parent / ".venv/bin/python")

# The known root-cause fix per fixture — whole-file replacements (the fix
# IS the client/gate conforming to the private standard). Applied by
# apply_fix.
PRIVATE_FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    # The Atlas Error Handling Standard: the code table (101 transient,
    # 205 client fault, 307 degraded, 404 escalate, unknown -> escalate),
    # quadratic transient backoff (1s, 4s) capped at 3 attempts, one 2s
    # degraded retry then the stale-cache fallback, the event-line formats
    # and the four error type names.
    "private-atlas-retry": [
        (
            "atlas_client.py",
            None,
            '"""Atlas service client - implements the Atlas Error Handling Standard."""\n'
            "\n"
            "\n"
            "class AtlasError(Exception):\n"
            '    """Base class for all Atlas call failures."""\n'
            "\n"
            "\n"
            "class AtlasTransientError(AtlasError):\n"
            '    """A TRANSIENT code still failing after the full retry budget."""\n'
            "\n"
            "\n"
            "class AtlasClientError(AtlasError):\n"
            '    """A CLIENT_FAULT code - never retried."""\n'
            "\n"
            "\n"
            "class AtlasDegradedError(AtlasError):\n"
            '    """A DEGRADED code still failing after its single retry, no fallback."""\n'
            "\n"
            "\n"
            "class AtlasEscalationError(AtlasError):\n"
            '    """An ESCALATE (or unknown) code - escalated immediately, never retried."""\n'
            "\n"
            "\n"
            "_KNOWN_CLASSES = {\n"
            '    "ATL-E101": "TRANSIENT",\n'
            '    "ATL-E205": "CLIENT_FAULT",\n'
            '    "ATL-E307": "DEGRADED",\n'
            '    "ATL-E404": "ESCALATE",\n'
            "}\n"
            "\n"
            "_TRANSIENT_ATTEMPTS = 3\n"
            "_DEGRADED_WAIT_SECONDS = 2\n"
            "\n"
            "\n"
            "class AtlasClient:\n"
            "    def __init__(self, transport, *, clock=None, on_event=None):\n"
            "        self._transport = transport\n"
            "        self._clock = clock\n"
            "        self._on_event = on_event\n"
            "\n"
            "    def call(self, op, payload=None, *, fallback=None):\n"
            "        response = self._transport.request(op, payload)\n"
            '        if response.startswith("OK "):\n'
            '            return response[len("OK ") :]\n'
            "        code = response.split()[-1]\n"
            '        klass = _KNOWN_CLASSES.get(code, "ESCALATE")\n'
            '        if klass == "CLIENT_FAULT":\n'
            "            raise AtlasClientError(code)\n"
            '        if klass == "ESCALATE":\n'
            "            raise AtlasEscalationError(code)\n"
            '        if klass == "DEGRADED":\n'
            "            return self._degraded(op, payload, code, fallback)\n"
            "        return self._transient(op, payload, code)\n"
            "\n"
            "    def _transient(self, op, payload, code):\n"
            "        # attempt 1 already happened in call(); this is the retry budget\n"
            "        for failed in range(1, _TRANSIENT_ATTEMPTS):\n"
            "            wait = failed * failed\n"
            '            self._emit(f"[atlas] retry code={code} attempt={failed} wait={wait}s")\n'
            "            self._sleep(wait)\n"
            "            response = self._transport.request(op, payload)\n"
            '            if response.startswith("OK "):\n'
            '                return response[len("OK ") :]\n'
            "        raise AtlasTransientError(code)\n"
            "\n"
            "    def _degraded(self, op, payload, code, fallback):\n"
            "        self._emit(\n"
            '            f"[atlas] retry code={code} attempt=1 wait={_DEGRADED_WAIT_SECONDS}s"\n'
            "        )\n"
            "        self._sleep(_DEGRADED_WAIT_SECONDS)\n"
            "        response = self._transport.request(op, payload)\n"
            '        if response.startswith("OK "):\n'
            '            return response[len("OK ") :]\n'
            "        if fallback is None:\n"
            "            raise AtlasDegradedError(code)\n"
            '        self._emit(f"[atlas] degraded code={code} fallback={fallback!r}")\n'
            "        return fallback\n"
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
    # The Meridian Release Gate Standard: the six violation codes in rule
    # order, the skip rules (malformed version skips parity; unknown
    # channel skips parity + approvers), the parity direction, the
    # promotion prefix, the per-channel approver minimums and the strict
    # migration ordering.
    "private-meridian-release": [
        (
            "release_gate.py",
            None,
            '"""Meridian release gate - validate a release config before promotion.\n'
            "\n"
            "Implements the Meridian Release Gate Standard (internal): the\n"
            "violation codes, their evaluation order and their skip rules.\n"
            '"""\n'
            "\n"
            '_ENV_ORDER = ["dev", "staging", "prod"]\n'
            '_CHANNELS = ("stable", "rapid", "lts")\n'
            '_APPROVERS_NEEDED = {"stable": 2, "rapid": 1, "lts": 3}\n'
            "\n"
            "\n"
            "def _version_parts(version):\n"
            '    parts = str(version).split(".")\n'
            "    if len(parts) != 3:\n"
            "        return None\n"
            "    for part in parts:\n"
            "        if not part.isdigit():\n"
            "            return None\n"
            '        if len(part) > 1 and part.startswith("0"):\n'
            "            return None\n"
            "    return parts\n"
            "\n"
            "\n"
            "def _is_promotion_prefix(environments):\n"
            "    return list(environments) == _ENV_ORDER[: len(environments)]\n"
            "\n"
            "\n"
            "def _migrations_ascending(migrations):\n"
            "    previous = None\n"
            "    for migration in migrations:\n"
            "        text = str(migration)\n"
            '        if len(text) != 5 or not text.startswith("M") or not text[1:].isdigit():\n'
            "            return False\n"
            "        value = int(text[1:])\n"
            "        if previous is not None and value <= previous:\n"
            "            return False\n"
            "        previous = value\n"
            "    return True\n"
            "\n"
            "\n"
            "def check_release(config):\n"
            '    """Return the violation codes for a release config (empty = compliant)."""\n'
            "    violations = []\n"
            '    parts = _version_parts(config.get("version", ""))\n'
            "    if parts is None:\n"
            '        violations.append("version-scheme")\n'
            '    channel = config.get("channel")\n'
            "    known_channel = channel in _CHANNELS\n"
            "    if not known_channel:\n"
            '        violations.append("channel-unknown")\n'
            "    if parts is not None and known_channel:\n"
            "        minor = int(parts[1])\n"
            '        if channel in ("stable", "lts"):\n'
            "            if minor % 2 != 0:\n"
            '                violations.append("minor-parity")\n'
            '        elif channel == "rapid":\n'
            "            if minor % 2 != 1:\n"
            '                violations.append("minor-parity")\n'
            '    if not _is_promotion_prefix(list(config.get("environments", []))):\n'
            '        violations.append("promotion-order")\n'
            "    if known_channel:\n"
            "        needed = _APPROVERS_NEEDED[channel]\n"
            '        if len(set(config.get("approvers", []))) < needed:\n'
            '            violations.append("approver-count")\n'
            '    if not _migrations_ascending(list(config.get("migrations", []))):\n'
            '        violations.append("migration-order")\n'
            "    return violations\n",
        ),
    ],
}


def materialize(task: dict[str, Any], root: Path) -> Path:
    """Write a fixture's shipped files into a fresh directory."""
    task_dir = root / task["name"]
    task_dir.mkdir(parents=True)
    for rel, content in task["files"].items():
        target = task_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return task_dir


def apply_fix(task_dir: Path, steps: list[tuple[str, str | None, str]]) -> None:
    """Apply the known root-cause fix: (file, None, content) replaces the
    whole file; (file, old, new) splices like the multi verifier."""
    for fix_file, old, new in steps:
        target = task_dir / fix_file
        if old is None:
            target.write_text(new, encoding="utf-8")
            continue
        text = target.read_text(encoding="utf-8")
        if old not in text:
            raise ValueError(f"fix anchor not found in {fix_file}")
        target.write_text(text.replace(old, new), encoding="utf-8")


def run_pytest(task_dir: Path, python: str | None = None) -> tuple[int, str]:
    # PYTHONDONTWRITEBYTECODE: the buggy-state run would leave __pycache__
    # behind; a same-size same-mtime-second fix write then reuses the stale
    # .pyc and the FIXED-state run silently executes the buggy code.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [python or PYTEST, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=task_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def verify_all(python: str | None = None) -> int:
    """The --set private verification (the default, unchanged): the 2 E2/E2B
    fixtures against PRIVATE_FIXES. Reads the module-level names at CALL
    time (tests monkeypatch them)."""
    return verify_fixtures(PRIVATE_TASKS, PRIVATE_FIXES, python=python)


def verify_fixtures(
    tasks: list[dict[str, Any]],
    fixes: dict[str, list[tuple[str, str | None, str]]],
    *,
    python: str | None = None,
) -> int:
    """Fail-as-shipped / pass-when-fixed for an arbitrary private fixture
    set (verify_all's core, parameterized for --set private2/all)."""
    failures: list[str] = []
    for task in tasks:
        name = task["name"]
        fix = fixes.get(name)
        if fix is None:
            failures.append(f"{name}: no fix defined")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = materialize(task, Path(tmp))
            code, out = run_pytest(task_dir, python)
            if code == 0:
                failures.append(f"{name}: FAILING-state passes (fixture is not buggy)")
                continue
            apply_fix(task_dir, fix)
            code, out = run_pytest(task_dir, python)
            if code != 0:
                failures.append(f"{name}: FIXED-state still fails:\n{out[-400:]}")
        status = "OK" if not any(f.startswith(name) for f in failures) else "BAD"
        print(f"{status}  {name}")

    if failures:
        print("\nFIXTURE PROBLEMS:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        f"\nAll {len(tasks)} private fixtures of this set verified: "
        "failing-when-buggy, passing-when-fixed."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--set",
        default="private",
        choices=["private", "private2", "private3", "all"],
        help=(
            "which private fixture set to verify: 'private' = the 2 E2/E2B "
            "fixtures (default), 'private2' = the 7 E2C fixtures, 'private3' = the "
            "40 dense-corpus fixtures, 'all' = private + private2"
        ),
    )
    args = parser.parse_args(argv)
    if args.set == "private":
        return verify_all()
    if args.set == "private2":
        return verify_fixtures(PRIVATE2_TASKS, PRIVATE2_FIXES)
    if args.set == "private3":
        return verify_fixtures(PRIVATE3_TASKS, PRIVATE3_FIXES)
    code = verify_all()
    return code if code != 0 else verify_fixtures(PRIVATE2_TASKS, PRIVATE2_FIXES)


if __name__ == "__main__":
    raise SystemExit(main())
