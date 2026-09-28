"""§80 delegation probe: does a profile-driven agent add value over raw model?

The 7 proof-loop rounds (n=33) showed no single-bug acceptance delta between
a naked client and a client + routed skills — every fixture was solvable by
every model tried. The recorded verdict: at this model tier, value must live
in process quality (orchestration, long-horizon discipline), not single-bug
acceptance. V3 gives that hypothesis its first testable surface: a
profile-driven delegated agent (AgentProfile + SkillPolicy + executor) that
answers a task in ONE shot with granted skills pinned in its prompt.

This probe measures the DELTA between three arms on tasks where the answer
is checkable deterministically:

  A — raw model: plain chat completion, no system prompt, no skills.
  B — profile agent, NO skills: same model through the V3 runtime with the
      aci-coder profile (execution policy, budget) but an empty skill policy.
  C — profile agent + granted skills: the full V3 chain — SkillPolicy
      required skills resolved against active production releases, bodies
      loaded from the object store with content re-verification (§39).

Arms B and C isolate exactly what the granted skills contribute over the
platform's orchestration alone; arm A anchors the raw-model baseline.

Tasks: multi-step REASONING questions with a deterministic rubric (the model
must produce an answer checkable by string/structure, not run code) — the
regime a single-shot delegated agent actually serves (§32: delegated-autonomy,
terminal state, no shared session).

Usage:
    ACI_DATABASE_URL=... .venv/bin/python scripts/delegation_probe.py \
        [--base-url http://localhost:20128/v1] [--model OneNexus/glm-5.3]

Requires the live DB (agent + skill corpus) and an OpenAI-compatible model
endpoint. Writes a JSON report to data/delegation-probe/.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aci.adapters.outbound.model_provider.executor import OpenAICompatExecutor
from aci.adapters.outbound.object_store.fs import FsObjectStore
from aci.adapters.outbound.postgres.base import make_engine, make_session_factory
from aci.adapters.outbound.postgres.repositories import (
    SqlAlchemyArtifactStore,
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.tasks import SqlAlchemyTaskRepository
from aci.application.delegate_task import ProfileDrivenAgentRuntime
from aci.config import Settings
from aci.domain.agent.models import (
    AgentProfile,
    AgentTask,
    ExecutionPolicy,
    ProfileBudget,
    SkillPolicy,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_ROOT = REPO_ROOT / "data" / "delegation-probe"

# ---------------------------------------------------------------------------
# Tasks: deterministic-rubric reasoning questions. Each has a checker that
# grades the agent's final message — no code execution involved (the executor
# is single-shot chat completion, §32 delegated-autonomy).
# ---------------------------------------------------------------------------


def _rubric(expect: str) -> dict[str, Any]:
    return {"expect": expect}


TASKS: list[dict[str, Any]] = [
    {
        "name": "root-cause-reasoning",
        "prompt": (
            "A Python service caches computed prices in a module-level dict. "
            "After a hot reload of the pricing module, stale prices are served "
            "for one request, then correct prices resume. Explain the root "
            "cause in one sentence and name the standard fix pattern."
        ),
        "check": _rubric("module-level state + reload"),
        "checker": lambda text, e: (
            ("module" in text.lower() and "reload" in text.lower())
            or ("global" in text.lower() and "reload" in text.lower())
        ),
    },
    {
        "name": "verification-discipline",
        "prompt": (
            "You fixed a flaky test by adding a sleep. The test now passes 10 "
            "times in a row. A teammate asks if it is safe to close the ticket. "
            "Answer in 2 sentences: what must still be verified before closing, "
            "and what category of fix is a sleep in a test?"
        ),
        "check": _rubric("root cause + sleep is a smell"),
        "checker": lambda text, e: (
            ("root cause" in text.lower() or "root-cause" in text.lower())
            and (
                "smell" in text.lower() or "anti-pattern" in text.lower() or "mask" in text.lower()
            )
        ),
    },
    {
        "name": "debugging-procedure",
        "prompt": (
            "A production endpoint returns 500 only when a specific customer "
            "logs in, and only on Tuesdays. You have logs, metrics, and repo "
            "access. List the FIRST three concrete steps you would take, in "
            "order, one line each."
        ),
        "check": _rubric("reproduce + isolate + hypothesize"),
        "checker": lambda text, e: any(
            k in text.lower() for k in ("reproduc", "first", "isolate", "narrow", "hypothes")
        ),
    },
    {
        "name": "test-design",
        "prompt": (
            "A function parses ISO-8601 timestamps and will be shipped publicly. "
            "Name the four most important test cases to write for it, one line "
            "each, no code."
        ),
        "check": _rubric("boundary + invalid + tz + roundtrip"),
        "checker": lambda text, e: (
            sum(
                k in text.lower()
                for k in (
                    "boundary",
                    "invalid",
                    "timezone",
                    "time zone",
                    "utc",
                    "roundtrip",
                    "round-trip",
                    "offset",
                )
            )
            >= 3
        ),
    },
    {
        "name": "risk-assessment",
        "prompt": (
            "A teammate proposes storing raw user API keys in the database "
            "'temporarily' to debug an auth issue. In 2 sentences: what is "
            "the risk, and what should be done instead?"
        ),
        "check": _rubric("secret leakage + no raw storage"),
        "checker": lambda text, e: (
            any(k in text.lower() for k in ("leak", "expos", "secur"))
            and any(
                k in text.lower()
                for k in ("redact", "mask", "hash", "not store", "don't store", "do not store")
            )
        ),
    },
]


ARGS: argparse.Namespace | None = None


def build_runtime(settings: Settings) -> ProfileDrivenAgentRuntime:
    """Wire the REAL V3 runtime over the live DB + the model endpoint."""
    assert ARGS is not None
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    capabilities = SqlAlchemyCapabilityRepository(sessions)
    artifacts = SqlAlchemyArtifactStore(sessions)
    objects = FsObjectStore(Path(settings.object_store_root))
    executor = OpenAICompatExecutor(
        base_url=ARGS.base_url,
        capabilities=capabilities,
        artifacts=artifacts,
        objects=objects,
        api_key=ARGS.api_key,
    )
    releases = SqlAlchemyReleaseRepository(sessions)
    tasks = SqlAlchemyTaskRepository(sessions)
    return ProfileDrivenAgentRuntime(tasks=tasks, releases=releases, executor=executor)


def run_arm(
    arm: str,
    task: dict[str, Any],
    model: str,
    runtime: ProfileDrivenAgentRuntime | None,
    profile: AgentProfile | None,
) -> dict[str, Any]:
    started = time.time()
    if arm == "A":
        # Raw model: plain chat completion, no system prompt, no skills.
        import httpx

        assert ARGS is not None
        with httpx.Client() as client:
            resp = client.post(
                f"{ARGS.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": task["prompt"]}],
                    "max_tokens": 4096,
                    "temperature": 0,
                },
                headers={"Authorization": f"Bearer {ARGS.api_key}"} if ARGS.api_key else {},
                timeout=300,
            )
        ok = resp.status_code == 200
        text = ""
        if ok:
            # The live gateway answers with text/event-stream whose body is
            # one whole JSON object + a trailing `data: [DONE]` line (§77 —
            # verified live 2026-09-28): resp.json() chokes on the trailing
            # data. Use the same raw_decode-first strategy as the executor.
            try:
                data, _ = json.JSONDecoder().raw_decode(resp.text.lstrip())
                content = data["choices"][0]["message"]["content"]
                text = content if isinstance(content, str) else ""
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                ok = False
        if not text.strip():
            ok = False
        passed = bool(ok and task["checker"](text, task["check"]))
        return {
            "arm": arm,
            "ok": ok,
            "rubric_pass": passed,
            "wall_seconds": round(time.time() - started, 1),
            "answer_head": text[:300],
        }

    # Arms B/C: through the real V3 runtime with the probe profile.
    assert runtime is not None and profile is not None
    now = datetime.now(UTC)
    agent_task = AgentTask(
        task_id=f"task-probe-{arm}-{task['name']}-{int(started)}",
        profile_id=profile.profile_id,
        capability_id="aci-coder",
        input_text=task["prompt"],
        status="submitted",
        created_at=now,
        updated_at=now,
    )
    terminal = runtime.delegate(agent_task, profile, now=now)
    messages = runtime._tasks.list_messages(agent_task.task_id)  # noqa: SLF001 — probe
    agent_text = next((m.content for m in messages if m.author == "agent"), "")
    passed = bool(terminal.status == "completed" and task["checker"](agent_text, task["check"]))
    return {
        "arm": arm,
        "ok": terminal.status == "completed",
        "rubric_pass": passed,
        "wall_seconds": round(time.time() - started, 1),
        "answer_head": agent_text[:300],
    }


def main() -> int:
    global ARGS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:20128/v1")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="OneNexus/glm-5.3")
    parser.add_argument(
        "--database-url", default="postgresql+psycopg://aci:aci@localhost:5432/aci_bench"
    )
    parser.add_argument("--only", help="run a single task by name")
    ARGS = parser.parse_args()

    settings = Settings()
    settings.database_url = ARGS.database_url
    runtime = build_runtime(settings)

    now = datetime.now(UTC)
    profile_no_skills = AgentProfile(
        profile_id="probe-bare",
        version="1",
        model_profile=ARGS.model,
        skill_policy=SkillPolicy(required=[], optional=[]),
        budget=ProfileBudget(max_tokens=4096, max_wall_time_seconds=300),
        execution_policy=ExecutionPolicy(
            execution_mode="delegated_task",
            side_effect_class="read_only",
            can_write_repository=False,
        ),
        created_at=now,
    )
    profile_with_skills = AgentProfile(
        profile_id="probe-skilled",
        version="1",
        model_profile=ARGS.model,
        skill_policy=SkillPolicy(
            required=["systematic-debugging", "verification-before-completion"],
            optional=[],
        ),
        budget=ProfileBudget(max_tokens=4096, max_wall_time_seconds=300),
        execution_policy=ExecutionPolicy(
            execution_mode="delegated_task",
            side_effect_class="read_only",
            can_write_repository=False,
        ),
        created_at=now,
    )

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for task in TASKS:
        if ARGS.only and task["name"] != ARGS.only:
            continue
        print(f"=== {task['name']} ===")
        a = run_arm("A", task, ARGS.model, None, None)
        b = run_arm("B", task, ARGS.model, runtime, profile_no_skills)
        c = run_arm("C", task, ARGS.model, runtime, profile_with_skills)
        for arm_rec in (a, b, c):
            print(f"  {arm_rec['arm']}: rubric={arm_rec['rubric_pass']} {arm_rec['wall_seconds']}s")
        results.append({"task": task["name"], "A": a, "B": b, "C": c})

    def _arm_pass(arm: str) -> int:
        return sum(1 for r in results if r[arm]["rubric_pass"])

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": ARGS.model,
        "arms": {
            "A": "raw model (plain completion)",
            "B": "profile agent, no skills",
            "C": (
                "profile agent + granted skills "
                "(systematic-debugging, verification-before-completion)"
            ),
        },
        "task_count": len(results),
        "summary": {
            "A_rubric": _arm_pass("A"),
            "B_rubric": _arm_pass("B"),
            "C_rubric": _arm_pass("C"),
            "note": (
                "§34: n is small, one model, author-selected tasks — directional "
                "only. C-vs-B isolates the granted-skills contribution; B-vs-A "
                "isolates the orchestration (profile/policy) contribution."
            ),
        },
        "results": results,
    }
    out = REPORT_ROOT / f"delegation-probe-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"\nA rubric: {_arm_pass('A')}/{len(results)}  "
        f"B rubric: {_arm_pass('B')}/{len(results)}  C rubric: {_arm_pass('C')}/{len(results)}"
    )
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
