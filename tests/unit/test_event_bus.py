"""EventBus + CancelToken tests (harness.md §21, §23; INV-15)."""

import logging

import pytest
from pydantic import BaseModel, ValidationError

from aci.domain.runtime.events import EventEnvelope
from aci.runtime.cancellation import CancelToken, RunCancelled
from aci.runtime.event_bus import (
    ALL_EVENT_TYPES,
    RUN_STARTED,
    TOOL_EXECUTION_COMPLETED,
    EventBus,
    NullEventBus,
)


class TestEventBus:
    def test_crashing_sink_never_reaches_the_emitter(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        bus = EventBus()
        received: list[EventEnvelope] = []

        def boom(event: EventEnvelope) -> None:
            raise RuntimeError("sink down")

        bus.subscribe(boom)
        bus.subscribe(received.append)
        with caplog.at_level(logging.ERROR, logger="aci.runtime.event_bus"):
            envelope = bus.emit(RUN_STARTED, run_id="run-1")
        assert received == [envelope]
        assert "sink crashed" in caplog.text
        assert bus.history("run-1") == [envelope]

    def test_envelope_fields_and_history_isolation(self) -> None:
        bus = EventBus()
        first = bus.emit(
            TOOL_EXECUTION_COMPLETED,
            run_id="run-1",
            payload={"tool_id": "read_file", "status": "success"},
            turn_id="turn-1",
            correlation_id="c1",
            parent_run_id="run-0",
        )
        bus.emit(RUN_STARTED, run_id="run-2")
        assert first.event_type in ALL_EVENT_TYPES
        assert first.payload == {"tool_id": "read_file", "status": "success"}
        assert (first.turn_id, first.correlation_id, first.parent_run_id) == (
            "turn-1",
            "c1",
            "run-0",
        )
        assert first.timestamp.tzinfo is not None
        assert [e.run_id for e in bus.history("run-1")] == ["run-1"]
        assert bus.history("ghost") == []
        history = bus.history("run-1")
        history.clear()
        assert len(bus.history("run-1")) == 1  # history() hands out a copy

    def test_null_bus_records_but_ships_nothing(self) -> None:
        bus = NullEventBus()
        envelope = bus.emit(RUN_STARTED, run_id="run-1")
        assert envelope.event_id.startswith("evt_")
        assert bus.history("run-1") == [envelope]


class TestCancelToken:
    def test_raise_only_after_cancel(self) -> None:
        token = CancelToken("run-1")
        token.raise_if_cancelled()
        token.cancel()
        assert token.cancelled
        with pytest.raises(RunCancelled) as exc:
            token.raise_if_cancelled()
        assert exc.value.run_id == "run-1"
        token.cancel()  # idempotent
        assert token.cancelled

    def test_usable_as_pydantic_field_but_never_serialized(self) -> None:
        class Holder(BaseModel):
            token: CancelToken | None = None

        token = CancelToken("run-1")
        assert Holder(token=token).token is token
        with pytest.raises(ValidationError):
            Holder(token="run-1")  # type: ignore[arg-type]
