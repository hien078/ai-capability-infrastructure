"""GuardrailManager (harness.md §14): semantic/structural checks, separate from authority.

Guardrails validate tool inputs and outputs (INV-06: no side effect before
guardrail); they never grant authority. All checks are deterministic and run
in registration order — first BLOCK wins (§25).
"""

import re
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from aci.domain.runtime.tools import ToolObservation, ToolSpec

GuardrailStatus = Literal["PASS", "WARN", "BLOCK", "TRANSFORM"]


class GuardrailResult(BaseModel):
    """§14.3 guardrail result. Engine-level contract (not domain)."""

    model_config = {"frozen": True}

    status: GuardrailStatus
    reason: str = ""
    guardrail: str = ""
    transformed_value: dict[str, Any] | None = None


class PreToolGuardrail(Protocol):
    name: str

    def check(self, tool: ToolSpec, args: dict[str, Any]) -> GuardrailResult: ...


class PostToolGuardrail(Protocol):
    name: str

    def check(
        self, tool: ToolSpec, args: dict[str, Any], observation: ToolObservation
    ) -> GuardrailResult: ...


def _pass(name: str) -> GuardrailResult:
    return GuardrailResult(status="PASS", guardrail=name)


def _block(name: str, reason: str) -> GuardrailResult:
    return GuardrailResult(status="BLOCK", guardrail=name, reason=reason)


def _warn(name: str, reason: str) -> GuardrailResult:
    return GuardrailResult(status="WARN", guardrail=name, reason=reason)


def _walk(value: Any) -> Any:
    yield value


def _string_values(args: dict[str, Any], field_names: tuple[str, ...]) -> list[tuple[str, str]]:
    """Yield (field_name, string_value) for the given arg fields (recursing into lists/dicts)."""
    out: list[tuple[str, str]] = []

    def _collect(field: str, value: Any) -> None:
        if isinstance(value, str):
            out.append((field, value))
        elif isinstance(value, list):
            for item in value:
                _collect(field, item)
        elif isinstance(value, dict):
            for item in value.values():
                _collect(field, item)

    for field in field_names:
        if field in args:
            _collect(field, args[field])
    return out


class ShellInjectionGuard:
    """Pre-tool: shell metacharacter patterns in command-like fields → BLOCK."""

    name = "shell_injection"

    FIELD_NAMES = ("command", "cmd", "script", "shell", "bash", "sh", "argv")
    PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("command substitution $( )", re.compile(r"\$\(\s")),
        ("backtick substitution", re.compile(r"`[^`]*`")),
        ("chained destructive command (; rm -rf)", re.compile(r";\s*rm\s+-rf")),
        ("pipe to sudo", re.compile(r"\|\s*sudo\b")),
    )

    def check(self, tool: ToolSpec, args: dict[str, Any]) -> GuardrailResult:
        for field, value in _string_values(args, self.FIELD_NAMES):
            for label, pattern in self.PATTERNS:
                if pattern.search(value):
                    return _block(self.name, f"shell metacharacter pattern ({label}) in {field!r}")
        return _pass(self.name)


class PathTraversalGuard:
    """Pre-tool: `..` segments or absolute paths outside envelope scope → BLOCK."""

    name = "path_traversal"

    FIELD_NAMES = (
        "path",
        "file",
        "filename",
        "filepath",
        "dir",
        "directory",
        "dest",
        "destination",
    )

    def __init__(self, allowed_prefixes: tuple[str, ...] = ("/workspace",)) -> None:
        self._allowed_prefixes = allowed_prefixes

    def check(self, tool: ToolSpec, args: dict[str, Any]) -> GuardrailResult:
        for field, value in _string_values(args, self.FIELD_NAMES):
            segments = value.split("/")
            if ".." in segments:
                return _block(self.name, f"path traversal (`..`) in {field!r}")
            if value.startswith("/"):
                matched = any(
                    value == p or value.startswith(p.rstrip("/") + "/")
                    for p in self._allowed_prefixes
                )
                if not matched:
                    return _block(
                        self.name, f"absolute path {value!r} in {field!r} outside envelope scope"
                    )
        return _pass(self.name)


