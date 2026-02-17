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
from trading_tools.config import config
from trading_tools.revolut_x.auth.signer import Ed25519Signer

# Load your private key
private_key = Ed25519Signer.load_private_key_from_file(
    config.REVOLUT_X_PRIVATE_KEY_PATH
)

# Create signer
signer = Ed25519Signer(private_key)

# Generate a test signature
import time
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

## Next Steps

### Implement API Client

The next step is to create a full HTTP client that:

1. Makes authenticated requests to the Revolut X API
2. Handles rate limiting and retries
3. Provides methods for common operations:
   - Get account balance
   - Fetch market data
   - Place orders
   - Get trade history

### Example Usage (Coming Soon)

```python
from trading_tools.revolut_x import RevolutXClient

# Initialize client
client = RevolutXClient()

# Get balance
balance = await client.get_balance()

# Get market data
btc_price = await client.get_ticker("BTC-USD")

# Place an order
order = await client.create_order(
    symbol="BTC-USD",
    side="buy",
    quantity="0.001",
    order_type="market"
)
```

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
