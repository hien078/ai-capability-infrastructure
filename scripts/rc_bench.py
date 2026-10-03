"""Real-client bench (rc-bench): OpenCode and Goose, each WITH and WITHOUT ACI,
on the hard (indirect-prompt) private2 fixtures — run on the Linux host.

Arms (same model ``OneNexus/glm-5.3`` through the local gateway, same prompt):

* ``OC-N`` — OpenCode, no ACI (no plugin, no skills catalog).
* ``OC-A`` — OpenCode + the ACI routing plugin (``.opencode/plugins``) + the
  ACI skills catalog: every prompt is routed (``POST /v1/routes``) and the
  selected skill ids are injected; OpenCode lazy-loads the SKILL.md.
* ``G-N``  — Goose, developer extension only.
* ``G-A``  — Goose + the ACI MCP server (streamable HTTP): the model is
  OFFERED ``route_capabilities`` / skill resources and must call them itself.

Isolation (ADR-014 amendment 29 lesson — unsandboxed clients read siblings'
answers): every client process runs in bubblewrap with ``$HOME`` and ``/tmp``
replaced by empty tmpfs, the docker socket masked, and ONLY its own run root
(workdir + per-run XDG config/data/cache) bound back, plus the client
binaries read-only. The repo, object store, transcripts, other runs and the
ACI servers' files are unreachable by path. The ACI servers (REST :8010,
MCP :8011, both on the disposable ``aci_e2b`` copy) run outside the sandbox;
the network is shared (the gateway lives on localhost) — transcripts are
scanned for any attempt to reach those ports, the DB, or paths outside the
run root.

Usage::

    .venv/bin/python scripts/rc_bench.py --smoke OC-A      # one wiring check
    .venv/bin/python scripts/rc_bench.py --repeat 2 --parallel 4 --out data/rc-bench/round.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from private2_tasks import PRIVATE2_TASKS  # noqa: E402
from private3_tasks import PRIVATE3_TASKS  # noqa: E402

HOME = Path.home()
RC = HOME / ".cache/aci-rc"
RUNS = RC / "runs"
OC_TEMPLATE = RC / "oc-template"
OPENCODE = HOME / ".opencode/bin/opencode"
GOOSE = HOME / ".local/bin/goose"
BUN = HOME / ".bun/bin/bun"
#: The user's site-packages (pytest lives there) — read-only in every arm.
SITE = HOME / ".local/lib/python3.14/site-packages"
PLUGIN_SRC = ROOT / "src/aci/adapters/inbound/opencode/plugin/index.ts"
REST = "http://127.0.0.1:8010"
MCP = "http://127.0.0.1:8011/mcp"
MODEL = "OneNexus/glm-5.3"
ARMS = ("OC-N", "OC-A", "OC-S", "G-N", "G-A")
#: Default arms (user, 2026-10-02: Goose dropped; native-skills arm added).
DEFAULT_ARMS = ("OC-N", "OC-A", "OC-S")
#: The SAME 45 skills the ACI catalog serves, exported as native OpenCode
#: skills (<name>/SKILL.md) — arm OC-S: OpenCode's own skill mechanism, no ACI.
NATIVE_SKILLS = RC / "native-skills"
#: The native-skills tree arm OC-S copies (--native-dir; default the 45-skill one).
NATIVE_DIR = NATIVE_SKILLS
#: Fixture sets: private2 = the 4 indirect E2C fixtures (round 3);
#: private3 = the 40 dense-corpus fixtures (rc-bench v2).
SETS: dict[str, list[dict[str, Any]]] = {"private2": PRIVATE2_TASKS, "private3": PRIVATE3_TASKS}
FIXTURES = (
    "private2-drawbridge-rollout",
    "private2-cairn-sunset",
    "private2-queue-consumer-retry",
    "private2-infra-config",
)
WALL_CAP = 900
#: Transcript strings that mean the model reached for the answer outside its
#: own run (servers, DB, docker, the repo/object store, sibling runs).
FORBIDDEN = (
    ":8010",
    ":8011",
    "5432",
    "psql",
    "docker exec",
    "docker ps",
    "docker run",
    "aci-rc/objects",
    "aci-rc/runs",
    "Data/Projects",
    ".snapshots/",
    "private2_tasks",
    "aci_e2b",
)


def _gateway() -> tuple[str, str]:
    cfg = json.loads(
        re.sub(r"(?m)^\s*//.*$", "", (HOME / ".config/opencode/opencode.json").read_text())
    )
    opts = cfg["provider"]["local-gateway"]["options"]
    return str(opts["baseURL"]), str(opts["apiKey"])


def ensure_oc_template() -> None:
    """The .opencode project template: plugin + deps + the ACI skills catalog."""
    plugins = OC_TEMPLATE / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PLUGIN_SRC, plugins / "aci-router.ts")
    (OC_TEMPLATE / "package.json").write_text(
        json.dumps({"dependencies": {"@opencode/plugin": "2.0.18", "@opencode/schema": "2.0.18"}})
    )
    if not (OC_TEMPLATE / "node_modules/@opencode/plugin").exists():
        subprocess.run([str(BUN), "install"], cwd=OC_TEMPLATE, check=True, capture_output=True)


def _materialize(fixture: dict[str, Any], work: Path) -> dict[str, str]:
    for rel, content in fixture["files"].items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return {
        rel: hashlib.sha256(content.encode()).hexdigest()
        for rel, content in fixture["files"].items()
        if Path(rel).name.startswith("test_")
    }


def _write_client_config(arm: str, root: Path, base_url: str, api_key: str) -> None:
    cfg = root / "xdg-config"
    if arm.startswith("OC"):
        (cfg / "opencode").mkdir(parents=True)
        (cfg / "opencode/opencode.json").write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "model": f"local-gateway/{MODEL}",
                    "provider": {
                        "local-gateway": {
                            "npm": "@ai-sdk/openai-compatible",
                            "options": {"baseURL": base_url, "apiKey": api_key},
                            "models": {MODEL: {"tool_call": True}},
                        }
                    },
                }
            )
        )
        if arm == "OC-S":
            shutil.copytree(NATIVE_DIR, cfg / "opencode/skills")
        if arm == "OC-A":
            work = root / "work"
            shutil.copytree(OC_TEMPLATE, work / ".opencode")
            (work / "opencode.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://opencode.ai/config.json",
                        "skills": [f"{REST}/opencode/skills/"],
                    }
                )
            )
    else:
        (cfg / "goose").mkdir(parents=True)
        g = yaml.safe_load((HOME / ".config/goose/config.yaml").read_text())
        (cfg / "goose/config.yaml").write_text(
            yaml.safe_dump(
                {
                    "GOOSE_PROVIDER": "openai",
                    "GOOSE_MODEL": MODEL,
                    "GOOSE_MODE": "auto",
                    "OPENAI_HOST": g.get("OPENAI_HOST"),
                    "OPENAI_BASE_PATH": g.get("OPENAI_BASE_PATH"),
                    "extensions": {},
                }
            )
        )


def _bwrap(root: Path, extra_ro: list[Path]) -> list[str]:
    cmd = [
        "bwrap",
        "--dev-bind", "/", "/",
        # ALL of /home (not just $HOME) and the btrfs snapshot trees: the
        # first round showed `find /` reaching the repo through
        # /home/.snapshots/<n>/snapshot/hien/Data/Projects/... .
        "--tmpfs", "/home",
        "--tmpfs", "/.snapshots",
        "--tmpfs", "/tmp",
        "--tmpfs", "/var/tmp",
        "--ro-bind", "/dev/null", "/run/docker.sock",
        # Own PID namespace + procfs: no other process (sibling runs, the
        # ACI servers) is visible, so /proc/<pid>/{cwd,root,environ} cannot
        # bypass the tmpfs masks (seen in the G-A smoke: the model ran ps).
        "--unshare-pid",
        "--proc", "/proc",
        "--bind", str(root), str(root),
        "--die-with-parent",
    ]  # fmt: skip
    for path in extra_ro:
        cmd += ["--ro-bind", str(path), str(path)]
    return cmd


def run_one(fixture: dict[str, Any], arm: str, repeat: int, out_dir: Path) -> dict[str, Any]:
    base_url, api_key = _gateway()
    root = RUNS / f"{secrets.token_hex(6)}"
    work = root / "work"
    work.mkdir(parents=True)
    for sub in ("xdg-data", "xdg-cache", "xdg-state"):
        (root / sub).mkdir()
    test_digests = _materialize(fixture, work)
    _write_client_config(arm, root, base_url, api_key)
    env = {
        "PATH": f"{HOME}/.opencode/bin:{HOME}/.bun/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(HOME),
        "PWD": str(work),
        "TERM": "dumb",
        "LANG": "C.UTF-8",
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
    }
    prompt = str(fixture["prompt"])
    if arm.startswith("OC"):
        ro = [HOME / ".opencode", HOME / ".bun", SITE]
        env["ACI_ROUTER_BASE_URL"] = REST
        env["ACI_ROUTER_PRINCIPAL"] = "rc-bench"
        if arm != "OC-A":
            env["ACI_ROUTER_DISABLED"] = "1"
        client = [str(OPENCODE), "run", "--standalone", "--auto", "--format=json", prompt]
    else:
        ro = [GOOSE, SITE]
        env["OPENAI_API_KEY"] = api_key
        client = [str(GOOSE), "run", "--no-session", "--no-profile", "--with-builtin", "developer"]
        if arm == "G-A":
            client += ["--with-streamable-http-extension", MCP]
        client += ["--max-turns", "40", "--output-format", "json", "-t", prompt]
    rest_log = RC / "logs/rest-8010.log"
    log_offset = rest_log.stat().st_size if rest_log.exists() else 0
    transcript = out_dir / f"{fixture['name']}.{arm}.{repeat}.{root.name}.log"
    err_path = transcript.with_suffix(".stderr")
    started = time.time()
    timed_out = False
    # Stream to files: a timed-out run keeps its full transcript (captured
    # pipes lost it, which blinded the contamination scan on every timeout).
    with transcript.open("w") as out_fh, err_path.open("w") as err_fh:
        try:
            proc = subprocess.run(
                _bwrap(root, ro) + ["--chdir", str(work), "--"] + client,
                env=env,
                stdout=out_fh,
                stderr=err_fh,
                timeout=WALL_CAP,
                check=False,
            )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = -9
    wall = round(time.time() - started, 1)
    stdout = transcript.read_text(errors="replace")
    stderr = err_path.read_text(errors="replace")

    tampered = [
        rel
        for rel, digest in test_digests.items()
        if not (work / rel).exists()
        or hashlib.sha256((work / rel).read_bytes()).hexdigest() != digest
    ]
    post = subprocess.run(
        ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    sources = "\n".join(
        p.read_text(errors="replace")
        for p in work.rglob("*.py")
        if ".opencode" not in p.parts and not p.name.startswith("test_")
    )
    text = (stdout + stderr).replace(str(root), "<RUN>")
    allowed = {"OC-A": {":8010"}, "G-A": {":8011"}}.get(arm, set())
    hits = sorted({f for f in FORBIDDEN if f in text and f not in allowed})
    # OC-A delivery evidence: catalog fetches of THIS fixture's skill on the
    # ACI server during the run (arms interleave fixtures, so skill ids
    # attribute the fetch; OpenCode loads injected skills server-side, they
    # never appear in the transcript).
    with rest_log.open(errors="replace") as fh:
        fh.seek(log_offset)
        served = fh.read()
    catalog_fetches = served.count(f"/opencode/skills/{fixture['skill_id']}/")
    model_failure = (not timed_out) and _is_model_failure(arm, text, wall)
    return {
        "arm": arm,
        "fixture": fixture["name"],
        "skill_id": fixture["skill_id"],
        "repeat": repeat,
        "run_root": str(root),
        "transcript": str(transcript),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "wall_seconds": wall,
        "tests_pass_at_end": post.returncode == 0 and not tampered,
        "post_hoc_tail": post.stdout[-400:],
        "tampered": tampered,
        "markers_in_sources": sum(1 for m in fixture.get("markers", ()) if m in sources),
        "markers_total": len(fixture.get("markers", ())),
        "contamination_hits": hits,
        "skill_in_transcript": str(fixture["skill_id"]) in text,
        "catalog_skill_fetches": catalog_fetches,
        "aci_tool_calls": len(re.findall(r"route_capabilities|search_capabilities|skill://", text)),
        "model_failure": model_failure,
        "started_at": started,
    }


def _is_model_failure(arm: str, text: str, wall: float) -> bool:
    """A run that died on the provider before doing work (gateway 5xx /
    transport error, under a minute) is INVALID, not a result."""
    if wall > 60:
        return False
    return bool(
        re.search(r"provider\.transport|\b5\d\d\b.*(error|Error)|APIError|ECONNREFUSED", text)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    parser.add_argument("--set", choices=sorted(SETS), default="private2")
    parser.add_argument("--fixtures", default=None, help="comma list (default: set default)")
    parser.add_argument("--native-dir", type=Path, default=NATIVE_SKILLS)
    parser.add_argument(
        "--arm-repeats", default="", help="per-arm repeat override, e.g. OC-N=1,OC-A=2"
    )
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--out", default="data/rc-bench/round.json")
    parser.add_argument("--smoke", choices=ARMS)
    args = parser.parse_args(argv)
    global NATIVE_DIR
    NATIVE_DIR = args.native_dir.expanduser()
    ensure_oc_template()
    out = Path(args.out)
    out_dir = out.with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    pack = SETS[args.set]
    by_name = {f["name"]: f for f in pack}
    if args.fixtures:
        names = args.fixtures.split(",")
    elif args.set == "private2":
        names = list(FIXTURES)
    else:
        names = [str(f["name"]) for f in pack]
    fixtures = [by_name[n] for n in names]
    per_arm = {
        k: int(v) for k, v in (item.split("=") for item in args.arm_repeats.split(",") if item)
    }
    if args.smoke:
        jobs = [(fixtures[0], args.smoke, 0)]
    else:
        arms = args.arms.split(",")
        # Interleave arms so each arm sees the same gateway load over time.
        jobs = [
            (f, a, r)
            for r in range(args.repeat)
            for f in fixtures
            for a in arms
            if r < per_arm.get(a, args.repeat)
        ]
    rows: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(run_one, f, a, r, out_dir): (f["name"], a, r) for f, a, r in jobs}
        for fut in concurrent.futures.as_completed(futures):
            name, arm, rep = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:  # noqa: BLE001 — a crashed run is recorded, not fatal
                row = {"arm": arm, "fixture": name, "repeat": rep, "crashed": repr(exc)}
            rows.append(row)
            print(
                f"[{name} {arm} #{rep}] pass={row.get('tests_pass_at_end')} "
                f"wall={row.get('wall_seconds')}s mf={row.get('model_failure')} "
                f"timeout={row.get('timed_out')} skill={row.get('skill_in_transcript')} "
                f"aci_calls={row.get('aci_tool_calls')} hits={row.get('contamination_hits')} "
                f"tampered={row.get('tampered')}",
                flush=True,
            )
            out.write_text(json.dumps({"model": MODEL, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
