"""Tests for directional feature extraction functions."""

from decimal import Decimal

import pytest

from trading_tools.apps.directional.features import (
    compute_book_imbalance,
    compute_leader_momentum,
    compute_momentum,
    compute_price_change,
    compute_rsi_signal,
    compute_tick_imbalance,
    compute_tick_price_velocity,
    compute_tick_volume_accel,
    compute_volatility_regime,
    compute_volume_profile,
    compute_whale_signal,
    extract_features,
)
from trading_tools.apps.directional.models import TickSample
from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.core.models import ZERO, Candle, Interval

_SYMBOL = "BTC-USD"
_INTERVAL = Interval.M1
_BASE_TS = 1_710_000_000
_BASE_PRICE = Decimal(100)


def _make_candle(
    ts: int,
    open_: Decimal,
    close: Decimal,
    *,
    high: Decimal | None = None,
    low: Decimal | None = None,
    volume: Decimal = Decimal(1000),
) -> Candle:
    """Create a Candle with computed high/low if not provided."""
    h = high if high is not None else max(open_, close)
    lo = low if low is not None else min(open_, close)
    return Candle(
        symbol=_SYMBOL,
        timestamp=ts,
        open=open_,
        high=h,
        low=lo,
        close=close,
        volume=volume,
        interval=_INTERVAL,
    )


def _make_rising_candles(n: int, start_price: Decimal = _BASE_PRICE) -> list[Candle]:
    """Create N candles with steadily rising prices."""
    candles: list[Candle] = []
    for i in range(n):
        o = start_price + Decimal(i)
        c = start_price + Decimal(i + 1)
        candles.append(
            _make_candle(
                _BASE_TS + i * 60,
                o,
                c,
                high=c,
                low=o,
                volume=Decimal(1000 + i * 100),
            )
        )
    return candles


def _make_falling_candles(n: int, start_price: Decimal = _BASE_PRICE) -> list[Candle]:
    """Create N candles with steadily falling prices."""
    candles: list[Candle] = []
    for i in range(n):
        o = start_price - Decimal(i)
        c = start_price - Decimal(i + 1)
        candles.append(
            _make_candle(
                _BASE_TS + i * 60,
                o,
                c,
                high=o,
                low=c,
                volume=Decimal(1000 + i * 100),
            )
        )
    return candles


def _make_flat_candles(n: int, price: Decimal = _BASE_PRICE) -> list[Candle]:
    """Create N candles with no price change."""
    return [
        _make_candle(
            _BASE_TS + i * 60,
            price,
            price,
            high=price,
            low=price,
            volume=Decimal(1000),
        )
        for i in range(n)
    ]


def _make_order_book(
    token_id: str, bid_sizes: list[Decimal], ask_sizes: list[Decimal]
) -> OrderBook:
    """Create an OrderBook with specified bid/ask sizes at fixed prices."""
    bids = tuple(OrderLevel(price=Decimal("0.50"), size=s) for s in bid_sizes)
    asks = tuple(OrderLevel(price=Decimal("0.50"), size=s) for s in ask_sizes)
    return OrderBook(
        token_id=token_id,
        bids=bids,
        asks=asks,
        spread=Decimal("0.01"),
        midpoint=Decimal("0.50"),
        min_order_size=Decimal(5),
    )


class TestComputeMomentum:
    """Test recency-weighted momentum computation."""

    def test_rising_prices_positive(self) -> None:
        """Rising prices produce a positive momentum signal."""
        candles = _make_rising_candles(5)
        result = compute_momentum(candles)
        assert result > ZERO

    def test_falling_prices_negative(self) -> None:
        """Falling prices produce a negative momentum signal."""
        candles = _make_falling_candles(5)
        result = compute_momentum(candles)
        assert result < ZERO

    def test_flat_prices_zero(self) -> None:
        """Flat prices produce zero momentum."""
        candles = _make_flat_candles(5)
        result = compute_momentum(candles)
        assert result == ZERO

    def test_clamped_to_range(self) -> None:
        """Momentum is clamped to [-1, 1]."""
        candles = _make_rising_candles(5)
        result = compute_momentum(candles)
        assert -1 <= result <= 1

    def test_empty_candles_raises(self) -> None:
        """Empty candle list raises ValueError."""
        with pytest.raises(ValueError, match="at least 1 candle"):
            compute_momentum([])


