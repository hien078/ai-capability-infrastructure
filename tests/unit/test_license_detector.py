"""Unit tests for the license detector (V2 governance, plan §55/§24).

The safety property under test: unknown is never a guess — an unmatched
license maps to `unknown_requires_review` permissions, which §24
blocks from production redistribution until a human assesses it.
"""

import json
from pathlib import Path

import pytest

from aci.domain.provenance.models import LicensePermissions
from aci.providers.licensing import detect_license, spdx_permissions

MIT_TEXT = """\
MIT License

Copyright (c) 2025 Someone Example

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
"""

APACHE_TEXT = """\
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION
"""

BSD3_TEXT = """\
BSD 3-Clause License

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met.

Neither the name of the copyright holder nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.
"""

BSD2_TEXT = """\
BSD 2-Clause License

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met.

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
"""

ISC_TEXT = """\
ISC License

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted.
"""

GPL3_TEXT = """\
                    GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

Everyone is permitted to copy and distribute verbatim copies.
"""

UNLICENSE_TEXT = """\
This is free and unencumbered software released into the public domain.
Anyone is free to copy, modify, publish, use, sell, or distribute it.
"""

PROPRIETARY_TEXT = """\
PROPRIETARY AND CONFIDENTIAL

This source code is the property of ACME Corp. No redistribution.
"""


def pkg(tmp_path: Path, license_text: str | None, name: str = "LICENSE.txt") -> Path:
    d = tmp_path / "skill-pkg"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\n", encoding="utf-8")
    if license_text is not None:
        (d / name).write_text(license_text, encoding="utf-8")
    return d


def test_mit_detected_from_file(tmp_path: Path) -> None:
    d = pkg(tmp_path, MIT_TEXT)
    det = detect_license(d)
    assert det.spdx_id == "MIT"
    assert det.method == "license-file"
    assert det.evidence == "LICENSE.txt"
    assert det.disagreement is False


def test_mit_survives_year_and_holder_variance(tmp_path: Path) -> None:
    variant = MIT_TEXT.replace("2025 Someone Example", "1998-2026 Other Holder")
    d = pkg(tmp_path, variant)
    assert detect_license(d).spdx_id == "MIT"


def test_apache_2_detected(tmp_path: Path) -> None:
    d = pkg(tmp_path, APACHE_TEXT)
    assert detect_license(d).spdx_id == "Apache-2.0"


def test_bsd_3_vs_bsd_2_disambiguated(tmp_path: Path) -> None:
    assert detect_license(pkg(tmp_path / "a", BSD3_TEXT)).spdx_id == "BSD-3-Clause"
    assert detect_license(pkg(tmp_path / "b", BSD2_TEXT)).spdx_id == "BSD-2-Clause"


def test_isc_gpl_unlicense_detected(tmp_path: Path) -> None:
    assert detect_license(pkg(tmp_path / "a", ISC_TEXT)).spdx_id == "ISC"
    assert detect_license(pkg(tmp_path / "b", GPL3_TEXT)).spdx_id == "GPL-3.0-only"
    assert detect_license(pkg(tmp_path / "c", UNLICENSE_TEXT)).spdx_id == "Unlicense"


def test_unknown_license_is_never_a_guess(tmp_path: Path) -> None:
    d = pkg(tmp_path, PROPRIETARY_TEXT)
    det = detect_license(d)
    assert det.spdx_id == "unknown"
    # A file exists but is unreadable to the matcher: honest evidence trail.
    assert det.method == "license-file"
    assert det.evidence == "LICENSE.txt"
    perms = spdx_permissions(det.spdx_id)
    assert perms.unknown_requires_review is True
    assert perms.can_redistribute is False


def test_no_license_at_all_is_unknown(tmp_path: Path) -> None:
    d = pkg(tmp_path, None)
    det = detect_license(d)
    assert det.spdx_id == "unknown"
    assert spdx_permissions(det.spdx_id).can_redistribute is False


def test_manifest_field_used_when_no_license_file(tmp_path: Path) -> None:
    d = pkg(tmp_path, None)
    (d / "package.json").write_text(json.dumps({"name": "x", "license": "MIT"}), encoding="utf-8")
    det = detect_license(d)
    assert det.spdx_id == "MIT"
    assert det.method == "manifest-field"


def test_manifest_field_spdx_expression_reduced_to_first_id(tmp_path: Path) -> None:
    d = pkg(tmp_path, None)
    (d / "package.json").write_text(json.dumps({"license": "Apache-2.0 OR MIT"}), encoding="utf-8")
    assert detect_license(d).spdx_id == "Apache-2.0"


def test_file_text_wins_over_manifest_disagreement(tmp_path: Path) -> None:
    d = pkg(tmp_path, MIT_TEXT)
    (d / "package.json").write_text(json.dumps({"license": "GPL-3.0-only"}), encoding="utf-8")
    det = detect_license(d)
    assert det.spdx_id == "MIT"
    assert det.disagreement is True


def test_unknown_manifest_field_does_not_override_unknown(tmp_path: Path) -> None:
    d = pkg(tmp_path, PROPRIETARY_TEXT)
    (d / "package.json").write_text(json.dumps({"license": "MIT"}), encoding="utf-8")
    # The file exists but does not match a known license → unknown wins.
    det = detect_license(d)
    assert det.spdx_id == "unknown"


def test_permissions_table_semantics() -> None:
    permissive = spdx_permissions("MIT")
    assert isinstance(permissive, LicensePermissions)
    assert permissive.can_redistribute is True
    assert permissive.attribution_required is True
    assert permissive.share_alike_required is False

    copyleft = spdx_permissions("GPL-3.0-only")
    assert copyleft.can_redistribute is True
    assert copyleft.share_alike_required is True

    public = spdx_permissions("Unlicense")
    assert public.can_redistribute is True
    assert public.attribution_required is False

    unknown = spdx_permissions("totally-made-up")
    assert unknown.can_redistribute is False
    assert unknown.unknown_requires_review is True


@pytest.mark.parametrize("spdx", ["MIT", "Apache-2.0", "BSD-3-Clause", "ISC"])
def test_known_ids_are_redistributable(spdx: str) -> None:
    assert spdx_permissions(spdx).can_redistribute is True
