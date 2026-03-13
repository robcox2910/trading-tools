# Claude Code Instructions

## Code Style

- All public functions, methods, classes, and modules **must** have a docstring
- Docstrings must be **thorough, clean, and informative** — not just one-word placeholders
  - Classes: explain what the class represents, its role in the system, and key behaviour
  - Methods/functions: explain what it does, include `Args:`, `Returns:`, and `Raises:` sections where applicable
  - Modules: describe the purpose of the file and what it contains
  - Always write docstrings so someone new to the codebase can understand the code without reading the implementation
- Use imperative mood: "Return the trade." not "Returns the trade."
- One-line docstrings for truly trivial functions; multi-line with summary + details for everything else
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

## Documentation

Documentation must be kept up to date with every code change. Outdated docs are worse than no docs.

- **Adding/changing CLI flags or commands**: Update the relevant doc in `docs/` (`BACKTESTER.md`, `POLYMARKET.md`, or `GETTING_STARTED.md`) to reflect the new options, defaults, and help text
- **Adding a new module or app**: Update `docs/ARCHITECTURE.md` project tree and module tables
- **Adding a new feature**: Update `README.md` feature list and add usage examples
- **Changing configuration**: Update `docs/GETTING_STARTED.md` and `.env.example` if new env vars are introduced
- **Adding a new strategy**: Add it to the strategy tables in `docs/BACKTESTER.md` or `docs/POLYMARKET.md`
- **Changing dependencies**: Note any new prerequisites in `docs/GETTING_STARTED.md` if they require system-level setup

When in doubt, grep the `docs/` directory for references to the code you changed and update any stale content.

## Notebooks (`src/users/*/`)

Notebooks live under `src/users/<username>/` and are for exploration, not production code.

- **Reuse core and data first** — before writing any data-fetching logic in a notebook, check whether a function already exists in `trading_tools.core`, `trading_tools.data`, or a client. If an existing function nearly fits but is too narrow, generalise it in the source module first, then call it from the notebook.
- **Auto-reload must always be on** — every notebook must begin with:
  ```python
  %load_ext autoreload
  %autoreload 2
  ```
  This ensures edits to library code are picked up without restarting the kernel.
- **No duplicate logic** — notebooks must not re-implement anything that already lives in the package. Call the module; don't copy it.
