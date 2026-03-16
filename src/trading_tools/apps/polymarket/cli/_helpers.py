"""Shared helpers for Polymarket CLI commands.

Centralise constants and utility functions that are reused across multiple
CLI command modules: series slug parsing, verbose logging setup, authenticated
client construction, market discovery, and strategy parameter handling.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import typer

from trading_tools.apps.polymarket_bot.strategy_factory import build_pm_strategy
from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError

if TYPE_CHECKING:
    from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy

CRYPTO_5M_SERIES: tuple[str, ...] = (
    "btc-updown-5m",
    "eth-updown-5m",
    "sol-updown-5m",
    "xrp-updown-5m",
    "doge-updown-5m",
    "bnb-updown-5m",
    "hype-updown-5m",
)

CRYPTO_15M_SERIES: tuple[str, ...] = (
    "btc-updown-15m",
    "eth-updown-15m",
    "sol-updown-15m",
    "xrp-updown-15m",
    "doge-updown-15m",
    "bnb-updown-15m",
    "hype-updown-15m",
)

CRYPTO_4H_SERIES: tuple[str, ...] = (
    "btc-updown-4h",
    "eth-updown-4h",
    "sol-updown-4h",
    "xrp-updown-4h",
    "doge-updown-4h",
    "bnb-updown-4h",
    "hype-updown-4h",
)

CRYPTO_DAILY_SERIES: tuple[str, ...] = (
    "btc-updown-daily",
    "eth-updown-daily",
    "sol-updown-daily",
    "xrp-updown-daily",
    "doge-updown-daily",
    "bnb-updown-daily",
    "hype-updown-daily",
)


_SHORTCUT_MAP: dict[str, tuple[str, ...]] = {
    "crypto-5m": CRYPTO_5M_SERIES,
    "crypto-15m": CRYPTO_15M_SERIES,
    "crypto-4h": CRYPTO_4H_SERIES,
    "crypto-daily": CRYPTO_DAILY_SERIES,
}


def parse_series_slugs(series: str) -> tuple[str, ...]:
    """Parse a comma-separated series string into expanded slug tuples.

    Expand the shortcuts ``"crypto-5m"``, ``"crypto-15m"``, ``"crypto-4h"``,
    and ``"crypto-daily"`` into all crypto Up/Down series slugs for the
    respective timeframe.

    Args:
        series: Comma-separated series slugs, or shortcuts like
            ``"crypto-5m"`` / ``"crypto-15m"`` / ``"crypto-4h"`` /
            ``"crypto-daily"``.

    Returns:
        Tuple of expanded series slug strings.

    """
    slugs: list[str] = []
    for raw in series.split(","):
        slug = raw.strip()
        if not slug:
            continue
        if slug in _SHORTCUT_MAP:
            slugs.extend(_SHORTCUT_MAP[slug])
        else:
            slugs.append(slug)
    return tuple(slugs)


def configure_logging(*, verbose: bool = False) -> None:
    """Configure application logging for bot commands.

    Always enable INFO-level logging so trade signals, PERF summaries,
    and market rotations are visible.  When ``verbose`` is ``True``,
    lower application loggers to DEBUG while keeping noisy third-party
    loggers (websockets, httpx) at WARNING to avoid disk-filling output.

    Args:
        verbose: Enable DEBUG-level logging for application code.

    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if verbose:
        # Silence noisy third-party loggers that flood disk at DEBUG level
        for name in ("websockets", "httpx", "httpcore", "py_clob_client", "hpack", "h2"):
            logging.getLogger(name).setLevel(logging.WARNING)


def require_whale_db_url() -> str:
    """Read the WHALE_DB_URL environment variable or abort.

    Abort with a clear error if the environment variable is not set.
    There is no SQLite fallback — whale data lives in PostgreSQL.

    Returns:
        The WHALE_DB_URL connection string.

    """
    db_url = os.environ.get("WHALE_DB_URL", "")
    if not db_url:
        typer.echo(
            "Error: WHALE_DB_URL environment variable is required.\n"
            "Set it to your PostgreSQL connection string, e.g.:\n"
            '  export WHALE_DB_URL="postgresql+asyncpg://user:pass@host:5432/trading_tools"',
            err=True,
        )
        raise typer.Exit(code=1)
    return db_url


