# Polymarket

The Polymarket CLI provides tools for browsing prediction markets, placing trades, running automated trading bots, collecting real-time tick data, and monitoring whale traders.

All commands are accessed via the `trading-tools-polymarket` entry point.

## Prerequisites

Some commands require no authentication (market queries), while trading and bot commands require Polymarket credentials. See [Getting Started](GETTING_STARTED.md) for full setup instructions.

| Feature | Auth Required |
|---------|--------------|
| Market search, odds, order book | No |
| Trading, balance, orders | Yes |
| Paper trading bot | Yes |
| Live trading bot | Yes |
| Tick collection | No |
| Whale monitoring | No |

## Market Queries

These commands query public Polymarket data and require no authentication.

### `markets` — Search Prediction Markets

```bash
trading-tools-polymarket markets --keyword "Bitcoin" --limit 10
```

| Flag | Default | Description |
|------|---------|-------------|
| `--keyword` | `Bitcoin` | Search keyword for market questions |
| `--limit` | `20` | Maximum number of results |

### `odds` — Display Market Odds

```bash
trading-tools-polymarket odds <condition_id>
```

| Argument | Description |
|----------|-------------|
| `condition_id` | Unique identifier for the market condition (positional) |

### `book` — Display Order Book

```bash
trading-tools-polymarket book <token_id> --depth 20
```

| Argument/Flag | Default | Description |
|---------------|---------|-------------|
| `token_id` | *(required)* | CLOB token identifier (positional) |
| `--depth` | `10` | Number of price levels to display |

## Trading

These commands require Polymarket credentials configured in `.env`. See [Getting Started](GETTING_STARTED.md).

### `trade` — Place a Trade

```bash
# Limit order: buy 10 YES shares at $0.60
trading-tools-polymarket trade \
  --condition-id 0x1234... \
  --side buy \
  --outcome yes \
  --amount 10 \
  --price 0.60

# Market order
trading-tools-polymarket trade \
  --condition-id 0x1234... \
  --side buy \
  --outcome yes \
  --amount 10 \
  --type market

# Skip confirmation prompt
trading-tools-polymarket trade \
  --condition-id 0x1234... \
  --side sell \
  --outcome no \
  --amount 5 \
  --price 0.40 \
  --no-confirm
```

| Flag | Default | Description |
|------|---------|-------------|
| `--condition-id` | *(required)* | Market condition ID (hex string) |
| `--side` | *(required)* | Order side: `buy` or `sell` |
| `--outcome` | *(required)* | Outcome to trade: `yes` or `no` |
| `--amount` | *(required)* | Number of shares to trade (minimum 5) |
| `--price` | `0.5` | Limit price 0.01–0.99 (ignored for market orders) |
| `--type` | `limit` | Order type: `limit` or `market` |
| `--no-confirm` | `false` | Skip confirmation prompt |

### `balance` — Display USDC Balance

```bash
trading-tools-polymarket balance
```

Shows current USDC balance and allowance. Balance is returned in micro-USDC (6 decimal places) and automatically converted to dollars.

### `orders` — List Open Orders

```bash
trading-tools-polymarket orders
```

### `cancel` — Cancel an Order

```bash
trading-tools-polymarket cancel --order-id <id>
```

| Flag | Default | Description |
|------|---------|-------------|
| `--order-id` | *(required)* | ID of the order to cancel |

### `redeem` — Redeem Winning Positions

```bash
trading-tools-polymarket redeem
trading-tools-polymarket redeem --no-confirm
```

Redeems winning positions on-chain via the CTF contract. Requires POL for gas fees on Polygon.

| Flag | Default | Description |
|------|---------|-------------|
| `--no-confirm` | `false` | Skip confirmation prompt |

**Note:** Polymarket does NOT auto-redeem winning tokens. You must redeem manually using this command or the Polymarket UI.

## Trading Bots

### `bot` — Paper Trading Bot

Run a simulated trading bot against live market data. No real trades are placed.

