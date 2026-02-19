# Getting Started with Revolut X API

This guide will help you set up and start using the Revolut X cryptocurrency trading API.

## Prerequisites

1. A Revolut X account
2. Python 3.14+
3. uv package manager

## Step 1: Generate Ed25519 Key Pair

The Revolut X API uses Ed25519 signature-based authentication. Generate your key pair:

```bash
# Generate private key
openssl genpkey -algorithm ed25519 -out private_key.pem

# Extract public key
openssl pkey -in private_key.pem -pubout -out public_key.pem

# View public key (to upload to Revolut X)
cat public_key.pem
```

**Important:** Store your `private_key.pem` securely and never commit it to version control!

## Step 2: Create API Key on Revolut X

1. Log into your Revolut X account at https://revolut.com/x
2. Navigate to Profile → Settings → API Keys
3. Click "Create API Key"
4. Paste your **public key** (from `public_key.pem`)
5. Save the generated 64-character API key

## Step 3: Configure Environment Variables

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` and add your credentials:

```env
REVOLUT_X_API_KEY=your_64_character_api_key_here
REVOLUT_X_PRIVATE_KEY_PATH=/path/to/your/private_key.pem
REVOLUT_X_BASE_URL=https://api.revolut.com/api/1.0
ENVIRONMENT=sandbox  # or 'production'
```

## Step 4: Install Dependencies

```bash
uv sync --all-extras
```

## Step 5: Test Authentication

Create a simple test script to verify your setup:

```python
from trading_tools.core.config import config
from trading_tools.clients.revolut_x.auth.signer import Ed25519Signer
import time

# Load your private key
private_key_path = config.get("revolut_x.private_key_path")
private_key = Ed25519Signer.load_private_key_from_file(private_key_path)

# Create signer
signer = Ed25519Signer(private_key)

# Generate a test signature
timestamp = str(int(time.time() * 1000))
signature = signer.generate_signature(
    timestamp=timestamp,
    method="GET",
    path="/api/1.0/balance",
    query="",
    body=""
)

print(f"Signature generated successfully: {signature[:32]}...")
```

## Fetching Candle Data

Once credentials are configured, use the fetch command to download historical candle data:

```bash
# Fetch BTC-USD hourly candles for January 2024
trading-tools-fetch --symbol BTC-USD --interval 1h --start 2024-01-01 --end 2024-02-01 --output candles.csv

# Or run as a module
python -m trading_tools.apps.fetcher.run --start 2024-01-01 --output candles.csv
```

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol` | `BTC-USD` | Trading pair |
| `--interval` | `1h` | Candle interval (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w`) |
| `--start` | *(required)* | Start date (ISO 8601 like `2024-01-01`) or Unix timestamp |
| `--end` | now | End date or Unix timestamp |
| `--output` | `candles.csv` | Output CSV file path |

The output CSV is compatible with the backtester's `--source csv` mode.

## Using the HTTP Client

Once configured, see **[HTTP Client Usage](HTTP_CLIENT_USAGE.md)** for the complete API reference including all methods, error handling, and examples.

## API Documentation

Full API documentation: https://developer.revolut.com/docs/x-api/revolut-x-crypto-exchange-rest-api

## Security Best Practices

1. **Never commit private keys** - Keep `private_key.pem` out of version control
2. **Use environment variables** - Store sensitive data in `.env` files
3. **Rotate keys regularly** - Generate new key pairs periodically
4. **Test in sandbox first** - Use sandbox environment before production
5. **Monitor API usage** - Keep track of your rate limits and request patterns

## Troubleshooting

### Signature Verification Failed

- Ensure your private key matches the public key uploaded to Revolut X
- Check that timestamp is in milliseconds
- Verify the request path, query, and body are correctly formatted

### API Key Invalid

- Confirm you copied the full 64-character API key
- Check that the API key is enabled in your Revolut X account
- Ensure you're using the correct environment (sandbox vs production)

## Support

- GitHub Issues: https://github.com/robcox2910/trading-tools/issues
- Revolut X Support: https://developer.revolut.com/support
