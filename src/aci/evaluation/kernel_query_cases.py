"""Kernel-style capability-request cases (harness.md §11.3; §55).

``DEV_CASES`` phrase tasks the way a user writes a ticket. The HarnessKernel
routes something else: the model's ``request_capability`` call, normalized
to ``objective`` (one sentence, often first-person — "I need a … to …")
plus constraint lines (language / framework / environment), joined by
newlines (``normalized_need`` in ``adapters/outbound/agent_capabilities``).
These cases replay that shape through the same §14 pipeline so kernel-path
selection quality is measurable on its own axis.

Annotations reference the REAL production corpus (``CORPUS_CAPABILITY_IDS``,
36 skills) and are written from each skill's own description. Where a case
names ``relevant_irrelevant``, it prefers plausible confusers (a skill that
shares vocabulary but would mislead the agent, e.g. finishing-a-development-
branch for a FAILING test) over never-retrieved ids, so misroutes measure
something.

Caveats (§34, recorded honestly):
- The two ``kernel-real-*`` cases are verbatim ``task_text`` of the
  ``harness-kernel`` route runs in ``aci_bench`` (2026-10-01, real model via
  the kernel). The rest are author-written in that shape — the same author
  who diagnosed the reranker on the real ones, so bias risk is real; treat
  differences as directional, not causal.
- Routing-side only: no acceptance tests execute here.

Coverage (29): debugging 6, testing/TDD 5, refactoring/design 2, review 2,
planning/workflow 4, git/branch 2, frontend 3, docs/authoring 2, MCP/API/
security 3.
"""

from aci.evaluation.dev_cases import CORPUS_CAPABILITY_IDS
from aci.evaluation.models import BenchmarkCase

__all__ = ["CORPUS_CAPABILITY_IDS", "KERNEL_QUERY_CASES"]

_FIXTURE = "kernel/capability-request"


def _case(
    case_id: str,
    category: str,
    objective: str,
    constraints: list[str],
    *,
    strong: list[str],
    acceptable: list[str] | None = None,
    irrelevant: list[str] | None = None,
) -> BenchmarkCase:
    """Build the routed text exactly like ``normalized_need``: objective +
    constraint lines, newline-joined."""
    return BenchmarkCase(
        case_id=case_id,
        category=category,
        fixture=_FIXTURE,
        task_text="\n".join([objective, *constraints]),
        relevant_strong=strong,
        relevant_acceptable=acceptable or [],
        relevant_irrelevant=irrelevant or [],
    )


_DEBUG_STRONG = ["systematic-debugging", "debugging", "diagnosing-bugs"]