```bash
# Default: mean reversion strategy
trading-tools-polymarket bot --series btc-updown-5m --capital 1000

# Late snipe strategy on specific markets
trading-tools-polymarket bot --strategy pm_late_snipe --markets <id1>,<id2>

# With verbose tick logging
trading-tools-polymarket bot --strategy pm_market_making --series btc-updown-5m --verbose
```

| Flag | Default | Description |
|------|---------|-------------|
| `--strategy` | `pm_mean_reversion` | Strategy name (see below) |
| `--markets` | | Comma-separated condition IDs to track |
| `--series` | | Comma-separated series slugs for auto-discovery (e.g. `btc-updown-5m`) |
| `--capital` | `1000.0` | Initial virtual capital in USD |
| `--ob-refresh` | `30` | Seconds between order book refreshes |
| `--max-ticks` | unlimited | Stop after N ticks |
| `--max-position-pct` | `0.1` | Max fraction of capital per market |
| `--kelly-frac` | `0.25` | Fractional Kelly multiplier |
| `--period` | `20` | Rolling window period (mean reversion) |
| `--z-threshold` | `1.5` | Z-score threshold (mean reversion) |
| `--spread-pct` | `0.03` | Half-spread fraction (market making) |
| `--imbalance-threshold` | `0.65` | Imbalance threshold (liquidity hunting) |
| `--min-edge` | `0.02` | Minimum edge (cross-market arb) |
| `--snipe-threshold` | `0.8` | Price threshold for late snipe (0.5–1.0) |
| `--snipe-window` | `60` | Seconds before market end to start sniping |
| `--verbose`, `-v` | `false` | Enable tick-by-tick logging |

**Available strategies:**

| Strategy | Description |
|----------|-------------|
| `pm_mean_reversion` | Trade deviations from rolling mean price |
| `pm_market_making` | Place symmetric bid/ask spreads |
| `pm_liquidity_hunting` | Exploit order book imbalances |
| `pm_cross_market_arb` | Arbitrage mispricing across correlated markets |
| `pm_late_snipe` | Snipe high-confidence outcomes near market close |

You must provide either `--markets` or `--series` (or both) to specify which markets to trade.

### `bot-live` — Live Trading Bot

Run a live trading bot with real money. **Requires the `--confirm-live` flag** to prevent accidental execution.

```bash
trading-tools-polymarket bot-live \
  --strategy pm_late_snipe \
  --series btc-updown-5m \
  --max-loss-pct 0.05 \
  --confirm-live
```

Includes all options from `bot` plus:

| Flag | Default | Description |
|------|---------|-------------|
| `--strategy` | `pm_late_snipe` | Strategy name (default differs from paper bot) |
| `--max-loss-pct` | `0.10` | Max drawdown fraction before auto-stop (0–1) |
| `--market-orders` / `--limit-orders` | `--market-orders` | Use FOK market orders or GTC limit orders |
| `--confirm-live` | `false` | **Required flag** — prevents accidental live trading |
| `--auto-redeem` / `--no-auto-redeem` | `--auto-redeem` | Redeem winning tokens on-chain automatically |

The bot will automatically stop trading if the loss limit (`--max-loss-pct`) is reached.

## Tick Collection

### `tick-collect` — Stream Real-Time Tick Data

Connect to Polymarket's WebSocket feed and store trade events in a database.

```bash
# Collect ticks for auto-discovered markets
trading-tools-polymarket tick-collect --series btc-updown-5m --verbose

# Collect specific markets with order book snapshots
trading-tools-polymarket tick-collect \
  --markets <id1>,<id2> \
  --book-interval 30 \
  --book-depth 20

# Use PostgreSQL instead of SQLite
trading-tools-polymarket tick-collect \
  --series btc-updown-5m \
  --db-url "postgresql+asyncpg://user:pass@host:5432/trading_tools"
```

