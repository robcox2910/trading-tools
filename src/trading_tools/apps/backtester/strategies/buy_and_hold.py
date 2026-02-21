"""Buy-and-hold benchmark strategy.

How it works:
    Emit a BUY signal on the very first candle and never sell. The
    backtest engine's ``force_close()`` at the end of the simulation
    handles the exit. This gives a passive benchmark return that any
    active strategy can be compared against.

What it tries to achieve:
    Provide a baseline. If an active strategy cannot beat buy-and-hold,
    it is not adding value over simply holding the asset.

Params:
    None — no configuration is needed.
"""

from trading_tools.core.models import ONE, Candle, Side, Signal


class BuyAndHoldStrategy:
    """Emit BUY on the first candle and hold until the backtest ends.

    The simplest possible strategy: buy once, hold forever. The
    backtest engine force-closes the position at the end of the
    simulation, so no explicit SELL signal is ever emitted.
    """

    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "buy_and_hold"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Emit a BUY signal on the first candle, None thereafter."""
        if not history:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason="Buy and hold — initial entry",
            )
        return None
