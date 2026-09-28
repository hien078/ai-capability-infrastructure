"""Artifact finder + boundary detection (auto2.md §10-11, Phase 3).

Two stages, both ending in PROPOSALS (never capabilities):

1. find_artifact_groups (§10) — deterministic path-pattern scan over a
   repository work tree. Not only SKILL.md: prompts, rules, commands,
   playbooks, workflows, checklists... grouped by directory proximity.

2. BoundaryDetector (§11) — protocol; the intelligence that infers
   "which files form ONE capability". The heuristic default
   (HeuristicBoundaryDetector) is deterministic and honest: a directory
   with a SKILL.md is one candidate (the entrypoint convention), a
   directory of loose markdown files is one candidate per file with
   LOW boundary confidence (the LLM detector refines these later).
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from aci.domain.acquisition.extraction import (
    ArtifactFileRef,
    ArtifactGroup,
    RawCandidate,
)

#: §10 path patterns — reusable intelligence shapes beyond SKILL.md.
_ARTIFACT_PATTERNS = (
    re.compile(r"(^|/)SKILL\.md$", re.IGNORECASE),
    re.compile(
        r"(^|/)(skills|prompts|agents|workflows|rules|commands|playbooks|checklists)(/|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(^|/)(review-guides|debug-procedures|tool-recipes|planning-templates)(/|$)",
        re.IGNORECASE,
    ),
)

#: Files never part of an artifact group (§20 classifier agrees).
_EXCLUDE = re.compile(r"(^|/)(node_modules|\.git|__pycache__)(/|$)|\.pyc$|\.lockb?$")


def _is_artifact(path: str) -> bool:
    if _EXCLUDE.search(path):
        return False
    return any(p.search(path) for p in _ARTIFACT_PATTERNS)


def find_artifact_groups(
    tree: Path,
    *,
    source_repo: str,
    source_path: str,
    observed_revision: str,
) -> list[ArtifactGroup]:
    """Scan a checked-out work tree for artifact groups (§10).

    Deterministic: same tree → same groups. Grouping is by directory —
    files in one directory form one group (directory proximity is the
    cheapest boundary signal; the boundary detector refines from there).
    """
    groups: dict[str, list[ArtifactFileRef]] = {}
    for file in sorted(tree.rglob("*")):
        rel = file.relative_to(tree).as_posix()
        if not file.is_file() or not _is_artifact(rel):
            continue
        import hashlib

        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        directory = rel.rsplit("/", 1)[0] if "/" in rel else ""
        groups.setdefault(directory, []).append(ArtifactFileRef(path=rel, sha256=digest))

    result: list[ArtifactGroup] = []
    for i, (directory, files) in enumerate(sorted(groups.items())):
        role = _suspected_role(directory, files)
        result.append(
            ArtifactGroup(
                group_id=f"raw-group-{i:03d}",
                files=files,
                suspected_role=role,
                source_repo=source_repo,
                source_path=source_path,
                observed_revision=observed_revision,
            )
        )
    return result


def _suspected_role(directory: str, files: list[ArtifactFileRef]) -> str:
    """Cheap role hint from the directory name — a routing hint for the
    boundary detector, never a classification."""
    parts = [p for p in directory.split("/") if p]
    if parts:
        return parts[-1].lower()
    return "repo-root"


class BoundaryDetector(Protocol):
    """§11 boundary inference — pluggable intelligence (§1.3).

    An LLM implementation infers capability boundaries from group content;
    the output is ALWAYS a RawCandidate proposal, never a capability.
    """

    detector_version: str

    def detect(self, group: ArtifactGroup, *, now: datetime) -> list[RawCandidate]: ...


class HeuristicBoundaryDetector:
    """Deterministic floor (§11): entrypoint convention + one-per-file.

    - A group containing SKILL.md → ONE candidate (the entrypoint is the
      boundary; supporting files ride along).
    - A group of loose markdown files → one candidate PER FILE with low
      boundary confidence (0.3): the heuristic cannot know whether three
      loose .md files are one methodology or three; the LLM detector
      refines this, and the low confidence marks the guess honestly.
    """

    detector_version = "heuristic-boundary-detector:1.0.0"

    def __init__(self, *, read: Callable[[Path, str], str] | None = None) -> None:
        # read(tree_root, rel_path) -> file text; injectable for tests.
        self._read = read or _default_read

    def detect(
        self, group: ArtifactGroup, *, tree: Path | None = None, now: datetime | None = None
    ) -> list[RawCandidate]:
        detected_at = now or datetime.now(UTC)
        entry = next((f for f in group.files if f.path.lower().endswith("skill.md")), None)
        if entry is not None:
            name = _capability_name(entry.path)
            return [
                RawCandidate(
                    candidate_id=f"cand-{name}-{entry.sha256[:8]}",
                    group_id=group.group_id,
                    proposed_name=name,
                    primary_files=[entry],
                    supporting_files=[f for f in group.files if f is not entry],
                    inferred_provides=[name.replace("-", "-analysis") if "debug" in name else name],
                    detected_by=self.detector_version,
                    detected_at=detected_at,
                    boundary_confidence=0.9,  # entrypoint convention is a strong signal
                )
            ]
        # No entrypoint: one candidate per markdown file, low confidence.
        out: list[RawCandidate] = []
        for f in group.files:
            if not f.path.lower().endswith((".md", ".markdown")):
                continue
            name = _capability_name(f.path)
            out.append(
                RawCandidate(
                    candidate_id=f"cand-{name}-{f.sha256[:8]}",
                    group_id=group.group_id,
                    proposed_name=name,
                    primary_files=[f],
                    supporting_files=[],
                    inferred_provides=[],
                    detected_by=self.detector_version,
                    detected_at=detected_at,
                    boundary_confidence=0.3,  # loose file — boundary is a guess
                )
            )
        return out


def _default_read(tree: Path, rel: str) -> str:
    return (tree / rel).read_text(encoding="utf-8", errors="replace")


def _capability_name(path: str) -> str:
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if stem.lower() == "skill":
        stem = path.rsplit("/", 2)[-2] if "/" in path else stem
    slug = re.sub(r"[^a-z0-9-]+", "-", stem.lower()).strip("-")
    return slug or "unnamed-candidate"