| Flag | Default | Description |
|------|---------|-------------|
| `--markets` | | Comma-separated condition IDs to subscribe to |
| `--series` | | Comma-separated series slugs for auto-discovery |
| `--db-url` | env `TICK_DB_URL` or `sqlite+aiosqlite:///tick_data.db` | SQLAlchemy async DB URL |
| `--flush-interval` | `10` | Max seconds between DB flushes |
| `--flush-batch-size` | `100` | Max ticks buffered before forced flush |
| `--discovery-interval` | `300` | Seconds between market re-discovery |
| `--discovery-lead` | `30` | Seconds before next boundary to trigger discovery |
| `--book-interval` | `0` | Seconds between order book polls (0 = disabled) |
| `--book-depth` | `10` | Max bid/ask levels to store per snapshot |
| `--book-stagger` | `100` | Milliseconds between polling each token |
| `--verbose`, `-v` | `false` | Enable debug logging |

**Database tables:**

- `ticks` — Trade events (timestamp, token_id, price, size)
- `order_book_snapshots` — Order book state at each poll interval

## Whale Monitoring

Track and analyse large Polymarket traders.

### `whale-add` — Register a Whale Address

```bash
trading-tools-polymarket whale-add --address 0x1234... --label "BigTrader"
```

| Flag | Default | Description |
|------|---------|-------------|
| `--address` | *(required)* | Whale proxy wallet address |
| `--label` | auto-generated from address | Friendly name for the whale |
| `--db-url` | env `WHALE_DB_URL` or `sqlite+aiosqlite:///whale_data.db` | SQLAlchemy async DB URL |

### `whale-monitor` — Run Whale Trade Monitor

Long-running service that polls for new trades by tracked whales.

```bash
trading-tools-polymarket whale-monitor --poll-interval 60 --verbose
```

| Flag | Default | Description |
|------|---------|-------------|
| `--whales` | | Comma-separated whale proxy wallet addresses (overrides DB) |
| `--db-url` | env `WHALE_DB_URL` or `sqlite+aiosqlite:///whale_data.db` | SQLAlchemy async DB URL |
| `--poll-interval` | `120` | Seconds between polling cycles |
| `--verbose`, `-v` | `false` | Enable debug logging |

**Database tables:**

- `tracked_whales` — Registered whale addresses and labels
- `whale_trades` — Whale trade history

### `whale-analyse` — Analyse Whale Strategy

```bash
trading-tools-polymarket whale-analyse --address 0x1234... --days 30
```

| Flag | Default | Description |
|------|---------|-------------|
| `--address` | *(required)* | Whale proxy wallet address to analyse |
| `--days` | `7` | Number of days to analyse |
| `--db-url` | env `WHALE_DB_URL` or `sqlite+aiosqlite:///whale_data.db` | SQLAlchemy async DB URL |

### `whale-markets` — Per-Market Directional Analysis

```bash
trading-tools-polymarket whale-markets --address 0x1234... --days 1 --min-trades 20
```

| Flag | Default | Description |
|------|---------|-------------|
| `--address` | *(required)* | Whale proxy wallet address to analyse |
| `--days` | `1` | Number of days to analyse |
| `--min-trades` | `10` | Minimum trades per market to include |
| `--db-url` | env `WHALE_DB_URL` or `sqlite+aiosqlite:///whale_data.db` | SQLAlchemy async DB URL |

### `whale-correlate` — Correlate Whale Bets with Spot Price

Cross-reference a whale's directional bet per market with actual spot price movement fetched from Binance.

```bash
trading-tools-polymarket whale-correlate --address 0x1234... --days 7 --min-trades 10
```

| Flag | Default | Description |
|------|---------|-------------|
| `--address` | *(required)* | Whale proxy wallet address to analyse |
| `--days` | `1` | Number of days to analyse |
| `--min-trades` | `10` | Minimum trades per market to include |
| `--db-url` | env `WHALE_DB_URL` or `sqlite+aiosqlite:///whale_data.db` | SQLAlchemy async DB URL |

For each market the command shows: the whale's favoured side (Up/Down), the actual price change over the market window, and whether the whale's call was correct.

