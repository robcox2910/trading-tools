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
| Directional bot (paper) | No |
| Directional bot (live) | Yes |
| Directional backtest | No |
| Whale copy bot (paper) | No (requires WHALE_DB_URL) |
| Whale copy bot (live) | Yes |

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

## Spread Capture Bot

### `spread-capture` — Buy Both Sides When Combined < $1.00

Run a polling service that scans BTC/ETH/SOL/XRP/DOGE Up/Down markets for spread opportunities where the combined cost of buying both sides is below $1.00, guaranteeing profit at settlement. Paper mode by default; pass `--confirm-live` for real orders.

The bot uses a simple, guaranteed-profit approach:

1. **Market discovery:** periodically scan configured series slugs (e.g. `btc-updown-5m`, `eth-updown-15m`) for active markets with future settlement times.
2. **Spread detection:** fetch CLOB order book best **ask** prices for both Up and Down tokens. If the combined ask price is below `max_combined_cost` and the net margin (after Polymarket fees) exceeds `min_spread_margin`, an opportunity is detected.
3. **Entry:** buy both sides simultaneously. Position size is `capital × max_position_pct / combined_cost`, capped by `max_book_pct` of visible ask depth to limit market impact. In live mode, prices are re-validated via VWAP walk of the order book before placing orders.
4. **Settlement:** at market expiry, one side pays out $1.00 per token. P&L = winning quantity × $1.00 - total cost basis - entry fees. Since combined cost < $1.00, profit is guaranteed for paired positions.
5. **Single-leg management:** if one side fails to fill (FOK rejection or GTC timeout), the bot attempts to unwind the filled leg at market. If unwind fails, the position is tracked as SINGLE_LEG and the bot attempts early exit when >60 seconds remain before expiry.

```bash
# Paper mode (default) — log signals, track virtual P&L
trading-tools-polymarket spread-capture \
  --series-slugs btc-updown-5m \
  --poll-interval 5 \
  --capital 100 \
  --max-combined-cost 0.98 \
  --min-spread-margin 0.01 \
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
max_combined_cost: "0.97"
min_spread_margin: "0.01"
fee_rate: "0.25"
fee_exponent: 2
max_book_pct: "0.20"
compound_profits: true
circuit_breaker_losses: 5
circuit_breaker_cooldown: 600
```

| Flag | Default | Description |
|------|---------|-------------|
| `--series-slugs` | `btc-updown-5m,eth-updown-5m` | Comma-separated series slugs or `crypto-5m`/`crypto-15m` shortcut |
| `--config` | *none* | Path to YAML config file (CLI flags override YAML values) |
| `--strategy` | `simultaneous` | Execution strategy: `simultaneous` (both sides at once), `accumulate` (independent per-side fills over time), or `maker` (resting GTC limit bids on both sides) |
| `--poll-interval` | `5` | Seconds between scan cycles |
| `--capital` | `100` | Starting capital in USDC (paper mode) |
| `--max-position-pct` | `0.10` | Max fraction of capital per spread trade |
| `--max-combined-cost` | `0.98` | Max combined cost of both sides to enter (must be < 1.0) |
| `--min-spread-margin` | `0.01` | Min profit margin per token pair after fees |
| `--max-window` | `0` | Max market window in seconds (e.g. 300 for 5-min only, 0=all) |
| `--max-entry-age-pct` | `0.60` | Max fraction of window elapsed before skipping entry |
| `--max-open-positions` | `10` | Max concurrent spread positions |
| `--fee-rate` | `0.25` | Polymarket crypto fee rate coefficient |
| `--fee-exponent` | `2` | Polymarket fee exponent for `price × (1-price)` term |
| `--max-book-pct` | `0.20` | Max fraction of visible order book depth to consume per side |
| `--use-market-orders/--no-use-market-orders` | `false` | Use FOK market orders instead of GTC limit |
| `--single-leg-timeout` | `10` | Seconds before cancelling unfilled side (live only) |
| `--rediscovery-interval` | `30` | Seconds between market rediscovery calls |
| `--compound-profits/--no-compound-profits` | `true` | Grow paper capital by adding realised P&L |
| `--circuit-breaker-losses` | `3` | Consecutive losses to trigger cooldown (0=disabled) |
| `--circuit-breaker-cooldown` | `300` | Seconds to pause after circuit breaker triggers |
| `--max-drawdown-pct` | `0.15` | Max session drawdown as fraction — halt entries when exceeded |
| `--paper-slippage-pct` | `0.005` | Simulated slippage for paper fills |
| `--confirm-live` | `false` | **Required flag** for live trading |
| `--verbose`, `-v` | `false` | Enable DEBUG logging |