def build_authenticated_client() -> PolymarketClient:
    """Build an authenticated PolymarketClient from environment variables.

    Read the private key and optional API credentials from the environment.
    Abort with an error if the private key is not set.

    Returns:
        Authenticated PolymarketClient ready for trading.

    """
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        typer.echo("Error: POLYMARKET_PRIVATE_KEY environment variable is required.", err=True)
        raise typer.Exit(code=1)

    api_key = os.environ.get("POLYMARKET_API_KEY") or None
    api_secret = os.environ.get("POLYMARKET_API_SECRET") or None
    api_passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE") or None
    funder_address = os.environ.get("POLYMARKET_FUNDER_ADDRESS") or None

    return PolymarketClient(
        private_key=private_key,
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
        funder_address=funder_address,
    )


async def discover_markets(
    client: PolymarketClient,
    slugs: tuple[str, ...],
) -> list[tuple[str, str]]:
    """Discover active markets from series slugs.

    Query the Gamma API for each slug and return the condition IDs
    and end dates of all active markets found.

    Args:
        client: Polymarket API client for Gamma lookups.
        slugs: Expanded series slug tuple.

    Returns:
        List of ``(condition_id, end_date)`` tuples.

    """
    if not slugs:
        return []

    typer.echo(f"Discovering markets for series: {', '.join(slugs)}...")
    discovered = await client.discover_series_markets(list(slugs))
    for cid, end_date in discovered:
        typer.echo(f"  Found: {cid[:20]}... ends {end_date}")
    return discovered


@dataclass(frozen=True)
class StrategyParams:
    """Strategy-specific parameters shared by paper and live bot commands.

    Encapsulate the keyword arguments forwarded to ``build_pm_strategy``
    so both bot CLIs reference a single definition instead of duplicating
    option defaults and the strategy-building call.

    Attributes:
        period: Rolling window period for mean reversion strategy.
        z_threshold: Z-score threshold for mean reversion strategy.
        spread_pct: Half-spread fraction for market making strategy.
        imbalance_threshold: Imbalance threshold for liquidity strategy.
        min_edge: Minimum edge for cross-market arbitrage strategy.
        snipe_threshold: Price threshold for late snipe strategy (0.5-1.0).
        snipe_window: Seconds before market end to start sniping.

    """

    period: int = 20
    z_threshold: float = 1.5
    spread_pct: float = 0.03
    imbalance_threshold: float = 0.65
    min_edge: float = 0.02
    snipe_threshold: float = 0.8
    snipe_window: int = 60

    def build_strategy(self, name: str) -> PredictionMarketStrategy:
        """Build a strategy instance from these parameters.

        Wrap ``build_pm_strategy`` with a user-friendly error handler
        that aborts via ``typer.Exit`` on invalid strategy names.

        Args:
            name: Strategy identifier (must be one of ``PM_STRATEGY_NAMES``).

        Returns:
            Configured ``PredictionMarketStrategy`` instance.

        Raises:
            typer.Exit: If the strategy name is invalid.

        """
        try:
            return build_pm_strategy(
                name,
                period=self.period,
                z_threshold=self.z_threshold,
                spread_pct=self.spread_pct,
                imbalance_threshold=self.imbalance_threshold,
                min_edge=self.min_edge,
                snipe_threshold=self.snipe_threshold,
                snipe_window=self.snipe_window,
            )
        except typer.BadParameter as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc


async def resolve_market_ids(
    client: PolymarketClient,
    markets: str,
    series: str,
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Parse market IDs and discover markets from series slugs.

    Combine explicitly specified condition IDs with auto-discovered
    markets from series slugs. Abort if no markets are resolved.

    Args:
        client: Polymarket API client for Gamma lookups.
        markets: Comma-separated condition IDs.
        series: Comma-separated series slugs (or shortcuts like ``"crypto-5m"``).

    Returns:
        Tuple of ``(market_ids, market_end_times, series_slugs)`` where
        ``market_end_times`` contains ``(condition_id, end_date)`` pairs
        from discovered markets and ``series_slugs`` are the expanded slugs.

    Raises:
        typer.Exit: If no markets are resolved from either source.

    """
    market_ids = tuple(m.strip() for m in markets.split(",") if m.strip())
    market_end_times: tuple[tuple[str, str], ...] = ()
    series_slugs = parse_series_slugs(series)

    if series_slugs:
        try:
            discovered = await discover_markets(client, series_slugs)
        except PolymarketAPIError as exc:
            typer.echo(f"Warning: Series discovery failed: {exc}", err=True)
            discovered = []

        if discovered:
            discovered_ids = tuple(cid for cid, _ in discovered)
            market_end_times = tuple(discovered)
            market_ids = (*market_ids, *discovered_ids)

    if not market_ids:
        typer.echo(
            "Error: specify --markets or --series (e.g. --series crypto-5m)",
            err=True,
        )
        raise typer.Exit(code=1)

    return market_ids, market_end_times, series_slugs
