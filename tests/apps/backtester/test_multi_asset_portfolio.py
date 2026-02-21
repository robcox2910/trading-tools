"""Test suite for the multi-asset portfolio module."""

from decimal import Decimal

from trading_tools.apps.backtester.multi_asset_portfolio import MultiAssetPortfolio
from trading_tools.core.models import ExecutionConfig, Side, Signal

_INITIAL_CAPITAL = Decimal(10_000)
_HALF_POSITION = Decimal("0.5")
_EXPECTED_FORCE_CLOSE_COUNT = 2


def _signal(symbol: str, side: Side) -> Signal:
    """Build a signal for the given symbol and side."""
    return Signal(side=side, symbol=symbol, strength=Decimal(1), reason="test")


class TestMultiAssetPortfolio:
    """Test multi-asset portfolio position management."""

    def test_open_positions_for_multiple_symbols(self) -> None:
        """Open positions for two different symbols simultaneously."""
        portfolio = MultiAssetPortfolio(
            _INITIAL_CAPITAL,
            ExecutionConfig(position_size_pct=_HALF_POSITION),
        )
        portfolio.process_signal(_signal("BTC-USD", Side.BUY), Decimal(100), 1000)
        portfolio.process_signal(_signal("ETH-USD", Side.BUY), Decimal(50), 1001)
        assert "BTC-USD" in portfolio.positions
        assert "ETH-USD" in portfolio.positions

    def test_duplicate_buy_ignored(self) -> None:
        """Ignore a BUY signal for a symbol that already has an open position."""
        portfolio = MultiAssetPortfolio(_INITIAL_CAPITAL)
        portfolio.process_signal(_signal("BTC-USD", Side.BUY), Decimal(100), 1000)
        portfolio.process_signal(_signal("BTC-USD", Side.BUY), Decimal(110), 1001)
        assert len(portfolio.positions) == 1

    def test_sell_closes_only_target_symbol(self) -> None:
        """Close only the specified symbol's position on SELL."""
        portfolio = MultiAssetPortfolio(
            _INITIAL_CAPITAL,
            ExecutionConfig(position_size_pct=_HALF_POSITION),
        )
        portfolio.process_signal(_signal("BTC-USD", Side.BUY), Decimal(100), 1000)
        portfolio.process_signal(_signal("ETH-USD", Side.BUY), Decimal(50), 1001)
        trade = portfolio.process_signal(_signal("BTC-USD", Side.SELL), Decimal(110), 2000)
        assert trade is not None
        assert trade.symbol == "BTC-USD"
        assert "BTC-USD" not in portfolio.positions
        assert "ETH-USD" in portfolio.positions

    def test_insufficient_capital_skips_buy(self) -> None:
        """Skip opening a position when capital is insufficient."""
        portfolio = MultiAssetPortfolio(_INITIAL_CAPITAL)
        portfolio.process_signal(_signal("BTC-USD", Side.BUY), Decimal(100), 1000)
        # All capital consumed, second buy should be skipped
        result = portfolio.process_signal(_signal("ETH-USD", Side.BUY), Decimal(50), 1001)
        assert result is None
        assert "ETH-USD" not in portfolio.positions

    def test_force_close_all_closes_everything(self) -> None:
        """Close all open positions via force_close_all."""
        portfolio = MultiAssetPortfolio(
            _INITIAL_CAPITAL,
            ExecutionConfig(position_size_pct=_HALF_POSITION),
        )
        portfolio.process_signal(_signal("BTC-USD", Side.BUY), Decimal(100), 1000)
        portfolio.process_signal(_signal("ETH-USD", Side.BUY), Decimal(50), 1001)
        trades = portfolio.force_close_all({"BTC-USD": Decimal(110), "ETH-USD": Decimal(55)}, 3000)
        assert len(trades) == _EXPECTED_FORCE_CLOSE_COUNT
        assert len(portfolio.positions) == 0

    def test_sell_on_no_position_ignored(self) -> None:
        """Ignore a SELL signal when no position is open for the symbol."""
        portfolio = MultiAssetPortfolio(_INITIAL_CAPITAL)
        result = portfolio.process_signal(_signal("BTC-USD", Side.SELL), Decimal(100), 1000)
        assert result is None

    def test_trades_accumulate(self) -> None:
        """Accumulate completed trades from round-trip closures."""
        portfolio = MultiAssetPortfolio(_INITIAL_CAPITAL)
        portfolio.process_signal(_signal("BTC-USD", Side.BUY), Decimal(100), 1000)
        portfolio.process_signal(_signal("BTC-USD", Side.SELL), Decimal(110), 2000)
        assert len(portfolio.trades) == 1
        assert portfolio.trades[0].pnl > Decimal(0)
