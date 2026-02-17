# Revolut X HTTP Client Usage

Complete guide for using the Revolut X HTTP client.

## Quick Start

### Basic Usage

```python
import asyncio
from trading_tools.clients.revolut_x import RevolutXClient

async def main():
    # Create client from configuration
    async with RevolutXClient.from_config() as client:
        # Make API requests
        balance = await client.get("/balance")
        print(balance)

asyncio.run(main())
```

### Manual Initialization

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from trading_tools.clients.revolut_x import RevolutXClient
from trading_tools.clients.revolut_x.auth.signer import Ed25519Signer

# Load private key
private_key = Ed25519Signer.load_private_key_from_file("/path/to/private_key.pem")

# Create client
client = RevolutXClient(
    api_key="your_64_character_api_key",
    private_key=private_key,
    base_url="https://api.revolut.com/api/1.0",
)

# Use client
response = await client.get("/balance")

# Don't forget to close
await client.close()
```

## HTTP Methods

### GET Request

```python
# Simple GET
data = await client.get("/orders")

# GET with query parameters
data = await client.get("/orders", params={
    "status": "open",
    "limit": 10
})
```

### POST Request

```python
# Create an order
order = await client.post("/orders", data={
    "symbol": "BTC-USD",
    "side": "buy",
    "quantity": "0.001",
    "type": "market"
})
```

### PUT Request

```python
# Update an order
updated = await client.put("/orders/12345", data={
    "quantity": "0.002"
})
```

### DELETE Request

```python
# Cancel an order
result = await client.delete("/orders/12345")
```

## Error Handling

The client provides specific exceptions for different error scenarios:

```python
from trading_tools.clients.revolut_x import (
    RevolutXClient,
    RevolutXAuthenticationError,
    RevolutXRateLimitError,
    RevolutXValidationError,
    RevolutXNotFoundError,
    RevolutXAPIError,
)

async def safe_api_call():
    try:
        async with RevolutXClient.from_config() as client:
            balance = await client.get("/balance")
            return balance

    except RevolutXAuthenticationError as e:
        print(f"Authentication failed: {e}")
        print(f"Status code: {e.status_code}")

    except RevolutXRateLimitError as e:
        print(f"Rate limit exceeded: {e}")
        # Implement retry logic with backoff

    except RevolutXValidationError as e:
        print(f"Invalid request: {e}")

    except RevolutXNotFoundError as e:
        print(f"Resource not found: {e}")

    except RevolutXAPIError as e:
        print(f"API error: {e}")
        print(f"Status code: {e.status_code}")
```

## Exception Hierarchy

```
RevolutXError (base)
  └── RevolutXAPIError
        ├── RevolutXAuthenticationError (401)
        ├── RevolutXValidationError (400)
        ├── RevolutXNotFoundError (404)
        └── RevolutXRateLimitError (429)
```

## Context Manager

The client supports async context managers for automatic resource cleanup:

```python
# Recommended: Use context manager
async with RevolutXClient.from_config() as client:
    data = await client.get("/balance")
    # Client automatically closed when exiting context

# Manual management
client = RevolutXClient.from_config()
try:
    data = await client.get("/balance")
finally:
    await client.close()
