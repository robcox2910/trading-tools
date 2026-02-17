# Trading Tools

Trading tools for analyzing and trading cryptocurrencies via the Revolt API.

## Features

- 🚀 Built with modern Python 3.14
- ⚡ Fast dependency management with [uv](https://github.com/astral-sh/uv)
- 🔧 Linting and formatting with [Ruff](https://github.com/astral-sh/ruff)
- ✅ Test-driven development with pytest
- 📊 Full test coverage reporting
- 🔒 Pre-commit hooks for code quality
- 🤖 GitHub Actions CI/CD pipeline
- 📦 Modern project structure with src layout

## Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) package manager

## Installation

### Install uv (if not already installed)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Clone and setup

```bash
git clone <your-repo-url>
cd trading-tools

# Create virtual environment and install dependencies
uv sync --all-extras
```

## Development

### Install pre-commit hooks

```bash
uv run pre-commit install
```

### Running tests

```bash
# Run all tests with coverage
uv run pytest

# Run specific test file
uv run pytest tests/test_example.py

# Run with verbose output
uv run pytest -v
```

### Code quality

```bash
# Run linter
uv run ruff check .

# Fix auto-fixable issues
uv run ruff check --fix .

# Format code
uv run ruff format .

# Type checking
uv run mypy src tests
```

### Running locally

```bash
# Activate virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Run your application
python -m trading_tools
```

## Project Structure

```
trading-tools/
├── src/trading_tools/
│   ├── apps/                  # Runnable applications (entry: run.py)
│   ├── clients/               # External API clients
│   │   └── revolut_x/        # Revolut X API client
│   ├── core/                 # Core utilities and shared code
│   │   └── config.py        # YAML configuration loader
│   ├── data/                 # Data providers and storage
│   └── config/               # Configuration files (YAML)
│       └── settings.yaml
├── tests/                     # Test suite (mirrors src structure)
├── docs/                      # Documentation
│   ├── ARCHITECTURE.md       # Architecture and design principles
│   └── GETTING_STARTED.md   # Setup guide
├── .github/workflows/        # CI/CD pipelines
├── .pre-commit-config.yaml  # Pre-commit hooks
├── pyproject.toml           # Project configuration
└── README.md
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture documentation.

## Coverage Requirements

The project maintains a minimum of 80% test coverage. Coverage reports are generated in:
- Terminal output (summary)
- `htmlcov/` directory (detailed HTML report)
- `coverage.xml` (for CI/CD integration)

## CI/CD Pipeline

The GitHub Actions pipeline runs on every push and pull request:

1. **Lint Job**: Runs ruff linter, formatter, and mypy
2. **Test Job**: Runs pytest with coverage reporting
3. **Security Job**: Runs security checks

## TDD Workflow

We follow Test-Driven Development practices:

1. Write a failing test first
2. Write minimal code to make the test pass
3. Refactor while keeping tests green
4. Ensure coverage stays above 80%

## Deployment

Currently configured for local development. AWS deployment configuration coming soon.

## Contributing

1. Create a feature branch
2. Write tests first (TDD)
3. Implement features
4. Ensure all tests pass and coverage is maintained
5. Run pre-commit hooks
6. Submit a pull request

## License

Add your license here
