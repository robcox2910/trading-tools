"""Typed data models for Polymarket prediction market data.

Provide frozen dataclasses that insulate the rest of the codebase from the
untyped dictionaries returned by ``py-clob-client`` and the Gamma API.
All monetary values use ``Decimal`` for precision.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class OrderLevel:
    """Single price level in an order book.

    Represent one bid or ask entry with its price and available size.

    Args:
        price: Price of the level as a decimal between 0 and 1.
        size: Available quantity at this price level.

    """

    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class OrderBook:
    """Typed order book snapshot for a Polymarket token.

    Contain the full bid/ask ladder along with computed spread and midpoint.

    Args:
        token_id: CLOB token identifier for the market outcome.
        bids: Price levels on the buy side, ordered best-to-worst.
        asks: Price levels on the sell side, ordered best-to-worst.
        spread: Difference between best ask and best bid.
        midpoint: Average of best bid and best ask prices.

    """

    token_id: str
    bids: tuple[OrderLevel, ...]
    asks: tuple[OrderLevel, ...]
    spread: Decimal
    midpoint: Decimal


@dataclass(frozen=True)
class MarketToken:
    """Represent a YES or NO outcome token in a prediction market.

    Args:
        token_id: CLOB token identifier.
        outcome: Human-readable outcome label (e.g. "Yes" or "No").
        price: Current price between 0 and 1, reflecting implied probability.

    """

    token_id: str
    outcome: str
    price: Decimal


@dataclass(frozen=True)
class Market:
    """Typed representation of a Polymarket prediction market.

    Aggregate metadata from the Gamma API with live pricing from CLOB
    into a single immutable record.

    Args:
        condition_id: Unique identifier for the market condition.
        question: The prediction question (e.g. "Will BTC reach $100K?").
        description: Detailed description of the market resolution criteria.
        tokens: Outcome tokens (typically YES and NO) with current prices.
        end_date: ISO-8601 date string when the market resolves.
        volume: Total trading volume in USD.
        liquidity: Current available liquidity in USD.
        active: Whether the market is currently open for trading.

    """

    condition_id: str
    question: str
    description: str
    tokens: tuple[MarketToken, ...]
    end_date: str
    volume: Decimal
    liquidity: Decimal
    active: bool


@dataclass(frozen=True)
class OrderRequest:
    """Typed input for placing an order on Polymarket.

    Encapsulate all parameters needed to submit a limit or market order
    to the CLOB API.

    Args:
        token_id: CLOB token identifier for the outcome to trade.
        side: Order side -- ``"BUY"`` or ``"SELL"``.
        price: Limit price between 0 and 1 (ignored for market orders).
        size: Number of shares to trade.
        order_type: ``"limit"`` for GTC limit orders, ``"market"`` for FOK.

    """

    token_id: str
    side: str
    price: Decimal
    size: Decimal
    order_type: str


@dataclass(frozen=True)
class OrderResponse:
    """Typed result from submitting an order to the CLOB API.

    Carry the essential fields from the API response so callers can
    display confirmation and track fill status.

    Args:
        order_id: Unique identifier assigned by the CLOB.
        status: Order status (e.g. ``"live"``, ``"matched"``, ``"cancelled"``).
        token_id: CLOB token identifier that was traded.
        side: Order side -- ``"BUY"`` or ``"SELL"``.
        price: Submitted price.
        size: Submitted size in shares.
        filled: Number of shares already filled.

    """

    order_id: str
    status: str
    token_id: str
    side: str
    price: Decimal
    size: Decimal
    filled: Decimal


@dataclass(frozen=True)
class Balance:
    """Typed balance and allowance information for a Polymarket asset.

    Represent the on-chain balance and contract allowance for either
    USDC collateral or a conditional outcome token.

    Args:
        asset_type: ``"COLLATERAL"`` for USDC or ``"CONDITIONAL"`` for tokens.
        balance: Current balance in the asset's native units.
        allowance: Approved spending allowance for the exchange contract.

    """

    asset_type: str
    balance: Decimal
    allowance: Decimal


@dataclass(frozen=True)
class RedeemablePosition:
    """A resolved position that can be redeemed for USDC collateral.

    Represent a winning conditional token position discovered via the
    Polymarket Data API.  Include the token ID and size needed to place
    a SELL order on the CLOB to recover value.

    Args:
        condition_id: Market condition identifier.
        token_id: CLOB token identifier (the ``asset`` field from the Data API).
        outcome: Outcome label (e.g. ``"Up"``, ``"Down"``, ``"Yes"``, ``"No"``).
        size: Number of tokens held.
        title: Human-readable market title.

    """

    condition_id: str
    token_id: str
    outcome: str
    size: Decimal
    title: str