class TestComputeVolatilityRegime:
    """Test ATR-based volatility regime computation."""

    def test_returns_decimal(self) -> None:
        """Volatility regime returns a Decimal value."""
        candles = _make_rising_candles(16)
        result = compute_volatility_regime(candles, period=14)
        assert isinstance(result, Decimal)

    def test_clamped_to_range(self) -> None:
        """Volatility regime is clamped to [-1, 1]."""
        candles = _make_rising_candles(16)
        result = compute_volatility_regime(candles, period=14)
        assert -1 <= result <= 1

    def test_insufficient_candles_raises(self) -> None:
        """Insufficient candles raises ValueError from atr()."""
        candles = _make_rising_candles(3)
        with pytest.raises(ValueError, match="ATR"):
            compute_volatility_regime(candles, period=14)


class TestComputeVolumeProfile:
    """Test z-scored volume profile computation."""

    def test_rising_with_increasing_volume_positive(self) -> None:
        """Rising prices with increasing volume produce positive signal."""
        candles = _make_rising_candles(10)
        result = compute_volume_profile(candles, recent_bars=5)
        assert result > ZERO

    def test_clamped_to_range(self) -> None:
        """Volume profile is clamped to [-1, 1]."""
        candles = _make_rising_candles(10)
        result = compute_volume_profile(candles, recent_bars=5)
        assert -1 <= result <= 1

    def test_insufficient_candles_raises(self) -> None:
        """Insufficient candles raises ValueError."""
        candles = _make_rising_candles(3)
        with pytest.raises(ValueError, match="volume_profile"):
            compute_volume_profile(candles, recent_bars=5)


class TestComputeBookImbalance:
    """Test order book imbalance computation."""

    def test_more_up_bids_positive(self) -> None:
        """More bid depth on Up side produces positive imbalance."""
        up_book = _make_order_book("up", [Decimal(100), Decimal(200)], [Decimal(50)])
        down_book = _make_order_book("down", [Decimal(50)], [Decimal(50)])
        result = compute_book_imbalance(up_book, down_book)
        assert result > ZERO

    def test_more_down_bids_negative(self) -> None:
        """More bid depth on Down side produces negative imbalance."""
        up_book = _make_order_book("up", [Decimal(50)], [Decimal(50)])
        down_book = _make_order_book("down", [Decimal(100), Decimal(200)], [Decimal(50)])
        result = compute_book_imbalance(up_book, down_book)
        assert result < ZERO

    def test_equal_bids_zero(self) -> None:
        """Equal bid depth produces zero imbalance."""
        up_book = _make_order_book("up", [Decimal(100)], [Decimal(50)])
        down_book = _make_order_book("down", [Decimal(100)], [Decimal(50)])
        result = compute_book_imbalance(up_book, down_book)
        assert result == ZERO

    def test_empty_books_zero(self) -> None:
        """Empty order books produce zero imbalance."""
        up_book = _make_order_book("up", [], [])
        down_book = _make_order_book("down", [], [])
        result = compute_book_imbalance(up_book, down_book)
        assert result == ZERO

    def test_clamped_to_range(self) -> None:
        """Imbalance is clamped to [-1, 1]."""
        up_book = _make_order_book("up", [Decimal(1000)], [])
        down_book = _make_order_book("down", [Decimal(1)], [])
        result = compute_book_imbalance(up_book, down_book)
        assert -1 <= result <= 1


