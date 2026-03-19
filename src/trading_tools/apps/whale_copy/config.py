"""Configuration for the whale copy trading bot.

Define an immutable configuration dataclass that holds all tunable
parameters: polling frequency, capital management, whale signal
thresholds, and risk controls.  Support loading from a YAML file
and applying CLI overrides on top.
"""

import dataclasses
import functools
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


@functools.lru_cache(maxsize=1)
def _decimal_field_names() -> frozenset[str]:
    """Return field names on ``WhaleCopyConfig`` that have type ``Decimal``.

    Cached after first call since the set of Decimal fields is immutable.
    The result is used by ``_parse_config_dict`` to decide which YAML
    values need ``Decimal`` conversion.
    """
    return frozenset(f.name for f in dataclasses.fields(WhaleCopyConfig) if f.type is Decimal)


def _parse_config_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Convert raw YAML dict values to types expected by ``WhaleCopyConfig``.

    Numeric string values are converted to ``Decimal`` for fields that
    require it; bare numeric YAML values (``float``/``int``) are also
    handled.  Unknown keys are silently dropped.

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
    """Immutable configuration for the whale copy trading bot.

    Control polling behaviour, whale signal thresholds, position sizing,
    fill parameters, and capital management.  All monetary values use
    ``Decimal`` for precision.

    Attributes:
        poll_interval: Seconds between market scan cycles.
        capital: Starting capital in USDC (paper mode base).
        max_position_pct: Maximum fraction of capital per market.
        series_slugs: Event series slugs to scan for markets.
        max_open_positions: Maximum concurrent whale copy positions.
        rediscovery_interval: Seconds between market rediscovery calls.
        fill_size_tokens: Token quantity per fill (must meet minimum
            order size of 5).
        max_fill_age_pct: Maximum fraction of the market window elapsed
            before fills are stopped on existing positions.
        min_fill_age_pct: Minimum fraction of the market window elapsed
            before fills begin.  Wait for the whale to show their real
            hand before copying — early trades are often noise.
        max_entry_age_pct: Maximum fraction of the market window elapsed
            before new positions are opened.
        min_whale_volume: Minimum total whale dollar volume on a market
            before we start filling.  Ignore tiny feint trades.
        max_book_pct: Maximum fraction of visible order book depth to
            consume per fill.
        max_price: Maximum ask price to buy — skip fills above this.
        paper_slippage_pct: Simulated slippage for paper fills.
        fee_rate: Polymarket crypto fee rate coefficient.
        fee_exponent: Exponent for the fee formula.
        circuit_breaker_losses: Consecutive losses to trigger cooldown
            (0 = disabled).
        circuit_breaker_cooldown: Seconds to pause after circuit breaker.
        max_drawdown_pct: Maximum session drawdown as fraction of
            starting capital.
        compound_profits: Grow paper capital by adding realised P&L.
        use_market_orders: Use FOK market orders instead of GTC limit.
        min_whale_conviction: Minimum dollar ratio on favoured side
            before entering (e.g. 1.5 means 1.5x more $ on one side).
        max_window_seconds: Maximum market window duration to trade
            (0 = no limit).

    """

    poll_interval: int = 5
    capital: Decimal = Decimal(1000)
    max_position_pct: Decimal = Decimal("0.10")
    series_slugs: tuple[str, ...] = (
        "btc-updown-5m",
        "eth-updown-5m",
        "xrp-updown-5m",
        "sol-updown-5m",
    )
    max_open_positions: int = 10
    rediscovery_interval: int = 30
    fill_size_tokens: Decimal = Decimal(5)
    max_fill_age_pct: Decimal = Decimal("0.90")
    min_fill_age_pct: Decimal = Decimal("0.40")
    max_entry_age_pct: Decimal = Decimal("0.60")
    min_whale_volume: Decimal = Decimal(50)
    max_book_pct: Decimal = Decimal("0.20")
    max_price: Decimal = Decimal("0.60")
    paper_slippage_pct: Decimal = Decimal("0.005")
    fee_rate: Decimal = Decimal("0.25")
    fee_exponent: int = 2
    circuit_breaker_losses: int = 5
    circuit_breaker_cooldown: int = 300
    max_drawdown_pct: Decimal = Decimal("0.20")
    compound_profits: bool = True
    use_market_orders: bool = False
    min_whale_conviction: Decimal = Decimal("1.5")
    max_window_seconds: int = 0

    @classmethod
    def from_yaml(cls, path: Path) -> "WhaleCopyConfig":
        """Load configuration from a YAML file.

        Keys must match dataclass field names.  Numeric values are
        converted to ``Decimal`` where the field type requires it.
        Unknown keys are silently ignored.

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
        *base* only when the override value is not ``None``.

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
