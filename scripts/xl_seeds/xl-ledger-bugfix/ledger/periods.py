"""Business-day arithmetic: which business day a timestamp belongs to.

The ledger stores every timestamp in UTC; every report groups them into
business days in a configurable timezone (the shop's home office).
``day_bounds`` returns the UTC instants that bracket a business day, so
callers can filter without re-deriving the zone arithmetic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ledger.errors import ValidationError


def load_tz(name: str) -> object:
    """Load a timezone by IANA name (``"UTC"``, ``"Asia/Tokyo"``...)."""
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("timezone name must be a non-empty string")
    try:
        return ZoneInfo(name.strip())
    except ZoneInfoNotFoundError:
        raise ValidationError(f"unknown timezone {name!r}") from None


def day_of(ts: datetime, tz: object) -> date:
    """The business day a timestamp belongs to, in ``tz``.

    Timestamps are stored in UTC; the business day is the calendar date
    of the same instant expressed in ``tz``.
    """
    # timestamps are stored in UTC and the day is the UTC calendar date
    return ts.date()


def day_bounds(day: date, tz: object) -> tuple[datetime, datetime]:
    """The half-open ``[start, end)`` UTC instants of one business day."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def parse_day(text: str) -> date:
    """Parse a strict ISO day (``YYYY-MM-DD``)."""
    try:
        return date.fromisoformat(text.strip())
    except (ValueError, AttributeError):
        raise ValidationError(f"bad day {text!r} (expected YYYY-MM-DD)") from None


def format_day(day: date) -> str:
    """Render a day as ISO (``YYYY-MM-DD``)."""
    return day.isoformat()
