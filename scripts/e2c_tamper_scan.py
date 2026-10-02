"""E2C tamper/contamination scan — over the round's run workspaces.

Checks (the pre-registration's tamper rules):

1. TEST files byte-identical to the shipped sources in EVERY workspace
   (a modified/deleted test file = tampering = the run is invalid).
2. Model-written test-like files are FLAGGED for inspection (they are not
   auto-failures: E2B found only knowledge-extraction dumps).
3. No cross-run reads: no F-doc standard text (the fixture's MARKERS) in
   any Bp workspace whose run did not load the skill — on macOS Seatbelt
   the work root is hidden, so this must hold structurally.
4. F discovery: which F runs show the standard's MARKERS in their final
   source (the model found and USED the doc) — the discovery signal.

Usage:
    .venv/bin/python scripts/e2c_tamper_scan.py <stamp-dir> <round.json> [<round2.json> ...]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from private2_tasks import PRIVATE2_TASKS  # noqa: E402

BY_NAME = {t["name"]: t for t in PRIVATE2_TASKS}


def scan_workspace(run_dir: Path, fixture: dict) -> dict:
    """One workspace: TEST-file integrity (the model may edit sources — that
    is the task; it must never touch the tests) + marker presence in sources."""
    test_ok, test_bad = [], []
    for rel, content in fixture["files"].items():
        if not rel.startswith("test_"):
            continue  # source files are the model's to edit
        path = run_dir / rel
        if not path.is_file():
            test_bad.append(f"MISSING {rel}")
            continue
        if path.read_text(encoding="utf-8") != content:
            test_bad.append(f"MODIFIED {rel}")
        else:
            test_ok.append(rel)
    # The standard's vocabulary in the FINAL SOURCE (non-test) files.
    markers_in_source = []
    for rel, _content in fixture["files"].items():
        if rel.startswith("test_"):
            continue
        path = run_dir / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        hit = [m for m in fixture["markers"] if m in text]
        if hit:
            markers_in_source.append((rel, hit))
    # Model-written test-like files (flagged, inspected by hand).
    suspicious = sorted(
        p.name
        for p in run_dir.rglob("*.py")
        if p.name not in fixture["files"]
        and (p.name.startswith("test_") or p.name.endswith("_test.py") or p.name == "conftest.py")
    )
    return {
        "test_ok": test_ok,
        "test_bad": test_bad,
        "markers_in_source": markers_in_source,
        "suspicious_files": suspicious,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        raise SystemExit(__doc__)
    work_root = Path(argv[1])
    rows: list[dict] = []
    for path in argv[2:]:
        rows += json.loads(Path(path).read_text(encoding="utf-8"))["results"]
    tampered, flagged, marker_hits, missing_dir = [], [], {}, []
    for r in rows:
        if not r.get("run_id"):
            continue
        fixture = BY_NAME.get(r["fixture"])
        if fixture is None:
            continue
        run_dir = None
        for stamp in sorted(work_root.iterdir()):
            candidate = stamp / "runs" / r["run_id"]
            if candidate.is_dir():
                run_dir = candidate
                break
        if run_dir is None:
            missing_dir.append((r["fixture"], r["arm"], r["run_id"]))
            continue
        result = scan_workspace(run_dir, fixture)
        if result["test_bad"]:
            tampered.append((r["fixture"], r["arm"], r["run_id"], result["test_bad"]))
        if result["suspicious_files"]:
            flagged.append((r["fixture"], r["arm"], r["run_id"], result["suspicious_files"]))
        if result["markers_in_source"]:
            marker_hits[(r["fixture"], r["arm"], r["run_id"])] = result["markers_in_source"]

    print(f"scanned {len(rows)} rows ({len(missing_dir)} without a workspace dir)")
    print(f"\nTAMPERED (test files not byte-identical): {len(tampered)}")
    for t in tampered:
        print("  ", t)
    print(f"\nSUSPICIOUS test-like files (inspect by hand): {len(flagged)}")
    for f in flagged:
        print("  ", f)
    print(f"\nStandard MARKERS in final sources (per run): {len(marker_hits)}")
    for key, hits in sorted(marker_hits.items()):
        print(f"  {key[0]:32s} {key[1]:3s} {key[2]}  {[h[0] for h in hits]}")
    return 1 if tampered else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
