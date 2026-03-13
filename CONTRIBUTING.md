# Contributing

Thank you for your interest in contributing to Trading Tools. This guide covers the development setup, workflow, code standards, and PR process.

## Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) package manager

## Setup

```bash
git clone https://github.com/robcox2910/trading-tools.git
cd trading-tools
uv sync --all-extras
uv run pre-commit install
```

Optionally, copy `.env.example` to `.env` and fill in any API credentials you need for the features you're working on. See [Getting Started](docs/GETTING_STARTED.md) for details.

## Development Workflow

1. **Branch from `main`** using a descriptive name (e.g. `feat/rsi-strategy`, `fix/csv-timestamp`).
2. **Write a failing test first** (Red), then make it pass (Green), then refactor.
3. **Run checks locally** before pushing:
   ```bash
   uv run pytest                          # Tests + coverage (80% minimum)
   uv run ruff check .                    # Lint
   uv run ruff format --check .           # Format check
   uv run pyright src tests               # Type check (strict mode)
   ```
4. **Update documentation** if your change affects CLI flags, project structure, configuration, or user-facing features. See the [Documentation](#documentation) section below.
5. **Commit and push** your branch, then open a pull request against `main`.

## Code Standards

| Tool | Purpose | Config |
|------|---------|--------|
| **ruff** | Linting and formatting | `pyproject.toml` |
| **pyright** | Type checking (strict mode) | `pyproject.toml` |
| **pytest** | Testing with 80%+ coverage required | `pyproject.toml` |

### Docstrings

All public modules, classes, methods, and functions **must** have a docstring. Use imperative mood (e.g., "Return the result." not "Returns the result."). Follow the Google docstring convention. This is enforced by ruff's `D` (pydocstyle) rules.

For non-trivial functions, include `Args:`, `Returns:`, and `Raises:` sections where applicable. Write docstrings so someone new to the codebase can understand the code without reading the implementation.

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/) format, enforced by commitizen:

| Prefix | Use for |
|--------|---------|
| `feat:` | New features |
| `fix:` | Bug fixes |
| `refactor:` | Code restructuring (no behaviour change) |
| `test:` | Test additions or changes |
| `docs:` | Documentation updates |
| `ci:` | CI/CD changes |
| `chore:` | Maintenance tasks |

### Pre-commit Hooks

Pre-commit runs ruff, ruff-format, pyright, pip-audit, and actionlint automatically on each commit. Commitizen validates commit messages on the `commit-msg` stage. If a hook fails, fix the issue and commit again. To run hooks manually:

```bash
uv run pre-commit run --all-files
```

## Documentation

Keep documentation up to date with every code change. When your PR modifies user-facing behaviour, update the relevant docs:

| Change | Update |
|--------|--------|
| New/changed CLI flags or commands | `docs/BACKTESTER.md`, `docs/POLYMARKET.md`, or `docs/GETTING_STARTED.md` |
| New module or application | `docs/ARCHITECTURE.md` project tree and tables |
| New feature | `README.md` feature list and examples |
| New env var or config | `docs/GETTING_STARTED.md` and `.env.example` |
| New strategy | Strategy tables in `docs/BACKTESTER.md` or `docs/POLYMARKET.md` |

Full documentation guide is in [CLAUDE.md](CLAUDE.md) under the Documentation section.

## PR Process

1. All CI checks must pass (`lint`, `test (3.14)`, `security`).
2. Branch protection is enabled on `main` — direct pushes are blocked.
3. Linear history is enforced — merge commits are not allowed.
4. PRs require review before merging.
5. Keep PRs focused: one feature or fix per PR.

## Project Structure

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full project layout, module responsibilities, and design principles.
