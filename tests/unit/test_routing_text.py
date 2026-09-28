"""Unit tests for the shared routing text normalizer (§16.1/§17.1 v2 fold).

The fold is what lets the offline hashing embedder and the reranker's
token-overlap bridge morphology ("failures" vs "failure") — measured need
from benchmark run smoke-10-clean-catalog (§75 step 23).
"""

from aci.adapters.outbound.model_provider.hashing import HashingEmbedder
from aci.domain.routing.text import fold_token, normalize_tokens


def test_fold_plural_s() -> None:
    assert fold_token("failures") == "failure"
    assert fold_token("tests") == "test"
    assert fold_token("sessions") == "session"


def test_fold_ies_to_y() -> None:
    assert fold_token("dependencies") == "dependency"
    assert fold_token("ies") == "ies"  # too short to fold


def test_fold_ing_ed() -> None:
    assert fold_token("testing") == "test"
    assert fold_token("debugging") == "debugg"
    assert fold_token("fixed") == "fix"


def test_fold_guards_non_plural_endings() -> None:
    assert fold_token("class") == "class"  # ss
    assert fold_token("status") == "status"  # us
    assert fold_token("analysis") == "analysis"  # is
    assert fold_token("bug") == "bug"  # no suffix
    assert fold_token("is") == "is"  # too short


def test_normalize_tokens_lowercases_splits_and_folds_in_order() -> None:
    assert normalize_tokens("Fix intermittent FAILURES, quickly!") == [
        "fix",
        "intermittent",
        "failure",
        "quickly",
    ]


def test_embedder_bridges_morphology() -> None:
    """The v2 fold makes "failures" match "failure" in vector space."""

    def cosine(u: list[float], v: list[float]) -> float:
        return sum(x * y for x, y in zip(u, v, strict=True))

    embedder = HashingEmbedder()
    query = embedder.embed(["fix intermittent authentication failures"])[0]
    close = embedder.embed(["use when encountering any bug, test failure"])[0]
    far = embedder.embed(["brand guidelines for corporate typography"])[0]
    assert cosine(query, close) > 0.0
    assert cosine(query, close) > cosine(query, far)


def test_embedder_deterministic_across_instances() -> None:
    a = HashingEmbedder().embed(["root cause tracing"])[0]
    b = HashingEmbedder().embed(["root cause tracing"])[0]
    assert a == b
