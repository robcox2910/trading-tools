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
| Spread capture bot (paper) | No |
| Spread capture bot (live) | Yes |

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

Run a simulated trading bot against live market data. No real trades are placed. Fees use the Polymarket polynomial formula `C × p × feeRate × (p(1-p))^exponent` — fees are highest at p=0.50 and drop toward zero at price extremes. Order book slippage is also modelled for realistic P&L. Use `--max-loss-pct` to auto-stop the bot on excessive drawdown.

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
| `--fee-rate` | `0.25` | Fee rate parameter in polynomial formula (0.25=crypto, 0.0175=sports, 0=disabled) |
| `--fee-exponent` | `2` | Fee exponent (2=crypto, 1=sports) |
| `--max-loss-pct` | `-100` | Stop bot at this drawdown % (e.g. -20 for 20% loss limit) |
| `--verbose`, `-v` | `false` | Enable tick-by-tick logging |

**Available strategies:**

| Strategy | Description |
|----------|-------------|
| `pm_mean_reversion` | Trade deviations from rolling mean price |
| `pm_market_making` | Place symmetric bid/ask spreads |
| `pm_liquidity_imbalance` | Exploit order book imbalances |
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

Correlate a whale's directional bets with actual spot price movement using Binance 1-minute candles. For each market the whale traded, this command determines whether their favoured side matched the actual price direction.

```bash
trading-tools-polymarket whale-correlate --address 0x1234... --days 1 --min-trades 10
```

| Flag | Default | Description |
|------|---------|-------------|
| `--address` | *(required)* | Whale proxy wallet address to analyse |
| `--days` | `1` | Number of days to analyse |
| `--min-trades` | `10` | Minimum trades per market to include |
| `--db-url` | env `WHALE_DB_URL` or `sqlite+aiosqlite:///whale_data.db` | SQLAlchemy async DB URL |

## Spread Capture Bot

### `spread-capture` — Buy Both Sides When Combined < $1.00

Run a polling service that monitors BTC/ETH 5-minute prediction markets and captures the spread when the combined price of both sides drops below $1.00. Paper mode by default; pass `--confirm-live` for real orders.

The bot uses a multi-phase approach:

1. **Directional entry (leg 1):** detect the favoured side (via whale bias signals or series slug discovery) and buy it immediately at current CLOB prices. Position size is determined by the Kelly criterion based on estimated win rate.
2. **Take-profit:** each poll cycle, check if the leg 1 token price has risen above the take-profit threshold (`entry × (1 + take_profit_pct)`). If so, sell early to lock in known profit.
3. **Defensive hedge:** if the leg 1 token price drops below `entry × (1 - defensive_hedge_pct)`, buy the opposite side to cap loss at settlement instead of selling into a thin book. The position becomes hedged with a bounded max loss. If the combined cost (leg1 + hedge) would exceed `max_defensive_hedge_cost`, sell the tokens instead to avoid locking in a large guaranteed loss.
4. **Profit hedge (leg 2):** monitor the opposite side each poll cycle. When `effective_leg1_price + hedge_price ≤ max_spread_cost - 2×fee_rate`, the opposite side is cheap enough to lock in guaranteed profit. Hedge uses FOK market orders by default for fast execution.
5. **Settlement:** all hedged positions resolve at market expiry with known P&L (profit or capped loss).

If no hedge opportunity arises before expiry, the position resolves as a pure directional bet (profitable when the whale is correct ~80% of the time).

6. **Flip trading (optional):** when `--enable-flipping` is set, take-profit exits are followed by immediate re-entry on the opposite side, capturing multiple spread swings per market window. On take-profit, the bot sells leg 1 tokens and immediately buys the opposite side with the same dollar amount. If that side then rises to the flip take-profit threshold, it flips back. Flips take priority over take-profit hedging when enabled. Flips are capped by `--max-flips-per-market` and stop when fewer than `--min-flip-buffer-seconds` remain before expiry. When flip limits are reached, the bot falls back to take-profit hedging (combined < $1.00) or selling.

The service uses **incremental polling** for minimal latency: only new trades since the last poll are fetched, and a rolling window of trades is maintained in memory.

