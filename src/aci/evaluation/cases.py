"""The 10-task smoke benchmark (plan §§35.1, 70; §75 step 22).

Synthetic fixtures across categories — enough to exercise the variant
matrix end to end, never enough for architecture-wide conclusions (§35.1:
"do not make architecture-wide conclusions from ten tasks"). The
``relevant_*`` annotations name capability ids a deployment would plausibly
have; they help evaluate routing but never define the only acceptable
solution path (§35). Grow to 30–50 only after V1 value is measured (§75).
"""

from aci.evaluation.models import BenchmarkCase

SMOKE_CASES: list[BenchmarkCase] = [
    BenchmarkCase(
        case_id="auth-race-001",
        category="debugging",
        fixture="repo/auth-race-v1",
        task_text=(
            "Fix intermittent authentication failures under concurrent refresh: "
            "sessions occasionally survive logout and refresh tokens race."
        ),
        relevant_strong=["systematic-debugging", "concurrency-analysis"],
        relevant_acceptable=["auth-debugging"],
        relevant_irrelevant=["css-layout"],
        acceptance_tests=["test_refresh_race", "test_no_duplicate_session"],
        forbidden_actions=["disable_authentication", "delete_tests"],
        baseline_capability_ids=["systematic-debugging"],
    ),
    BenchmarkCase(
        case_id="migrate-pg-001",
        category="refactoring",
        fixture="repo/shop-sqlite-v3",
        task_text=(
            "Migrate the shop service from SQLite to PostgreSQL without downtime: "
            "schema, connection pooling, and a reversible cutover plan."
        ),
        relevant_strong=["database-migration"],
        relevant_acceptable=["sql-diagnostics"],
        relevant_irrelevant=["css-layout", "release-notes"],
        acceptance_tests=["test_migration_roundtrip", "test_orders_readwrite"],
        forbidden_actions=["drop_orders_table"],
        baseline_capability_ids=["database-migration"],
    ),
    BenchmarkCase(
        case_id="flake-tests-001",
        category="testing",
        fixture="repo/ci-flakes-v2",
        task_text=(
            "Quarantine and fix flaky integration tests: identify time-dependent "
            "and order-dependent tests and make the suite green on reruns."
        ),
        relevant_strong=["test-hygiene"],
        relevant_acceptable=["systematic-debugging"],
        relevant_irrelevant=["css-layout"],
        acceptance_tests=["test_suite_rerun_green"],
        forbidden_actions=["delete_tests", "skip_all_tests"],
        baseline_capability_ids=["test-hygiene"],
    ),
    BenchmarkCase(
        case_id="secrets-leak-001",
        category="security",
        fixture="repo/api-keys-v1",
        task_text=(
            "Rotate leaked API keys found in the repository history and prevent "
            "future commits from including secrets."
        ),
        relevant_strong=["secret-scanning", "incident-response"],
        relevant_acceptable=["git-hygiene"],
        relevant_irrelevant=["css-layout", "release-notes"],
        acceptance_tests=["test_no_secrets_in_history", "test_precommit_blocks_secrets"],
        forbidden_actions=["commit_new_secrets"],
        baseline_capability_ids=["secret-scanning"],
    ),
    BenchmarkCase(
        case_id="slow-query-001",
        category="performance",
        fixture="repo/reporting-v4",
        task_text=(
            "The nightly reporting job takes 40 minutes; profile the slow "
            "queries and bring the run under five minutes."
        ),
        relevant_strong=["query-optimization"],
        relevant_acceptable=["sql-diagnostics", "load-profiling"],
        relevant_irrelevant=["css-layout"],
        acceptance_tests=["test_reporting_under_five_minutes"],
        forbidden_actions=["disable_reporting"],
        baseline_capability_ids=["query-optimization"],
    ),
    BenchmarkCase(
        case_id="dep-drift-001",
        category="maintenance",
        fixture="repo/legacy-deps-v2",
        task_text=(
            "Audit outdated dependencies with known CVEs and upgrade the "
            "vulnerable ones with minimal API breakage."
        ),
        relevant_strong=["dependency-audit"],
        relevant_acceptable=["release-notes"],
        relevant_irrelevant=["css-layout"],
        acceptance_tests=["test_no_known_cves", "test_public_api_unchanged"],
        forbidden_actions=["downgrade_runtime"],
        baseline_capability_ids=["dependency-audit"],
    ),
    BenchmarkCase(
        case_id="api-docs-001",
        category="documentation",
        fixture="repo/public-api-v1",
        task_text=(
            "The public REST API has drifted from its docs; regenerate accurate "
            "OpenAPI descriptions with working examples."
        ),
        relevant_strong=["api-documentation"],
        relevant_acceptable=["release-notes"],
        relevant_irrelevant=["query-optimization"],
        acceptance_tests=["test_openapi_validates", "test_examples_execute"],
        forbidden_actions=["delete_docs"],
        baseline_capability_ids=["api-documentation"],
    ),
    BenchmarkCase(
        case_id="retry-storm-001",
        category="debugging",
        fixture="repo/payments-v5",
        task_text=(
            "Downstream payment provider outages cause retry storms; add "
            "exponential backoff with jitter and a circuit breaker."
        ),
        relevant_strong=["resilience-patterns", "concurrency-analysis"],
        relevant_acceptable=["systematic-debugging"],
        relevant_irrelevant=["css-layout"],
        acceptance_tests=["test_backoff_under_load", "test_breaker_opens"],
        forbidden_actions=["disable_retries", "disable_circuit_breaker"],
        baseline_capability_ids=["resilience-patterns"],
    ),
    BenchmarkCase(
        case_id="i18n-glyphs-001",
        category="refactoring",
        fixture="repo/storefront-i18n-v1",
        task_text=(
            "User-entered product names render as mojibake in some locales; "
            "normalize text handling end to end."
        ),
        relevant_strong=["i18n-text-handling"],
        relevant_acceptable=["css-layout"],
        relevant_irrelevant=["query-optimization"],
        acceptance_tests=["test_unicode_roundtrip", "test_locale_rendering"],
        forbidden_actions=["strip_user_input"],
        baseline_capability_ids=["i18n-text-handling"],
    ),
    BenchmarkCase(
        case_id="onboarding-doc-001",
        category="documentation",
        fixture="repo/contrib-v2",
        task_text=(
            "New contributors cannot run the test suite locally; write a "
            "reproducible setup guide from a clean checkout."
        ),
        relevant_strong=["onboarding-docs"],
        relevant_acceptable=["test-hygiene"],
        relevant_irrelevant=["secret-scanning"],
        acceptance_tests=["test_clean_checkout_green"],
        forbidden_actions=["commit_env_secrets"],
        baseline_capability_ids=["onboarding-docs"],
    ),
]
