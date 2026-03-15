"""Graceful shutdown handler for trading bots.

Install OS signal handlers (SIGINT, SIGTERM) and expose a simple
boolean flag that polling loops and event streams can check to exit
cleanly. Used by the spread capture bot, live trading engine, and
whale monitor to avoid duplicating signal-handling boilerplate.
"""

from __future__ import annotations

import asyncio
import logging
import signal as signal_mod

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Coordinate graceful shutdown across async trading loops.

    Install SIGINT and SIGTERM handlers on the running event loop so
    that a Ctrl-C or ``systemctl stop`` sets a flag. Polling loops
    check ``should_stop`` each cycle; WebSocket streams break on the
    flag and proceed to cleanup.

    """

    def __init__(self) -> None:
        """Initialize with shutdown flag unset."""
        self._stop = False

    def install(self) -> None:
        """Register SIGINT and SIGTERM handlers on the running event loop.

        Safe to call multiple times — subsequent calls overwrite the
        previous handler with an identical one.
        """
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal_mod.SIGINT, self._handle)
        loop.add_signal_handler(signal_mod.SIGTERM, self._handle)
        logger.debug("Shutdown handlers installed for SIGINT/SIGTERM")

    def request(self) -> None:
        """Programmatically request shutdown (e.g. from a loss limit check)."""
        self._stop = True

    @property
    def should_stop(self) -> bool:
        """Return ``True`` if a shutdown has been requested."""
        return self._stop

    def _handle(self) -> None:
        """Signal handler callback — set the stop flag and log."""
        logger.info("Shutdown signal received")
        self._stop = True
