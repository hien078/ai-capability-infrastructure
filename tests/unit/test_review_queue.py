"""review_queue approve-license input validation (the human gate's own gate).

``cmd_approve_license`` recorded ANY string as a human license decision:
``spdx_permissions()`` falls back to unknown for unrecognized ids
(can_redistribute=False), so promotion still failed closed — but the
append-only audit trail carried the typo as if it were a real decision,
with no warning. A human gate must refuse an identifier it does not know.
"""

import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import review_queue as rq  # noqa: E402


class _FakeLicenses:
    def __init__(self) -> None:
        self.recorded: list[Any] = []

    def put_assessment(self, assessment: Any) -> None:
        self.recorded.append(assessment)

    def get_assessment(self, capability_id: str, version: str) -> None:
        return None


class _FakeSecurities:
    def get_assessment(self, capability_id: str, version: str) -> None:
        return None


class _FakeEngine:
    def connect(self) -> Any:  # unused on this path
        raise AssertionError("approve-license must not need raw SQL")


def _wire(monkeypatch: pytest.MonkeyPatch) -> _FakeLicenses:
    fake = _FakeLicenses()
    monkeypatch.setattr(
        rq,
        "_engine_and_factories",
        lambda: (_FakeEngine(), None, fake, _FakeSecurities(), None),
    )
    return fake


class TestApproveLicenseSpdxValidation:
    def test_refuses_an_unrecognized_spdx_id_without_recording(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A typo ('Apache 2.0' — space, not hyphen) must NOT become the human
        decision: nothing is written to the append-only audit trail."""
        fake = _wire(monkeypatch)
        code = rq.cmd_approve_license("cap-1", "1.0.0", "Apache 2.0", "alice", "", promote=False)
        assert code != 0
        assert fake.recorded == []
        err = capsys.readouterr().err
        assert "Apache 2.0" in err
        assert "MIT" in err  # the message lists the known ids

    @pytest.mark.parametrize("spdx_id", ["MIT", "Apache-2.0", "GPL-3.0-only", "Unlicense"])
    def test_known_ids_record_the_decision(
        self, monkeypatch: pytest.MonkeyPatch, spdx_id: str
    ) -> None:
        fake = _wire(monkeypatch)
        code = rq.cmd_approve_license("cap-1", "1.0.0", spdx_id, "alice", "", promote=False)
        assert code == 0
        assert len(fake.recorded) == 1
        assert fake.recorded[0].license_identifier == spdx_id
        assert fake.recorded[0].assessed_by == "human:alice"

    def test_the_known_set_is_the_detectors_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The validation must not drift from the detector's own permission
        table — every id the detector can resolve is acceptable here."""
        from aci.providers.licensing.detector import SPDX_PERMISSIONS

        fake = _wire(monkeypatch)
        for spdx_id in SPDX_PERMISSIONS:
            assert rq.cmd_approve_license("cap", "1", spdx_id, "a", "", promote=False) == 0
        assert len(fake.recorded) == len(SPDX_PERMISSIONS)
