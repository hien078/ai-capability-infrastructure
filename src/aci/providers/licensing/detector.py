"""License detection for skill packages (V2 governance, plan §55).

Pure text matching — no DB, no framework. Given a skill package
directory, detect the SPDX identifier from (a) a LICENSE/COPYING file's
content, corroborated by (b) a manifest `license` field (package.json /
pyproject.toml). The file text wins on disagreement: manifests are
trivially editable, license texts are canonical.

Unknown is a first-class outcome, never a guess: an unmatched license
maps to `unknown_requires_review` and §24 blocks production
redistribution until a human assesses it. That default is the safety
property the whole gate chain depends on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from aci.domain.provenance.models import LicensePermissions

__all__ = ["LicenseDetection", "detect_license", "spdx_permissions"]

#: License file basenames to probe, in precedence order.
LICENSE_FILES: tuple[str, ...] = (
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "LICENCE",
    "LICENCE.txt",
    "COPYING",
    "COPYING.txt",
    "COPYING.LESSER",
)

#: Distinctive normalized-text signatures per SPDX id. Matched as
#: (all-of) substrings against whitespace-collapsed lowercase text.
_SIGNATURES: dict[str, tuple[str, ...]] = {
    "MIT": (
        "permission is hereby granted, free of charge, to any person obtaining a copy",
        'the software is provided "as is"',
    ),
    "Apache-2.0": (
        "apache license",
        "version 2.0, january 2004",
        "http://www.apache.org/licenses/",
    ),
    "BSD-3-Clause": (
        "redistribution and use in source and binary forms",
        "may be used to endorse or promote products derived from this software",
    ),
    "BSD-2-Clause": (
        "redistribution and use in source and binary forms",
        "redistributions in binary form must reproduce the above copyright notice",
    ),
    "ISC": (
        "permission to use, copy, modify, and/or distribute this software for any purpose",
        "with or without fee",
    ),
    "MPL-2.0": (
        "mozilla public license",
        "version 2.0",
    ),
    "GPL-3.0-only": (
        "gnu general public license",
        "version 3",
    ),
    "GPL-2.0-only": (
        "gnu general public license",
        "version 2",
    ),
    "LGPL-3.0-only": (
        "gnu lesser general public license",
        "version 3",
    ),
    "AGPL-3.0-only": (
        "gnu affero general public license",
        "version 3",
    ),
    "Unlicense": ("this is free and unencumbered software released into the public domain",),
    "CC0-1.0": (
        "creative commons zero",
        "cc0",
    ),
}

#: BSD-2 vs BSD-3 share the first clauses; 3-clause adds the endorse
#: sentence, 2-clause lacks it. Resolve by presence of the extra clause.
_BSD3_EXTRA = "may be used to endorse or promote products derived from this software"

#: SPDX id → machine-readable §24 permissions. Copyleft families stay
#: redistributable (the license permits it) but carry share-alike; the
#: catalog must surface that before redistribution.
_PERMISSIVE = LicensePermissions(
    can_ingest=True,
    can_modify=True,
    can_store=True,
    can_redistribute=True,
    commercial_use_allowed=True,
    attribution_required=True,
)
_PUBLIC_DOMAIN = LicensePermissions(
    can_ingest=True,
    can_modify=True,
    can_store=True,
    can_redistribute=True,
    commercial_use_allowed=True,
)
_COPYLEFT = LicensePermissions(
    can_ingest=True,
    can_modify=True,
    can_store=True,
    can_redistribute=True,
    commercial_use_allowed=True,
    attribution_required=True,
    share_alike_required=True,
)
_UNKNOWN = LicensePermissions(
    can_ingest=True,
    can_modify=False,
    can_store=True,
    can_redistribute=False,
    commercial_use_allowed=False,
    unknown_requires_review=True,
)

SPDX_PERMISSIONS: dict[str, LicensePermissions] = {
    "MIT": _PERMISSIVE,
    "BSD-2-Clause": _PERMISSIVE,
    "BSD-3-Clause": _PERMISSIVE,
    "ISC": _PERMISSIVE,
    "Apache-2.0": _PERMISSIVE,
    "Unlicense": _PUBLIC_DOMAIN,
    "CC0-1.0": _PUBLIC_DOMAIN,
    "0BSD": _PUBLIC_DOMAIN,
    "MPL-2.0": _COPYLEFT,
    "GPL-2.0-only": _COPYLEFT,
    "GPL-3.0-only": _COPYLEFT,
    "LGPL-3.0-only": _COPYLEFT,
    "AGPL-3.0-only": _COPYLEFT,
}


@dataclass(frozen=True)
class LicenseDetection:
    """What the detector found in one package."""

    spdx_id: str
    #: "license-file" | "manifest-field" | "none"
    method: str
    #: Where the evidence lives (relative path or manifest pointer).
    evidence: str
    #: True when file text and manifest field disagree (file wins).
    disagreement: bool = False


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _match_text(raw: str) -> str | None:
    text = _normalize(raw)
    if "gnu affero general public license" in text:
        return "AGPL-3.0-only" if "version 3" in text else None
    if "gnu lesser general public license" in text:
        return "LGPL-3.0-only" if "version 3" in text else None
    if "gnu general public license" in text:
        if "version 3" in text:
            return "GPL-3.0-only"
        if "version 2" in text:
            return "GPL-2.0-only"
        return None
    if "apache license" in text and "version 2.0" in text:
        return "Apache-2.0"
    if _all(_SIGNATURES["MIT"], text):
        return "MIT"
    if _all(_SIGNATURES["BSD-3-Clause"], text) or _all(_SIGNATURES["BSD-2-Clause"], text):
        return "BSD-3-Clause" if _BSD3_EXTRA in text else "BSD-2-Clause"
    if _all(_SIGNATURES["ISC"], text):
        return "ISC"
    if _all(_SIGNATURES["MPL-2.0"], text):
        return "MPL-2.0"
    if _all(_SIGNATURES["Unlicense"], text):
        return "Unlicense"
    if _all(_SIGNATURES["CC0-1.0"], text):
        return "CC0-1.0"
    return None


def _all(needles: tuple[str, ...], haystack: str) -> bool:
    return all(n in haystack for n in needles)


def _manifest_field(package_dir: Path) -> str | None:
    """SPDX id from package.json `license` or pyproject `[project].license`."""
    pkg = package_dir / "package.json"
    if pkg.is_file():
        try:
            field = json.loads(pkg.read_text(encoding="utf-8")).get("license")
        except (json.JSONDecodeError, OSError):
            field = None
        if isinstance(field, str) and field.strip():
            return _clean_spdx(field)
    pyproject = package_dir / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib  # noqa: PLC0415

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            field = data.get("project", {}).get("license")
        except (tomllib.TOMLDecodeError, OSError, ImportError):
            field = None
        if isinstance(field, str) and field.strip():
            return _clean_spdx(field)
    return None


def _clean_spdx(raw: str) -> str:
    """Reduce an SPDX expression to its first identifier, canonical casing."""
    token = re.split(r"[ ()+|,&]", raw.strip())[0]
    if not token:
        return ""
    if token in SPDX_PERMISSIONS:
        return token
    for known in SPDX_PERMISSIONS:
        if known.lower() == token.lower():
            return known
    return token


def detect_license(package_dir: Path) -> LicenseDetection:
    """Detect the license of one skill package.

    Precedence: license-file text > manifest field > unknown. A manifest
    disagreement is recorded but never overrides the file.
    """
    manifest = _manifest_field(package_dir)
    for name in LICENSE_FILES:
        candidate = package_dir / name
        if not candidate.is_file():
            continue
        raw = candidate.read_text(encoding="utf-8", errors="replace")
        spdx = _match_text(raw)
        if spdx is None:
            # A license file exists but is not a recognized license: the
            # honest answer is unknown. The manifest cannot rescue it —
            # the file is the authoritative evidence and it says something
            # we cannot machine-read. §24 keeps it review-gated.
            return LicenseDetection(spdx_id="unknown", method="license-file", evidence=name)
        return LicenseDetection(
            spdx_id=spdx,
            method="license-file",
            evidence=name,
            disagreement=manifest is not None and manifest != spdx,
        )
    if manifest and manifest in SPDX_PERMISSIONS:
        return LicenseDetection(
            spdx_id=manifest, method="manifest-field", evidence="package.json/pyproject.toml"
        )
    return LicenseDetection(spdx_id="unknown", method="none", evidence="")


def spdx_permissions(spdx_id: str) -> LicensePermissions:
    """§24 permissions for an SPDX id; unknown stays review-gated."""
    return SPDX_PERMISSIONS.get(spdx_id, _UNKNOWN)
