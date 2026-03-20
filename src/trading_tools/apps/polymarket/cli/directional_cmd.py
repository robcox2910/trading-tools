"""CLI command for the directional trading bot on Polymarket.

Run a polling service that scans BTC/ETH Up/Down markets, extracts
features from Binance candles and Polymarket order books, estimates
P(Up), and buys the predicted winning side with Kelly-optimal sizing.
Default is paper mode; pass ``--confirm-live`` for real orders.
"""

import asyncio
import logging
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from trading_tools.apps.directional.config import DirectionalConfig
from trading_tools.apps.directional.repository import DirectionalResultRepository
from trading_tools.apps.directional.trader import DirectionalTrader
from trading_tools.apps.polymarket.cli._helpers import (
    build_authenticated_client,
    configure_logging,
    parse_series_slugs,
)
from trading_tools.apps.whale_monitor.repository import WhaleRepository

_logger = logging.getLogger(__name__)

_LIVE_WARNING_DELAY = 2


def directional(
    series_slugs: Annotated[
        str | None,
        typer.Option(
            "--series-slugs",
            help="Comma-separated series slugs or 'crypto-5m' shortcut",
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
    min_edge: Annotated[
        str | None, typer.Option(help="Minimum probability edge to enter (e.g. 0.05)")
    ] = None,
    kelly_fraction: Annotated[
        str | None, typer.Option(help="Fractional Kelly multiplier (e.g. 0.5)")
    ] = None,
    max_position_pct: Annotated[
        str | None, typer.Option(help="Max fraction of capital per trade (e.g. 0.15)")
    ] = None,
    entry_start: Annotated[
        int | None, typer.Option(help="Seconds before close to start entries (e.g. 30)")
    ] = None,
    entry_end: Annotated[
        int | None, typer.Option(help="Seconds before close to stop entries (e.g. 10)")
    ] = None,
    signal_lookback: Annotated[
        int | None, typer.Option(help="Seconds of Binance candle lookback (e.g. 300)")
    ] = None,
    max_open_positions: Annotated[
        int | None, typer.Option(help="Max concurrent directional positions")
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
    confirm_live: Annotated[
        bool, typer.Option("--confirm-live", help="Enable LIVE trading with real orders")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable DEBUG logging")] = False,
) -> None:
    """Trade directionally on Up/Down markets by buying the predicted winner.

    Scan Polymarket rotating crypto markets, extract features from
    Binance candles and order books, estimate P(Up) via a weighted
    ensemble, and buy the predicted winning side with Kelly-optimal
    sizing.  Paper mode by default; use ``--confirm-live`` for real
    orders.
    """
    configure_logging(verbose=verbose)

    # Build config: defaults -> YAML -> CLI overrides
    base = DirectionalConfig.from_yaml(Path(config_file)) if config_file else DirectionalConfig()

    overrides: dict[str, object] = {}
    if series_slugs is not None:
        overrides["series_slugs"] = parse_series_slugs(series_slugs)

    _decimal_params = {
        "capital": capital,
        "min_edge": min_edge,
        "kelly_fraction": kelly_fraction,
        "max_position_pct": max_position_pct,
        "max_drawdown_pct": max_drawdown_pct,
    }
    for key, val in _decimal_params.items():
        if val is not None:
            overrides[key] = Decimal(str(val))

    overrides.update(
        {
            key: val
            for key, val in {
                "poll_interval": poll_interval,
                "entry_window_start": entry_start,
                "entry_window_end": entry_end,
                "signal_lookback_seconds": signal_lookback,
                "max_open_positions": max_open_positions,
                "circuit_breaker_losses": circuit_breaker_losses,
                "circuit_breaker_cooldown": circuit_breaker_cooldown,
            }.items()
            if val is not None
        }
    )

    config = DirectionalConfig.with_overrides(base, **overrides)

    if confirm_live:
        typer.echo("=" * 60)
        typer.echo("  WARNING: LIVE TRADING MODE")
        typer.echo("  Real orders will be placed on Polymarket.")
        typer.echo(f"  Capital: ${config.capital}  Kelly: {config.kelly_fraction}")
        typer.echo("=" * 60)
        time.sleep(_LIVE_WARNING_DELAY)

    async def _run() -> None:
        client = build_authenticated_client()
        repo: DirectionalResultRepository | None = None
        whale_repo: WhaleRepository | None = None

        # Persist results when SPREAD_DB_URL or WHALE_DB_URL is configured
        db_url = os.environ.get("SPREAD_DB_URL", "") or os.environ.get("WHALE_DB_URL", "")
        if db_url:
            repo = DirectionalResultRepository(db_url)
            await repo.init_db()
            _logger.info("Directional result persistence enabled")

            # Reuse same DB for whale signal queries
            whale_repo = WhaleRepository(db_url)
            _logger.info("Whale signal feature enabled")

        trader = DirectionalTrader(
            config=config,
            client=client,
            live=confirm_live,
        )
        if repo is not None:
            trader.set_repository(repo)
        if whale_repo is not None:
            trader.set_whale_repo(whale_repo)

        try:
            await trader.run()
        finally:
            await client.close()
            if repo is not None:
                await repo.close()
            if whale_repo is not None:
                await whale_repo.close()

    asyncio.run(_run())
