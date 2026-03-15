"""Engine for backtesting whale copy-trading using historical order book data.

For each trade in a whale's history, simulate copying that trade at configurable
time offsets (e.g. 500 ms, 1 000 ms, 5 000 ms) by walking the nearest order
book snapshot.

Sizing model
------------
The copy size is capital-proportional rather than a fixed token percentage:

1. Compute the whale's trade cost: ``whale_size * whale_price`` (USD spent).
2. Express as a fraction of the whale's wallet: ``cost / whale_capital``.
3. Apply the same fraction to our allocated capital: ``fraction * our_capital``.
4. Walk the order book spending up to that USD budget to buy tokens.

Example: whale spends $100 of their $10 000 wallet (1 %).  We have $500
allocated → we deploy $5 and buy as many tokens as that buys.

Data flow:
1. Load whale trades from ``WhaleRepository`` for the given address and period.
2. For each trade and each offset, query ``TickRepository`` for the nearest
   ``OrderBookSnapshot`` at ``trade_ts * 1000 + offset_ms``.
3. Parse the snapshot JSON and simulate a budget-constrained walk-the-book fill.
4. Aggregate per-offset statistics across all trades.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.whale_copy_trader.copy_backtest_models import (
    CopyAttempt,
    CopyBacktestResult,
    CopyBacktestTrade,
    OffsetStats,
)

if TYPE_CHECKING:
    from trading_tools.apps.tick_collector.models import OrderBookSnapshot
    from trading_tools.apps.tick_collector.repository import TickRepository
    from trading_tools.apps.whale_monitor.models import WhaleTrade
    from trading_tools.apps.whale_monitor.repository import WhaleRepository

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1000
_BPS_MULTIPLIER = Decimal(10000)
_ZERO = Decimal(0)
_FULL_UTILISATION = Decimal("0.99")


def _parse_levels(json_str: str) -> list[tuple[Decimal, Decimal]]:
    """Parse a JSON order book level array into decimal price/size tuples.

    Args:
        json_str: JSON string encoding a ``[[price, size], ...]`` array.

    Returns:
        List of ``(price, size)`` tuples with ``Decimal`` values.

    """
    raw: list[list[float]] = json.loads(json_str)
    return [(Decimal(str(entry[0])), Decimal(str(entry[1]))) for entry in raw]


def _simulate_fill_usd(
    levels: list[tuple[Decimal, Decimal]],
    budget_usd: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Walk order book levels spending up to a USD budget.

    Consume each level in priority order, buying as many tokens as the
    remaining budget allows at each price level, until the budget is
    exhausted or the book is depleted.

    Args:
        levels: ``(price, size)`` tuples in fill-priority order — asks sorted
            ascending for BUY, bids sorted descending for SELL.
        budget_usd: Maximum USD amount to spend.

    Returns:
        Tuple of ``(fill_qty, vwap_price, cost_usd)``.  All values are zero
        when the book has no eligible levels or budget is zero.

    """
    if budget_usd <= _ZERO or not levels:
        return _ZERO, _ZERO, _ZERO

    filled_qty = _ZERO
    spent_usd = _ZERO

    for price, size in levels:
        remaining_budget = budget_usd - spent_usd
        if remaining_budget <= _ZERO:
            break
        affordable_qty = remaining_budget / price
        take = min(size, affordable_qty)
        spent_usd += price * take
        filled_qty += take

    if filled_qty == _ZERO:
        return _ZERO, _ZERO, _ZERO

    vwap = spent_usd / filled_qty
    return filled_qty, vwap, spent_usd


def _attempt_from_snapshot(
    snapshot: OrderBookSnapshot,
    side: str,
    whale_price: Decimal,
    our_budget_usd: Decimal,
    offset_ms: int,
) -> CopyAttempt:
    """Simulate a copy fill from an order book snapshot using a USD budget.

    Parse the snapshot's bid/ask JSON, select levels for the appropriate side,
    walk the book spending up to ``our_budget_usd``, and compute slippage
    vs the whale's execution price.

    For a BUY copy the asks are walked in ascending price order (cheapest
    first).  For a SELL copy the bids are walked in descending price order
    (highest first).  Slippage is the penalty relative to the whale's price:
    positive means the copier got a worse deal.

    Args:
        snapshot: Order book snapshot ORM record.
        side: Trade direction — ``"BUY"`` or ``"SELL"``.
        whale_price: Whale's execution price for slippage comparison.
        our_budget_usd: Maximum USD amount to deploy.
        offset_ms: Time offset this attempt represents (for the result).

    Returns:
        A ``CopyAttempt`` recording fill quantity, VWAP price, cost, and
        slippage.

    """
    if side.upper() == "BUY":
        levels = sorted(_parse_levels(snapshot.asks_json), key=lambda lv: lv[0])
    else:
        levels = sorted(_parse_levels(snapshot.bids_json), key=lambda lv: lv[0], reverse=True)

    fill_qty, fill_price, cost_usd = _simulate_fill_usd(levels, our_budget_usd)

    if fill_qty > _ZERO:
        slippage = fill_price - whale_price if side.upper() == "BUY" else whale_price - fill_price
    else:
        slippage = _ZERO

    return CopyAttempt(
        offset_ms=offset_ms,
        snapshot_ts_ms=snapshot.timestamp,
        ob_found=True,
        our_budget_usd=our_budget_usd,
        fill_qty=fill_qty,
        fill_price=fill_price,
        cost_usd=cost_usd,
        slippage=slippage,
    )


