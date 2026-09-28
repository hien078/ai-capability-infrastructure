"""Verify §80 multi-file fixtures before any agent run (§34 discipline).

Each fixture must be FAILING in its shipped (buggy) state AND PASSING after
the known root-cause fix is applied — otherwise the acceptance signal is
meaningless. The fix applied here is the minimal root-cause fix (what the
task author verified by hand), not a patch of the tests.

Usage:
    .venv/bin/python scripts/verify_multi_fixtures.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from proof_loop import LONG_TASKS, MULTI_TASKS  # noqa: E402

PYTEST = str(Path(__file__).resolve().parent.parent / ".venv/bin/python")

# Minimal root-cause fixes, one per task: (file, old, new) — applied in order.
FIXES = {
    "multi-event-aliasing": [
        ("bus.py", "from events import make_event", "import copy\n\nfrom events import make_event"),
        (
            "bus.py",
            "            fn(event)",
            "            fn(copy.deepcopy(event))",
        ),
    ],
    "multi-config-precedence": (
        "config.py",
        "    config = DEFAULTS\n    config.update(overrides)",
        "    config = {**DEFAULTS, **overrides}",
    ),
    "multi-pipeline-ordering": (
        "pipeline.py",
        "return [r.raw for r in records if not validate(r)]",
        "return [r.raw for r in records if validate(r)]",
    ),
    "multi-cache-invalidation": (
        "cache.py",
        "    def put(self, key: str, value: str) -> None:\n        self._store.put(key, value)",
        "    def put(self, key: str, value: str) -> None:\n"
        "        self._store.put(key, value)\n"
        "        self._cache.pop(key, None)",
    ),
    "multi-error-translation": (
        "service.py",
        "    except Exception as exc:",
        "    except NotFound as exc:",
    ),
    # Long-horizon fixtures: minimal root-cause fixes (inverse of the
    # deliberately-installed bug in each fixture).
    "long-order-pipeline": (
        "pricing/rules.py",
        "if i.qty > 10",
        "if i.qty >= 10",
    ),
    "long-auth-session": (
        "auth/sessions.py",
        "if e[0] == email",
        "if e[0] != email",
    ),
    "long-notify-fanout": (
        "notify.py",
        "    def send(self, channel: str, payload: dict) -> list[str]:\n"
        "        return self.broker.publish(channel, payload)",
        "    def send(self, channel: str, payload: dict) -> list[str]:\n"
        "        if not self.channels.enabled(channel):\n"
        "            return []\n"
        "        return self.broker.publish(channel, payload)",
    ),
}


def run_pytest(task_dir: Path) -> tuple[int, str]:
    # PYTHONDONTWRITEBYTECODE: the buggy-state run would leave __pycache__
    # behind; a same-size same-mtime-second fix write then reuses the stale
    # .pyc and the FIXED-state run silently executes the buggy code.
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


def main() -> int:
    failures: list[str] = []
    for task in [*MULTI_TASKS, *LONG_TASKS]:
        name = task["name"]
        fix = FIXES.get(name)
        if fix is None:
            failures.append(f"{name}: no fix defined")
            continue
        steps = fix if isinstance(fix, list) else [fix]
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / name
            task_dir.mkdir()
            for rel, content in task["files"].items():
                target = task_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            code, out = run_pytest(task_dir)
            if code == 0:
                failures.append(f"{name}: FAILING-state passes (fixture is not buggy)")
                continue

            for fix_file, old, new in steps:
                target = task_dir / fix_file
                text = target.read_text(encoding="utf-8")
                if old not in text:
                    failures.append(f"{name}: fix anchor not found in {fix_file}")
                    break
                target.write_text(text.replace(old, new), encoding="utf-8")
            else:
                code, out = run_pytest(task_dir)
                if code != 0:
                    failures.append(f"{name}: FIXED-state still fails:\n{out[-400:]}")

        status = "OK" if not any(f.startswith(name) for f in failures) else "BAD"
        print(f"{status}  {name}")

    if failures:
        print("\nFIXTURE PROBLEMS:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll 5 multi fixtures verified: failing-when-buggy, passing-when-fixed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
