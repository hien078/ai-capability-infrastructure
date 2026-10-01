"""Standard workspace tools (harness.md §12, §16, §19): the model's file and
command surface, its dispatcher, run-workspace provisioning, and the
verifier-run command check."""

import hashlib
import os
import posixpath
import re
import shlex
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import ExecutionEnvelope
from aci.domain.runtime.evidence import CandidateResult, CheckResult, EvidenceItem, EvidenceKind
from aci.domain.runtime.state import RuntimeStateSnapshot
from aci.domain.runtime.tools import OutputPolicy, SideEffectReport, ToolAuthority, ToolSpec
from aci.runtime.protocols import GuardrailManager, ProcessResult, ToolDispatchResult
from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime
from aci.runtime.verification import VerifierCallable
from aci.runtime.workspace import NOISE_DIRS, WorkspaceManager, command_within_prefixes

TOOL_VERSION = "1.0.0"
VERIFICATION_CHECK_NAME = "verification_command_passes"
#: Never copied into a run workspace: caches, VCS metadata, installed environments.
PROVISION_IGNORE: frozenset[str] = NOISE_DIRS | {".venv", "node_modules"}
_DETAIL_CHARS = 1500
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

_PATH_PROPERTY = {
    "type": "string",
    "description": "Path relative to the workspace root, e.g. `src/app.py` (`.` is the root).",
}


def _object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def standard_tool_specs(
    *, allow_commands: bool, command_timeout_ms: int = 120_000
) -> list[ToolSpec]:
    """§12.3 — the standard workspace tool set the model gateway advertises."""
    specs = [
        ToolSpec(
            tool_id="read_file",
            version=TOOL_VERSION,
            description=(
                "Read a UTF-8 text file from the workspace. Every output line starts with its "
                "1-based line number and two spaces so you can cite `path:line`; that prefix "
                "is not part of the file. Very long files are truncated in the middle."
            ),
            input_schema=_object_schema({"path": _PATH_PROPERTY}),
            side_effect_class="READ_ONLY",
            authority_requirements=ToolAuthority(read_path_args=["path"]),
            idempotency_class="IDEMPOTENT",
            concurrency_safe=True,
        ),
        ToolSpec(
            tool_id="list_dir",
            version=TOOL_VERSION,
            description=(
                "List the entries of a workspace directory, one per line, sorted by name. "
                "Directory names end with `/`. Use `.` for the workspace root."
            ),
            input_schema=_object_schema({"path": _PATH_PROPERTY}),
            side_effect_class="READ_ONLY",
            authority_requirements=ToolAuthority(read_path_args=["path"]),
            idempotency_class="IDEMPOTENT",
            concurrency_safe=True,
        ),
        ToolSpec(
            tool_id="write_file",
            version=TOOL_VERSION,
            description=(
                "Create or overwrite a workspace file with exactly `content` (UTF-8); missing "
                "parent directories are created. To change part of an existing file, prefer "
                "edit_file."
            ),
            input_schema=_object_schema(
                {
                    "path": _PATH_PROPERTY,
                    "content": {"type": "string", "description": "The complete new file text."},
                }
            ),
            side_effect_class="LOCAL_MUTATION",
            authority_requirements=ToolAuthority(write_path_args=["path"]),
            idempotency_class="IDEMPOTENT",
        ),
        ToolSpec(
            tool_id="edit_file",
            version=TOOL_VERSION,
            description=(
                "Replace exactly one occurrence of `old_string` with `new_string` in a workspace "
                "file. `old_string` must match the file text exactly (without read_file's "
                "line-number prefixes) and occur exactly once; include neighbouring lines to "
                "make it unique. If it occurs zero or several times the call fails and the "
                "file is left unchanged."
            ),
            input_schema=_object_schema(
                {
                    "path": _PATH_PROPERTY,
                    "old_string": {
                        "type": "string",
                        "description": "Exact existing text to replace; must occur exactly once.",
                    },
                    "new_string": {"type": "string", "description": "The replacement text."},
                }
            ),
            side_effect_class="LOCAL_MUTATION",
            authority_requirements=ToolAuthority(read_path_args=["path"], write_path_args=["path"]),
            idempotency_class="NON_IDEMPOTENT",
        ),
    ]
    if allow_commands:
        specs.append(
            ToolSpec(
                tool_id="run_command",
                version=TOOL_VERSION,
                description=(
                    "Run a program in the workspace root and return its exit code, stdout and "
                    "stderr. `command` is an argv array executed directly, NOT through a shell: "
                    "pipes, redirection, globbing, `&&` and `cd` do not work. Example: "
                    '["python", "-m", "pytest", "-q"]. Only programs allowed by this run\'s '
                    "process scope may run; stdin is closed and the process is killed after "
                    f"{command_timeout_ms // 1000}s."
                ),
                input_schema=_object_schema(
                    {
                        "command": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": "Program and arguments, one array element each.",
                        }
                    }
                ),
                side_effect_class="LOCAL_MUTATION",
                authority_requirements=ToolAuthority(command_args=["command"]),
                timeout_ms=command_timeout_ms,
                cancellation_support="KILLABLE",
                idempotency_class="UNKNOWN",
                output_policy=OutputPolicy(truncation="head_tail"),
            )
        )
    return specs


