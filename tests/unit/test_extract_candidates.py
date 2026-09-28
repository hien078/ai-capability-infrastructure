"""Artifact finder + boundary detector tests (auto2.md §10-11, Phase 3).

The §11 invariants: 1 file ≠ 1 capability (an entrypoint directory is
ONE candidate with supporting files riding along); loose markdown files
are one candidate EACH with LOW boundary confidence (the heuristic
cannot know if three loose files are one methodology or three — the
LLM detector refines that later); everything is a PROPOSAL, never a
capability.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from aci.application.extract_candidates import (
    HeuristicBoundaryDetector,
    find_artifact_groups,
)
from aci.domain.acquisition.extraction import ArtifactFileRef, ArtifactGroup

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _write(tree: Path, rel: str, content: str = "x") -> Path:
    p = tree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _group(files: list[tuple[str, str]], directory: str = "skills/debugging") -> ArtifactGroup:
    return ArtifactGroup(
        group_id="raw-group-000",
        files=[ArtifactFileRef(path=p, sha256=s) for p, s in files],
        suspected_role="debugging",
        source_repo="https://github.com/example/repo.git",
        source_path="skills/debugging",
        observed_revision="abc123",
    )


# ---------------------------------------------------------------------------
# Artifact finder (§10) — deterministic path scan
# ---------------------------------------------------------------------------


def test_finder_groups_by_directory(tmp_path: Path) -> None:
    _write(tmp_path, "skills/debugging/SKILL.md")
    _write(tmp_path, "skills/debugging/references/guide.md")
    _write(tmp_path, "skills/testing/SKILL.md")
    _write(tmp_path, "src/main.py")  # not an artifact
    _write(tmp_path, "node_modules/junk/SKILL.md")  # excluded
    groups = find_artifact_groups(
        tmp_path, source_repo="r", source_path=".", observed_revision="abc"
    )
    # three artifact DIRECTORIES (debugging, its references/, testing) —
    # directory proximity is the grouping; junk and src excluded.
    assert len(groups) == 3
    assert all("node_modules" not in f.path for g in groups for f in g.files)
    assert all("src/main.py" not in f.path for g in groups for f in g.files)


def test_finder_finds_more_than_skill_md(tmp_path: Path) -> None:
    """§10: the crawler must not search only for SKILL.md — prompts,
    rules, playbooks are artifact material too."""
    _write(tmp_path, "prompts/code-review.md")
    _write(tmp_path, "rules/no-secrets.md")
    _write(tmp_path, "playbooks/release.md")
    groups = find_artifact_groups(
        tmp_path, source_repo="r", source_path=".", observed_revision="abc"
    )
    paths = {f.path for g in groups for f in g.files}
    assert "prompts/code-review.md" in paths
    assert "rules/no-secrets.md" in paths
    assert "playbooks/release.md" in paths


def test_finder_hashes_are_real(tmp_path: Path) -> None:
    _write(tmp_path, "skills/x/SKILL.md", "hello world")
    groups = find_artifact_groups(
        tmp_path, source_repo="r", source_path=".", observed_revision="abc"
    )
    f = groups[0].files[0]
    assert f.sha256 == hashlib.sha256(b"hello world").hexdigest()


# ---------------------------------------------------------------------------
# Boundary detector (§11) — 1 file ≠ 1 capability
# ---------------------------------------------------------------------------


def test_entrypoint_directory_is_one_candidate_with_supporting() -> None:
    """§11 verbatim: debugger.md + root-cause.md + reproduce.md +
    verification.md may be ONE capability. With a SKILL.md entrypoint
    the heuristic treats the directory as one candidate and the other
    files ride along as supporting."""
    group = _group(
        [
            ("skills/debugging/SKILL.md", "a" * 64),
            ("skills/debugging/debugger.md", "b" * 64),
            ("skills/debugging/root-cause.md", "c" * 64),
            ("skills/debugging/reproduce.md", "d" * 64),
        ]
    )
    detector = HeuristicBoundaryDetector()
    out = detector.detect(group, now=NOW)
    assert len(out) == 1
    cand = out[0]
    assert cand.proposed_name == "debugging"
    assert cand.primary_files[0].path == "skills/debugging/SKILL.md"
    assert len(cand.supporting_files) == 3
    assert cand.boundary_confidence == 0.9  # entrypoint convention is strong


def test_loose_files_are_one_candidate_each_low_confidence() -> None:
    """No entrypoint: three loose .md files → three candidates, each with
    LOW boundary confidence — the heuristic honestly cannot know whether
    they are one methodology or three (the LLM detector refines this)."""
    group = _group(
        [
            ("notes/debugger.md", "a" * 64),
            ("notes/root-cause.md", "b" * 64),
            ("notes/reproduce.md", "c" * 64),
        ],
        directory="notes",
    )
    detector = HeuristicBoundaryDetector()
    out = detector.detect(group, now=NOW)
    assert len(out) == 3
    assert all(c.boundary_confidence == 0.3 for c in out)
    assert all(len(c.supporting_files) == 0 for c in out)


def test_non_markdown_files_are_not_candidates_without_entrypoint() -> None:
    group = _group(
        [("assets/logo.png", "a" * 64), ("data/values.json", "b" * 64)],
        directory="assets",
    )
    detector = HeuristicBoundaryDetector()
    assert detector.detect(group, now=NOW) == []


def test_candidate_id_is_digest_stable() -> None:
    """Same files → same candidate ids (idempotency §74): the digest rides
    in the id so re-detecting the same tree never duplicates."""
    group = _group([("skills/x/SKILL.md", "a" * 64)])
    d1 = HeuristicBoundaryDetector().detect(group, now=NOW)
    d2 = HeuristicBoundaryDetector().detect(group, now=NOW)
    assert d1[0].candidate_id == d2[0].candidate_id
    assert d1[0].candidate_id == f"cand-x-{'a' * 8}"


def test_detector_output_is_proposal_not_capability() -> None:
    """The RawCandidate carries no trust: it names its detector (audit)
    and its confidence is about the BOUNDARY GUESS, never quality."""
    group = _group([("skills/x/SKILL.md", "a" * 64)])
    out = HeuristicBoundaryDetector().detect(group, now=NOW)
    assert out[0].detected_by == "heuristic-boundary-detector:1.0.0"
    assert 0.0 <= out[0].boundary_confidence <= 1.0
    assert not hasattr(out[0], "quality_confidence")
