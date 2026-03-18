"""Timestamp parsing and conversion utilities.

Provide shared timestamp constants and helpers used across collectors,
engines, and CLI commands. Centralise ``now_ms()``, ``MS_PER_SECOND``,
and ``FIVE_MINUTES`` to eliminate duplicate definitions.
"""

import time
from datetime import UTC, datetime

MS_PER_SECOND = 1000
"""Milliseconds per second — used when converting between epoch-seconds and epoch-ms."""

FIVE_MINUTES = 300
"""Five minutes in seconds — used for prediction market window bucketing."""


def now_ms() -> int:
    """Return the current time as epoch milliseconds.

    Returns:
        Current wall-clock time in milliseconds since the Unix epoch.

    """
    return int(time.time() * MS_PER_SECOND)


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
