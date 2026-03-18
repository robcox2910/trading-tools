"""Tests for the DirectionalTrader orchestration wrapper."""

from decimal import Decimal
from unittest.mock import MagicMock

from trading_tools.apps.directional.config import DirectionalConfig
from trading_tools.apps.directional.trader import DirectionalTrader


class _FakeClient:
    """Minimal fake client for construction tests."""


def _make_trader(**kwargs: object) -> DirectionalTrader:
    """Create a DirectionalTrader with default config and fake client."""
    config = DirectionalConfig(**({"capital": Decimal(100)} | kwargs))  # type: ignore[arg-type]
    return DirectionalTrader(config=config, client=_FakeClient())  # type: ignore[arg-type]


class TestDirectionalTraderInit:
    """Test DirectionalTrader initialisation."""

    def test_default_paper_mode(self) -> None:
        """Trader defaults to paper mode."""
        trader = _make_trader()
        assert trader.live is False
        assert trader.poll_count == 0

    def test_empty_positions_and_results(self) -> None:
        """Positions and results start empty before run()."""
        trader = _make_trader()
        assert len(trader.positions) == 0
        assert len(trader.results) == 0

    def test_stop_sets_flag(self) -> None:
        """Calling stop() signals the shutdown handler."""
        trader = _make_trader()
        trader.stop()
        assert trader._shutdown.should_stop is True

    def test_set_repository(self) -> None:
        """Attach a repository before run()."""
        trader = _make_trader()
        repo = MagicMock()
        trader.set_repository(repo)
        assert trader._repo is repo


class TestDirectionalTraderLogging:
    """Test logging methods don't raise when engine is None."""

    def test_log_heartbeat_no_engine(self) -> None:
        """Heartbeat is a no-op when engine is None."""
        trader = _make_trader()
        trader._log_heartbeat()  # Should not raise

    def test_log_periodic_summary_no_engine(self) -> None:
        """Periodic summary is a no-op when engine is None."""
        trader = _make_trader()
        trader._log_periodic_summary()  # Should not raise

    def test_log_summary_no_engine(self) -> None:
        """Final summary is a no-op when engine is None."""
        trader = _make_trader()
        trader._log_summary()  # Should not raise

    def test_log_heartbeat_with_engine(self) -> None:
        """Heartbeat logs when engine is set."""
        trader = _make_trader()
        engine = MagicMock()
        engine.results = []
        engine.positions = {}
        trader._engine = engine
        trader._log_heartbeat()  # Should not raise

    def test_log_periodic_summary_with_results(self) -> None:
        """Periodic summary handles results correctly."""
        trader = _make_trader()
        engine = MagicMock()
        engine.positions = {}
        mock_result = MagicMock()
        mock_result.pnl = Decimal("5.00")
        engine.results = [mock_result]
        engine.execution.get_capital.return_value = Decimal(105)
        trader._engine = engine
        trader._log_periodic_summary()  # Should not raise

    def test_log_summary_with_engine(self) -> None:
        """Final summary logs when engine is set."""
        trader = _make_trader()
        engine = MagicMock()
        engine.results = []
        engine.positions = {}
        trader._engine = engine
        trader._log_summary()  # Should not raise