## Backtesting Polymarket Strategies

### `backtest-snipe` — Backtest Late Snipe on Synthetic Data

Backtest the late snipe strategy using synthetic candle data from Binance.

```bash
trading-tools-polymarket backtest-snipe --start 2025-01-01 --end 2025-01-31 --verbose
```

| Flag | Default | Description |
|------|---------|-------------|
| `--symbols` | `BTC-USD,ETH-USD,SOL-USD,XRP-USD` | Comma-separated symbols |
| `--start` | *(required)* | Start date `YYYY-MM-DD` |
| `--end` | *(required)* | End date `YYYY-MM-DD` |
| `--capital` | `1000.0` | Initial virtual capital in USD |
| `--snipe-threshold` | `0.8` | Price threshold for late snipe (0.5–1.0) |
| `--snipe-window` | `90` | Seconds before market end to start sniping |
| `--scale-factor` | `15.0` | Snapshot simulator price sensitivity |
| `--kelly-frac` | `0.25` | Fractional Kelly multiplier |
| `--max-position-pct` | `0.1` | Max fraction of capital per market |
| `--verbose`, `-v` | `false` | Enable per-trade logging |

### `backtest-ticks` — Backtest Late Snipe on Real Tick Data

Backtest against real tick data collected by `tick-collect`.

```bash
trading-tools-polymarket backtest-ticks --start 2025-01-01 --end 2025-01-31
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | *(required)* | Start date `YYYY-MM-DD` |
| `--end` | *(required)* | End date `YYYY-MM-DD` |
| `--db-url` | env `TICK_DB_URL` or `sqlite+aiosqlite:///tick_data.db` | SQLAlchemy async DB URL |
| `--capital` | `1000.0` | Initial virtual capital in USD |
| `--snipe-threshold` | `0.8` | Price threshold for late snipe (0.5–1.0) |
| `--snipe-window` | `90` | Seconds before market end to start sniping |
| `--bucket-seconds` | `1` | Seconds per snapshot bucket |
| `--window-minutes` | `5` | Market window duration in minutes (5 or 15) |
| `--kelly-frac` | `0.25` | Fractional Kelly multiplier |
| `--max-position-pct` | `0.1` | Max fraction of capital per market |
| `--max-slippage` | `0.05` | Max slippage tolerance (0–1 scale) |
| `--verbose`, `-v` | `false` | Enable per-trade logging |

### `grid-backtest` — Grid Search Snipe Parameters

Exhaustively search threshold and window combinations to find optimal parameters.

```bash
trading-tools-polymarket grid-backtest --start 2025-01-01 --end 2025-01-31
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | *(required)* | Start date `YYYY-MM-DD` |
| `--end` | *(required)* | End date `YYYY-MM-DD` |
| `--db-url` | env `TICK_DB_URL` or `sqlite+aiosqlite:///tick_data.db` | SQLAlchemy async DB URL |
| `--capital` | `1000.0` | Initial virtual capital in USD |
| `--bucket-seconds` | `1` | Seconds per snapshot bucket |
| `--kelly-frac` | `0.25` | Fractional Kelly multiplier |
| `--max-position-pct` | `0.1` | Max fraction of capital per market |
| `--max-slippage` | `0.05` | Max slippage tolerance (0–1 scale) |
| `--verbose`, `-v` | `false` | Enable per-trade logging |

The grid searches thresholds from 0.55 to 0.95 (step 0.05) and windows from 120s down to 10s (step 10s).

## Database Support

Both tick collection and whale monitoring support SQLite (default) and PostgreSQL:

```bash
# SQLite (default, no setup required)
--db-url "sqlite+aiosqlite:///tick_data.db"

# PostgreSQL (requires asyncpg)
--db-url "postgresql+asyncpg://user:pass@host:5432/trading_tools"
```

Set the `TICK_DB_URL` or `WHALE_DB_URL` environment variable to avoid passing `--db-url` on every command.
