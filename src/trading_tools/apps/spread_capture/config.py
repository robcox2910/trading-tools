"""Configuration for the spread capture bot.

Define an immutable configuration dataclass that holds all tunable
parameters: polling frequency, spread thresholds, position sizing,
and capital management.  Support loading from a YAML file and applying
CLI overrides on top.
"""

import dataclasses
import functools
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


@functools.lru_cache(maxsize=1)
def _decimal_field_names() -> frozenset[str]:
    """Return field names on ``SpreadCaptureConfig`` that have type ``Decimal``.

    Cached after first call since the set of Decimal fields is immutable.
    The result is used by ``_parse_config_dict`` to decide which YAML
    values need ``Decimal`` conversion.
    """
    return frozenset(f.name for f in dataclasses.fields(SpreadCaptureConfig) if f.type is Decimal)


def _parse_config_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Convert raw YAML dict values to types expected by ``SpreadCaptureConfig``.

    Numeric string values are converted to ``Decimal`` for fields that
    require it; bare numeric YAML values (``float``/``int``) are also
    handled.  Unknown keys (not matching any dataclass field) are
    silently dropped.

    Args:
        data: Raw dictionary from ``yaml.safe_load``.

    Returns:
        Filtered and converted keyword arguments suitable for the
        ``SpreadCaptureConfig`` constructor.

    Raises:
        ValueError: If a Decimal field contains an unconvertible value.

    """
    valid_names = {f.name for f in dataclasses.fields(SpreadCaptureConfig)}
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
class SpreadCaptureConfig:
    """Immutable configuration for the spread capture bot.

    Control polling behaviour, spread thresholds, position sizing, and
    capital management.  All monetary values use ``Decimal`` for precision.

    Attributes:
        poll_interval: Seconds between market scan cycles.
        capital: Starting capital in USDC (paper mode base).
        max_position_pct: Maximum fraction of capital per spread trade.
        max_window_seconds: Maximum market window duration to trade
            (0 = no limit).
        max_entry_age_pct: Maximum fraction of the market window elapsed
            before entries are skipped.
        use_market_orders: Use FOK market orders instead of GTC limit.
        fee_rate: Polymarket crypto fee rate coefficient (e.g. ``0.25``).
        fee_exponent: Exponent for the ``price * (1 - price)`` term in
            the Polymarket fee formula.
        max_book_pct: Maximum fraction of visible order book depth to
            consume per side.  Prevents excessive market impact.
        compound_profits: Grow paper capital by adding realised P&L.
        circuit_breaker_losses: Consecutive losses to trigger cooldown
            (0 = disabled).
        circuit_breaker_cooldown: Seconds to pause after circuit breaker.
        max_drawdown_pct: Maximum session drawdown as fraction of starting
            capital.  Halt all entries when exceeded.
        paper_slippage_pct: Simulated slippage for paper fills.
        series_slugs: Event series slugs to scan for markets.  Supports
            ``"crypto-5m"`` and ``"crypto-15m"`` shortcuts via
            ``parse_series_slugs()`` in ``_helpers.py``.
        max_combined_cost: Maximum combined cost of both sides to enter.
            Must be below 1.0 to guarantee profit.
        min_spread_margin: Minimum profit margin per token pair after fees.
            Opportunities where ``1.0 - combined - 2 * fee`` is below this
            threshold are skipped.
        max_open_positions: Maximum concurrent spread positions.
        single_leg_timeout: Seconds to wait for unfilled side before
            cancelling (live mode only).
        rediscovery_interval: Seconds between market rediscovery calls.
        strategy: Trading strategy — ``"simultaneous"`` for the original
            both-sides-at-once approach, ``"accumulate"`` for independent
            per-side fills over time.
        per_side_ask_threshold: Buy a side when its best ask is below
            this price.  Only used by the ``"accumulate"`` strategy.
        max_combined_vwap: Stop accumulating if the combined VWAP of
            both legs would exceed this value.
        max_imbalance_ratio: Maximum quantity ratio between the heavier
            and lighter legs.  Pause the heavier side until the other
            catches up.
        fill_size_tokens: Base token quantity per individual fill in
            the ``"accumulate"`` strategy.

    """

    poll_interval: int = 5
    capital: Decimal = Decimal(100)
    max_position_pct: Decimal = Decimal("0.10")
    max_window_seconds: int = 0
    max_entry_age_pct: Decimal = Decimal("0.60")
    use_market_orders: bool = False
    fee_rate: Decimal = Decimal("0.25")
    fee_exponent: int = 2
    max_book_pct: Decimal = Decimal("0.20")
    compound_profits: bool = True
    circuit_breaker_losses: int = 3
    circuit_breaker_cooldown: int = 300
    max_drawdown_pct: Decimal = Decimal("0.15")
    paper_slippage_pct: Decimal = Decimal("0.005")
    series_slugs: tuple[str, ...] = ("btc-updown-5m", "eth-updown-5m")
    max_combined_cost: Decimal = Decimal("0.98")
    min_spread_margin: Decimal = Decimal("0.01")
    max_open_positions: int = 10
    single_leg_timeout: int = 10
    rediscovery_interval: int = 30
    strategy: str = "simultaneous"
    per_side_ask_threshold: Decimal = Decimal("0.52")
    max_combined_vwap: Decimal = Decimal("0.995")
    max_imbalance_ratio: Decimal = Decimal("2.0")
    fill_size_tokens: Decimal = Decimal(10)

    @classmethod
    def from_yaml(cls, path: Path) -> "SpreadCaptureConfig":
        """Load configuration from a YAML file.

        Keys must match dataclass field names.  Numeric values are
        converted to ``Decimal`` where the field type requires it.
        Unknown keys are silently ignored, making forward-compatible
        config files easy.

        Args:
            path: Filesystem path to a YAML configuration file.

        Returns:
            A new ``SpreadCaptureConfig`` populated from the file, with
            dataclass defaults filling any omitted fields.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If a Decimal field contains an unconvertible value.

        """
        with path.open() as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}
        return cls(**_parse_config_dict(data))

    @classmethod
    def with_overrides(
        cls, base: "SpreadCaptureConfig", **overrides: object
    ) -> "SpreadCaptureConfig":
        """Create a new config by applying non-None overrides to a base.

        Iterate over *overrides* and replace the corresponding field in
        *base* only when the override value is not ``None``.  This
        enables the *defaults -> YAML -> CLI flags* layering pattern:
        pass all CLI arguments (which default to ``None`` when unset)
        and only explicitly-provided values will take effect.

        Args:
            base: Existing configuration to start from.
            **overrides: Field-name / value pairs.  ``None`` values are
                skipped; unknown keys are silently ignored.

        Returns:
            A new ``SpreadCaptureConfig`` with overrides applied.

        """
        fields = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
        for key, value in overrides.items():
            if value is not None and key in fields:
                fields[key] = value
        return cls(**fields)
