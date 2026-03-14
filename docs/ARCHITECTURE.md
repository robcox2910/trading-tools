# Trading Tools Architecture

## Project Structure

The project follows a clean, modular architecture designed for scalability and maintainability.

```
trading-tools/
├── src/trading_tools/
│   ├── apps/                        # Runnable applications
│   │   ├── fetcher/                 # Historical candle data fetcher
│   │   │   └── run.py               # CLI entry point
│   │   ├── backtester/              # Candle-based backtesting engine
│   │   │   ├── cli/                 # CLI commands (run, compare, monte-carlo, walk-forward)
│   │   │   ├── strategies/          # 10 pluggable trading strategies
│   │   │   ├── engine.py            # Backtest orchestration
│   │   │   ├── portfolio.py         # Portfolio state tracking
│   │   │   ├── metrics.py           # Performance metrics (Sharpe, drawdown, etc.)
│   │   │   ├── compare.py           # Multi-strategy comparison
│   │   │   ├── monte_carlo.py       # Monte Carlo simulation
│   │   │   ├── walk_forward.py      # Walk-forward optimisation
│   │   │   └── run.py               # CLI entry point
│   │   ├── polymarket/              # Polymarket CLI application
│   │   │   ├── cli/                 # CLI commands (trade, bot, whale, tick, backtest)
│   │   │   ├── backtest_common.py   # Shared backtest utilities
│   │   │   ├── grid_backtest.py     # Grid search engine
│   │   │   └── run.py               # CLI entry point
│   │   ├── polymarket_bot/          # Paper and live trading bot engines
│   │   │   ├── strategies/          # 5 Polymarket-specific strategies
│   │   │   ├── bot_engine.py        # Paper trading engine
│   │   │   └── live_engine.py       # Live trading engine
│   │   ├── tick_collector/          # Real-time WebSocket tick streaming
│   │   │   ├── collector.py         # WebSocket consumer and DB writer
│   │   │   └── models.py            # Tick and order book SQLAlchemy models
│   │   ├── whale_monitor/           # Whale trade monitoring service
│   │   │   ├── monitor.py           # Polling service
│   │   │   ├── models.py            # Whale and trade SQLAlchemy models
│   │   │   ├── repository.py        # Async SQLAlchemy repository for whales and trades
│   │   │   ├── analyser.py          # Aggregate trades into WhaleAnalysis / MarketBreakdown
│   │   │   ├── correlator.py        # Cross-reference whale bets with Binance spot price direction
│   │   │   ├── enricher.py          # Enrich trades with Gamma market metadata and P&L
│   │   │   ├── leaderboard.py       # Discover profitable traders via leaderboard or market enumeration
│   │   │   ├── collector.py         # Shared trade-fetching utilities
│   │   │   └── config.py            # Whale monitor configuration dataclasses
│   │   └── whale_copy_trader/       # Real-time whale copy-trading service
│   │       ├── config.py            # WhaleCopyConfig (frozen dataclass)
│   │       ├── models.py            # CopySignal, SideLeg, OpenPosition, CopyResult
│   │       ├── signal_detector.py   # Incremental polling and signal detection
│   │       └── copy_trader.py       # Dual-side spread copy-trading engine
│   ├── clients/                     # External API clients
│   │   ├── revolut_x/               # Revolut X API client
│   │   │   ├── auth/                # Ed25519 authentication
│   │   │   ├── models/              # Request/response models
│   │   │   └── endpoints/           # API endpoint implementations
│   │   ├── polymarket/              # Polymarket CLOB API client
│   │   │   └── client.py            # Order placement, book queries, redemption
│   │   └── binance/                 # Binance API client
│   │       └── client.py            # Public candle data fetching
│   ├── core/                        # Core utilities and shared code
│   │   ├── config.py                # YAML configuration loader with env var substitution
│   │   ├── models.py                # Candle, Signal, Trade, Position, BacktestResult
│   │   ├── protocols.py             # CandleProvider, TradingStrategy protocols
│   │   └── timestamps.py            # Timestamp parsing and conversion utilities
│   ├── data/                        # Data providers
│   │   └── providers/               # Pluggable candle data sources
│   │       ├── csv_provider.py      # Offline CSV candle provider
│   │       ├── revolut_x.py         # Revolut X API candle provider
│   │       └── binance.py           # Binance API candle provider
│   └── config/                      # Configuration files (YAML)
│       ├── settings.yaml            # Base configuration (committed)
│       └── settings.local.yaml      # Local overrides (gitignored)
├── tests/                           # Test suite (mirrors src structure)
│   ├── apps/                        # Application tests
│   ├── clients/                     # Client tests
│   ├── core/                        # Core model/protocol tests
│   └── data/                        # Data provider tests
├── docs/                            # Documentation
│   ├── ARCHITECTURE.md              # This file
│   ├── GETTING_STARTED.md           # Setup and authentication
│   ├── BACKTESTER.md                # Backtester reference
│   ├── POLYMARKET.md                # Polymarket CLI reference
│   ├── CLIENTS.md                   # Client module public method reference
│   └── HTTP_CLIENT_USAGE.md         # Revolut X HTTP client API
├── infra/                           # Terraform infrastructure (AWS)
│   ├── main.tf                      # EC2, RDS, security groups, CloudWatch
│   ├── variables.tf                 # Input variables
│   └── terraform.tfvars             # Variable values (gitignored)
├── .github/workflows/               # CI/CD pipelines
├── .env.example                     # Environment variable template
├── pyproject.toml                    # Project config, ruff, pytest, pyright
├── CLAUDE.md                        # Code quality conventions
├── CONTRIBUTING.md                   # Developer workflow and PR process
└── README.md                        # Project overview
```