def number_lines(content: str) -> str:
    """`read_file` output: `f"{n:>5}  {line}"` per line, split on `\\n` only."""
    if not content:
        return "(empty file)"
    lines = content.split("\n")
    if lines[-1] == "":
        lines.pop()
    return "\n".join(f"{n:>5}  {line.removesuffix(chr(13))}" for n, line in enumerate(lines, 1))


def format_process_output(result: ProcessResult) -> str:
    timed_out = " (timed out)" if result.timed_out else ""
    return (
        f"exit_code: {result.exit_code}{timed_out}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def _exit_label(result: ProcessResult) -> str:
    return "timeout" if result.timed_out else str(result.exit_code)


def _norm(path: str) -> str:
    return posixpath.normpath(path)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _string_arg(tool: ToolSpec, args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str):
        raise DomainError(
            ErrorCode.TOOL_ARGUMENT_INVALID, f"{tool.tool_id}: argument {name!r} must be a string"
        )
    return value


def _argv_arg(tool: ToolSpec, args: dict[str, Any]) -> list[str]:
    value = args.get("command")
    if not isinstance(value, list) or not value or not all(isinstance(a, str) for a in value):
        raise DomainError(
            ErrorCode.TOOL_ARGUMENT_INVALID,
            f"{tool.tool_id}: 'command' must be a non-empty array of strings",
        )
    return list(value)


class WorkspaceToolDispatcher:
    """ToolDispatcher over one managed workspace (§12.1 steps 6-7). Every call
    goes through the WorkspaceManager, so its bound envelope re-enforces what
    ToolRuntime already authorized (INV-04, defense in depth)."""

    def __init__(self, manager: WorkspaceManager, workspace_id: str) -> None:
        self._manager = manager
        self._workspace_id = workspace_id
        self._command_seq = 0
        self._handlers: dict[str, Callable[[ToolSpec, dict[str, Any]], ToolDispatchResult]] = {
            "read_file": self._read_file,
            "list_dir": self._list_dir,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "run_command": self._run_command,
        }

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        handler = self._handlers.get(tool.tool_id)
        if handler is None:
            raise DomainError(ErrorCode.TOOL_NOT_FOUND, f"no workspace tool named {tool.tool_id!r}")
        start = time.monotonic()
        try:
            result = handler(tool, args)
        except UnicodeDecodeError as exc:
            raise DomainError(
                ErrorCode.TOOL_ARGUMENT_INVALID,
                f"{tool.tool_id}: {args.get('path')!r} is not a UTF-8 text file",
            ) from exc
        except OSError as exc:
            # OS errors carry absolute server paths; report the workspace-relative subject.
            subject = args.get("path", args.get("command"))
            raise DomainError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                f"{tool.tool_id}: {exc.strerror or type(exc).__name__}: {subject!r}",
            ) from exc
        return result.model_copy(update={"duration_ms": int((time.monotonic() - start) * 1000)})

    def _read_file(self, tool: ToolSpec, args: dict[str, Any]) -> ToolDispatchResult:
        path = _string_arg(tool, args, "path")
        content = self._manager.read_file(self._workspace_id, path)
        return ToolDispatchResult(
            output=number_lines(content),
            evidence=[
                EvidenceItem(
                    kind=EvidenceKind.FILE_STATE,
                    ref=f"file://{_norm(path)}",
                    sha256=_sha256(content),
                    summary="read",
                )
            ],
        )

    def _list_dir(self, tool: ToolSpec, args: dict[str, Any]) -> ToolDispatchResult:
        path = _string_arg(tool, args, "path")
        entries = self._manager.list_dir(self._workspace_id, path)
        return ToolDispatchResult(
            output="\n".join(entries) if entries else "(empty directory)",
            evidence=[
                EvidenceItem(
                    kind=EvidenceKind.FILE_STATE, ref=f"file://{_norm(path)}", summary="listed"
                )
            ],
        )

    def _write_file(self, tool: ToolSpec, args: dict[str, Any]) -> ToolDispatchResult:
        path = _string_arg(tool, args, "path")
        content = _string_arg(tool, args, "content")
        self._manager.write_file(self._workspace_id, path, content)
        size = len(content.encode("utf-8"))
        return self._written(path, content, f"wrote {_norm(path)} ({size} bytes)")

    def _edit_file(self, tool: ToolSpec, args: dict[str, Any]) -> ToolDispatchResult:
        path = _string_arg(tool, args, "path")
        old = _string_arg(tool, args, "old_string")
        new = _string_arg(tool, args, "new_string")
        if not old:
            raise DomainError(
                ErrorCode.TOOL_ARGUMENT_INVALID, "edit_file: old_string must not be empty"
            )
        content = self._manager.read_file(self._workspace_id, path)
        count = content.count(old)
        if count != 1:
            problem = (
                "was not found"
                if count == 0
                else f"occurs {count} times; include surrounding lines so it is unique"
            )
            raise DomainError(
                ErrorCode.TOOL_ARGUMENT_INVALID,
                f"edit_file: old_string {problem} in {_norm(path)} (file unchanged)",
            )
        index = content.index(old)
        updated = content[:index] + new + content[index + len(old) :]
        self._manager.write_file(self._workspace_id, path, updated)
        line = content.count("\n", 0, index) + 1
        return self._written(path, updated, f"edited {_norm(path)} at line {line}")

    def _written(self, path: str, content: str, output: str) -> ToolDispatchResult:
        rel = _norm(path)
        return ToolDispatchResult(
            output=output,
            side_effects=SideEffectReport(state="confirmed", resources_changed=[f"file:{rel}"]),
            evidence=[
                EvidenceItem(
                    kind=EvidenceKind.FILE_STATE,
                    ref=f"file://{rel}",
                    sha256=_sha256(content),
                    summary="written",
                )
            ],
        )

    def _run_command(self, tool: ToolSpec, args: dict[str, Any]) -> ToolDispatchResult:
        argv = _argv_arg(tool, args)
        before = self._manager.file_hashes(self._workspace_id)
        result = self._manager.execute(self._workspace_id, argv, tool.timeout_ms)
        after = self._manager.file_hashes(self._workspace_id)
        changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        self._command_seq += 1
        return ToolDispatchResult(
            output=format_process_output(result),
            side_effects=SideEffectReport(
                state="confirmed" if changed else "none",
                resources_changed=[f"file:{p}" for p in changed],
            ),
            evidence=[
                EvidenceItem(
                    kind=EvidenceKind.COMMAND_OUTPUT,
                    ref=f"cmd://{self._command_seq}",
                    summary=f"exit={_exit_label(result)} {shlex.join(argv)}",
                )
            ],
        )


