"""Typed event envelope (harness.md §21). All components emit typed events;
telemetry is complete enough to replay/debug a run (INV-15, §55).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """§21.1 — one typed runtime event."""

    model_config = {"frozen": True}

    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)  # §21.2 taxonomy, e.g. "tool.execution.finished"
    timestamp: datetime
    run_id: str = Field(min_length=1)
    parent_run_id: str | None = None
    turn_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
