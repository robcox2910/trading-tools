# Backtester

The backtester runs trading strategies against historical candle data to evaluate performance before risking real capital. It uses a protocol-based design, so you can plug in custom strategies and data providers without modifying the engine.

## Key Concepts

| Concept | Description |
|---------|-------------|
| **CandleProvider** | Protocol that supplies OHLCV candle data (CSV file, API, etc.) |
| **TradingStrategy** | Protocol that receives candles and emits buy/sell signals |
| **BacktestEngine** | Orchestrator that feeds candles to a strategy and tracks results |
| **Portfolio** | Manages capital, positions, and completed trades during a run |

## CLI Commands

The backtester provides four commands via `trading-tools-backtest`:

| Command | Description |
|---------|-------------|
| `run` | Execute a single strategy backtest |
| `compare` | Compare all 10 strategies on the same data |
| `monte-carlo` | Run a backtest then Monte Carlo simulation |
| `walk-forward` | Walk-forward optimisation across time windows |

## Data Sources

Three data sources are available via `--source`:

| Source | Flag | Auth Required | Description |
|--------|------|--------------|-------------|
| CSV | `--source csv` (default) | No | Load candles from a local CSV file |
| Revolut X | `--source revolut-x` | Yes | Fetch candles live from Revolut X API |
| Binance | `--source binance` | No | Fetch candles from Binance public API |

```bash
# CSV (default)
trading-tools-backtest run --source csv --csv data/btc_1h.csv

# Revolut X (requires credentials — see Getting Started)
trading-tools-backtest run --source revolut-x --symbol BTC-USD --start 1704067200

# Binance (public, no auth)
trading-tools-backtest run --source binance --symbol BTC-USD --interval 1h --start 1704067200
```

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

Rows are filtered by `symbol`, `interval`, and timestamp range at load time. Use `trading-tools-fetch` to generate compatible CSV files.

## Strategies

Ten built-in strategies are available:

| Strategy | Type | Description |
|----------|------|-------------|
| `sma_crossover` | Trend following | Buy when short SMA crosses above long SMA; sell on cross below |
| `ema_crossover` | Trend following | Buy when short EMA crosses above long EMA; sell on cross below |
| `rsi` | Momentum | Buy when RSI drops below oversold; sell when above overbought |
| `macd` | Momentum | Buy when MACD crosses above signal line; sell on cross below |
| `bollinger` | Mean reversion | Buy at lower band; sell at upper band |
| `stochastic` | Momentum | Buy when %K crosses above %D in oversold zone; sell in overbought |
| `vwap` | Volume-weighted | Buy below VWAP; sell above VWAP |
| `donchian` | Breakout | Buy at new high-channel breakout; sell at low-channel break |
| `mean_reversion` | Mean reversion | Buy when z-score drops below threshold; sell above |
| `buy_and_hold` | Benchmark | Buy on first candle, hold until end |

### Strategy Parameters

Each strategy uses different parameters. Only the relevant parameters are used for each strategy — others are ignored.

**Moving average strategies** (`sma_crossover`, `ema_crossover`):

| Flag | Default | Description |
|------|---------|-------------|
| `--short-period` | `10` | Short moving average period |
| `--long-period` | `20` | Long moving average period |

**RSI**:

| Flag | Default | Description |
|------|---------|-------------|
| `--period` | `14` | RSI lookback period |
| `--overbought` | `70` | Overbought threshold (sell signal) |
| `--oversold` | `30` | Oversold threshold (buy signal) |

**MACD**:

| Flag | Default | Description |
|------|---------|-------------|
| `--fast-period` | `12` | Fast EMA period |
| `--slow-period` | `26` | Slow EMA period |
| `--signal-period` | `9` | Signal EMA period |

**Bollinger Bands**:

| Flag | Default | Description |
|------|---------|-------------|
| `--period` | `14` | Bollinger Band lookback period |
| `--num-std` | `2.0` | Number of standard deviations |

**Stochastic**:

| Flag | Default | Description |
|------|---------|-------------|
| `--k-period` | `14` | Stochastic %K period |
| `--d-period` | `3` | Stochastic %D period |
| `--overbought` | `70` | Overbought threshold |
| `--oversold` | `30` | Oversold threshold |

**VWAP, Donchian**:

| Flag | Default | Description |
|------|---------|-------------|
| `--period` | `14` | Lookback period |

**Mean Reversion**:

| Flag | Default | Description |
|------|---------|-------------|
| `--period` | `14` | Rolling window period |
| `--z-threshold` | `2.0` | Z-score threshold for entry/exit |

## Commands

### `run` — Single Strategy Backtest

```bash
trading-tools-backtest run --csv data/btc_1h.csv --strategy rsi --period 14
```

**Common flags (shared by all commands):**

