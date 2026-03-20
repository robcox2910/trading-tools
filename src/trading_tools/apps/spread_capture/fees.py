"""Fee computation utilities for the spread capture bot.

Provide the Polymarket fee formula as a standalone function so that both
the market scanner (for margin filtering) and the trader (for P&L
calculation) use a single consistent implementation.

The fee formula is: ``price * fee_rate * (price * (1 - price))^fee_exponent``.
"""

from decimal import Decimal

from trading_tools.core.models import ONE, ZERO


def compute_poly_fee(
    price: Decimal,
    fee_rate: Decimal,
    fee_exponent: int,
) -> Decimal:
    """Compute the per-token Polymarket fee for a given price.

    The fee is quantity-independent and applies to each token purchased.
    Formula: ``price * fee_rate * (price * (1 - price))^fee_exponent``.

    Args:
        price: Token price (0 < price < 1).
        fee_rate: Fee rate coefficient (e.g. ``Decimal("0.25")``).
        fee_exponent: Exponent applied to the ``price * (1 - price)`` term.

    Returns:
        Per-token fee in USDC.  Returns zero when fee_rate is zero or
        price is out of range.

    """
    if fee_rate == ZERO or price <= ZERO or price >= ONE:
        return ZERO
    return price * fee_rate * (price * (ONE - price)) ** fee_exponent
