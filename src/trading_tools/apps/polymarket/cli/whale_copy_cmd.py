"""CLI command for real-time whale copy-trading on Polymarket.

Run a polling service that monitors a whale's trades via the Polymarket
Data API directly, detects directional bias signals on BTC/ETH markets,
and copies them using temporal spread arbitrage. Defaults to paper mode;
pass ``--confirm-live`` for real orders.

When ``WHALE_DB_URL`` is set, closed trade results are persisted to the
``copy_results`` table in the same database as whale trades.
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
)
from trading_tools.apps.whale_copy_trader.config import WhaleCopyConfig
from trading_tools.apps.whale_copy_trader.copy_trader import WhaleCopyTrader
from trading_tools.apps.whale_copy_trader.repository import CopyResultRepository

_logger = logging.getLogger(__name__)

_LIVE_WARNING_DELAY = 2

# Maps CLI parameter names to WhaleCopyConfig field names for str→Decimal
# conversion.  Only params whose CLI type is ``str | None`` and whose
# config field type is ``Decimal`` belong here.
_DECIMAL_PARAMS: dict[str, str] = {
    "min_bias": "min_bias",
    "capital": "capital",
    "max_position_pct": "max_position_pct",
    "max_spread_cost": "max_spread_cost",
    "max_entry_price": "max_entry_price",
    "stop_loss_pct": "stop_loss_pct",
    "win_rate": "win_rate",
    "kelly_fraction": "kelly_fraction",
    "clob_fee_rate": "clob_fee_rate",
    "take_profit_pct": "take_profit_pct",
    "max_unhedged_exposure_pct": "max_unhedged_exposure_pct",
    "min_win_rate": "min_win_rate",
    "max_asset_exposure_pct": "max_asset_exposure_pct",
    "hedge_urgency_threshold": "hedge_urgency_threshold",
    "hedge_urgency_spread_bump": "hedge_urgency_spread_bump",
}

# Maps CLI parameter names to config field names for pass-through values
# (int params that don't need Decimal conversion).
_DIRECT_PARAMS: dict[str, str] = {
    "poll_interval": "poll_interval",
    "lookback": "lookback_seconds",
    "min_trades": "min_trades",
    "min_time_to_start": "min_time_to_start",
    "max_window": "max_window_seconds",
    "min_kelly_results": "min_kelly_results",
    "circuit_breaker_losses": "circuit_breaker_losses",
    "circuit_breaker_cooldown": "circuit_breaker_cooldown",
}


def _build_config(
    address: str,
    config_file: str | None,
    *,
    no_hedge_market_orders: bool,
    adaptive_kelly: bool | None,
    compound_profits: bool | None,
    use_market_orders: bool | None,
    **cli_args: object,
) -> WhaleCopyConfig:
    """Build a ``WhaleCopyConfig`` from an optional YAML file plus CLI overrides.

    Precedence (highest wins): CLI flags → YAML file → dataclass defaults.
    Only non-``None`` CLI values are applied as overrides so that unset
    flags fall through to the YAML or default value.

    Args:
        address: Whale proxy wallet address (always applied).
        config_file: Optional path to a YAML configuration file.
        no_hedge_market_orders: Inverted bool flag for hedge order type.
        adaptive_kelly: Tri-state bool (``None`` = not set on CLI).
        compound_profits: Tri-state bool (``None`` = not set on CLI).
        use_market_orders: Tri-state bool (``None`` = not set on CLI).
        **cli_args: Remaining CLI parameters keyed by their CLI name.

    Returns:
        Fully resolved ``WhaleCopyConfig``.

    """
    base = (
        WhaleCopyConfig.from_yaml(Path(config_file))
        if config_file
        else WhaleCopyConfig(whale_address=address)
    )

    overrides: dict[str, object] = {"whale_address": address}

    for cli_name, field_name in _DECIMAL_PARAMS.items():
        val = cli_args.get(cli_name)
        if val is not None:
            overrides[field_name] = Decimal(str(val))

    for cli_name, field_name in _DIRECT_PARAMS.items():
        val = cli_args.get(cli_name)
        if val is not None:
            overrides[field_name] = val

    # Boolean flags with special handling
    if no_hedge_market_orders:
        overrides["hedge_with_market_orders"] = False
    if adaptive_kelly is not None:
        overrides["adaptive_kelly"] = adaptive_kelly
    if compound_profits is not None:
        overrides["compound_profits"] = compound_profits
    if use_market_orders is not None:
        overrides["use_market_orders"] = use_market_orders

    return WhaleCopyConfig.with_overrides(base, **overrides)


def whale_copy(  # noqa: PLR0913
    address: Annotated[str, typer.Option(help="Whale proxy wallet address to copy")],
    config_file: Annotated[
        str | None,
        typer.Option("--config", help="Path to YAML config file (CLI flags override)"),
    ] = None,
    poll_interval: Annotated[
        int | None, typer.Option(help="Seconds between API polls (lower = faster)")
    ] = None,
    lookback: Annotated[
        int | None, typer.Option(help="Rolling window in seconds for trade accumulation")
    ] = None,
    min_bias: Annotated[
        str | None, typer.Option(help="Minimum bias ratio to trigger a copy signal")
    ] = None,
    min_trades: Annotated[
        int | None, typer.Option(help="Minimum trades per market to trigger a signal")
    ] = None,
    min_time_to_start: Annotated[
        int | None, typer.Option(help="Min seconds before window opens to act on signal")
    ] = None,
    capital: Annotated[
        str | None, typer.Option(help="Starting capital in USDC (paper mode)")
    ] = None,
    max_position_pct: Annotated[
        str | None, typer.Option(help="Max fraction of capital per trade (e.g. 0.10)")
    ] = None,
    max_window: Annotated[
        int | None,
        typer.Option(help="Max market window in seconds (e.g. 300 for 5-min only, 0=all)"),
    ] = None,
    max_spread_cost: Annotated[
        str | None,
        typer.Option(help="Max combined cost of both legs to trigger hedge (e.g. 0.95)"),
    ] = None,
    max_entry_price: Annotated[
        str | None,
        typer.Option(help="Max price for directional entry (skip if above, e.g. 0.65)"),
    ] = None,
    no_hedge_market_orders: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--no-hedge-market-orders",
            help="Use GTC limit orders for hedge leg instead of FOK market",
        ),
    ] = False,
    stop_loss_pct: Annotated[
        str | None,
        typer.Option(help="Stop-loss threshold as fraction (e.g. 0.50 = cut at 50%% drop)"),
    ] = None,
    win_rate: Annotated[
        str | None, typer.Option(help="Estimated whale win rate for Kelly sizing (e.g. 0.80)")
    ] = None,
    kelly_fraction: Annotated[
        str | None, typer.Option(help="Fractional Kelly multiplier (e.g. 0.5 = half-Kelly)")
    ] = None,
    clob_fee_rate: Annotated[
        str | None, typer.Option(help="Per-leg CLOB fee rate for hedge profitability (e.g. 0.0)")
    ] = None,
    take_profit_pct: Annotated[
        str | None,
        typer.Option(help="Take profit at this %% gain above entry (e.g. 0.15 = 15%%)"),
    ] = None,
    max_unhedged_exposure_pct: Annotated[
        str | None,
        typer.Option(help="Max fraction of capital in unhedged positions (e.g. 0.50)"),
    ] = None,
    adaptive_kelly: Annotated[
        bool | None,
        typer.Option(
            "--adaptive-kelly/--no-adaptive-kelly",
            help="Dynamically adjust Kelly win rate from realised outcomes",
        ),
    ] = None,
    min_kelly_results: Annotated[
        int | None,
        typer.Option(help="Min closed unhedged trades before adaptive Kelly activates"),
    ] = None,
    min_win_rate: Annotated[
        str | None, typer.Option(help="Floor for adaptive Kelly win rate (e.g. 0.55)")
    ] = None,
    max_asset_exposure_pct: Annotated[
        str | None,
        typer.Option(help="Max fraction of capital per asset+side (e.g. 0.30)"),
    ] = None,
    compound_profits: Annotated[
        bool | None,
        typer.Option(
            "--compound-profits/--no-compound-profits",
            help="Grow paper capital by adding realised P&L",
        ),
    ] = None,
    hedge_urgency_threshold: Annotated[
        str | None,
        typer.Option(help="Time fraction below which hedge spread is relaxed (e.g. 0.20)"),
    ] = None,
    hedge_urgency_spread_bump: Annotated[
        str | None,
        typer.Option(help="Amount added to max_spread_cost in urgency zone (e.g. 0.03)"),
    ] = None,
    circuit_breaker_losses: Annotated[
        int | None,
        typer.Option(help="Consecutive unhedged losses to trigger cooldown (0=disabled)"),
    ] = None,
    circuit_breaker_cooldown: Annotated[
        int | None,
        typer.Option(help="Seconds to pause new entries after circuit breaker triggers"),
    ] = None,
    use_market_orders: Annotated[
        bool | None,
        typer.Option(
            "--use-market-orders/--no-use-market-orders",
            help="Use market orders (FOK) instead of limit orders (GTC)",
        ),
    ] = None,
    confirm_live: Annotated[  # noqa: FBT002
        bool, typer.Option("--confirm-live", help="Enable LIVE trading with real orders")
    ] = False,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable DEBUG logging")
    ] = False,
) -> None:
    """Copy a whale's directional bets on BTC/ETH markets in real-time.

    Poll the Polymarket Data API directly for the whale's trades, detect
    directional bias signals, and copy them using temporal spread
    arbitrage. Paper mode by default; use ``--confirm-live`` for real
    Polymarket orders.

    Configuration is layered: dataclass defaults → YAML file (``--config``)
    → CLI flags. Only explicitly-set CLI flags override YAML values.
    """
    configure_logging(verbose=verbose)

    config = _build_config(
        address=address,
        config_file=config_file,
        no_hedge_market_orders=no_hedge_market_orders,
        adaptive_kelly=adaptive_kelly,
        compound_profits=compound_profits,
        use_market_orders=use_market_orders,
        poll_interval=poll_interval,
        lookback=lookback,
        min_bias=min_bias,
        min_trades=min_trades,
        min_time_to_start=min_time_to_start,
        capital=capital,
        max_position_pct=max_position_pct,
        max_window=max_window,
        max_spread_cost=max_spread_cost,
        max_entry_price=max_entry_price,
        stop_loss_pct=stop_loss_pct,
        win_rate=win_rate,
        kelly_fraction=kelly_fraction,
        clob_fee_rate=clob_fee_rate,
        take_profit_pct=take_profit_pct,
        max_unhedged_exposure_pct=max_unhedged_exposure_pct,
        min_kelly_results=min_kelly_results,
        min_win_rate=min_win_rate,
        max_asset_exposure_pct=max_asset_exposure_pct,
        hedge_urgency_threshold=hedge_urgency_threshold,
        hedge_urgency_spread_bump=hedge_urgency_spread_bump,
        circuit_breaker_losses=circuit_breaker_losses,
        circuit_breaker_cooldown=circuit_breaker_cooldown,
    )

    if confirm_live:
        typer.echo("=" * 60)
        typer.echo("  WARNING: LIVE TRADING MODE")
        typer.echo("  Real orders will be placed on Polymarket.")
        typer.echo(f"  Capital: ${config.capital}  Max/trade: {config.max_position_pct:.0%}")
        typer.echo("=" * 60)
        time.sleep(_LIVE_WARNING_DELAY)

    async def _run() -> None:
        # Both paper and live modes need a client for CLOB price data
        client = build_authenticated_client()
        repo: CopyResultRepository | None = None

        # Persist closed trade results when WHALE_DB_URL is configured
        whale_db_url = os.environ.get("WHALE_DB_URL", "")
        if whale_db_url:
            repo = CopyResultRepository(whale_db_url)
            await repo.init_db()
            _logger.info("Copy result persistence enabled (WHALE_DB_URL)")

        trader = WhaleCopyTrader(
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