**`maker` strategy flags** (only active when `--strategy maker`):

| Flag | Default | Description |
|------|---------|-------------|
| `--maker-bid-up` | `0.25` | Resting bid price for the Up token |
| `--maker-bid-down` | `0.25` | Resting bid price for the Down token |
| `--maker-order-size` | `20` | Token quantity per maker order |

The maker strategy places resting GTC limit buy orders at fixed bid prices on both Up and Down sides, waiting for taker sells instead of taking liquidity at the best ask. Use `--single-leg-timeout 300` to wait until the full window expires. Backtest results show 13.9% of 5-min windows see both sides fill at $0.25/$0.25 bids.

```bash
# Paper maker bot
trading-tools-polymarket spread-capture \
  --strategy maker \
  --maker-bid-up 0.25 --maker-bid-down 0.25 \
  --maker-order-size 20 \
  --series-slugs btc-updown-5m \
  --single-leg-timeout 300

# Live maker bot
trading-tools-polymarket spread-capture \
  --confirm-live \
  --strategy maker \
  --maker-bid-up 0.25 --maker-bid-down 0.25 \
  --maker-order-size 20 \
  --series-slugs btc-updown-5m \
  --single-leg-timeout 300
```

**`accumulate` strategy flags** (only active when `--strategy accumulate`):

| Flag | Default | Description |
|------|---------|-------------|
| `--signal-delay-seconds` | `300` | Seconds of Binance data to look back before window opens for momentum signal |
| `--hedge-start-threshold` | `0.50` | Early hedge: only buy secondary side when ask < this price |
| `--hedge-end-threshold` | `0.90` | Late hedge: maximum time-decay threshold near fill cutoff |
| `--hedge-start-pct` | `0.20` | Begin hedge fills at this fraction of window elapsed |
| `--max-primary-price` | `0.60` | Maximum ask price for primary side fills (prevents buying decided markets) |
| `--max-imbalance-ratio` | `1.3` | Maximum ratio of tokens held on one side vs the other (whale median is 1.15, P75 is 1.30) |
| `--initial-fill-size` | `20` | Token quantity for first fill on primary side (establishes base position) |
| `--fill-size-tokens` | `2` | Token quantity for hedge fills and primary adjustments (whale DCA pattern) |
| `--max-fill-age-pct` | `0.80` | Stop filling when market window is past this fraction (whale median fill is at 60%) |

**Spread detection pipeline:**

1. Discover active markets from configured series slugs (refreshed every `rediscovery_interval` seconds)
2. Fetch CLOB order books for both Up and Down tokens
3. Use best ask price as actual buy cost (not bids or midpoints)
4. Compute net margin: `1.0 - combined_ask - up_fee - down_fee`
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

## Directional Trading Bot

The directional trading bot buys only the predicted winning side of binary crypto Up/Down markets using momentum, volatility, volume, and order-book features to estimate P(Up). Positions are sized via Kelly criterion. Fully independent from the spread capture bot.

### `directional` — Run Directional Paper/Live Trading

```bash
# Paper mode (default)
trading-tools-polymarket directional --capital 100 --min-edge 0.05 -v

# With custom entry window and Kelly fraction
trading-tools-polymarket directional \
  --capital 200 \
  --min-edge 0.03 \
  --kelly-fraction 0.3 \
  --entry-start 30 \
  --entry-end 10 \
  --series-slugs crypto-5m

# Live mode (real orders)
trading-tools-polymarket directional --capital 50 --confirm-live
```

