"""Held-out routing evaluation set (plan §3.1, docs/plans/aci-improvement-2026-10.md).

``DEV_CASES`` and ``KERNEL_QUERY_CASES`` were used to diagnose and tune the
router, so they can never become held-out by relabeling. This set is the
complement: NEW tasks, NEW wording, annotated independently. Any PR that
changes reranker/embedder/composer must carry a paired A/B on this set plus
the dev sets (§34); the dev sets stay the regression/tuning instruments.

Authorship and independence (recorded honestly, §34):
- Authored 2026-10-01 by an annotator independent of all router changes —
  no reranker/embedder/composer code or weight was read or changed while
  writing these cases. **MUST NOT be used for tuning — evaluation only.**
  Any threshold or weight adjusted against this set converts it into a dev
  set and voids it as held-out evidence.
- Annotations were written from each skill's own trusted routing summary
  (name/description/provides), read READ-ONLY from the ``aci_bench``
  production corpus on 2026-10-01 — never from ``DEV_CASES`` /
  ``KERNEL_QUERY_CASES`` annotations, and never from raw SKILL.md bodies.
- Task texts are new: a unit test enforces no exact match and no >80%
  token overlap (Jaccard AND containment) against both existing sets.
- Author-annotated, routing-side only: no acceptance tests execute, no
  causal claims, n is small — every number from this set is directional
  (§34) until a paired A/B with adequate sample says otherwise.

Coverage (30 cases): debugging 4, testing 3, refactoring/design 2,
security 2, planning/workflow 3, frontend/visual-design 3, comms 2,
skill-authoring 1, platform 2, agent-meta 4, empty-bundle 4. Styles mix
user-style tickets (``heldout/user-task``) with agent-style capability
requests in the kernel ``normalized_need`` shape (objective + constraint
lines, ``heldout/capability-request``).

Empty-bundle cases (``EMPTY_BUNDLE_CASE_IDS``): the CORRECT answer is that
no skill in the corpus fits. They still annotate ``relevant_irrelevant``
confusers so misroutes measure something; the headline metric there is
abstention. Honest expectation for the current pipeline: it has no
confidence floor anywhere (retrieval always returns candidates, the
composer takes rank order up to the budgets), so abstention will be 0
unless retrieval is empty — a structural property this set exists to
make visible, not a bug to fix under it.

``aci-coder`` (kind=agent) is deliberately NOT in the vocabulary: routing
replays filter ``allowed_kinds=["skill"]`` (the benchmark harness and the
kernel capability path both do), so an agent-kind annotation could never
be selected and would only distort recall.
"""

from aci.evaluation.models import BenchmarkCase

__all__ = ["EMPTY_BUNDLE_CASE_IDS", "HELDOUT_CASES", "HELDOUT_CORPUS_CAPABILITY_IDS"]

#: The real production corpus, pinned READ-ONLY from ``aci_bench`` on
#: 2026-10-01 (36 active production skills). Every annotation must reference
#: one of these ids — a unit test enforces it, so a renamed/removed skill
#: breaks this set loudly instead of silently measuring nothing. Kept in
#: sync with ``dev_cases.CORPUS_CAPABILITY_IDS`` by test (same corpus pin).
HELDOUT_CORPUS_CAPABILITY_IDS: frozenset[str] = frozenset(
    {
        "academy-guide",
        "algorithmic-art",
        "brainstorming",
        "brand-guidelines",
        "canvas-design",
        "claude-api",
        "codebase-design",
        "debugging",
        "diagnosing-bugs",
        "diagnosing-superpowers",
        "discernment-nudge",
        "dispatching-parallel-agents",
        "executing-plans",
        "finishing-a-development-branch",
        "frontend-design",
        "internal-comms",
        "mcp-builder",
        "prompt-injection-defense",
        "receiving-code-review",
        "refactoring",
        "requesting-code-review",
        "skill-creator",
        "slack-gif-creator",
        "subagent-driven-development",
        "systematic-debugging",
        "tdd",
        "test-driven-development",
        "testing",
        "theme-factory",
        "using-git-worktrees",
        "using-superpowers",
        "verification-before-completion",
        "webapp-testing",
        "web-artifacts-builder",
        "writing-plans",
        "writing-skills",
    }
)

_USER_TASK = "heldout/user-task"
_CAPABILITY_REQUEST = "heldout/capability-request"


