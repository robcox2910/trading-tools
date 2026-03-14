"""Data models for the whale copy-trading service.

Define value objects for copy signals (detected whale bias), side legs
(individual Up/Down token positions), open positions that hold two legs
for dual-side spread capture, and closed trade results.
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
        up_volume_pct: Fraction of whale spend on Up (0.0-1.0).
        down_volume_pct: Fraction of whale spend on Down (sums to 1.0).

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
    up_volume_pct: Decimal = Decimal("0.5")
    down_volume_pct: Decimal = Decimal("0.5")


def _empty_str_list() -> list[str]:
    """Return an empty list[str] for dataclass default_factory."""
    return []


@dataclass
class SideLeg:
    """One side of a dual-side spread position (Up or Down).

    Track the weighted-average entry price, total quantity, and cost basis
    for a single outcome token. Mutable so fills can be added incrementally
    as the whale increases conviction or the bot tops up.

    Attributes:
        side: Outcome direction (``"Up"`` or ``"Down"``).
        entry_price: Weighted-average entry price across all fills.
        quantity: Total token quantity across all fills.
        cost_basis: Total USDC spent (sum of price * quantity per fill).
        order_ids: CLOB order IDs for live trades on this leg.

    """

    side: str
    entry_price: Decimal
    quantity: Decimal
    cost_basis: Decimal
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


@dataclass
class OpenPosition:
    """A live (open) dual-side copy position that accumulates top-ups.

    Hold two optional legs (Up and Down) for spread capture. The favoured
    side gets more capital based on the whale's volume allocation, while
    the unfavoured side provides hedging. Either leg may be ``None`` if
    the computed quantity falls below the 5-token minimum.

    Attributes:
        signal: The most recent signal for this market.
        favoured_side: Current favoured direction (``"Up"`` or ``"Down"``).
        up_leg: Position leg for Up tokens, or ``None`` if below minimum.
        down_leg: Position leg for Down tokens, or ``None`` if below minimum.
        entry_time: UTC epoch seconds of the first entry.
        last_bias: Bias ratio at the most recent entry or top-up.
        is_paper: ``True`` for simulated trades, ``False`` for live.

    """

    signal: CopySignal
    favoured_side: str
    up_leg: SideLeg | None
    down_leg: SideLeg | None
    entry_time: int
    last_bias: Decimal
    is_paper: bool = True

    @property
    def total_cost_basis(self) -> Decimal:
        """Return the combined cost basis of both legs."""
        up_cost = self.up_leg.cost_basis if self.up_leg else Decimal(0)
        down_cost = self.down_leg.cost_basis if self.down_leg else Decimal(0)
        return up_cost + down_cost

    @property
    def all_order_ids(self) -> list[str]:
        """Return all CLOB order IDs across both legs."""
        ids: list[str] = []
        if self.up_leg:
            ids.extend(self.up_leg.order_ids)
        if self.down_leg:
            ids.extend(self.down_leg.order_ids)
        return ids


@dataclass(frozen=True)
class CopyResult:
    """Outcome of a closed dual-side copy trade.

    Immutable record of a position that has been closed, with final
    P&L calculated from the winning leg's payout minus total cost.

    Attributes:
        signal: The copy signal that triggered this trade.
        favoured_side: Position direction when closed.
        up_entry: Weighted-average entry price for Up leg, or ``None``.
        up_qty: Total Up tokens, or ``None`` if no Up leg.
        down_entry: Weighted-average entry price for Down leg, or ``None``.
        down_qty: Total Down tokens, or ``None`` if no Down leg.
        total_cost_basis: Total USDC spent across both legs.
        entry_time: UTC epoch seconds when first opened.
        exit_time: UTC epoch seconds when closed, or ``None`` if still open.
        winning_side: Which side won (``"Up"`` or ``"Down"``), or ``None``.
        pnl: Realised profit/loss in USDC.
        is_paper: ``True`` for simulated trades, ``False`` for live.
        order_ids: All CLOB order IDs for this position.

    """

    signal: CopySignal
    favoured_side: str
    up_entry: Decimal | None
    up_qty: Decimal | None
    down_entry: Decimal | None
    down_qty: Decimal | None
    total_cost_basis: Decimal
    entry_time: int
    exit_time: int | None = None
    winning_side: str | None = None
    pnl: Decimal = Decimal(0)
    is_paper: bool = True
    order_ids: tuple[str, ...] = ()
