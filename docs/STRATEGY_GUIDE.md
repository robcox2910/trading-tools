# Strategy Implementation Guide

How to implement and integrate a new trading strategy with the bot framework.

## Architecture Overview

The bot framework uses **composition over inheritance**. Trading engines compose shared services from `bot_framework/` and delegate signal generation to a pluggable strategy:

```
Engine (event loop + lifecycle)
  ├── Strategy (signal generation)
  ├── PositionRedeemer (CTF redemption)
  └── OrderExecutor (CLOB order placement)
```

Shared services handle the common infrastructure, so strategy authors only need to implement signal logic.

## Strategy Types

Two engine types exist, distinguished by their event source:

### WebSocket-Driven (`BaseTradingEngine`)

For strategies that react to real-time price feeds. Used by:
- Late snipe (`pm_late_snipe`)
- Mean reversion (`pm_mean_reversion`)
- Market making (`pm_market_making`)
- Liquidity imbalance (`pm_liquidity_imbalance`)
- Cross-market arbitrage (`pm_cross_market_arb`)

### Polling-Driven (`WhaleCopyTrader`)

For strategies that react to external data sources (e.g. whale trades in a database). Used by:
- Whale copy-trade (`whale_copy`)

## Adding a WebSocket Strategy

### 1. Create the strategy module

Create `src/trading_tools/apps/polymarket_bot/strategies/my_strategy.py`:

```python
"""My custom prediction market strategy."""

from dataclasses import dataclass
from decimal import Decimal

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.core.models import Signal


@dataclass
class PMMyStrategy:
    """Implement the strategy logic.

    Attributes:
        threshold: Minimum signal strength to trigger a trade.

    """

    threshold: Decimal = Decimal("0.10")

    @property
    def name(self) -> str:
        """Return the strategy identifier."""
        return "pm_my_strategy"

    def on_snapshot(self, snapshot: MarketSnapshot) -> Signal | None:
        """Evaluate a market snapshot and optionally emit a signal.

        Args:
            snapshot: Current market state.

        Returns:
            A ``Signal`` if conditions are met, or ``None``.

        """
        # Your logic here
        return None
```

### 2. Implement `PredictionMarketStrategy` protocol

Your strategy must have:
- `name` property returning a string identifier
- `on_snapshot(snapshot: MarketSnapshot) -> Signal | None`

Reference `late_snipe.py` for a minimal working example.

### 3. Register in the factory

Edit `src/trading_tools/apps/polymarket_bot/strategies/strategy_factory.py`:

1. Add to `PM_STRATEGY_NAMES`:
   ```python
   PM_STRATEGY_NAMES = (..., "pm_my_strategy")
   ```

2. Add a case in `build_pm_strategy()`:
   ```python
   if name == "pm_my_strategy":
       return PMMyStrategy(threshold=threshold)
   ```

### 4. Add CLI flags (if needed)

Edit `src/trading_tools/apps/polymarket/cli/bot_cmd.py` to add custom parameters.

### 5. Write tests

Create `tests/apps/polymarket_bot/strategies/test_my_strategy.py`:
- Test `name` property returns correct string
- Test `on_snapshot()` returns `None` when conditions not met
- Test `on_snapshot()` returns correct `Signal` when conditions met
- Test edge cases (empty snapshots, extreme prices, etc.)

## Adding a Polling Strategy

### 1. Create the app module

Create a new directory under `src/trading_tools/apps/my_strategy/`:

```
my_strategy/
├── __init__.py
├── engine.py        # Polling engine
├── detector.py      # Signal detection logic
└── models.py        # Strategy-specific data models
```

### 2. Compose shared services

In your engine, compose `PositionRedeemer` and `OrderExecutor` from `bot_framework`:

```python
from trading_tools.apps.bot_framework import OrderExecutor, PositionRedeemer


@dataclass
class MyEngine:
    client: PolymarketClient
    _redeemer: PositionRedeemer | None = None
    _executor: OrderExecutor | None = None

    async def run(self) -> None:
        self._redeemer = PositionRedeemer(client=self.client)
        self._executor = OrderExecutor(
            client=self.client,
            use_market_orders=False,
        )
        # ... polling loop ...
```

### 3. Wire up a CLI command

Create `src/trading_tools/apps/polymarket/cli/my_strategy_cmd.py` and register it in the Polymarket CLI app.

### 4. Reference implementation

See `src/trading_tools/apps/whale_copy_trader/` for a complete working example of a polling strategy with dual-side spread capture.

## Shared Services Reference

### `PositionRedeemer`

Discover and redeem resolved winning positions on-chain via the CTF contract.

```python
redeemer = PositionRedeemer(client=client, min_order_size=Decimal(5))
await redeemer.redeem_if_available()  # Non-blocking, spawns background task
```

- Queries the Polymarket Data API for redeemable positions
- Filters positions below `min_order_size`
- Spawns a background `asyncio.Task` for on-chain redemption
- Cancels any in-flight task before starting a new one
- All errors are logged, never propagated

### `OrderExecutor`

Place CLOB orders with automatic request construction and error handling.

```python
executor = OrderExecutor(client=client, use_market_orders=True)
response = await executor.place_order(token_id, "BUY", price, quantity)
if response is not None:
    print(f"Order placed: {response.order_id}")
```

- Constructs `OrderRequest` with the correct order type (FOK market or GTC limit)
- Returns `OrderResponse` on success, `None` on failure
- All errors are logged, never propagated

## Testing Checklist

- [ ] Protocol conformance: strategy implements `PredictionMarketStrategy` (or equivalent)
- [ ] Signal generation: correct signals emitted for known inputs
- [ ] No signal: `None` returned when conditions are not met
- [ ] Edge cases: extreme prices, empty data, zero volumes
- [ ] Sizing: position sizes respect constraints
- [ ] Error handling: API failures handled gracefully
- [ ] Integration: engine + strategy + services work together
- [ ] Minimum 80% coverage
