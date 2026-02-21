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