def _user(
    case_id: str,
    category: str,
    task_text: str,
    *,
    strong: list[str],
    acceptable: list[str] | None = None,
    irrelevant: list[str] | None = None,
) -> BenchmarkCase:
    """A user-style ticket: prose task text, the way a human writes it."""
    return BenchmarkCase(
        case_id=case_id,
        category=category,
        fixture=_USER_TASK,
        task_text=task_text,
        relevant_strong=strong,
        relevant_acceptable=acceptable or [],
        relevant_irrelevant=irrelevant or [],
    )


def _request(
    case_id: str,
    category: str,
    objective: str,
    constraints: list[str],
    *,
    strong: list[str],
    acceptable: list[str] | None = None,
    irrelevant: list[str] | None = None,
) -> BenchmarkCase:
    """An agent-style capability request in the kernel ``normalized_need``
    shape: one objective sentence plus constraint lines, newline-joined."""
    return BenchmarkCase(
        case_id=case_id,
        category=category,
        fixture=_CAPABILITY_REQUEST,
        task_text="\n".join([objective, *constraints]),
        relevant_strong=strong,
        relevant_acceptable=acceptable or [],
        relevant_irrelevant=irrelevant or [],
    )


#: Cases whose CORRECT answer is an empty bundle — no corpus skill fits the
#: task. The router abstaining there is the win; anything it selects is a
#: misroute (the annotated confusers make that measurable).
EMPTY_BUNDLE_CASE_IDS: frozenset[str] = frozenset(
    {
        "heldout-empty-k8s",
        "heldout-empty-offsite",
        "heldout-empty-speech",
        "heldout-empty-translate",
    }
)

