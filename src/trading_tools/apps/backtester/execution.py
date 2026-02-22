"""Shared execution helpers for backtesting.

Provide pure functions for slippage application, position allocation
(including volatility-based sizing), and risk-management exit triggers.
These helpers consolidate logic previously duplicated across the
single-asset and multi-asset portfolio and engine modules.
"""

from decimal import Decimal

from trading_tools.apps.backtester.indicators import atr as compute_atr
from trading_tools.core.models import (
    ONE,
    ZERO,
    Candle,
    ExecutionConfig,
    RiskConfig,
    Side,
)


def apply_entry_slippage(price: Decimal, slippage_pct: Decimal) -> Decimal:
    """Apply slippage to an entry price (worsen buy price upward).

    Args:
        price: The raw market price at entry.
        slippage_pct: Slippage as a decimal fraction (e.g. 0.01 for 1%).

    Returns:
        The effective entry price after slippage.

    """
    return price * (ONE + slippage_pct)


def apply_exit_slippage(price: Decimal, slippage_pct: Decimal) -> Decimal:
    """Apply slippage to an exit price (worsen sell price downward).

    Args:
        price: The raw market price at exit.
        slippage_pct: Slippage as a decimal fraction (e.g. 0.01 for 1%).

    Returns:
        The effective exit price after slippage.

    """
    return price * (ONE - slippage_pct)


def compute_allocation(
    *,
    capital: Decimal,
    price: Decimal,
    exec_config: ExecutionConfig,
    history: list[Candle] | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """Compute position allocation, entry fee, and quantity.

    When volatility sizing is enabled and sufficient history is available,
    scale the allocation based on ATR so each trade risks approximately
    ``target_risk_pct`` of capital. The result is capped at
    ``position_size_pct`` of the given capital.

    Args:
        capital: The capital base used for sizing (available or initial).
        price: Effective entry price (after slippage).
        exec_config: Execution cost and sizing configuration.
        history: Optional candle history for volatility-based sizing.

    Returns:
        Tuple of (allocation, entry_fee, quantity) where allocation is
        the total capital committed (including the fee), entry_fee is
        the fee portion, and quantity is the number of units purchased.

    """
    max_available = capital * exec_config.position_size_pct

    available = max_available
    if exec_config.volatility_sizing and history is not None:
        atr_needed = exec_config.atr_period + 1
        if len(history) >= atr_needed:
            atr_value = compute_atr(history, period=exec_config.atr_period)
            if atr_value > ZERO:
                risk_budget = capital * exec_config.target_risk_pct
                vol_quantity = risk_budget / atr_value
                vol_allocation = vol_quantity * price
                available = min(vol_allocation, max_available)

    if price <= ZERO:
        return ZERO, ZERO, ZERO

    entry_fee = available * exec_config.taker_fee_pct
    investable = available - entry_fee
    quantity = investable / price

    return available, entry_fee, quantity


def check_risk_triggers(
    candle: Candle,
    entry_price: Decimal,
    risk_config: RiskConfig,
    side: Side = Side.BUY,
) -> Decimal | None:
    """Check stop-loss and take-profit triggers against a candle.

    Evaluate whether the candle breaches the stop-loss or take-profit
    level relative to the entry price. For LONG (BUY) positions,
    stop-loss triggers on the low, take-profit on the high. For SHORT
    (SELL) positions, the logic is inverted. Stop-loss takes priority
    when both trigger on the same candle (conservative assumption).

    Args:
        candle: The current candle to check against.
        entry_price: The entry price of the open position.
        risk_config: Risk configuration with stop-loss/take-profit thresholds.
        side: The position side (BUY for long, SELL for short).

    Returns:
        The exit price if a risk exit is triggered, ``None`` otherwise.

    """
    stop_loss_pct = risk_config.stop_loss_pct
    take_profit_pct = risk_config.take_profit_pct

    if side == Side.SELL:
        stop_triggered = stop_loss_pct is not None and candle.high >= entry_price * (
            ONE + stop_loss_pct
        )
        tp_triggered = take_profit_pct is not None and candle.low <= entry_price * (
            ONE - take_profit_pct
        )

        if stop_triggered and stop_loss_pct is not None:
            return entry_price * (ONE + stop_loss_pct)
        if tp_triggered and take_profit_pct is not None:
            return entry_price * (ONE - take_profit_pct)
    else:
        stop_triggered = stop_loss_pct is not None and candle.low <= entry_price * (
            ONE - stop_loss_pct
        )
        tp_triggered = take_profit_pct is not None and candle.high >= entry_price * (
            ONE + take_profit_pct
        )

        if stop_triggered and stop_loss_pct is not None:
            return entry_price * (ONE - stop_loss_pct)
        if tp_triggered and take_profit_pct is not None:
            return entry_price * (ONE + take_profit_pct)

    return None
