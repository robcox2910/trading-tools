"""Configuration for the whale copy-trading service.

Define an immutable configuration dataclass that holds all tunable
parameters: polling frequency, signal thresholds, position sizing,
and capital management.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class WhaleCopyConfig:
    """Immutable configuration for the whale copy-trading service.

    Control polling behaviour, signal detection thresholds, and position
    sizing. All monetary values use ``Decimal`` for precision.

    Attributes:
        whale_address: Proxy wallet address of the whale to copy.
        poll_interval: Seconds between incremental DB polls (lower = faster).
        lookback_seconds: Rolling window size for trade accumulation.
        min_bias: Minimum bias ratio to trigger a copy signal.
        min_trades: Minimum trade count per market to trigger a signal.
        capital: Starting capital for paper mode (USDC).
        max_position_pct: Maximum fraction of capital per single trade.
        use_market_orders: Use market orders for fastest execution.

    """

    whale_address: str
    poll_interval: int = 5
    lookback_seconds: int = 300
    min_bias: Decimal = Decimal("1.5")
    min_trades: int = 3
    capital: Decimal = Decimal(100)
    max_position_pct: Decimal = Decimal("0.10")
    use_market_orders: bool = True
