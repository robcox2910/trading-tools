"""Data models for the whale copy trading bot.

Define the ``WhalePosition`` dataclass that tracks an open position on
a single market.  Unlike ``AccumulatingPosition`` in spread capture,
the ``whale_side`` field is NOT locked in at entry — it updates every
poll cycle to reflect the whale's current net direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading_tools.apps.spread_capture.models import (
        PositionState,
        SideLeg,
        SpreadOpportunity,
    )


@dataclass
class WhalePosition:
    """Track a whale-copy position on one Up/Down market.

    Both ``up_leg`` and ``down_leg`` start with zero quantity and
    accumulate fills over time.  If the whale flips direction mid-window,
    both legs can have tokens — winning tokens pay $1.00 and losing
    tokens pay $0.00 at settlement.

    Attributes:
        opportunity: Market metadata (condition ID, tokens, timestamps).
        state: Current lifecycle state (ACCUMULATING or SETTLED).
        up_leg: The Up side leg (starts at qty=0, fills added over time).
        down_leg: The Down side leg (starts at qty=0, fills added over time).
        entry_time: UTC epoch seconds when the position was first opened.
        is_paper: ``True`` for simulated trades, ``False`` for live.
        budget: Total USDC budget allocated for this market.
        whale_side: Current whale consensus direction — updates every
            poll based on the whale's net position.  ``None`` before
            the first whale signal is received.

    """

    opportunity: SpreadOpportunity
    state: PositionState
    up_leg: SideLeg
    down_leg: SideLeg
    entry_time: int
    is_paper: bool = True
    budget: Decimal = Decimal(0)
    whale_side: str | None = None

    @property
    def total_cost_basis(self) -> Decimal:
        """Return the combined cost basis of both legs."""
        return self.up_leg.cost_basis + self.down_leg.cost_basis

    @property
    def all_order_ids(self) -> list[str]:
        """Return all CLOB order IDs across both legs."""
        return list(self.up_leg.order_ids) + list(self.down_leg.order_ids)
