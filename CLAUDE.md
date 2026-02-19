# Claude Code Instructions

## Code Style

- All public functions, methods, classes, and modules **must** have a docstring
- Use imperative mood: "Return the trade." not "Returns the trade."
- One-line docstrings for simple functions; multi-line with summary + details for complex ones
- Follow the Google docstring convention (D211, D212 selected via ruff)

## Linting

- Ruff enforces a comprehensive rule set — run `uv run ruff check .` before committing
- Pyright in strict mode — run `uv run pyright src tests` before committing
- All violations must be fixed, not ignored (except D203/D213 incompatible pair)
- Inline `# noqa:` is acceptable only when tools genuinely conflict (e.g., ARG002 vs pyright protocol parameter names, PLW0108 for typed default_factory lambdas)

## Testing

- TDD workflow: Red-Green-Refactor
- Minimum 80% coverage enforced by pytest-cov
- Test classes and methods also require docstrings
- Use named constants instead of magic values (PLR2004)
- Run `uv run pytest` before committing

## Commits

- Use conventional commits (enforced by commitizen): `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `ci:`, `chore:`
- All changes go via PR from a feature branch (main is protected)
- Pre-commit hooks must pass: ruff, ruff-format, pyright, pip-audit, actionlint

## Dependencies

- Package manager: uv
- Add production deps to `[project.dependencies]`
- Add dev deps to `[project.optional-dependencies.dev]` or `[dependency-groups.dev]`
- Run `uv sync --all-extras` after modifying dependencies
