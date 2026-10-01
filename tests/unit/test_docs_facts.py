"""Docs-vs-code facts guard (docs/plans/aci-improvement-2026-10.md §3.6).

Stale documentation is a recurring defect class in this repo (ADR count,
client count, "sandbox is a non-goal", cleanup status, token caps). This
guard pins the CODE as the source of truth and fails when a doc sentence
STATES a contradicting value.

Rules (deliberate):

- Checked docs: CLAUDE.md, the AGENTS.md status-pointer header (everything
  above its first ``## `` heading — the history below it is an append-only
  record of past states and is never rewritten, so it is NOT checked), and
  docs/architecture-current.md.
- Only machine-verifiable facts are guarded (file counts, constants,
  defaults). Operational facts — real-client count, aci_bench cleanup /
  relations state — live in the database and telemetry and are out of scope.
- A fact ABSENT from a doc is fine: docs need not repeat every fact. Only a
  CONTRADICTING sentence fails, naming the file and the code's value.
- A stale sentence that is QUOTED in order to be corrected
  (docs/architecture-current.md §7 does exactly this) is exempt: every claim
  pattern starts with ``(?<!")`` so a match that begins right after a double
  quote is a quotation, not an assertion.
- ``**`` bold markers are stripped before matching so ``**14**`` reads as 14.

When this test fails on a real doc: fix the DOC sentence (the code is the
truth), not the patterns — unless the code fact itself moved, in which case
update the doc and the synthetic fixtures in the self-test below.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from aci.config import Settings
from aci.domain.capability.models import DEFAULT_MAX_CONTEXT_TOKENS
from aci.routing.composer import VERSION as COMPOSER_VERSION
from aci.routing.rerankers.heuristic import VERSION as RERANKER_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]


def _count_adr_files() -> int:
    """Accepted ADRs on disk: docs/adr/ NNN-*.md files (README.md excluded)."""
    return len(
        [p for p in (REPO_ROOT / "docs" / "adr").iterdir() if re.fullmatch(r"\d{3}-.*\.md", p.name)]
    )


def _latest_migration_number() -> int:
    """Highest NNNN prefix in migrations/versions/ (the alembic head revision)."""
    return max(
        int(m.group(1))
        for p in (REPO_ROOT / "migrations" / "versions").iterdir()
        if (m := re.fullmatch(r"(\d{4})_.*\.py", p.name))
    )


def _agents_md_status_header() -> str:
    """AGENTS.md above its first ``## `` heading — the status pointers only."""
    header: list[str] = []
    for line in (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            break
        header.append(line)
    top = "\n".join(header)
    assert "Status pointer" in top, (
        "AGENTS.md status-pointer header not where _agents_md_status_header() "
        "expects it — update the extraction, do not silently check nothing"
    )
    return top


def _doc_texts() -> dict[str, str]:
    return {
        "CLAUDE.md": (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8"),
        "AGENTS.md (status pointer header)": _agents_md_status_header(),
        "docs/architecture-current.md": (REPO_ROOT / "docs" / "architecture-current.md").read_text(
            encoding="utf-8"
        ),
    }


# --- code facts (the source of truth the docs are checked against) ---

_ADR_COUNT = _count_adr_files()
_LATEST_MIGRATION = _latest_migration_number()
#: Settings field DEFAULTS, not the process env: model_fields is class-level
#: metadata, so an exported ACI_AGENT_SANDBOX=none in the test env cannot
#: masquerade as the default.
_PRELOAD_DEFAULT: bool = Settings.model_fields["agent_capability_preload"].default
_SANDBOX_DEFAULT: str = Settings.model_fields["agent_sandbox"].default
_PRELOAD_STATE = "OFF" if not _PRELOAD_DEFAULT else "ON"
#: The stale phrasing the self-test must flag: the opposite of the default.
_PRELOAD_OPPOSITE = "ON" if _PRELOAD_STATE == "OFF" else "OFF"


class Fact(NamedTuple):
    """A machine-verifiable code fact and the doc sentences that state it."""

    description: str
    source: str  # where the expected value lives in the code/tree
    expected: int | str
    parse: Callable[[str], int | str]
    patterns: tuple[str, ...]  # the stated value is always capture group 1


FACTS: tuple[Fact, ...] = (
    Fact(
        description="accepted ADR count",
        source=f"docs/adr/ NNN-*.md files ({_ADR_COUNT})",
        expected=_ADR_COUNT,
        parse=int,
        patterns=(
            r'(?<!")\b(\d+)\s+accepted\s+ADRs\b',  # "holds 14 accepted ADRs"
            r'(?<!")\b(\d+)\s+ADR\b',  # "14 ADR Accepted"
            r'(?<!")\b\d{3}\s*[–-]\s*(\d{3})\b',  # "(001–014)" — range END
        ),
    ),
    Fact(
        description="latest migration revision",
        source=f"migrations/versions/ (head {_LATEST_MIGRATION:04d})",
        expected=_LATEST_MIGRATION,
        parse=int,
        patterns=(
            r'(?<!")\b\d{4}\s*[–-]\s*(\d{4})\b',  # "migrations 0001–0019" — range END
        ),
    ),
    Fact(
        description="DEFAULT_MAX_CONTEXT_TOKENS",
        source=f"src/aci/domain/capability/models.py (= {DEFAULT_MAX_CONTEXT_TOKENS})",
        expected=DEFAULT_MAX_CONTEXT_TOKENS,
        parse=int,
        patterns=(
            r'(?<!")DEFAULT_MAX_CONTEXT_TOKENS`?[^0-9\n]{0,12}?(\d+)',  # "= 8000" / "(8000)"
            r'(?<!")≤\s*(\d+)\s+token',  # "≤8000 token" (compose budget)
            r'(?<!")mặc\s*định\s*=?\s*(\d{4})',  # Vietnamese "default 8000"
        ),
    ),
    Fact(
        description="reranker VERSION",
        source=f"src/aci/routing/rerankers/heuristic.py (= {RERANKER_VERSION})",
        expected=RERANKER_VERSION,
        parse=str,
        patterns=(
            r'(?<!")[Rr]eranker\s+(?:is\s+)?v(\d+)\b',  # "the reranker is v3"
            r'(?<!")[Rr]erank\s+(?:heuristic\s+)?v(\d+)\b',  # "rerank heuristic v3"
            r'(?<!")heuristic\.py`?[^.\n]{0,40}?VERSION\s*=\s*"(\d+)"',  # VERSION = "3"
        ),
    ),
    Fact(
        description="composer VERSION",
        source=f"src/aci/routing/composer.py (= {COMPOSER_VERSION})",
        expected=COMPOSER_VERSION,
        parse=str,
        patterns=(
            r'(?<!")[Cc]ompose(?:r)?\s+v(\d+)\b',  # "Composer v2" / "compose v2"
        ),
    ),
    Fact(
        description="ACI_AGENT_CAPABILITY_PRELOAD default",
        source=f"src/aci/config.py Settings.agent_capability_preload (= {_PRELOAD_DEFAULT})",
        expected=_PRELOAD_STATE,
        parse=str,
        patterns=(
            r'(?<!")ACI_AGENT_CAPABILITY_PRELOAD`?[^.\n]{0,40}?\b(ON|OFF)\b',
            r'(?<!")\b[Pp]reload\s+(?:stays\s+|is\s+)?\b(ON|OFF)\b',
        ),
    ),
    Fact(
        description="ACI_AGENT_SANDBOX default",
        source=f"src/aci/config.py Settings.agent_sandbox (= {_SANDBOX_DEFAULT!r})",
        expected=_SANDBOX_DEFAULT,
        parse=str,
        patterns=(
            r'(?<!")ACI_AGENT_SANDBOX`?[^.\n]{0,60}?(?:default|mặc\s*định)[^.\n]{0,20}?(\w+)',
        ),
    ),
)

#: Sentences that must never be ASSERTED as current state (quote them to
#: correct them and the ``(?<!")`` guard exempts the quotation).
FORBIDDEN_CLAIMS: tuple[tuple[str, str], ...] = (
    (
        r'(?<!")SandboxWorkspace is a non-goal',
        "superseded by ADR-014 amendment 13: the bwrap sandbox is live and fail-closed",
    ),
)


def _violations(doc: str, text: str) -> list[str]:
    """Every contradicting or forbidden sentence in one doc's text."""
    errors: list[str] = []
    plain = text.replace("**", "")  # markdown bold: **14** reads as 14
    for fact in FACTS:
        for pattern in fact.patterns:
            for match in re.finditer(pattern, plain):
                stated = fact.parse(match.group(1))
                if stated != fact.expected:
                    errors.append(
                        f"{doc}: {fact.description}: sentence {match.group(0)!r} says "
                        f"{stated!r}, expected {fact.expected!r} (code: {fact.source})"
                    )
    for pattern, correction in FORBIDDEN_CLAIMS:
        for match in re.finditer(pattern, plain):
            errors.append(f"{doc}: stale claim {match.group(0)!r} — {correction}")
    return errors


def test_docs_state_current_code_facts() -> None:
    """CLAUDE.md / AGENTS.md status header / architecture-current.md match the code."""
    errors: list[str] = []
    for doc, text in _doc_texts().items():
        errors.extend(_violations(doc, text))
    assert not errors, "stale doc facts:\n" + "\n".join(f"- {e}" for e in errors)


def test_claim_patterns_detect_stale_and_current() -> None:
    """Pin the guard's own machinery — a vacuous guard guards nothing.

    Synthetic stale text (every fact wrong) must flag every fact; synthetic
    current text built from the LIVE code values must pass; quoted stale
    sentences (the correction-table style) must be exempt.
    """
    stale = (
        f"docs/adr/ holds {_ADR_COUNT - 1} accepted ADRs (001-0{_ADR_COUNT - 1:02d}), "
        f"{_ADR_COUNT - 1} ADR accepted; migrations 0001-{_LATEST_MIGRATION - 3:04d}. "
        f"DEFAULT_MAX_CONTEXT_TOKENS = {DEFAULT_MAX_CONTEXT_TOKENS - 2000}, "
        f"≤{DEFAULT_MAX_CONTEXT_TOKENS - 2000} token, "
        f"mặc định {DEFAULT_MAX_CONTEXT_TOKENS - 2000}. "
        f"The reranker is v{int(RERANKER_VERSION) - 1}, "
        f"rerank heuristic v{int(RERANKER_VERSION) - 1}, "
        f'`heuristic.py`, `VERSION = "{int(RERANKER_VERSION) - 1}"`. '
        f"Composer v{int(COMPOSER_VERSION) - 1} budgets. "
        f"ACI_AGENT_CAPABILITY_PRELOAD stays {_PRELOAD_OPPOSITE}, "
        f"preload {_PRELOAD_OPPOSITE}. "
        f"ACI_AGENT_SANDBOX (default: {'none' if _SANDBOX_DEFAULT == 'bwrap' else 'bwrap'}). "
        "SandboxWorkspace is a non-goal."
    )
    violations = _violations("synthetic-stale.md", stale)
    for fact in FACTS:
        assert any(fact.description in v for v in violations), (fact.description, violations)
    assert any("SandboxWorkspace" in v for v in violations), violations

    current = (
        f"docs/adr/ holds {_ADR_COUNT} accepted ADRs (001–0{_ADR_COUNT:02d}), "
        f"{_ADR_COUNT} ADR accepted; migrations 0001–{_LATEST_MIGRATION:04d}. "
        f"DEFAULT_MAX_CONTEXT_TOKENS = {DEFAULT_MAX_CONTEXT_TOKENS}, "
        f"≤{DEFAULT_MAX_CONTEXT_TOKENS} token, mặc định {DEFAULT_MAX_CONTEXT_TOKENS}. "
        f"The reranker is v{RERANKER_VERSION}, rerank heuristic v{RERANKER_VERSION}, "
        f'`heuristic.py`, `VERSION = "{RERANKER_VERSION}"`. '
        f"Composer v{COMPOSER_VERSION} budgets. "
        f"ACI_AGENT_CAPABILITY_PRELOAD stays {_PRELOAD_STATE}, preload {_PRELOAD_STATE}. "
        f"ACI_AGENT_SANDBOX (default: {_SANDBOX_DEFAULT})."
    )
    assert _violations("synthetic-current.md", current) == []

    quoted = (
        'The old doc said "13 accepted ADRs" and "SandboxWorkspace is a non-goal" '
        'and "ACI_AGENT_SANDBOX (default: none)" — quoted here only to correct them.'
    )
    assert _violations("synthetic-quoted.md", quoted) == []
