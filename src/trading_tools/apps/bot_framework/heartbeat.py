"""Periodic heartbeat logger for CloudWatch-compatible monitoring.

Emit structured INFO log lines at a configurable interval so all
trading bots produce consistent, parseable heartbeat metrics for
CloudWatch metric filters and alerting.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 60


class HeartbeatLogger:
    """Emit periodic heartbeat log lines with caller-supplied metrics.

    Track elapsed time and emit a structured INFO log line when the
    configured interval has passed. Each bot supplies its own metrics
    dict; the logger handles timing and formatting consistently.

    Args:
        interval: Minimum seconds between heartbeat emissions.
        prefix: Log line prefix (e.g. ``"HEARTBEAT"``).

    """

    def __init__(self, interval: int = _DEFAULT_INTERVAL, prefix: str = "HEARTBEAT") -> None:
        """Initialize the heartbeat logger.

        Args:
            interval: Seconds between heartbeat log lines.
            prefix: Prefix string for the log message.

        """
        self._interval = interval
        self._prefix = prefix
        self._last: float = float("-inf")

    def maybe_log(self, **metrics: Any) -> None:
        """Emit a heartbeat log line if the interval has elapsed.

        Format each metric as ``key=value`` in a single structured log
        line. Numeric values are formatted to 2 decimal places if they
        are floats.

        Args:
            **metrics: Key-value pairs to include in the log line.

        """
        now = time.monotonic()
        if now - self._last < self._interval:
            return
        self._last = now

        parts: list[str] = []
        for key, value in metrics.items():
            if isinstance(value, float | Decimal):
                parts.append(f"{key}={float(value):.2f}")
            else:
                parts.append(f"{key}={value}")

        logger.info("%s %s", self._prefix, " ".join(parts))
