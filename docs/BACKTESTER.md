# Backtester

The backtester runs trading strategies against historical candle data to evaluate performance before risking real capital. It uses a protocol-based design, so you can plug in custom strategies and data providers without modifying the engine.

## Key Concepts

| Concept | Description |
|---------|-------------|
| **CandleProvider** | Protocol that supplies OHLCV candle data (CSV file, API, etc.) |
| **TradingStrategy** | Protocol that receives candles and emits buy/sell signals |
| **BacktestEngine** | Orchestrator that feeds candles to a strategy and tracks results |
| **Portfolio** | Manages capital, positions, and completed trades during a run |

## CSV File Format

The CSV provider expects a header row followed by one row per candle:

```csv
symbol,timestamp,open,high,low,close,volume,interval
BTC-USD,1609459200,29000.50,29500.00,28800.00,29300.75,1250.5,1h
BTC-USD,1609462800,29300.75,29800.00,29100.00,29650.00,980.3,1h
BTC-USD,1609466400,29650.00,30100.00,29400.00,29900.25,1100.0,1h
```

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | string | Trading pair (e.g. `BTC-USD`, `ETH-USD`) |
| `timestamp` | int | Unix timestamp in seconds |
| `open` | decimal | Opening price |
| `high` | decimal | Highest price in the interval |
| `low` | decimal | Lowest price in the interval |
| `close` | decimal | Closing price |
| `volume` | decimal | Trading volume |
| `interval` | string | Candle interval: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w` |

Rows are filtered by `symbol`, `interval`, and timestamp range at load time.

## CLI Usage

```bash
python -m trading_tools.apps.backtester.run --csv <path> [OPTIONS]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--csv` | path | *(required)* | Path to CSV candle data file |
| `--symbol` | string | `BTC-USD` | Trading pair symbol |
| `--interval` | string | config or `1h` | Candle interval |
| `--capital` | decimal | config or `10000` | Initial capital |
| `--strategy` | string | `sma_crossover` | Strategy to use |
| `--short-period` | int | `10` | SMA short window (sma_crossover only) |
| `--long-period` | int | `20` | SMA long window (sma_crossover only) |
| `--start` | int | `0` | Start Unix timestamp |
| `--end` | int | `2^53` | End Unix timestamp |

`--interval` and `--capital` fall back to values in `config/settings.yaml` under the `backtester` key when not provided on the command line.

## Writing a Custom Strategy

Implement the `TradingStrategy` protocol from `trading_tools.core.protocols`:

```python
from decimal import Decimal

from trading_tools.core.models import Candle, Signal, Side


class MyStrategy:
    """Example: buy when price drops 5%, sell when it rises 5%."""

    @property
    def name(self) -> str:
        return "my_strategy"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        if not history:
            return None

        prev = history[-1]
        change = (candle.close - prev.close) / prev.close

        if change <= Decimal("-0.05"):
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=Decimal("1"),
                reason="Price dropped 5%",
            )
        if change >= Decimal("0.05"):
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=Decimal("1"),
                reason="Price rose 5%",
            )
        return None
```

**Rules:**
- `on_candle` receives the current candle and all previously seen candles in `history`.
- Return `None` for no action, or a `Signal` with `Side.BUY` or `Side.SELL`.
- `strength` must be between 0 and 1 (inclusive).
- The portfolio holds at most one position at a time. BUY opens; SELL closes.

To register the strategy with the CLI, add it to the `_STRATEGIES` dict in `apps/backtester/run.py`.

## Writing a Custom Provider

Implement the `CandleProvider` protocol from `trading_tools.core.protocols`:

```python
from trading_tools.core.models import Candle, Interval


class MyProvider:
    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        # Fetch or generate candles here
        ...
```

The method must be async and return a list of `Candle` objects filtered by the given parameters.

## Metrics

After a run completes, the engine calculates these metrics:

| Metric | Description |
|--------|-------------|
| `total_return` | `(final_capital - initial_capital) / initial_capital` as a decimal (0.20 = 20%) |
| `win_rate` | Fraction of trades with positive PnL (0.0 to 1.0) |
| `profit_factor` | Gross profit / gross loss. Returns 0 when there are no losing trades |
| `max_drawdown` | Largest peak-to-trough decline as a fraction of the peak equity |
| `sharpe_ratio` | Mean trade return / standard deviation of trade returns (risk-free rate = 0) |
| `total_trades` | Number of completed round-trip trades |

## Example Run

```bash
python -m trading_tools.apps.backtester.run \
  --csv data/btc_1h.csv \
  --symbol BTC-USD \
  --interval 1h \
  --capital 10000 \
  --strategy sma_crossover \
  --short-period 10 \
  --long-period 50
```

Sample output:

```
==================================================
Strategy:        sma_crossover_10_50
Symbol:          BTC-USD
Interval:        1h
Initial Capital: 10000
Final Capital:   11480.32

Trades:          7

              --- Metrics ---
  total_return        : 0.148032
  win_rate            : 0.571429
  profit_factor       : 2.340000
  max_drawdown        : 0.083200
  sharpe_ratio        : 0.920000
  total_trades        : 7.000000
==================================================
```

## How the Engine Works

1. Loads all matching candles from the provider.
2. Iterates through candles in order, calling `strategy.on_candle()` for each.
3. When a BUY signal fires and no position is open, the portfolio goes all-in at the candle's close price.
4. When a SELL signal fires and a position is open, the portfolio closes it at the candle's close price.
5. Any position still open after the last candle is force-closed.
6. Metrics are calculated from the completed trades and returned in a `BacktestResult`.
