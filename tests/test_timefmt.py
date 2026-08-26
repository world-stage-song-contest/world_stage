from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from world_stage.utils.timefmt import (
    format_utc_datetime,
    format_warsaw_datetime,
    parse_utc_datetime,
)


@given(
    value=st.datetimes(
        min_value=datetime(1960, 1, 1),
        max_value=datetime(9999, 12, 31, 23, 59),
        timezones=st.just(UTC),
    )
)
def test_utc_form_values_round_trip_to_the_minute(value):
    formatted = format_utc_datetime(value)

    assert parse_utc_datetime(formatted) == value.replace(second=0, microsecond=0)


@given(value=st.dates().map(str))
def test_utc_form_values_require_a_start_time(value):
    with pytest.raises(ValueError):
        parse_utc_datetime(value)


@given(
    value=st.datetimes(
        min_value=datetime(1960, 1, 1),
        max_value=datetime(9999, 12, 30, 23, 59),
        timezones=st.just(UTC),
    )
)
def test_server_datetime_fallback_uses_warsaw_time(value):
    expected = value.astimezone(ZoneInfo("Europe/Warsaw")).strftime("%d %b %Y, %H:%M")

    assert format_warsaw_datetime(value) == expected
