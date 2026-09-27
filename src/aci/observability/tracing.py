"""Minimal tracing abstraction with an OTel-compatible shape, no vendor dependency.

Swap the tracer returned by get_tracer() for a real OpenTelemetry tracer later
without touching call sites.
"""

from typing import Protocol

AttributeValue = str | int | float | bool
Attributes = dict[str, AttributeValue]


class Span(Protocol):
    def set_attribute(self, key: str, value: AttributeValue) -> None: ...
    def __enter__(self) -> "Span": ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class Tracer(Protocol):
    def start_span(self, name: str, attributes: Attributes | None = None) -> Span: ...


class NoOpSpan:
    def __init__(self, attributes: Attributes | None = None) -> None:
        self.attributes: Attributes = dict(attributes or {})

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        self.attributes[key] = value

    def __enter__(self) -> "NoOpSpan":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class NoOpTracer:
    def start_span(self, name: str, attributes: Attributes | None = None) -> NoOpSpan:
        return NoOpSpan(attributes)


_tracer: Tracer = NoOpTracer()


def get_tracer() -> Tracer:
    return _tracer


def set_tracer(tracer: Tracer) -> None:
    global _tracer
    _tracer = tracer
