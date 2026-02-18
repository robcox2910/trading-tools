# Trading Tools

Trading tools for analyzing and trading cryptocurrencies via the Revolut X API.

## Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) package manager

## Installation

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and setup
git clone <your-repo-url>
cd trading-tools
uv sync --all-extras

# Install pre-commit hooks
uv run pre-commit install
```

## Development

```bash
# Run tests with coverage
uv run pytest

# Run specific test file
uv run pytest tests/test_example.py -v

# Lint and format
uv run ruff check .          # Check for issues
uv run ruff check --fix .    # Auto-fix issues
uv run ruff format .         # Format code

# Type check
uv run pyright src tests

# Run all checks
uv run ruff check . && uv run ruff format --check . && uv run pyright src tests

# Install/add dependencies
uv sync --all-extras
uv add package-name
```

## CI/CD

GitHub Actions runs on every push and pull request: linting, tests with coverage (80% minimum), and security checks.

All code follows TDD (Red-Green-Refactor) and must adhere to DRY and SOLID principles.

## Documentation

- **[Getting Started](docs/GETTING_STARTED.md)** - Setup, authentication, and first API call
- **[Architecture](docs/ARCHITECTURE.md)** - Project structure, design principles, and configuration
- **[HTTP Client Usage](docs/HTTP_CLIENT_USAGE.md)** - Complete HTTP client API reference
- **[Backtester](docs/BACKTESTER.md)** - CSV format, CLI flags, custom strategies, and metrics
- **[Contributing](CONTRIBUTING.md)** - Developer workflow, code standards, and PR process

## License

Add your license here
