"""Data models for the whale copy-trading service.

Define value objects for copy signals (detected whale bias) and open
positions that accumulate top-ups as the whale's conviction changes.
"""

from dataclasses import dataclass, field
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


def _empty_str_list() -> list[str]:
    """Return an empty list[str] for dataclass default_factory."""
    return []


@dataclass
class OpenPosition:
    """A live (open) copy position that accumulates top-ups.

    Track the weighted-average entry price and total quantity across
    multiple entries as the whale adjusts their position. Mutable so
    top-ups and flips can update it in place.

    Attributes:
        signal: The most recent signal for this market.
        side: Current position direction (``"Up"`` or ``"Down"``).
        entry_price: Weighted-average entry price across all fills.
        quantity: Total token quantity across all fills.
        cost_basis: Total USDC spent (entry_price * quantity).
        entry_time: UTC epoch seconds of the first entry.
        last_bias: Bias ratio at the most recent entry or top-up.
        is_paper: ``True`` for simulated trades, ``False`` for live.
        order_ids: CLOB order IDs for live trades.

    """

    signal: CopySignal
    side: str
    entry_price: Decimal
    quantity: Decimal
    cost_basis: Decimal
    entry_time: int
    last_bias: Decimal
    is_paper: bool = True
    order_ids: list[str] = field(default_factory=_empty_str_list)

    def add_fill(self, price: Decimal, qty: Decimal) -> None:
        """Add a new fill and update the weighted-average entry price.

        Args:
            price: Entry price of the new fill.
            qty: Quantity of the new fill.

        """
        new_cost = price * qty
        self.cost_basis += new_cost
        self.quantity += qty
        self.entry_price = (self.cost_basis / self.quantity).quantize(Decimal("0.0001"))


@dataclass(frozen=True)
class CopyResult:
    """Outcome of a closed copy trade.

    Immutable record of a position that has been closed, with final
    P&L calculated from the entry and exit prices.

    Attributes:
        signal: The copy signal that triggered this trade.
        side: Position direction when closed.
        entry_price: Weighted-average entry price.
        quantity: Total tokens traded.
        entry_time: UTC epoch seconds when first opened.
        exit_price: Price at which the position was closed, or ``None``.
        exit_time: UTC epoch seconds when closed, or ``None`` if still open.
        pnl: Realised profit/loss in USDC.
        is_paper: ``True`` for simulated trades, ``False`` for live.
        order_ids: All CLOB order IDs for this position.

    """

    signal: CopySignal
    side: str
    entry_price: Decimal
    quantity: Decimal
    entry_time: int
    exit_price: Decimal | None = None
    exit_time: int | None = None
    pnl: Decimal = Decimal(0)
    is_paper: bool = True
    order_ids: tuple[str, ...] = ()