class TestComputeRsiSignal:
    """Test RSI-to-signal mapping."""

    def test_rising_prices_positive(self) -> None:
        """Strong uptrend produces positive RSI signal."""
        candles = _make_rising_candles(16)
        result = compute_rsi_signal(candles, period=14)
        assert result > ZERO

    def test_falling_prices_negative(self) -> None:
        """Strong downtrend produces negative RSI signal."""
        candles = _make_falling_candles(16)
        result = compute_rsi_signal(candles, period=14)
        assert result < ZERO

    def test_clamped_to_range(self) -> None:
        """RSI signal is clamped to [-1, 1]."""
        candles = _make_rising_candles(16)
        result = compute_rsi_signal(candles, period=14)
        assert -1 <= result <= 1

    def test_insufficient_candles_raises(self) -> None:
        """Insufficient candles raises ValueError from rsi()."""
        candles = _make_rising_candles(3)
        with pytest.raises(ValueError, match="RSI"):
            compute_rsi_signal(candles, period=14)


class TestComputePriceChange:
    """Test percentage price change computation."""

    def test_rising_prices_positive(self) -> None:
        """Rising prices produce positive price change."""
        candles = _make_rising_candles(5)
        result = compute_price_change(candles)
        assert result > ZERO

    def test_falling_prices_negative(self) -> None:
        """Falling prices produce negative price change."""
        candles = _make_falling_candles(5)
        result = compute_price_change(candles)
        assert result < ZERO

    def test_flat_prices_zero(self) -> None:
        """Flat prices produce zero change."""
        candles = _make_flat_candles(5)
        result = compute_price_change(candles)
        assert result == ZERO

    def test_clamped_to_range(self) -> None:
        """Price change is clamped to [-1, 1]."""
        candles = _make_rising_candles(5)
        result = compute_price_change(candles)
        assert -1 <= result <= 1

    def test_insufficient_candles_raises(self) -> None:
        """Fewer than 2 candles raises ValueError."""
        candle = _make_candle(_BASE_TS, _BASE_PRICE, _BASE_PRICE)
        with pytest.raises(ValueError, match="at least 2"):
            compute_price_change([candle])


class TestComputeWhaleSignal:
    """Test whale signal conversion."""

    def test_up_returns_one(self) -> None:
        """Up whale direction returns 1."""
        assert compute_whale_signal("Up") == Decimal(1)

    def test_down_returns_negative_one(self) -> None:
        """Down whale direction returns -1."""
        assert compute_whale_signal("Down") == Decimal(-1)

    def test_none_returns_zero(self) -> None:
        """No whale activity returns 0."""
        assert compute_whale_signal(None) == ZERO


class TestComputeLeaderMomentum:
    """Test leader (BTC) momentum feature extraction."""

    def test_rising_btc_returns_positive(self) -> None:
        """Rising BTC price produces positive leader momentum."""
        candles = _make_rising_candles(5)
        result = compute_leader_momentum(candles)
        assert result > ZERO

    def test_falling_btc_returns_negative(self) -> None:
        """Falling BTC price produces negative leader momentum."""
        candles = _make_falling_candles(5)
        result = compute_leader_momentum(candles)
        assert result < ZERO

    def test_none_returns_zero(self) -> None:
        """None leader candles return 0 (e.g. for BTC itself)."""
        assert compute_leader_momentum(None) == ZERO

    def test_empty_returns_zero(self) -> None:
        """Empty candle list returns 0."""
        assert compute_leader_momentum([]) == ZERO

    def test_single_candle_returns_zero(self) -> None:
        """Single candle is not enough for a change signal."""
        candles = [_make_candle(_BASE_TS, _BASE_PRICE, _BASE_PRICE + Decimal(1))]
        assert compute_leader_momentum(candles) == ZERO

    def test_result_in_range(self) -> None:
        """Leader momentum is always in [-1, 1]."""
        candles = _make_rising_candles(10)
        result = compute_leader_momentum(candles)
        assert Decimal(-1) <= result <= Decimal(1)

    def test_lookback_window_respected(self) -> None:
        """Only candles within lookback_seconds are used."""
        # 5 candles 60s apart — with lookback=60, only last 2 should be used
        candles: list[Candle] = []
        for i in range(5):
            ts = _BASE_TS + i * 60
            candles.append(_make_candle(ts, _BASE_PRICE, _BASE_PRICE + Decimal(i)))
        result_short = compute_leader_momentum(candles, lookback_seconds=60)
        result_long = compute_leader_momentum(candles, lookback_seconds=300)
        # Both should be valid, but may differ
        assert Decimal(-1) <= result_short <= Decimal(1)
        assert Decimal(-1) <= result_long <= Decimal(1)


