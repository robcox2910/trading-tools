# Trading Tools Architecture

## Project Structure

The project follows a clean, modular architecture designed for scalability and maintainability.

```
trading-tools/
├── src/trading_tools/
│   ├── __init__.py
│   ├── apps/                   # Runnable applications
│   │   └── backtester/        # Candle-based backtesting engine
│   │       ├── engine.py      # Backtest orchestration
│   │       ├── portfolio.py   # Portfolio state tracking
│   │       ├── metrics.py     # Performance metrics (Sharpe, drawdown, etc.)
│   │       ├── strategies/    # Pluggable trading strategies
│   │       │   └── sma_crossover.py
│   │       └── run.py         # CLI entry point
│   ├── clients/               # External API clients
│   │   └── revolut_x/         # Revolut X API client
│   │       ├── auth/          # Ed25519 authentication
│   │       ├── models/        # Data models
│   │       └── endpoints/     # API endpoint implementations
│   ├── core/                  # Core utilities and shared code
│   │   ├── config.py          # YAML configuration loader
│   │   ├── models.py          # Candle, Signal, Trade, Position, BacktestResult
│   │   └── protocols.py       # CandleProvider, TradingStrategy protocols
│   ├── data/                  # Data providers
│   │   └── providers/         # Candle data sources
│   │       ├── csv_provider.py    # Offline CSV candle provider
│   │       └── revolut_x.py       # Revolut X API candle provider
│   └── config/                # Configuration files (YAML)
│       ├── settings.yaml      # Base configuration
│       └── settings.local.yaml # Local overrides (gitignored)
├── tests/                     # Test suite (mirrors src structure)
│   ├── apps/                  # Application tests
│   ├── clients/               # Client tests
│   ├── core/                  # Core model/protocol tests
│   └── data/                  # Data provider tests
├── docs/                      # Documentation
└── .github/workflows/         # CI/CD pipelines
```

## Module Responsibilities

### `/apps` - Applications
Runnable applications and processes. Each application should have:
- `run.py` - Entry point for the application
- Application-specific logic
- CLI argument parsing if needed

**Example**: Trading bot, market monitor, backtester

### `/clients` - API Clients
Clients for external services and APIs. Each client includes:
- Authentication and authorization
- Request/response handling
- Error handling and retries
- Rate limiting
- API-specific models and types

**Example**: `revolut_x/` - Revolut X API client

### `/core` - Core Utilities
Shared utilities, models, and protocols:
- Configuration management (`config.py`)
- Domain models: Candle, Signal, Trade, Position, BacktestResult (`models.py`)
- Structural protocols: CandleProvider, TradingStrategy (`protocols.py`)

**Example**: `config.py` - YAML-based configuration loader

### `/data` - Data Layer
Data providers and access:
- Market data providers (candles, tickers)
- Pluggable via the `CandleProvider` protocol
- Async I/O for external data sources

**Example**: `providers/csv_provider.py`, `providers/revolut_x.py`

### `/config` - Configuration Files
YAML configuration files with environment variable substitution (`${VAR_NAME:default}`):
- `settings.yaml` - Base configuration (committed)
- `settings.local.yaml` - Local overrides (gitignored)
- Dot-notation access: `config.get("revolut_x.api_key")`

See [GETTING_STARTED.md](GETTING_STARTED.md) for full configuration and authentication setup.

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
- **apps**: What to run
- **clients**: How to communicate with external services
- **core**: Shared functionality
- **data**: How to store and retrieve data

### 4. Dependency Direction
```
apps → clients → core
apps → data → core
data → clients
```

Core should never depend on apps, clients, or data.

### 5. Configuration Over Code
- Use YAML configuration files
- Environment-specific overrides
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
- **Branch protection**: required status checks (lint, test, security), linear history

## Application Entry Points

All applications follow the same pattern:

```python
# src/trading_tools/apps/my_app/run.py
"""My Application entry point."""

from typing import Annotated

import typer

from trading_tools.core.config import config

app = typer.Typer(help="My Application")


@app.command()
def run(
    verbose: Annotated[bool, typer.Option(help="Enable verbose output")] = False,
) -> None:
    """Run the application."""
    print(f"Running in {config.get('environment')} mode (verbose={verbose})")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

## Client Structure

Each client should follow this pattern:

```
clients/
└── service_name/
    ├── __init__.py       # Main client export
    ├── client.py         # HTTP client
    ├── auth/             # Authentication
    │   └── signer.py
    ├── models/           # Request/response models
    │   ├── request.py
    │   └── response.py
    └── endpoints/        # API endpoints
        ├── market.py
        ├── trading.py
        └── account.py
```

## Data Layer Structure

Data providers implement the `CandleProvider` protocol:

```
data/
└── providers/
    ├── __init__.py
    ├── csv_provider.py       # Offline/testing provider
    └── revolut_x.py          # Live API provider
```

## Testing Strategy

### Test Organization
Tests mirror the source structure:
```
tests/
├── apps/
├── clients/
├── core/
└── data/
```

### Test Types
1. **Unit Tests**: Test individual functions/classes
2. **Integration Tests**: Test component interactions
3. **End-to-End Tests**: Test complete workflows

### Test Fixtures
Use pytest fixtures for common setup:
```python
@pytest.fixture
def revolut_client():
    return RevolutXClient(api_key="test_key")
```

## Future Considerations

### Microservices
The current structure can evolve into microservices:
- Each app becomes a service
- Clients and data layers are shared libraries
- Core utilities as a common library

### AWS Deployment
Structure supports AWS deployment:
- Apps run as Lambda functions, ECS tasks, or EC2 instances
- Clients for AWS services (S3, DynamoDB, etc.)
- Data layer abstracts storage (RDS, DynamoDB, S3)

### Horizontal Scaling
Design supports scaling:
- Stateless applications
- External data storage
- Message queues for async processing