## Application Entry Points

Three CLI applications are registered in `pyproject.toml`:

| Entry Point | Module | Description |
|-------------|--------|-------------|
| `trading-tools-fetch` | `apps.fetcher.run` | Fetch historical candle data to CSV |
| `trading-tools-backtest` | `apps.backtester.run` | Backtest trading strategies |
| `trading-tools-polymarket` | `apps.polymarket.run` | Polymarket trading, bots, and monitoring |

Each application uses [Typer](https://typer.tiangolo.com/) for CLI argument parsing. Commands are defined in `cli/` subdirectories and registered on the main app in `run.py`.

## Module Responsibilities

### `/apps` — Applications

Runnable applications and long-lived services. Each application has:
- `run.py` — Typer app and entry point
- `cli/` — Command definitions (one file per command or command group)
- Application-specific logic (engines, models, strategies)

**Applications:**

| App | Purpose |
|-----|---------|
| `fetcher` | Download historical OHLCV data from Revolut X or Binance |
| `backtester` | Run strategies against candle data, compare, simulate, and optimise |
| `polymarket` | CLI for market queries, trading, bots, tick collection, and whale monitoring |
| `polymarket_bot` | Paper and live trading engines with fee/slippage modelling and loss limits (consumed by `polymarket` CLI) |
| `tick_collector` | WebSocket tick streaming to SQLite or PostgreSQL |
| `whale_monitor` | Polling service that tracks whale trades, with analysis, per-market breakdown, trade enrichment, and Binance spot correlation |
| `whale_copy_trader` | Dual-side spread whale copy-trading (paper and live) |

### `/clients` — API Clients

Clients for external services and APIs. Each client includes:
- Authentication and authorisation
- Request/response handling
- Error handling and retries

| Client | Purpose |
|--------|---------|
| `revolut_x` | Revolut X API — Ed25519-authenticated HTTP client for candles, orders, and account data |
| `polymarket` | Polymarket CLOB API — order placement, order book queries, balance, and on-chain redemption |
| `binance` | Binance API — public candle data fetching (no authentication required) |

### `/core` — Core Utilities

Shared utilities, models, and protocols:

| Module | Purpose |
|--------|---------|
| `config.py` | YAML-based configuration loader with `${ENV_VAR:default}` substitution |
| `models.py` | Domain models: `Candle`, `Signal`, `Trade`, `Position`, `BacktestResult`, `Side`, `Interval` |
| `protocols.py` | Structural protocols: `CandleProvider`, `TradingStrategy` |
| `timestamps.py` | Timestamp parsing (ISO 8601, Unix seconds/milliseconds) and conversion |

### `/data` — Data Layer

Data providers implement the `CandleProvider` protocol for pluggable data sources:

| Provider | Source | Auth Required |
|----------|--------|--------------|
| `csv_provider.py` | Local CSV files | No |
| `revolut_x.py` | Revolut X API | Yes |
| `binance.py` | Binance API | No |

### `/config` — Configuration Files

YAML configuration files with environment variable substitution (`${VAR_NAME:default}`):

- `settings.yaml` — Base configuration (committed to version control)
- `settings.local.yaml` — Local overrides (gitignored)
- Dot-notation access: `config.get("revolut_x.api_key")`

See [Getting Started](GETTING_STARTED.md) for full configuration and authentication setup.

## Backtester Strategies

Ten built-in strategies for candle-based backtesting:

| Strategy | Type | Key Parameters |
|----------|------|----------------|
| `sma_crossover` | Trend following | `short-period`, `long-period` |
| `ema_crossover` | Trend following | `short-period`, `long-period` |
| `rsi` | Momentum | `period`, `overbought`, `oversold` |
| `macd` | Momentum | `fast-period`, `slow-period`, `signal-period` |
| `bollinger` | Mean reversion | `period`, `num-std` |
| `stochastic` | Momentum | `k-period`, `d-period`, `overbought`, `oversold` |
| `vwap` | Volume-weighted | `period` |
| `donchian` | Breakout | `period` |
| `mean_reversion` | Mean reversion | `period`, `z-threshold` |
| `buy_and_hold` | Benchmark | (none) |

## Polymarket Bot Strategies

Five strategies for Polymarket prediction market trading:

| Strategy | Description |
|----------|-------------|
| `pm_mean_reversion` | Trade deviations from rolling mean price |
| `pm_market_making` | Place symmetric bid/ask spreads |
| `pm_liquidity_imbalance` | Exploit order book imbalances |
| `pm_cross_market_arb` | Arbitrage mispricing across correlated markets |
| `pm_late_snipe` | Snipe high-confidence outcomes near market close |

## Design Principles

### 1. DRY (Don't Repeat Yourself)
- Extract shared logic into core utilities
- Use configuration files instead of duplicating values
- Single source of truth for all business rules and constants

### 2. SOLID Principles
- **Single Responsibility**: Each module/class has one well-defined purpose
- **Open/Closed**: Extend behaviour through new modules, not modifying existing ones
- **Liskov Substitution**: Subtypes must be substitutable for their base types
- **Interface Segregation**: Small, focused interfaces over large general-purpose ones
- **Dependency Inversion**: Depend on abstractions, not concrete implementations

### 3. Separation of Concerns
- **apps** — What to run
- **clients** — How to communicate with external services
- **core** — Shared functionality
- **data** — How to retrieve market data

### 4. Dependency Direction
```
apps → clients → core
apps → data → core
data → clients
```

Core should never depend on apps, clients, or data.

### 5. Configuration Over Code
- Use YAML configuration files
- Environment-specific overrides via `.env` and `settings.local.yaml`
- Avoid hardcoding values

### 6. Test-Driven Development
- Write tests first (Red-Green-Refactor)
- Maintain 80%+ coverage
- Test at appropriate levels (unit, integration)

### 7. Code Quality Enforcement

All public APIs must have docstrings. The full ruff rule set enforces:

- **Security**: `S` (flake8-bandit) — no hardcoded secrets, safe subprocess usage
- **Performance**: `PERF` (Perflint) — avoid common performance anti-patterns
- **Pythonic code**: `UP` (pyupgrade), `SIM` (simplify), `FURB` (refurb), `PIE` (flake8-pie)
- **Correctness**: `PL` (Pylint subset), `B` (bugbear), `RET` (return consistency)
- **Documentation**: `D` (pydocstyle) — all public classes, methods, and functions require docstrings
- **Clean code**: `T20` (no print), `ERA` (no commented-out code), `ARG` (no unused arguments)
- **API design**: `FBT` (no boolean traps), `DTZ` (timezone-aware datetimes)
- **Testing**: `PT` (pytest best practices)

Commit validation pipeline:

- **pre-commit hooks**: ruff, ruff-format, pyright, pip-audit, actionlint
- **commit-msg hook**: commitizen (conventional commits)
- **Branch protection**: required status checks (`lint`, `test (3.14)`, `security`), linear history

## Database Architecture

The tick collector and whale monitor use SQLAlchemy with async drivers:

| Table | App | Description |
|-------|-----|-------------|
| `ticks` | tick_collector | Trade events (timestamp, token_id, price, size) |
| `order_book_snapshots` | tick_collector | Order book state at each poll interval |
| `tracked_whales` | whale_monitor | Registered whale addresses and labels |
| `whale_trades` | whale_monitor | Historical whale trade records |

**Supported databases:**

- SQLite with aiosqlite (default, zero-config)
- PostgreSQL with asyncpg (production, deployed on AWS RDS)

## Infrastructure

Production infrastructure is defined in `infra/` using Terraform:

- **EC2** (t3.medium) — runs tick collector, whale monitor, and trading bots as systemd services
- **RDS PostgreSQL** (db.t4g.micro) — persistent storage for ticks and whale data
- **CloudWatch** — log aggregation and alarms
- **Secrets Manager** — API keys and database credentials

See [Getting Started](GETTING_STARTED.md) for local development setup.

## Testing Strategy

### Test Organisation

Tests mirror the source structure:

```
tests/
├── apps/
│   ├── backtester/
│   ├── polymarket/
│   ├── polymarket_bot/
│   ├── tick_collector/
│   ├── whale_monitor/
│   └── whale_copy_trader/
├── clients/
│   ├── revolut_x/
│   ├── polymarket/
│   └── binance/
├── core/
└── data/
```

### Test Types

1. **Unit Tests** — Test individual functions/classes in isolation
2. **Integration Tests** — Test component interactions
3. **End-to-End Tests** — Test complete CLI workflows

### Fixtures

Use pytest fixtures for common setup. Async tests use `pytest-asyncio`.