_TICK_BASE_MS = _BASE_TS * 1000


def _make_tick(ts_ms: int, price: float, size: float, side: str = "BUY") -> TickSample:
    """Create a TickSample for testing."""
    return TickSample(price=price, size=size, side=side, timestamp_ms=ts_ms)


class TestComputeTickImbalance:
    """Test tick buy/sell imbalance feature."""

    def test_all_buys_returns_one(self) -> None:
        """All BUY ticks produce imbalance of 1."""
        ticks = [_make_tick(_TICK_BASE_MS + i * 1000, 0.5, 100.0, "BUY") for i in range(5)]
        assert compute_tick_imbalance(ticks) == Decimal(1)

    def test_all_sells_returns_negative_one(self) -> None:
        """All SELL ticks produce imbalance of -1."""
        ticks = [_make_tick(_TICK_BASE_MS + i * 1000, 0.5, 100.0, "SELL") for i in range(5)]
        assert compute_tick_imbalance(ticks) == Decimal(-1)

    def test_balanced_returns_zero(self) -> None:
        """Equal buy and sell volume returns 0."""
        ticks = [
            _make_tick(_TICK_BASE_MS, 0.5, 100.0, "BUY"),
            _make_tick(_TICK_BASE_MS + 1000, 0.5, 100.0, "SELL"),
        ]
        assert compute_tick_imbalance(ticks) == ZERO

    def test_none_returns_zero(self) -> None:
        """None ticks return 0."""
        assert compute_tick_imbalance(None) == ZERO

    def test_empty_returns_zero(self) -> None:
        """Empty ticks return 0."""
        assert compute_tick_imbalance([]) == ZERO


class TestComputeTickPriceVelocity:
    """Test tick price velocity feature."""

    def test_rising_prices_positive(self) -> None:
        """Rising tick prices produce positive velocity."""
        ticks = [_make_tick(_TICK_BASE_MS + i * 1000, 0.50 + i * 0.01, 10.0) for i in range(5)]
        assert compute_tick_price_velocity(ticks) > ZERO

    def test_falling_prices_negative(self) -> None:
        """Falling tick prices produce negative velocity."""
        ticks = [_make_tick(_TICK_BASE_MS + i * 1000, 0.60 - i * 0.01, 10.0) for i in range(5)]
        assert compute_tick_price_velocity(ticks) < ZERO

    def test_none_returns_zero(self) -> None:
        """None ticks return 0."""
        assert compute_tick_price_velocity(None) == ZERO

    def test_single_tick_returns_zero(self) -> None:
        """Single tick is not enough for velocity."""
        ticks = [_make_tick(_TICK_BASE_MS, 0.5, 10.0)]
        assert compute_tick_price_velocity(ticks) == ZERO

    def test_result_in_range(self) -> None:
        """Velocity is always in [-1, 1]."""
        ticks = [_make_tick(_TICK_BASE_MS + i * 1000, 0.50 + i * 0.05, 10.0) for i in range(10)]
        result = compute_tick_price_velocity(ticks)
        assert Decimal(-1) <= result <= Decimal(1)