def build_workspace_tool_runtime(
    manager: WorkspaceManager,
    workspace_id: str,
    *,
    allow_commands: bool,
    command_timeout_ms: int = 120_000,
    guardrails: GuardrailManager | None = None,
) -> ToolRuntime:
    """A ToolRuntime whose registry is the standard workspace tool set."""
    registry = ToolRegistry()
    for spec in standard_tool_specs(
        allow_commands=allow_commands, command_timeout_ms=command_timeout_ms
    ):
        registry.register(spec)
    return ToolRuntime(
        registry, WorkspaceToolDispatcher(manager, workspace_id), guardrails=guardrails
    )


def provision_workspace(source: Path, runs_root: Path, run_id: str) -> Path:
    """§16.2 — each run works on its own copy of `source` at `runs_root/run_id`.

    Caches, VCS metadata and installed environments stay behind; symlinks are
    copied as symlinks, but any that resolve outside the copy are dropped so a
    run can never reach server files through its workspace."""
    if not _RUN_ID.fullmatch(run_id):
        raise DomainError(
            ErrorCode.WORKSPACE_PATH_INVALID, f"run id is not a safe directory name: {run_id!r}"
        )
    if not source.is_dir():
        raise DomainError(ErrorCode.WORKSPACE_NOT_FOUND, f"unknown workspace: {source.name!r}")
    runs_root.mkdir(parents=True, exist_ok=True)
    destination = runs_root / run_id
    try:
        destination.mkdir()
    except FileExistsError as exc:
        raise DomainError(
            ErrorCode.WORKSPACE_PATH_INVALID, f"run workspace already exists: {run_id!r}"
        ) from exc
    try:
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=_provision_ignore(source, runs_root),
            dirs_exist_ok=True,
        )
        _drop_escaping_symlinks(destination)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def _provision_ignore(source: Path, runs_root: Path) -> Callable[[str, list[str]], set[str]]:
    source_real = os.path.realpath(source)
    runs_real = os.path.realpath(runs_root)
    # A runs root inside the source must not be copied into its own runs.
    nested = runs_real == source_real or runs_real.startswith(source_real + os.sep)

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {name for name in names if name in PROVISION_IGNORE}
        if nested:
            skipped |= {
                name
                for name in names
                if os.path.realpath(os.path.join(directory, name)) == runs_real
            }
        return skipped

    return ignore