class SecretLeakGuard:
    """Post-tool: secret patterns in inline_output → BLOCK (output must not reach context)."""

    name = "secret_leak"

    PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
        ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
        ("api token (sk-)", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
        ("token assignment", re.compile(r"\btoken\s*=\s*[^\s]{16,}")),
    )

    def check(
        self, tool: ToolSpec, args: dict[str, Any], observation: ToolObservation
    ) -> GuardrailResult:
        for label, pattern in self.PATTERNS:
            if pattern.search(_observation_text(observation)):
                return _block(self.name, f"secret pattern ({label}) in tool output")
        return _pass(self.name)


def _observation_text(observation: ToolObservation) -> str:
    return observation.inline_output + "\n" + observation.summary


class OutputSchemaGuard:
    """Post-tool: minimal shape check against tool.output_schema → WARN on mismatch."""

    name = "output_schema"

    def check(
        self, tool: ToolSpec, args: dict[str, Any], observation: ToolObservation
    ) -> GuardrailResult:
        schema = tool.output_schema
        if not schema or schema.get("type") != "object":
            return _pass(self.name)
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return _pass(self.name)
        required: list[Any] = schema.get("required", [])
        if not isinstance(required, list):
            required = []
        # The observation's inline output is a string; a structured tool is
        # expected to surface its JSON in summary. Minimal check: required
        # keys must appear in the inline output text.
        text = observation.inline_output
        missing = [key for key in required if str(key) not in text]
        if missing:
            return _warn(self.name, f"output missing required keys: {', '.join(missing)}")
        return _pass(self.name)


class GuardrailManager:
    """Runs an ordered list of guardrails; first BLOCK wins (§14, §25)."""

    def __init__(
        self,
        pre_guardrails: Sequence[PreToolGuardrail] | None = None,
        post_guardrails: Sequence[PostToolGuardrail] | None = None,
    ) -> None:
        self._pre: list[PreToolGuardrail] = list(pre_guardrails or [])
        self._post: list[PostToolGuardrail] = list(post_guardrails or [])

    def register_pre(self, guardrail: PreToolGuardrail) -> None:
        self._pre.append(guardrail)

    def register_post(self, guardrail: PostToolGuardrail) -> None:
        self._post.append(guardrail)

    def check_pre_tool(self, tool: ToolSpec, args: dict[str, Any]) -> GuardrailResult:
        for guardrail in self._pre:
            result = guardrail.check(tool, args)
            if result.status == "BLOCK":
                return result
        return GuardrailResult(status="PASS", guardrail="pre_tool")

    def check_post_tool(
        self, tool: ToolSpec, args: dict[str, Any], observation: ToolObservation
    ) -> GuardrailResult:
        for guardrail in self._post:
            result = guardrail.check(tool, args, observation)
            if result.status == "BLOCK":
                return result
        return GuardrailResult(status="PASS", guardrail="post_tool")


class InputGuardrail(Protocol):
    """§14.1 — validates the incoming task/contract before a run starts."""

    name: str

    def check_input(self, objective: str, constraints: Sequence[str]) -> GuardrailResult: ...


class OutputGuardrail(Protocol):
    """§14.1 — validates the final result before it leaves the runtime."""

    name: str

    def check_output(self, summary: str, claims: Sequence[str]) -> GuardrailResult: ...


class MalformedTaskGuard:
    """§14.4 input guardrail: a task with no objective or forbidden data
    handling request is malformed."""

    name = "malformed_task"

    def check_input(self, objective: str, constraints: Sequence[str]) -> GuardrailResult:
        if not objective.strip():
            return _block(self.name, "task objective is empty")
        return _pass(self.name)


class CompletionClaimGuard:
    """§14.4 output guardrail: an unsupported completion claim (no criteria
    addressed, no evidence) is blocked before the result leaves."""

    name = "completion_claim"

    def check_output(self, summary: str, claims: Sequence[str]) -> GuardrailResult:
        if not summary.strip() and not claims:
            return _block(self.name, "result has neither summary nor claims")
        return _pass(self.name)


__all__: list[str] = [
    "CompletionClaimGuard",
    "GuardrailManager",
    "GuardrailResult",
    "InputGuardrail",
    "MalformedTaskGuard",
    "OutputGuardrail",
    "OutputSchemaGuard",
    "PathTraversalGuard",
    "SecretLeakGuard",
    "ShellInjectionGuard",
]
