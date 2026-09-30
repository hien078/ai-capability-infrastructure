"""GuardrailManager tests (harness.md §14): pre/post tool checks, first BLOCK wins."""

from __future__ import annotations

from typing import Any

from aci.domain.runtime.tools import ToolObservation, ToolSpec
from aci.runtime.guardrails import (
    GuardrailManager,
    GuardrailResult,
    OutputSchemaGuard,
    PathTraversalGuard,
    SecretLeakGuard,
    ShellInjectionGuard,
)


def _tool(**over: Any) -> ToolSpec:
    base: dict[str, Any] = {"tool_id": "t1", "version": "1"}
    base.update(over)
    return ToolSpec(**base)


def _obs(inline: str = "", summary: str = "") -> ToolObservation:
    return ToolObservation(tool_call_id="c1", tool_id="t1", inline_output=inline, summary=summary)


def _manager() -> GuardrailManager:
    return GuardrailManager(
        pre_guardrails=[ShellInjectionGuard(), PathTraversalGuard()],
        post_guardrails=[SecretLeakGuard(), OutputSchemaGuard()],
    )


class TestShellInjectionGuard:
    def test_command_substitution_blocked(self) -> None:
        result = ShellInjectionGuard().check(_tool(), {"command": "echo $( cat /etc/passwd )"})
        assert result.status == "BLOCK"
        assert result.guardrail == "shell_injection"

    def test_backticks_blocked(self) -> None:
        result = ShellInjectionGuard().check(_tool(), {"command": "echo `whoami`"})
        assert result.status == "BLOCK"

    def test_semicolon_rm_rf_blocked(self) -> None:
        result = ShellInjectionGuard().check(_tool(), {"command": "ls; rm -rf /"})
        assert result.status == "BLOCK"

    def test_pipe_to_sudo_blocked(self) -> None:
        result = ShellInjectionGuard().check(_tool(), {"command": "cat secret | sudo tee /etc/x"})
        assert result.status == "BLOCK"

    def test_benign_command_passes(self) -> None:
        result = ShellInjectionGuard().check(_tool(), {"command": "git status --short"})
        assert result.status == "PASS"

    def test_non_command_args_ignored(self) -> None:
        result = ShellInjectionGuard().check(_tool(), {"path": "/tmp/$(evil)"})
        assert result.status == "PASS"


class TestPathTraversalGuard:
    def test_dotdot_blocked(self) -> None:
        result = PathTraversalGuard().check(_tool(), {"path": "/workspace/../etc/passwd"})
        assert result.status == "BLOCK"
        assert result.guardrail == "path_traversal"

    def test_relative_dotdot_blocked(self) -> None:
        result = PathTraversalGuard().check(_tool(), {"file": "notes/../../secrets"})
        assert result.status == "BLOCK"

    def test_absolute_outside_scope_blocked(self) -> None:
        result = PathTraversalGuard().check(_tool(), {"path": "/etc/passwd"})
        assert result.status == "BLOCK"

    def test_within_scope_passes(self) -> None:
        result = PathTraversalGuard().check(_tool(), {"path": "/workspace/src/main.py"})
        assert result.status == "PASS"

    def test_relative_path_passes(self) -> None:
        result = PathTraversalGuard().check(_tool(), {"path": "src/main.py"})
        assert result.status == "PASS"


class TestSecretLeakGuard:
    def test_aws_key_blocked(self) -> None:
        guard = SecretLeakGuard()
        result = guard.check(_tool(), {}, _obs(inline="export KEY=AKIAIOSFODNN7EXAMPLE"))
        assert result.status == "BLOCK"

    def test_private_key_blocked(self) -> None:
        guard = SecretLeakGuard()
        result = guard.check(
            _tool(), {}, _obs(inline="-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END KEY-----")
        )
        assert result.status == "BLOCK"

    def test_sk_token_blocked(self) -> None:
        guard = SecretLeakGuard()
        result = guard.check(_tool(), {}, _obs(inline="sk-abcdefghijklmnopqrstuvwx"))
        assert result.status == "BLOCK"

    def test_token_assignment_blocked(self) -> None:
        guard = SecretLeakGuard()
        result = guard.check(_tool(), {}, _obs(inline="token=abcdef0123456789abcdef"))
        assert result.status == "BLOCK"

    def test_clean_output_passes(self) -> None:
        guard = SecretLeakGuard()
        result = guard.check(_tool(), {}, _obs(inline="all tests passed"))
        assert result.status == "PASS"


class TestOutputSchemaGuard:
    def _schema_tool(self) -> ToolSpec:
        return _tool(
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}, "count": {"type": "integer"}},
                "required": ["ok", "count"],
            }
        )

    def test_missing_required_key_warns(self) -> None:
        guard = OutputSchemaGuard()
        result = guard.check(self._schema_tool(), {}, _obs(inline='{"ok": true}'))
        assert result.status == "WARN"
        assert "count" in result.reason

    def test_shape_match_passes(self) -> None:
        guard = OutputSchemaGuard()
        result = guard.check(self._schema_tool(), {}, _obs(inline='{"ok": true, "count": 3}'))
        assert result.status == "PASS"

    def test_no_schema_passes(self) -> None:
        guard = OutputSchemaGuard()
        result = guard.check(_tool(), {}, _obs(inline="plain text"))
        assert result.status == "PASS"


class TestGuardrailManager:
    def test_pre_tool_first_block_wins(self) -> None:
        manager = _manager()
        result = manager.check_pre_tool(_tool(), {"command": "ls; rm -rf /", "path": "/etc/passwd"})
        assert result.status == "BLOCK"
        assert result.guardrail == "shell_injection"

    def test_pre_tool_pass_when_clean(self) -> None:
        manager = _manager()
        result = manager.check_pre_tool(_tool(), {"command": "ls", "path": "/workspace/a"})
        assert result.status == "PASS"

    def test_post_tool_secret_leak_blocks(self) -> None:
        manager = _manager()
        result = manager.check_post_tool(
            _tool(), {"command": "cat /workspace/.env"}, _obs(inline="AKIAIOSFODNN7EXAMPLE")
        )
        assert result.status == "BLOCK"
        assert result.guardrail == "secret_leak"

    def test_post_tool_schema_warn_passes_through(self) -> None:
        manager = _manager()
        tool = _tool(output_schema={"type": "object", "properties": {"a": {}}, "required": ["a"]})
        result = manager.check_post_tool(tool, {}, _obs(inline="nothing here"))
        # WARN does not block; manager returns PASS after all guardrails run.
        assert result.status == "PASS"

    def test_registration_order_extension(self) -> None:
        manager = GuardrailManager()
        manager.register_pre(PathTraversalGuard())
        result = manager.check_pre_tool(_tool(), {"path": "/etc/passwd"})
        assert result.status == "BLOCK"

    def test_result_contract_frozen(self) -> None:
        result = GuardrailResult(status="PASS", reason="ok")
        try:
            result.status = "BLOCK"  # type: ignore[misc]
        except Exception:
            pass
        else:
            raise AssertionError("GuardrailResult must be frozen")
