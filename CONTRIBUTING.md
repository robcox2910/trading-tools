# Contributing

## Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) package manager

## Setup

```bash
git clone <repo-url>
cd trading-tools
uv sync --all-extras
uv run pre-commit install
```

## Development Workflow

1. **Branch from `main`** using a descriptive name (e.g. `feat/rsi-strategy`, `fix/csv-timestamp`).
2. **Write a failing test first** (Red), then make it pass (Green), then refactor.
3. **Run checks locally** before pushing:
   ```bash
   uv run pytest                          # Tests + coverage
   uv run ruff check .                    # Lint
   uv run ruff format --check .           # Format check
   uv run pyright src tests               # Type check
   ```
4. **Commit and push** your branch, then open a pull request against `main`.

## Code Standards

| Tool | Purpose | Config |
|------|---------|--------|
| **ruff** | Linting and formatting | `pyproject.toml` |
| **pyright** | Type checking (strict mode) | `pyproject.toml` |
| **pytest** | Testing with 80%+ coverage required | `pyproject.toml` |

### Docstrings

All public modules, classes, methods, and functions **must** have a docstring. Use imperative mood (e.g., "Return the result." not "Returns the result."). This is enforced by ruff's `D` (pydocstyle) rules.

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/) format, enforced by commitizen:
- `feat:` new features
- `fix:` bug fixes
- `refactor:` code restructuring
- `test:` test additions/changes
- `docs:` documentation updates
- `ci:` CI/CD changes
- `chore:` maintenance tasks

### Pre-commit Hooks

Pre-commit runs ruff, ruff-format, pyright, pip-audit, and actionlint automatically on each commit. Commitizen validates commit messages on the `commit-msg` stage. If a hook fails, fix the issue and commit again. To run hooks manually:

```bash
uv run pre-commit run --all-files
```

## PR Process

1. All CI checks must pass (lint, type check, tests, coverage).
2. Branch protection is enabled on `main` -- direct pushes are blocked.
3. PRs require review before merging.
4. Keep PRs focused: one feature or fix per PR.

## Project Structure

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full project layout, module responsibilities, and design principles.
