"""Shared helpers for the backtester CLI commands.

Provide validation, resolution, and config-building functions used by
all four CLI commands (run, compare, monte-carlo, walk-forward). Keep
these separate from the command modules to avoid circular imports and
duplication.
"""

from decimal import Decimal
from pathlib import Path

import typer

from trading_tools.apps.backtester.strategy_factory import STRATEGY_NAMES
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.clients.revolut_x.client import RevolutXClient
from trading_tools.core.config import get_config
from trading_tools.core.models import ExecutionConfig, Interval, RiskConfig
from trading_tools.core.protocols import CandleProvider
from trading_tools.data.providers.binance import BinanceCandleProvider
from trading_tools.data.providers.csv_provider import CsvCandleProvider
from trading_tools.data.providers.revolut_x import RevolutXCandleProvider

VALID_SOURCES = ("csv", "revolut-x", "binance")


def validate_strategy(value: str) -> str:
    """Validate that the strategy name is one of the known strategy identifiers.

    Raise ``typer.BadParameter`` if the name is not recognised.
    """
    if value not in STRATEGY_NAMES:
        raise typer.BadParameter(f"Must be one of: {', '.join(STRATEGY_NAMES)}")
    return value


def resolve_interval(raw: str | None) -> Interval:
    """Resolve the candle interval from the CLI option or YAML config default.

    Fall back to ``1h`` when neither the CLI option nor the config key
    ``backtester.default_interval`` is set.
    """
    value = raw or get_config().get("backtester.default_interval", "1h")
    return Interval(str(value))


def resolve_capital(capital: float | None) -> Decimal:
    """Resolve the initial capital from the CLI option or YAML config default.

    Fall back to ``10000`` when neither the CLI option nor the config key
    ``backtester.initial_capital`` is set. Convert to ``Decimal`` for
    lossless arithmetic throughout the backtester.
    """
    if capital is not None:
        return Decimal(str(capital))
    raw: object = get_config().get("backtester.initial_capital", 10000)
    return Decimal(str(raw))


def validate_source(value: str) -> str:
    """Validate that the data source is one of the supported providers.

    Raise ``typer.BadParameter`` if the source is not recognised.
    """
    if value not in VALID_SOURCES:
        raise typer.BadParameter(f"Must be one of: {', '.join(VALID_SOURCES)}")
    return value


def build_provider(
    source: str,
    csv_path: Path | None,
) -> tuple[CandleProvider, RevolutXClient | BinanceClient | None]:
    """Build a candle provider based on the selected source.

    Return the provider and an optional client that must be closed after use.
    """
    if source == "revolut-x":
        client: RevolutXClient | BinanceClient = RevolutXClient.from_config()
        return RevolutXCandleProvider(client), client

    if source == "binance":
        binance_client = BinanceClient()
        return BinanceCandleProvider(binance_client), binance_client

    if csv_path is None:
        raise typer.BadParameter("--csv is required when --source is csv", param_hint="'--csv'")
    return CsvCandleProvider(csv_path), None


def build_risk_config(
    stop_loss: float | None,
    take_profit: float | None,
    circuit_breaker: float | None,
    recovery_pct: float | None,
) -> RiskConfig:
    """Build a ``RiskConfig`` from CLI float arguments.

    Convert each optional float to ``Decimal`` or ``None``.
    """
    return RiskConfig(
        stop_loss_pct=Decimal(str(stop_loss)) if stop_loss is not None else None,
        take_profit_pct=Decimal(str(take_profit)) if take_profit is not None else None,
        circuit_breaker_pct=Decimal(str(circuit_breaker)) if circuit_breaker is not None else None,
        recovery_pct=Decimal(str(recovery_pct)) if recovery_pct is not None else None,
    )


def build_execution_config(
    *,
    maker_fee: float,
    taker_fee: float,
    slippage: float,
    position_size: float,
    volatility_sizing: bool = False,
    atr_period: int = 14,
    target_risk_pct: float = 0.02,
) -> ExecutionConfig:
    """Build an ``ExecutionConfig`` from CLI float arguments.

    Consolidate the inline ``ExecutionConfig(...)`` construction that
    was duplicated across all four CLI commands. Convert floats to
    ``Decimal`` for lossless arithmetic.

    Args:
        maker_fee: Maker fee as a decimal fraction.
        taker_fee: Taker fee as a decimal fraction.
        slippage: Slippage as a decimal fraction.
        position_size: Fraction of capital to deploy per trade (0-1).
        volatility_sizing: Whether to use ATR-based position sizing.
        atr_period: ATR lookback period for volatility sizing.
        target_risk_pct: Target risk per trade as a decimal fraction.

    Returns:
        A fully configured ``ExecutionConfig`` instance.

    """
    if maker_fee < 0:
        raise typer.BadParameter("maker-fee must be >= 0", param_hint="'--maker-fee'")
    if taker_fee < 0:
        raise typer.BadParameter("taker-fee must be >= 0", param_hint="'--taker-fee'")
    if not 0 <= slippage <= 1:
        raise typer.BadParameter("slippage must be between 0 and 1", param_hint="'--slippage'")
    if not 0 < position_size <= 1:
        raise typer.BadParameter(
            "position-size must be between 0 (exclusive) and 1 (inclusive)",
            param_hint="'--position-size'",
        )

    return ExecutionConfig(
        maker_fee_pct=Decimal(str(maker_fee)),
        taker_fee_pct=Decimal(str(taker_fee)),
        slippage_pct=Decimal(str(slippage)),
        position_size_pct=Decimal(str(position_size)),
        volatility_sizing=volatility_sizing,
        atr_period=atr_period,
        target_risk_pct=Decimal(str(target_risk_pct)),
    )
