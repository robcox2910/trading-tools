"""Simulate prediction market snapshots from exchange candle data.

Convert 1-minute Binance candles into synthetic ``MarketSnapshot`` sequences
that model how YES/NO prices would evolve in each 5-minute "Up or Down"
prediction market window. At the window open, prices start near 50/50. As
the candle data reveals which direction the price is moving, the YES/NO
prices drift toward certainty, converging near 99/1 by the final minute.
"""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ZERO, Candle

_DEFAULT_SCALE = Decimal(15)
_HALF = Decimal("0.5")
_MAX_DISPLACEMENT = Decimal("0.495")
_WINDOW_SECONDS = 300
_MINUTE_SECONDS = 60
_ROUND_4 = Decimal("0.0001")


class SnapshotSimulator:
    """Simulate prediction market snapshots from exchange candle data.

    Transform a sequence of 1-minute candles covering a 5-minute window
    into ``MarketSnapshot`` objects with synthetic YES/NO prices. The
    price model uses the running price change relative to window open,
    scaled by time fraction and a configurable sensitivity factor.

    Args:
        scale_factor: Multiplier controlling how quickly prices move away
            from 50/50. Higher values mean earlier convergence toward
            certainty. Default is 15.

    """

    def __init__(self, scale_factor: Decimal = _DEFAULT_SCALE) -> None:
        """Initialize the simulator with a price sensitivity scale factor.

        Args:
            scale_factor: Sensitivity multiplier for price drift (must be > 0).

        Raises:
            ValueError: If scale_factor is not positive.

        """
        if scale_factor <= ZERO:
            msg = f"scale_factor must be positive, got {scale_factor}"
            raise ValueError(msg)
        self._scale = scale_factor

    def simulate_window(
        self,
        symbol: str,
        window_open_ts: int,
        candles: list[Candle],
    ) -> list[MarketSnapshot]:
        """Generate synthetic market snapshots for one 5-minute window.

        Use up to 5 one-minute candles to produce one ``MarketSnapshot``
        per candle. The YES price drifts from 0.50 based on how much the
        asset price has moved relative to the window open, scaled by both
        elapsed time and the configured ``scale_factor``.

        Args:
            symbol: Trading pair identifier (e.g. ``"BTC-USD"``).
            window_open_ts: Unix epoch seconds of the 5-minute window start.
            candles: Up to 5 one-minute candles within this window.

        Returns:
            List of ``MarketSnapshot`` objects, one per candle.

        Raises:
            ValueError: If candles list is empty.

        """
        if not candles:
            msg = "candles list must not be empty"
            raise ValueError(msg)

        condition_id = f"{symbol}_{window_open_ts}"
        end_ts = window_open_ts + _WINDOW_SECONDS
        end_date = datetime.fromtimestamp(end_ts, tz=UTC).isoformat()
        window_open_price = candles[0].open
        empty_book = OrderBook(
            token_id="",
            bids=(),
            asks=(),
            spread=ZERO,
            midpoint=_HALF,
        )

        snapshots: list[MarketSnapshot] = []
        for i, candle in enumerate(candles):
            minute_index = i + 1  # 1-based: minute 1 through 5
            time_frac = Decimal(minute_index) / Decimal(5)

            if window_open_price != ZERO:
                change_pct = (candle.close - window_open_price) / window_open_price
            else:
                change_pct = ZERO

            confidence = abs(change_pct) * self._scale * time_frac
            sign = Decimal(1) if change_pct >= ZERO else Decimal(-1)
            displacement = min(confidence, _MAX_DISPLACEMENT)

            yes_price = (_HALF + sign * displacement).quantize(
                _ROUND_4,
                rounding=ROUND_HALF_UP,
            )
            no_price = (Decimal(1) - yes_price).quantize(
                _ROUND_4,
                rounding=ROUND_HALF_UP,
            )

            question = f"Will {symbol} go up in the next 5 minutes?"
            snapshots.append(
                MarketSnapshot(
                    condition_id=condition_id,
                    question=question,
                    timestamp=candle.timestamp,
                    yes_price=yes_price,
                    no_price=no_price,
                    order_book=empty_book,
                    volume=ZERO,
                    liquidity=ZERO,
                    end_date=end_date,
                ),
            )

        return snapshots
