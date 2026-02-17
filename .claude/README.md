# Claude Code Project Settings

This directory contains project-specific settings for Claude Code.

## Settings File

The `settings.json` file defines:

- **Project metadata**: Name, description, language, and Python version
- **Common commands**: Shortcuts for frequently used operations
- **Auto-approve patterns**: Commands that can run without manual approval
- **Tooling configuration**: Test framework, linter, formatter, etc.
- **TDD mode**: Enforces test-driven development practices

## Usage

Claude Code will automatically detect and use these settings when working in this project directory.

### Quick Commands

You can reference these commands in conversation:

- `test` - Run all tests with coverage
- `lint` - Check code quality
- `lint:fix` - Fix auto-fixable linting issues
- `format` - Format code according to project style
- `typecheck` - Run static type checking
- `pre-commit` - Run all pre-commit hooks
- `install` - Install/sync all dependencies
- `update` - Update all dependencies

### Auto-Approved Operations

The following operations will run automatically without prompting:

- Running tests (`pytest`)
- Code linting and formatting (`ruff`)
- Type checking (`mypy`)
- Installing dependencies (`uv sync`)
- Read-only git operations (`git status`, `git diff`, `git log`)

### Always Prompted Operations

The following operations will always require confirmation:

- Git commits and pushes
- Creating GitHub repositories
- Installing system packages
- Removing dependencies
- Destructive operations

## Customization

You can modify `settings.json` to adjust auto-approve patterns, add custom commands, or change project settings.
