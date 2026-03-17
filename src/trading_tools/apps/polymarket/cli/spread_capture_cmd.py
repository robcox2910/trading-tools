"""CLI command for the spread capture bot on Polymarket.

Run a polling service that scans BTC/ETH/SOL/XRP Up/Down markets for
spread opportunities where the combined cost of both sides is below
$1.00, and enters both sides to lock in guaranteed profit at settlement.
Default is paper mode; pass ``--confirm-live`` for real orders.

When ``SPREAD_DB_URL`` (or ``WHALE_DB_URL`` as fallback) is set, settled
trade results are persisted to the ``spread_results`` table.
"""

import asyncio
import logging
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from trading_tools.apps.polymarket.cli._helpers import (
    build_authenticated_client,
    configure_logging,
    parse_series_slugs,
)
from trading_tools.apps.spread_capture.accumulating_trader import AccumulatingTrader
from trading_tools.apps.spread_capture.config import SpreadCaptureConfig
from trading_tools.apps.spread_capture.repository import SpreadResultRepository
from trading_tools.apps.spread_capture.spread_trader import SpreadTrader

_logger = logging.getLogger(__name__)

_LIVE_WARNING_DELAY = 2

_PARAM_MAP: tuple[tuple[str, str, bool], ...] = (
    # Decimal params (CLI type is str, converted to Decimal)
    ("capital", "capital", True),
    ("max_position_pct", "max_position_pct", True),
    ("max_combined_cost", "max_combined_cost", True),
    ("min_spread_margin", "min_spread_margin", True),
    ("max_entry_age_pct", "max_entry_age_pct", True),
    ("fee_rate", "fee_rate", True),
    ("max_book_pct", "max_book_pct", True),
    ("max_drawdown_pct", "max_drawdown_pct", True),
    ("paper_slippage_pct", "paper_slippage_pct", True),
    ("max_imbalance_ratio", "max_imbalance_ratio", True),
    ("fill_size_tokens", "fill_size_tokens", True),
    ("initial_fill_size", "initial_fill_size", True),
    ("max_fill_age_pct", "max_fill_age_pct", True),
    ("hedge_start_threshold", "hedge_start_threshold", True),
    ("hedge_end_threshold", "hedge_end_threshold", True),
    ("hedge_start_pct", "hedge_start_pct", True),
    ("max_primary_price", "max_primary_price", True),
    # Direct params (int, passed through unchanged)
    ("poll_interval", "poll_interval", False),
    ("max_window", "max_window_seconds", False),
    ("max_open_positions", "max_open_positions", False),
    ("single_leg_timeout", "single_leg_timeout", False),
    ("rediscovery_interval", "rediscovery_interval", False),
    ("circuit_breaker_losses", "circuit_breaker_losses", False),
    ("circuit_breaker_cooldown", "circuit_breaker_cooldown", False),
    ("fee_exponent", "fee_exponent", False),
    ("signal_delay_seconds", "signal_delay_seconds", False),
)


def _build_config(
    config_file: str | None,
    *,
    series_slugs_str: str | None,
    compound_profits: bool | None,
    use_market_orders: bool | None,
    strategy: str | None = None,
    **cli_args: object,
) -> SpreadCaptureConfig:
    """Build a ``SpreadCaptureConfig`` from an optional YAML file plus CLI overrides.

    Precedence (highest wins): CLI flags -> YAML file -> dataclass defaults.
    Only non-``None`` CLI values are applied as overrides so that unset
    flags fall through to the YAML or default value.

    Args:
        config_file: Optional path to a YAML configuration file.
        series_slugs_str: Comma-separated series slugs string.
        compound_profits: Tri-state bool (``None`` = not set on CLI).
        use_market_orders: Tri-state bool (``None`` = not set on CLI).
        strategy: Trading strategy name (``None`` = not set on CLI).
        **cli_args: Remaining CLI parameters keyed by their CLI name.

    Returns:
        Fully resolved ``SpreadCaptureConfig``.

    """
    base = (
        SpreadCaptureConfig.from_yaml(Path(config_file)) if config_file else SpreadCaptureConfig()
    )

    overrides: dict[str, object] = {}

    if series_slugs_str is not None:
        overrides["series_slugs"] = parse_series_slugs(series_slugs_str)

    for cli_name, field_name, is_decimal in _PARAM_MAP:
        val = cli_args.get(cli_name)
        if val is not None:
            overrides[field_name] = Decimal(str(val)) if is_decimal else val

    if compound_profits is not None:
        overrides["compound_profits"] = compound_profits
    if use_market_orders is not None:
        overrides["use_market_orders"] = use_market_orders
    if strategy is not None:
        overrides["strategy"] = strategy

    return SpreadCaptureConfig.with_overrides(base, **overrides)