```bash
# Paper mode (default) — log signals, track virtual P&L
trading-tools-polymarket spread-capture \
  --series-slugs btc-updown-5m \
  --poll-interval 5 \
  --min-bias 1.5 \
  --min-trades 3 \
  --capital 100 \
  --max-spread-cost 0.95 \
  --max-entry-price 0.65 \
  -v

# Live mode — place real limit orders on Polymarket
trading-tools-polymarket spread-capture \
  --series-slugs btc-updown-5m \
  --capital 100 \
  --max-position-pct 0.10 \
  --confirm-live

# Load settings from a YAML config file (CLI flags override YAML values)
trading-tools-polymarket spread-capture \
  --config spread-capture.yaml \
  --series-slugs btc-updown-5m \
  --capital 200 \
  -v
```

**YAML config file** — all fields are optional (dataclass defaults fill omitted values). CLI flags override YAML values; YAML overrides defaults. Keys match ``SpreadCaptureConfig`` field names:

```yaml
# spread-capture.yaml
series_slugs: "btc-updown-5m"
capital: "200"
max_position_pct: "0.15"
max_spread_cost: "0.95"
max_entry_price: "0.65"
adaptive_kelly: true
compound_profits: true
circuit_breaker_losses: 5
circuit_breaker_cooldown: 600
enable_flipping: true
max_flips_per_market: 4
min_flip_buffer_seconds: 30
flip_take_profit_pct: "0.10"
```

| Flag | Default | Description |
|------|---------|-------------|
| `--series-slugs` | *(required)* | Comma-separated series slugs for market discovery (e.g. `btc-updown-5m`) |
| `--config` | *none* | Path to YAML config file (CLI flags override YAML values) |
| `--poll-interval` | `5` | Seconds between DB polls (lower = faster) |
| `--lookback` | `900` | Rolling window in seconds for trade accumulation |
| `--min-bias` | `1.3` | Minimum bias ratio to trigger a copy signal |
| `--min-trades` | `2` | Minimum trades per market to trigger a signal |
| `--capital` | `100` | Starting capital in USDC (paper mode) |
| `--max-position-pct` | `0.10` | Max fraction of capital per single trade |
| `--max-spread-cost` | `0.95` | Max combined cost of both legs to trigger hedge (e.g. 0.95 = min 5% return) |
| `--max-entry-price` | `0.65` | Max price for directional entry (skip if favoured side already above this) |
| `--max-window` | `0` | Max market window in seconds (e.g. 300 for 5-min only, 0=all) |
| `--no-hedge-market-orders` | `false` | Use GTC limit orders for hedge leg instead of FOK market |
| `--defensive-hedge-pct` | `0.10` | Buy opposite side when leg1 drops this % (e.g. 0.10 = hedge at 10% drop) |
| `--max-defensive-hedge-cost` | `1.05` | Max combined cost for defensive hedge; sell instead if exceeded |
| `--win-rate` | `0.80` | Estimated whale win rate for Kelly criterion sizing |
| `--kelly-fraction` | `0.5` | Fractional Kelly multiplier (e.g. 0.5 = half-Kelly for safety) |
| `--clob-fee-rate` | `0.0` | Per-leg CLOB fee rate for hedge profitability check |
| `--take-profit-pct` | `0.15` | Take profit at this % gain above entry (e.g. 0.15 = 15%) |
| `--max-unhedged-exposure-pct` | `0.50` | Max fraction of capital in net unhedged exposure per asset (opposite sides offset) |
| `--adaptive-kelly/--no-adaptive-kelly` | `true` | Dynamically adjust Kelly win rate from realised unhedged outcomes |
| `--min-kelly-results` | `20` | Min closed unhedged trades before adaptive Kelly activates |
| `--min-win-rate` | `0.65` | Floor for adaptive Kelly win rate |
| `--max-asset-exposure-pct` | `0.30` | Max fraction of capital per asset+side (e.g. all BTC-USD Up) |
| `--compound-profits/--no-compound-profits` | `true` | Grow paper capital by adding realised P&L from closed trades |
| `--hedge-urgency-threshold` | `0.20` | Time fraction below which hedge spread threshold is relaxed |
| `--hedge-urgency-spread-bump` | `0.03` | Amount added to max_spread_cost in urgency zone |
| `--circuit-breaker-losses` | `3` | Consecutive unhedged losses to trigger cooldown pause (0=disabled) |
| `--circuit-breaker-cooldown` | `300` | Seconds to pause new entries after circuit breaker triggers |
| `--max-drawdown-pct` | `0.15` | Max session drawdown as fraction — halt entries at 15% loss from start |
| `--drawdown-throttle-pct` | `0.10` | HWM drawdown fraction to throttle Kelly by 50% (e.g. 0.10 = throttle at 10% below peak) |
| `--paper-slippage-pct` | `0.005` | Simulated slippage for paper fills (e.g. 0.005 = 0.5% worse price) |
| `--signal-strength-sizing/--no-signal-strength-sizing` | `true` | Scale position size by signal strength (bias ratio × trade count) |
| `--max-entry-age-pct` | `0.60` | Max fraction of window elapsed before skipping entry (e.g. 0.60 = first 60% only) |
| `--halt-win-rate` | `0.55` | Halt entries when adaptive win rate drops below this threshold |
| `--enable-flipping/--no-flipping` | `false` | Flip to opposite side on take-profit instead of selling (active spread capture) |
| `--max-flips-per-market` | `4` | Max flips per market window |
| `--min-flip-buffer-seconds` | `30` | Stop flipping with fewer than this many seconds to expiry |
| `--flip-take-profit-pct` | `0.10` | Tighter take-profit % for flip legs (e.g. 0.10 = 10% vs 15% initial) |
| `--confirm-live` | `false` | **Required flag** for live trading |
| `--db-url` | env `SPREAD_DB_URL`, `WHALE_DB_URL`, or `sqlite+aiosqlite:///whale_data.db` | SQLAlchemy async DB URL |
| `--verbose`, `-v` | `false` | Enable DEBUG logging |

