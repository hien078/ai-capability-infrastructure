"""Pattern-based security scanner for skill packages (V2, plan §55).

Pure text analysis — no DB, no framework, no network. Scans every file in
a skill package for high-precision attack primitives and records weaker
signals as warnings. The promotion gate (ADR-012) consumes the verdict:
``failed`` blocks production; warnings are recorded in the assessment's
findings for human review but do not block — a pattern scanner that
auto-failed on weak signals would quarantine legitimate security-domain
skills (``prompt-injection-defense`` *describes* exfiltration attacks).

Honest limits (recorded, not hidden):
- This is lexical pattern matching, not semantics: a skilled author can
  evade every pattern here. It raises the floor (obvious attacks fail
  automatically); it is not a boundary (§61 content boundaries remain
  structural: raw bodies are unreachable from routing, scripts are inert
  digest-pinned bytes).
- Critical patterns are chosen for precision (unambiguous execution
  primitives); everything else is a warning. False negatives on novel
  attacks are expected — the human review workflow (§55) is the backstop.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aci.domain.provenance.models import SecurityAssessment  # noqa: F401 (re-export hint)

SCANNER_VERSION = "pattern-scanner:1.0.0"

#: Directories never scanned (not part of the package content contract).
SKIP_DIRS = {".git", ".github", "node_modules", "__pycache__"}

#: High-precision execution primitives — any match is critical.
_CRITICAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("pipe_to_shell", r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba)?sh\b"),
    ("pipe_to_sudo", r"\|\s*sudo\s+(?:ba)?sh\b"),
    ("eval_base64", r"\beval\s*\(\s*(?:base64|atob|bytes\.fromb64)[\w.]*\s*\("),
    ("exec_base64", r"\bexec\s*\(\s*(?:base64|atob)[\w.]*\s*\("),
)

#: Weak signals — recorded as warnings, never blocking on their own.
_WARNING_PATTERNS: tuple[tuple[str, str], ...] = (
    ("override_mention", r"(?i)\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\b"),
    (
        "safety_disclaimer_strip",
        r"(?i)\b(?:disable|bypass|remove)\s+(?:your\s+)?(?:safety|guardrails?)\b",
    ),
    ("credential_file_access", r"(?i)(?:\.env\b|\.ssh\/|\.aws\/|id_rsa)"),
    ("network_egress", r"\b(?:curl|wget|requests\.(?:get|post)|fetch|http\.get)\s*\("),
    ("base64_blob", r"(?i)\b[A-Za-z0-9+/]{80,}={0,2}"),
)

#: Informational only.
_INFO_PATTERNS: tuple[tuple[str, str], ...] = (
    ("executable_script", r"^#!.+\b(?:sh|bash|python|node)\b"),
)


@dataclass(frozen=True)
class ScanFinding:
    """One pattern hit. ``severity``: critical | warning | info."""

    severity: str
    category: str
    path: str
    detail: str

    def __str__(self) -> str:
        return f"{self.severity}:{self.category}:{self.path}:{self.detail[:120]}"


def _scan_text(path: str, text: str) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    for category, pattern in _CRITICAL_PATTERNS:
        if re.search(pattern, text):
            findings.append(ScanFinding("critical", category, path, pattern))
    for category, pattern in _WARNING_PATTERNS:
        if re.search(pattern, text):
            findings.append(ScanFinding("warning", category, path, pattern))
    for category, pattern in _INFO_PATTERNS:
        if re.search(pattern, text, re.MULTILINE):
            findings.append(ScanFinding("info", category, path, pattern))
    return findings


def _looks_base64(text: str) -> bool:
    """Heuristic: long base64 runs that actually decode are suspicious."""
    for match in re.finditer(r"[A-Za-z0-9+/]{80,}={0,2}", text):
        try:
            base64.b64decode(match.group(0), validate=True)
        except Exception:  # noqa: BLE001 — heuristic, any failure is fine
            continue
        return True
    return False


def scan_package(package_dir: Path) -> list[ScanFinding]:
    """Scan every file in a skill package directory."""
    findings: list[ScanFinding] = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        if SKIP_DIRS.intersection(path.parts):
            continue
        rel = path.relative_to(package_dir).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # noqa: BLE001 — unreadable file is itself a signal
            findings.append(ScanFinding("warning", "unreadable_file", rel, ""))
            continue
        findings.extend(_scan_text(rel, text))
        if _looks_base64(text):
            findings.append(ScanFinding("warning", "decodable_base64_blob", rel, ""))
    return findings


def verdict(findings: list[ScanFinding]) -> Literal["passed", "failed"]:
    """§24/ADR-012 gate input: ``failed`` if any critical finding, else ``passed``.

    Warnings never block on their own — they are recorded for human review
    (see module docstring for the false-positive rationale).
    """
    return "failed" if any(f.severity == "critical" for f in findings) else "passed"
