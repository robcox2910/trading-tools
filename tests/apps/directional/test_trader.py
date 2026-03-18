"""Tests for the DirectionalTrader orchestration wrapper."""

from trading_tools.apps.directional.config import DirectionalConfig
from trading_tools.apps.directional.trader import DirectionalTrader


class TestDirectionalTraderInit:
    """Test DirectionalTrader initialisation."""

    def test_default_paper_mode(self) -> None:
        """Trader defaults to paper mode."""
        config = DirectionalConfig()

        class _FakeClient:
            """Minimal fake client for construction test."""

        trader = DirectionalTrader(config=config, client=_FakeClient())  # type: ignore[arg-type]
        assert trader.live is False
        assert trader.poll_count == 0

    def test_empty_positions_and_results(self) -> None:
        """Positions and results start empty before run()."""
        config = DirectionalConfig()

        class _FakeClient:
            """Minimal fake client for construction test."""

        trader = DirectionalTrader(config=config, client=_FakeClient())  # type: ignore[arg-type]
        assert len(trader.positions) == 0
        assert len(trader.results) == 0

    def test_stop_sets_flag(self) -> None:
        """Calling stop() signals the shutdown handler."""
        config = DirectionalConfig()

        class _FakeClient:
            """Minimal fake client for construction test."""

        trader = DirectionalTrader(config=config, client=_FakeClient())  # type: ignore[arg-type]
        trader.stop()
        assert trader._shutdown.should_stop is True