```

## Configuration

### Using YAML Configuration

Set up your `config/settings.yaml`:

```yaml
revolut_x:
  api_key: ${REVOLUT_X_API_KEY}
  private_key_path: ${REVOLUT_X_PRIVATE_KEY_PATH}
  base_url: ${REVOLUT_X_BASE_URL:https://api.revolut.com/api/1.0}
```

Then use `from_config()`:

```python
# Automatically loads from config
client = RevolutXClient.from_config()
```

### Using Environment Variables

Set in `.env`:

```bash
REVOLUT_X_API_KEY=your_api_key_here
REVOLUT_X_PRIVATE_KEY_PATH=/path/to/private_key.pem
REVOLUT_X_BASE_URL=https://api.revolut.com/api/1.0
```

## Authentication

The client automatically handles Ed25519 signature authentication:

1. **Timestamp**: Generated automatically (Unix timestamp in milliseconds)
2. **Signature**: Created from: `timestamp + method + path + query + body`
3. **Headers**: Automatically added to every request:
   - `X-Revx-API-Key`: Your API key
   - `X-Revx-Timestamp`: Request timestamp
   - `X-Revx-Signature`: Ed25519 signature

You don't need to worry about these details - the client handles everything!

## Advanced Usage

### Custom Timeout

```python
client = RevolutXClient(
    api_key=api_key,
    private_key=private_key,
    timeout=60.0  # 60 second timeout
)
```

### Query String Handling

Query parameters are automatically sorted for consistent signature generation:

```python
# These produce the same signature
await client.get("/orders", params={"limit": 10, "status": "open"})
await client.get("/orders", params={"status": "open", "limit": 10})
```

### JSON Body Handling

Request bodies are automatically minified for signature consistency:

```python
# Both produce the same signature
await client.post("/orders", data={"symbol": "BTC-USD", "side": "buy"})
await client.post("/orders", data={
    "symbol": "BTC-USD",
    "side": "buy"
})
```

## Complete Example

```python
import asyncio
from trading_tools.clients.revolut_x import (
    RevolutXClient,
    RevolutXAPIError,
    RevolutXRateLimitError,
)

async def main():
    """Example: Get balance and create an order."""

    try:
        async with RevolutXClient.from_config() as client:
            # Get account balance
            print("Fetching balance...")
            balance = await client.get("/balance")
            print(f"Balance: {balance}")

            # Get open orders
            print("\nFetching open orders...")
            orders = await client.get("/orders", params={
                "status": "open",
                "limit": 5
            })
            print(f"Open orders: {orders}")

            # Create a new order
            print("\nCreating order...")
            new_order = await client.post("/orders", data={
                "symbol": "BTC-USD",
                "side": "buy",
                "quantity": "0.001",
                "type": "market"
            })
            print(f"Order created: {new_order}")

            # Cancel an order
            order_id = new_order.get("id")
            if order_id:
                print(f"\nCancelling order {order_id}...")
                result = await client.delete(f"/orders/{order_id}")
                print(f"Cancellation result: {result}")

    except RevolutXRateLimitError:
        print("Rate limit exceeded. Please wait before retrying.")

    except RevolutXAPIError as e:
        print(f"API error: {e} (Status: {e.status_code})")

if __name__ == "__main__":
    asyncio.run(main())
```

## Testing

### Mock Client for Testing

```python
from unittest.mock import AsyncMock, patch
import pytest
from trading_tools.clients.revolut_x import RevolutXClient

@pytest.mark.asyncio
async def test_my_function():
    """Example test using mocked client."""

    # Create a mock response
    mock_response = {"balance": {"USD": "1000.00"}}

    # Mock the client
    with patch.object(RevolutXClient, 'get', new=AsyncMock(return_value=mock_response)):
        client = RevolutXClient.from_config()
        balance = await client.get("/balance")
        assert balance == mock_response
```

## Best Practices

1. **Use Context Managers**: Always use `async with` for automatic cleanup
2. **Handle Errors**: Catch specific exceptions for better error handling
3. **Rate Limiting**: Implement retry logic with exponential backoff for rate limits
4. **Logging**: Add logging for debugging and monitoring
5. **Configuration**: Use config files instead of hardcoding credentials
6. **Testing**: Mock the client in tests to avoid real API calls

## Next Steps

- See [GETTING_STARTED.md](GETTING_STARTED.md) for initial setup
- See [ARCHITECTURE.md](ARCHITECTURE.md) for project structure
- Refer to [Revolut X API docs](https://developer.revolut.com/docs/x-api/revolut-x-crypto-exchange-rest-api) for endpoint details

---

**Last Updated**: February 17, 2026