def _no_book_attempt(offset_ms: int, our_budget_usd: Decimal) -> CopyAttempt:
    """Return a CopyAttempt representing a missing order book snapshot.

    Args:
        offset_ms: Time offset this attempt represents.
        our_budget_usd: USD budget that would have been deployed.

    Returns:
        A ``CopyAttempt`` with ``ob_found=False`` and all numeric fields zero.

    """
    return CopyAttempt(
        offset_ms=offset_ms,
        snapshot_ts_ms=0,
        ob_found=False,
        our_budget_usd=our_budget_usd,
        fill_qty=_ZERO,
        fill_price=_ZERO,
        cost_usd=_ZERO,
        slippage=_ZERO,
    )


def _aggregate_stats(
    trades: list[CopyBacktestTrade],
    offset_ms: int,
) -> OffsetStats:
    """Compute aggregate statistics for a single time offset.

    Iterate all trades, isolate the ``CopyAttempt`` for this offset, and
    compute budget utilisation rates and slippage averages.

    Args:
        trades: All backtest trade records.
        offset_ms: The offset to aggregate statistics for.

    Returns:
        ``OffsetStats`` with budget utilisation and slippage figures.

    """
    trades_with_book = 0
    trades_full = 0
    trades_partial = 0
    utilisations: list[Decimal] = []
    slippages: list[Decimal] = []
    total_deployed = _ZERO

    for trade in trades:
        attempt = next((a for a in trade.attempts if a.offset_ms == offset_ms), None)
        if attempt is None or not attempt.ob_found:
            continue
        trades_with_book += 1
        utilisations.append(attempt.budget_utilisation)
        total_deployed += attempt.cost_usd
        if attempt.fully_deployed:
            trades_full += 1
        elif attempt.fill_qty > _ZERO:
            trades_partial += 1
        if attempt.fill_qty > _ZERO:
            slippages.append(attempt.slippage)

    n_book = Decimal(max(trades_with_book, 1))
    n_slip = Decimal(max(len(slippages), 1))
    avg_util = sum(utilisations, _ZERO) / n_book if utilisations else _ZERO
    avg_slippage = sum(slippages, _ZERO) / n_slip if slippages else _ZERO
    max_slippage = max(slippages, default=_ZERO)
    avg_slippage_bps = avg_slippage * _BPS_MULTIPLIER

    return OffsetStats(
        offset_ms=offset_ms,
        trades_total=len(trades),
        trades_with_book=trades_with_book,
        trades_fully_deployed=trades_full,
        trades_partial=trades_partial,
        avg_budget_utilisation=avg_util,
        avg_slippage=avg_slippage,
        max_slippage=max_slippage,
        avg_slippage_bps=avg_slippage_bps,
        total_deployed_usd=total_deployed,
    )


