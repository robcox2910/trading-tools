"""Shared helpers for Polymarket CLI commands.

Centralise constants and utility functions that are reused across multiple
CLI command modules: series slug parsing, verbose logging setup, and
authenticated client construction.
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


def parse_series_slugs(series: str) -> tuple[str, ...]:
    """Parse a comma-separated series string into expanded slug tuples.

    Expand the special value ``"crypto-5m"`` into all four crypto
    Up/Down 5-minute series slugs.

    Args:
        series: Comma-separated series slugs or ``"crypto-5m"`` shortcut.

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
