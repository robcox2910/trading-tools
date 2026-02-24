"""CLI commands for live trade execution on Polymarket.

Provide ``trade``, ``balance``, ``orders``, and ``cancel`` subcommands
that authenticate against the CLOB API using a Polygon wallet private key
and execute real trades.  All trade commands require confirmation by default.
"""

import asyncio
import os
from decimal import Decimal, InvalidOperation
from typing import Annotated

import typer

from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import OrderRequest

_SIDE_BUY = "BUY"
_SIDE_SELL = "SELL"
_VALID_SIDES = {_SIDE_BUY, _SIDE_SELL}
_VALID_OUTCOMES = {"yes", "no"}
_VALID_ORDER_TYPES = {"limit", "market"}
_MIN_PRICE = Decimal("0.01")
_MAX_PRICE = Decimal("0.99")


def _build_authenticated_client() -> PolymarketClient:
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


def _resolve_token_id(
    market_tokens: tuple[object, ...],
    outcome: str,
) -> str:
    """Resolve a token ID from the market's tokens by outcome label.

    Args:
        market_tokens: Tuple of MarketToken objects from the market.
        outcome: Outcome label to match (case-insensitive).

    Returns:
        Token ID for the matching outcome.

    Raises:
        typer.Exit: When the outcome is not found in the market's tokens.

    """
    outcome_lower = outcome.lower()
    for token in market_tokens:
        if getattr(token, "outcome", "").lower() == outcome_lower:
            return str(getattr(token, "token_id", ""))
    typer.echo(f"Error: Outcome '{outcome}' not found in market tokens.", err=True)
    raise typer.Exit(code=1)