**Signal detection pipeline:**

1. Poll `whale_trades` table incrementally (only new trades since last check)
2. Group by `condition_id`, compute bias via `analyse_markets()`
3. Filter: BTC/ETH asset only, future time window, bias > threshold, trades >= min
4. Fetch current CLOB prices; skip if favoured side > `max_entry_price`
5. Open directional leg 1 (buy favoured side, Kelly-sized with optional signal strength scaling)
6. Each poll cycle checks (in order): take-profit → defensive hedge → profit hedge → expiry
7. Take-profit: if flipping enabled, sell + flip to opposite side (priority); otherwise hedge opposite side when combined < $1.00; sell as fallback
8. Defensive hedge: buy opposite side if leg 1 price drops below `entry × (1 - defensive_hedge_pct)`. If combined cost > `max_defensive_hedge_cost`, sell leg 1 instead
9. Hedge: if `effective_leg1_price + hedge_price ≤ max_spread_cost - 2×fee`, buy matching token quantity on opposite side (FOK by default)
10. Close remaining positions when the market window expires; P&L depends on state

**Heartbeat:** Logs status every 60 seconds (poll count, unhedged/hedged positions, P&L) for CloudWatch monitoring.

**Database persistence:** When `SPREAD_DB_URL` (or `WHALE_DB_URL`) is set, closed trade results are automatically persisted to the `copy_results` table. Each result is written immediately at close time (not batched) so data survives crashes. The table stores denormalized signal fields (condition_id, asset, bias_ratio, window timestamps) alongside execution details (entry/hedge prices, quantities, P&L, state) for direct querying without joins.

This enables post-hoc analysis such as backtesting different `max_spread_cost` thresholds:

```sql
-- Compare hedge rates at different spread cost thresholds
SELECT
  CASE WHEN state = 'hedged' THEN 'hedged' ELSE 'unhedged' END AS outcome,
  COUNT(*) AS trades,
  AVG(pnl) AS avg_pnl
FROM copy_results
WHERE is_paper = true
GROUP BY outcome;
```

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

Set the `TICK_DB_URL`, `WHALE_DB_URL`, or `SPREAD_DB_URL` environment variable to avoid passing `--db-url` on every command.
