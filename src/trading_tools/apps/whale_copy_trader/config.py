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
        hedge_with_market_orders: Use FOK market orders for the hedge leg
            regardless of ``use_market_orders``. The hedge is time-critical;
            a GTC limit sitting unfilled defeats the purpose.
        stop_loss_pct: Maximum fractional price drop on an unhedged leg 1
            before the position is force-closed. 0.50 means cut the loss
            when the token drops 50 %% from the entry price.
        win_rate: Estimated whale win rate for Kelly criterion sizing.
        kelly_fraction: Fractional Kelly multiplier (0.5 = half-Kelly).
        clob_fee_rate: Per-leg CLOB fee rate for hedge profitability check.
            Polymarket currently charges 0 maker fee, but this is configurable.
        take_profit_pct: Fractional price gain above entry to trigger a
            take-profit exit. 0.15 means sell when token price rises 15 %%
            above the entry price (e.g. entry 0.50 → sell at 0.575).
        max_unhedged_exposure_pct: Maximum fraction of capital that may be
            committed to unhedged (non-guaranteed) positions at any time.
            Once reached, no new positions are opened until existing ones
            are hedged, stopped, exited, or settled.
        adaptive_kelly: Dynamically adjust Kelly win rate from realised
            unhedged trade outcomes instead of using a static estimate.
        min_kelly_results: Minimum closed unhedged trades before adaptive
            Kelly activates. Below this count, the static ``win_rate``
            is used.
        min_win_rate: Floor for the adaptive Kelly win rate. Prevents the
            sizing from collapsing after a short losing streak.
        max_asset_exposure_pct: Maximum fraction of total capital that may
            be committed to a single asset + side combination (e.g. all
            BTC-USD Up positions). Prevents over-concentration.
        compound_profits: In paper mode, grow available capital by adding
            realised P&L from closed trades. When ``False``, capital
            remains fixed at the starting value.
        hedge_urgency_threshold: Fraction of time remaining in a market
            window below which the hedge spread threshold is relaxed.
        hedge_urgency_spread_bump: Amount added to ``max_spread_cost``
            when the market window is in the urgency zone.
        circuit_breaker_losses: Number of consecutive unhedged losses
            that triggers a cooldown pause. Set to ``0`` to disable.
        circuit_breaker_cooldown: Seconds to pause new entries after the
            circuit breaker triggers.

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
    hedge_with_market_orders: bool = True
    stop_loss_pct: Decimal = Decimal("0.50")
    win_rate: Decimal = Decimal("0.80")
    kelly_fraction: Decimal = Decimal("0.5")
    clob_fee_rate: Decimal = Decimal("0.0")
    take_profit_pct: Decimal = Decimal("0.15")
    max_unhedged_exposure_pct: Decimal = Decimal("0.50")
    adaptive_kelly: bool = True
    min_kelly_results: int = 20
    min_win_rate: Decimal = Decimal("0.55")
    max_asset_exposure_pct: Decimal = Decimal("0.30")
    compound_profits: bool = True
    hedge_urgency_threshold: Decimal = Decimal("0.20")
    hedge_urgency_spread_bump: Decimal = Decimal("0.03")
    circuit_breaker_losses: int = 3
    circuit_breaker_cooldown: int = 300
