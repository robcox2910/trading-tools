# Claude Code Instructions

## Development Flow

- After you have completed a given implementation, run the linters, type checker, and any tests for effected areas of the codebase before returning to the user.

### Commands

| Step | Command | Notes |
|------|---------|-------|
| Format check | `uv run ruff format --check src/trading_tools tests` | Auto-fix with `--fix` removed |
| Format fix | `uv run ruff format src/trading_tools tests` | Apply formatting in-place |
| Lint check | `uv run ruff check src/trading_tools tests` | Auto-fix safe rules with `--fix` |
| Type check | `uv run pyright src/trading_tools tests` | Strict mode; suppress noisy stubs errors listed below |
| Tests | `uv run pytest` | Enforces 80% coverage via pytest-cov |
| Tests (fast) | `uv run pytest tests/apps/whale_monitor/` | Scope to a subdirectory when iterating |

**Pyright noise filter** — pipe output through this grep to surface only genuine errors:
```
uv run pyright src/trading_tools tests 2>&1 | grep -v \
  "reportUnknownMemberType\|reportUnknownVariableType\|reportUnknownArgumentType\
\|reportMissingTypeStubs\|reportAttributeAccessIssue\|reportUnknownParameterType\
\|reportUnknownLambdaType"
```

**Recommended pre-commit sequence:**
```bash
uv run ruff format src/trading_tools tests
uv run ruff check src/trading_tools tests
uv run pyright src/trading_tools tests   # apply noise filter above
uv run pytest
```

- Before pushing to a PR, ensure all of the above commands pass without errors. This is required for CI to pass and for maintainers to review your code. And check whether any of the markdown files within docs/ need to be updated.

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
- For numeric heavy operations, use numpy or pandas vectorisation instead of loops where possible (PLR2004)

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

Documentation must be kept up to date with every code change. Outdated docs are worse than no docs. **Doc updates must be included in the same PR as the code change** — not deferred to a follow-up.

### What to update

| Change | Files to update |
|--------|----------------|
| New/changed CLI flags or commands | `docs/BACKTESTER.md`, `docs/POLYMARKET.md`, or `docs/GETTING_STARTED.md` |
| New module or application | `docs/ARCHITECTURE.md` project tree and module tables |
| New user-facing feature | `README.md` feature list and usage examples |
| New env var or config key | `docs/GETTING_STARTED.md` and `.env.example` |
| New backtester strategy | Strategy table in `docs/BACKTESTER.md` and `docs/ARCHITECTURE.md` |
| New Polymarket strategy | Strategy table in `docs/POLYMARKET.md` and `docs/ARCHITECTURE.md` |
| Changed dependencies | `docs/GETTING_STARTED.md` if they require system-level setup |

### How to verify

Before committing, grep `docs/` for references to the code you changed and update any stale content:

```bash
grep -r "old_function_name" docs/
```

### Documentation index

| File | Content |
|------|---------|
| `README.md` | Project overview, feature list, quick-start examples |
| `docs/GETTING_STARTED.md` | Installation, API key setup, env var reference |
| `docs/BACKTESTER.md` | All backtester strategies, commands, risk management flags |
| `docs/POLYMARKET.md` | All Polymarket commands (trading, bots, ticks, whales, backtests) |
| `docs/ARCHITECTURE.md` | Project structure, module responsibilities, design principles |
| `docs/STRATEGY_GUIDE.md` | How to implement and integrate a new trading strategy |
| `docs/HTTP_CLIENT_USAGE.md` | Revolut X HTTP client API reference |
| `CONTRIBUTING.md` | Developer workflow, code standards, PR process |
| `.env.example` | Template for all environment variables |

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