def _drop_escaping_symlinks(root: Path) -> None:
    root_real = Path(os.path.realpath(root))
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            entry = Path(dirpath) / name
            if not entry.is_symlink():
                continue
            target = Path(os.path.realpath(entry))
            if target != root_real and root_real not in target.parents:
                entry.unlink()
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]


def verification_command_check(
    manager: WorkspaceManager,
    workspace_id: str,
    argv: list[str],
    *,
    allowed_prefixes: list[str],
    timeout_ms: int = 300_000,
) -> VerifierCallable:
    """§19 — a MANDATORY check that the verifier itself runs `argv` in the
    workspace (INV-08: evidence, not the model's claim). INV-06: an argv
    outside `allowed_prefixes` fails the check without executing anything."""
    command = list(argv)
    rendered = shlex.join(command)

    def run(snapshot: RuntimeStateSnapshot, candidate: CandidateResult) -> CheckResult:
        if not command_within_prefixes(command, allowed_prefixes):
            return CheckResult(
                name=VERIFICATION_CHECK_NAME,
                passed=False,
                mandatory=True,
                detail=f"refused: {rendered!r} is outside the allowed verification prefixes",
            )
        try:
            result = manager.execute(workspace_id, command, timeout_ms)
        except DomainError as exc:
            if exc.code is not ErrorCode.PERMISSION_DENIED:
                raise
            # Fail closed AND visible: an unusable sandbox (or an envelope
            # refusal) is a failed mandatory check whose detail says why.
            return CheckResult(
                name=VERIFICATION_CHECK_NAME,
                passed=False,
                mandatory=True,
                detail=f"refused: {exc}"[:_DETAIL_CHARS],
            )
        header = f"timed out after {timeout_ms}ms\n" if result.timed_out else ""
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        # §61: the detail reaches the model and the client — never the server path.
        for root in {str(manager.root(workspace_id)), os.path.realpath(manager.root(workspace_id))}:
            output = output.replace(root, ".")
        return CheckResult(
            name=VERIFICATION_CHECK_NAME,
            passed=result.exit_code == 0 and not result.timed_out,
            mandatory=True,
            detail=header + output[-(_DETAIL_CHARS - len(header)) :],
            evidence=[
                EvidenceItem(
                    kind=EvidenceKind.TEST_RESULT,
                    ref=f"verify://{workspace_id}",
                    summary=f"exit={_exit_label(result)} {rendered}",
                )
            ],
        )

    return VerifierCallable(VERIFICATION_CHECK_NAME, run, mandatory=True)


__all__ = [
    "PROVISION_IGNORE",
    "TOOL_VERSION",
    "VERIFICATION_CHECK_NAME",
    "WorkspaceToolDispatcher",
    "build_workspace_tool_runtime",
    "format_process_output",
    "number_lines",
    "provision_workspace",
    "standard_tool_specs",
    "verification_command_check",
]
