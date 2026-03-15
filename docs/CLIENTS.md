# Client Module Reference

Public methods for every client class in `src/trading_tools/clients/`.
All clients are async and should be used as async context managers unless stated otherwise.

---

## PolymarketClient

`trading_tools.clients.polymarket.client.PolymarketClient`

The primary interface for all Polymarket interactions. Wraps the Gamma API, CLOB API, and
Data API behind a single async façade. No credentials are required for read-only methods;
authenticated methods require `POLYMARKET_PRIVATE_KEY` and `POLYMARKET_FUNDER_ADDRESS`.

### Market Discovery (no auth)

| Method | Returns | Description |
|---|---|---|
| `search_markets(keyword, *, limit)` | `list[Market]` | Search prediction markets by keyword via the Gamma API. |
| `get_market(condition_id)` | `Market` | Fetch a single market with live CLOB midpoint prices. |
| `get_market_tokens(condition_id)` | `Market` | Fetch a market without midpoint price enrichment (faster). |
| `get_market_info(market)` | `tuple[str, dict]` | Resolve a URL / slug / condition ID to its Gamma API endpoint URL and full raw market dict. |
| `get_order_book(token_id)` | `OrderBook` | Fetch the live bid/ask ladder for a CLOB token. |
| `discover_series_markets(series_slugs, *, include_next)` | `list[tuple[str, str]]` | Discover active `(condition_id, token_id)` pairs for recurring event series (e.g. `btc-up-or-down-5m`). |

### Trader & Leaderboard Data (no auth)

| Method | Returns | Description |
|---|---|---|
| `get_leaderboard(*, limit, time_period, order_by, category, offset)` | `list[TraderProfile]` | Fetch the global Polymarket leaderboard from the Data API. `time_period`: `"DAY"` / `"WEEK"` / `"MONTH"` / `"ALL"`. `order_by`: `"PNL"` / `"VOL"`. |
| `get_trader_trades(address, *, limit, offset)` | `list[dict]` | Fetch a page of trade history for a proxy wallet address. |
| `get_trader_trades_for_market(condition_id, *, limit, offset)` | `list[dict]` | Fetch a page of all trades in a specific market. |
| `lookup_user_address(username)` | `str \| None` | Resolve a Polymarket display name to a proxy wallet address via the leaderboard endpoint. Returns `None` if not found. |

### Account & Orders (auth required)

| Method | Returns | Description |
|---|---|---|
| `derive_api_creds()` | `tuple[str, str, str]` | Derive `(api_key, api_secret, api_passphrase)` from the wallet's private key. |
| `get_balance(asset_type)` | `Balance` | Fetch CLOB balance and allowance for `"COLLATERAL"` (USDC) or `"CONDITIONAL"` tokens. |
| `get_wallet_balance(rpc_url)` | `Decimal` | Fetch the on-chain USDC.e balance of the proxy wallet via JSON-RPC. |
| `sync_balance(asset_type)` | `None` | Tell the CLOB to re-sync its cached balance from on-chain state. |
| `place_order(request)` | `OrderResponse` | Submit a limit or market order. Accepts an `OrderRequest` dataclass. |
| `cancel_order(order_id)` | `dict` | Cancel an open order by ID. |
| `get_open_orders()` | `list[OrderResponse]` | Fetch all open orders for the authenticated account. |
| `get_redeemable_positions()` | `list[RedeemablePosition]` | Discover winning positions available for redemption via the Data API. |
| `redeem_positions(condition_ids, rpc_url)` | `int` | Redeem winning conditional tokens for resolved markets. Returns the number of positions redeemed. |
| `get_portfolio_value()` | `Decimal` | Compute total portfolio value: CLOB USDC balance plus current market value of all open positions. |

---

## GammaClient

`trading_tools.clients.polymarket._gamma_client.GammaClient`

Low-level async client for the Polymarket Gamma API (`gamma-api.polymarket.com`).
Used internally by `PolymarketClient`; call via `PolymarketClient` in application code.

| Method | Returns | Description |
|---|---|---|
| `get_markets(*, active, closed, limit, offset)` | `list[dict]` | Fetch a paginated list of prediction markets. |
| `get_market(condition_id)` | `dict` | Fetch a single market's raw metadata dict by condition ID. |
| `market_url(condition_id)` | `str` | Return the canonical Gamma API URL for a condition ID without making a network call. |
| `get_events(*, slug, active, limit)` | `list[dict]` | Fetch events, optionally filtered by slug. Events group related markets (e.g. a 5-minute series). |

---

## BinanceClient

`trading_tools.clients.binance.client.BinanceClient`

Async HTTP client for the public Binance REST API. No authentication required.
Typically used via `BinanceCandleProvider` rather than called directly.

| Method | Returns | Description |
|---|---|---|
| `get(path, params)` | `Any` | Send a GET request to the Binance API and return parsed JSON. |

---

## RevolutXClient

`trading_tools.clients.revolut_x.client.RevolutXClient`

Async HTTP client for the Revolut X trading API. Requires API credentials.
Use `RevolutXClient.from_config()` to construct from environment variables.

| Method | Returns | Description |
|---|---|---|
| `from_config()` | `RevolutXClient` | Class method. Create a client from environment-variable configuration. |
| `get(path, params)` | `dict` | Authenticated GET request. |
| `post(path, data, params)` | `dict` | Authenticated POST request. |
| `put(path, data, params)` | `dict` | Authenticated PUT request. |
| `delete(path, params)` | `dict` | Authenticated DELETE request. |

---

## Data Models

`trading_tools.clients.polymarket.models`

Frozen dataclasses returned by `PolymarketClient` methods.

| Model | Key Fields |
|---|---|
| `Market` | `condition_id`, `question`, `tokens`, `volume`, `liquidity`, `active` |
| `MarketToken` | `token_id`, `outcome`, `price` |
| `OrderBook` | `token_id`, `bids`, `asks`, `spread`, `midpoint` |
| `OrderLevel` | `price`, `size` |
| `OrderRequest` | `token_id`, `side`, `price`, `size`, `order_type` |
| `OrderResponse` | `order_id`, `status`, `token_id`, `side`, `price`, `size`, `filled` |
| `Balance` | `asset_type`, `balance`, `allowance` |
| `TraderProfile` | `rank`, `proxy_wallet`, `name`, `pnl`, `volume` |
| `RedeemablePosition` | `condition_id`, `token_id`, `outcome`, `size`, `title` |
