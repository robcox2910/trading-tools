"""Configuration for the directional trading algorithm.

Define an immutable configuration dataclass that holds all tunable
parameters: polling frequency, entry timing, capital management,
estimator weights, and risk controls.  Support loading from a YAML
file and applying CLI overrides on top.
"""

import dataclasses
import functools
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


@functools.lru_cache(maxsize=1)
def _decimal_field_names() -> frozenset[str]:
    """Return field names on ``DirectionalConfig`` that have type ``Decimal``.

    Cached after first call since the set of Decimal fields is immutable.
    The result is used by ``_parse_config_dict`` to decide which YAML
    values need ``Decimal`` conversion.
    """
    return frozenset(f.name for f in dataclasses.fields(DirectionalConfig) if f.type is Decimal)


def _parse_config_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Convert raw YAML dict values to types expected by ``DirectionalConfig``.

    Numeric string values are converted to ``Decimal`` for fields that
    require it; bare numeric YAML values (``float``/``int``) are also
    handled.  Unknown keys (not matching any dataclass field) are
    silently dropped.

    Args:
        data: Raw dictionary from ``yaml.safe_load``.

    Returns:
        Filtered and converted keyword arguments suitable for the
        ``DirectionalConfig`` constructor.

    Raises:
        ValueError: If a Decimal field contains an unconvertible value.

    """
    valid_names = {f.name for f in dataclasses.fields(DirectionalConfig)}
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
class DirectionalConfig:
    """Immutable configuration for the directional trading algorithm.

    Control polling behaviour, entry timing windows, capital management,
    estimator weights, and risk parameters.  All monetary values use
    ``Decimal`` for precision.

    Attributes:
        poll_interval: Seconds between market scan cycles.
        capital: Starting capital in USDC (paper mode base).
        max_position_pct: Maximum fraction of capital per trade.
        kelly_fraction: Fractional Kelly multiplier (0.5 = half Kelly).
        min_edge: Minimum probability edge required to enter a trade.
            Edge is defined as ``|p_predicted - token_price|``.
        entry_window_start: Seconds before market close to begin
            considering entries.  E.g. 30 means enter when T-30s or less
            remains.
        entry_window_end: Seconds before market close to stop entries.
            E.g. 10 means no entries when fewer than 10s remain.
        signal_lookback_seconds: Seconds of Binance 1-min candle data
            to fetch for feature extraction.
        fee_rate: Polymarket crypto fee rate coefficient.
        fee_exponent: Exponent for the Polymarket fee formula.
        compound_profits: Grow paper capital by adding realised P&L.
        circuit_breaker_losses: Consecutive losses to trigger cooldown
            (0 = disabled).
        circuit_breaker_cooldown: Seconds to pause after circuit breaker.
        max_drawdown_pct: Maximum session drawdown as fraction of starting
            capital.  Halt all entries when exceeded.
        paper_slippage_pct: Simulated slippage for paper fills.
        max_open_positions: Maximum concurrent directional positions.
        series_slugs: Event series slugs to scan for markets.
        w_momentum: Estimator weight for the momentum feature.
        w_volatility: Estimator weight for the volatility regime feature.
        w_volume: Estimator weight for the volume profile feature.
        w_book_imbalance: Estimator weight for the order book imbalance
            feature.
        w_rsi: Estimator weight for the RSI signal feature.
        w_price_change: Estimator weight for the price change feature.
        min_token_price: Minimum token price to buy.  Skip tokens the
            market has already written off (e.g. $0.06 with 30s left
            means 94% decided against this side).

    """

    poll_interval: int = 3
    capital: Decimal = Decimal(100)
    max_position_pct: Decimal = Decimal("0.15")
    kelly_fraction: Decimal = Decimal("0.5")
    min_edge: Decimal = Decimal("0.05")
    min_token_price: Decimal = Decimal("0.15")
    entry_window_start: int = 30
    entry_window_end: int = 10
    signal_lookback_seconds: int = 1200
    fee_rate: Decimal = Decimal("0.25")
    fee_exponent: int = 2
    compound_profits: bool = True
    circuit_breaker_losses: int = 3
    circuit_breaker_cooldown: int = 300
    max_drawdown_pct: Decimal = Decimal("0.15")
    paper_slippage_pct: Decimal = Decimal("0.005")
    max_open_positions: int = 10
    series_slugs: tuple[str, ...] = ("btc-updown-5m", "eth-updown-5m")
    w_momentum: Decimal = Decimal("0.30")
    w_volatility: Decimal = Decimal("0.10")
    w_volume: Decimal = Decimal("0.15")
    w_book_imbalance: Decimal = Decimal("0.20")
    w_rsi: Decimal = Decimal("0.10")
    w_price_change: Decimal = Decimal("0.15")

    @classmethod
    def from_yaml(cls, path: Path) -> "DirectionalConfig":
        """Load configuration from a YAML file.

        Keys must match dataclass field names.  Numeric values are
        converted to ``Decimal`` where the field type requires it.
        Unknown keys are silently ignored, making forward-compatible
        config files easy.

        Args:
            path: Filesystem path to a YAML configuration file.

        Returns:
            A new ``DirectionalConfig`` populated from the file, with
            dataclass defaults filling any omitted fields.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If a Decimal field contains an unconvertible value.

        """
        with path.open() as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}
        return cls(**_parse_config_dict(data))

    @classmethod
    def with_overrides(cls, base: "DirectionalConfig", **overrides: object) -> "DirectionalConfig":
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
            A new ``DirectionalConfig`` with overrides applied.

        """
        fields = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
        for key, value in overrides.items():
            if value is not None and key in fields:
                fields[key] = value
        return cls(**fields)
