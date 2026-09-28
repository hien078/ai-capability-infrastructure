"""File classifier + permission analyzer + risk aggregator (auto2.md §20-23).

Extends the pattern scanner (§19, already built) with the three layers it
was missing. All three are DETERMINISTIC — no LLM here (the semantic
instruction analyzer §22 is a separate, later layer whose output is a
signal, never an approval).

- classify_file (§20): every file gets a class; policy follows the class
  (markdown → inspect, script → scan, binary → manual review, unknown
  executable → reject).
- analyze_permissions (§21): extract requested/implicit capabilities from
  skill text — a skill REQUESTING a permission never RECEIVES it; the
  output is metadata for policy/routing only.
- aggregate_risk (§23): LOW/MEDIUM/HIGH/CRITICAL from static findings +
  permissions + (later) semantic findings. Policy: CRITICAL → reject,
  HIGH → human review, MEDIUM → staging under restrictions, LOW → continue.
"""

import re
from typing import Literal

FileClass = Literal[
    "documentation",
    "prompt",
    "skill",
    "configuration",
    "script",
    "executable",
    "binary",
    "archive",
    "data",
    "unknown",
]

#: auto2.md §20 policy per class — what the pipeline does with each kind.
CLASS_POLICY: dict[str, str] = {
    "documentation": "inspect",
    "prompt": "inspect",
    "skill": "inspect",
    "configuration": "inspect",
    "script": "quarantine + scan",
    "executable": "manual review / reject",
    "binary": "manual review / reject",
    "archive": "unpack only in isolated scanner",
    "data": "inspect",
    "unknown": "inspect",
}

_BINARY_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".mp3",
    ".mp4",
    ".wav",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".rar",
    ".7z",
    ".pyc",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".class",
}
_SCRIPT_EXT = {
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".py",
    ".pl",
    ".rb",
    ".lua",
    ".js",
    ".mjs",
    ".ts",
    ".tsx",
    ".jsx",
}
_CONFIG_EXT = {
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".lock",
    ".lockb",
}
_DOC_EXT = {".md", ".markdown", ".rst", ".txt", ".adoc"}
_PROMPT_EXT = {".prompt", ".prompt.md", ".txt.prompt"}
_SKILL_NAME = re.compile(r"skill\.md$", re.IGNORECASE)


def classify_file(path: str) -> FileClass:
    """Classify one file by name (extension + entrypoint conventions).

    Deterministic: same path → same class, always. Content heuristics
    (shebang detection) belong to the scanner layer, not the classifier.
    """
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    if _SKILL_NAME.search(name):
        return "skill"
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in _BINARY_EXT:
        if ext in {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z"}:
            return "archive"
        return "binary"
    if ext in _SCRIPT_EXT:
        return "script"
    if ext in _CONFIG_EXT:
        return "configuration"
    if name in {"makefile", "dockerfile"} or name.startswith("makefile"):
        return "script"
    if ext in _DOC_EXT:
        return "documentation"
    if ext in _PROMPT_EXT:
        return "prompt"
    if name.startswith(".") or ext == "":
        return "unknown"
    return "unknown"


# ---------------------------------------------------------------------------
# Permission analyzer (§21)
# ---------------------------------------------------------------------------

PermissionName = Literal[
    "filesystem.read",
    "filesystem.write",
    "shell",
    "git",
    "network",
    "browser",
    "database",
    "secrets",
    "process",
    "package-manager",
    "cloud-credentials",
]

#: Deterministic text signals per permission. A skill MENTIONING a tool
#: does not receive it (§21) — this is metadata for policy/routing.
#: Short shell-token signals use word boundaries (``\\brm\\b``) so English
#: prose ("confirm the fix") cannot false-positive on them.
_PERMISSION_SIGNALS: dict[PermissionName, tuple[str, ...]] = {
    "filesystem.read": ("read the file", "read file", "cat ", "open the file", "ls "),
    "filesystem.write": ("write the file", "create file", "mkdir ", r"\brm\b", "touch "),
    "shell": ("bash ", r"\bsh\b", "shell command", "run the command", "subprocess", "exec("),
    "git": ("git commit", "git push", "git checkout", "git branch"),
    "network": ("curl ", "wget ", "http request", "fetch the url", "api call"),
    "browser": ("browser", "webdriver", "selenium", "playwright", "navigate to"),
    "database": ("database", r"\bsql\b", "postgres", "mysql", "sqlite", "query the"),
    "secrets": (".env", "api key", "api_key", r"\btoken\b", "credential", r"\bsecret\b"),
    "process": (r"\bkill\b", "ps aux", r"\bprocess\b", "systemctl", "daemon"),
    "package-manager": ("npm install", "pip install", "bun add", "apt install", "yarn add"),
    "cloud-credentials": ("aws_", r"\bgcp\b", "azure ", "iam role", "cloud credential"),
}

#: Signals that are plain substrings vs word-boundary regexes.


def _matches(text_lower: str, signal: str) -> bool:
    if signal.startswith("\\b") or "\\b" in signal:
        return re.search(signal, text_lower) is not None
    return signal in text_lower


def analyze_permissions(text: str) -> list[PermissionName]:
    """Extract requested/implicit capabilities from skill text.

    Deterministic keyword containment over the LOWERCASED text. The
    result is METADATA (§21): a skill requesting ``shell`` does not get
    it — the client permission system remains the authority (§25.3).
    """
    lower = text.lower()
    found: list[PermissionName] = []
    for permission, signals in _PERMISSION_SIGNALS.items():
        if any(_matches(lower, s) for s in signals):
            found.append(permission)
    return found


# ---------------------------------------------------------------------------
# Risk aggregator (§23)
# ---------------------------------------------------------------------------

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def aggregate_risk(
    *,
    critical_findings: int,
    warning_findings: int,
    permissions: list[PermissionName],
    binary_files: int = 0,
    archive_files: int = 0,
) -> RiskLevel:
    """Aggregate every security signal into one risk level (§23).

    Deterministic policy — the same inputs always produce the same level:

    CRITICAL → any critical static finding (the scanner already blocks
               these; the aggregator double-books them for the report).
    HIGH     → dangerous permission requests (secrets/cloud-credentials)
               or unreviewed binaries/archives.
    MEDIUM   → shell/network/package-manager requests, or warnings present.
    LOW      → anything else — documentation-grade content.
    """
    if critical_findings > 0:
        return "CRITICAL"
    dangerous = {"secrets", "cloud-credentials"}
    if dangerous.intersection(permissions) or binary_files > 0 or archive_files > 0:
        return "HIGH"
    moderate = {"shell", "network", "package-manager", "process", "filesystem.write"}
    if moderate.intersection(permissions) or warning_findings > 0:
        return "MEDIUM"
    return "LOW"


#: auto2.md §23 policy per level — what the pipeline does next.
RISK_POLICY: dict[RiskLevel, str] = {
    "CRITICAL": "reject / emergency block",
    "HIGH": "mandatory human review",
    "MEDIUM": "staging under restrictions",
    "LOW": "continue",
}