HELDOUT_CASES: list[BenchmarkCase] = [
    # --- debugging (4) -------------------------------------------------------
    _user(
        "heldout-debug-memory",
        "debugging",
        "Our long-running Python ingestion service gains roughly 50 MB of RSS "
        "per day until the OOM killer ends it. Find where the memory is being "
        "retained before anyone changes code.",
        strong=["diagnosing-bugs", "systematic-debugging"],
        acceptable=["debugging"],
        irrelevant=["refactoring", "theme-factory"],
    ),
    _user(
        "heldout-debug-deadlock",
        "debugging",
        "Two worker threads occasionally freeze the whole process when each "
        "holds a database lock the other wants. Reproduce the freeze under "
        "load before proposing a fix.",
        strong=["systematic-debugging", "debugging"],
        acceptable=["diagnosing-bugs"],
        irrelevant=["dispatching-parallel-agents", "brand-guidelines"],
    ),
    _user(
        "heldout-debug-mojibake",
        "debugging",
        "Loading a customer's CSV turns names like 'Renée' into 'RenÃ©e' "
        "inside our Postgres tables. Work out where the encoding breaks "
        "before fixing it.",
        strong=["debugging", "diagnosing-bugs"],
        acceptable=["systematic-debugging"],
        irrelevant=["internal-comms", "algorithmic-art"],
    ),
    _request(
        "heldout-kernel-debug-heap",
        "debugging",
        "I need a way to find why our Node.js API server's heap climbs until "
        "the process restarts, using the core dumps we already keep.",
        ["Node.js", "no speculative fixes"],
        strong=["debugging", "systematic-debugging"],
        acceptable=["diagnosing-bugs"],
        irrelevant=["canvas-design", "writing-plans"],
    ),
    # --- testing (3) --------------------------------------------------------
    _user(
        "heldout-test-property",
        "testing",
        "Add property-based tests with Hypothesis to our pricing module: "
        "generate inputs instead of hand-picking them, and shrink any "
        "counterexample down to a minimal reproduction.",
        strong=["testing"],
        acceptable=["tdd", "test-driven-development"],
        irrelevant=["algorithmic-art", "slack-gif-creator"],
    ),
    _request(
        "heldout-kernel-test-snapshot",
        "testing",
        "I need to pin the exact JSON shape our public API returns today so "
        "that any future change to it fails in CI.",
        ["Python", "the API is already live"],
        strong=["testing"],
        acceptable=["verification-before-completion"],
        irrelevant=["theme-factory", "debugging"],
    ),
    _user(
        "heldout-test-cli",
        "testing",
        "Our CLI silently ignores flags that arrive after positional "
        "arguments. Write the regression tests that lock the intended "
        "parsing behavior, including the awkward combinations.",
        strong=["testing"],
        acceptable=["systematic-debugging", "tdd"],
        irrelevant=["frontend-design", "internal-comms"],
    ),
    # --- refactoring / design (2) -------------------------------------------
    _user(
        "heldout-refactor-circular",
        "refactoring",
        "Three of our modules import each other in a cycle. Untangle the "
        "circular imports without changing what any of them exports.",
        strong=["refactoring", "codebase-design"],
        acceptable=["verification-before-completion"],
        irrelevant=["slack-gif-creator", "brand-guidelines"],
    ),
    _request(
        "heldout-kernel-design-config",
        "design",
        "I need to decide where configuration parsing should live so the "
        "rest of the application never imports the config file format "
        "directly.",
        ["Python", "several formats must keep working"],
        strong=["codebase-design"],
        acceptable=["refactoring"],
        irrelevant=["frontend-design", "canvas-design"],
    ),
    # --- security (2) --------------------------------------------------------
    _user(
        "heldout-sec-cicommit",
        "security",
        "A malicious commit message could steer our CI agent into running "
        "arbitrary commands. Analyze that risk and wall the untrusted text "
        "off from the agent's tool arguments.",
        strong=["prompt-injection-defense"],
        acceptable=["testing"],
        irrelevant=["finishing-a-development-branch", "debugging"],
    ),
    _request(
        "heldout-kernel-sec-crossagent",
        "security",
        "I need to stop one agent's output from being able to command a "
        "second agent that reads it downstream.",
        ["the two agents share a work queue"],
        strong=["prompt-injection-defense"],
        acceptable=["verification-before-completion"],
        irrelevant=["dispatching-parallel-agents", "tdd"],
    ),
    # --- planning / workflow (3) ---------------------------------------------
    _user(
        "heldout-plan-migration",
        "planning",
        "We have to move 40 tables from MySQL to Postgres with zero "
        "downtime. Draft the ordered plan with checkpoints and rollback "
        "points before anyone writes code.",
        strong=["writing-plans"],
        acceptable=["brainstorming", "verification-before-completion"],
        irrelevant=["slack-gif-creator", "algorithmic-art"],
    ),
    _request(
        "heldout-kernel-workflow-packages",
        "workflow",
        "I need to drive a written implementation plan through several "
        "independent work packages in this session, spawning a fresh "
        "context for each package.",
        ["the packages touch disjoint files"],
        strong=["subagent-driven-development"],
        acceptable=["dispatching-parallel-agents", "executing-plans"],
        irrelevant=["tdd", "debugging"],
    ),
    _user(
        "heldout-workflow-hotfix",
        "workflow",
        "A production hotfix has to land tonight while the checkout is "
        "occupied by a half-finished refactor. Get an isolated copy of "
        "main, apply the fix there, and bring it back.",
        strong=["using-git-worktrees"],
        acceptable=["finishing-a-development-branch"],
        irrelevant=["debugging", "theme-factory"],
    ),
    # --- frontend / visual design (3) ----------------------------------------
    _user(
        "heldout-fe-darkmode",
        "frontend",
        "Add a dark mode to our marketing site that follows the visitor's OS "
        "preference and keeps both themes readable at WCAG contrast.",
        strong=["frontend-design"],
        acceptable=["theme-factory"],
        irrelevant=["tdd", "debugging"],
    ),
    _user(
        "heldout-design-poster",
        "visual-design",
        "Produce a printable A2 poster for a local jazz festival — layout, "
        "palette, and typography — delivered as a PDF.",
        strong=["canvas-design"],
        acceptable=["theme-factory", "brand-guidelines"],
        irrelevant=["algorithmic-art", "tdd"],
    ),
    _request(
        "heldout-kernel-fe-catalog",
        "frontend",
        "I need a claude.ai artifact that lets a user browse two hundred "
        "products, filter them by facet, and open a detail drawer for each "
        "one.",
        ["React", "shadcn/ui"],
        strong=["web-artifacts-builder"],
        acceptable=["frontend-design"],
        irrelevant=["mcp-builder", "tdd"],
    ),
    # --- comms (2) ------------------------------------------------------------
    _user(
        "heldout-comms-faq",
        "comms",
        "Write the FAQ page for our internal tooling portal: the ten "
        "questions new hires actually ask, each answered in a paragraph "
        "with a link to the runbook.",
        strong=["internal-comms"],
        acceptable=["discernment-nudge"],
        irrelevant=["tdd", "debugging"],
    ),
    _user(
        "heldout-comms-newsletter",
        "comms",
        "Draft this quarter's company newsletter item about the platform "
        "team's move off the legacy queue — one page, plain language, no "
        "jargon.",
        strong=["internal-comms"],
        irrelevant=["writing-plans", "debugging"],
    ),
    # --- skill authoring (1) --------------------------------------------------
    _user(
        "heldout-skill-tuning",
        "skill-authoring",
        "Our custom skill fires on far too many unrelated prompts. Tighten "
        "its description and frontmatter so it only triggers where it "
        "genuinely helps, and prove the change with evals.",
        strong=["skill-creator"],
        acceptable=["writing-skills"],
        irrelevant=["tdd", "debugging"],
    ),
    # --- platform / API (2) ---------------------------------------------------
    _request(
        "heldout-kernel-platform-inventory",
        "platform",
        "I need to expose our internal inventory service to agents as "
        "read-only MCP tools with strict schemas.",
        ["Python", "FastMCP"],
        strong=["mcp-builder"],
        acceptable=["claude-api"],
        irrelevant=["tdd", "canvas-design"],
    ),
    _user(
        "heldout-platform-migration",
        "platform",
        "Migrate our prompts off the deprecated fast model to the current "
        "one, then re-check costs and context limits per request.",
        strong=["claude-api"],
        acceptable=["verification-before-completion"],
        irrelevant=["tdd", "refactoring"],
    ),
    # --- agent meta (4) -------------------------------------------------------
    _user(
        "heldout-meta-session",
        "agent-meta",
        "My last session redid work it had already finished and ignored "
        "the plan I gave it. Work out why, and gather the evidence for a "
        "bug report to the maintainers.",
        strong=["diagnosing-superpowers"],
        acceptable=["systematic-debugging"],
        irrelevant=["finishing-a-development-branch", "canvas-design"],
    ),
    _user(
        "heldout-meta-academy",
        "agent-meta",
        "Where should a non-technical teammate start learning to build "
        "Claude skills? Point them at the right courses and tutorials.",
        strong=["academy-guide"],
        irrelevant=["writing-skills", "debugging"],
    ),
    _user(
        "heldout-meta-gif",
        "agent-meta",
        "Make a short looping GIF of our deploy dashboard celebrating the "
        "release, sized for a Slack announcement.",
        strong=["slack-gif-creator"],
        irrelevant=["internal-comms", "algorithmic-art", "tdd"],
    ),
    _request(
        "heldout-kernel-meta-questions",
        "agent-meta",
        "I need a check to run over my own recommendation before I send "
        "it, appending the short questions the user should ask me back.",
        ["at most once per conversation"],
        strong=["discernment-nudge"],
        irrelevant=["brainstorming", "debugging"],
    ),
    # --- empty bundle (4) -----------------------------------------------------
    # The correct answer for each of these is that NO corpus skill fits.
    # ``relevant_irrelevant`` names the plausible confusers so a misroute
    # is measurable; abstention is the win condition.
    _user(
        "heldout-empty-k8s",
        "empty-bundle",
        "Prepare the Kubernetes manifest set for our Go billing service: "
        "rolling deploys, liveness probes, and a per-pod resource budget.",
        strong=[],
        acceptable=[],
        irrelevant=[
            "writing-plans",
            "finishing-a-development-branch",
            "verification-before-completion",
        ],
    ),
    _user(
        "heldout-empty-translate",
        "empty-bundle",
        "Translate our twelve-page user manual from English to Japanese, "
        "keeping the same section structure and a consistent technical "
        "tone.",
        strong=[],
        acceptable=[],
        irrelevant=["claude-api", "internal-comms", "theme-factory"],
    ),
    _user(
        "heldout-empty-speech",
        "empty-bundle",
        "Write a warm five-minute wedding speech for my sister's reception "
        "— a couple of anecdotes, nothing that excludes any guest.",
        strong=[],
        acceptable=[],
        irrelevant=["brainstorming", "internal-comms", "discernment-nudge"],
    ),
    _user(
        "heldout-empty-offsite",
        "empty-bundle",
        "Plan our twelve-person team offsite in Lisbon: three days, a "
        "workshop day, one dinner, and a total budget under six thousand "
        "euros.",
        strong=[],
        acceptable=[],
        irrelevant=["brainstorming", "writing-plans", "internal-comms"],
    ),
]
