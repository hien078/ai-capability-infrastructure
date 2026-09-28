"""Excellence Constitution loader (crawl.md STEP 2, §17).

The constitution VERSIONS the definition of excellence: profiles per
capability type, domains per task family, hard gates deterministic,
anti-hype rules global. This loader validates the files at load time —
a malformed constitution must fail loudly, never silently govern.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
CONSTITUTION_ROOT = REPO_ROOT / "policies" / "excellence"

#: §18: the Skeptical Critic is mandatory in every evaluation.
REQUIRED_JUDGES = ("architecture", "engineering", "novelty", "capability_gain", "critic")


class ConstitutionError(Exception):
    """The constitution files are malformed — fail loudly (§17)."""


@lru_cache(maxsize=1)
def load_constitution() -> dict[str, Any]:
    path = CONSTITUTION_ROOT / "constitution.yaml"
    if not path.is_file():
        raise ConstitutionError(f"constitution not found at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConstitutionError("constitution.yaml is not a mapping")
    for key in ("constitution_version", "hard_gates", "judges"):
        if key not in data:
            raise ConstitutionError(f"constitution.yaml missing {key!r}")
    for judge in REQUIRED_JUDGES:
        if judge not in data["judges"].get("required", []):
            raise ConstitutionError(
                f"judge {judge!r} missing from required judges (§18: the "
                "Skeptical Critic is mandatory, never optional)"
            )
    return data


@lru_cache(maxsize=16)
def load_profile(capability_type: str) -> dict[str, Any]:
    path = CONSTITUTION_ROOT / "profiles" / f"{capability_type}.yaml"
    if not path.is_file():
        raise ConstitutionError(
            f"no excellence profile for type {capability_type!r} (§16: a "
            "universal rubric is incorrect — every evaluated type needs one)"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.get("type") != capability_type:
        raise ConstitutionError(
            f"profile {path.name}: declared type {data.get('type')!r} != filename"
        )
    if "dimensions" not in data:
        raise ConstitutionError(f"profile {path.name}: no dimensions")
    return dict(data)


@lru_cache(maxsize=16)
def load_domain(domain: str) -> dict[str, Any]:
    path = CONSTITUTION_ROOT / "domains" / f"{domain}.yaml"
    if not path.is_file():
        raise ConstitutionError(f"no excellence domain config for {domain!r}")
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def importance_of(profile: dict[str, Any], dimension: str) -> str:
    """The importance of one dimension in one profile (§16)."""
    return str(profile.get("dimensions", {}).get(dimension, {}).get("importance", "medium"))
