"""Shared helpers for Polymarket CLI commands.

Centralise constants and utility functions that are reused across multiple
CLI command modules: series slug parsing, verbose logging setup, authenticated
client construction, and market discovery.
"""

import logging
import os

import typer

from trading_tools.clients.polymarket.client import PolymarketClient

CRYPTO_5M_SERIES: tuple[str, ...] = (
    "btc-updown-5m",
    "eth-updown-5m",
    "sol-updown-5m",
    "xrp-updown-5m",
)

CRYPTO_15M_SERIES: tuple[str, ...] = (
    "btc-updown-15m",
    "eth-updown-15m",
    "sol-updown-15m",
    "xrp-updown-15m",
)


def parse_series_slugs(series: str) -> tuple[str, ...]:
    """Parse a comma-separated series string into expanded slug tuples.

    Expand the shortcuts ``"crypto-5m"`` and ``"crypto-15m"`` into
    all four crypto Up/Down series slugs for the respective timeframe.

    Args:
        series: Comma-separated series slugs, or the ``"crypto-5m"`` /
            ``"crypto-15m"`` shortcuts.

    Returns:
        Tuple of expanded series slug strings.

    """
    slugs: list[str] = []
    for s in series.split(","):
        s = s.strip()  # noqa: PLW2901
        if not s:
            continue
        if s == "crypto-5m":
            slugs.extend(CRYPTO_5M_SERIES)
        elif s == "crypto-15m":
            slugs.extend(CRYPTO_15M_SERIES)
        else:
            slugs.append(s)
    return tuple(slugs)


def configure_verbose_logging() -> None:
    """Enable INFO-level logging for tick-by-tick engine output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )


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
