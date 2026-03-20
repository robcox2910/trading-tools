# Trading Tools

A suite of cryptocurrency trading tools for fetching market data, backtesting strategies, and trading on prediction markets. Supports **Revolut X**, **Binance**, and **Polymarket** via three dedicated CLI applications.

## Features

- **Data Fetching** — Download historical OHLCV candle data from Revolut X or Binance
- **Backtesting** — Test 10 built-in trading strategies against historical data with full risk management
- **Strategy Comparison** — Rank all strategies side-by-side on the same dataset
- **Monte Carlo Simulation** — Stress-test strategy robustness with randomised trade ordering
- **Walk-Forward Optimisation** — Rolling out-of-sample validation across time windows
- **Polymarket Trading** — Browse prediction markets, place trades, and manage orders
- **Live Trading Bot** — Automated paper and live trading with configurable strategies
- **Tick Collection** — Real-time WebSocket tick streaming to PostgreSQL or SQLite
- **Whale Monitoring** — Track and analyse large Polymarket traders
- **Interactive Charts** — Plotly-powered equity curves and strategy comparisons

## Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) package manager

## Quick Start

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/robcox2910/trading-tools.git
cd trading-tools
uv sync --all-extras

# Copy environment template and add your API keys
cp .env.example .env

# Install pre-commit hooks (required for contributing)
uv run pre-commit install

# Run tests to verify everything works
uv run pytest
```

See [Getting Started](docs/GETTING_STARTED.md) for detailed setup including API key generation and authentication.

## CLI Tools

The project provides three CLI entry points:

### `trading-tools-fetch` — Fetch Historical Data

Download OHLCV candle data from Revolut X or Binance to CSV.

```bash
# Fetch BTC-USD hourly candles for January 2025
trading-tools-fetch --symbol BTC-USD --interval 1h --start 2025-01-01 --end 2025-02-01

# Fetch from Binance instead of Revolut X
trading-tools-fetch --source binance --symbol ETH-USD --interval 4h --start 2025-01-01

# Custom output path
trading-tools-fetch --start 2025-01-01 --output data/btc_hourly.csv
```

### `trading-tools-backtest` — Backtest Strategies

Run trading strategies against historical candle data.

```bash
# Run SMA crossover on a CSV file
trading-tools-backtest run --csv data/btc_1h.csv --strategy sma_crossover

# Compare all 10 strategies side-by-side
trading-tools-backtest compare --csv data/btc_1h.csv --sort-by sharpe_ratio

# Monte Carlo simulation (1000 shuffles)
trading-tools-backtest monte-carlo --csv data/btc_1h.csv --strategy rsi --shuffles 1000

# Walk-forward optimisation
trading-tools-backtest walk-forward --csv data/btc_1h.csv --train-window 200 --test-window 50

# With risk management
trading-tools-backtest run --csv data/btc_1h.csv --strategy bollinger \
  --stop-loss 0.05 --take-profit 0.10 --maker-fee 0.001 --slippage 0.0005

# Generate interactive charts
trading-tools-backtest run --csv data/btc_1h.csv --strategy macd --chart
```

**Available strategies:** `sma_crossover`, `ema_crossover`, `rsi`, `macd`, `bollinger`, `stochastic`, `vwap`, `donchian`, `mean_reversion`, `buy_and_hold`

See [Backtester](docs/BACKTESTER.md) for all options, strategy parameters, and risk management flags.

### `trading-tools-polymarket` — Polymarket Trading

Browse prediction markets, trade, run bots, and monitor whales.

```bash
# Search for markets (no auth required)
trading-tools-polymarket markets --keyword "Bitcoin"
trading-tools-polymarket odds <condition_id>
trading-tools-polymarket book <token_id>

# Place a trade (requires Polymarket credentials)
trading-tools-polymarket trade --condition-id <id> --side buy --outcome yes --amount 10

# Run paper trading bot
trading-tools-polymarket bot --strategy pm_mean_reversion --series btc-updown-5m

# Run live trading bot (requires --confirm-live flag)
trading-tools-polymarket bot-live --strategy pm_late_snipe --series btc-updown-5m --confirm-live

# Collect real-time tick data
trading-tools-polymarket tick-collect --series btc-updown-5m

# Monitor whale traders
trading-tools-polymarket whale-add --address 0x... --label "BigTrader"
trading-tools-polymarket whale-monitor --verbose
trading-tools-polymarket whale-analyse --address 0x... --days 7
trading-tools-polymarket whale-correlate --address 0x... --days 1

# Spread capture bot (paper mode)
trading-tools-polymarket spread-capture --series-slugs btc-updown-5m -v

# Spread capture bot with real orders
trading-tools-polymarket spread-capture --series-slugs btc-updown-5m --confirm-live
```

See [Polymarket](docs/POLYMARKET.md) for all commands, options, and setup instructions.

## Development

```bash
# Run tests with coverage
uv run pytest

# Run specific test file
uv run pytest tests/apps/backtester/test_engine.py -v

# Lint and format
uv run ruff check .          # Check for issues
uv run ruff check --fix .    # Auto-fix issues
uv run ruff format .         # Format code

# Type check
uv run pyright src tests

# Run all checks
uv run ruff check . && uv run ruff format --check . && uv run pyright src tests
```

## CI/CD

GitHub Actions runs on every push and pull request:

- **Lint** — ruff check and format verification
- **Test** — pytest with 80% minimum coverage
- **Security** — pip-audit dependency scanning

All code follows TDD (Red-Green-Refactor) and must adhere to DRY and SOLID principles.

## Documentation

- **[Getting Started](docs/GETTING_STARTED.md)** — Installation, API key setup, and authentication
- **[Backtester](docs/BACKTESTER.md)** — Strategies, CLI flags, risk management, and custom strategies
- **[Polymarket](docs/POLYMARKET.md)** — Market queries, trading, bots, tick collection, and whale monitoring
- **[Architecture](docs/ARCHITECTURE.md)** — Project structure, design principles, and module responsibilities
- **[Strategy Guide](docs/STRATEGY_GUIDE.md)** — How to implement and integrate a new trading strategy
- **[HTTP Client Usage](docs/HTTP_CLIENT_USAGE.md)** — Revolut X HTTP client API reference
- **[Clients](docs/CLIENTS.md)** — Client module reference for all external API integrations
- **[Contributing](CONTRIBUTING.md)** — Developer workflow, code standards, and PR process

## License

Add your license here
