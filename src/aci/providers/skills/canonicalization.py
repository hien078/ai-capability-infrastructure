"""Canonicalization (plan §22): normalize source metadata into registry identity.

V1 keeps file bytes untouched; only identity fields are normalized and every
applied transformation is recorded in provenance.local_transformations.
"""

import re

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.skills.models import SkillMetadata

_NON_ID = re.compile(r"[^a-z0-9-]+")
_EDGE_DASHES = re.compile(r"-+")


def canonical_capability_id(name: str) -> str:
    """Derive a stable capability id from the skill display name."""
    slug = _EDGE_DASHES.sub("-", _NON_ID.sub("-", name.strip().lower())).strip("-")
    # Capability.id requires 2-64 chars; reject early with a clean domain error
    # instead of letting pydantic ValidationError leak out of the provider (§45).
    if len(slug) < 2 or not slug[0].isalnum():
        raise DomainError(
            ErrorCode.SKILL_PACKAGE_INVALID,
            f"cannot derive a valid capability id from name {name!r}",
        )
    if len(slug) > 64:
        slug = slug[:64].rstrip("-")
    return slug


def derive_version(metadata: SkillMetadata) -> str:
    """Frontmatter version wins; otherwise the first ingest defaults to 0.1.0."""
    return metadata.version or "0.1.0"


def transformations_for(metadata: SkillMetadata, capability_id: str) -> list[str]:
    applied: list[str] = []
    if metadata.name != capability_id:
        applied.append(f"name-normalized:{metadata.name}->{capability_id}")
    if metadata.version is None:
        applied.append("version-defaulted:0.1.0")
    return applied
