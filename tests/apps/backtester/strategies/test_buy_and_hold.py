"""Test suite for the buy-and-hold benchmark strategy."""

from decimal import Decimal

from trading_tools.apps.backtester.strategies.buy_and_hold import BuyAndHoldStrategy
from trading_tools.core.models import Candle, Interval, Side
from trading_tools.core.protocols import TradingStrategy


def _candle(ts: int) -> Candle:
    """Build a minimal candle at the given timestamp."""
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=Decimal(100),
        high=Decimal(105),
        low=Decimal(95),
        close=Decimal(100),
        volume=Decimal(10),
        interval=Interval.H1,
    )


class TestBuyAndHoldStrategy:
    """Test the buy-and-hold strategy signal logic."""

    def test_first_candle_emits_buy(self) -> None:
        """Emit a BUY signal on the very first candle."""
        strategy = BuyAndHoldStrategy()
        signal = strategy.on_candle(_candle(1000), [])
        assert signal is not None
        assert signal.side == Side.BUY

    def test_subsequent_candles_return_none(self) -> None:
        """Return None for all candles after the first."""
        strategy = BuyAndHoldStrategy()
        first = _candle(1000)
        second = _candle(2000)
        assert strategy.on_candle(second, [first]) is None

    def test_name_is_buy_and_hold(self) -> None:
        """Return the expected strategy name."""
        assert BuyAndHoldStrategy().name == "buy_and_hold"

    def test_satisfies_trading_strategy_protocol(self) -> None:
        """Satisfy the TradingStrategy protocol at runtime."""
        assert isinstance(BuyAndHoldStrategy(), TradingStrategy)

    def test_signal_targets_candle_symbol(self) -> None:
        """Target the symbol from the candle in the BUY signal."""
        candle = Candle(
            symbol="ETH-USD",
            timestamp=1000,
            open=Decimal(100),
            high=Decimal(105),
            low=Decimal(95),
            close=Decimal(100),
            volume=Decimal(10),
            interval=Interval.H1,
        )
        signal = BuyAndHoldStrategy().on_candle(candle, [])
        assert signal is not None
        assert signal.symbol == "ETH-USD"
