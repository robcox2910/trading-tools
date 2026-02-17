# Trading Tools Architecture

## Project Structure

The project follows a clean, modular architecture designed for scalability and maintainability.

```
trading-tools/
├── src/trading_tools/
│   ├── __init__.py
│   ├── apps/                   # Runnable applications
│   │   └── [app_name]/
│   │       └── run.py         # Entry point for each application
│   ├── clients/               # External API clients
│   │   └── revolut_x/         # Revolut X API client
│   │       ├── auth/          # Authentication logic
│   │       ├── models/        # Data models
│   │       └── endpoints/     # API endpoint implementations
│   ├── core/                  # Core utilities and shared code
│   │   └── config.py         # Configuration loader
│   ├── data/                  # Data providers and storage
│   │   └── [provider_name]/  # Database, cache, etc.
│   └── config/                # Configuration files (YAML)
│       ├── settings.yaml      # Base configuration
│       └── settings.local.yaml # Local overrides (gitignored)
├── tests/                     # Test suite
│   ├── apps/                 # Application tests
│   ├── clients/              # Client tests
│   ├── core/                 # Core utility tests
│   └── data/                 # Data provider tests
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
Shared utilities and common functionality:
- Configuration management
- Logging setup
- Database connections
- Generic utilities (date/time, calculations)
- Base classes and interfaces

**Example**: `config.py` - YAML-based configuration loader

### `/data` - Data Layer
Data providers, storage, and access:
- Database models and repositories
- Cache implementations
- Data transformation
- Market data providers
- Historical data storage

**Example**: PostgreSQL repository, Redis cache

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

## Application Entry Points

All applications follow the same pattern:

```python
# src/trading_tools/apps/my_app/run.py
"""My Application entry point."""

import argparse
from trading_tools.core.config import config

def main() -> None:
    """Run the application."""
    parser = argparse.ArgumentParser(description="My Application")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Application logic here
    print(f"Running in {config.get('environment')} mode")

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

Data providers follow this pattern:

```
data/
└── provider_name/
    ├── __init__.py       # Provider export
    ├── repository.py     # Data access
    ├── models.py         # Data models
    └── migrations/       # Database migrations
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
