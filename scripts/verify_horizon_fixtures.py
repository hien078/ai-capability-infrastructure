"""Verify §80 horizon fixtures before any agent run (§34 discipline).

Stronger than the multi pre-verification — each fixture must satisfy ALL
of:

1. FAILING in its shipped (buggy) state;
2. PASSING after the known root-cause fix (never a test patch);
3. STILL FAILING after the OBVIOUS-BUT-WRONG fix at the symptom's
   location — that pin is what makes the fixture hard: patching the
   symptom cannot pass the suite, only the root fix can.

Usage:
    .venv/bin/python scripts/verify_horizon_fixtures.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from horizon_tasks import HORIZON_TASKS  # noqa: E402

PYTEST = str(Path(__file__).resolve().parent.parent / ".venv/bin/python")

# Minimal root-cause fixes: (file, old, new) applied in order.
FIXTURE_FIXES: dict[str, Any] = {
    "horizon-ledger-reversal": (
        "ledger/posting.py",
        "    entry = Entry(account, abs(amount_cents), kind)",
        "    entry = Entry(account, amount_cents, kind)",
    ),
    "horizon-scheduler-order": (
        "scheduler/queue.py",
        "        self._jobs.insert(0, job)",
        "        self._jobs.append(job)",
    ),
    "horizon-catalog-order": [
        (
            "catalog/sort.py",
            '    if by == "name":\n'
            "        items.sort(key=lambda i: (i.name, i.id))\n"
            '    elif by == "price":\n'
            "        items.sort(key=lambda i: (i.price_cents, i.id))\n"
            "    else:\n"
            '        raise ValueError(f"unknown sort key: {by}")\n'
            "    return items",
            '    if by == "name":\n'
            "        return sorted(items, key=lambda i: (i.name, i.id))\n"
            '    if by == "price":\n'
            "        return sorted(items, key=lambda i: (i.price_cents, i.id))\n"
            '    raise ValueError(f"unknown sort key: {by}")',
        ),
        (
            "catalog/query.py",
            "    catalog[:] = [i for i in catalog if needle.lower() in i.name.lower()]\n"
            '    return sort_items(catalog, by="name")',
            "    matched = [i for i in catalog if needle.lower() in i.name.lower()]\n"
            '    return sort_items(matched, by="name")',
        ),
    ],
    "horizon-settings-migration": [
        (
            "settings/migrate.py",
            '        "timeout": doc.get("timeout", 30),\n    }',
            '        "timeout": doc.get("timeout", 30),\n'
            '        "labels": dict(doc.get("labels", {})),\n    }',
        ),
        (
            "settings/env.py",
            "        if raw is not None:\n            out[key] = int(raw)",
            "        if raw:\n            out[key] = int(raw)",
        ),
    ],
    "horizon-cache-tz": (
        "cache/codec.py",
        '            return datetime.fromisoformat(v["__dt__"]).replace(tzinfo=None)',
        '            return datetime.fromisoformat(v["__dt__"])',
    ),
}

# The tempting symptom-location patch: applying ONLY this must leave the
# suite red (the pinning assertion still fails) — otherwise the fixture
# does not force root-cause work.
WRONG_FIXES: dict[str, Any] = {
    # patch the report instead of posting — the sign is already destroyed
    "horizon-ledger-reversal": (
        "ledger/report.py",
        "    return sum(e.signed_cents() for e in journal.entries_for(account))",
        "    total = sum(e.signed_cents() for e in journal.entries_for(account))\n"
        "    return total + 2 * sum(\n"
        "        e.amount_cents for e in journal.entries_for(account) if e.amount_cents < 0\n"
        "    )",
    ),
    # pop from the other end instead of fixing add()
    "horizon-scheduler-order": (
        "scheduler/queue.py",
        "        return self._jobs.pop(0)",
        "        return self._jobs.pop()",
    ),
    # re-sort in render instead of fixing sort_items/search
    "horizon-catalog-order": (
        "catalog/report.py",
        '    return "\\n".join(f"{i.id}: {i.name} ({i.price_cents / 100:.2f})" for i in catalog)',
        "    return render(sorted(catalog, key=lambda i: i.id))",
    ),
    # default labels in the loader instead of carrying them in migrate()
    "horizon-settings-migration": (
        "settings/loader.py",
        "    migrated = migrate(doc)",
        "    migrated = migrate(doc)\n    migrated.setdefault('labels', {})",
    ),
    # strip tz from the clock side instead of keeping it in the codec
    "horizon-cache-tz": (
        "cache/ttl.py",
        "    age = (now - stored_at).total_seconds()",
        "    age = (now.replace(tzinfo=None) - stored_at).total_seconds()",
    ),
}


def run_pytest(task_dir: Path) -> tuple[int, str]:
    # PYTHONDONTWRITEBYTECODE: the buggy-state run would leave __pycache__
    # in the re-materialized fixture tree, and a same-size same-mtime-second
    # fix write makes CPython reuse the stale .pyc — the FIXED-state run would
    # silently execute the buggy code (the §80 bytecode-cache trap). Same
    # guard as verify_multi/private/domain_fixtures.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [PYTEST, "-m", "pytest", "-q"],
        cwd=task_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def materialize(task: dict[str, Any], root: Path) -> Path:
    task_dir = root / task["name"]
    task_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in task["files"].items():
        target = task_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return task_dir


def apply(task_dir: Path, steps: Any) -> None:
    if isinstance(steps, tuple):
        steps = [steps]
    for fix_file, old, new in steps:
        target = task_dir / fix_file
        text = target.read_text(encoding="utf-8")
        assert old in text, f"anchor not found in {fix_file}"
        target.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    for task in HORIZON_TASKS:
        name = task["name"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # 1. shipped state must FAIL
            task_dir = materialize(task, root)
            code, _ = run_pytest(task_dir)
            if code == 0:
                failures.append(f"{name}: FAILING-state passes (fixture is not buggy)")

            # 2. root-cause fix must make it PASS
            task_dir = materialize(task, root)
            apply(task_dir, FIXTURE_FIXES[name])
            code, out = run_pytest(task_dir)
            if code != 0:
                failures.append(f"{name}: FIXED-state still fails:\n{out[-400:]}")

            # 3. the symptom-location patch must NOT make it pass
            task_dir = materialize(task, root)
            apply(task_dir, WRONG_FIXES[name])
            code, _ = run_pytest(task_dir)
            if code == 0:
                failures.append(f"{name}: WRONG-fix passes (pin is broken)")

        status = "OK" if not any(f.startswith(name) for f in failures) else "BAD"
        print(f"{status}  {name}")

    if failures:
        print("\nFIXTURE PROBLEMS:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        "\nAll horizon fixtures verified: failing-when-buggy, "
        "passing-when-root-fixed, red-when-symptom-patched."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
