"""Tests for portfolio state tracking."""

from decimal import Decimal

from trading_tools.apps.backtester.portfolio import Portfolio
from trading_tools.core.models import ExecutionConfig, Side, Signal, Trade

EXPECTED_ROUND_TRIP_COUNT = 2


def _buy_signal(symbol: str = "BTC-USD") -> Signal:
    return Signal(side=Side.BUY, symbol=symbol, strength=Decimal(1), reason="test buy")


def _sell_signal(symbol: str = "BTC-USD") -> Signal:
    return Signal(side=Side.SELL, symbol=symbol, strength=Decimal(1), reason="test sell")


class TestPortfolio:
    """Tests for Portfolio state management."""

    def test_initial_state(self) -> None:
        """Test portfolio starts with given capital and no position."""
        p = Portfolio(Decimal(10000))
        assert p.capital == Decimal(10000)
        assert p.position is None
        assert p.trades == []

    def test_buy_opens_position(self) -> None:
        """Test buy signal opens a new position."""
        p = Portfolio(Decimal(10000))
        result = p.process_signal(_buy_signal(), Decimal(100), 1000)
        assert result is None
        assert p.position is not None
        assert p.position.entry_price == Decimal(100)
        assert p.position.quantity == Decimal(100)
        assert p.capital == Decimal(0)

    def test_sell_closes_position(self) -> None:
        """Test sell signal closes an open position."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        trade = p.process_signal(_sell_signal(), Decimal(110), 2000)
        assert isinstance(trade, Trade)
        assert trade.pnl == Decimal(1000)
        assert p.position is None
        assert p.capital == Decimal(11000)

    def test_sell_without_position_ignored(self) -> None:
        """Test sell signal without open position is ignored."""
        p = Portfolio(Decimal(10000))
        result = p.process_signal(_sell_signal(), Decimal(100), 1000)
        assert result is None
        assert p.capital == Decimal(10000)

    def test_buy_with_existing_position_ignored(self) -> None:
        """Test buy signal with existing position is ignored."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        result = p.process_signal(_buy_signal(), Decimal(110), 2000)
        assert result is None
        assert p.position is not None
        assert p.position.entry_price == Decimal(100)

    def test_force_close(self) -> None:
        """Test force-closing an open position."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        trade = p.force_close(Decimal(120), 3000)
        assert isinstance(trade, Trade)
        assert trade.exit_price == Decimal(120)
        assert p.position is None
        assert p.capital == Decimal(12000)

    def test_force_close_no_position(self) -> None:
        """Test force-close with no open position returns None."""
        p = Portfolio(Decimal(10000))
        result = p.force_close(Decimal(100), 1000)
        assert result is None

    def test_multiple_round_trips(self) -> None:
        """Test multiple buy-sell round trips accumulate correctly."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        p.process_signal(_sell_signal(), Decimal(110), 2000)
        p.process_signal(_buy_signal(), Decimal(110), 3000)
        p.process_signal(_sell_signal(), Decimal(121), 4000)
        assert len(p.trades) == EXPECTED_ROUND_TRIP_COUNT
        assert p.capital == Decimal(12100)

    def test_losing_trade(self) -> None:
        """Test losing trade reduces capital."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        trade = p.process_signal(_sell_signal(), Decimal(90), 2000)
        assert trade is not None
        assert trade.pnl == Decimal(-1000)
        assert p.capital == Decimal(9000)

    def test_trades_list_is_copy(self) -> None:
        """Test that trades property returns a copy."""
        p = Portfolio(Decimal(10000))
        trades = p.trades
        trades.append(None)  # type: ignore[arg-type]
        assert p.trades == []


class TestPortfolioWithFees:
    """Tests for Portfolio with execution fees and slippage."""

    def test_slippage_worsens_entry_price(self) -> None:
        """Test that slippage increases the effective entry price."""
        cfg = ExecutionConfig(slippage_pct=Decimal("0.01"))
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        assert p.position is not None
        expected_price = Decimal(101)  # 100 * 1.01
        assert p.position.entry_price == expected_price

    def test_slippage_worsens_exit_price(self) -> None:
        """Test that slippage decreases the effective exit price."""
        cfg = ExecutionConfig(slippage_pct=Decimal("0.01"))
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        trade = p.process_signal(_sell_signal(), Decimal(110), 2000)
        assert trade is not None
        expected_exit = Decimal("108.9")  # 110 * 0.99
        assert trade.exit_price == expected_exit

    def test_fees_deducted(self) -> None:
        """Test that entry and exit fees are recorded on the trade."""
        cfg = ExecutionConfig(
            taker_fee_pct=Decimal("0.001"),
            maker_fee_pct=Decimal("0.001"),
        )
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        trade = p.process_signal(_sell_signal(), Decimal(100), 2000)
        assert trade is not None
        assert trade.entry_fee == Decimal(10)  # 10000 * 0.001
        assert trade.exit_fee > Decimal(0)

    def test_round_trip_with_fees_reduces_capital(self) -> None:
        """Test that a flat-price round trip loses money due to fees."""
        cfg = ExecutionConfig(
            taker_fee_pct=Decimal("0.001"),
            maker_fee_pct=Decimal("0.001"),
        )
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        p.process_signal(_sell_signal(), Decimal(100), 2000)
        assert p.capital < Decimal(10000)


class TestPortfolioPositionSizing:
    """Tests for Portfolio position sizing."""

    def test_half_position_uses_half_capital(self) -> None:
        """Test that position_size_pct=0.5 deploys only half the capital."""
        cfg = ExecutionConfig(position_size_pct=Decimal("0.5"))
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        assert p.position is not None
        expected_qty = Decimal(50)  # 5000 / 100
        assert p.position.quantity == expected_qty

    def test_remaining_capital_preserved(self) -> None:
        """Test that unused capital is preserved when position_size_pct < 1."""
        cfg = ExecutionConfig(position_size_pct=Decimal("0.5"))
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        expected_remaining = Decimal(5000)
        assert p.capital == expected_remaining

    def test_default_full_deployment(self) -> None:
        """Test that default position_size_pct=1 deploys all capital."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        assert p.capital == Decimal(0)
        assert p.position is not None
        expected_qty = Decimal(100)
        assert p.position.quantity == expected_qty
