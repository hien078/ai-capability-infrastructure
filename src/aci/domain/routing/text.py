"""Text normalization for routing signals (plan §16.1, §17.1).

One canonical tokenizer shared by every routing-side text signal — the
hashing embedder's vectors and the reranker's token-overlap — so docs and
queries always see the same folding. Measured need (§75 step 23, benchmark
run ``smoke-10-clean-catalog``): raw tokens could not bridge even trivial
morphology ("failures" vs "test failure", "tests" vs "test"), capping the
smoke-set recall at tie-luck; folding bridges it deterministically.

The fold is deliberately light: because documents and queries are folded
by the same function, linguistic correctness matters less than
consistency. It stays deterministic and offline (§50: no model gateway).
"""

import re

_TOKEN = re.compile(r"[a-z0-9]+")


def fold_token(token: str) -> str:
    """Fold one lowercase token: plural ``s``/``ies``, progressive ``ing``, past ``ed``.

    Guards keep short words and common non-plural endings (``ss``/``us``/``is``)
    intact so ``class``/``status``/``analysis`` never lose their tail.
    """

    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    return token


def normalize_tokens(text: str) -> list[str]:
    """Lowercase, split, and fold every token of ``text`` (order preserved)."""

    return [fold_token(t) for t in _TOKEN.findall(text.lower())]
