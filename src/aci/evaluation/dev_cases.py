"""The 30+ case dev benchmark (V2 benchmark arena, plan §§34–35, 55, 70).

Unlike ``SMOKE_CASES`` (synthetic fixtures annotating hypothetical ids),
``DEV_CASES`` annotates the REAL production corpus (36 skills, 2026-09-28)
so recall/misroute measure actual routing discrimination. Annotations are
written from each skill's own description — what it genuinely covers —
never from what we want the router to do (§35: annotations help evaluate
routing, they do not define the only acceptable solution path).

Caveats (§34, recorded honestly):
- Annotations were written by the same author who built the router and
  ingested the corpus — bias risk is real; treat recall as directional.
- ``acceptance_tests``/``forbidden_actions`` name criteria that live with a
  future fixture; the harness does not execute them (routing-side metrics
  only). The primary score still comes from task acceptance criteria.
- Cases are author-selected, not sampled; no causal claims.

Coverage (31 cases): debugging 5, testing 5, refactoring/design 4,
security 2, planning/workflow 5, review 2, verification 1, frontend/design
3, platform 2, skill-authoring 1, comms 1.
"""

from aci.evaluation.models import BenchmarkCase

#: The real production corpus (2026-09-28). Every annotation must reference
#: one of these ids — a unit test enforces it, so a renamed/removed skill
#: breaks the dev set loudly instead of silently measuring nothing.
CORPUS_CAPABILITY_IDS: frozenset[str] = frozenset(
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

DEV_CASES: list[BenchmarkCase] = [
    # --- debugging (5) -------------------------------------------------------
    BenchmarkCase(
        case_id="dev-py-mutable-001",
        category="debugging",
        fixture="repo/py-service-v1",
        task_text=(
            "Fix a mutable default argument in a Python service: state leaks "
            "between calls and the second caller sees the first caller's data."
        ),
        relevant_strong=["diagnosing-bugs", "debugging"],
        relevant_acceptable=["systematic-debugging", "verification-before-completion"],
        relevant_irrelevant=["theme-factory", "slack-gif-creator"],
        acceptance_tests=["test_no_state_leak_between_calls"],
        forbidden_actions=["delete_second_caller"],
        baseline_capability_ids=["diagnosing-bugs"],
    ),
    BenchmarkCase(
        case_id="dev-py-race-001",
        category="debugging",
        fixture="repo/py-queue-v2",
        task_text=(
            "Diagnose an intermittent race condition in a Python job queue: "
            "tasks are occasionally dropped under concurrent load."
        ),
        relevant_strong=["systematic-debugging", "diagnosing-bugs"],
        relevant_acceptable=["debugging"],
        relevant_irrelevant=["canvas-design", "brand-guidelines"],
        acceptance_tests=["test_no_dropped_tasks_under_load"],
        forbidden_actions=["disable_concurrency"],
        baseline_capability_ids=["systematic-debugging"],
    ),
    BenchmarkCase(
        case_id="dev-py-exc-001",
        category="debugging",
        fixture="repo/py-cli-v1",
        task_text=(
            "A Python CLI crashes with a confusing traceback. Find the root "
            "cause — the deepest frame — not the symptom frame."
        ),
        relevant_strong=["debugging", "diagnosing-bugs"],
        relevant_acceptable=["systematic-debugging"],
        relevant_irrelevant=["internal-comms", "algorithmic-art"],
        acceptance_tests=["test_cli_exits_cleanly"],
        forbidden_actions=["wrap_traceback_in_try_except"],
        baseline_capability_ids=["debugging"],
    ),
    BenchmarkCase(
        case_id="dev-perf-regress-001",
        category="debugging",
        fixture="repo/api-latency-v3",
        task_text=(
            "Profile a performance regression that appeared between two "
            "releases: p95 latency doubled. Bisect it to the change."
        ),
        relevant_strong=["diagnosing-bugs"],
        relevant_acceptable=["systematic-debugging"],
        relevant_irrelevant=["theme-factory", "slack-gif-creator"],
        acceptance_tests=["test_p95_under_baseline"],
        forbidden_actions=["raise_latency_budget"],
        baseline_capability_ids=["diagnosing-bugs"],
    ),
    BenchmarkCase(
        case_id="dev-js-null-001",
        category="debugging",
        fixture="repo/ts-frontend-v1",
        task_text=(
            "Debug a TypeError in a TypeScript frontend: a nested response "
            "field is sometimes undefined and crashes the render."
        ),
        relevant_strong=["debugging"],
        relevant_acceptable=["diagnosing-bugs", "systematic-debugging"],
        relevant_irrelevant=["mcp-builder", "writing-plans"],
        acceptance_tests=["test_render_survives_undefined"],
        forbidden_actions=["disable_strict_null_checks"],
        baseline_capability_ids=["debugging"],
    ),
    # --- testing (5) --------------------------------------------------------
    BenchmarkCase(
        case_id="dev-tdd-feature-001",
        category="testing",
        fixture="repo/cart-feature-v1",
        task_text=(
            "Implement a new shopping-cart feature test-first: red, green, "
            "then refactor. The tests must outlive the implementation."
        ),
        relevant_strong=["test-driven-development", "tdd"],
        relevant_acceptable=["testing"],
        relevant_irrelevant=["theme-factory", "slack-gif-creator"],
        acceptance_tests=["test_cart_feature_green"],
        forbidden_actions=["write_impl_before_test"],
        baseline_capability_ids=["test-driven-development"],
    ),
    BenchmarkCase(
        case_id="dev-pytest-cov-001",
        category="testing",
        fixture="repo/cart-py-v1",
        task_text=(
            "Write pytest unit tests for a ShoppingCart class: happy path, "
            "boundary values, invalid inputs, and exception coverage."
        ),
        relevant_strong=["testing"],
        relevant_acceptable=["tdd", "test-driven-development"],
        relevant_irrelevant=["canvas-design", "claude-api"],
        acceptance_tests=["test_cart_boundary_and_exceptions"],
        forbidden_actions=["assert_true_always"],
        baseline_capability_ids=["testing"],
    ),
    BenchmarkCase(
        case_id="dev-flaky-001",
        category="testing",
        fixture="repo/ci-flakes-v3",
        task_text=(
            "Fix flaky tests that fail intermittently on CI: freeze time, "
            "seed randomness, isolate filesystem and network state."
        ),
        relevant_strong=["testing", "test-driven-development"],
        relevant_acceptable=["systematic-debugging"],
        relevant_irrelevant=["brand-guidelines", "algorithmic-art"],
        acceptance_tests=["test_suite_rerun_green_x3"],
        forbidden_actions=["skip_flaky_tests"],
        baseline_capability_ids=["testing"],
    ),
    BenchmarkCase(
        case_id="dev-e2e-web-001",
        category="testing",
        fixture="repo/webapp-login-v2",
        task_text=(
            "Write end-to-end browser tests for a webapp login flow with "
            "Playwright: success, wrong password, and locked-out paths."
        ),
        relevant_strong=["webapp-testing"],
        relevant_acceptable=["testing"],
        relevant_irrelevant=["slack-gif-creator", "internal-comms"],
        acceptance_tests=["test_login_e2e_green"],
        forbidden_actions=["disable_wrong_password_check"],
        baseline_capability_ids=["webapp-testing"],
    ),
    BenchmarkCase(
        case_id="dev-mock-001",
        category="testing",
        fixture="repo/svc-boundaries-v1",
        task_text=(
            "Decide what to mock in tests for a service that calls external "
            "APIs and a database: mock at system boundaries only."
        ),
        relevant_strong=["tdd"],
        relevant_acceptable=["testing"],
        relevant_irrelevant=["theme-factory", "slack-gif-creator"],
        acceptance_tests=["test_no_internal_mocking"],
        forbidden_actions=["mock_the_code_under_test"],
        baseline_capability_ids=["tdd"],
    ),
    # --- refactoring / design (4) -------------------------------------------
    BenchmarkCase(
        case_id="dev-godclass-001",
        category="refactoring",
        fixture="repo/legacy-godclass-v4",
        task_text=(
            "Refactor a legacy God-class into small focused modules with "
            "behavior-preserving steps and a test safety net."
        ),
        relevant_strong=["refactoring", "codebase-design"],
        relevant_acceptable=["verification-before-completion"],
        relevant_irrelevant=["slack-gif-creator", "brand-guidelines"],
        acceptance_tests=["test_behavior_preserved"],
        forbidden_actions=["delete_tests_to_speed_up"],
        baseline_capability_ids=["refactoring"],
    ),
    BenchmarkCase(
        case_id="dev-seam-001",
        category="design",
        fixture="repo/payments-greenfield-v1",
        task_text=(
            "Design module boundaries for a new payments service: where "
            "should the seam go so callers never see implementation detail?"
        ),
        relevant_strong=["codebase-design"],
        relevant_acceptable=["refactoring"],
        relevant_irrelevant=["slack-gif-creator", "algorithmic-art"],
        acceptance_tests=["test_seam_hides_impl"],
        forbidden_actions=["expose_db_connection_to_callers"],
        baseline_capability_ids=["codebase-design"],
    ),
    BenchmarkCase(
        case_id="dev-deep-module-001",
        category="design",
        fixture="repo/shallow-module-v1",
        task_text=(
            "Make a shallow module deeper: hide its implementation behind a "
            "small interface and keep it testable through that interface."
        ),
        relevant_strong=["codebase-design"],
        relevant_acceptable=["refactoring"],
        relevant_irrelevant=["internal-comms", "slack-gif-creator"],
        acceptance_tests=["test_interface_stays_small"],
        forbidden_actions=["add_more_public_methods"],
        baseline_capability_ids=["codebase-design"],
    ),
    BenchmarkCase(
        case_id="dev-techdebt-001",
        category="refactoring",
        fixture="repo/legacy-scan-v2",
        task_text=(
            "Identify code smells across a legacy codebase — long methods, "
            "duplicated logic, primitive obsession — and apply proven "
            "refactoring patterns."
        ),
        relevant_strong=["refactoring"],
        relevant_acceptable=["codebase-design"],
        relevant_irrelevant=["claude-api", "slack-gif-creator"],
        acceptance_tests=["test_smells_resolved"],
        forbidden_actions=["rewrite_from_scratch"],
        baseline_capability_ids=["refactoring"],
    ),
    # --- security (2) -------------------------------------------------------
    BenchmarkCase(
        case_id="dev-injection-001",
        category="security",
        fixture="repo/agent-arch-v1",
        task_text=(
            "Review an AI agent architecture for prompt injection risks and "
            "harden tool egress boundaries against untrusted content."
        ),
        relevant_strong=["prompt-injection-defense"],
        relevant_acceptable=["verification-before-completion"],
        relevant_irrelevant=["theme-factory", "slack-gif-creator"],
        acceptance_tests=["test_untrusted_content_cannot_invoke_tools"],
        forbidden_actions=["trust_model_judgment_for_authz"],
        baseline_capability_ids=["prompt-injection-defense"],
    ),
    BenchmarkCase(
        case_id="dev-untrusted-doc-001",
        category="security",
        fixture="repo/rag-pipeline-v2",
        task_text=(
            "Harden a RAG pipeline that ingests untrusted documents against "
            "stored prompt injection: isolate content, constrain tools, "
            "add injection-focused tests."
        ),
        relevant_strong=["prompt-injection-defense"],
        relevant_acceptable=["testing"],
        relevant_irrelevant=["canvas-design", "algorithmic-art"],
        acceptance_tests=["test_stored_injection_inert"],
        forbidden_actions=["concat_untrusted_into_system_prompt"],
        baseline_capability_ids=["prompt-injection-defense"],
    ),
    # --- planning / workflow (5) --------------------------------------------
    BenchmarkCase(
        case_id="dev-plan-feature-001",
        category="planning",
        fixture="repo/feature-spec-v1",
        task_text=(
            "Write an implementation plan for a multi-step feature from its "
            "spec: ordered steps, success criteria, risks."
        ),
        relevant_strong=["writing-plans"],
        relevant_acceptable=["executing-plans", "brainstorming"],
        relevant_irrelevant=["slack-gif-creator", "theme-factory"],
        acceptance_tests=["plan_reviewable_by_human"],
        forbidden_actions=["touch_code_before_plan"],
        baseline_capability_ids=["writing-plans"],
    ),
    BenchmarkCase(
        case_id="dev-execute-plan-001",
        category="planning",
        fixture="repo/plan-inline-v1",
        task_text=(
            "Execute a written implementation plan step by step in this "
            "session as the implementer, keeping progress honest."
        ),
        relevant_strong=["executing-plans"],
        relevant_acceptable=["verification-before-completion"],
        relevant_irrelevant=["algorithmic-art", "slack-gif-creator"],
        acceptance_tests=["test_plan_steps_all_done"],
        forbidden_actions=["skip_steps_silently"],
        baseline_capability_ids=["executing-plans"],
    ),
    BenchmarkCase(
        case_id="dev-brainstorm-001",
        category="planning",
        fixture="repo/new-feature-v1",
        task_text=(
            "Explore approaches for a new product feature before committing "
            "to an implementation: intent, options, trade-offs."
        ),
        relevant_strong=["brainstorming"],
        relevant_acceptable=["writing-plans"],
        relevant_irrelevant=["slack-gif-creator", "mcp-builder"],
        acceptance_tests=["test_approaches_documented"],
        forbidden_actions=["jump_straight_to_code"],
        baseline_capability_ids=["brainstorming"],
    ),
    BenchmarkCase(
        case_id="dev-parallel-001",
        category="workflow",
        fixture="repo/big-refactor-v2",
        task_text=(
            "Speed up a large refactor by dispatching its independent "
            "subtasks to parallel agents without shared state."
        ),
        relevant_strong=["dispatching-parallel-agents"],
        relevant_acceptable=["subagent-driven-development"],
        relevant_irrelevant=["canvas-design", "slack-gif-creator"],
        acceptance_tests=["test_subtasks_merged_cleanly"],
        forbidden_actions=["parallelize_shared_state_tasks"],
        baseline_capability_ids=["dispatching-parallel-agents"],
    ),
    BenchmarkCase(
        case_id="dev-worktree-001",
        category="workflow",
        fixture="repo/two-features-v1",
        task_text=(
            "Work on two features in parallel without stepping on git state: "
            "isolate each in its own worktree, then integrate both."
        ),
        relevant_strong=["using-git-worktrees"],
        relevant_acceptable=["finishing-a-development-branch"],
        relevant_irrelevant=["algorithmic-art", "slack-gif-creator"],
        acceptance_tests=["test_both_features_integrated"],
        forbidden_actions=["commit_to_shared_checkout"],
        baseline_capability_ids=["using-git-worktrees"],
    ),
    # --- review (2) ----------------------------------------------------------
    BenchmarkCase(
        case_id="dev-review-pr-001",
        category="review",
        fixture="repo/risky-change-v1",
        task_text=(
            "Request a thorough code review for a risky change before "
            "merging: what to look for, how to present the diff."
        ),
        relevant_strong=["requesting-code-review"],
        relevant_acceptable=["receiving-code-review"],
        relevant_irrelevant=["theme-factory", "slack-gif-creator"],
        acceptance_tests=["test_review_requested_before_merge"],
        forbidden_actions=["merge_without_review"],
        baseline_capability_ids=["requesting-code-review"],
    ),
    BenchmarkCase(
        case_id="dev-respond-review-001",
        category="review",
        fixture="repo/pr-feedback-v1",
        task_text=(
            "Address reviewer feedback on a pull request — some comments "
            "seem technically questionable — without breaking the tests."
        ),
        relevant_strong=["receiving-code-review"],
        relevant_acceptable=["verification-before-completion"],
        relevant_irrelevant=["canvas-design", "slack-gif-creator"],
        acceptance_tests=["test_feedback_addressed_tests_green"],
        forbidden_actions=["blindly_apply_all_suggestions"],
        baseline_capability_ids=["receiving-code-review"],
    ),
    # --- verification (1) ---------------------------------------------------
    BenchmarkCase(
        case_id="dev-verify-001",
        category="verification",
        fixture="repo/claimed-fix-v1",
        task_text=(
            "Before declaring the bugfix done, verify it end to end: run "
            "the reproduction, the tests, and the original scenario."
        ),
        relevant_strong=["verification-before-completion"],
        relevant_acceptable=["systematic-debugging"],
        relevant_irrelevant=["slack-gif-creator", "brand-guidelines"],
        acceptance_tests=["test_verification_commands_ran"],
        forbidden_actions=["claim_done_without_running"],
        baseline_capability_ids=["verification-before-completion"],
    ),
    # --- frontend / design (3) ----------------------------------------------
    BenchmarkCase(
        case_id="dev-landing-001",
        category="frontend",
        fixture="repo/landing-v2",
        task_text=(
            "Design a new landing page with a modern visual identity: "
            "aesthetic direction, typography, and brand colors."
        ),
        relevant_strong=["frontend-design"],
        relevant_acceptable=["brand-guidelines", "theme-factory"],
        relevant_irrelevant=["tdd", "debugging"],
        acceptance_tests=["test_landing_renders_distinctive"],
        forbidden_actions=["default_bootstrap_look"],
        baseline_capability_ids=["frontend-design"],
    ),
    BenchmarkCase(
        case_id="dev-brand-001",
        category="design",
        fixture="repo/product-brand-v1",
        task_text=(
            "Define brand guidelines for a new product: voice, palette, "
            "typography — applied consistently across artifacts."
        ),
        relevant_strong=["brand-guidelines"],
        relevant_acceptable=["theme-factory"],
        relevant_irrelevant=["tdd", "debugging", "refactoring"],
        acceptance_tests=["test_guidelines_applied"],
        forbidden_actions=["invent_offbrand_colors"],
        baseline_capability_ids=["brand-guidelines"],
    ),
    BenchmarkCase(
        case_id="dev-art-001",
        category="generative",
        fixture="repo/marketing-art-v1",
        task_text=(
            "Create a generative art sketch with p5.js for the marketing "
            "site: seeded randomness, interactive parameters."
        ),
        relevant_strong=["algorithmic-art"],
        relevant_acceptable=["canvas-design"],
        relevant_irrelevant=["tdd", "debugging"],
        acceptance_tests=["test_sketch_seeded_reproducible"],
        forbidden_actions=["unseeded_random"],
        baseline_capability_ids=["algorithmic-art"],
    ),
    # --- platform (2) --------------------------------------------------------
    BenchmarkCase(
        case_id="dev-mcp-server-001",
        category="platform",
        fixture="repo/mcp-tools-v1",
        task_text=(
            "Build a small MCP server exposing three tools over stdio: "
            "well-designed schemas, validation, error handling."
        ),
        relevant_strong=["mcp-builder"],
        relevant_acceptable=["claude-api"],
        relevant_irrelevant=["tdd", "canvas-design"],
        acceptance_tests=["test_tools_discoverable_and_callable"],
        forbidden_actions=["loose_typed_tool_schemas"],
        baseline_capability_ids=["mcp-builder"],
    ),
    BenchmarkCase(
        case_id="dev-claude-api-001",
        category="platform",
        fixture="repo/support-bot-v1",
        task_text=(
            "Integrate the Claude API into a support bot: model choice, "
            "streaming, tool use, and token accounting."
        ),
        relevant_strong=["claude-api"],
        relevant_acceptable=["mcp-builder"],
        relevant_irrelevant=["tdd", "refactoring"],
        acceptance_tests=["test_bot_streams_and_counts_tokens"],
        forbidden_actions=["hardcode_model_ids"],
        baseline_capability_ids=["claude-api"],
    ),
    # --- skill authoring (1) -----------------------------------------------
    BenchmarkCase(
        case_id="dev-skill-author-001",
        category="skill-authoring",
        fixture="repo/new-skill-v1",
        task_text=(
            "Write a new agent skill: SKILL.md structure, progressive "
            "disclosure, references, and validation before deployment."
        ),
        relevant_strong=["writing-skills"],
        relevant_acceptable=["skill-creator"],
        relevant_irrelevant=["tdd", "debugging"],
        acceptance_tests=["test_skill_parses_and_validates"],
        forbidden_actions=["wall_of_text_skill"],
        baseline_capability_ids=["writing-skills"],
    ),
    # --- comms (1) -----------------------------------------------------------
    BenchmarkCase(
        case_id="dev-comms-001",
        category="comms",
        fixture="repo/deploy-announce-v1",
        task_text=(
            "Draft an internal announcement about the new deployment "
            "process: what changes, why, and who is affected."
        ),
        relevant_strong=["internal-comms"],
        relevant_acceptable=["discernment-nudge"],
        relevant_irrelevant=["tdd", "debugging"],
        acceptance_tests=["test_draft_clear_and_actionable"],
        forbidden_actions=["raw_slack_dump"],
        baseline_capability_ids=["internal-comms"],
    ),
]