class CopyBacktestEngine:
    """Backtest how well a trader could have copy-traded a whale.

    For each whale trade, look up the nearest order book snapshot at each
    configured time offset and simulate deploying a capital-proportional USD
    budget by walking the book.

    Sizing: the whale's trade cost as a fraction of their wallet determines
    what fraction of our allocated capital to deploy.  If the whale spends 1%
    of their wallet and we have $500 allocated, we deploy $5.

    Args:
        whale_repo: Repository for querying historical whale trades.
        tick_repo: Repository for querying order book snapshots.
        ob_tolerance_ms: Maximum millisecond distance allowed when searching
            for a nearest order book snapshot.  Defaults to 2 000 ms.

    """

    def __init__(
        self,
        whale_repo: WhaleRepository,
        tick_repo: TickRepository,
        ob_tolerance_ms: int = 2000,
    ) -> None:
        """Initialise the engine with data repositories.

        Args:
            whale_repo: Repository for querying historical whale trades.
            tick_repo: Repository for querying order book snapshots.
            ob_tolerance_ms: Maximum millisecond distance allowed when
                searching for a nearest order book snapshot.

        """
        self._whale_repo = whale_repo
        self._tick_repo = tick_repo
        self._ob_tolerance_ms = ob_tolerance_ms

    async def run(
        self,
        whale_address: str,
        start_ts: int,
        end_ts: int,
        whale_capital: Decimal,
        our_capital: Decimal,
        offsets_ms: list[int],
    ) -> CopyBacktestResult:
        """Run the copy-trading backtest for a whale address and time range.

        Load all whale trades within ``[start_ts, end_ts]`` (epoch seconds),
        compute a capital-proportional USD budget for each trade, simulate copy
        attempts at each offset, and return aggregated results.

        Args:
            whale_address: Proxy wallet address of the whale to monitor.
            start_ts: Inclusive start of the analysis window (epoch seconds).
            end_ts: Inclusive end of the analysis window (epoch seconds).
            whale_capital: Estimated total USD value of the whale's wallet.
                Used to compute the fractional size of each trade.
            our_capital: USD capital allocated to copy-trading this whale.
                We deploy ``(whale_trade_cost / whale_capital) * our_capital``
                per trade.
            offsets_ms: List of millisecond offsets to simulate
                (e.g. ``[500, 1000, 5000]``).

        Returns:
            ``CopyBacktestResult`` with per-trade records and per-offset stats.

        """
        whale_trades = await self._whale_repo.get_trades(whale_address, start_ts, end_ts)
        logger.info("Loaded %d whale trades for %s", len(whale_trades), whale_address)

        backtest_trades: list[CopyBacktestTrade] = []
        for wt in whale_trades:
            record = await self._process_trade(wt, whale_capital, our_capital, offsets_ms)
            backtest_trades.append(record)

        stats_by_offset = {
            offset: _aggregate_stats(backtest_trades, offset) for offset in offsets_ms
        }

        return CopyBacktestResult(
            whale_address=whale_address,
            whale_capital=whale_capital,
            our_capital=our_capital,
            total_trades=len(backtest_trades),
            offsets_ms=tuple(offsets_ms),
            trades=tuple(backtest_trades),
            stats_by_offset=stats_by_offset,
        )

    async def _process_trade(
        self,
        wt: WhaleTrade,
        whale_capital: Decimal,
        our_capital: Decimal,
        offsets_ms: list[int],
    ) -> CopyBacktestTrade:
        """Simulate copy attempts for a single whale trade.

        Compute the capital-proportional budget, then for each offset fetch the
        nearest order book snapshot and simulate a fill.

        Args:
            wt: The whale trade to simulate copying.
            whale_capital: Whale's estimated total wallet value in USD.
            our_capital: Our allocated copy-trading capital in USD.
            offsets_ms: Offsets in milliseconds to simulate.

        Returns:
            A ``CopyBacktestTrade`` bundling the whale trade with all attempts.

        """
        whale_price = Decimal(str(wt.price))
        whale_size = Decimal(str(wt.size))
        whale_trade_cost = whale_size * whale_price
        whale_fraction = whale_trade_cost / whale_capital if whale_capital > _ZERO else _ZERO
        our_budget_usd = (whale_fraction * our_capital).quantize(Decimal("0.000001"))

        trade_ts_ms = wt.timestamp * _MS_PER_SECOND

        attempts: list[CopyAttempt] = []
        for offset_ms in offsets_ms:
            target_ts_ms = trade_ts_ms + offset_ms
            snapshot = await self._tick_repo.get_nearest_book_snapshot(
                token_id=wt.asset_id,
                timestamp_ms=target_ts_ms,
                tolerance_ms=self._ob_tolerance_ms,
            )
            if snapshot is None:
                attempts.append(_no_book_attempt(offset_ms, our_budget_usd))
                logger.debug(
                    "No OB snapshot for %s at offset +%dms (ts=%d)",
                    wt.asset_id[:12],
                    offset_ms,
                    target_ts_ms,
                )
            else:
                attempt = _attempt_from_snapshot(
                    snapshot, wt.side, whale_price, our_budget_usd, offset_ms
                )
                attempts.append(attempt)
                logger.debug(
                    "Trade %s offset +%dms: deployed=$%.4f fill=%.4f @ %.4f slip=%.4f",
                    wt.transaction_hash[:10],
                    offset_ms,
                    attempt.cost_usd,
                    attempt.fill_qty,
                    attempt.fill_price,
                    attempt.slippage,
                )

        return CopyBacktestTrade(
            whale_address=wt.whale_address,
            condition_id=wt.condition_id,
            asset_id=wt.asset_id,
            title=wt.title,
            outcome=wt.outcome,
            side=wt.side,
            whale_size=whale_size,
            whale_price=whale_price,
            whale_ts=wt.timestamp,
            whale_trade_cost=whale_trade_cost,
            whale_fraction=whale_fraction,
            our_budget_usd=our_budget_usd,
            attempts=tuple(attempts),
        )
