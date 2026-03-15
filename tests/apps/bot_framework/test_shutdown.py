"""Tests for the shared GracefulShutdown service."""

from __future__ import annotations

from trading_tools.apps.bot_framework.shutdown import GracefulShutdown


class TestGracefulShutdown:
    """Tests for graceful shutdown coordination."""

    def test_initial_state_is_running(self) -> None:
        """Shutdown flag starts as False."""
        gs = GracefulShutdown()
        assert not gs.should_stop

    def test_request_sets_flag(self) -> None:
        """Calling request() sets the stop flag."""
        gs = GracefulShutdown()
        gs.request()
        assert gs.should_stop

    def test_internal_handle_sets_flag(self) -> None:
        """The signal handler callback sets the stop flag."""
        gs = GracefulShutdown()
        gs._handle()
        assert gs.should_stop
