"""Pure feature extraction functions for the directional trading algorithm.

Each function computes a single feature from market data and returns
a ``Decimal`` value normalised to ``[-1, 1]``.  Positive values indicate
an Up bias; negative values indicate a Down bias.  The ``extract_features``
orchestrator calls all individual functions and returns a ``FeatureVector``.

All functions are pure — no I/O, no side effects — and depend only on
``Candle`` objects from ``trading_tools.core.models`` and the shared
indicators library.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.backtester.indicators import atr, rsi, z_score
from trading_tools.core.models import HUNDRED, ONE, TWO, ZERO

from .models import FeatureVector, TickSample

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trading_tools.clients.polymarket.models import OrderBook
    from trading_tools.core.models import Candle

_FIFTY = Decimal(50)
_CLAMP_THRESHOLD = Decimal(5)


def _clamp(value: Decimal, lo: Decimal = -ONE, hi: Decimal = ONE) -> Decimal:
    """Clamp a value to the range ``[lo, hi]``.

    Args:
        value: The value to clamp.
        lo: Lower bound (default ``-1``).
        hi: Upper bound (default ``1``).

    Returns:
        The clamped value.

    """
    return max(lo, min(hi, value))


def compute_momentum(candles: Sequence[Candle]) -> Decimal:
    """Compute recency-weighted momentum from candle data.

    Each candle's return (close - open) is weighted by its position
    in the sequence: the most recent candle gets weight N, the oldest
    gets weight 1.  The weighted sum is normalised by the sum of
    weights and the average price to produce a value in ``[-1, 1]``.

    Args:
        candles: 1-min candles ordered oldest to newest (at least 1).

    Returns:
        Momentum signal in ``[-1, 1]``.  Positive = Up bias.

    Raises:
        ValueError: If no candles are provided.

    """
    if not candles:
        msg = "Need at least 1 candle for momentum"
        raise ValueError(msg)

    weighted_sum = ZERO
    weight_total = ZERO
    for i, candle in enumerate(candles):
        weight = Decimal(i + 1)
        weighted_sum += weight * (candle.close - candle.open)
        weight_total += weight

    avg_price = sum(c.close for c in candles) / Decimal(len(candles))
    if avg_price == ZERO:
        return ZERO

    normalised = weighted_sum / weight_total / avg_price * HUNDRED
    return _clamp(normalised)


def compute_volatility_regime(candles: Sequence[Candle], period: int = 14) -> Decimal:
    """Compute volatility regime from ATR relative to close price.

    High volatility (high ATR/close) maps toward ``-1`` (bearish /
    uncertain); low volatility maps toward ``1`` (bullish / trending).
    The raw ratio is inverted and normalised: ``1 - 2 * (ATR / close)``,
    clamped to ``[-1, 1]``.

    Args:
        candles: 1-min candles ordered oldest to newest
            (at least ``period + 1``).
        period: ATR period (default 14).

    Returns:
        Volatility regime signal in ``[-1, 1]``.

    Raises:
        ValueError: If insufficient candles for ATR computation.

    """
    atr_val = atr(candles, period)
    close = candles[-1].close
    if close == ZERO:
        return ZERO

    ratio = atr_val / close
    signal = ONE - TWO * ratio * HUNDRED
    return _clamp(signal)


def compute_volume_profile(candles: Sequence[Candle], recent_bars: int = 5) -> Decimal:
    """Compute volume profile as a z-scored recent-to-average volume ratio.

    Compare the average volume of the most recent ``recent_bars`` candles
    to the full sequence.  Higher recent volume relative to average is
    interpreted as confirming the prevailing price direction.

    Args:
        candles: 1-min candles ordered oldest to newest (at least
            ``recent_bars + 1``).
        recent_bars: Number of recent candles for the "recent" average.

    Returns:
        Volume profile signal in ``[-1, 1]``.

    Raises:
        ValueError: If insufficient candles.

    """
    min_candles = recent_bars + 1
    if len(candles) < min_candles:
        msg = f"Need at least {min_candles} candles for volume_profile, got {len(candles)}"
        raise ValueError(msg)

    volumes = [c.volume for c in candles]
    z = z_score(volumes)

    # Direction: positive z-score with rising prices = Up bias
    price_direction = candles[-1].close - candles[-recent_bars].close
    z = -abs(z) if price_direction < ZERO else abs(z)

    return _clamp(z / _CLAMP_THRESHOLD)


def compute_book_imbalance(up_book: OrderBook, down_book: OrderBook) -> Decimal:
    """Compute order book imbalance between Up and Down sides.

    Measure the asymmetry in bid depth between the two outcome books.
    More bid depth on the Up side (buyers willing to hold Up) relative
    to Down is interpreted as an Up signal.

    Formula: ``(up_bid_depth - down_bid_depth) / total_depth``.

    Args:
        up_book: Order book for the Up outcome token.
        down_book: Order book for the Down outcome token.

    Returns:
        Imbalance signal in ``[-1, 1]``.

    """
    up_bid = sum(level.size for level in up_book.bids)
    down_bid = sum(level.size for level in down_book.bids)
    total = up_bid + down_bid
    if total == ZERO:
        return ZERO
    return _clamp(Decimal(str(up_bid - down_bid)) / Decimal(str(total)))


def compute_rsi_signal(candles: Sequence[Candle], period: int = 14) -> Decimal:
    """Map RSI to a directional signal in ``[-1, 1]``.

    RSI 50 maps to 0 (neutral).  RSI 100 maps to 1 (strong Up).
    RSI 0 maps to -1 (strong Down).  Linear mapping:
    ``signal = (RSI - 50) / 50``.

    Args:
        candles: 1-min candles ordered oldest to newest
            (at least ``period + 1``).
        period: RSI period (default 14).

    Returns:
        RSI-based directional signal in ``[-1, 1]``.

    Raises:
        ValueError: If insufficient candles for RSI computation.

    """
    rsi_val = rsi(candles, period)
    return _clamp((rsi_val - _FIFTY) / _FIFTY)


def compute_price_change(candles: Sequence[Candle]) -> Decimal:
    """Compute percentage price change over the candle window.

    Simple first-to-last close change, normalised so that a 1% move
    maps to approximately 0.5 on the ``[-1, 1]`` scale.

    Args:
        candles: 1-min candles ordered oldest to newest (at least 2).

    Returns:
        Price change signal in ``[-1, 1]``.

    Raises:
        ValueError: If fewer than 2 candles.

    """
    min_candles = 2
    if len(candles) < min_candles:
        msg = f"Need at least 2 candles for price_change, got {len(candles)}"
        raise ValueError(msg)

    first_close = candles[0].close
    last_close = candles[-1].close
    if first_close == ZERO:
        return ZERO

    pct_change = (last_close - first_close) / first_close * HUNDRED
    # Scale: 1% -> 0.5
    scaled = pct_change * _FIFTY / HUNDRED
    return _clamp(scaled)


def compute_leader_momentum(
    leader_candles: Sequence[Candle] | None,
    lookback_seconds: int = 60,
) -> Decimal:
    """Compute the leader asset's (BTC) recent price change as a signal.

    Measure BTC's price change over the last ``lookback_seconds`` to
    capture the "BTC leads altcoins by seconds" effect.  Normalise to
    ``[-1, 1]`` using the same scaling as ``compute_price_change`` (1%
    move -> ~0.5).

    Return ``0`` when *leader_candles* is ``None`` or empty (e.g. for
    BTC itself where this feature would double-count momentum).

    Args:
        leader_candles: Recent 1-min candles for the leader asset (BTC),
            ordered oldest to newest.  ``None`` disables the feature.
        lookback_seconds: Seconds of history to consider (default 60).

    Returns:
        Leader momentum signal in ``[-1, 1]``.

    """
    if not leader_candles:
        return ZERO

    # Use only candles within the lookback window from the most recent
    latest_ts = leader_candles[-1].timestamp
    cutoff = latest_ts - lookback_seconds
    recent = [c for c in leader_candles if c.timestamp >= cutoff]
    if len(recent) < 2:  # noqa: PLR2004 — need at least 2 candles for a change
        return ZERO

    first_close = recent[0].close
    last_close = recent[-1].close
    if first_close == ZERO:
        return ZERO

    pct_change = (last_close - first_close) / first_close * HUNDRED
    scaled = pct_change * _FIFTY / HUNDRED
    return _clamp(scaled)


def compute_whale_signal(whale_direction: str | None) -> Decimal:
    """Convert a whale directional signal to a normalised feature value.

    Map the whale's net positioning (from the whale_trades DB) to a
    ``[-1, 1]`` signal.  ``"Up"`` maps to ``1``, ``"Down"`` maps to
    ``-1``, and ``None`` (no whale activity) maps to ``0``.

    Args:
        whale_direction: ``"Up"``, ``"Down"``, or ``None``.

    Returns:
        Whale signal in ``[-1, 1]``.

    """
    if whale_direction == "Up":
        return ONE
    if whale_direction == "Down":
        return -ONE
    return ZERO


def compute_tick_imbalance(
    ticks: Sequence[TickSample] | None,
    lookback_ms: int = 60_000,
) -> Decimal:
    """Compute buy/sell volume imbalance from recent Polymarket ticks.

    Measure the net directional flow for the Up token:
    ``(buy_volume - sell_volume) / total_volume``.  Positive values
    indicate net buying (bullish), negative = net selling (bearish).

    Args:
        ticks: Recent Up-token tick samples, ordered by timestamp.
            ``None`` or empty disables the feature.
        lookback_ms: Milliseconds of history to consider (default 60s).

    Returns:
        Imbalance signal in ``[-1, 1]``.

    """
    if not ticks:
        return ZERO

    cutoff = ticks[-1].timestamp_ms - lookback_ms
    recent = [t for t in ticks if t.timestamp_ms >= cutoff]
    if not recent:
        return ZERO

    buy_vol = sum(t.size for t in recent if t.side == "BUY")
    sell_vol = sum(t.size for t in recent if t.side == "SELL")
    total = buy_vol + sell_vol
    if total == 0.0:
        return ZERO

    return _clamp(Decimal(str((buy_vol - sell_vol) / total)))


def compute_tick_price_velocity(
    ticks: Sequence[TickSample] | None,
    lookback_ms: int = 30_000,
) -> Decimal:
    """Compute the rate of price change from recent tick prices.

    Fit a simple first-to-last price change over the lookback window,
    normalised so that a 1% move maps to ~0.5 on the ``[-1, 1]`` scale
    (same scaling as ``compute_price_change``).

    Args:
        ticks: Recent Up-token tick samples, ordered by timestamp.
            ``None`` or empty disables the feature.
        lookback_ms: Milliseconds of history to consider (default 30s).

    Returns:
        Price velocity signal in ``[-1, 1]``.

    """
    if not ticks:
        return ZERO

    cutoff = ticks[-1].timestamp_ms - lookback_ms
    recent = [t for t in ticks if t.timestamp_ms >= cutoff]
    if len(recent) < 2:  # noqa: PLR2004 — need at least 2 ticks
        return ZERO

    first_price = recent[0].price
    last_price = recent[-1].price
    if first_price == 0.0:
        return ZERO

    pct_change = (last_price - first_price) / first_price * 100
    scaled = Decimal(str(pct_change)) * _FIFTY / HUNDRED
    return _clamp(scaled)


def compute_tick_volume_accel(
    ticks: Sequence[TickSample] | None,
    lookback_ms: int = 60_000,
) -> Decimal:
    """Compute volume acceleration from recent Polymarket ticks.

    Split the lookback window into two equal halves.  Compare the total
    volume in the recent half to the earlier half.  Accelerating volume
    near the decision time suggests stronger conviction.

    The ratio ``recent / earlier`` is mapped to ``[-1, 1]``:
    ratio > 1 (accelerating) → positive, ratio < 1 (decelerating) →
    negative.  Formula: ``clamp((ratio - 1) * 2)``.

    Args:
        ticks: Recent Up-token tick samples, ordered by timestamp.
            ``None`` or empty disables the feature.
        lookback_ms: Milliseconds of history to consider (default 60s).

    Returns:
        Volume acceleration signal in ``[-1, 1]``.

    """
    if not ticks:
        return ZERO

    latest_ms = ticks[-1].timestamp_ms
    cutoff = latest_ms - lookback_ms
    midpoint_ms = latest_ms - lookback_ms // 2

    earlier_vol = sum(t.size for t in ticks if cutoff <= t.timestamp_ms < midpoint_ms)
    recent_vol = sum(t.size for t in ticks if t.timestamp_ms >= midpoint_ms)

    if earlier_vol == 0.0:
        return ZERO if recent_vol == 0.0 else ONE

    ratio = recent_vol / earlier_vol
    signal = Decimal(str((ratio - 1.0) * 2.0))
    return _clamp(signal)


def extract_features(
    candles: Sequence[Candle],
    up_book: OrderBook,
    down_book: OrderBook,
    *,
    atr_period: int = 14,
    rsi_period: int = 14,
    volume_recent_bars: int = 5,
    whale_direction: str | None = None,
    leader_candles: Sequence[Candle] | None = None,
    up_ticks: Sequence[TickSample] | None = None,
) -> FeatureVector:
    """Extract all features from market data and return a ``FeatureVector``.

    Orchestrate all individual feature functions.  Each feature is
    independently normalised to ``[-1, 1]``.

    Args:
        candles: 1-min Binance candles for the asset, ordered oldest
            to newest.
        up_book: Order book for the Up outcome token.
        down_book: Order book for the Down outcome token.
        atr_period: Period for ATR in volatility regime computation.
        rsi_period: Period for RSI computation.
        volume_recent_bars: Recent bars for volume profile.
        whale_direction: Whale net positioning (``"Up"``, ``"Down"``,
            or ``None``).
        leader_candles: Recent BTC 1-min candles for leader momentum.
            Pass ``None`` for BTC itself to avoid double-counting.
        up_ticks: Recent Polymarket tick samples for the Up token.
            Pass ``None`` when tick data is unavailable.

    Returns:
        A ``FeatureVector`` with all eleven features populated.

    """
    return FeatureVector(
        momentum=compute_momentum(candles),
        volatility_regime=compute_volatility_regime(candles, period=atr_period),
        volume_profile=compute_volume_profile(candles, recent_bars=volume_recent_bars),
        book_imbalance=compute_book_imbalance(up_book, down_book),
        rsi_signal=compute_rsi_signal(candles, period=rsi_period),
        price_change_pct=compute_price_change(candles),
        whale_signal=compute_whale_signal(whale_direction),
        leader_momentum=compute_leader_momentum(leader_candles),
        tick_imbalance=compute_tick_imbalance(up_ticks),
        tick_price_velocity=compute_tick_price_velocity(up_ticks),
        tick_volume_accel=compute_tick_volume_accel(up_ticks),
    )
