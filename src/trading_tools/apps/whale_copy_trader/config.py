"""Configuration for the whale copy-trading service.

Define an immutable configuration dataclass that holds all tunable
parameters: polling frequency, signal thresholds, position sizing,
spread arbitrage targets, and capital management.  Supports loading
from a YAML file and applying CLI overrides on top.
"""

import dataclasses
import functools
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


@functools.lru_cache(maxsize=1)
def _decimal_field_names() -> frozenset[str]:
    """Return field names on WhaleCopyConfig that have type ``Decimal``.

    Cached after first call since the set of Decimal fields is immutable.
    The result is used by ``_parse_config_dict`` to decide which YAML
    values need ``Decimal`` conversion.
    """
    return frozenset(f.name for f in dataclasses.fields(WhaleCopyConfig) if f.type is Decimal)


def _parse_config_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Convert raw YAML dict values to types expected by ``WhaleCopyConfig``.

    Numeric string values are converted to ``Decimal`` for fields that
    require it; bare numeric YAML values (``float``/``int``) are also
    handled.  Unknown keys (not matching any dataclass field) are
    silently dropped.

    Args:
        data: Raw dictionary from ``yaml.safe_load``.

    Returns:
        Filtered and converted keyword arguments suitable for the
        ``WhaleCopyConfig`` constructor.

    Raises:
        ValueError: If a Decimal field contains an unconvertible value.

    """
    valid_names = {f.name for f in dataclasses.fields(WhaleCopyConfig)}
    decimal_names = _decimal_field_names()
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key not in valid_names:
            continue
        if key in decimal_names:
            try:
                result[key] = Decimal(str(value))
            except InvalidOperation as exc:
                msg = f"Cannot convert {key}={value!r} to Decimal"
                raise ValueError(msg) from exc
        else:
            result[key] = value
    return result


@dataclasses.dataclass(frozen=True)
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
        defensive_hedge_pct: Maximum fractional price drop on an unhedged
            leg 1 before a defensive hedge is placed. Instead of selling
            tokens into a thin book, buy the opposite side to cap the
            maximum loss at settlement. 0.10 means hedge when the token
            drops 10 %% from the entry price.
        max_defensive_hedge_cost: Maximum combined cost (leg1 effective
            price + hedge price) for a defensive hedge. When the combined
            cost exceeds this threshold, sell leg 1 tokens instead of
            hedging to avoid locking in a large guaranteed loss. A value
            of 1.05 means reject defensive hedges that would guarantee
            more than 5 %% loss per token.
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
        max_drawdown_pct: Maximum session drawdown as a fraction of starting
            capital. When cumulative P&L drops below ``-max_drawdown_pct *
            session_start_capital``, all new entries are halted until the
            session is restarted.
        drawdown_throttle_pct: Fraction below the high-water mark at which
            Kelly sizing is throttled to 50 %. Reduces position sizes during
            drawdowns without fully halting.
        paper_slippage_pct: Simulated slippage applied to paper fills.
            Entry and hedge prices are worsened by this percentage so that
            paper results more closely approximate live execution.
        signal_strength_sizing: Scale Kelly position size proportionally to
            each signal's ``strength_score``. Stronger signals (higher bias,
            more trades) receive larger allocations.
        max_entry_age_pct: Maximum fraction of the market window elapsed
            before entries are skipped. A value of ``0.60`` means entries
            are only allowed in the first 60 % of the window.
        halt_win_rate: When the adaptive win rate drops below this level,
            halt all new entries. Unlike ``min_win_rate`` which floors Kelly
            sizing, this stops trading entirely to limit losses.
        enable_flipping: Master toggle for flip trading. When enabled,
            take-profit sell exits are replaced with immediate re-entry on
            the opposite side, capturing multiple spread swings per window.
        max_flips_per_market: Maximum number of flips allowed per market
            window. Prevents runaway flip loops in volatile markets.
        min_flip_buffer_seconds: Stop flipping when fewer than this many
            seconds remain before market expiry. Ensures enough time for
            the flipped position to settle or hedge.
        flip_take_profit_pct: Tighter take-profit threshold used for flip
            legs (e.g. 0.10 = 10% vs 0.15 for initial entry). Faster
            exits on flips since we're capturing smaller swings.

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
    defensive_hedge_pct: Decimal = Decimal("0.10")
    max_defensive_hedge_cost: Decimal = Decimal("1.05")
    win_rate: Decimal = Decimal("0.80")
    kelly_fraction: Decimal = Decimal("0.5")
    clob_fee_rate: Decimal = Decimal("0.0")
    take_profit_pct: Decimal = Decimal("0.15")
    max_unhedged_exposure_pct: Decimal = Decimal("0.50")
    adaptive_kelly: bool = True
    min_kelly_results: int = 20
    min_win_rate: Decimal = Decimal("0.65")
    max_asset_exposure_pct: Decimal = Decimal("0.30")
    compound_profits: bool = True
    hedge_urgency_threshold: Decimal = Decimal("0.20")
    hedge_urgency_spread_bump: Decimal = Decimal("0.03")
    circuit_breaker_losses: int = 3
    circuit_breaker_cooldown: int = 300
    max_drawdown_pct: Decimal = Decimal("0.15")
    drawdown_throttle_pct: Decimal = Decimal("0.10")
    paper_slippage_pct: Decimal = Decimal("0.005")
    signal_strength_sizing: bool = True
    max_entry_age_pct: Decimal = Decimal("0.60")
    halt_win_rate: Decimal = Decimal("0.55")
    enable_flipping: bool = False
    max_flips_per_market: int = 4
    min_flip_buffer_seconds: int = 30
    flip_take_profit_pct: Decimal = Decimal("0.10")

    @classmethod
    def from_yaml(cls, path: Path) -> "WhaleCopyConfig":
        """Load configuration from a YAML file.

        Keys must match dataclass field names.  Numeric values are
        converted to ``Decimal`` where the field type requires it.
        Unknown keys are silently ignored, making forward-compatible
        config files easy.

        Args:
            path: Filesystem path to a YAML configuration file.

        Returns:
            A new ``WhaleCopyConfig`` populated from the file, with
            dataclass defaults filling any omitted fields.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If a Decimal field contains an unconvertible value.

        """
        with path.open() as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}
        return cls(**_parse_config_dict(data))

    @classmethod
    def with_overrides(cls, base: "WhaleCopyConfig", **overrides: object) -> "WhaleCopyConfig":
        """Create a new config by applying non-None overrides to a base.

        Iterate over *overrides* and replace the corresponding field in
        *base* only when the override value is not ``None``.  This
        enables the *defaults → YAML → CLI flags* layering pattern:
        pass all CLI arguments (which default to ``None`` when unset)
        and only explicitly-provided values will take effect.

        Args:
            base: Existing configuration to start from.
            **overrides: Field-name / value pairs.  ``None`` values are
                skipped; unknown keys are silently ignored.

        Returns:
            A new ``WhaleCopyConfig`` with overrides applied.

        """
        fields = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
        for key, value in overrides.items():
            if value is not None and key in fields:
                fields[key] = value
        return cls(**fields)
