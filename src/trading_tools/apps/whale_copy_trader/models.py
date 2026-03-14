"""Data models for the whale copy-trading service.

Define immutable value objects for copy signals (detected whale bias)
and copy results (paper or live trade outcomes).
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CopySignal:
    """A detected whale directional bias worth copying.

    Represent a market where the whale has taken a strong enough position
    (above the bias threshold) on a parseable BTC/ETH Up/Down market
    whose time window has not yet expired.

    Attributes:
        condition_id: Polymarket market condition identifier.
        title: Human-readable market title.
        asset: Spot trading pair (``"BTC-USD"`` or ``"ETH-USD"``).
        favoured_side: Whale's favoured direction (``"Up"`` or ``"Down"``).
        bias_ratio: Volume ratio of favoured side to unfavoured.
        trade_count: Number of whale trades in this market.
        window_start_ts: UTC epoch seconds when the market window opens.
        window_end_ts: UTC epoch seconds when the market window closes.
        detected_at: UTC epoch seconds when the signal was detected.

    """

    condition_id: str
    title: str
    asset: str
    favoured_side: str
    bias_ratio: Decimal
    trade_count: int
    window_start_ts: int
    window_end_ts: int
    detected_at: int


@dataclass(frozen=True)
class CopyResult:
    """Outcome of a copy trade, either paper or live.

    Track entry/exit prices, quantity, P&L, and whether the trade was
    executed on-chain or only simulated.

    Attributes:
        signal: The copy signal that triggered this trade.
        entry_price: Price at which the position was opened.
        quantity: Number of tokens traded.
        entry_time: UTC epoch seconds when the position was opened.
        exit_price: Price at which the position was closed, or ``None``.
        exit_time: UTC epoch seconds when closed, or ``None`` if still open.
        pnl: Realised profit/loss in USDC (zero while open).
        is_paper: ``True`` for simulated trades, ``False`` for live.
        order_id: CLOB order ID for live trades, ``None`` for paper.

    """

    signal: CopySignal
    entry_price: Decimal
    quantity: Decimal
    entry_time: int
    exit_price: Decimal | None = None
    exit_time: int | None = None
    pnl: Decimal = Decimal(0)
    is_paper: bool = True
    order_id: str | None = None