def trade(
    condition_id: Annotated[str, typer.Option(help="Market condition ID (hex string)")],
    side: Annotated[str, typer.Option(help="Order side: buy or sell")],
    outcome: Annotated[str, typer.Option(help="Outcome to trade: yes or no")],
    amount: Annotated[float, typer.Option(help="Number of shares to trade")],
    price: Annotated[
        float, typer.Option(help="Limit price (0.01-0.99, ignored for market orders)")
    ] = 0.5,
    order_type: Annotated[
        str, typer.Option("--type", help="Order type: limit or market")
    ] = "limit",
    no_confirm: Annotated[  # noqa: FBT002
        bool, typer.Option("--no-confirm", help="Skip confirmation prompt")
    ] = False,
) -> None:
    """Place a trade on a Polymarket prediction market.

    Fetch market info, display a summary with order book top-of-book,
    prompt for confirmation, then submit the order.

    Args:
        condition_id: Market condition ID (hex string).
        side: Order side (buy or sell).
        outcome: Outcome token to trade (yes or no).
        amount: Number of shares to trade.
        price: Limit price between 0.01 and 0.99.
        order_type: ``limit`` for GTC or ``market`` for FOK.
        no_confirm: Skip the confirmation prompt.

    """
    side_upper = side.upper()
    if side_upper not in _VALID_SIDES:
        typer.echo(f"Error: Side must be 'buy' or 'sell', got '{side}'.", err=True)
        raise typer.Exit(code=1)

    if outcome.lower() not in _VALID_OUTCOMES:
        typer.echo(f"Error: Outcome must be 'yes' or 'no', got '{outcome}'.", err=True)
        raise typer.Exit(code=1)

    if order_type.lower() not in _VALID_ORDER_TYPES:
        typer.echo(f"Error: Order type must be 'limit' or 'market', got '{order_type}'.", err=True)
        raise typer.Exit(code=1)

    try:
        price_dec = Decimal(str(price))
    except InvalidOperation:
        typer.echo(f"Error: Invalid price '{price}'.", err=True)
        raise typer.Exit(code=1) from None

    if order_type.lower() == "limit" and not (_MIN_PRICE <= price_dec <= _MAX_PRICE):
        typer.echo(
            f"Error: Limit price must be between {_MIN_PRICE} and {_MAX_PRICE}, got {price_dec}.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        size_dec = Decimal(str(amount))
    except InvalidOperation:
        typer.echo(f"Error: Invalid amount '{amount}'.", err=True)
        raise typer.Exit(code=1) from None

    if size_dec <= 0:
        typer.echo("Error: Amount must be positive.", err=True)
        raise typer.Exit(code=1)

    if no_confirm:
        typer.echo("WARNING: Confirmation disabled. Order will be placed immediately.")

    asyncio.run(
        _trade(
            condition_id=condition_id,
            side=side_upper,
            outcome=outcome.lower(),
            price=price_dec,
            size=size_dec,
            order_type=order_type.lower(),
            confirm=not no_confirm,
        )
    )


async def _trade(
    *,
    condition_id: str,
    side: str,
    outcome: str,
    price: Decimal,
    size: Decimal,
    order_type: str,
    confirm: bool,
) -> None:
    """Execute the trade workflow asynchronously.

    Fetch market data, display a preview, optionally confirm, and submit.

    Args:
        condition_id: Market condition ID.
        side: Normalized side (``BUY`` or ``SELL``).
        outcome: Outcome label (``yes`` or ``no``).
        price: Limit price.
        size: Number of shares.
        order_type: ``limit`` or ``market``.
        confirm: Whether to prompt for confirmation.

    """
    client = _build_authenticated_client()
    try:
        async with client:
            # Fetch market info
            market = await client.get_market(condition_id)
            token_id = _resolve_token_id(market.tokens, outcome)

            # Display market summary
            typer.echo(f"\nMarket: {market.question}")
            typer.echo(f"Condition ID: {market.condition_id}")
            typer.echo(f"End date: {market.end_date}")
            for token in market.tokens:
                typer.echo(f"  {token.outcome}: {token.price:.4f}")

            # Fetch and display order book
            book = await client.get_order_book(token_id)
            if book.bids or book.asks:
                typer.echo(f"\nOrder Book (spread: {book.spread:.4f}):")
                if book.bids:
                    typer.echo(f"  Best bid: {book.bids[0].price:.4f} x {book.bids[0].size:.2f}")
                if book.asks:
                    typer.echo(f"  Best ask: {book.asks[0].price:.4f} x {book.asks[0].size:.2f}")

            # Display order preview
            estimated_cost = price * size if order_type == "limit" else size
            typer.echo("\n--- Order Preview ---")
            typer.echo(f"Type: {order_type.upper()}")
            typer.echo(f"Side: {side}")
            typer.echo(f"Outcome: {outcome.upper()}")
            typer.echo(f"Token: {token_id[:20]}...")
            if order_type == "limit":
                typer.echo(f"Price: {price:.4f}")
            typer.echo(f"Size: {size:.2f} shares")
            typer.echo(f"Estimated cost: ${estimated_cost:.2f}")

            # Confirm
            if confirm:
                proceed = typer.confirm("\nPlace this order?")
                if not proceed:
                    typer.echo("Order cancelled.")
                    raise typer.Exit(code=0)

            # Place order
            request = OrderRequest(
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                order_type=order_type,
            )
            result = await client.place_order(request)

            typer.echo("\n--- Order Result ---")
            typer.echo(f"Order ID: {result.order_id}")
            typer.echo(f"Status: {result.status}")
            typer.echo(f"Filled: {result.filled:.2f} / {result.size:.2f}")
    except PolymarketAPIError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def balance() -> None:
    """Display the USDC balance and allowance for the authenticated wallet."""
    asyncio.run(_balance())


async def _balance() -> None:
    """Fetch and display the USDC balance."""
    client = _build_authenticated_client()
    try:
        async with client:
            bal = await client.get_balance("COLLATERAL")
    except PolymarketAPIError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"\nUSDC Balance: {bal.balance}")
    typer.echo(f"Allowance:    {bal.allowance}")


def orders() -> None:
    """List all open orders for the authenticated wallet."""
    asyncio.run(_orders())


