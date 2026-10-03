"""Hidden acceptance: business days and timezone handling.

A transaction's business day is the calendar date of its timestamp
expressed in the report timezone — Tokyo evenings are the next day, New
York early hours are the previous one, DST days are 23 or 25 hours long.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from ledger.errors import ValidationError
from ledger.periods import day_bounds, day_of, format_day, load_tz, parse_day

UTC = UTC
TOKYO = load_tz("Asia/Tokyo")
NY = load_tz("America/New_York")


def test_load_tz_known_and_unknown():
    assert load_tz("UTC") is not None
    assert load_tz("Asia/Tokyo") is not None
    with pytest.raises(ValidationError):
        load_tz("Mars/Olympus_Mons")


def test_day_of_utc_is_the_utc_date():
    assert day_of(datetime(2026, 1, 1, 10, tzinfo=UTC), load_tz("UTC")) == date(2026, 1, 1)


def test_day_of_tokyo_evening_is_the_next_day():
    # 22:30 UTC on Jan 1 is 07:30 JST on Jan 2.
    assert day_of(datetime(2026, 1, 1, 22, 30, tzinfo=UTC), TOKYO) == date(2026, 1, 2)


def test_day_of_new_york_early_hours_are_the_previous_day():
    # 02:30 UTC on Jan 2 is 21:30 EST on Jan 1.
    assert day_of(datetime(2026, 1, 2, 2, 30, tzinfo=UTC), NY) == date(2026, 1, 1)


def test_day_of_dst_spring_forward():
    # 2026-03-08 in New York: 02:00 EST jumps to 03:00 EDT.
    # 06:30 UTC = 01:30 EST; 07:30 UTC = 03:30 EDT — both March 8.
    assert day_of(datetime(2026, 3, 8, 6, 30, tzinfo=UTC), NY) == date(2026, 3, 8)
    assert day_of(datetime(2026, 3, 8, 7, 30, tzinfo=UTC), NY) == date(2026, 3, 8)


def test_day_bounds_are_utc_instants():
    start, end = day_bounds(date(2026, 1, 1), load_tz("UTC"))
    assert start == datetime(2026, 1, 1, tzinfo=UTC)
    assert end == datetime(2026, 1, 2, tzinfo=UTC)


def test_day_bounds_tokyo_bracket_midnight_jst():
    start, end = day_bounds(date(2026, 1, 2), TOKYO)
    assert start == datetime(2026, 1, 1, 15, 0, tzinfo=UTC)
    assert end == datetime(2026, 1, 2, 15, 0, tzinfo=UTC)


def test_day_bounds_dst_day_is_23_hours():
    start, end = day_bounds(date(2026, 3, 8), NY)
    assert end - start == timedelta(hours=23)


def test_parse_day_is_strict_iso():
    assert parse_day("2026-01-05") == date(2026, 1, 5)
    with pytest.raises(ValidationError):
        parse_day("2026-1-5")
    with pytest.raises(ValidationError):
        parse_day("2026-13-01")
    with pytest.raises(ValidationError):
        parse_day("not a day")


def test_format_day_roundtrips():
    assert format_day(parse_day("2026-01-05")) == "2026-01-05"
