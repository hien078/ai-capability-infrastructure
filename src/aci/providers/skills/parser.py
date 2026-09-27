"""SKILL.md parser: YAML frontmatter + markdown body (plan §20).

Only safe_load; frontmatter must be a mapping with non-empty name/description.
The body is never interpreted here — it is hashed and stored as a blob.
"""

import re

import yaml

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.skills.models import SkillMetadata

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _invalid(detail: str) -> DomainError:
    return DomainError(ErrorCode.SKILL_PACKAGE_INVALID, f"invalid SKILL.md: {detail}")


def parse_skill_md(text: str) -> SkillMetadata:
    match = _FRONTMATTER.match(text)
    if match is None:
        raise _invalid("missing YAML frontmatter delimited by ---")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise _invalid(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise _invalid("frontmatter must be a mapping")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _invalid("'name' is required and must be a non-empty string")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise _invalid("'description' is required and must be a non-empty string")

    version = data.get("version")
    if version is not None:
        if not isinstance(version, str) or not _SEMVER.match(version):
            raise _invalid(f"'version' must match X.Y.Z, got {version!r}")

    license_ = data.get("license")
    if license_ is not None and not isinstance(license_, str):
        raise _invalid("'license' must be a string")

    provides = data.get("provides", [])
    if not isinstance(provides, list) or not all(isinstance(p, str) for p in provides):
        raise _invalid("'provides' must be a list of strings")

    return SkillMetadata(
        name=name.strip(),
        description=description.strip(),
        version=version,
        license=license_,
        provides=provides,
    )
