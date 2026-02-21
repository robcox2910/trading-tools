"""Test suite for the multi-asset backtest engine."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.multi_asset_engine import MultiAssetEngine
from trading_tools.apps.backtester.strategies.buy_and_hold import BuyAndHoldStrategy
from trading_tools.core.models import Candle, ExecutionConfig, Interval, Side, Signal

_INITIAL_CAPITAL = Decimal(10_000)
_HOUR_SECONDS = 3600
_EXPECTED_MIN_TRADES_TWO_SYMBOLS = 2


def _candle(symbol: str, ts: int, close: str) -> Candle:
    """Build a candle for the given symbol, timestamp, and close price."""
    c = Decimal(close)
    return Candle(
        symbol=symbol,
        timestamp=ts,
        open=c,
        high=c + Decimal(5),
        low=c - Decimal(5),
        close=c,
        volume=Decimal(100),
        interval=Interval.H1,
    )


class StubMultiProvider:
    """Stub candle provider that returns pre-configured candles per symbol."""

    def __init__(self, candles_by_symbol: dict[str, list[Candle]]) -> None:
        """Initialize with candles keyed by symbol."""
        self._candles = candles_by_symbol

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,  # noqa: ARG002
        start_ts: int,  # noqa: ARG002
        end_ts: int,  # noqa: ARG002
    ) -> list[Candle]:
        """Return pre-configured candles for the given symbol."""
        return self._candles.get(symbol, [])


class AlwaysBuyStrategy:
    """Emit BUY on the first candle for each symbol."""

    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "always_buy"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Emit BUY when no history exists for this symbol."""
        if not history:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=Decimal(1),
                reason="Always buy first candle",
            )
        return None


class TestMultiAssetEngine:
    """Test the multi-asset backtest engine."""

    @pytest.mark.asyncio
    async def test_single_symbol_produces_trades(self) -> None:
        """Produce trades for a single symbol like the regular engine."""
        candles = [_candle("BTC-USD", 1000 + i * _HOUR_SECONDS, str(100 + i)) for i in range(10)]
        provider = StubMultiProvider({"BTC-USD": candles})
        engine = MultiAssetEngine(
            provider=provider,
            strategy=BuyAndHoldStrategy(),
            symbols=["BTC-USD"],
            initial_capital=_INITIAL_CAPITAL,
        )
        result = await engine.run(Interval.H1, 0, 2**53)
        assert len(result.trades) >= 1
        assert all(t.symbol == "BTC-USD" for t in result.trades)

    @pytest.mark.asyncio
    async def test_two_symbols_produce_trades_for_both(self) -> None:
        """Produce trades for both symbols when running with two."""
        btc_candles = [
            _candle("BTC-USD", 1000 + i * _HOUR_SECONDS, str(100 + i)) for i in range(10)
        ]
        eth_candles = [_candle("ETH-USD", 1000 + i * _HOUR_SECONDS, str(50 + i)) for i in range(10)]
        provider = StubMultiProvider({"BTC-USD": btc_candles, "ETH-USD": eth_candles})
        engine = MultiAssetEngine(
            provider=provider,
            strategy=AlwaysBuyStrategy(),
            symbols=["BTC-USD", "ETH-USD"],
            initial_capital=_INITIAL_CAPITAL,
            execution_config=ExecutionConfig(position_size_pct=Decimal("0.5")),
        )
        result = await engine.run(Interval.H1, 0, 2**53)
        symbols_traded = {t.symbol for t in result.trades}
        assert "BTC-USD" in symbols_traded
        assert "ETH-USD" in symbols_traded

    @pytest.mark.asyncio
    async def test_candles_interleaved_by_timestamp(self) -> None:
        """Process candles from multiple symbols interleaved by timestamp."""
        btc_candles = [
            _candle("BTC-USD", 1000, "100"),
            _candle("BTC-USD", 3000, "110"),
        ]
        eth_candles = [
            _candle("ETH-USD", 2000, "50"),
            _candle("ETH-USD", 4000, "55"),
        ]
        provider = StubMultiProvider({"BTC-USD": btc_candles, "ETH-USD": eth_candles})
        engine = MultiAssetEngine(
            provider=provider,
            strategy=AlwaysBuyStrategy(),
            symbols=["BTC-USD", "ETH-USD"],
            initial_capital=_INITIAL_CAPITAL,
            execution_config=ExecutionConfig(position_size_pct=Decimal("0.5")),
        )
        result = await engine.run(Interval.H1, 0, 2**53)
        assert len(result.trades) >= _EXPECTED_MIN_TRADES_TWO_SYMBOLS
        # Candles should be ordered by timestamp in result
        timestamps = [c.timestamp for c in result.candles]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_no_candles_returns_empty_result(self) -> None:
        """Return an empty result when no candles are available."""
        provider = StubMultiProvider({})
        engine = MultiAssetEngine(
            provider=provider,
            strategy=BuyAndHoldStrategy(),
            symbols=["BTC-USD"],
            initial_capital=_INITIAL_CAPITAL,
        )
        result = await engine.run(Interval.H1, 0, 2**53)
        assert len(result.trades) == 0
        assert result.final_capital == _INITIAL_CAPITAL

    @pytest.mark.asyncio
    async def test_result_symbol_is_comma_separated(self) -> None:
        """Store the symbol as a comma-separated list of all symbols."""
        provider = StubMultiProvider({"BTC-USD": [_candle("BTC-USD", 1000, "100")]})
        engine = MultiAssetEngine(
            provider=provider,
            strategy=BuyAndHoldStrategy(),
            symbols=["BTC-USD", "ETH-USD"],
            initial_capital=_INITIAL_CAPITAL,
        )
        result = await engine.run(Interval.H1, 0, 2**53)
        assert result.symbol == "BTC-USD,ETH-USD"