async def _orders() -> None:
    """Fetch and display open orders."""
    client = _build_authenticated_client()
    try:
        async with client:
            open_orders = await client.get_open_orders()
    except PolymarketAPIError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not open_orders:
        typer.echo("\nNo open orders.")
        return

    typer.echo(f"\nOpen Orders ({len(open_orders)}):")
    typer.echo(f"{'ID':<40} {'Side':<6} {'Price':>8} {'Size':>10} {'Filled':>10} {'Status':<10}")
    typer.echo("-" * 86)
    for order in open_orders:
        typer.echo(
            f"{order.order_id[:40]:<40} {order.side:<6} "
            f"{order.price:>8.4f} {order.size:>10.2f} "
            f"{order.filled:>10.2f} {order.status:<10}"
        )


def cancel(
    order_id: Annotated[str, typer.Option(help="ID of the order to cancel")],
) -> None:
    """Cancel an open order by its ID.

    Args:
        order_id: Identifier of the order to cancel.

    """
    asyncio.run(_cancel(order_id=order_id))


async def _cancel(*, order_id: str) -> None:
    """Cancel an order and display the result.

    Args:
        order_id: Identifier of the order to cancel.

    """
    client = _build_authenticated_client()
    try:
        async with client:
            result = await client.cancel_order(order_id)
    except PolymarketAPIError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"\nOrder cancelled: {order_id}")
    typer.echo(f"Result: {result}")


_REDEEM_SELL_PRICE = Decimal("0.99")
_MIN_REDEEM_SIZE = Decimal(5)


def redeem(
    no_confirm: Annotated[  # noqa: FBT002
        bool, typer.Option("--no-confirm", help="Skip confirmation prompt")
    ] = False,
) -> None:
    """Discover and redeem all winning positions via CLOB SELL at 0.99.

    Query the Polymarket Data API for redeemable positions held by the
    proxy wallet, then sell each at 0.99 to recover USDC collateral.
    Require ``POLYMARKET_FUNDER_ADDRESS`` to discover positions.

    Args:
        no_confirm: Skip the confirmation prompt.

    """
    asyncio.run(_redeem(confirm=not no_confirm))


async def _redeem(*, confirm: bool) -> None:
    """Discover and redeem positions asynchronously.

    Args:
        confirm: Whether to prompt for confirmation before selling.

    """
    client = _build_authenticated_client()
    try:
        async with client:
            positions = await client.get_redeemable_positions()

            if not positions:
                typer.echo("\nNo redeemable positions found.")
                return

            typer.echo(f"\nFound {len(positions)} redeemable position(s):")
            for pos in positions:
                typer.echo(f"  {pos.title} ({pos.outcome}): {pos.size} tokens")

            # Filter out positions below minimum order size
            eligible = [p for p in positions if p.size >= _MIN_REDEEM_SIZE]
            skipped = len(positions) - len(eligible)
            if skipped > 0:
                typer.echo(
                    f"\n  Skipping {skipped} position(s) below minimum size ({_MIN_REDEEM_SIZE})"
                )

            if not eligible:
                typer.echo("\nNo positions large enough to redeem.")
                return

            typer.echo(f"\nWill sell {len(eligible)} position(s) at {_REDEEM_SELL_PRICE}:")
            for pos in eligible:
                typer.echo(f"  SELL {pos.size} {pos.outcome} @ {_REDEEM_SELL_PRICE} — {pos.title}")

            if confirm:
                proceed = typer.confirm("\nProceed with redemption?")
                if not proceed:
                    typer.echo("Cancelled.")
                    raise typer.Exit(code=0)

            redeemed = 0
            for pos in eligible:
                request = OrderRequest(
                    token_id=pos.token_id,
                    side=_SIDE_SELL,
                    price=_REDEEM_SELL_PRICE,
                    size=pos.size,
                    order_type="limit",
                )
                result = await client.place_order(request)
                redeemed += 1
                typer.echo(
                    f"  Sold {pos.outcome} @ {_REDEEM_SELL_PRICE}: "
                    f"order={result.order_id[:20]}... status={result.status}"
                )

            typer.echo(f"\nRedeemed {redeemed}/{len(eligible)} positions.")

    except PolymarketAPIError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
