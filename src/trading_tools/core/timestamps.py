"""Timestamp parsing utilities for CLI date arguments."""

from datetime import UTC, datetime


def parse_timestamp(value: str) -> int:
    """Parse a date string or raw integer into a Unix timestamp.

    Accept ISO 8601 date strings (``2024-01-01``, ``2024-01-01T12:00:00``)
    or raw integer Unix timestamps.

    Args:
        value: Date string or integer timestamp.

    Returns:
        Unix timestamp in seconds.

    Raises:
        ValueError: If the value cannot be parsed.

    """
    # Try raw integer first
    try:
        return int(value)
    except ValueError:
        pass

    # Try ISO 8601 formats
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=UTC)
            return int(dt.timestamp())
        except ValueError:
            continue

    msg = f"Cannot parse timestamp: {value!r}. Use ISO 8601 (YYYY-MM-DD) or a Unix timestamp."
    raise ValueError(msg)
