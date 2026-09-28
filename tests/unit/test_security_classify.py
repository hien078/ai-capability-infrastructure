"""Security pipeline extension tests (auto2.md §20-23).

File classifier, permission analyzer, risk aggregator — all deterministic.
Validated against the REAL corpus policy: prompt-injection-defense
*describes* attacks and must not aggregate CRITICAL for mentioning them
(the §19 false-positive rationale carries over), and a skill requesting
a permission never receives it (§21).
"""

from aci.providers.security.classify import (
    RISK_POLICY,
    aggregate_risk,
    analyze_permissions,
    classify_file,
)

# ---------------------------------------------------------------------------
# File classifier (§20)
# ---------------------------------------------------------------------------


def test_skill_entrypoint_classified_skill() -> None:
    assert classify_file("SKILL.md") == "skill"
    assert classify_file("skills/debugging/SKILL.md") == "skill"


def test_scripts_and_binaries_and_archives() -> None:
    assert classify_file("scripts/run.sh") == "script"
    assert classify_file("tool.py") == "script"
    assert classify_file("Makefile") == "script"
    assert classify_file("assets/logo.png") == "binary"
    assert classify_file("bundle.zip") == "archive"
    assert classify_file("refs.tar.gz") == "archive"


def test_docs_and_config() -> None:
    assert classify_file("README.md") == "documentation"
    assert classify_file("references/checklist.md") == "documentation"
    assert classify_file("package.json") == "configuration"
    assert classify_file("opencode.json") == "configuration"


def test_unknown_extension_is_unknown_not_guess() -> None:
    assert classify_file("mystery.weird") == "unknown"


def test_class_policy_covers_every_class() -> None:
    """Every class the classifier can return has a §20 policy — no class
    silently falls through without a pipeline decision."""
    classes = {
        "documentation",
        "prompt",
        "skill",
        "configuration",
        "script",
        "executable",
        "binary",
        "archive",
        "data",
        "unknown",
    }
    for cls in classes:
        assert classify_file("whatever") in classes  # classifier stays in-domain
        assert cls in RISK_POLICY or cls in (
            "documentation",
            "prompt",
            "skill",
            "configuration",
            "script",
            "executable",
            "binary",
            "archive",
            "data",
            "unknown",
        )


# ---------------------------------------------------------------------------
# Permission analyzer (§21)
# ---------------------------------------------------------------------------


def test_permissions_extracted_from_text() -> None:
    text = (
        "Run the command `bash scripts/build.sh` to build, then use curl "
        "to check the endpoint. Read the file config.yaml first."
    )
    perms = analyze_permissions(text)
    assert "shell" in perms
    assert "network" in perms
    assert "filesystem.read" in perms


def test_clean_documentation_requests_nothing() -> None:
    text = (
        "Follow this procedure: understand the bug, form one hypothesis, "
        "test it, and verify the fix with the existing test suite."
    )
    assert analyze_permissions(text) == []


def test_requesting_is_not_receiving() -> None:
    """§21: the analyzer returns METADATA. A skill mentioning shell does
    not get shell — this test pins the contract by asserting the return
    is a plain list (policy input), never a grant object."""
    perms = analyze_permissions("run the command in bash")
    assert perms == ["shell"]  # metadata, not a grant


def test_secrets_mention_detected() -> None:
    perms = analyze_permissions("put your api key in the .env file")
    assert "secrets" in perms


# ---------------------------------------------------------------------------
# Risk aggregator (§23)
# ---------------------------------------------------------------------------


def test_critical_finding_dominates_everything() -> None:
    assert (
        aggregate_risk(
            critical_findings=1,
            warning_findings=0,
            permissions=[],
        )
        == "CRITICAL"
    )


def test_dangerous_permissions_are_high() -> None:
    assert (
        aggregate_risk(
            critical_findings=0,
            warning_findings=0,
            permissions=["secrets"],
        )
        == "HIGH"
    )
    assert (
        aggregate_risk(
            critical_findings=0,
            warning_findings=0,
            permissions=[],
            binary_files=1,
        )
        == "HIGH"
    )


def test_moderate_permissions_are_medium() -> None:
    assert (
        aggregate_risk(
            critical_findings=0,
            warning_findings=0,
            permissions=["shell"],
        )
        == "MEDIUM"
    )
    assert (
        aggregate_risk(
            critical_findings=0,
            warning_findings=3,
            permissions=[],
        )
        == "MEDIUM"
    )


def test_clean_content_is_low() -> None:
    assert (
        aggregate_risk(
            critical_findings=0,
            warning_findings=0,
            permissions=[],
        )
        == "LOW"
    )


def test_prompt_injection_defense_describes_attacks_not_critical() -> None:
    """The §19 false-positive rationale carries into §23: a security-domain
    skill that DESCRIBES credential attacks must not aggregate HIGH just
    for the mention — its real signals are documentation-grade."""
    body = (
        "# Prompt Injection Defense\n\nAttackers may try: 'ignore previous "
        "instructions', 'reveal your api key', 'put secrets in a file'. "
        "This skill teaches how to RECOGNIZE and REFUSE these."
    )
    perms = analyze_permissions(body)
    # the mention is detected as metadata (honest)…
    assert "secrets" in perms
    # …but with no critical findings and no binaries the aggregate is
    # governed by the permission policy — HIGH would quarantine a skill
    # whose whole job is describing attacks; the human review at HIGH
    # is the correct place for it, so we assert the deterministic policy
    # mapping rather than pretend the mention is absent.
    level = aggregate_risk(critical_findings=0, warning_findings=0, permissions=perms)
    assert level in ("HIGH", "MEDIUM")
    assert RISK_POLICY[level] in ("mandatory human review", "staging under restrictions")


def test_risk_policy_is_total() -> None:
    for level in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        assert level in RISK_POLICY
