"""Data models for the whale copy-trading service.

Define value objects for copy signals (detected whale bias), side legs
(individual Up/Down token positions), open positions that transition
through UNHEDGED → HEDGED states for temporal spread arbitrage, and
closed trade results.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class PositionState(Enum):
    """Lifecycle state of a copy-trading position.

    UNHEDGED: Leg 1 (directional) placed, waiting for hedge opportunity.
    HEDGED: Both legs placed, guaranteed profit locked in.
    """

    UNHEDGED = "unhedged"
    HEDGED = "hedged"


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
    """One side of a spread position (Up or Down).

    Track the weighted-average entry price, total quantity, and cost basis
    for a single outcome token. Mutable so fills can be added incrementally.

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
    """A live (open) temporal spread arbitrage position.

    Start with a single directional leg (leg1) copying the whale's
    favoured side. Transition to HEDGED when the opposite side becomes
    cheap enough that combined cost < ``max_spread_cost``, locking in
    guaranteed profit.

    Attributes:
        signal: The most recent signal for this market.
        state: Current lifecycle state (UNHEDGED or HEDGED).
        leg1: The directional entry leg (whale's favoured side).
        hedge_leg: The hedge leg (opposite side), or ``None`` if unhedged.
        hedge_side: The outcome name for the hedge leg (opposite of leg1).
        entry_time: UTC epoch seconds of the first entry.
        is_paper: ``True`` for simulated trades, ``False`` for live.

    """

    signal: CopySignal
    state: PositionState
    leg1: SideLeg
    hedge_leg: SideLeg | None
    hedge_side: str
    entry_time: int
    is_paper: bool = True

    @property
    def total_cost_basis(self) -> Decimal:
        """Return the combined cost basis of both legs."""
        hedge_cost = self.hedge_leg.cost_basis if self.hedge_leg else Decimal(0)
        return self.leg1.cost_basis + hedge_cost

    @property
    def all_order_ids(self) -> list[str]:
        """Return all CLOB order IDs across both legs."""
        ids: list[str] = list(self.leg1.order_ids)
        if self.hedge_leg:
            ids.extend(self.hedge_leg.order_ids)
        return ids

    @property
    def favoured_side(self) -> str:
        """Return the whale's favoured side (leg1 direction)."""
        return self.leg1.side


@dataclass(frozen=True)
class CopyResult:
    """Outcome of a closed copy trade.

    Immutable record of a position that has been closed, with final
    P&L calculated from the winning leg's payout minus total cost.

    Attributes:
        signal: The copy signal that triggered this trade.
        state: Position state at close (UNHEDGED or HEDGED).
        leg1_side: Direction of the directional entry leg.
        leg1_entry: Entry price for leg 1.
        leg1_qty: Token quantity for leg 1.
        hedge_entry: Entry price for hedge leg, or ``None``.
        hedge_qty: Token quantity for hedge leg, or ``None``.
        total_cost_basis: Total USDC spent across both legs.
        entry_time: UTC epoch seconds when first opened.
        exit_time: UTC epoch seconds when closed, or ``None`` if still open.
        winning_side: Which side won (``"Up"`` or ``"Down"``), or ``None``.
        pnl: Realised profit/loss in USDC.
        is_paper: ``True`` for simulated trades, ``False`` for live.
        order_ids: All CLOB order IDs for this position.

    """

    signal: CopySignal
    state: PositionState
    leg1_side: str
    leg1_entry: Decimal
    leg1_qty: Decimal
    hedge_entry: Decimal | None
    hedge_qty: Decimal | None
    total_cost_basis: Decimal
    entry_time: int
    exit_time: int | None = None
    winning_side: str | None = None
    pnl: Decimal = Decimal(0)
    is_paper: bool = True
    order_ids: tuple[str, ...] = ()