| Flag | Default | Description |
|------|---------|-------------|
| `--capital` | `100` | Starting USDC capital (paper mode) |
| `--min-edge` | `0.05` | Minimum probability edge to enter |
| `--kelly-fraction` | `0.5` | Fractional Kelly multiplier (0.5 = half-Kelly) |
| `--max-position-pct` | `0.15` | Maximum fraction of capital per trade |
| `--entry-start` | `30` | Seconds before close to start entries |
| `--entry-end` | `10` | Seconds before close to stop entries |
| `--signal-lookback` | `300` | Seconds of Binance candle lookback |
| `--series-slugs` | `btc-updown-5m,eth-updown-5m` | Series to scan (supports `crypto-5m` shortcut) |
| `--max-open-positions` | `10` | Maximum concurrent positions |
| `--config` | — | Path to YAML config file |
| `--confirm-live` | `False` | Enable live trading with real orders |
| `-v` / `--verbose` | `False` | Enable DEBUG logging |

### `directional-backtest` — Backtest Directional Algorithm

```bash
trading-tools-polymarket directional-backtest \
  --start 2026-03-01 --end 2026-03-06 \
  --capital 1000 --min-edge 0.05 -v
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | — | Start date (YYYY-MM-DD, required) |
| `--end` | — | End date (YYYY-MM-DD, required) |
| `--capital` | `1000` | Initial virtual capital |
| `--min-edge` | `0.05` | Minimum probability edge |
| `--kelly-fraction` | `0.5` | Fractional Kelly multiplier |
| `--entry-start` | `30` | Seconds before close to start entries |
| `--entry-end` | `10` | Seconds before close to stop entries |
| `--signal-lookback` | `300` | Binance candle lookback seconds |
| `--series-slug` | — | Filter to a specific series slug |
| `--db-url` | `$TICK_DB_URL` | Database URL for tick data |
| `--whale-db-url` | `$WHALE_DB_URL` | DB URL for whale trades (defaults to `--db-url`) |
| `-v` / `--verbose` | `False` | Enable per-window logging |

Output includes standard metrics (P&L, win rate, avg P&L) plus calibration metrics:
- **Brier score**: Mean squared error of probability predictions (< 0.25 = better than random)
- **Avg P(win) when correct**: Confidence when the algorithm was right
- **Avg P(win) when incorrect**: Confidence when the algorithm was wrong

### `train-weights` — Train Estimator Weights via Logistic Regression

Fit all 8 feature weights and a bias (intercept) term simultaneously on historical market outcome data using gradient descent. The learned weights are mathematically optimal for the `P(Up) = sigmoid(dot(features, w) + bias)` model form and slot directly into `DirectionalConfig`. Features include a cross-asset `leader_momentum` signal (BTC price change in last 60s) that captures the "BTC leads altcoins" effect.

```bash
trading-tools-polymarket train-weights \
  --start 2026-03-01 --end 2026-03-19 \
  --signal-lookback 1200 --l2-lambda 0.01 \
  --output-yaml trained_weights.yaml
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | — | Start date (YYYY-MM-DD, required) |
| `--end` | — | End date (YYYY-MM-DD, required) |
| `--entry-start` | `30` | Seconds before close to evaluate entry |
| `--signal-lookback` | `1200` | Binance candle lookback seconds |
| `--learning-rate` | `0.1` | Gradient descent step size |
| `--max-iterations` | `10000` | Maximum gradient descent iterations |
| `--l2-lambda` | `0.0` | L2 regularisation coefficient (0 = none) |
| `--output-yaml` | — | Write learned weights to a YAML config file |
| `--series-slug` | — | Filter to a specific series slug |
| `--all-slugs` | `False` | Train per-slug weights alongside global |
| `--db-url` | `$TICK_DB_URL` | Database URL for tick data |
| `--whale-db-url` | `$WHALE_DB_URL` | DB URL for whale trades (defaults to `--db-url`) |
| `-v` / `--verbose` | `False` | Enable verbose logging |

Output includes learned vs. default weight comparison, accuracy, and log-loss. Use `--output-yaml` to save weights for loading via `DirectionalConfig.from_yaml()`.

#### Per-slug weight training

Use `--all-slugs` to train separate weights for each series slug (e.g. `btc-updown-5m`, `eth-updown-5m`) alongside the global weights. Slugs with fewer than 50 samples are skipped.

```bash
trading-tools-polymarket train-weights \
  --start 2026-03-01 --end 2026-03-19 \
  --all-slugs --output-yaml weights.yaml
```

The output YAML includes a `weights_by_slug` section:

```yaml
w_momentum: 0.15       # global fallback
w_whale: 0.50
weights_by_slug:
  btc-updown-5m:
    w_momentum: 0.88
    w_rsi: 5.17
  eth-updown-5m:
    w_momentum: 0.72
```

When the directional engine evaluates a market, it looks up the market's series slug in `weights_by_slug` and uses those weights. Markets without a slug-specific entry fall back to the global weights.

## Whale Copy Bot

The whale copy bot mirrors the net directional positioning of tracked whale traders in real time. Unlike the spread capture strategy (which locks in a signal early), this bot re-reads the whale's current direction every poll cycle and buys tokens on whichever side they currently favour. If the whale flips mid-window, both sides can accumulate tokens — winning tokens pay $1.00, losing tokens pay $0.00.

### `whale-copy` — Mirror Whale Directional Positioning

```bash
# Paper mode (default) — mirror whales on 5m crypto markets
trading-tools-polymarket whale-copy --series-slugs crypto-5m --capital 1000

# With custom conviction threshold and fill size
trading-tools-polymarket whale-copy \
  --series-slugs crypto-5m \
  --capital 500 \
  --min-conviction 2.0 \
  --fill-size 10 \
  --max-price 0.55

# Live mode
trading-tools-polymarket whale-copy \
  --series-slugs crypto-5m \
  --capital 1000 \
  --confirm-live
```

Requires `WHALE_DB_URL` environment variable (whale addresses loaded from database). Optionally set `SPREAD_DB_URL` or `WHALE_DB_URL` for result persistence.

| Flag | Default | Description |
|------|---------|-------------|
| `--series-slugs` | `btc-updown-5m,eth-updown-5m,xrp-updown-5m,sol-updown-5m` | Comma-separated series slugs or `crypto-5m` shortcut |
| `--config` | `None` | Path to YAML config file (CLI flags override) |
| `--poll-interval` | `5` | Seconds between scan cycles |
| `--capital` | `1000` | Starting capital in USDC (paper mode) |
| `--fill-size` | `5` | Tokens per fill (must meet min order size) |
| `--max-price` | `0.60` | Maximum ask price to buy |
| `--min-conviction` | `1.5` | Minimum whale dollar ratio on favoured side |
| `--max-position-pct` | `0.10` | Max fraction of capital per market |
| `--max-open-positions` | `10` | Max concurrent positions |
| `--circuit-breaker-losses` | `5` | Consecutive losses to trigger cooldown |
| `--circuit-breaker-cooldown` | `300` | Seconds to pause after circuit breaker |
| `--max-drawdown-pct` | `0.20` | Max session drawdown as fraction |
| `--confirm-live` | `False` | Enable live trading with real orders |
| `-v` / `--verbose` | `False` | Enable DEBUG logging |

**Logging keywords for CloudWatch/log monitoring:**
- `WHALE-DIRECTION` — whale's current favoured side each poll
- `WHALE-FLIP` — whale changed direction mid-window
- `FILL` — token purchase on the favoured side
- `CLOSE` — position settled with P&L

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

### `backtest-spread` — Backtest Spread Capture Strategy

Replay historical market windows through the spread capture engine using stored order book snapshots and market metadata from the tick database.

```bash
trading-tools-polymarket backtest-spread \
  --start 2026-03-01 --end 2026-03-15 \
  --strategy accumulate \
  --signal-delay 300 --hedge-start 0.45 --hedge-end 0.65 \
  --capital 1000 -v
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | *(required)* | Start date `YYYY-MM-DD` |
| `--end` | *(required)* | End date `YYYY-MM-DD` |
| `--db-url` | env `TICK_DB_URL` or `sqlite+aiosqlite:///tick_data.db` | SQLAlchemy async DB URL |
| `--strategy` | `accumulate` | Strategy: `accumulate` |
| `--series-slug` | `None` | Filter to a specific series slug |
| `--capital` | `1000.0` | Initial virtual capital in USD |
| `--signal-delay` | `300` | Seconds of Binance lookback before window |
| `--hedge-start` | `0.45` | Early hedge threshold |
| `--hedge-end` | `0.65` | Late hedge threshold |
| `--hedge-start-pct` | `0.20` | Start hedging at this fraction of window |
| `--max-fill-age-pct` | `0.80` | Stop fills after this fraction of window |
| `--max-imbalance` | `3.0` | Max quantity ratio between legs |
| `--fill-size` | `2.0` | Tokens per adjustment fill |
| `--initial-fill` | `20.0` | Tokens for first fill on each side |
| `--poll-interval` | `5` | Seconds between poll cycles during replay |
| `--slippage` | `0.005` | Paper slippage percentage |
| `--verbose`, `-v` | `false` | Enable per-window logging |