KERNEL_QUERY_CASES: list[BenchmarkCase] = [
    # --- debugging (6) -------------------------------------------------------
    # Verbatim real kernel queries (aci_bench route_runs, client_type
    # 'harness-kernel', 2026-10-01).
    _case(
        "kernel-real-001",
        "debugging",
        "Systematic debugging procedure for a failing test in a Python workspace: "
        "find root cause, fix, and verify with pytest",
        ["Python", "pytest"],
        strong=_DEBUG_STRONG,
        acceptable=["verification-before-completion", "testing"],
        irrelevant=["finishing-a-development-branch", "using-git-worktrees"],
    ),
    _case(
        "kernel-real-002",
        "debugging",
        "A test in this workspace fails; I need a systematic debugging procedure to "
        "find the root cause, fix it, and verify with tests.",
        ["Python", "pytest"],
        strong=_DEBUG_STRONG,
        acceptable=["verification-before-completion", "testing"],
        irrelevant=["finishing-a-development-branch", "using-git-worktrees"],
    ),
    _case(
        "kernel-debug-003",
        "debugging",
        "I need to find the root cause of an intermittent KeyError raised in a "
        "background worker before changing any code.",
        ["Python", "reproduce before fixing"],
        strong=_DEBUG_STRONG,
        acceptable=["verification-before-completion"],
        irrelevant=["brainstorming", "finishing-a-development-branch"],
    ),
    _case(
        "kernel-debug-004",
        "debugging",
        "Diagnose why the endpoint latency doubled after the last change.",
        ["Python", "profile first, no speculative fixes"],
        strong=["diagnosing-bugs"],
        acceptable=["systematic-debugging", "debugging"],
        irrelevant=["frontend-design", "test-driven-development"],
    ),
    _case(
        "kernel-debug-005",
        "debugging",
        "Help me debug a crashing CLI command from its stack trace and the error logs.",
        ["TypeScript", "Node.js"],
        strong=["debugging", "systematic-debugging"],
        acceptable=["diagnosing-bugs"],
        irrelevant=["canvas-design", "writing-plans"],
    ),
    _case(
        "kernel-debug-006",
        "debugging",
        "The CI build is failing on one test that passes locally; I need a procedure "
        "to track down the cause.",
        ["Python", "GitHub Actions"],
        strong=["systematic-debugging", "diagnosing-bugs"],
        acceptable=["debugging", "testing"],
        irrelevant=["finishing-a-development-branch", "requesting-code-review"],
    ),
    # --- testing / TDD (5) ---------------------------------------------------
    _case(
        "kernel-tdd-001",
        "testing",
        "Implement a new parser function test-first using red-green-refactor.",
        ["Python", "pytest"],
        strong=["tdd", "test-driven-development"],
        acceptable=["testing"],
        irrelevant=["finishing-a-development-branch", "brand-guidelines"],
    ),
    _case(
        "kernel-tdd-002",
        "testing",
        "Fix this bug test-first: I want a failing regression test that reproduces "
        "it before touching the code.",
        ["Go", "go test"],
        strong=["tdd", "test-driven-development"],
        acceptable=["systematic-debugging", "debugging", "diagnosing-bugs"],
        irrelevant=["frontend-design", "internal-comms"],
    ),
    _case(
        "kernel-test-003",
        "testing",
        "Write unit and integration tests for an existing module and report the coverage.",
        ["Python", "pytest-cov"],
        strong=["testing"],
        acceptable=["tdd", "test-driven-development"],
        irrelevant=["frontend-design", "using-git-worktrees"],
    ),
    _case(
        "kernel-test-004",
        "testing",
        "I need to test the login flow of a local web app in a real browser and "
        "capture screenshots.",
        ["Playwright", "localhost:3000"],
        strong=["webapp-testing"],
        acceptable=["testing"],
        irrelevant=["algorithmic-art", "internal-comms"],
    ),
    _case(
        "kernel-verify-005",
        "verification",
        "Before I report the task as done, I need to verify the fix really works "
        "and show the evidence.",
        ["run the full test suite"],
        strong=["verification-before-completion"],
        acceptable=["testing"],
        irrelevant=["brainstorming", "canvas-design"],
    ),
    # --- refactoring / design (2) -------------------------------------------
    _case(
        "kernel-refactor-001",
        "refactoring",
        "Refactor a 400-line function into smaller units without changing its behavior.",
        ["Java", "keep the existing tests green"],
        strong=["refactoring"],
        acceptable=["codebase-design", "testing"],
        irrelevant=["diagnosing-bugs", "finishing-a-development-branch"],
    ),
    _case(
        "kernel-design-002",
        "design",
        "Design the interface of a storage module so it hides complexity and is easy to test.",
        ["Python", "small public API"],
        strong=["codebase-design"],
        acceptable=["refactoring", "tdd"],
        irrelevant=["frontend-design", "canvas-design"],
    ),
    # --- review (2) ----------------------------------------------------------
    _case(
        "kernel-review-001",
        "review",
        "I need a code review of my changes before merging them.",
        ["summarize the diff and the risks"],
        strong=["requesting-code-review"],
        acceptable=["verification-before-completion", "finishing-a-development-branch"],
        irrelevant=["brainstorming", "tdd"],
    ),
    _case(
        "kernel-review-002",
        "review",
        "Address the reviewer's feedback on my pull request; some suggestions look "
        "technically wrong.",
        ["verify each suggestion before applying it"],
        strong=["receiving-code-review"],
        acceptable=["verification-before-completion"],
        irrelevant=["canvas-design", "writing-plans"],
    ),
    # --- planning / workflow (4) --------------------------------------------
    _case(
        "kernel-plan-001",
        "planning",
        "Write an implementation plan for a multi-step feature before touching any code.",
        ["the spec is in docs/spec.md"],
        strong=["writing-plans"],
        acceptable=["brainstorming"],
        irrelevant=["debugging", "webapp-testing"],
    ),
    _case(
        "kernel-plan-002",
        "planning",
        "Execute the existing implementation plan step by step in this session myself.",
        ["no subagent tool available"],
        strong=["executing-plans"],
        acceptable=["verification-before-completion"],
        irrelevant=["brainstorming", "canvas-design"],
    ),
    _case(
        "kernel-plan-003",
        "planning",
        "Split three independent failing modules across parallel agents.",
        ["no shared state between the tasks"],
        strong=["dispatching-parallel-agents"],
        acceptable=["subagent-driven-development"],
        irrelevant=["tdd", "brand-guidelines"],
    ),
    _case(
        "kernel-brainstorm-004",
        "planning",
        "Explore the requirements and design options for a new feature before implementing it.",
        ["ask about user intent first"],
        strong=["brainstorming"],
        acceptable=["writing-plans"],
        irrelevant=["debugging", "testing"],
    ),
    # --- git / branch (2) ----------------------------------------------------
    _case(
        "kernel-git-001",
        "workflow",
        "Implementation is complete and all tests pass; decide how to merge this "
        "branch or open a pull request.",
        ["git", "GitHub"],
        strong=["finishing-a-development-branch"],
        acceptable=["requesting-code-review", "verification-before-completion"],
        irrelevant=["brainstorming", "debugging"],
    ),
    _case(
        "kernel-git-002",
        "workflow",
        "Set up an isolated git worktree for a new feature branch.",
        ["do not disturb the current checkout"],
        strong=["using-git-worktrees"],
        irrelevant=["debugging", "frontend-design"],
    ),
    # --- frontend (3) --------------------------------------------------------
    _case(
        "kernel-fe-001",
        "frontend",
        "Design a distinctive landing page UI with intentional typography.",
        ["React", "avoid a templated look"],
        strong=["frontend-design"],
        acceptable=["web-artifacts-builder", "theme-factory"],
        irrelevant=["tdd", "debugging"],
    ),
    _case(
        "kernel-fe-002",
        "frontend",
        "Build a multi-component claude.ai HTML artifact with state management.",
        ["React", "Tailwind CSS", "shadcn/ui"],
        strong=["web-artifacts-builder"],
        acceptable=["frontend-design"],
        irrelevant=["mcp-builder", "debugging"],
    ),
    _case(
        "kernel-fe-003",
        "frontend",
        "Apply Anthropic brand colors and typography to a slide deck artifact.",
        ["company style guidelines"],
        strong=["brand-guidelines"],
        acceptable=["theme-factory"],
        irrelevant=["tdd", "algorithmic-art"],
    ),
    # --- docs / authoring (2) -----------------------------------------------
    _case(
        "kernel-docs-001",
        "comms",
        "Draft an incident report for the team about yesterday's outage.",
        ["internal audience", "concise"],
        strong=["internal-comms"],
        irrelevant=["diagnosing-bugs", "debugging"],
    ),
    _case(
        "kernel-skill-002",
        "skill-authoring",
        "Create a new agent skill with a SKILL.md and evals that check it triggers.",
        ["progressive disclosure"],
        strong=["skill-creator", "writing-skills"],
        irrelevant=["tdd", "mcp-builder"],
    ),
    # --- MCP / API / security (2) -------------------------------------------
    _case(
        "kernel-mcp-001",
        "platform",
        "Build an MCP server that exposes an existing REST API as tools.",
        ["Python", "FastMCP"],
        strong=["mcp-builder"],
        acceptable=["claude-api"],
        irrelevant=["tdd", "frontend-design"],
    ),
    _case(
        "kernel-api-002",
        "platform",
        "Call the Claude API with streaming and tool use from a backend service.",
        ["Python", "anthropic SDK", "prompt caching"],
        strong=["claude-api"],
        acceptable=["mcp-builder"],
        irrelevant=["testing", "refactoring"],
    ),
    _case(
        "kernel-sec-003",
        "security",
        "Harden the agent's tool calls against prompt injection from fetched web pages.",
        ["isolate untrusted content"],
        strong=["prompt-injection-defense"],
        irrelevant=["webapp-testing", "debugging"],
    ),
]