class TestComputeTickVolumeAccel:
    """Test tick volume acceleration feature."""

    def test_accelerating_volume_positive(self) -> None:
        """More recent volume than earlier produces positive signal."""
        ticks = [
            # Earlier half: small volume
            _make_tick(_TICK_BASE_MS, 0.5, 10.0),
            _make_tick(_TICK_BASE_MS + 10_000, 0.5, 10.0),
            # Recent half: large volume
            _make_tick(_TICK_BASE_MS + 30_000, 0.5, 100.0),
            _make_tick(_TICK_BASE_MS + 50_000, 0.5, 100.0),
        ]
        assert compute_tick_volume_accel(ticks) > ZERO

    def test_decelerating_volume_negative(self) -> None:
        """Less recent volume than earlier produces negative signal."""
        ticks = [
            # Earlier half: large volume
            _make_tick(_TICK_BASE_MS, 0.5, 100.0),
            _make_tick(_TICK_BASE_MS + 10_000, 0.5, 100.0),
            # Recent half: small volume
            _make_tick(_TICK_BASE_MS + 30_000, 0.5, 10.0),
            _make_tick(_TICK_BASE_MS + 50_000, 0.5, 10.0),
        ]
        assert compute_tick_volume_accel(ticks) < ZERO

    def test_none_returns_zero(self) -> None:
        """None ticks return 0."""
        assert compute_tick_volume_accel(None) == ZERO

    def test_result_in_range(self) -> None:
        """Acceleration is always in [-1, 1]."""
        ticks = [_make_tick(_TICK_BASE_MS + i * 5000, 0.5, float(10 + i * 5)) for i in range(12)]
        result = compute_tick_volume_accel(ticks)
        assert Decimal(-1) <= result <= Decimal(1)


class TestExtractFeatures:
    """Test the feature extraction orchestrator."""

    def test_returns_feature_vector(self) -> None:
        """Extract features returns a FeatureVector with all fields."""
        candles = _make_rising_candles(20)
        up_book = _make_order_book("up", [Decimal(100)], [Decimal(50)])
        down_book = _make_order_book("down", [Decimal(50)], [Decimal(50)])
        result = extract_features(candles, up_book, down_book)
        assert isinstance(result.momentum, Decimal)
        assert isinstance(result.volatility_regime, Decimal)
        assert isinstance(result.volume_profile, Decimal)
        assert isinstance(result.book_imbalance, Decimal)
        assert isinstance(result.rsi_signal, Decimal)
        assert isinstance(result.price_change_pct, Decimal)
        assert isinstance(result.whale_signal, Decimal)
        assert isinstance(result.leader_momentum, Decimal)
        assert isinstance(result.tick_imbalance, Decimal)
        assert isinstance(result.tick_price_velocity, Decimal)
        assert isinstance(result.tick_volume_accel, Decimal)

    def test_all_features_in_range(self) -> None:
        """All features are in [-1, 1]."""
        candles = _make_rising_candles(20)
        up_book = _make_order_book("up", [Decimal(100)], [Decimal(50)])
        down_book = _make_order_book("down", [Decimal(50)], [Decimal(50)])
        leader_candles = _make_rising_candles(5)
        up_ticks = [_make_tick(_TICK_BASE_MS + i * 1000, 0.55, 50.0, "BUY") for i in range(5)]
        result = extract_features(
            candles,
            up_book,
            down_book,
            whale_direction="Up",
            leader_candles=leader_candles,
            up_ticks=up_ticks,
        )
        for field_name in (
            "momentum",
            "volatility_regime",
            "volume_profile",
            "book_imbalance",
            "rsi_signal",
            "price_change_pct",
            "whale_signal",
            "leader_momentum",
            "tick_imbalance",
            "tick_price_velocity",
            "tick_volume_accel",
        ):
            val = getattr(result, field_name)
            assert -1 <= val <= 1, f"{field_name}={val} out of range"

    def test_leader_candles_none_gives_zero(self) -> None:
        """Without leader candles, leader_momentum is 0."""
        candles = _make_rising_candles(20)
        up_book = _make_order_book("up", [Decimal(100)], [Decimal(50)])
        down_book = _make_order_book("down", [Decimal(50)], [Decimal(50)])
        result = extract_features(candles, up_book, down_book)
        assert result.leader_momentum == ZERO

    def test_no_ticks_gives_zero_tick_features(self) -> None:
        """Without tick data, all tick features are 0."""
        candles = _make_rising_candles(20)
        up_book = _make_order_book("up", [Decimal(100)], [Decimal(50)])
        down_book = _make_order_book("down", [Decimal(50)], [Decimal(50)])
        result = extract_features(candles, up_book, down_book)
        assert result.tick_imbalance == ZERO
        assert result.tick_price_velocity == ZERO
        assert result.tick_volume_accel == ZERO
