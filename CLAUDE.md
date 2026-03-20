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
- **Suppression comments (`# noqa`, `# type: ignore`, `# pyright: ignore`) are a last resort**
  - NEVER add an inline suppression without first exhausting alternatives
  - If genuinely unavoidable, MUST include an inline comment explaining why
  - Prefer `per-file-ignores` in `pyproject.toml` over inline `# noqa` for rules that legitimately apply to entire file categories
  - Do NOT use `from __future__ import annotations` — we target Python 3.14+
  - Do NOT use `if TYPE_CHECKING:` blocks — TCH rule is disabled by design
- For unused protocol parameters (ARG002), prefix with `_` when pyright allows it; use `# noqa: ARG002` only when pyright strict requires matching parameter names

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
| `docs/CLIENTS.md` | Client module reference for all external API integrations |
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

## Workflow Orchestration

### 1. Plan Node Default

- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately — don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy

- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop

- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done

- Never mark a task complete without proving it works
- Diff behaviour between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)

- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes — don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing

- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests — then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.
