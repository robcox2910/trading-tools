# Getting Started

This guide covers installation, configuration, and authentication for all three CLI tools.

## Prerequisites

1. Python 3.14+
2. [uv](https://github.com/astral-sh/uv) package manager
3. API credentials for the services you want to use (see below)

## Step 1: Install Dependencies

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/robcox2910/trading-tools.git
cd trading-tools
uv sync --all-extras
```

## Step 2: Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` and fill in credentials for the services you plan to use. You only need to configure the sections relevant to your use case — all credentials are optional.

### Revolut X (for `trading-tools-fetch` and `--source revolut-x`)

Required for fetching candle data from Revolut X and using the Revolut X HTTP client.

```env
REVOLUT_X_API_KEY=your_64_character_api_key_here
REVOLUT_X_PRIVATE_KEY_PATH=/path/to/your/private_key.pem
REVOLUT_X_BASE_URL=https://revx.revolut.com/api/1.0
```

**Note:** The base URL is `https://revx.revolut.com/api/1.0` (not `api.revolut.com`).

See [Revolut X API Setup](#revolut-x-api-setup) below for key generation instructions.

### Polymarket (for `trading-tools-polymarket` trading commands)

Required for placing trades, running bots, and checking balances. Market queries (`markets`, `odds`, `book`), tick collection, and whale monitoring work without credentials.

```env
POLYMARKET_PRIVATE_KEY=0x_your_polygon_wallet_private_key_here
POLYMARKET_API_KEY=
POLYMARKET_API_SECRET=
POLYMARKET_API_PASSPHRASE=
POLYMARKET_FUNDER_ADDRESS=your_proxy_wallet_address
```

**Key points:**
- `POLYMARKET_PRIVATE_KEY` — your Polygon wallet (EOA) private key
- `POLYMARKET_FUNDER_ADDRESS` — your proxy wallet address (NOT the EOA). Find it at `polymarket.com/profile/0x...`
- API key/secret/passphrase — derive these using `client.derive_api_key()` or leave blank for initial setup
- UI-funded accounts use `signature_type=1` (POLY_PROXY)

### Database (for tick collection and whale monitoring)

Set database URLs to use PostgreSQL instead of the default SQLite:

```env
TICK_DB_URL=postgresql+asyncpg://user:pass@host:5432/trading_tools
WHALE_DB_URL=postgresql+asyncpg://user:pass@host:5432/trading_tools
SPREAD_DB_URL=postgresql+asyncpg://user:pass@host:5432/trading_tools
```

If these are not set, commands default to local SQLite files (`tick_data.db`, `whale_data.db`). `SPREAD_DB_URL` falls back to `WHALE_DB_URL` if unset. You can also pass `--db-url` on any command to override.

### General Settings

```env
ENVIRONMENT=development    # development, sandbox, or production
LOG_LEVEL=INFO             # DEBUG, INFO, WARNING, ERROR
```

## Step 3: Install Pre-commit Hooks

Required for contributing code:

```bash
uv run pre-commit install
```

This installs hooks for ruff, pyright, pip-audit, actionlint, and commitizen.

## Step 4: Verify Installation

```bash
# Run the test suite
uv run pytest

# Check that CLI tools are available
trading-tools-fetch --help
trading-tools-backtest --help
trading-tools-polymarket --help
```

## Revolut X API Setup

The Revolut X API uses Ed25519 signature-based authentication.

### Generate Ed25519 Key Pair

```bash
# Generate private key
openssl genpkey -algorithm ed25519 -out private_key.pem

# Extract public key
openssl pkey -in private_key.pem -pubout -out public_key.pem

# View public key (to upload to Revolut X)
cat public_key.pem
```

**Important:** Store your `private_key.pem` securely and never commit it to version control.

### Create API Key on Revolut X

1. Log into your Revolut X account at https://revolut.com/x
2. Navigate to Profile > Settings > API Keys
3. Click "Create API Key"
4. Paste your **public key** (from `public_key.pem`)
5. Save the generated 64-character API key

### Test Authentication

```bash
# Fetch candles to verify your Revolut X credentials
trading-tools-fetch --symbol BTC-USD --interval 1h --start 2025-01-01 --end 2025-01-02
```

### API Notes

- **Base URL**: `https://revx.revolut.com/api/1.0`
- **Signature**: base64-encoded Ed25519 (not hex)
- **Auth headers**: `X-Revx-API-Key`, `X-Revx-Timestamp` (ms), `X-Revx-Signature` (base64)
- **Candle intervals** (minutes): 5, 15, 30, 60, 240, 1440, 2880, 5760, 10080, 20160, 40320
- **Max 100 candles per request** — the fetcher paginates automatically
- **Historical data is limited** — very old date ranges may return recent data instead

## Fetching Candle Data

Once credentials are configured, use the fetch command to download historical candle data:

```bash
# Fetch BTC-USD hourly candles for January 2025
trading-tools-fetch --symbol BTC-USD --interval 1h --start 2025-01-01 --end 2025-02-01

# Fetch from Binance (no auth required)
trading-tools-fetch --source binance --symbol ETH-USD --interval 4h --start 2025-01-01

# Custom output path
trading-tools-fetch --start 2025-01-01 --output data/btc_hourly.csv
```

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol` | `BTC-USD` | Trading pair |
| `--interval` | `1h` | Candle interval (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w`) |
| `--start` | *(required)* | Start date (ISO 8601 like `2025-01-01`) or Unix timestamp |
| `--end` | now | End date or Unix timestamp |
| `--output` | `candles.csv` | Output CSV file path |
| `--source` | `revolut-x` | Data source: `revolut-x` or `binance` |

The output CSV is compatible with the backtester's `--source csv` mode.

## Configuration System

The project uses a layered configuration system:

1. **`src/trading_tools/config/settings.yaml`** — Base configuration (committed)
2. **`src/trading_tools/config/settings.local.yaml`** — Local overrides (gitignored)
3. **`.env`** — Environment variables (gitignored)

YAML files support environment variable substitution with defaults:

```yaml
revolut_x:
  api_key: "${REVOLUT_X_API_KEY:}"
  base_url: "${REVOLUT_X_BASE_URL:https://revx.revolut.com/api/1.0}"
```

Access values in code via dot-notation:

```python
from trading_tools.core.config import config

api_key = config.get("revolut_x.api_key")
```

`.env` values are loaded automatically by python-dotenv and override YAML defaults.

## Using the HTTP Client

For programmatic access to the Revolut X API, see **[HTTP Client Usage](HTTP_CLIENT_USAGE.md)**.

## Next Steps

- **[Backtester](BACKTESTER.md)** — Run strategies against historical data
- **[Polymarket](POLYMARKET.md)** — Trade prediction markets, run bots, monitor whales
- **[Architecture](ARCHITECTURE.md)** — Understand the project structure
- **[Contributing](../CONTRIBUTING.md)** — Set up your development environment and submit PRs

## Security Best Practices

1. **Never commit private keys** — Keep `private_key.pem` and `.env` out of version control
2. **Use environment variables** — Store sensitive data in `.env` files
3. **Rotate keys regularly** — Generate new key pairs periodically
4. **Test in sandbox first** — Use sandbox environment before production
5. **Start with paper trading** — Use `bot` before `bot-live`

## Troubleshooting

### Signature Verification Failed (Revolut X)

- Ensure your private key matches the public key uploaded to Revolut X
- Check that timestamp is in milliseconds
- Verify the signing path includes the `/api/1.0/` prefix

### API Key Invalid (Revolut X)

- Confirm you copied the full 64-character API key
- Check that the API key is enabled in your Revolut X account

### Polymarket Trade Rejected

- Ensure `POLYMARKET_FUNDER_ADDRESS` is your proxy wallet, not your EOA
- Minimum order size is 5 tokens
- `maker` must be the proxy wallet address, `signer` must be the EOA

### Database Connection Failed

- For PostgreSQL: check that the `asyncpg` driver is installed (`uv sync --all-extras`)
- Verify the connection string format: `postgresql+asyncpg://user:pass@host:5432/db`
- For SQLite: the file is created automatically on first use

## Support

- GitHub Issues: https://github.com/robcox2910/trading-tools/issues
- Revolut X API Docs: https://developer.revolut.com/docs/x-api/revolut-x-crypto-exchange-rest-api