def spread_capture(
    series_slugs: Annotated[
        str | None,
        typer.Option(
            "--series-slugs",
            help="Comma-separated series slugs or 'crypto-5m'/'crypto-15m' shortcut",
        ),
    ] = None,
    config_file: Annotated[
        str | None,
        typer.Option("--config", help="Path to YAML config file (CLI flags override)"),
    ] = None,
    poll_interval: Annotated[int | None, typer.Option(help="Seconds between scan cycles")] = None,
    capital: Annotated[
        str | None, typer.Option(help="Starting capital in USDC (paper mode)")
    ] = None,
    max_position_pct: Annotated[
        str | None, typer.Option(help="Max fraction of capital per spread (e.g. 0.10)")
    ] = None,
    max_combined_cost: Annotated[
        str | None,
        typer.Option(help="Max combined cost of both sides to enter (e.g. 0.98)"),
    ] = None,
    min_spread_margin: Annotated[
        str | None,
        typer.Option(help="Min profit margin per token pair (e.g. 0.01)"),
    ] = None,
    max_open_positions: Annotated[
        int | None, typer.Option(help="Max concurrent spread positions")
    ] = None,
    max_window: Annotated[
        int | None,
        typer.Option(help="Max market window in seconds (e.g. 300 for 5-min only, 0=all)"),
    ] = None,
    max_entry_age_pct: Annotated[
        str | None,
        typer.Option(help="Max fraction of window elapsed to enter (e.g. 0.60)"),
    ] = None,
    single_leg_timeout: Annotated[
        int | None,
        typer.Option(help="Seconds before cancelling unfilled side (live only)"),
    ] = None,
    rediscovery_interval: Annotated[
        int | None,
        typer.Option(help="Seconds between market rediscovery calls"),
    ] = None,
    fee_rate: Annotated[
        str | None, typer.Option(help="Polymarket fee rate coefficient (e.g. 0.25)")
    ] = None,
    fee_exponent: Annotated[
        int | None, typer.Option(help="Polymarket fee exponent (e.g. 2)")
    ] = None,
    max_book_pct: Annotated[
        str | None,
        typer.Option(help="Max fraction of visible depth per side (e.g. 0.20)"),
    ] = None,
    circuit_breaker_losses: Annotated[
        int | None,
        typer.Option(help="Consecutive losses to trigger cooldown (0=disabled)"),
    ] = None,
    circuit_breaker_cooldown: Annotated[
        int | None,
        typer.Option(help="Seconds to pause after circuit breaker triggers"),
    ] = None,
    max_drawdown_pct: Annotated[
        str | None,
        typer.Option(help="Max session drawdown as fraction (e.g. 0.15)"),
    ] = None,
    paper_slippage_pct: Annotated[
        str | None,
        typer.Option(help="Simulated slippage for paper fills (e.g. 0.005)"),
    ] = None,
    compound_profits: Annotated[
        bool | None,
        typer.Option(
            "--compound-profits/--no-compound-profits",
            help="Grow paper capital by adding realised P&L",
        ),
    ] = None,
    use_market_orders: Annotated[
        bool | None,
        typer.Option(
            "--use-market-orders/--no-use-market-orders",
            help="Use FOK market orders instead of GTC limit",
        ),
    ] = None,
    strategy: Annotated[
        str | None,
        typer.Option(
            help="Strategy: 'simultaneous' (both sides at once) or 'accumulate' (per-side)"
        ),
    ] = None,
    max_imbalance_ratio: Annotated[
        str | None,
        typer.Option(help="Max qty ratio between legs before pausing heavier side (e.g. 2.0)"),
    ] = None,
    fill_size_tokens: Annotated[
        str | None,
        typer.Option(help="Adjustment fill size in tokens after initial position (e.g. 5)"),
    ] = None,
    initial_fill_size: Annotated[
        str | None,
        typer.Option(help="Initial fill size in tokens for first entry on each side (e.g. 20)"),
    ] = None,
    max_fill_age_pct: Annotated[
        str | None,
        typer.Option(help="Stop filling when market window is past this fraction (e.g. 0.70)"),
    ] = None,
    signal_delay_seconds: Annotated[
        int | None,
        typer.Option(help="Seconds to wait into window before reading Binance signal (e.g. 60)"),
    ] = None,
    hedge_start_threshold: Annotated[
        str | None,
        typer.Option(help="Early hedge: only buy secondary when ask < this (e.g. 0.45)"),
    ] = None,
    hedge_end_threshold: Annotated[
        str | None,
        typer.Option(help="Late hedge: accept up to this price near cutoff (e.g. 0.55)"),
    ] = None,
    hedge_start_pct: Annotated[
        str | None,
        typer.Option(help="Begin hedge fills at this fraction of window elapsed (e.g. 0.20)"),
    ] = None,
    max_primary_price: Annotated[
        str | None,
        typer.Option(help="Max ask price for primary side fills (e.g. 0.60)"),
    ] = None,
    confirm_live: Annotated[  # noqa: FBT002
        bool, typer.Option("--confirm-live", help="Enable LIVE trading with real orders")
    ] = False,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable DEBUG logging")
    ] = False,
) -> None:
    """Capture spreads on Up/Down markets by buying both sides below $1.00.

    Scan Polymarket rotating markets for spread opportunities where the
    combined cost of buying both Up and Down sides is below the threshold,
    guaranteeing profit at settlement.  Paper mode by default; use
    ``--confirm-live`` for real orders.

    Configuration is layered: dataclass defaults -> YAML file (``--config``)
    -> CLI flags.
    """
    configure_logging(verbose=verbose)

    config = _build_config(
        config_file=config_file,
        series_slugs_str=series_slugs,
        compound_profits=compound_profits,
        use_market_orders=use_market_orders,
        strategy=strategy,
        poll_interval=poll_interval,
        capital=capital,
        max_position_pct=max_position_pct,
        max_combined_cost=max_combined_cost,
        min_spread_margin=min_spread_margin,
        max_open_positions=max_open_positions,
        max_window=max_window,
        max_entry_age_pct=max_entry_age_pct,
        single_leg_timeout=single_leg_timeout,
        rediscovery_interval=rediscovery_interval,
        fee_rate=fee_rate,
        fee_exponent=fee_exponent,
        max_book_pct=max_book_pct,
        circuit_breaker_losses=circuit_breaker_losses,
        circuit_breaker_cooldown=circuit_breaker_cooldown,
        max_drawdown_pct=max_drawdown_pct,
        paper_slippage_pct=paper_slippage_pct,
        max_imbalance_ratio=max_imbalance_ratio,
        fill_size_tokens=fill_size_tokens,
        initial_fill_size=initial_fill_size,
        max_fill_age_pct=max_fill_age_pct,
        signal_delay_seconds=signal_delay_seconds,
        hedge_start_threshold=hedge_start_threshold,
        hedge_end_threshold=hedge_end_threshold,
        hedge_start_pct=hedge_start_pct,
        max_primary_price=max_primary_price,
    )

    if confirm_live:
        typer.echo("=" * 60)
        typer.echo("  WARNING: LIVE TRADING MODE")
        typer.echo("  Real orders will be placed on Polymarket.")
        typer.echo(f"  Capital: ${config.capital}  Max/trade: {config.max_position_pct:.0%}")
        typer.echo("=" * 60)
        time.sleep(_LIVE_WARNING_DELAY)

    async def _run() -> None:
        client = build_authenticated_client()
        repo: SpreadResultRepository | None = None

        # Persist results when SPREAD_DB_URL or WHALE_DB_URL is configured
        db_url = os.environ.get("SPREAD_DB_URL", "") or os.environ.get("WHALE_DB_URL", "")
        if db_url:
            repo = SpreadResultRepository(db_url)
            await repo.init_db()
            _logger.info("Spread result persistence enabled")

        if config.strategy == "accumulate":
            trader: SpreadTrader | AccumulatingTrader = AccumulatingTrader(
                config=config,
                live=confirm_live,
                client=client,
            )
        else:
            trader = SpreadTrader(
                config=config,
                live=confirm_live,
                client=client,
            )
        if repo is not None:
            trader.set_repo(repo)

        try:
            await trader.run()
        finally:
            await client.close()
            if repo is not None:
                await repo.close()

    asyncio.run(_run())
