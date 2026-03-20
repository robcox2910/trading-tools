"""Tests for the shared HeartbeatLogger service."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

from trading_tools.apps.bot_framework.heartbeat import HeartbeatLogger

if TYPE_CHECKING:
    import pytest

_SHORT_INTERVAL = 1


class TestHeartbeatLogger:
    """Tests for periodic heartbeat emission."""

    def test_logs_on_first_call(self, caplog: pytest.LogCaptureFixture) -> None:
        """Emit a heartbeat on the very first call."""
        hb = HeartbeatLogger(interval=_SHORT_INTERVAL)
        with caplog.at_level(logging.INFO):
            hb.maybe_log(polls=5, pnl=1.23)
        assert "HEARTBEAT" in caplog.text
        assert "polls=5" in caplog.text
        assert "pnl=1.23" in caplog.text

    def test_suppresses_within_interval(self, caplog: pytest.LogCaptureFixture) -> None:
        """Do not emit a heartbeat if the interval has not elapsed."""
        hb = HeartbeatLogger(interval=_SHORT_INTERVAL)
        hb.maybe_log(polls=1)
        caplog.clear()

        with caplog.at_level(logging.INFO):
            hb.maybe_log(polls=2)
        assert "HEARTBEAT" not in caplog.text

    def test_emits_after_interval(self, caplog: pytest.LogCaptureFixture) -> None:
        """Emit again after the interval has elapsed."""
        hb = HeartbeatLogger(interval=_SHORT_INTERVAL)
        hb.maybe_log(polls=1)

        original = time.monotonic()
        with patch("time.monotonic", return_value=original + 2):
            caplog.clear()
            with caplog.at_level(logging.INFO):
                hb.maybe_log(polls=2)
        assert "HEARTBEAT" in caplog.text
        assert "polls=2" in caplog.text

    def test_custom_prefix(self, caplog: pytest.LogCaptureFixture) -> None:
        """Use a custom prefix in the log line."""
        hb = HeartbeatLogger(prefix="STATUS")
        with caplog.at_level(logging.INFO):
            hb.maybe_log(ok=True)
        assert "STATUS" in caplog.text
