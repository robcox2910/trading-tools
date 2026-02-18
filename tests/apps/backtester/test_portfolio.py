"""Tests for portfolio state tracking."""

from decimal import Decimal

from trading_tools.apps.backtester.portfolio import Portfolio
from trading_tools.core.models import Side, Signal, Trade


def _buy_signal(symbol: str = "BTC-USD") -> Signal:
    return Signal(side=Side.BUY, symbol=symbol, strength=Decimal("1"), reason="test buy")


def _sell_signal(symbol: str = "BTC-USD") -> Signal:
    return Signal(side=Side.SELL, symbol=symbol, strength=Decimal("1"), reason="test sell")


class TestPortfolio:
    def test_initial_state(self) -> None:
        p = Portfolio(Decimal("10000"))
        assert p.capital == Decimal("10000")
        assert p.position is None
        assert p.trades == []

    def test_buy_opens_position(self) -> None:
        p = Portfolio(Decimal("10000"))
        result = p.process_signal(_buy_signal(), Decimal("100"), 1000)
        assert result is None
        assert p.position is not None
        assert p.position.entry_price == Decimal("100")
        assert p.position.quantity == Decimal("100")
        assert p.capital == Decimal("0")

    def test_sell_closes_position(self) -> None:
        p = Portfolio(Decimal("10000"))
        p.process_signal(_buy_signal(), Decimal("100"), 1000)
        trade = p.process_signal(_sell_signal(), Decimal("110"), 2000)
        assert isinstance(trade, Trade)
        assert trade.pnl == Decimal("1000")
        assert p.position is None
        assert p.capital == Decimal("11000")

    def test_sell_without_position_ignored(self) -> None:
        p = Portfolio(Decimal("10000"))
        result = p.process_signal(_sell_signal(), Decimal("100"), 1000)
        assert result is None
        assert p.capital == Decimal("10000")

    def test_buy_with_existing_position_ignored(self) -> None:
        p = Portfolio(Decimal("10000"))
        p.process_signal(_buy_signal(), Decimal("100"), 1000)
        result = p.process_signal(_buy_signal(), Decimal("110"), 2000)
        assert result is None
        assert p.position is not None
        assert p.position.entry_price == Decimal("100")

    def test_force_close(self) -> None:
        p = Portfolio(Decimal("10000"))
        p.process_signal(_buy_signal(), Decimal("100"), 1000)
        trade = p.force_close(Decimal("120"), 3000)
        assert isinstance(trade, Trade)
        assert trade.exit_price == Decimal("120")
        assert p.position is None
        assert p.capital == Decimal("12000")

    def test_force_close_no_position(self) -> None:
        p = Portfolio(Decimal("10000"))
        result = p.force_close(Decimal("100"), 1000)
        assert result is None

    def test_multiple_round_trips(self) -> None:
        p = Portfolio(Decimal("10000"))
        p.process_signal(_buy_signal(), Decimal("100"), 1000)
        p.process_signal(_sell_signal(), Decimal("110"), 2000)
        p.process_signal(_buy_signal(), Decimal("110"), 3000)
        p.process_signal(_sell_signal(), Decimal("121"), 4000)
        assert len(p.trades) == 2
        assert p.capital == Decimal("12100")

    def test_losing_trade(self) -> None:
        p = Portfolio(Decimal("10000"))
        p.process_signal(_buy_signal(), Decimal("100"), 1000)
        trade = p.process_signal(_sell_signal(), Decimal("90"), 2000)
        assert trade is not None
        assert trade.pnl == Decimal("-1000")
        assert p.capital == Decimal("9000")

    def test_trades_list_is_copy(self) -> None:
        p = Portfolio(Decimal("10000"))
        trades = p.trades
        trades.append(None)  # type: ignore[arg-type]
        assert p.trades == []
