"""Excellence Constitution tests (crawl.md STEP 2, §16-17).

The constitution VERSIONS the definition of excellence: malformed
files fail loudly; the Skeptical Critic is structurally mandatory;
every evaluated type has a profile (a universal rubric is incorrect);
popularity is always very_low.
"""

import pytest

from aci.providers.evaluation.constitution import (
    REQUIRED_JUDGES,
    ConstitutionError,
    importance_of,
    load_constitution,
    load_domain,
    load_profile,
)


def test_constitution_loads_and_versions() -> None:
    c = load_constitution()
    assert c["constitution_version"] == "0.1.0"
    assert "hard_gates" in c and "judges" in c


def test_critic_is_structurally_mandatory() -> None:
    """§18: the Skeptical Critic is required — a constitution without
    it must fail to load (this is the anti-hype counterweight)."""
    assert "critic" in REQUIRED_JUDGES
    assert "critic" in load_constitution()["judges"]["required"]


def test_hard_gates_are_deterministic_never_llm() -> None:
    """§10: license/security/malicious gates are deterministic — the
    constitution records them as fail conditions, not judge dimensions."""
    gates = load_constitution()["hard_gates"]
    for name, action in gates.items():
        assert action == "fail", f"{name} must be a hard fail, not {action!r}"


def test_every_profile_type_has_a_profile() -> None:
    """§16: a universal rubric is incorrect — each evaluated type needs
    its own profile. The shipped profiles cover the corpus's types."""
    for t in ("skill", "agent", "tool", "evaluator"):
        p = load_profile(t)
        assert p["type"] == t
        assert p["dimensions"]


def test_unknown_profile_fails_loudly() -> None:
    with pytest.raises(ConstitutionError, match="no excellence profile"):
        load_profile("not-a-type")


def test_popularity_is_always_very_low() -> None:
    """§30/§67: popularity is deliberately near the bottom in every
    profile — a profile that ranks popularity high must be rejected in
    review; this test makes the shipped ones honest."""
    for t in ("skill", "agent", "tool"):
        assert importance_of(load_profile(t), "popularity") == "very_low"


def test_coding_domain_has_benchmark_families() -> None:
    d = load_domain("coding")
    assert "bug_repair" in d["benchmark_families"]
    assert d["counterfactual_required"] is True  # §50


def test_importance_defaults_to_medium_not_error() -> None:
    assert importance_of(load_profile("skill"), "unknown_dim") == "medium"
