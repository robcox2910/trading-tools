"""Tests for timestamp parsing and conversion utilities."""

import time

import pytest

from trading_tools.core.timestamps import FIVE_MINUTES, MS_PER_SECOND, now_ms, parse_timestamp

_JAN_1_2024_UTC = 1704067200


class TestParseTimestamp:
    """Tests for parse_timestamp."""

    def test_iso_date(self) -> None:
        """Parse an ISO 8601 date string."""
        assert parse_timestamp("2024-01-01") == _JAN_1_2024_UTC

    def test_iso_datetime(self) -> None:
        """Parse an ISO 8601 datetime string."""
        assert parse_timestamp("2024-01-01T12:00:00") == _JAN_1_2024_UTC + 43200

    def test_unix_timestamp(self) -> None:
        """Pass through a raw Unix timestamp."""
        assert parse_timestamp("1704067200") == _JAN_1_2024_UTC

    def test_invalid_raises(self) -> None:
        """Raise ValueError for unparseable input."""
        with pytest.raises(ValueError, match="Cannot parse timestamp"):
            parse_timestamp("not-a-date")


class TestNowMs:
    """Tests for now_ms helper."""

    def test_returns_milliseconds(self) -> None:
        """Return current time in epoch milliseconds."""
        before = int(time.time() * MS_PER_SECOND)
        result = now_ms()
        after = int(time.time() * MS_PER_SECOND)
        assert before <= result <= after

    def test_returns_int(self) -> None:
        """Return an integer, not float."""
        assert isinstance(now_ms(), int)


class TestConstants:
    """Tests for timestamp module constants."""

    def test_ms_per_second(self) -> None:
        """MS_PER_SECOND is 1000."""
        expected = 1000
        assert expected == MS_PER_SECOND

    def test_five_minutes(self) -> None:
        """FIVE_MINUTES is 300 seconds."""
        expected = 300
        assert expected == FIVE_MINUTES
