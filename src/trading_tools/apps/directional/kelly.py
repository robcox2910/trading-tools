"""Kelly criterion sizing for directional binary bets.

Compute the optimal fraction of capital to wager on a binary outcome
token given the estimated win probability and the token's market price.
In a binary market where winning tokens pay $1.00, the Kelly formula
simplifies to: ``f* = (p - price) / (1 - price)``.

Apply fractional Kelly (default half-Kelly) and a maximum position cap
to manage variance.
"""

from decimal import Decimal

from trading_tools.core.models import ONE, ZERO


def kelly_fraction(
    p_win: Decimal,
    token_price: Decimal,
    fractional: Decimal = Decimal("0.5"),
    max_fraction: Decimal = Decimal("0.15"),
) -> Decimal:
    """Compute fractional Kelly bet size for a binary outcome token.

    In a binary market paying $1.00 for winning tokens, the full Kelly
    fraction is ``(p_win - price) / (1 - price)``.  This is then
    multiplied by ``fractional`` (e.g. 0.5 for half-Kelly) and capped
    at ``max_fraction``.

    Return zero when the edge is non-positive (no bet), the price is
    at or above $1.00 (no upside), or the probability is out of bounds.

    Args:
        p_win: Estimated probability of this token winning (0 < p < 1).
        token_price: Current market price of the token (0 < price < 1).
        fractional: Kelly fraction multiplier (default 0.5 = half-Kelly).
        max_fraction: Maximum allowed fraction of capital (default 0.15).

    Returns:
        Optimal fraction of capital to bet, in ``[0, max_fraction]``.

    """
    if p_win <= ZERO or p_win >= ONE:
        return ZERO
    if token_price <= ZERO or token_price >= ONE:
        return ZERO

    edge = p_win - token_price
    if edge <= ZERO:
        return ZERO

    full_kelly = edge / (ONE - token_price)
    sized = full_kelly * fractional
    return min(sized, max_fraction)