| Flag | Default | Description |
|------|---------|-------------|
| `--source` | `csv` | Data source: `csv`, `revolut-x`, or `binance` |
| `--csv` | *(required for csv source)* | Path to CSV candle data file |
| `--symbol` | `BTC-USD` | Trading pair symbol |
| `--interval` | config or `1h` | Candle interval (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w`) |
| `--capital` | config or `10000` | Initial capital |
| `--start` | `0` | Start Unix timestamp |
| `--end` | max int | End Unix timestamp |

**Run-specific flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--strategy` | `sma_crossover` | Strategy name (see table above) |
| `--benchmark` | `false` | Compare against buy-and-hold |
| `--symbols` | | Comma-separated symbols for multi-asset mode |
| `--chart` | `false` | Generate interactive Plotly charts |
| `--chart-output` | | Save charts to HTML file (otherwise opens in browser) |

### `compare` — Compare All Strategies

Run all 10 strategies on the same data and rank them by a chosen metric.

```bash
trading-tools-backtest compare --csv data/btc_1h.csv --sort-by sharpe_ratio
```

| Flag | Default | Description |
|------|---------|-------------|
| `--sort-by` | `total_return` | Metric to rank by |
| `--chart` | `false` | Generate comparison charts |
| `--chart-output` | | Save charts to HTML file |

### `monte-carlo` — Monte Carlo Simulation

Run a backtest then shuffle trade ordering to assess strategy robustness.

```bash
trading-tools-backtest monte-carlo --csv data/btc_1h.csv --strategy macd --shuffles 5000 --seed 42
```

| Flag | Default | Description |
|------|---------|-------------|
| `--strategy` | `sma_crossover` | Strategy to simulate |
| `--shuffles` | `1000` | Number of Monte Carlo shuffles |
| `--seed` | random | Random seed for reproducibility |
| `--chart` | `false` | Generate distribution charts |
| `--chart-output` | | Save charts to HTML file |

### `walk-forward` — Walk-Forward Optimisation

Split data into rolling train/test windows, optimise parameters on training data, and validate on test data.

```bash
trading-tools-backtest walk-forward --csv data/btc_1h.csv --train-window 200 --test-window 50
```

| Flag | Default | Description |
|------|---------|-------------|
| `--train-window` | `100` | Training window size in candles |
| `--test-window` | `50` | Test window size in candles |
| `--step` | `50` | Step size between folds |
| `--sort-metric` | `total_return` | Metric to select best strategy per fold |
| `--chart` | `false` | Generate fold charts |
| `--chart-output` | | Save charts to HTML file |

## Risk Management

All backtest commands support these risk management options:

### Transaction Costs

| Flag | Default | Description |
|------|---------|-------------|
| `--maker-fee` | `0.0` | Maker fee as decimal (e.g. `0.001` = 0.1%) |
| `--taker-fee` | `0.0` | Taker fee as decimal |
| `--slippage` | `0.0` | Slippage as decimal (e.g. `0.0005` = 0.05%) |

### Stop Loss and Take Profit

| Flag | Default | Description |
|------|---------|-------------|
| `--stop-loss` | disabled | Stop-loss threshold as decimal (e.g. `0.05` = 5% loss) |
| `--take-profit` | disabled | Take-profit threshold as decimal (e.g. `0.10` = 10% gain) |

### Position Sizing

| Flag | Default | Description |
|------|---------|-------------|
| `--position-size` | `1.0` | Fraction of capital per trade (0–1, where 1 = all-in) |
| `--volatility-sizing` | `false` | Use ATR-based position sizing |
| `--atr-period` | `14` | ATR lookback period (when volatility sizing enabled) |
| `--target-risk-pct` | `0.02` | Target risk per trade as decimal (when volatility sizing enabled) |

### Circuit Breaker

| Flag | Default | Description |
|------|---------|-------------|
| `--circuit-breaker` | disabled | Halt trading at this drawdown fraction (e.g. `0.15` = 15%) |
| `--recovery-pct` | disabled | Resume trading after this recovery fraction (e.g. `0.5` = 50% recovery) |

### Example with Full Risk Management

```bash
trading-tools-backtest run \
  --csv data/btc_1h.csv \
  --strategy bollinger \
  --period 20 \
  --num-std 2.0 \
  --maker-fee 0.001 \
  --taker-fee 0.002 \
  --slippage 0.0005 \
  --stop-loss 0.05 \
  --take-profit 0.10 \
  --position-size 0.5 \
  --circuit-breaker 0.15 \
  --recovery-pct 0.5 \
  --benchmark \
  --chart
```

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

To register the strategy with the CLI, add it to the `_STRATEGIES` dict in the strategy registry.

## Writing a Custom Provider

Implement the `CandleProvider` protocol from `trading_tools.core.protocols`:

```python
from trading_tools.core.models import Candle, Interval


class MyProvider:
    """Provide candles from a custom data source."""

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

## How the Engine Works

1. Loads all matching candles from the provider.
2. Iterates through candles in order, calling `strategy.on_candle()` for each.
3. When a BUY signal fires and no position is open, the portfolio opens a position at the candle's close price.
4. When a SELL signal fires and a position is open, the portfolio closes it at the candle's close price.
5. Stop-loss and take-profit are checked on every candle while a position is open.
6. Any position still open after the last candle is force-closed.
7. Metrics are calculated from the completed trades and returned in a `BacktestResult`.

## Example Run

```bash
trading-tools-backtest run \
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
