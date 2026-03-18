"""CLI command for the whale copy trading bot on Polymarket.

Run a polling service that mirrors whale directional positioning on
BTC/ETH/SOL/XRP Up/Down markets.  Default is paper mode; pass
``--confirm-live`` for real orders.
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
    require_whale_db_url,
)
from trading_tools.apps.spread_capture.repository import SpreadResultRepository
from trading_tools.apps.whale_copy.config import WhaleCopyConfig
from trading_tools.apps.whale_copy.signal import WhaleSignalClient
from trading_tools.apps.whale_copy.trader import WhaleCopyTrader
from trading_tools.apps.whale_monitor.repository import WhaleRepository

_logger = logging.getLogger(__name__)

_LIVE_WARNING_DELAY = 2


def _build_config(
    *,
    config_file: str | None,
    series_slugs: str | None,
    capital: str | None,
    fill_size: str | None,
    max_price: str | None,
    min_conviction: str | None,
    max_position_pct: str | None,
    max_drawdown_pct: str | None,
    poll_interval: int | None,
    max_open_positions: int | None,
    circuit_breaker_losses: int | None,
    circuit_breaker_cooldown: int | None,
) -> WhaleCopyConfig:
    """Build a ``WhaleCopyConfig`` from defaults, YAML, and CLI overrides.

    Args:
        config_file: Optional YAML config path.
        series_slugs: Comma-separated series slugs.
        capital: Starting capital string.
        fill_size: Tokens per fill string.
        max_price: Max ask price string.
        min_conviction: Min whale conviction ratio string.
        max_position_pct: Max position fraction string.
        max_drawdown_pct: Max drawdown fraction string.
        poll_interval: Poll interval seconds.
        max_open_positions: Max concurrent positions.
        circuit_breaker_losses: Consecutive losses threshold.
        circuit_breaker_cooldown: Cooldown seconds.

    Returns:
        Fully resolved ``WhaleCopyConfig``.

    """
    base = WhaleCopyConfig.from_yaml(Path(config_file)) if config_file else WhaleCopyConfig()

    overrides: dict[str, object] = {}
    if series_slugs is not None:
        overrides["series_slugs"] = parse_series_slugs(series_slugs)

    for key, val in {
        "capital": capital,
        "fill_size_tokens": fill_size,
        "max_price": max_price,
        "min_whale_conviction": min_conviction,
        "max_position_pct": max_position_pct,
        "max_drawdown_pct": max_drawdown_pct,
    }.items():
        if val is not None:
            overrides[key] = Decimal(str(val))

    overrides.update(
        {
            key: val
            for key, val in {
                "poll_interval": poll_interval,
                "max_open_positions": max_open_positions,
                "circuit_breaker_losses": circuit_breaker_losses,
                "circuit_breaker_cooldown": circuit_breaker_cooldown,
            }.items()
            if val is not None
        }
    )

    return WhaleCopyConfig.with_overrides(base, **overrides)


async def _run_trader(config: WhaleCopyConfig, *, live: bool) -> None:
    """Load whales, create the trader, and run the polling loop.

    Args:
        config: Fully resolved whale copy configuration.
        live: Enable live trading with real orders.

    """
    whale_db_url = require_whale_db_url()
    whale_repo = WhaleRepository(whale_db_url)
    try:
        whales = await whale_repo.get_active_whales()
    finally:
        await whale_repo.close()

    if not whales:
        typer.echo("Error: no active whales found in database.", err=True)
        raise typer.Exit(code=1)

    whale_addresses = tuple(w.address for w in whales)
    typer.echo(f"Loaded {len(whale_addresses)} whale addresses")

    signal_client = WhaleSignalClient(whale_addresses=whale_addresses)
    client = build_authenticated_client()

    repo: SpreadResultRepository | None = None
    db_url = os.environ.get("SPREAD_DB_URL", "") or os.environ.get("WHALE_DB_URL", "")
    if db_url:
        repo = SpreadResultRepository(db_url)
        await repo.init_db()
        _logger.info("Result persistence enabled")

    trader = WhaleCopyTrader(config=config, signal_client=signal_client, live=live, client=client)
    if repo is not None:
        trader.set_repo(repo)

    try:
        await trader.run()
    finally:
        await signal_client.close()
        await client.close()
        if repo is not None:
            await repo.close()


def whale_copy(
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
    fill_size: Annotated[str | None, typer.Option(help="Tokens per fill (>= 5 minimum)")] = None,
    max_price: Annotated[str | None, typer.Option(help="Maximum ask price to buy")] = None,
    min_conviction: Annotated[
        str | None,
        typer.Option(help="Minimum whale dollar ratio on favoured side"),
    ] = None,
    max_position_pct: Annotated[
        str | None, typer.Option(help="Max fraction of capital per market")
    ] = None,
    max_open_positions: Annotated[
        int | None, typer.Option(help="Max concurrent whale copy positions")
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
        typer.Option(help="Max session drawdown as fraction (e.g. 0.20)"),
    ] = None,
    confirm_live: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--confirm-live", help="Enable LIVE trading with real orders"),
    ] = False,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable DEBUG logging")
    ] = False,
) -> None:
    """Mirror whale directional positioning on Up/Down markets.

    Continuously poll whale trade activity via the Polymarket Data API,
    accumulate tokens on the whale's favoured side each cycle, and
    settle at market expiry.  The whale direction updates dynamically
    every poll — no locked-in signal.  Paper mode by default; use
    ``--confirm-live`` for real orders.
    """
    configure_logging(verbose=verbose)

    config = _build_config(
        config_file=config_file,
        series_slugs=series_slugs,
        capital=capital,
        fill_size=fill_size,
        max_price=max_price,
        min_conviction=min_conviction,
        max_position_pct=max_position_pct,
        max_drawdown_pct=max_drawdown_pct,
        poll_interval=poll_interval,
        max_open_positions=max_open_positions,
        circuit_breaker_losses=circuit_breaker_losses,
        circuit_breaker_cooldown=circuit_breaker_cooldown,
    )

    if confirm_live:
        typer.echo("=" * 60)
        typer.echo("  WARNING: LIVE TRADING MODE")
        typer.echo("  Real orders will be placed on Polymarket.")
        typer.echo(f"  Capital: ${config.capital}  Fill: {config.fill_size_tokens} tokens")
        typer.echo("=" * 60)
        time.sleep(_LIVE_WARNING_DELAY)

    asyncio.run(_run_trader(config, live=confirm_live))
