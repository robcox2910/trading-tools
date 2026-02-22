"""Kelly criterion position sizer for binary outcome markets.

Provide a pure function that computes the optimal fraction of bankroll to
wager on a binary bet, scaled by a fractional multiplier. Quarter-Kelly
(0.25) is the default because full Kelly assumes perfect probability
estimates — unrealistic in practice.
"""

from decimal import Decimal

from trading_tools.core.models import ONE, ZERO

_DEFAULT_FRACTIONAL = Decimal("0.25")


def kelly_fraction(
    estimated_prob: Decimal,
    market_price: Decimal,
    *,
    fractional: Decimal = _DEFAULT_FRACTIONAL,
) -> Decimal:
    """Return the recommended fraction of bankroll to wager.

    Compute the Kelly criterion for a binary bet where the payoff is
    ``1 / market_price - 1`` for a winning wager:

        kelly = (estimated_prob - market_price) / (1 - market_price)

    Scale the result by ``fractional`` to reduce variance (quarter-Kelly
    by default). Return ``ZERO`` when there is no positive edge.

    Args:
        estimated_prob: Estimated true probability of the YES outcome (0-1).
        market_price: Current market price of the YES token (0-1).
        fractional: Kelly fraction multiplier (e.g. 0.25 for quarter-Kelly).

    Returns:
        Fraction of bankroll to wager, between 0 and ``fractional``.

    """
    if market_price >= ONE:
        return ZERO
    edge = estimated_prob - market_price
    if edge <= ZERO:
        return ZERO
    full_kelly = edge / (ONE - market_price)
    return full_kelly * fractional
