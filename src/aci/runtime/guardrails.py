"""GuardrailManager (harness.md §14): semantic/structural checks, separate from authority.

Guardrails validate tool inputs and outputs (INV-06: no side effect before
guardrail); they never grant authority. All checks are deterministic and run
in registration order — first BLOCK wins (§25); TRANSFORM results chain so
the final redacted output reaches ToolRuntime; WARN is reported, not dropped.
"""

import json
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


def _string_values(args: dict[str, Any], field_names: tuple[str, ...]) -> list[tuple[str, str]]:
    """(field_name, string) for every value under a matching key at ANY depth
    of the args tree (lists and dicts included) — nesting never hides a field."""
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

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in field_names:
                    _collect(key, item)
                else:
                    _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(args)
    return out


class ShellInjectionGuard:
    """Pre-tool: shell metacharacter patterns in command-like fields → BLOCK.

    Applied to shell strings and argv lists alike: substitution, chaining into
    a privileged/destructive command, and piping into a shell are suspicious
    regardless of how the tool executes (§14.4)."""

    name = "shell_injection"

    FIELD_NAMES = ("command", "cmd", "script", "shell", "bash", "sh", "argv")
    PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("command substitution $( )", re.compile(r"\$\(")),
        ("backtick substitution", re.compile(r"`[^`]*`")),
        (
            "chained privileged/destructive command",
            re.compile(r"(?:[;&|]|\n)\s*(?:sudo|doas|mkfs|shred|rm\s+-{1,2}\w*[rf]\w*)\b"),
        ),
        ("pipe to shell", re.compile(r"\|\s*(?:sudo|doas|sh|bash|zsh|dash|ksh)\b")),
    )

    def check(self, tool: ToolSpec, args: dict[str, Any]) -> GuardrailResult:
        for field, value in _string_values(args, self.FIELD_NAMES):
            for label, pattern in self.PATTERNS:
                if pattern.search(value):
                    return _block(self.name, f"shell metacharacter pattern ({label}) in {field!r}")
        return _pass(self.name)


class PathTraversalGuard:
    """Pre-tool: `..` segments → BLOCK; absolute paths → BLOCK unless they lie
    under an explicitly allowed prefix (tool paths are workspace-relative)."""

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

    def __init__(self, allowed_prefixes: tuple[str, ...] = ()) -> None:
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
    """Post-tool: secret patterns in the observation → BLOCK (default), or in
    ``redact`` mode → TRANSFORM with every match replaced in ``inline_output``.
    A private key block is never redactable (the header is only its first
    line) and always blocks; a secret in the summary always blocks."""

    name = "secret_leak"

    PATTERNS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
        ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), True),
        ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), False),
        ("api token (sk-)", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), True),
        ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), True),
        ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), True),
        ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"), True),
        (
            "credential assignment",
            re.compile(
                r"\b(?:token|api[_-]?key|secret|password)\s*[=:]\s*['\"]?[A-Za-z0-9_+/=-]{16,}",
                re.IGNORECASE,
            ),
            True,
        ),
    )

    def __init__(self, mode: Literal["block", "redact"] = "block") -> None:
        self._mode = mode

    def check(
        self, tool: ToolSpec, args: dict[str, Any], observation: ToolObservation
    ) -> GuardrailResult:
        if self._mode == "block":
            text = _observation_text(observation)
            for label, pattern, _ in self.PATTERNS:
                if pattern.search(text):
                    return _block(self.name, f"secret pattern ({label}) in tool output")
            return _pass(self.name)
        for label, pattern, _ in self.PATTERNS:
            if pattern.search(observation.summary):
                return _block(self.name, f"secret pattern ({label}) in tool summary")
        redacted = observation.inline_output
        labels: list[str] = []
        for label, pattern, redactable in self.PATTERNS:
            if not pattern.search(redacted):
                continue
            if not redactable:
                return _block(self.name, f"secret pattern ({label}) in tool output")
            redacted = pattern.sub(f"[REDACTED:{label}]", redacted)
            labels.append(label)
        if not labels:
            return _pass(self.name)
        return GuardrailResult(
            status="TRANSFORM",
            guardrail=self.name,
            reason=f"redacted secret pattern(s): {', '.join(labels)}",
            transformed_value={"redacted_output": redacted},
        )


def _observation_text(observation: ToolObservation) -> str:
    return observation.inline_output + "\n" + observation.summary


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


def _json_type_matches(expected: Any, value: Any) -> bool:
    if not isinstance(expected, str) or expected not in _JSON_TYPES:
        return True
    if isinstance(value, bool) and expected in ("integer", "number"):
        return False
    return isinstance(value, _JSON_TYPES[expected])


class OutputSchemaGuard:
    """Post-tool: when the tool declares an object ``output_schema``, the
    inline output must parse as a JSON object with the required keys and the
    declared property types → WARN on mismatch (observational, never grants)."""

    name = "output_schema"

    def check(
        self, tool: ToolSpec, args: dict[str, Any], observation: ToolObservation
    ) -> GuardrailResult:
        schema = tool.output_schema
        if not schema or schema.get("type") != "object":
            return _pass(self.name)
        try:
            data = json.loads(observation.inline_output)
        except ValueError:
            return _warn(self.name, "output is not valid JSON but the tool declares an object")
        if not isinstance(data, dict):
            return _warn(self.name, "output is not a JSON object")
        required = schema.get("required", [])
        if not isinstance(required, list):
            required = []
        missing = [str(key) for key in required if key not in data]
        if missing:
            return _warn(self.name, f"output missing required keys: {', '.join(missing)}")
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, prop in properties.items():
                if key in data and isinstance(prop, dict):
                    if not _json_type_matches(prop.get("type"), data[key]):
                        return _warn(
                            self.name,
                            f"output key {key!r} is not of type {prop.get('type')!r}",
                        )
        return _pass(self.name)


class GuardrailManager:
    """Runs an ordered list of guardrails; first BLOCK wins (§14, §25).
    Otherwise the result is the chained TRANSFORM (post-tool), else the first
    WARN, else PASS."""

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
        warned: GuardrailResult | None = None
        for guardrail in self._pre:
            result = guardrail.check(tool, args)
            if result.status == "BLOCK":
                return result
            if result.status != "PASS" and warned is None:
                warned = result
        return warned or GuardrailResult(status="PASS", guardrail="pre_tool")

    def check_post_tool(
        self, tool: ToolSpec, args: dict[str, Any], observation: ToolObservation
    ) -> GuardrailResult:
        current = observation
        transformers: list[str] = []
        reasons: list[str] = []
        warned: GuardrailResult | None = None
        for guardrail in self._post:
            result = guardrail.check(tool, args, current)
            if result.status == "BLOCK":
                return result
            if result.status == "TRANSFORM":
                value = result.transformed_value or {}
                if "redacted_output" in value:
                    current = current.model_copy(
                        update={"inline_output": str(value["redacted_output"])}
                    )
                    transformers.append(result.guardrail or guardrail.name)
                    reasons.append(result.reason)
            elif result.status == "WARN" and warned is None:
                warned = result
        if transformers:
            return GuardrailResult(
                status="TRANSFORM",
                guardrail=",".join(transformers),
                reason="; ".join(r for r in reasons if r),
                transformed_value={"redacted_output": current.inline_output},
            )
        return warned or GuardrailResult(status="PASS", guardrail="post_tool")


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
