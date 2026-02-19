"""Tests for timestamp parsing utilities."""

import pytest

from trading_tools.core.timestamps import parse_timestamp

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
