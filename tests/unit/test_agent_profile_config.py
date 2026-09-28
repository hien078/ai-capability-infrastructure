"""Unit tests for AgentProfile deployment config (V3 §56.1).

Profiles are DATA loaded from ACI_AGENT_PROFILES (a JSON file); the loader
validates every record through the domain model — a profile that violates
the delegation-only invariant (§32) must fail configuration, not run.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from aci.adapters.inbound.rest.wiring import _load_agent_profiles

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

PROFILE_RECORD = {
    "profile_id": "coder",
    "version": "1",
    "model_profile": "test-model",
    "skill_policy": {"required": ["test-skill"]},
    "budget": {"max_tokens": 512, "max_wall_time_seconds": 30},
    "execution_policy": {"side_effect_class": "read_only"},
    "created_at": NOW.isoformat(),
}


def test_empty_path_means_no_profiles() -> None:
    assert _load_agent_profiles("") == {}


def test_bare_list_form(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([PROFILE_RECORD]), encoding="utf-8")
    profiles = _load_agent_profiles(str(path))
    assert set(profiles) == {"coder"}
    assert profiles["coder"].model_profile == "test-model"
    assert profiles["coder"].skill_policy.required == ["test-skill"]


def test_wrapped_form(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"profiles": [PROFILE_RECORD]}), encoding="utf-8")
    assert set(_load_agent_profiles(str(path))) == {"coder"}


def test_non_delegated_profile_is_rejected(tmp_path: Path) -> None:
    """§32: an agent profile delegates — anything else fails configuration."""
    bad = {**PROFILE_RECORD, "execution_policy": {"execution_mode": "remote_call"}}
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([bad]), encoding="utf-8")
    with pytest.raises(ValidationError):
        _load_agent_profiles(str(path))


def test_non_list_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"profiles": {"a": 1}}), encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON list"):
        _load_agent_profiles(str(path))
