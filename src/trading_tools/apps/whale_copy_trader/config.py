"""Configuration for the whale copy-trading service.

Define an immutable configuration dataclass that holds all tunable
parameters: polling frequency, signal thresholds, position sizing,
spread arbitrage targets, and capital management.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class WhaleCopyConfig:
    """Immutable configuration for the whale copy-trading service.

    Control polling behaviour, signal detection thresholds, position
    sizing, and temporal spread arbitrage parameters. All monetary
    values use ``Decimal`` for precision.

    Attributes:
        whale_address: Proxy wallet address of the whale to copy.
        poll_interval: Seconds between incremental DB polls (lower = faster).
        lookback_seconds: Rolling window size for trade accumulation.
        min_bias: Minimum bias ratio to trigger a copy signal.
        min_trades: Minimum trade count per market to trigger a signal.
        min_time_to_start: Minimum seconds before window opens to act on a signal.
        capital: Starting capital for paper mode (USDC).
        max_position_pct: Maximum fraction of capital per single trade.
        max_window_seconds: Maximum market window duration to trade (0 = no limit).
        use_market_orders: Use market orders (FOK) instead of limit orders (GTC).
        max_spread_cost: Maximum combined cost of both legs to trigger a hedge.
            A value below 1.0 guarantees profit (e.g. 0.95 = min 5% return).
        max_entry_price: Maximum price for the directional entry leg. Skip
            markets where the favoured side has already moved above this.

    """

    whale_address: str
    poll_interval: int = 5
    lookback_seconds: int = 900
    min_bias: Decimal = Decimal("1.3")
    min_trades: int = 2
    min_time_to_start: int = 0
    capital: Decimal = Decimal(100)
    max_position_pct: Decimal = Decimal("0.10")
    max_window_seconds: int = 0
    use_market_orders: bool = False
    max_spread_cost: Decimal = Decimal("0.95")
    max_entry_price: Decimal = Decimal("0.65")