Requires `market_metadata` and `order_book_snapshots` tables populated by the tick collector.

### `grid-spread` — Grid Search Spread Capture Parameters

Sweep hedge start/end thresholds and signal delay across a grid, replay each combination, and display results as markdown tables.

```bash
trading-tools-polymarket grid-spread \
  --start 2026-03-01 --end 2026-03-15 \
  --hedge-start 0.35,0.40,0.45,0.50 \
  --hedge-end 0.55,0.60,0.65,0.70,0.80,0.90 \
  --signal-delay 180,300,420
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | *(required)* | Start date `YYYY-MM-DD` |
| `--end` | *(required)* | End date `YYYY-MM-DD` |
| `--db-url` | env `TICK_DB_URL` or `sqlite+aiosqlite:///tick_data.db` | SQLAlchemy async DB URL |
| `--hedge-start` | `0.35,0.40,0.45,0.50` | Comma-separated hedge start values |
| `--hedge-end` | `0.55,0.60,0.65,0.70,0.80,0.90` | Comma-separated hedge end values |
| `--signal-delay` | `180,300,420` | Comma-separated signal delay values (seconds) |
| `--series-slug` | `None` | Filter to a specific series slug |
| `--capital` | `1000.0` | Initial virtual capital in USD |
| `--poll-interval` | `5` | Seconds between poll cycles during replay |
| `--slippage` | `0.005` | Paper slippage percentage |
| `--verbose`, `-v` | `false` | Enable per-window logging |

### `limit-backtest` — Backtest Limit Order Spread Capture

Simulate placing resting limit buy orders on both Up and Down tokens across a grid of bid prices, order sizes, and entry delays. Display fill rates, P&L, and Sharpe ratios as markdown tables.

```bash
trading-tools-polymarket limit-backtest \
  --start 2026-03-05 --end 2026-03-19 \
  --bid-up 0.05,0.10,0.15,0.20,0.25,0.30 \
  --bid-down 0.05,0.10,0.15,0.20,0.25,0.30 \
  --order-sizes 10,20,50 \
  --entry-delays 0.0,0.10,0.20 \
  --series-slug btc-updown-5m -v
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | *(required)* | Start date `YYYY-MM-DD` |
| `--end` | *(required)* | End date `YYYY-MM-DD` |
| `--db-url` | env `TICK_DB_URL` or `sqlite+aiosqlite:///tick_data.db` | SQLAlchemy async DB URL |
| `--bid-up` | `0.05,0.10,0.15,0.20,0.25,0.30` | Comma-separated Up bid prices |
| `--bid-down` | `0.05,0.10,0.15,0.20,0.25,0.30` | Comma-separated Down bid prices |
| `--order-sizes` | `10,20,50` | Comma-separated order sizes in tokens |
| `--entry-delays` | `0.0,0.10,0.20` | Comma-separated entry delay fractions (0.0 = at open) |
| `--series-slug` | `None` | Filter to a specific series slug |
| `--verbose`, `-v` | `false` | Enable per-window logging |

Output tables show:
- **Fill Rate (Both Sides)** — fraction of windows where both limit orders filled
- **Total P&L** — sum of guaranteed + directional P&L across all windows
- **Sharpe Ratio** — avg P&L / std P&L per window
- **Avg Guaranteed P&L** — mean profit from paired tokens in both-filled windows

## Database Support

Both tick collection and whale monitoring support SQLite (default) and PostgreSQL:

```bash
# SQLite (default, no setup required)
--db-url "sqlite+aiosqlite:///tick_data.db"

# PostgreSQL (requires asyncpg)
--db-url "postgresql+asyncpg://user:pass@host:5432/trading_tools"
```

Set the `TICK_DB_URL`, `WHALE_DB_URL`, or `SPREAD_DB_URL` environment variable to avoid passing `--db-url` on every command.
