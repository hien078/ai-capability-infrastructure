"""Agent platform domain contracts (V3 §56, §30.1, §32) — pure model tests."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aci.domain.agent import (
    AgentProfile,
    AgentTask,
    ExecutionPolicy,
    ProfileBudget,
    SkillPolicy,
    TaskArtifact,
    TaskMessage,
    advance_task,
)
from aci.domain.capability.errors import DomainError, ErrorCode

NOW = datetime(2026, 9, 28, tzinfo=UTC)
LATER = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _profile(**overrides: object) -> AgentProfile:
    """A valid reviewer-shaped profile (§56.1 example)."""
    payload: dict[str, object] = {
        "profile_id": "reviewer",
        "version": "1.0.0",
        "model_profile": "reasoning-medium",
        "allowed_tools": ["repository.read", "tests.read"],
        "skill_policy": SkillPolicy(required=["code-review"]),
        "budget": ProfileBudget(max_tokens=200_000, max_wall_time_seconds=600),
        "execution_policy": ExecutionPolicy(
            side_effect_class="read_only", can_write_repository=False
        ),
        "created_at": NOW,
    }
    payload.update(overrides)
    return AgentProfile.model_validate(payload)


def _task(**overrides: object) -> AgentTask:
    payload: dict[str, object] = {
        "task_id": "task-1",
        "profile_id": "reviewer",
        "capability_id": "code-reviewer-agent",
        "input_text": "Review PR #42 for correctness.",
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(overrides)
    return AgentTask.model_validate(payload)


class TestAgentProfile:
    def test_profile_is_frozen_data_not_subsystem(self) -> None:
        profile = _profile()
        with pytest.raises(ValidationError):
            profile.model_profile = "changed"  # type: ignore[misc]
        assert profile.model_config["frozen"] is True

    def test_delegation_is_the_only_agent_execution_mode(self) -> None:
        # §32: agent = delegated_task. Other modes belong to other kinds.
        with pytest.raises(ValidationError, match="delegated_task"):
            _profile(
                execution_policy=ExecutionPolicy(
                    execution_mode="remote_call", side_effect_class="read_only"
                )
            )

    def test_budgets_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _profile(budget=ProfileBudget(max_tokens=0, max_wall_time_seconds=600))

    def test_skill_policy_holds_capability_ids_not_nested_skills(self) -> None:
        profile = _profile(skill_policy=SkillPolicy(required=["code-review"], optional=["tdd"]))
        assert profile.skill_policy.required == ["code-review"]
        assert profile.skill_policy.optional == ["tdd"]


class TestAgentTaskLifecycle:
    def test_new_task_starts_submitted(self) -> None:
        assert _task().status == "submitted"

    def test_happy_path_lifecycle(self) -> None:
        task = _task()
        task = advance_task(task, "working", now=LATER)
        assert task.status == "working" and task.updated_at == LATER
        task = advance_task(task, "completed", now=LATER)
        assert task.status == "completed"

    def test_needs_input_round_trip(self) -> None:
        task = advance_task(advance_task(_task(), "working", now=LATER), "needs_input", now=LATER)
        task = advance_task(task, "working", now=LATER)
        assert task.status == "working"

    def test_illegal_transition_raises_domain_error(self) -> None:
        with pytest.raises(DomainError) as exc:
            advance_task(_task(), "completed", now=LATER)  # submitted -> completed: no
        assert exc.value.code == ErrorCode.TASK_TRANSITION_INVALID

    def test_terminal_states_are_immutable(self) -> None:
        for terminal in ("completed", "failed", "canceled"):
            task = advance_task(advance_task(_task(), "working", now=LATER), terminal, now=LATER)
            with pytest.raises(DomainError) as exc:
                advance_task(task, "working", now=LATER)
            assert exc.value.code == ErrorCode.TASK_TRANSITION_INVALID

    def test_advance_returns_new_instance_never_mutates(self) -> None:
        task = _task()
        _ = advance_task(task, "working", now=LATER)
        assert task.status == "submitted"  # original untouched


class TestTaskMessagesAndArtifacts:
    def test_message_author_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            TaskMessage(
                message_id="m-1",
                task_id="task-1",
                author="system",  # type: ignore[arg-type] — only agent|caller (§30.1)
                content="hi",
                created_at=NOW,
            )

    def test_artifact_digest_is_sha256_pinned(self) -> None:
        with pytest.raises(ValidationError):
            TaskArtifact(
                artifact_id="a-1",
                task_id="task-1",
                name="review.md",
                digest="md5:abc",
                created_at=NOW,
            )
