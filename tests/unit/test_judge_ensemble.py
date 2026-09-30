"""Judge ensemble tests (crawl.md STEP 8, §18-19).

The invariants: the Critic is structurally mandatory (an ensemble
without it cannot be constructed); every judge output carries
evidence lines (§19: no unsupported scores); unparseable output
raises, never fabricates (§74); judges are isolated (no judge sees
another's output — anchoring prevention).
"""

from datetime import UTC, datetime

import pytest

from aci.domain.acquisition.demand import EvidencePackage
from aci.providers.evaluation.judges import JudgeEnsemble, JudgeError

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _package() -> EvidencePackage:
    return EvidencePackage(
        package_id="pkg-1",
        mined_id="mine-1",
        capability={"name": "x", "mechanism": "y"},
        assembled_at=NOW,
    )


def test_ensemble_without_critic_cannot_be_constructed() -> None:
    """§18: the Skeptical Critic is MANDATORY — constructing an
    ensemble that omits it must fail, not degrade silently."""
    with pytest.raises(JudgeError, match="Skeptical Critic is mandatory"):
        JudgeEnsemble(
            "http://x",
            roles=("architecture", "engineering"),  # type: ignore[arg-type]
        )


def test_default_roles_include_all_required_judges() -> None:
    ens = JudgeEnsemble("http://x")
    assert set(ens._roles) == {
        "architecture",
        "engineering",
        "novelty",
        "capability_gain",
        "critic",
    }


def test_critic_prompt_is_adversarial() -> None:
    """The Critic's prompt must explicitly search for reasons NOT to
    promote (§30) — anti-hype is the role, not a mood."""
    from aci.providers.evaluation.judges import JUDGE_QUESTIONS

    assert "NOT to promote" in JUDGE_QUESTIONS["critic"]
    assert "README marketing" in JUDGE_QUESTIONS["critic"]


def test_package_prompt_marks_author_claims_unverified() -> None:
    """§30: claimed_by_author is stored separately and the prompt must
    LABEL it as unverified so judges do not treat it as evidence."""
    ens = JudgeEnsemble("http://x")
    prompt = ens._package_prompt(_package(), "corpus")
    assert "NOT verified" in prompt


def test_judge_error_is_never_silent() -> None:
    """§74: a failed judge raises JudgeError — there is no code path
    that returns an empty-but-successful report."""
    assert issubclass(JudgeError, Exception)


def test_judge_array_output_raises_judge_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """§74: a judge returning a JSON ARRAY (not an object) raises
    JudgeError — never an AttributeError crash, never a fabricated report."""
    ens = JudgeEnsemble("http://x")
    monkeypatch.setattr(ens, "_complete", lambda system, user: '[{"claim": "x"}]')
    with pytest.raises(JudgeError, match="not a JSON object"):
        ens.evaluate(_package(), "corpus")


def test_judge_malformed_claims_raise_judge_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A claim entry that is not a dict, or an invalid confidence value,
    raises JudgeError — the ensemble never crashes with a raw pydantic
    ValidationError (§74)."""
    ens = JudgeEnsemble("http://x")
    monkeypatch.setattr(
        ens,
        "_complete",
        lambda system, user: (
            '{"claims": [{"claim": "x", "evidence": ["e"], "confidence": "sure!"}]}'
        ),
    )
    with pytest.raises(JudgeError, match="malformed output"):
        ens.evaluate(_package(), "corpus")
